"""Agent-loop tests with a scripted LLM (no model download needed)."""

from __future__ import annotations

import unittest

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
        self.assertEqual(result.answer, "Found two loads.")
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
        self.assertEqual(result.answer, "done")
        self.assertEqual(len(result.steps), 2)  # third identical call was not executed

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
        self.assertEqual(result.answer, "Partial result.")
        self.assertEqual(len(result.steps), 2)
        self.assertIn("STEP LIMIT REACHED", llm.histories[-1][-1]["content"])

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
