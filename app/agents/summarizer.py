"""Summarizer Agent: removes duplicates and trims text before results reach Claude Code. No LLM."""

from __future__ import annotations

from typing import Any

MAX_TEXT_CHARS = 300
NEARBY_LINES = 1


def _trim(text: str) -> str:
    return text if len(text) <= MAX_TEXT_CHARS else f"{text[:MAX_TEXT_CHARS].rstrip()}..."


def _dedupe_findings(findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drop a model finding next to a kept finding of the same category.

    Findings arrive best first, with static ones ahead of model ones, so the most reliable wins.
    Static findings are never dropped, because each one is a separate confirmed rule match.
    """
    kept: list[dict[str, Any]] = []
    removed = 0
    for finding in findings:
        duplicate = finding["source"] == "model" and any(
            other["file"] == finding["file"]
            and other["category"] == finding["category"]
            and abs(other["line"] - finding["line"]) <= NEARBY_LINES
            for other in kept
        )
        if duplicate:
            removed += 1
        else:
            kept.append(
                {
                    **finding,
                    "issue": _trim(finding["issue"]),
                    "recommendation": _trim(finding["recommendation"]),
                }
            )
    return kept, removed


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return the result with duplicates removed. Tasks with nothing to combine pass through."""
    if result.get("task") != "code_review":
        return result
    findings, removed = _dedupe_findings(result["findings"])
    summary = result["summary"]
    if removed:
        summary = f"{summary} {removed} near-duplicate model findings removed."
    return {**result, "findings": findings, "findings_count": len(findings), "summary": summary}
