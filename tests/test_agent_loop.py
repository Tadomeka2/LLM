"""Agent-loop tests with a scripted LLM (no model download needed)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loadpilot import config
from loadpilot.loop import Agent


class ScriptedLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.histories: list[list[dict]] = []

    def chat(self, messages: list[dict]) -> str:
        self.histories.append([dict(message) for message in messages])
        if not self.responses:
            raise AssertionError("scripted LLM ran out of responses")
        return self.responses.pop(0)


def make_agent(responses: list[str], **kwargs) -> tuple[Agent, ScriptedLLM]:
    llm = ScriptedLLM(responses)
    return Agent(llm, system_prompt="test", **kwargs), llm


class AgentLoopTests(unittest.TestCase):
    def test_tool_then_answer(self) -> None:
        agent, llm = make_agent(
            [
                '{"tool":"search_loads","args":{"origin_state":"TX","dest_state":"GA","limit":2}}',
                '{"answer":"Found two loads."}',
            ]
        )
        result = agent.run("find loads")
        self.assertTrue(result.answer.startswith("Found two loads."))
        self.assertIn("SD-1001", result.answer)
        self.assertEqual(result.tools_used, ["search_loads"])
        self.assertTrue(result.steps[0].result["ok"])
        self.assertEqual(result.stopped_reason, "answer")
        # The second model call must contain the tool observation.
        self.assertIn("TOOL_RESULT search_loads", llm.histories[1][-1]["content"])

    def test_format_error_is_repaired(self) -> None:
        agent, _ = make_agent(
            [
                "I will search for you.",
                '{"tool":"route_distance","args":{"origin":"Dallas, TX","destination":"Atlanta, GA"}}',
                '{"answer":"About 850 miles."}',
            ]
        )
        result = agent.run("distance?")
        self.assertEqual(len(result.steps), 2)
        self.assertIsNotNone(result.steps[0].error)
        self.assertEqual(result.tools_used, ["route_distance"])
        self.assertIn("850", result.answer)

    def test_tool_error_is_visible_to_model(self) -> None:
        agent, llm = make_agent(
            [
                '{"tool":"calculate_profit","args":{"load_id":"SD-9999"}}',
                '{"answer":"That load is not on the board."}',
            ]
        )
        result = agent.run("profit?")
        self.assertFalse(result.steps[0].result["ok"])
        self.assertIn("not found", result.steps[0].result["error"])
        self.assertIn("TOOL_RESULT calculate_profit", llm.histories[1][-1]["content"])

    def test_repeated_call_is_blocked(self) -> None:
        call = '{"tool":"search_loads","args":{"origin_state":"TX"}}'
        agent, _ = make_agent([call, call, call, '{"answer":"done"}'], max_steps=6)
        result = agent.run("loop test")
        self.assertTrue(result.answer.startswith("done"))
        self.assertEqual(len(result.steps), 1)  # repeats after success were not executed

    def test_step_limit_forces_final_answer(self) -> None:
        agent, llm = make_agent(
            [
                '{"tool":"search_loads","args":{"origin_state":"TX"}}',
                '{"tool":"search_loads","args":{"origin_state":"GA"}}',
                '{"answer":"Partial result."}',
            ],
            max_steps=2,
        )
        result = agent.run("many steps")
        self.assertEqual(result.stopped_reason, "max_steps")
        self.assertTrue(result.answer.startswith("Partial result."))
        self.assertEqual(len(result.steps), 2)
        self.assertIn("STEP LIMIT REACHED", llm.histories[-1][-1]["content"])

    def test_unknown_argument_is_dropped_and_retried(self) -> None:
        agent, _ = make_agent(
            [
                '{"tool":"rank_loads","args":{"driver_city":"Dallas, TX","origin_state":"TX","dest_state":"GA","deadhead_miles":100}}',
                '{"answer":"SD-1001 is best."}',
            ]
        )
        result = agent.run("rank loads")
        step = result.steps[0]
        self.assertTrue(step.result["ok"])
        self.assertEqual(step.result["repaired_args"]["dropped"], ["deadhead_miles"])
        self.assertEqual(step.result["loads"][0]["id"], "SD-1001")

    def test_unrequested_watchlist_write_is_blocked(self) -> None:
        agent, _ = make_agent(
            [
                '{"tool":"watchlist","args":{"action":"add","load_id":"SD-1001"}}',
                '{"answer":"SD-1001 is the best load."}',
            ]
        )
        result = agent.run("Which Texas to Georgia load pays best?")
        self.assertFalse(result.steps[0].result["ok"])
        self.assertIn("not requested", result.steps[0].result["error"])
        self.assertEqual(result.answer, "SD-1001 is the best load.")

    def test_booking_request_gets_a_refusal_prefix(self) -> None:
        agent, _ = make_agent(['{"answer":"SD-1004 looks like a good load."}'])
        result = agent.run("Book load SD-1004 for me right now.")
        self.assertIn("can't book", result.answer.lower())
        self.assertIn("SD-1004", result.answer)

    def test_deadhead_warning_is_surfaced(self) -> None:
        agent, _ = make_agent(
            [
                '{"tool":"rank_loads","args":{"origin_state":"TX","dest_state":"GA"}}',
                '{"answer":"SD-1001 is best."}',
            ]
        )
        result = agent.run("Rank the Texas to Georgia loads for me.")
        self.assertIn("deadhead", result.answer.lower())
        self.assertIn("assumed", result.answer.lower())

    def test_invented_driver_city_is_removed(self) -> None:
        agent, _ = make_agent(
            [
                '{"tool":"rank_loads","args":{"driver_city":"Dallas, TX","origin_state":"TX","dest_state":"GA"}}',
                '{"answer":"SD-1001 is best for a driver in Dallas, TX."}',
            ]
        )
        result = agent.run("Rank the Texas to Georgia loads for me.")
        self.assertIsNone(result.steps[0].args.get("driver_city"))
        self.assertNotIn("Dallas", result.answer)
        self.assertIn("unspecified location", result.answer)
        self.assertIn("deadhead", result.answer.lower())

    def test_returned_load_ids_are_appended_when_model_omits_them(self) -> None:
        agent, _ = make_agent(
            [
                '{"tool":"search_loads","args":{"origin_state":"CA","dest_state":"WA","trailer_type":"enclosed","max_rate":2000}}',
                '{"answer":"I found one enclosed load for $1950."}',
            ]
        )
        result = agent.run("Find enclosed loads from California to Washington under $2,000.")
        self.assertIn("SD-1006", result.answer)
        self.assertIn("Load IDs:", result.answer)

    def test_explicit_watchlist_add_is_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlist.json"
            with patch.object(config, "WATCHLIST_FILE", path):
                agent, _ = make_agent(
                    [
                        '{"tool":"rank_loads","args":{"driver_city":"Dallas, TX","origin_state":"TX","dest_state":"GA","trailer_type":"open"}}',
                        '{"answer":"SD-1001 is best."}',
                    ]
                )
                result = agent.run(
                    "Find the best open-trailer load from Texas to Georgia for a driver "
                    "in Dallas, then add it to my watchlist."
                )
                self.assertEqual(result.tools_used[-1], "watchlist")
                self.assertTrue(result.steps[-1].result["ok"])
                self.assertIn("Saved SD-1001", result.answer)

    def test_step_limit_retries_when_model_still_calls_a_tool(self) -> None:
        agent, llm = make_agent(
            [
                '{"tool":"search_loads","args":{"origin_state":"TX"}}',
                '{"tool":"search_loads","args":{"origin_state":"GA"}}',
                '{"tool":"search_loads","args":{"origin_state":"CA"}}',
                "Plain-text summary.",
            ],
            max_steps=2,
        )
        result = agent.run("many steps")
        self.assertEqual(result.stopped_reason, "max_steps")
        self.assertTrue(result.answer.startswith("Plain-text summary."))
        self.assertIn("plain prose", llm.histories[-1][-1]["content"])

    def test_llm_failure_is_reported(self) -> None:
        class BrokenLLM:
            def chat(self, messages: list[dict]) -> str:
                raise RuntimeError("CUDA out of memory")

        result = Agent(BrokenLLM(), system_prompt="test").run("hi")
        self.assertEqual(result.stopped_reason, "llm_error")
        self.assertIn("CUDA out of memory", result.answer)

    def test_empty_message_rejected(self) -> None:
        agent, _ = make_agent([])
        with self.assertRaises(ValueError):
            agent.run("   ")


if __name__ == "__main__":
    unittest.main()
