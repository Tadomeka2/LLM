"""Interactive CLI / one-shot runner for LoadPilot.

Examples:
    python scripts/demo.py --list-tools
    python scripts/demo.py --ask "Find open loads from TX to GA"
    python scripts/demo.py                     # interactive chat
    python scripts/demo.py --model Qwen/Qwen2.5-7B-Instruct --4bit
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from loadpilot import Agent, LocalLLM, __version__  # noqa: E402
from loadpilot.tools import describe_tools  # noqa: E402


def save_run(payload: dict, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = log_dir / f"run-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def print_tools() -> None:
    for tool in describe_tools():
        print(f"\n{tool['name']}")
        print(f"  {tool['description']}")
        params = tool["parameters"].get("properties", {})
        print(f"  params: {', '.join(params) if params else '(none)'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="LoadPilot - local LLM dispatch analyst")
    parser.add_argument("--model", default=None, help="Hugging Face model id")
    parser.add_argument("--device", default=None, help="cpu / cuda / mps (auto by default)")
    parser.add_argument("--4bit", dest="four_bit", action="store_true", help="Load in 4-bit (CUDA only)")
    parser.add_argument("--ask", default=None, help="Run one request and exit")
    parser.add_argument("--max-steps", type=int, default=8, help="Maximum tool steps per request")
    parser.add_argument("--max-new-tokens", type=int, default=512, help="Generation cap per model turn")
    parser.add_argument("--temperature", type=float, default=0.1, help="Sampling temperature (0 = greedy)")
    parser.add_argument("--show-trace", action="store_true", help="Print every step after the answer")
    parser.add_argument("--quiet", action="store_true", help="Hide step-by-step tool logging")
    parser.add_argument("--list-tools", action="store_true", help="List tools and exit (no model needed)")
    parser.add_argument("--log-dir", default=str(ROOT / "logs"), help="Where to save run JSON files")
    args = parser.parse_args()

    if args.list_tools:
        print(f"LoadPilot {__version__} - {len(describe_tools())} tools")
        print_tools()
        return 0

    model_id = args.model or "Qwen/Qwen2.5-3B-Instruct"
    llm = LocalLLM(
        model_id,
        device=args.device,
        load_in_4bit=args.four_bit,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        verbose=not args.quiet,
    )
    agent = Agent(llm, max_steps=args.max_steps, verbose=not args.quiet)
    log_dir = Path(args.log_dir)

    def run_once(prompt: str) -> None:
        result = agent.run(prompt)
        print("\nAnswer:\n" + result.answer)
        if args.show_trace:
            print("\nTrace:")
            for step in result.steps:
                print(f"  {step.number}. {step.tool or 'format-error'} {json.dumps(step.args, ensure_ascii=False) if step.args else ''}")
                if step.error:
                    print(f"     error: {step.error}")
                elif step.result is not None:
                    print(f"     ok: {step.result.get('ok')}")
        path = save_run(
            {"model": model_id, "prompt": prompt, "result": result.to_dict()},
            log_dir,
        )
        print(f"\n[saved] {path}")

    try:
        if args.ask:
            run_once(args.ask)
            return 0

        print(f"LoadPilot {__version__} - model: {model_id}")
        print("Commands: /tools, /trace, /quit. Ask about loads, rates, profit or distances.\n")
        last = None
        while True:
            try:
                prompt = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not prompt:
                continue
            if prompt.lower() in {"/quit", "/exit", "quit", "exit"}:
                break
            if prompt == "/tools":
                print_tools()
                continue
            if prompt == "/trace":
                if last is None:
                    print("No run yet.")
                else:
                    for step in last.steps:
                        print(f"{step.number}. {step.tool or 'format-error'} -> {step.error or step.result}")
                continue
            last = agent.run(prompt)
            print("\nLoadPilot:\n" + last.answer + "\n")
            save_run({"model": model_id, "prompt": prompt, "result": last.to_dict()}, log_dir)
    finally:
        llm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
