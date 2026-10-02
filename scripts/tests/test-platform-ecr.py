#!/usr/bin/env python3
"""Check inventory failures before they can widen publisher permissions."""
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("platform_ecr", ROOT / "scripts/check-platform-ecr.py")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class PlatformInventoryTest(unittest.TestCase):
    def test_current_inventory_matches_service_repositories(self):
        names = CHECKER.validate_inventory(json.loads(CHECKER.INVENTORY.read_text()))
        self.assertEqual(set(names), {"was", "code-analyzer-agent", "error-check-agent", "alb-log-collector"})

    def test_reject_invalid_inventory(self):
        invalid = (None, {}, "was", [], ["was", "was"], [1], [True],
                   ["services/demo"], ["iris/was"], ["Was"], ["-was"], ["was\n"], ["a" * 252])
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                CHECKER.validate_inventory(value)


if __name__ == "__main__":
    unittest.main()
