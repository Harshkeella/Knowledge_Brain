"""Who the request is, and the storage that identity opens.

Isolation is enforced at the four places state is *opened* -- the LightRAG
instance, the manifest DB, the chat DB, the DuckDB file -- not at the forty
places they are called. A request-scoped ContextVar carries the identity, so
there is no unscoped path to forget a filter on: a caller that never heard of
users still gets its own user's data, and a background task spawned inside a
request inherits the identity when the task is created.

Two modes:

* ``AUTH_DISABLED=true`` (the local default) -- every request is
  ``LOCAL_USER_ID``, whose workspace is ``""`` and whose storage paths are the
  legacy ones. The single-user install keeps working with no migration.
* ``AUTH_DISABLED=false`` -- a Supabase-issued JWT is required. The user id is
  the token's ``sub``; it is never read from the request body or a header the
  client controls.

The provider is behind ``verify_token`` alone: swapping Supabase for another
issuer is one function, not a refactor.
"""

import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

logger = logging.getLogger("app.auth")
_settings = get_settings()


@dataclass(frozen=True)
class User:
    id: str
    email: str | None = None


_current_user: ContextVar[User | None] = ContextVar("current_user", default=None)

# Bearer, not a cookie: the dashboard and the extension are both cross-origin
# to the API, and a bearer token needs no CSRF story. auto_error=False so an
# unauthenticated request produces our own 401 body rather than FastAPI's.
_bearer = HTTPBearer(auto_error=False)


def verify_token(token: str) -> User:
    """Decode a Supabase access token. Raises on anything not verifiable.

    Supabase signs project JWTs with the project's JWT secret (HS256). The
    audience is "authenticated" for a signed-in user; an anon/service key
    carries a different one and must not pass.
    """
    if not _settings.supabase_jwt_secret:
        raise RuntimeError(
            "AUTH_DISABLED is false but SUPABASE_JWT_SECRET is unset -- the API "
            "cannot verify any token and would reject every request."
        )
    claims = jwt.decode(
        token,
        _settings.supabase_jwt_secret,
        algorithms=["HS256"],
        audience="authenticated",
        options={"require": ["exp", "sub"]},
    )
    return User(id=str(claims["sub"]), email=claims.get("email"))


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    """FastAPI dependency: the authenticated user, bound to this request.

    Also sets the ContextVar the storage layer reads, which is what makes the
    scoping automatic for every service the endpoint goes on to call.
    """
    if _settings.auth_disabled:
        user = User(id=_settings.local_user_id, email=None)
    else:
        if credentials is None or not credentials.credentials:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            user = verify_token(credentials.credentials)
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Your session has expired.")
        except jwt.PyJWTError:
            # Never echo the decoder's reason: it tells an attacker which half
            # of a forged token was wrong.
            raise HTTPException(status_code=401, detail="Not authenticated")

    _current_user.set(user)
    request.state.user = user
    await ensure_stores(user)
    return user


# Users whose per-user databases have had their schema applied in this process.
_provisioned: set[str] = set()


async def ensure_stores(user: User) -> None:
    """Create this user's databases the first time they are seen.

    A user's stores are files under their own directory, so "sign up" on the
    API side is just this: the first authenticated request creates them. Doing
    it at the identity boundary means no endpoint has to remember to.
    """
    if user.id in _provisioned:
        return
    # Imported here: both modules read the identity this one owns.
    from app.services import chat_store, manifest

    await manifest.init_db()
    await chat_store.init_db()
    _provisioned.add(user.id)


def current_user() -> User:
    """The user this call is running for.

    Fails closed: outside a request, with auth on, there is no identity to
    guess and returning a default would be the cross-tenant bug this module
    exists to make impossible.
    """
    user = _current_user.get()
    if user is not None:
        return user
    if _settings.auth_disabled:
        return User(id=_settings.local_user_id)
    raise RuntimeError(
        "No authenticated user in context. Every storage open is user-scoped; "
        "a code path that reaches storage outside a request must set one with "
        "run_as()."
    )


def run_as(user: User):
    """Bind an identity outside a request -- workers, scripts, tests.

    Returns the ContextVar token so a caller can reset it; in practice the
    context is per-task and simply discarded.
    """
    return _current_user.set(user)


def workspace() -> str:
    """The LightRAG workspace for the current user.

    LightRAG namespaces the graph file, the KV files and every Qdrant point by
    workspace, and filters vector queries on it server-side -- so this one
    string is what isolates the entire retrieval core.

    The local user maps to ``""`` deliberately: that is the workspace an
    existing single-user install already wrote, so turning multi-tenancy on
    does not strand anybody's data behind a rename.
    """
    uid = current_user().id
    return "" if uid == _settings.local_user_id else uid


def user_dir() -> str:
    """Directory for this user's non-LightRAG stores (manifest, chat, DuckDB).

    The local user keeps the legacy flat layout for the same reason as above.
    User ids come from a verified JWT's ``sub`` (a UUID from Supabase), but
    they still land in a path, so anything that is not a single safe path
    component is refused rather than sanitised.
    """
    uid = current_user().id
    if uid == _settings.local_user_id:
        return _settings.storage_dir
    if not uid or os.sep in uid or "/" in uid or uid in (".", ".."):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return os.path.join(_settings.storage_dir, "users", uid)
