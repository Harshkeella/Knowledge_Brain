"""What actually went into the answer, grouped per source.

LightRAG's `aquery_llm` returns `data.entities` / `data.relationships` /
`data.chunks` *after* truncation -- these are the items that survived into the
prompt, not the candidate set that was considered. That distinction is the
whole point of a provenance panel: showing the candidates would be showing
work the answer never saw.

Every item carries the `reference_id` of the source it came from, which is the
same id the `sources` SSE frame already sends, so the panel keys off something
the UI is holding.

The chain is ordered the way the panel reads it, source-first:

    Source document -> Chunk -> Entity -> Relationship -> ... -> Answer

Ceiling: ordering is by kind, not by a real derivation trace -- LightRAG does
not record which entity produced which claim. Good enough to show what the
answer rests on; it is not a proof tree.
"""

SNIPPET_CHARS = 240

# Per source, per kind. A panel is a glanceable proof, not a transcript, and
# an 80-entity source would render as a wall.
MAX_PER_KIND = 6


def _snippet(text: str | None) -> str:
    text = " ".join(str(text or "").split())
    return text[: SNIPPET_CHARS - 1] + "…" if len(text) > SNIPPET_CHARS else text


def _step(kind: str, node_id: str, label: str, snippet: str, **extra) -> dict:
    step = {"type": kind, "id": node_id, "label": label, "snippet": snippet}
    step.update({k: v for k, v in extra.items() if v is not None})
    return step


def build_evidence(data: dict) -> list[dict]:
    """`[{reference_id, file_path, chain: [step, ...]}, ...]`, one per source.

    Sources with nothing attributable to them are dropped rather than rendered
    as an empty panel behind a button that looks clickable.
    """
    data = data or {}
    references = data.get("references") or []

    by_ref: dict[str, list[dict]] = {}

    def bucket(ref_id) -> list[dict] | None:
        # An item whose reference_id isn't in the reference list can't be
        # attributed to a source the UI is showing, so it has no panel to
        # live in.
        key = str(ref_id or "")
        return by_ref.get(key)

    for ref in references:
        by_ref[str(ref.get("reference_id") or "")] = []

    for chunk in data.get("chunks") or []:
        target = bucket(chunk.get("reference_id"))
        if target is None:
            continue
        target.append(
            _step(
                "chunk",
                str(chunk.get("chunk_id") or ""),
                chunk.get("file_path") or "chunk",
                _snippet(chunk.get("content")),
            )
        )

    for entity in data.get("entities") or []:
        target = bucket(entity.get("reference_id"))
        if target is None:
            continue
        name = entity.get("entity_name") or ""
        target.append(
            _step(
                "entity",
                str(name),
                str(name),
                _snippet(entity.get("description")),
                entity_type=entity.get("entity_type"),
            )
        )

    for rel in data.get("relationships") or []:
        target = bucket(rel.get("reference_id"))
        if target is None:
            continue
        src, tgt = rel.get("src_id") or "", rel.get("tgt_id") or ""
        target.append(
            _step(
                "relationship",
                f"{src}->{tgt}",
                f"{src} → {tgt}",
                _snippet(rel.get("description")),
                keywords=rel.get("keywords"),
                # Both endpoints, so the panel can deep-link each one into the
                # graph explorer rather than only the pair as a label.
                src_id=src,
                tgt_id=tgt,
            )
        )

    # Rebuild in panel order, capped per kind, with the source as step one.
    order = {"chunk": 0, "entity": 1, "relationship": 2}
    evidence = []
    for ref in references:
        key = str(ref.get("reference_id") or "")
        steps = by_ref.get(key) or []
        if not steps:
            continue
        kept: list[dict] = []
        for kind in ("chunk", "entity", "relationship"):
            kept.extend([s for s in steps if s["type"] == kind][:MAX_PER_KIND])
        kept.sort(key=lambda s: order[s["type"]])
        file_path = ref.get("file_path") or "source"
        evidence.append(
            {
                "reference_id": key,
                "file_path": file_path,
                "chain": [_step("source", file_path, file_path, "")] + kept,
            }
        )
    return evidence
