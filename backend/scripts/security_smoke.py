"""End-to-end probe of a running hosted-mode API.

Proves against the real running app -- not a stub, not a fixture -- that every
application endpoint is 401 without a token, that forged/expired/wrong-audience
tokens are rejected, and that two users see two different knowledge bases.

`test_multi_tenancy.py` tests the same guarantee at the storage layer, where it
is enforced. This tests it through the wire, where it is observed. Run it
against a fresh deployment before letting anyone in:

    AUTH_DISABLED=false SUPABASE_JWT_SECRET=<secret>       uvicorn app.main:app --port 8123
    python scripts/security_smoke.py            # or: ... <base-url> <secret>

Exits non-zero on the first failure, so it drops into CI unchanged.
"""

import os

import datetime
import json
import sys
import urllib.error
import urllib.request

import jwt

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123").rstrip("/")
SECRET = (
    sys.argv[2]
    if len(sys.argv) > 2
    else os.getenv("SUPABASE_JWT_SECRET", "smoke-test-secret-long-enough-for-hs256-ok")
)
ALICE = "aaaaaaaa-0000-0000-0000-000000000001"
BOB = "bbbbbbbb-0000-0000-0000-000000000002"

PROTECTED = [
    ("GET", "/api/v1/knowledge-base"),
    ("GET", "/api/v1/graph/sources"),
    ("GET", "/api/v1/graph?label=*"),
    ("GET", "/api/v1/chat/sessions"),
    ("GET", "/api/v1/me/usage"),
    ("POST", "/api/v1/ingest/text"),
    ("POST", "/api/v1/ingest/url"),
    ("POST", "/api/v1/ingest/folder"),
]

failures = []


def token(sub, secret=SECRET, aud="authenticated", ttl=3600):
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {"sub": sub, "aud": aud, "email": f"{sub[:5]}@example.com",
         "exp": now + datetime.timedelta(seconds=ttl), "iat": now},
        secret, algorithm="HS256",
    )


def call(method, path, tok=None, body=None):
    request = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body else None,
    )
    if body:
        request.add_header("Content-Type", "application/json")
    if tok:
        request.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(label, actual, expected):
    ok = actual == expected if not callable(expected) else expected(actual)
    print(f"{'PASS' if ok else 'FAIL'}  {label}  -> {actual}")
    if not ok:
        failures.append(label)


print("--- health (public) ---")
status, health = call("GET", "/health")
check("GET /health is public", status, 200)
check("health reports auth configured", health["checks"]["auth"], "ok")

print("\n--- no token ---")
for method, path in PROTECTED:
    status, _ = call(method, path, body={} if method == "POST" else None)
    check(f"{method} {path}", status, 401)

print("\n--- bad tokens ---")
check("forged signature", call("GET", "/api/v1/me/usage",
      token(ALICE, secret="a-different-secret-entirely-abcdefgh"))[0], 401)
check("expired", call("GET", "/api/v1/me/usage", token(ALICE, ttl=-60))[0], 401)
check("wrong audience (anon key)",
      call("GET", "/api/v1/me/usage", token(ALICE, aud="anon"))[0], 401)
check("garbage", call("GET", "/api/v1/me/usage", "not-a-jwt")[0], 401)

print("\n--- alice ---")
alice = token(ALICE)
status, usage = call("GET", "/api/v1/me/usage", alice)
check("GET /me/usage", status, 200)
check("quota is 5 GB", usage["quota_bytes"], 5 * 1024**3)
check("starts empty", usage["used_bytes"], 0)

status, ingested = call("POST", "/api/v1/ingest/text", alice, {
    "text": "Ada Lovelace wrote the first algorithm for the Analytical Engine.",
    "title": f"alice-private-note-{ALICE[:8]}",
})
check("POST /ingest/text", status, 200)
alice_doc = ingested.get("doc_id") if status == 200 else None

status, docs = call("GET", "/api/v1/knowledge-base", alice)
check("alice sees her document", len(docs), 1)
status, usage = call("GET", "/api/v1/me/usage", alice)
check("usage grew", usage["used_bytes"] > 0, True)
check("llm ledger present", "tokens_today" in usage["llm"], True)

print("\n--- bob (isolation) ---")
bob = token(BOB)
check("bob's knowledge base is empty", call("GET", "/api/v1/knowledge-base", bob)[1], [])
check("bob's usage is zero", call("GET", "/api/v1/me/usage", bob)[1]["used_bytes"], 0)
check("bob's chat sessions are empty", call("GET", "/api/v1/chat/sessions", bob)[1], [])
check("bob's graph sources are empty",
      call("GET", "/api/v1/graph/sources", bob)[1]["nodes"], [])
if alice_doc:
    check("bob cannot delete alice's document by id",
          call("DELETE", f"/api/v1/knowledge-base/{alice_doc}", bob)[0], 404)
    check("alice still has it",
          [d["doc_id"] for d in call("GET", "/api/v1/knowledge-base", alice)[1]],
          [alice_doc])

print("\n--- server-path folder ingestion ---")
status, detail = call("POST", "/api/v1/ingest/folder", alice, {"path": "C:\\Windows"})
check("refused with auth on", status, 403)
status, _ = call("POST", "/api/v1/ingest/folder", alice, {"path": "../../etc"})
check("traversal attempt also refused", status, 403)

print("\n--- quota ---")
status, detail = call("POST", "/api/v1/ingest/text", alice, {"text": "", "title": "x"})
check("empty ingest rejected", status, 422)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("all smoke checks passed")
