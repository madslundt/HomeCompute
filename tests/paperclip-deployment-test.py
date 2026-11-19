#!/usr/bin/env python3
"""Protect Paperclip's overlays and container sandbox policy."""
import hashlib
import json
import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = runpy.run_path(str(ROOT / "scripts/deploy-paperclip.py"))


class PaperclipOverlayTest(unittest.TestCase):
    def test_codex_profile_preserves_the_capability_free_docker_baseline(self):
        profile = json.loads((ROOT / "deploy/paperclip/codex-seccomp.json").read_text())
        exceptions = profile["syscalls"][18:]
        profile["syscalls"] = profile["syscalls"][:18]
        encoded = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),
                         "12fe1f56b17038687c11e3eda0813eee6cb1d63a20248b3c1d9ca95ce5086bc9")
        self.assertEqual({name for rule in exceptions for name in rule["names"]},
                         {"clone", "mount", "pivot_root", "umount2", "unshare"})
        self.assertTrue(all(rule["action"] == "SCMP_ACT_ALLOW" for rule in exceptions))
        self.assertTrue(all(rule["args"] for rule in exceptions if rule["names"] != ["pivot_root"]))

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
