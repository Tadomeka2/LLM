"""The agent loop: model -> JSON action -> tool -> observation -> answer."""

from __future__ import annotations

import json
import logging
import re
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

    @staticmethod
    def _watchlist_change_requested(message: str) -> bool:
        """Only let the model mutate the watchlist when the user asked for it."""

        text = message.lower()
        triggers = (
            "watchlist",
            "watch list",
            "add to",
            "save ",
            "save it",
            "remove from",
            "delete from",
            "clear my",
            "clear the list",
            "შეინახ",
            "დაამატ",
            "წაშალ",
            "სიაში",
        )
        return any(trigger in text for trigger in triggers)

    @staticmethod
    def _watchlist_add_requested(message: str) -> bool:
        """True only for an explicit "add/save to watchlist" request."""

        text = message.lower()
        if any(word in text for word in ("remove", "delete", "clear", "წაშალ")):
            return False
        return bool(re.search(r"\b(add|save)\b", text)) or "დაამატ" in text or "შეინახ" in text

    @staticmethod
    def _booking_requested(message: str) -> bool:
        return bool(re.search(r"\b(book|booking|reserve|reservation)\b", message, re.IGNORECASE))

    @staticmethod
    def _driver_city_mentioned(message: str, driver_city: str) -> bool:
        """Reject a driver_city the user never supplied (small-model hallucination)."""

        city = str(driver_city).split(",")[0].strip().lower()
        return bool(city) and city in message.lower()

    def _finalize(
        self,
        answer: str,
        message: str,
        trace: list[Step],
        stopped_reason: str,
        dropped_driver_city: str | None = None,
    ) -> AgentResult:
        """Apply deterministic safety/completeness guards to the final answer.

        Small local models occasionally forget a user's explicit second step or
        omit a warning that a tool returned. These guards are deliberately
        narrow: they only add a sentence or complete an explicit request, they
        never invent numbers and they never replace the model's analysis.
        """

        answer = (answer or "").strip()

        # 1. Never let the model imply that it can book a load.
        if self._booking_requested(message):
            lowered = answer.lower()
            refusals = (
                "can't book",
                "cannot book",
                "not able to book",
                "can only",
                "cannot complete",
                "not a broker",
                "can't reserve",
                "cannot reserve",
            )
            if not any(phrase in lowered for phrase in refusals):
                answer = "I can't book loads or contact brokers. " + answer

        # 1b. If the model invented a driver city and we removed it from the
        #     tool call, remove the same claim from the prose so the answer does
        #     not contradict the deadhead warning.
        if dropped_driver_city:
            city = dropped_driver_city.split(",")[0].strip()
            if city:
                pattern = re.compile(
                    rf"((?:for (?:a|the) )?driver (?:in|from|located in) )"
                    rf"{re.escape(city)}(?:,\s*[A-Z]{{2}})?",
                    re.IGNORECASE,
                )
                answer = pattern.sub(r"\1an unspecified location", answer)

        # 2. If the user explicitly asked to save a load and the model forgot the
        #    second step, finish it from the tool result it already has.
        if self._watchlist_add_requested(message):
            already_saved = any(
                step.tool == "watchlist" and (step.result or {}).get("ok") for step in trace
            )
            if not already_saved:
                load_id = None
                for step in reversed(trace):
                    result = step.result or {}
                    if not result.get("ok"):
                        continue
                    loads = result.get("loads")
                    if isinstance(loads, list) and loads and isinstance(loads[0], dict):
                        load_id = loads[0].get("id") or loads[0].get("load_id")
                        if load_id:
                            break
                if not load_id:
                    match = re.search(r"\bSD-\d{4}\b", message, re.IGNORECASE)
                    if match:
                        load_id = match.group(0).upper()
                if load_id:
                    args = {"action": "add", "load_id": load_id}
                    result = tools.call_tool("watchlist", args)
                    number = (trace[-1].number + 1) if trace else 1
                    trace.append(
                        Step(
                            number=number,
                            raw="[auto] watchlist add (explicit user request)",
                            action={"tool": "watchlist", "args": args},
                            tool="watchlist",
                            args=args,
                            result=result,
                        )
                    )
                    if result.get("ok"):
                        answer = answer.rstrip() + f"\n\nSaved {load_id} to your watchlist."
                    else:
                        answer = answer.rstrip() + (
                            f"\n\nI couldn't save {load_id} to the watchlist: "
                            f"{result.get('error', 'unknown error')}."
                        )

        # 3. Never hide a deadhead assumption returned by rank_loads.
        warning = None
        for step in reversed(trace):
            result = step.result or {}
            if result.get("deadhead_warning"):
                warning = str(result["deadhead_warning"])
                break
        if warning:
            lowered = answer.lower()
            if "deadhead" not in lowered and "assum" not in lowered:
                answer = answer.rstrip() + f"\n\nNote: {warning}"

        # 4. A load result is only useful if the user can see the load IDs.
        #    Small models sometimes describe the load without ever naming it.
        returned_ids: list[str] = []
        for step in trace:
            result = step.result or {}
            loads = result.get("loads")
            if not isinstance(loads, list):
                continue
            for item in loads:
                if not isinstance(item, dict):
                    continue
                load_id = item.get("id") or item.get("load_id")
                if load_id and load_id not in returned_ids:
                    returned_ids.append(str(load_id))
        if returned_ids and not any(load_id.lower() in answer.lower() for load_id in returned_ids):
            answer = answer.rstrip() + "\n\nLoad IDs: " + ", ".join(returned_ids[:10])

        return AgentResult(answer=answer, steps=trace, stopped_reason=stopped_reason)

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
        succeeded: set[str] = set()
        dropped_driver_city: str | None = None

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
                return self._finalize(
                    action["answer"], message, trace, "answer", dropped_driver_city
                )

            name = action["tool"]
            args = action.get("args", {})
            if (
                name == "rank_loads"
                and isinstance(args, dict)
                and args.get("driver_city")
                and not self._driver_city_mentioned(message, args["driver_city"])
            ):
                # The model sometimes invents "Dallas, TX" for a request that
                # never named a driver location. Drop it so rank_loads returns
                # its explicit deadhead_warning instead of a fake-optimal rank.
                args = dict(args)
                invented = args.pop("driver_city")
                dropped_driver_city = str(invented)
                self._log(f"[{number}] dropped invented driver_city {invented!r}")
            signature = name + "|" + json.dumps(args, sort_keys=True, ensure_ascii=False)
            calls[signature] += 1
            self._log(f"[{number}] tool {name} {json.dumps(args, ensure_ascii=False)}")

            if signature in succeeded:
                self._log(f"[{number}] duplicate successful call blocked")
                messages.extend(
                    [
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                f"You already called {name} with these exact arguments and it "
                                "succeeded. The result is above. Do not repeat it: answer the "
                                'user now with {"answer":"..."}, or call a different tool if the '
                                "task truly needs one more step."
                            ),
                        },
                    ]
                )
                continue

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

            if (
                name == "watchlist"
                and isinstance(args, dict)
                and args.get("action") in {"add", "remove", "clear"}
                and not self._watchlist_change_requested(message)
            ):
                result = {
                    "ok": False,
                    "error": "watchlist modification was not requested by the user",
                    "hint": (
                        "Do not add, remove or clear loads unless the user explicitly asks. "
                        "Answer the question or recommend a load instead."
                    ),
                }
            else:
                result = tools.call_tool(name, args)
            if result.get("ok") is False and "unknown parameter" in str(result.get("error", "")):
                # Small models often add a plausible but unsupported argument
                # (e.g. deadhead_miles to rank_loads, which derives it itself).
                # Retry once without the extra keys instead of losing the turn.
                tool_ = tools.get_tool(name)
                allowed = set((tool_.parameters.get("properties", {}) if tool_ else {}))
                dropped = sorted(set(args) - allowed)
                repaired = {key: value for key, value in args.items() if key in allowed}
                if dropped and repaired:
                    retry = tools.call_tool(name, repaired)
                    if retry.get("ok"):
                        retry["repaired_args"] = {
                            "dropped": dropped,
                            "reason": result.get("error"),
                            "note": "Unsupported arguments were ignored; the tool used its documented defaults.",
                        }
                        result = retry
                        self._log(f"[{number}] repaired args, dropped {dropped}")
            if result.get("ok", True):
                succeeded.add(signature)
            step = Step(number=number, raw=raw, action=action, tool=name, args=args, result=result)
            trace.append(step)
            self._log(f"[{number}] -> ok={result.get('ok', True)}")

            observation = json.dumps(result, ensure_ascii=False, default=str)
            if len(observation) > 7000:
                observation = observation[:7000] + "...[truncated]"
            messages.extend(
                [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"TOOL_RESULT {name}:\n{observation}\n\n"
                            "If this answers the user, reply now with "
                            '{"answer":"..."} and do not call another tool.'
                        ),
                    },
                ]
            )

        # Step budget exhausted: ask once for a tool-free summary.
        self._log(f"[{limit}] step limit reached, forcing final answer")
        messages.append(
            {
                "role": "user",
                "content": (
                    "STEP LIMIT REACHED. Do not call any tool. Write the final answer now "
                    "as plain text (no JSON, no markdown), summarizing what you found and "
                    "what is still missing."
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
        elif action and "tool" in action:
            # The model ignored the instruction and tried another tool. Ask once
            # more for prose, without giving it a tool schema to imitate.
            messages.extend(
                [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": "Do not call a tool. Reply with plain prose only, no JSON.",
                    },
                ]
            )
            try:
                raw2 = self.llm.chat(messages)
            except Exception:  # noqa: BLE001
                raw2 = raw
            action2, _ = extract_action(raw2)
            answer = action2["answer"] if action2 and "answer" in action2 else raw2.strip()
        else:
            answer = raw.strip() or "I could not finish within the step limit."
        return self._finalize(answer, message, trace, "max_steps", dropped_driver_city)
