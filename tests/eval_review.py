"""Measures review quality against real Granite. Run with: python -m tests.eval_review

Not a pytest test: it reports a score rather than passing or failing, and takes a few minutes.
"""

from __future__ import annotations

from pathlib import Path

from app.agents.code_reviewer import review_code
from app.config.settings import get_settings
from app.llm import OllamaLLM

FIXTURES = Path(__file__).parent.resolve() / "fixtures" / "review_eval"
SEEDED_BUGS = {
    8: "division by zero",
    11: "mutable default",
    17: "sql injection",
    22: "unclosed file",
    23: "pickle",
    27: "shell=True",
    34: "bare except",
    39: "O(n^2) loop",
    46: "hardcoded key",
}


def main() -> None:
    settings = get_settings().model_copy(update={"allowed_workspaces": (FIXTURES,)})
    llm = OllamaLLM(settings)
    for name in ("seeded.py", "clean.py", "inject.py", "noinject.py"):
        result, metrics = review_code(FIXTURES, name, None, llm, settings)
        print(
            f"\n== {name}: findings={result.findings_count} discarded={result.findings_discarded} "
            f"skipped={result.skipped} calls={metrics.llm_calls} "
            f"tokens={metrics.prompt_tokens}+{metrics.output_tokens}"
        )
        for finding in result.findings:
            print(f"  L{finding.line} {finding.source} {finding.severity}/{finding.category}: {finding.issue[:95]}")
        if name == "seeded.py":
            for source in ("static", "model"):
                lines = {f.line for f in result.findings if f.source == source}
                print(f"  {source} recall: {len(lines & SEEDED_BUGS.keys())}/{len(SEEDED_BUGS)}")
            lines = {finding.line for finding in result.findings}
            missed = [bug for line, bug in SEEDED_BUGS.items() if line not in lines]
            print(f"  combined recall: {len(SEEDED_BUGS) - len(missed)}/{len(SEEDED_BUGS)}; missed: {missed}")
        if name == "clean.py":
            by_source = [f.source for f in result.findings]
            print(
                f"  false positives (no intended defects here): "
                f"static={by_source.count('static')} model={by_source.count('model')}"
            )


if __name__ == "__main__":
    main()
