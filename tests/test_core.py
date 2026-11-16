"""No order-execution capability is imported or invoked by these tests."""
import json
import tempfile
import unittest
from pathlib import Path

from max_trading_agent.core import CATALOG, build_index, load_catalog, plan, search


class KnowledgeFoundationTests(unittest.TestCase):
    def test_official_catalog_seed_is_valid(self):
        entries = load_catalog()
        self.assertGreaterEqual(len(entries), 12)
        self.assertIn("order_send", [item["id"] for item in entries])

    def test_analyze_has_market_and_account_data(self):
        p = plan("analyze_symbol")
        ids = [x["tool"] for x in p["steps"]]
        for name in ("account_info", "symbol_info_tick", "copy_rates_from_pos", "positions_get"):
            self.assertIn(name, ids)
        self.assertEqual(p["execution"], "DISABLED")
        self.assertNotIn("order_send", ids)

    def test_prepare_entry_is_preflight_only(self):
        ids = [x["tool"] for x in plan("prepare_entry")["steps"]]
        self.assertIn("order_check", ids)
        self.assertNotIn("order_send", ids)

    def test_unknown_tasks_rejected(self):
        with self.assertRaises(ValueError):
            plan("trade_now")

    def test_duplicate_catalog_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = Path(temp) / "tools.json"
            entries = json.loads(CATALOG.read_text(encoding="utf-8"))
            catalog.write_text(json.dumps(entries + [entries[0]]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_catalog(catalog)

    def test_search_fts_and_safe_query(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "index.sqlite3"
            self.assertGreater(build_index(db), 0)
            self.assertTrue(any(x["id"] == "symbol_info_tick" for x in search(db, "bid ask")))
            self.assertEqual(search(db, "???"), [])
            self.assertIsInstance(search(db, '") OR ( "*'), list)


if __name__ == "__main__":
    unittest.main()
