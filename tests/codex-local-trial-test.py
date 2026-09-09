#!/usr/bin/env python3
"""Tests for explicit Codex modes and their evidence-only promotion gate."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "scripts" / "codex_session.py"
TRIAL = ROOT / "benchmarks" / "codex_trial.py"


class CodexSessionTest(unittest.TestCase):
    def run_session(self, project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SESSION), "--project", str(project), "--dry-run", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def write_policy(self, project: Path, classification: str) -> None:
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        directory = project / ".codex"
        directory.mkdir()
        (directory / "data-policy.json").write_text(
            json.dumps({"schema_version": 1, "classification": classification})
        )
        subprocess.run(
            ["git", "-C", str(project), "add", ".codex/data-policy.json"],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(project), "-c", "user.name=Codex Test",
                "-c", "user.email=codex@example.invalid", "commit", "-qm", "policy",
            ],
            check=True,
        )

    def test_cloud_is_default_for_explicitly_allowed_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.write_policy(project, "cloud_allowed")
            result = self.run_session(project)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["mode"], "Cloud")
            self.assertEqual(value["command"], ["codex"])
            self.assertFalse(value["automatic_delegation"])

    def test_unknown_repository_fails_closed_for_cloud(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_session(Path(temporary), "cloud")
            self.assertEqual(result.returncode, 2)
            self.assertIn("missing_metadata", result.stderr)
            self.assertIn("local_only", result.stderr)

    def test_uncommitted_metadata_cannot_enable_cloud(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            directory = project / ".codex"
            directory.mkdir()
            (directory / "data-policy.json").write_text(
                json.dumps({"schema_version": 1, "classification": "cloud_allowed"})
            )
            result = self.run_session(project, "cloud")
            self.assertEqual(result.returncode, 2)
            self.assertIn("uncommitted_metadata", result.stderr)

    def test_local_mode_is_whole_session_and_accepts_unknown_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_session(Path(temporary), "local", "--", "--no-alt-screen")
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["mode"], "GB10 Local")
            self.assertEqual(value["classification"], "local_only")
            self.assertEqual(
                value["command"],
                [
                    "codex", "--strict-config", "--config", 'model_provider="gb10"',
                    "--model", "coding", "--no-alt-screen",
                ],
            )

    def test_committed_local_only_policy_denies_cloud(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.write_policy(project, "local_only")
            result = self.run_session(project, "cloud")
            self.assertEqual(result.returncode, 2)
            self.assertIn("committed_metadata", result.stderr)
            self.assertIn("local_only", result.stderr)

    def test_invalid_policy_is_not_treated_as_cloud_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.write_policy(project, "sometimes_cloud")
            result = self.run_session(project, "cloud")
            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid_metadata", result.stderr)


class CodexTrialGateTest(unittest.TestCase):
    def command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(TRIAL), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def record(self, ledger: Path, task_id: str, *, successful: bool = True) -> None:
        arguments = [
            "record", "--ledger", str(ledger), "--task-id", task_id,
            "--representative", "yes", "--outcome", "completed" if successful else "failed",
            "--local-attempts", "2" if successful else "3",
            "--verification", "passed" if successful else "failed",
            "--cloud-reimplementation", "no" if successful else "yes",
            "--cloud-review", "passed" if successful else "serious_defect",
            "--duration-minutes", "4.5", "--model", "coding",
            "--runtime", "qwen3.8-27b-nvfp4-vllm",
        ]
        result = self.command(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_requires_twenty_tasks_and_seventy_percent_composite_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "trials.jsonl"
            for index in range(20):
                self.record(ledger, f"trial-{index:02d}", successful=index < 14)
            result = self.command("status", "--ledger", str(ledger))
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["decision"], "eligible_for_consideration")
            self.assertEqual(value["evidence"]["qualifying_success_rate"], 70.0)
            self.assertFalse(value["automatic_delegation_enabled"])
            self.assertTrue(all(value["checks"].values()))

    def test_gate_does_not_round_up_below_seventy_percent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "trials.jsonl"
            for index in range(20):
                self.record(ledger, f"trial-{index:02d}", successful=index < 13)
            result = self.command("status", "--ledger", str(ledger))
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["decision"], "continue_explicit_trials")
            self.assertEqual(value["evidence"]["qualifying_success_rate"], 65.0)
            self.assertFalse(value["checks"]["minimum_qualifying_success_rate"])

    def test_nineteen_qualifying_tasks_do_not_clear_count_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "trials.jsonl"
            for index in range(19):
                self.record(ledger, f"trial-{index:02d}")
            result = self.command("status", "--ledger", str(ledger))
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["decision"], "continue_explicit_trials")
            self.assertFalse(value["checks"]["minimum_representative_tasks"])

    def test_duplicate_task_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "trials.jsonl"
            self.record(ledger, "trial-01")
            result = self.command(
                "record", "--ledger", str(ledger), "--task-id", "trial-01",
                "--representative", "yes", "--outcome", "completed",
                "--local-attempts", "1", "--verification", "passed",
                "--cloud-reimplementation", "no", "--cloud-review", "passed",
                "--duration-minutes", "1", "--model", "coding", "--runtime", "runtime",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("already exists", result.stderr)

    def test_nonrepresentative_task_is_not_in_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "trials.jsonl"
            result = self.command(
                "record", "--ledger", str(ledger), "--task-id", "synthetic-smoke",
                "--representative", "no", "--outcome", "completed",
                "--local-attempts", "1", "--verification", "passed",
                "--cloud-reimplementation", "no", "--cloud-review", "passed",
                "--duration-minutes", "1", "--model", "coding", "--runtime", "runtime",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            status = self.command("status", "--ledger", str(ledger))
            value = json.loads(status.stdout)
            self.assertEqual(value["evidence"]["representative_tasks"], 0)


if __name__ == "__main__":
    unittest.main()
