"""The isolation guarantee, tested rather than asserted.

Two halves, because the guarantee has two halves:

* the door -- an unverifiable token never becomes an identity;
* the rooms -- an identity only ever opens its own storage.

The second half is tested against the stores directly rather than over HTTP.
That is the layer where isolation is actually enforced (see app.core.auth), so
it is the layer a regression would break: an endpoint that forgot a filter is
not a way in, because there is no unscoped store to reach.
"""

import asyncio
import datetime

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core import auth
from app.core.config import get_settings
from app.services import chat_store, manifest
from app.services.ingestion import QuotaExceeded, _check_quota
from app.services.parsers import spreadsheet

SECRET = "test-jwt-secret-not-a-real-one-but-long-enough-for-hs256"
ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def _token(sub: str, *, expires_in: int = 3600, audience: str = "authenticated") -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {
            "sub": sub,
            "aud": audience,
            "email": f"{sub[:4]}@example.com",
            "exp": now + datetime.timedelta(seconds=expires_in),
            "iat": now,
        },
        SECRET,
        algorithm="HS256",
    )


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    """A deployment with authentication on and an empty storage root."""
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_disabled", False)
    monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    auth._provisioned.clear()
    yield settings
    spreadsheet.close_connections()
    auth._provisioned.clear()


@pytest.fixture
def client(hosted):
    """A probe app carrying only the real dependency -- no models, no LightRAG."""
    app = FastAPI()

    @app.get("/probe")
    async def probe(user: auth.User = Depends(auth.get_current_user)):
        return {"id": user.id, "workspace": auth.workspace()}

    with TestClient(app) as test_client:
        yield test_client


def _as(user_id: str):
    auth.run_as(auth.User(id=user_id))


async def _insert(user_id: str, doc_id: str, size_bytes: int = 10) -> dict:
    _as(user_id)
    await manifest.init_db()
    return await manifest.insert_document(
        doc_id=doc_id,
        file_name=f"{doc_id}.md",
        source_type="markdown",
        content_hash=f"hash-{doc_id}",
        chunk_count=1,
        size_bytes=size_bytes,
    )


# --- The door -------------------------------------------------------------


def test_unauthenticated_request_is_rejected(client):
    assert client.get("/probe").status_code == 401


