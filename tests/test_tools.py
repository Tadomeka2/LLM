"""Unit tests for the deterministic tool layer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loadpilot import config
from loadpilot.models import compute_profit, compute_profit_values
from loadpilot.parsing import extract_action
from loadpilot.tools import call_tool


class ProfitTests(unittest.TestCase):
    def test_known_load_math(self) -> None:
        result = call_tool("calculate_profit", {"load_id": "SD-1004", "deadhead_miles": 120})
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_miles"], 900)
        self.assertAlmostEqual(result["fuel_cost"], 519.23, places=2)
        self.assertAlmostEqual(result["fixed_cost"], 315.0, places=2)
        self.assertAlmostEqual(result["net_profit"], 345.77, places=2)
        self.assertAlmostEqual(result["effective_rpm"], 0.384, places=3)
        self.assertEqual(result["verdict"], "marginal")

    def test_hypothetical_load(self) -> None:
        result = compute_profit_values(2000, 1000, deadhead_miles=100, mpg=10, diesel_price=4, fixed_cost_per_mile=0.2)
        self.assertEqual(result["total_miles"], 1100)
        self.assertAlmostEqual(result["fuel_cost"], 440.0)
        self.assertAlmostEqual(result["fixed_cost"], 220.0)
        self.assertAlmostEqual(result["net_profit"], 1340.0)
        self.assertEqual(result["verdict"], "excellent")

    def test_bad_mpg_is_rejected_by_tool(self) -> None:
        result = call_tool("calculate_profit", {"load_id": "SD-1004", "mpg": 0})
        self.assertFalse(result["ok"])
        self.assertIn("mpg", result["error"])

    def test_unknown_load_is_rejected(self) -> None:
        result = call_tool("calculate_profit", {"load_id": "SD-9999"})
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])
        self.assertIn("hint", result)

    def test_load_id_wins_over_extra_hypothetical_fields(self) -> None:
        result = call_tool(
            "calculate_profit",
            {"load_id": "SD-1004", "rate_usd": 4000, "loaded_miles": 120, "deadhead_miles": 120},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_miles"], 900)
        self.assertEqual(result["ignored_inputs"], ["rate_usd", "loaded_miles"])


class SearchTests(unittest.TestCase):
    def test_filters_tx_to_ga(self) -> None:
        result = call_tool(
            "search_loads",
            {
                "origin_state": "TX",
                "dest_state": "GA",
                "trailer_type": "open",
                "pickup_after": "2026-11-05",
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 5)
        self.assertTrue(all(load["origin"].endswith("TX") for load in result["loads"]))
        self.assertTrue(all(load["destination"].endswith("GA") for load in result["loads"]))

    def test_max_rate_filter(self) -> None:
        result = call_tool(
            "search_loads",
            {"origin_state": "CA", "dest_state": "WA", "trailer_type": "enclosed", "max_rate": 2000},
        )
        self.assertTrue(result["ok"])
        self.assertEqual([load["id"] for load in result["loads"]], ["SD-1006"])

    def test_invalid_state(self) -> None:
        result = call_tool("search_loads", {"origin_state": "ZZ"})
        self.assertFalse(result["ok"])
        self.assertIn("US state", result["error"])

    def test_empty_result_has_hint(self) -> None:
        result = call_tool("search_loads", {"origin_state": "ME", "dest_state": "HI"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertIn("hint", result)


class DistanceTests(unittest.TestCase):
    def test_dallas_atlanta(self) -> None:
        result = call_tool("route_distance", {"origin": "Dallas, TX", "destination": "Atlanta, GA"})
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["miles"], 700)
        self.assertLessEqual(result["miles"], 900)

    def test_unknown_city_has_suggestion(self) -> None:
        result = call_tool("route_distance", {"origin": "Dallass, TX", "destination": "Atlanta, GA"})
        self.assertFalse(result["ok"])
        self.assertIn("Did you mean", result["hint"])


class MarketTests(unittest.TestCase):
    def test_tx_ga_stats(self) -> None:
        result = call_tool("market_stats", {"origin_state": "TX", "dest_state": "GA"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 5)
        self.assertAlmostEqual(result["avg_gross_rpm"], 1.5, places=2)
        self.assertIn("best_lanes", result)

    def test_empty_sample_has_hint(self) -> None:
        result = call_tool("market_stats", {"origin_state": "ME"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertIn("hint", result)


class WatchlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = config.WATCHLIST_FILE
        self._dir = tempfile.TemporaryDirectory()
        config.WATCHLIST_FILE = Path(self._dir.name) / "watchlist.json"

    def tearDown(self) -> None:
        config.WATCHLIST_FILE = self._old
        self._dir.cleanup()

    def test_lifecycle(self) -> None:
        added = call_tool("watchlist", {"action": "add", "load_id": "SD-1004", "note": "call broker"})
        self.assertTrue(added["ok"])
        self.assertEqual(added["entry"]["load_id"], "SD-1004")

        duplicate = call_tool("watchlist", {"action": "add", "load_id": "SD-1004", "note": "updated"})
        self.assertTrue(duplicate["updated"])

        listed = call_tool("watchlist", {"action": "list"})
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["entries"][0]["note"], "updated")

        removed = call_tool("watchlist", {"action": "remove", "load_id": "SD-1004"})
        self.assertTrue(removed["removed"])
        self.assertEqual(call_tool("watchlist", {"action": "list"})["count"], 0)

    def test_clear_requires_confirmation(self) -> None:
        result = call_tool("watchlist", {"action": "clear"})
        self.assertFalse(result["ok"])
        self.assertIn("confirm", result["error"])


class ParsingTests(unittest.TestCase):
    def test_plain_tool_call(self) -> None:
        action, error = extract_action('{"tool":"search_loads","args":{"origin_state":"TX"}}')
        self.assertIsNone(error)
        self.assertEqual(action["tool"], "search_loads")

    def test_fenced_json(self) -> None:
        action, error = extract_action('```json\n{"answer":"hello"}\n```')
        self.assertIsNone(error)
        self.assertEqual(action["answer"], "hello")

    def test_prose_before_json(self) -> None:
        action, error = extract_action('I will check.\n{"tool":"route_distance","args":{"origin":"Dallas, TX","destination":"Atlanta, GA"}}')
        self.assertIsNone(error)
        self.assertEqual(action["tool"], "route_distance")

    def test_arguments_alias(self) -> None:
        action, error = extract_action('{"name":"search_loads","arguments":{"origin_state":"TX"}}')
        self.assertIsNone(error)
        self.assertEqual(action["tool"], "search_loads")

    def test_both_tool_and_answer_rejected(self) -> None:
        action, error = extract_action('{"tool":"search_loads","args":{},"answer":"done"}')
        self.assertIsNone(action)
        self.assertIn("both", error)

    def test_non_object_args_rejected(self) -> None:
        action, error = extract_action('{"tool":"search_loads","args":"TX"}')
        self.assertIsNone(action)
        self.assertIn("args", error)

    def test_garbage_rejected(self) -> None:
        action, error = extract_action("I think the best load is SD-1001.")
        self.assertIsNone(action)
        self.assertIn("no JSON object", error)


class RegistryTests(unittest.TestCase):
    def test_close_tool_name_is_resolved(self) -> None:
        result = call_tool("market-stats", {"origin_state": "TX", "dest_state": "GA"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["resolved_tool"]["requested"], "market-stats")
        self.assertEqual(result["resolved_tool"]["used"], "market_stats")

    def test_unrelated_tool_name_still_fails(self) -> None:
        result = call_tool("weather", {})
        self.assertFalse(result["ok"])
        self.assertIn("unknown tool", result["error"])


if __name__ == "__main__":
    unittest.main()
