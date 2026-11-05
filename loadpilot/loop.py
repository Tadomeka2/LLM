"""The agent loop: model -> JSON action -> tool -> observation -> answer."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field

from . import tools
from .llm import ChatModel
from .parsing import extract_action
from .prompts import build_system_prompt

log = logging.getLogger(__name__)


@dataclass
class Step:
    number: int
    raw: str
    action: dict | None = None
    tool: str | None = None
    args: dict | None = None
    result: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "tool": self.tool,
            "args": self.args,
            "result": self.result,
            "error": self.error,
            "raw": self.raw,
        }


@dataclass
class AgentResult:
    answer: str
    steps: list[Step] = field(default_factory=list)
    stopped_reason: str = "answer"

    @property
    def tools_used(self) -> list[str]:
        return [step.tool for step in self.steps if step.tool]

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "stopped_reason": self.stopped_reason,
            "tools_used": self.tools_used,
            "steps": [step.to_dict() for step in self.steps],
        }


class Agent:
    """A small, explicit tool-calling agent.

    The model only chooses an action; Python validates the arguments and runs
    the tool. Every failure becomes an observation the model can recover from.
    """

    def __init__(
        self,
        llm: ChatModel,
        *,
        max_steps: int = 8,
        verbose: bool = False,
        system_prompt: str | None = None,
    ) -> None:
        self.llm = llm
        self.max_steps = max_steps
        self.verbose = verbose
        self.system_prompt = system_prompt or build_system_prompt()

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def run(self, user_message: str, *, max_steps: int | None = None) -> AgentResult:
        message = (user_message or "").strip()
        if not message:
            raise ValueError("user_message must not be empty")

        limit = max_steps or self.max_steps
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": message},
        ]
        trace: list[Step] = []
        format_errors = 0
        calls: Counter[str] = Counter()

        for number in range(1, limit + 1):
            try:
                raw = self.llm.chat(messages)
            except Exception as exc:  # noqa: BLE001 - surface a useful message
                log.exception("LLM generation failed")
                return AgentResult(
                    answer=f"The local model failed to generate a response: {type(exc).__name__}: {exc}",
                    steps=trace,
                    stopped_reason="llm_error",
                )

            action, error = extract_action(raw)
            if error:
                format_errors += 1
                trace.append(Step(number=number, raw=raw, error=error))
                self._log(f"[{number}] invalid format: {error}")
                if format_errors >= 3:
                    fallback = raw.strip() or "I could not produce a valid response."
                    return AgentResult(
                        answer=fallback,
                        steps=trace,
                        stopped_reason="format_fallback",
                    )
                messages.extend(
                    [
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                f"FORMAT ERROR: {error}\n"
                                'Reply with exactly ONE JSON object: '
                                '{"tool":"<name>","args":{...}} or {"answer":"..."}. '
                                "No markdown, no prose."
                            ),
                        },
                    ]
                )
                continue

            format_errors = 0

            if "answer" in action:
                self._log(f"[{number}] answer")
                return AgentResult(answer=action["answer"], steps=trace, stopped_reason="answer")

            name = action["tool"]
            args = action.get("args", {})
            signature = name + "|" + json.dumps(args, sort_keys=True, ensure_ascii=False)
            calls[signature] += 1
            self._log(f"[{number}] tool {name} {json.dumps(args, ensure_ascii=False)}")

            if calls[signature] > 2:
                self._log(f"[{number}] repeated call blocked")
                messages.extend(
                    [
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                f"You already called {name} with these exact arguments twice. "
                                "Do not repeat it. Use the previous TOOL_RESULT, try a different "
                                'tool/arguments, or finish with {"answer":"..."}.'
                            ),
                        },
                    ]
                )
                continue

            result = tools.call_tool(name, args)
            step = Step(number=number, raw=raw, action=action, tool=name, args=args, result=result)
            trace.append(step)
            self._log(f"[{number}] -> ok={result.get('ok', True)}")

            observation = json.dumps(result, ensure_ascii=False, default=str)
            if len(observation) > 7000:
                observation = observation[:7000] + "...[truncated]"
            messages.extend(
                [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"TOOL_RESULT {name}:\n{observation}"},
                ]
            )

        # Step budget exhausted: ask once for a tool-free summary.
        self._log(f"[{limit}] step limit reached, forcing final answer")
        messages.append(
            {
                "role": "user",
                "content": (
                    "STEP LIMIT REACHED. Do not call any tool. Reply now with exactly "
                    '{"answer":"..."} summarizing what you found and what is still missing.'
                ),
            }
        )
        try:
            raw = self.llm.chat(messages)
        except Exception as exc:  # noqa: BLE001
            return AgentResult(
                answer=f"The step limit was reached and the final generation failed: {exc}",
                steps=trace,
                stopped_reason="max_steps",
            )
        action, _ = extract_action(raw)
        if action and "answer" in action:
            answer = action["answer"]
        else:
            answer = raw.strip() or "I could not finish within the step limit."
        return AgentResult(answer=answer, steps=trace, stopped_reason="max_steps")