def test_garbage_token_is_rejected(client):
    response = client.get("/probe", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_token_signed_with_another_secret_is_rejected(client):
    forged = jwt.encode(
        {
            "sub": ALICE,
            "aud": "authenticated",
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1),
        },
        "an-attackers-own-secret-that-is-also-long-enough-for-hs256",
        algorithm="HS256",
    )
    response = client.get("/probe", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_expired_token_is_rejected(client):
    token = _token(ALICE, expires_in=-60)
    response = client.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_anon_key_audience_is_rejected(client):
    """A Supabase anon key is signed with the same secret but is not a user."""
    token = _token(ALICE, audience="anon")
    response = client.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_identity_comes_from_the_token_not_the_request(client):
    """A client-supplied user id is ignored; `sub` is the only identity."""
    token = _token(ALICE)
    response = client.get(
        "/probe",
        headers={"Authorization": f"Bearer {token}", "X-User-Id": BOB},
        params={"user_id": BOB},
    )
    assert response.status_code == 200
    assert response.json()["id"] == ALICE
    assert response.json()["workspace"] == ALICE


def test_valid_token_provisions_that_users_stores(client, hosted, tmp_path):
    token = _token(ALICE)
    response = client.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    user_root = tmp_path / "users" / ALICE
    assert (user_root / "manifest.sqlite3").exists()
    assert (user_root / "chat.sqlite3").exists()


# --- The rooms ------------------------------------------------------------


def test_each_user_gets_a_distinct_workspace_and_directory(hosted):
    _as(ALICE)
    alice_workspace, alice_dir = auth.workspace(), auth.user_dir()
    _as(BOB)
    assert auth.workspace() != alice_workspace
    assert auth.user_dir() != alice_dir


def test_the_local_user_keeps_the_legacy_layout(hosted, monkeypatch):
    """Turning multi-tenancy on must not strand an existing install's data."""
    monkeypatch.setattr(hosted, "auth_disabled", True)
    _as(hosted.local_user_id)
    assert auth.workspace() == ""
    assert auth.user_dir() == hosted.storage_dir


@pytest.mark.parametrize("evil", ["../../etc", "a/b", "..", ""])
def test_a_user_id_that_is_not_a_path_component_is_refused(hosted, evil):
    """Defence in depth: `sub` is a verified UUID, but it still lands in a path."""
    from fastapi import HTTPException

    _as(evil)
    with pytest.raises(HTTPException):
        auth.user_dir()


def test_documents_are_invisible_across_users(hosted):
    async def scenario():
        await _insert(ALICE, "doc-alice")
        await _insert(BOB, "doc-bob")

        _as(ALICE)
        alice_docs = [row["doc_id"] for row in await manifest.list_documents()]
        _as(BOB)
        bob_docs = [row["doc_id"] for row in await manifest.list_documents()]
        return alice_docs, bob_docs

    alice_docs, bob_docs = asyncio.run(scenario())
    assert alice_docs == ["doc-alice"]
    assert bob_docs == ["doc-bob"]


def test_a_user_cannot_read_or_delete_another_users_document_by_id(hosted):
    """Guessing the id is not enough -- it is not in the caller's store at all."""

    async def scenario():
        await _insert(ALICE, "doc-alice")
        _as(BOB)
        await manifest.init_db()
        return (
            await manifest.get_document("doc-alice"),
            await manifest.delete_document("doc-alice"),
            await manifest.find_by_hash("hash-doc-alice"),
        )

    fetched, deleted, by_hash = asyncio.run(scenario())
    assert fetched is None
    assert deleted is False
    assert by_hash is None

    async def alice_still_has_it():
        _as(ALICE)
        return await manifest.get_document("doc-alice")

    assert asyncio.run(alice_still_has_it()) is not None


def test_chat_sessions_are_invisible_across_users(hosted):
    async def scenario():
        _as(ALICE)
        await chat_store.init_db()
        session = await chat_store.create_session("Alice's thread")
        await chat_store.add_message(session["id"], "user", "a private question")

        _as(BOB)
        await chat_store.init_db()
        return (
            session["id"],
            await chat_store.list_sessions(),
            await chat_store.get_session(session["id"]),
            await chat_store.list_messages(session["id"]),
            await chat_store.delete_session(session["id"]),
        )

    _, sessions, fetched, messages, deleted = asyncio.run(scenario())
    assert sessions == []
    assert fetched is None
    assert messages == []
    assert deleted is False


def test_spreadsheet_tables_live_in_separate_databases(hosted):
    _as(ALICE)
    alice = spreadsheet.get_connection()
    alice.execute("CREATE TABLE salaries (name TEXT, amount INTEGER)")
    alice.execute("INSERT INTO salaries VALUES ('alice', 100)")

    _as(BOB)
    bob = spreadsheet.get_connection()
    assert bob is not alice
    tables = [row[0] for row in bob.execute("SHOW TABLES").fetchall()]
    assert "salaries" not in tables
    # Even hand-written SQL cannot name it: it is not in this database file.
    with pytest.raises(Exception):
        bob.execute("SELECT * FROM salaries").fetchall()


# --- Quota ----------------------------------------------------------------


def test_quota_admits_up_to_the_boundary_and_refuses_past_it(hosted, monkeypatch):
    monkeypatch.setattr(hosted, "storage_quota_bytes", 1000)

    async def scenario():
        await _insert(ALICE, "doc-1", size_bytes=900)
        _as(ALICE)
        # Exactly filling the quota is allowed; one byte more is not.
        await _check_quota(100)
        with pytest.raises(QuotaExceeded):
            await _check_quota(101)

    asyncio.run(scenario())


def test_deleting_a_document_releases_its_quota(hosted, monkeypatch):
    monkeypatch.setattr(hosted, "storage_quota_bytes", 1000)

    async def scenario():
        await _insert(ALICE, "doc-1", size_bytes=1000)
        _as(ALICE)
        with pytest.raises(QuotaExceeded):
            await _check_quota(1)
        await manifest.delete_document("doc-1")
        await _check_quota(1000)  # must not raise
        return await manifest.usage()

    usage = asyncio.run(scenario())
    assert usage == {
        "used_bytes": 0,
        "quota_bytes": 1000,
        "remaining_bytes": 1000,
        "document_count": 0,
    }


def test_one_users_usage_does_not_count_against_another(hosted, monkeypatch):
    monkeypatch.setattr(hosted, "storage_quota_bytes", 1000)

    async def scenario():
        await _insert(ALICE, "doc-1", size_bytes=1000)
        _as(BOB)
        await manifest.init_db()
        await _check_quota(1000)  # Bob's store is empty; must not raise
        return await manifest.usage()

    assert asyncio.run(scenario())["used_bytes"] == 0
