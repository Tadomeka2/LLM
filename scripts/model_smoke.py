"""Minimal local-model smoke test (downloads the model on first run).

Run:
    python scripts/model_smoke.py --model Qwen/Qwen2.5-1.5B-Instruct
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from loadpilot import LocalLLM  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    llm = LocalLLM(args.model, device=args.device)
    try:
        reply = llm.chat(
            [
                {"role": "system", "content": "You reply with one JSON object only."},
                {"role": "user", "content": 'Say hello as {"answer":"hi"}'},
            ]
        )
        print("MODEL OUTPUT:", reply)
    finally:
        llm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
