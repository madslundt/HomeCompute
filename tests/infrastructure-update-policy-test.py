#!/usr/bin/env python3
"""Approval classification tests; no host, registry, or HA connections."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from infrastructure_update_policy import classify_plan, classify_version


class VersionPolicyTest(unittest.TestCase):
    def test_same_major_semver_minor_and_patch_are_routine(self):
        for old, new in [("1.2.3", "1.2.4"), ("v2.3.0", "v2.4.0"), ("3.9.9", "3.10.0")]:
            with self.subTest(old=old, new=new):
                result = classify_version(old, new, "semver")
                self.assertEqual(result["classification"], "routine")
                self.assertFalse(result["approval_required"])

    def test_major_downgrade_unknown_and_prerelease_require_approval(self):
        for old, new, reason in [("1.9.0", "2.0.0", "major_version_changed"),
                                 ("2.1.0", "2.0.0", "downgrade"),
                                 ("1.2.3", "1.2.4-rc.1", "unknown_or_prerelease"),
                                 ("latest", "1.2.4", "unknown_or_prerelease"),
                                 ("1.2.3", "01.2.4", "unknown_or_prerelease"),
                                 ("0.2.1", "0.3.0", "unstable_minor_changed")]:
            with self.subTest(old=old, new=new):
                result = classify_version(old, new, "semver")
                self.assertTrue(result["approval_required"])
                self.assertEqual(result["reason"], reason)

    def test_semver_build_metadata_does_not_invent_version_order(self):
        self.assertEqual(classify_version("1.2.3+one", "1.2.3+one", "semver")["classification"], "unchanged")
        self.assertTrue(classify_version("1.2.3+one", "1.2.3+two", "semver")["approval_required"])

    def test_ha_calendar_month_or_year_change_requires_approval(self):
        self.assertFalse(classify_version("2026.10.1", "2026.10.2", "calendar")["approval_required"])
        for new in ["2026.11.0", "2027.1.0"]:
            self.assertEqual(classify_version("2026.10.1", new, "calendar")["reason"], "calendar_release_changed")
        self.assertTrue(classify_version("2026.10.1", "2026.13.0", "calendar")["approval_required"])

    def test_nixos_release_channel_change_requires_approval(self):
        self.assertFalse(classify_version("nixos-26.05.1", "nixos-26.05.2", "nixos")["approval_required"])
        self.assertEqual(classify_version("26.05", "26.11", "nixos")["reason"], "release_channel_changed")
        for new in ["unstable", "26.06", "26.05.1-abc"]:
            self.assertTrue(classify_version("26.05", new, "nixos")["approval_required"])

    def test_debian_numeric_upstream_major_epoch_and_revision_order(self):
        for old, new in [("1:2.9.0-1ubuntu2", "1:2.10.0-1ubuntu1"),
                         ("2.4-1ubuntu9", "2.4-1ubuntu10"), ("2.4", "2.4-1"),
                         ("2.4-1", "2.4-1+deb12u1")]:
            with self.subTest(old=old, new=new):
                self.assertFalse(classify_version(old, new, "debian")["approval_required"])
        for old, new, reason in [("1:2.4-1", "2:2.4-1", "epoch_changed"),
                                 ("2.4-1", "3.0-1", "major_version_changed"),
                                 ("2.4-1ubuntu10", "2.4-1ubuntu9", "downgrade"),
                                 ("2:2.4-1", "1:9.4-1", "downgrade"),
                                 ("2.4-1", "2.5~rc1-1", "unknown_or_prerelease"),
                                 ("2.4-1", "2.5+dfsg1-1", "unknown_or_prerelease")]:
            with self.subTest(old=old, new=new):
                result = classify_version(old, new, "debian")
                self.assertTrue(result["approval_required"])
                self.assertEqual(result["reason"], reason)

    def test_invalid_types_schemes_and_unbounded_metadata_rejected(self):
        for args in [(True, "1.2.4", "semver"), ("1.2.3", [], "semver"),
                     ("1.2.3", "1.2.4", "docker"), ("1.2.3", "1" * 257, "semver")]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError): classify_version(*args)


class PlanPolicyTest(unittest.TestCase):
    def setUp(self):
        self.plan = {"operation": "update", "target": "home-core", "updates": [
            {"component": "homepage", "scheme": "semver", "installed": "1.2.3", "candidate": "1.2.4"}]}

    def test_all_routine_plan_can_use_standing_authorization(self):
        for target in ["home-core", "home-spark", "home-assistant"]:
            result = classify_plan(dict(self.plan, target=target))
            self.assertEqual(result["classification"], "routine")
            self.assertFalse(result["approval_required"])
        self.assertEqual(self.plan["updates"][0]["candidate"], "1.2.4")

    def test_single_major_or_unknown_component_requires_whole_plan_approval(self):
        for candidate in ["2.0.0", "latest", "1.1.0", "1.3.0-rc.1"]:
            plan = copy.deepcopy(self.plan)
            plan["updates"].append(dict(plan["updates"][0], component="n8n", candidate=candidate))
            self.assertTrue(classify_plan(plan)["approval_required"])

    def test_fix_always_requires_approval_including_version_updates(self):
        for updates in [[], self.plan["updates"]]:
            result = classify_plan({"operation": "fix", "target": "home-spark", "updates": updates})
            self.assertTrue(result["approval_required"])
            self.assertEqual(result["reason"], "fix_requires_approval")

    def test_unchanged_plan_is_a_noop(self):
        self.plan["updates"][0]["candidate"] = "1.2.3"
        self.assertEqual(classify_plan(self.plan)["classification"], "unchanged")

    def test_exact_schema_targets_components_and_duplicates_required(self):
        bad = [dict(self.plan, command="reboot"), dict(self.plan, target="other-host"),
               dict(self.plan, operation="restart"), dict(self.plan, updates=[]),
               dict(self.plan, updates={}), dict(self.plan, updates=self.plan["updates"] * 2)]
        for change in [{"extra": "data"}, {"component": "../n8n"}, {"scheme": "arbitrary"}]:
            plan = copy.deepcopy(self.plan)
            plan["updates"][0].update(change)
            bad.append(plan)
        for plan in bad:
            with self.subTest(plan=plan):
                with self.assertRaises(ValueError): classify_plan(plan)


if __name__ == "__main__":
    unittest.main()
