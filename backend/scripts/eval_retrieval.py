"""Repeatable retrieval eval, so "accuracy" is a tracked number.

    python -m scripts.eval_retrieval                     # run and print
    python -m scripts.eval_retrieval --save baseline.json
    python -m scripts.eval_retrieval --compare baseline.json

Scores RETRIEVAL rather than prose. Each question in `eval_questions.yaml`
declares substrings that must show up in the evidence chain the pipeline built
(`services/provenance.build_evidence`), so the score does not move when the
answering model changes its wording. Three things are measured per question:

  retrieval  did the expected material make it into the evidence?
  grounding  did the answer stay inside that evidence?
  hops       did a question marked multi-hop actually decompose?

Run it before and after a retrieval or ontology change, and diff the two with
--compare. That is the whole point: a lever you cannot measure is a lever you
cannot claim.
"""

import argparse
import asyncio
import json
import os
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.chat import build_query_param, build_retrieval_param  # noqa: E402
from app.services import grounding, multihop, provenance  # noqa: E402
from app.services.lightrag_engine import get_rag, shutdown_rag  # noqa: E402

QUESTIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_questions.yaml")

# Substring that marks the model taking the "I don't know" exit the chat
# prompt asks for.
REFUSAL_MARKERS = ("no information", "does not contain", "not in the knowledge base")


def _evidence_text(evidence: list[dict]) -> str:
    parts = []
    for source in evidence:
        parts.append(source.get("file_path", ""))
        for step in source.get("chain", []):
            parts.append(step.get("label", ""))
            parts.append(step.get("snippet", ""))
    return " ".join(parts).lower()


async def run_question(rag, spec: dict) -> dict:
    question = spec["question"]
    started = time.monotonic()

    param = build_query_param([])
    seeds = await multihop.gather(rag, question, build_retrieval_param)
    if seeds:
        param.ll_keywords = seeds

    result = await rag.aquery_llm(question, param=param)
    if result.get("status") == "failure":
        return {
            "id": spec["id"],
            "question": question,
            "error": result.get("message", "query failed"),
        }

    evidence = provenance.build_evidence(result.get("data", {}))

    llm = result.get("llm_response", {})
    if llm.get("is_streaming"):
        answer = "".join([chunk async for chunk in llm["response_iterator"]])
    else:
        answer = llm.get("content") or ""

    haystack = _evidence_text(evidence)
    expected = [str(e) for e in (spec.get("expect") or [])]
    missing = [e for e in expected if e.lower() not in haystack]

    verdict = grounding.check(answer, evidence)
    refused = any(marker in answer.lower() for marker in REFUSAL_MARKERS)

    row = {
        "id": spec["id"],
        "question": question,
        "sources": len(evidence),
        "evidence_steps": sum(len(s.get("chain", [])) for s in evidence),
        "expected": len(expected),
        "missing": missing,
        "retrieval_ok": not missing,
        "grounding_ratio": round(verdict["supported_ratio"], 3),
        "unsupported": len(verdict["unsupported"]),
        "seconds": round(time.monotonic() - started, 2),
        "answer": answer[:300],
    }

    if spec.get("hops"):
        # A question marked multi-hop should have produced seed keywords; if it
        # didn't, decomposition silently regressed.
        row["decomposed"] = bool(seeds)
    if spec.get("expect_refusal"):
        row["refused"] = refused
        row["retrieval_ok"] = refused

    return row


def summarise(rows: list[dict]) -> dict:
    scored = [r for r in rows if "error" not in r]
    hop_rows = [r for r in scored if "decomposed" in r]
    return {
        "questions": len(rows),
        "errors": len(rows) - len(scored),
        "retrieval_pass": sum(1 for r in scored if r["retrieval_ok"]),
        "grounding_mean": (
            round(sum(r["grounding_ratio"] for r in scored) / len(scored), 3)
            if scored
            else 0.0
        ),
        "unsupported_total": sum(r["unsupported"] for r in scored),
        "decomposed": sum(1 for r in hop_rows if r["decomposed"]),
        "multi_hop_questions": len(hop_rows),
        "mean_seconds": (
            round(sum(r["seconds"] for r in scored) / len(scored), 2) if scored else 0.0
        ),
    }


def print_report(rows: list[dict], summary: dict) -> None:
    print(f"\n{'id':<5} {'src':>4} {'steps':>6} {'ground':>7} {'sec':>6}  status")
    print("-" * 62)
    for row in rows:
        if "error" in row:
            print(f"{row['id']:<5} {'':>4} {'':>6} {'':>7} {'':>6}  ERROR {row['error'][:30]}")
            continue
        status = "ok" if row["retrieval_ok"] else f"MISSING {row['missing']}"
        if row.get("decomposed") is False:
            status += " [no decomposition]"
        print(
            f"{row['id']:<5} {row['sources']:>4} {row['evidence_steps']:>6} "
            f"{row['grounding_ratio']:>7} {row['seconds']:>6}  {status}"
        )

    print("\nSummary")
    for key, value in summary.items():
        print(f"  {key:<22} {value}")


def print_diff(before: dict, after: dict) -> None:
    print("\nBefore -> after")
    print("-" * 46)
    for key, new in after["summary"].items():
        old = before["summary"].get(key)
        if isinstance(new, (int, float)) and isinstance(old, (int, float)):
            delta = round(new - old, 3)
            arrow = "+" if delta > 0 else ""
            print(f"  {key:<22} {old} -> {new}  ({arrow}{delta})")
        else:
            print(f"  {key:<22} {old} -> {new}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", metavar="PATH", help="write results as JSON")
    parser.add_argument("--compare", metavar="PATH", help="diff against a saved run")
    parser.add_argument("--only", metavar="ID", help="run one question by id")
    args = parser.parse_args()

    with open(QUESTIONS_PATH, encoding="utf-8") as fh:
        specs = yaml.safe_load(fh)["questions"]
    if args.only:
        specs = [s for s in specs if s["id"] == args.only]
        if not specs:
            print(f"No question with id {args.only}")
            return 1

    rag = await get_rag()
    try:
        rows = []
        for spec in specs:
            try:
                rows.append(await run_question(rag, spec))
            except Exception as e:
                rows.append({"id": spec["id"], "question": spec["question"], "error": str(e)})
    finally:
        await shutdown_rag()

    summary = summarise(rows)
    print_report(rows, summary)

    payload = {"summary": summary, "rows": rows}
    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nSaved to {args.save}")

    if args.compare:
        with open(args.compare, encoding="utf-8") as fh:
            print_diff(json.load(fh), payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
