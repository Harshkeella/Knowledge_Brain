import asyncio

import pytest

from app.services import chat_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store._settings, "storage_dir", str(tmp_path))
    asyncio.run(chat_store.init_db())
    return chat_store


def test_session_and_message_roundtrip(store):
    async def run():
        session = await store.create_session()
        assert session["title"] == store.UNTITLED

        evidence = [{"reference_id": "1", "file_path": "a.pdf", "chain": []}]
        await store.add_message(session["id"], "user", "What is the leave policy?")
        await store.add_message(session["id"], "assistant", "20 days.", evidence)

        messages = await store.list_messages(session["id"])
        assert [m["role"] for m in messages] == ["user", "assistant"]
        # The evidence chain survives the round trip, which is what makes a
        # reopened session's provenance panel work.
        assert messages[1]["evidence"] == evidence
        assert messages[0]["evidence"] == []

        # Appending bumps the session so it sorts to the top of the sidebar.
        refreshed = await store.get_session(session["id"])
        assert refreshed["updated_at"] >= session["updated_at"]

    asyncio.run(run())


def test_sessions_list_most_recent_first(store):
    async def run():
        first = await store.create_session()
        second = await store.create_session()
        await store.add_message(first["id"], "user", "later activity")

        assert [s["id"] for s in await store.list_sessions()] == [
            first["id"],
            second["id"],
        ]

    asyncio.run(run())


def test_rename_and_delete(store):
    async def run():
        session = await store.create_session()
        assert await store.rename_session(session["id"], "Leave policy")
        assert (await store.get_session(session["id"]))["title"] == "Leave policy"

        await store.add_message(session["id"], "user", "hi")
        assert await store.delete_session(session["id"])
        assert await store.get_session(session["id"]) is None
        # Messages cascade, so a deleted thread leaves nothing behind.
        assert await store.list_messages(session["id"]) == []

        assert not await store.rename_session("nope", "x")
        assert not await store.delete_session("nope")

    asyncio.run(run())


def test_corrupt_evidence_does_not_break_the_conversation(store):
    async def run():
        session = await store.create_session()
        await store.add_message(session["id"], "assistant", "answer", [{"a": 1}])
        async with store._connect() as db:
            await db.execute("UPDATE chat_messages SET evidence = 'not json'")
            await db.commit()

        messages = await store.list_messages(session["id"])
        assert messages[0]["content"] == "answer"
        assert messages[0]["evidence"] == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Short question", "Short question"),
        ("", chat_store.UNTITLED),
        ("   ", chat_store.UNTITLED),
        ("first line\nsecond line", "first line"),
    ],
)
def test_fallback_title(message, expected):
    assert chat_store.fallback_title(message) == expected


def test_fallback_title_truncates():
    title = chat_store.fallback_title("word " * 40)
    assert len(title) <= 50 and title.endswith("...")


def test_generate_title_falls_back_when_the_model_fails(store, monkeypatch):
    import app.services.lightrag_engine as engine

    async def boom(*a, **kw):
        raise RuntimeError("no model")

    monkeypatch.setattr(engine, "llm_model_func", boom)
    assert asyncio.run(store.generate_title("What is the leave policy?")) == (
        "What is the leave policy?"
    )


def test_generate_title_rejects_a_rambling_model(store, monkeypatch):
    import app.services.lightrag_engine as engine

    async def rambles(*a, **kw):
        return "Certainly! Here is a title for your question: " + "x" * 80

    monkeypatch.setattr(engine, "llm_model_func", rambles)
    assert asyncio.run(store.generate_title("Leave policy?")) == "Leave policy?"


def test_generate_title_uses_a_clean_model_answer(store, monkeypatch):
    import app.services.lightrag_engine as engine

    async def clean(*a, **kw):
        return '  "Leave Policy Overview"\n'

    monkeypatch.setattr(engine, "llm_model_func", clean)
    assert asyncio.run(store.generate_title("Leave policy?")) == (
        "Leave Policy Overview"
    )
