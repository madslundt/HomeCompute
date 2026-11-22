#!/usr/bin/env python3
"""Check T3 Code project provisioning without a deployed app or credentials."""

import importlib.util
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t3_projects", ROOT / "scripts/setup-t3code-projects.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
PROJECTS = json.loads((ROOT / "deploy/t3code/projects.json").read_text())
HOME = PROJECTS[0]


class ProjectTests(unittest.TestCase):
    def test_manifest_and_invalid_entries(self):
        self.assertEqual(setup.validate_projects(PROJECTS), PROJECTS)
        for item in ([HOME, HOME], [{**HOME, "repository": "madslundt/../elsewhere"}],
                     [{**HOME, "branch": "--config=credential.helper"}]):
            with self.subTest(item=item), self.assertRaises(ValueError):
                setup.validate_projects(item)

    def test_existing_checkout_keeps_local_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            checkout = root / "HomeCompute"
            setup.run("git", "init", "-q", str(checkout))
            setup.run("git", "-C", str(checkout), "remote", "add", "origin",
                      "https://github.com/madslundt/HomeCompute.git")
            pending = checkout / "pending.txt"
            pending.write_text("local work")
            self.assertEqual(setup.ensure_checkout(root, HOME), checkout)
            self.assertEqual(pending.read_text(), "local work")
            setup.run("git", "-C", str(checkout), "remote", "set-url", "origin",
                      "https://github.com/madslundt/Other.git")
            with self.assertRaisesRegex(ValueError, "different origin"):
                setup.ensure_checkout(root, HOME)
            self.assertEqual(pending.read_text(), "local work")

    def test_failed_clone_leaves_no_partial_checkout(self):
        with TemporaryDirectory() as directory, patch.object(setup, "run") as run:
            root = Path(directory)
            run.side_effect = subprocess.CalledProcessError(128, "git clone")
            with self.assertRaises(subprocess.CalledProcessError):
                setup.ensure_checkout(root, HOME)
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_only_t3s_known_duplicate_result_is_idempotent(self):
        checkout = Path("/data/projects/HomeCompute")
        with patch.object(setup.subprocess, "run") as run:
            diagnostic = f"[10:24:51.421] ERROR (#2): ProjectAlreadyExistsError: An active project already exists for '{checkout}'.\n"
            run.return_value = subprocess.CompletedProcess([], 1, "", diagnostic)
            self.assertEqual(setup.ensure_project(checkout), "already registered")
            run.return_value = subprocess.CompletedProcess([], 1, "", "server unavailable")
            with self.assertRaisesRegex(ValueError, "could not register HomeCompute"):
                setup.ensure_project(checkout)


if __name__ == "__main__":
    unittest.main()
