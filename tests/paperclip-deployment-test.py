#!/usr/bin/env python3
"""Protect the scoped deployment's Caddy and Homepage preimages."""
import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = runpy.run_path(str(ROOT / "scripts/deploy-paperclip.py"))


class PaperclipOverlayTest(unittest.TestCase):
    def test_caddy_overlay_preserves_existing_routes(self):
        source = (ROOT / "deploy/control-plane/Caddyfile").read_text()
        baseline = "http://:8080 {\n\treverse_proxy existing-homepage:3000\n}\n"

        def render(current):
            return DEPLOY["render"](current, source,
                                    r"^paperclip\.home\.arpa \{\n.*?^\}\n", "")

        with_site = render(baseline)
        self.assertTrue(with_site.startswith(baseline))
        self.assertIn("reverse_proxy paperclip:3100", with_site)
        candidate = DEPLOY["render_redirect"](with_site, source)
        self.assertIn("redir https://paperclip.home.arpa{uri} 308", candidate)
        self.assertIn("\treverse_proxy existing-homepage:3000\n", candidate)
        self.assertEqual(candidate, DEPLOY["render_redirect"](render(candidate), source))

    def test_homepage_inserts_only_the_canonical_card(self):
        source = (ROOT / "deploy/homepage/config/services.yaml").read_text()
        pattern = r"^    - Paperclip:\n.*?(?=^    - |^- |\Z)"
        baseline = "- AI assistants:\n    - Other app:\n        href: https://other.home.arpa/\n"
        candidate = DEPLOY["render"](baseline, source, pattern, "- AI assistants:\n")
        self.assertIn("href: https://paperclip.home.arpa/", candidate)
        self.assertTrue(candidate.endswith(baseline.split("\n", 1)[1]))
        self.assertEqual(candidate, DEPLOY["render"](candidate, source, pattern, "- AI assistants:\n"))

    def test_existing_conflicting_hostname_is_preserved(self):
        source = (ROOT / "deploy/homepage/config/services.yaml").read_text()
        baseline = "- AI assistants:\n    - Existing:\n        href: https://paperclip.home.arpa/\n"
        with self.assertRaisesRegex(ValueError, "differs"):
            DEPLOY["render"](baseline, source, r"^    - Paperclip:\n.*?(?=^    - |^- |\Z)", "- AI assistants:\n")


if __name__ == "__main__":
    unittest.main()
