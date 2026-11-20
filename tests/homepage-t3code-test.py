#!/usr/bin/env python3
"""Check that the scoped T3 Code rollout preserves the existing service links."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t3code_homepage", ROOT / "scripts/setup-t3code-homepage.py")
homepage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(homepage)

CANONICAL = (ROOT / "deploy/homepage/config/services.yaml").read_text()
CURRENT = """- AI assistants:
    - Working link:
        id: existing-link
        href: https://example.invalid/
- Administration:
    - Another link:
        href: https://example.net/
"""


class HomepageCardTests(unittest.TestCase):
    def test_add_card_preserves_links_and_is_idempotent(self):
        updated = homepage.add_card(CURRENT, CANONICAL)
        card, other_cards = updated.split("    - Working link:", 1)
        self.assertIn(f"href: {homepage.ACCESS_URL}", card)
        self.assertEqual(
            "    - Working link:" + other_cards,
            CURRENT.removeprefix(homepage.GROUP),
        )
        self.assertEqual(homepage.add_card(updated, CANONICAL), updated)

    def test_updates_only_known_previous_cards(self):
        expected = homepage.add_card(CURRENT, CANONICAL)
        for card in (homepage.LEGACY_CARD, homepage.TAILSCALE_CARD):
            with self.subTest(card=card):
                previous = CURRENT.replace(homepage.GROUP, homepage.GROUP + card, 1)
                self.assertEqual(homepage.add_card(previous, CANONICAL), expected)

    def test_refuses_duplicate_or_conflicting_card(self):
        for current in (
            CURRENT.replace("existing-link", "homecore-t3code"),
            homepage.add_card(CURRENT, CANONICAL).replace(homepage.ACCESS_URL, "https://wrong.invalid/"),
        ):
            with self.subTest(current=current):
                with self.assertRaisesRegex(ValueError, "differs"):
                    homepage.add_card(current, CANONICAL)


if __name__ == "__main__":
    unittest.main()
