import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def install_zendriver_stub():
    try:
        __import__("zendriver")
        __import__("zendriver.core.config", fromlist=["Config"])
        return
    except ModuleNotFoundError as exc:
        if exc.name != "zendriver":
            raise

    zendriver = types.ModuleType("zendriver")
    zendriver.cdp = types.SimpleNamespace()
    core = types.ModuleType("zendriver.core")
    config = types.ModuleType("zendriver.core.config")
    config.Config = type("Config", (), {})
    core.config = config

    sys.modules.setdefault("zendriver", zendriver)
    sys.modules.setdefault("zendriver.core", core)
    sys.modules.setdefault("zendriver.core.config", config)


install_zendriver_stub()

from platforms.ibon import (  # noqa: E402
    _parse_ibon_date_keyword_array,
    _select_ibon_date_buttons_by_keyword,
)


class IbonDateSelectionTest(unittest.TestCase):
    def setUp(self):
        self.buttons = [
            {"date_context": "7/10 A", "disabled": False, "node_id": 10},
            {"date_context": "7/10 B", "disabled": False, "node_id": 11},
            {"date_context": "7/11 A", "disabled": False, "node_id": 12},
            {"date_context": "7/11 B", "disabled": False, "node_id": 13},
        ]

    def select(self, keyword, mode):
        return _select_ibon_date_buttons_by_keyword(
            self.buttons,
            _parse_ibon_date_keyword_array(keyword),
            mode,
        )

    def test_json_keyword_top_to_bottom_selects_first_match(self):
        matched, found, keyword_index, keyword = self.select('"7/11"', "from top to bottom")

        self.assertTrue(found)
        self.assertEqual(0, keyword_index)
        self.assertEqual("7/11", keyword)
        self.assertEqual("7/11 A", matched[0]["date_context"])

    def test_json_keyword_bottom_to_top_selects_last_match(self):
        matched, found, _, _ = self.select('"7/11"', "from bottom to top")

        self.assertTrue(found)
        self.assertEqual("7/11 B", matched[0]["date_context"])
        self.assertEqual(13, matched[0]["node_id"])

    def test_underscore_mode_is_accepted_for_bottom_to_top(self):
        matched, found, _, _ = self.select('"7/11"', "from_bottom_to_top")

        self.assertTrue(found)
        self.assertEqual("7/11 B", matched[0]["date_context"])

    def test_single_keyword_space_parts_are_and_matched(self):
        matched, found, _, _ = self.select('"7/11 B"', "from top to bottom")

        self.assertTrue(found)
        self.assertEqual("7/11 B", matched[0]["date_context"])

    def test_keyword_priority_is_preserved_before_bottom_mode(self):
        matched, found, keyword_index, keyword = self.select('"7/11","7/10"', "from bottom to top")

        self.assertTrue(found)
        self.assertEqual(0, keyword_index)
        self.assertEqual("7/11", keyword)
        self.assertEqual("7/11 B", matched[0]["date_context"])

    def test_later_keyword_is_used_when_earlier_keyword_has_no_match(self):
        matched, found, keyword_index, keyword = self.select('"7/12","7/10"', "from bottom to top")

        self.assertTrue(found)
        self.assertEqual(1, keyword_index)
        self.assertEqual("7/10", keyword)
        self.assertEqual("7/10 B", matched[0]["date_context"])

    def test_center_and_random_return_all_matches_for_target_picker(self):
        center_matches, center_found, _, _ = self.select('"7/11"', "center")
        random_matches, random_found, _, _ = self.select('"7/11"', "random")

        expected = ["7/11 A", "7/11 B"]
        self.assertTrue(center_found)
        self.assertTrue(random_found)
        self.assertEqual(expected, [button["date_context"] for button in center_matches])
        self.assertEqual(expected, [button["date_context"] for button in random_matches])

    def test_no_match_returns_empty_result_for_caller_fallback(self):
        matched, found, keyword_index, keyword = self.select('"7/12"', "from bottom to top")

        self.assertFalse(found)
        self.assertEqual([], matched)
        self.assertIsNone(keyword_index)
        self.assertIsNone(keyword)

    def test_raw_keyword_and_semicolon_fallback_are_supported(self):
        self.assertEqual(["7/11"], _parse_ibon_date_keyword_array("7/11"))
        self.assertEqual(["7/12", "7/10"], _parse_ibon_date_keyword_array("7/12;7/10"))

        matched, found, keyword_index, keyword = self.select("7/12;7/10", "from bottom to top")

        self.assertTrue(found)
        self.assertEqual(1, keyword_index)
        self.assertEqual("7/10", keyword)
        self.assertEqual("7/10 B", matched[0]["date_context"])


if __name__ == "__main__":
    unittest.main()
