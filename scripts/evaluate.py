"""Run the evaluation suite against a local model and write a results table.

Usage:
    python scripts/evaluate.py --model Qwen/Qwen2.5-3B-Instruct
    python scripts/evaluate.py --only T01,T02,T09 --limit 3
    python scripts/evaluate.py --list-cases
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Keep evaluation runs away from the user's real watchlist.
os.environ.setdefault("WATCHLIST_FILE", str(ROOT / "logs" / "eval_watchlist.json"))

from loadpilot import Agent, LocalLLM, __version__  # noqa: E402
from loadpilot import config  # noqa: E402

CASES_PATH = ROOT / "tests" / "test_cases.json"
DEFAULT_MARKDOWN = ROOT / "docs" / "EVALUATION.md"
DEFAULT_JSON = ROOT / "docs" / "evaluation_results.json"


def evaluate_case(case: dict, result) -> tuple[str, list[str]]:
    checks = case.get("checks", {})
    if not checks:
        return "REVIEW", []

    failures: list[str] = []
    used = result.tools_used
    answer = result.answer.lower()

    for tool in checks.get("must_call", []):
        if tool not in used:
            failures.append(f"missing tool {tool}")
    must_any = checks.get("must_call_any", [])
    if must_any and not any(tool in used for tool in must_any):
        failures.append(f"none of the tools {must_any} were called")
    for tool in checks.get("forbid_tools", []):
        if tool in used:
            failures.append(f"forbidden tool {tool} was called")
    if checks.get("expect_no_tools") and used:
        failures.append(f"expected no tools, got {used}")

    if checks.get("expect_tool_error"):
        saw_error = any(
            isinstance(step.result, dict) and step.result.get("ok") is False
            for step in result.steps
        )
        if not saw_error:
            failures.append("expected at least one tool error")

    contains_any = checks.get("answer_contains_any", [])
    if contains_any and not any(phrase.lower() in answer for phrase in contains_any):
        failures.append(f"answer missing any of {contains_any}")
    for phrase in checks.get("answer_contains_all", []):
        if phrase.lower() not in answer:
            failures.append(f"answer missing '{phrase}'")
    for phrase in checks.get("answer_not_contains", []):
        if phrase.lower() in answer:
            failures.append(f"answer contains forbidden '{phrase}'")

    if checks.get("expect_clarification"):
        clarification = "?" in result.answer or any(
            word in answer
            for word in ("which", "what city", "where", "clarify", "მიუთით", "რომელ", "სად")
        )
        if not clarification:
            failures.append("expected a clarifying question")

    return ("PASS" if not failures else "FAIL"), failures


def md(value: object, limit: int = 160) -> str:
    text = str(value).replace("|", "\\|").replace("\n", "<br>")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def write_markdown(records: list[dict], model_id: str, output: Path) -> None:
    passes = sum(1 for row in records if row["status"] == "PASS")
    fails = sum(1 for row in records if row["status"] == "FAIL")
    reviews = sum(1 for row in records if row["status"] == "REVIEW")

    lines = [
        "# LoadPilot - evaluation results",
        "",
        f"- Model: `{model_id}` (local, transformers)",
        f"- Date: {date.today().isoformat()}",
        f"- LoadPilot version: {__version__}",
        f"- Cases: {len(records)} | PASS: {passes} | FAIL: {fails} | REVIEW: {reviews}",
        "",
        "Automatic checks verify which tools were called and whether the answer contains",
        "required facts. They are a reproducible first pass, not a substitute for reading",
        "the answers: FAIL rows are the interesting ones and should be reviewed manually.",
        "",
        "| ID | Category | Request | Expected behaviour | Tools used | Actual answer | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in records:
        lines.append(
            "| {id} | {category} | {request} | {expected} | {tools} | {answer} | **{status}** |".format(
                id=row["id"],
                category=row["category"],
                request=md(row["request"], 90),
                expected=md(row["expected"], 130),
                tools=md(", ".join(row["tools_used"]) or "(none)", 60),
                answer=md(row["answer"], 240),
                status=row["status"],
            )
        )
    lines.extend(
        [
            "",
            "## How to reproduce",
            "",
            "```bash",
            f"python scripts/evaluate.py --model {model_id}",
            "```",
            "",
            "The watchlist persists between cases in order; T05/T06 depend on that.",
            "",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate LoadPilot on the test suite")
    parser.add_argument("--model", default=config.DEFAULT_MODEL_ID)
    parser.add_argument("--device", default=None)
    parser.add_argument("--4bit", dest="four_bit", action="store_true")
    parser.add_argument("--only", default=None, help="Comma-separated case IDs")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N cases")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--max-new-tokens", type=int, default=config.MAX_NEW_TOKENS)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--markdown", default=str(DEFAULT_MARKDOWN))
    parser.add_argument("--json", default=str(DEFAULT_JSON))
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--list-cases", action="store_true")
    args = parser.parse_args()

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if args.only:
        wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
        cases = [case for case in cases if case["id"].upper() in wanted]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("No cases selected.")
        return 2
    if args.list_cases:
        for case in cases:
            print(f"{case['id']}: {case['request']}")
        return 0

    watchlist = Path(os.environ["WATCHLIST_FILE"])
    if watchlist.exists():
        watchlist.unlink()

    print(f"LoadPilot evaluation: {len(cases)} cases, model={args.model}")
    llm = LocalLLM(
        args.model,
        device=args.device,
        load_in_4bit=args.four_bit,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        verbose=not args.quiet,
    )
    records: list[dict] = []
    json_path = Path(args.json)
    markdown_path = Path(args.markdown)

    try:
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['id']} ...", end=" ", flush=True)
            agent = Agent(llm, max_steps=args.max_steps, verbose=False)
            started = time.time()
            try:
                result = agent.run(case["request"])
                status, failures = evaluate_case(case, result)
                answer = result.answer
                tools_used = result.tools_used
                stopped_reason = result.stopped_reason
                steps = result.to_dict()["steps"]
            except Exception as exc:  # noqa: BLE001 - one bad case must not kill the run
                status, failures = "FAIL", [f"runner error: {type(exc).__name__}: {exc}"]
                answer, tools_used, stopped_reason, steps = "", [], "runner_error", []
            duration = time.time() - started
            record = {
                "id": case["id"],
                "category": case["category"],
                "request": case["request"],
                "expected": case["expected"],
                "tools_used": tools_used,
                "answer": answer,
                "stopped_reason": stopped_reason,
                "status": status,
                "failures": failures,
                "duration_seconds": round(duration, 1),
                "steps": steps,
            }
            records.append(record)
            print(f"{status} ({duration:.1f}s) tools={tools_used}")
            if failures and not args.quiet:
                print("   " + "; ".join(failures))
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            write_markdown(records, args.model, markdown_path)
            if getattr(llm, "device", "").startswith("cuda"):
                llm._torch.cuda.empty_cache()
    finally:
        llm.close()

    write_markdown(records, args.model, markdown_path)
    passes = sum(1 for row in records if row["status"] == "PASS")
    print(f"\nSummary: {passes}/{len(records)} PASS")
    print(f"Markdown: {markdown_path}")
    print(f"JSON: {json_path}")
    return 0 if passes == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
