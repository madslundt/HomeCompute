#!/usr/bin/env python3
"""Focused security-boundary tests for the benchmark harness."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))


BENCHMARK_ADAPTERS = importlib.import_module("benchmarks.benchmark_adapters")
from benchmark_correctness_test import BenchmarkCorrectnessTest
BENCHMARK_HARNESS = importlib.import_module("benchmarks.harness")


class BenchmarkSecurityTest(unittest.TestCase):
    def test_fixture_workspace_and_overlay_paths_fail_closed(self) -> None:
        case_template = {
            "schema_version": 1,
            "id": "secure-case",
            "track": "code-implementation",
            "workspace": {"source": "workspace"},
            "messages": [{"role": "user", "content": "change it"}],
            "objective_checks": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            benchmark = base / "benchmark"
            plans = benchmark / "plans"
            fixtures = benchmark / "fixtures"
            plans.mkdir(parents=True)
            fixtures.mkdir()
            outside_case = base / "outside.json"
            outside_case.write_text(json.dumps(case_template))
            plan_path = plans / "plan.json"
            plan = {
                "schema_version": 1,
                "benchmark_id": "secure-paths",
                "version": "1",
                "trials": 1,
                "cases": ["../../outside.json"],
                "candidates": [{"id": "candidate"}],
            }
            plan_path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(
                BENCHMARK_HARNESS.BenchmarkError,
                "must remain under",
            ):
                BENCHMARK_HARNESS.load_plan(plan_path)

            case_path = fixtures / "case.json"
            case_path.write_text(json.dumps(case_template))
            fixture_link = fixtures / "case-link.json"
            fixture_link.symlink_to(case_path)
            plan["cases"] = ["../fixtures/case-link.json"]
            plan_path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(
                BENCHMARK_HARNESS.BenchmarkError,
                "must not use symbolic links",
            ):
                BENCHMARK_HARNESS.load_plan(plan_path)

            fixture_link.unlink()
            plan["cases"] = ["../fixtures/case.json"]
            plan_path.write_text(json.dumps(plan))
            workspace = fixtures / "workspace"
            workspace.mkdir()
            outside_file = base / "outside.txt"
            outside_file.write_text("secret")
            (workspace / "escape").symlink_to(outside_file)
            with self.assertRaisesRegex(
                BENCHMARK_HARNESS.BenchmarkError,
                "must not contain symbolic links",
            ):
                BENCHMARK_HARNESS.load_plan(plan_path)

            (workspace / "escape").unlink()
            (workspace / "source.py").write_text("value = 1\n")
            overlay = fixtures / "overlay"
            overlay.mkdir()
            (overlay / "escape").symlink_to(outside_file)
            case_template["workspace"]["hidden_overlay"] = "overlay"
            case_path.write_text(json.dumps(case_template))
            with self.assertRaisesRegex(
                BENCHMARK_HARNESS.BenchmarkError,
                "must not contain symbolic links",
            ):
                BENCHMARK_HARNESS.load_plan(plan_path)

    def test_workspace_symlinks_special_files_and_escaping_file_checks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.txt"
            outside.write_text("outside")
            (workspace / "escape").symlink_to(outside)
            with self.assertRaisesRegex(
                BENCHMARK_HARNESS.BenchmarkError,
                "must not contain symbolic links",
            ):
                BENCHMARK_HARNESS.evaluate("", [], workspace)

            (workspace / "escape").unlink()
            fifo = workspace / "fifo"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(
                BENCHMARK_HARNESS.BenchmarkError,
                "only regular files and directories",
            ):
                BENCHMARK_HARNESS.evaluate("", [], workspace)
            fifo.unlink()

            with self.assertRaisesRegex(
                BENCHMARK_HARNESS.BenchmarkError,
                "must remain under the coding workspace",
            ):
                BENCHMARK_HARNESS.evaluate_check(
                    "",
                    {"type": "file_exists", "path": "../outside.txt"},
                    workspace,
                )

    def test_objective_commands_use_managed_workspace_only_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            completed = subprocess.CompletedProcess([], 0, "", "")
            with (
                mock.patch.object(
                    BENCHMARK_ADAPTERS.shutil,
                    "which",
                    return_value="/trusted/bin/codex",
                ),
                mock.patch.object(
                    BENCHMARK_ADAPTERS,
                    "run_process",
                    return_value=completed,
                ) as run_process,
            ):
                passed, _ = BENCHMARK_HARNESS.evaluate_check(
                    "",
                    {
                        "type": "command",
                        "argv": ["python3", "-c", "open('inside', 'w').write('ok')"],
                    },
                    workspace,
                )
            self.assertTrue(passed)
            sandbox_argv, sandbox_cwd, timeout = run_process.call_args.args
            self.assertEqual(sandbox_cwd, workspace.resolve())
            self.assertEqual(timeout, 300)
            self.assertEqual(
                sandbox_argv[:3],
                ["codex", "sandbox", "--sandbox-state-json"],
            )
            self.assertEqual(
                sandbox_argv[4:],
                ["python3", "-c", "open('inside', 'w').write('ok')"],
            )
            state = json.loads(sandbox_argv[3])
            self.assertEqual(state["permissionProfile"]["type"], "managed")
            self.assertEqual(state["permissionProfile"]["network"], "restricted")
            self.assertEqual(
                state["permissionProfile"]["file_system"]["entries"],
                [
                    {
                        "path": {
                            "type": "special",
                            "value": {"kind": "minimal"},
                        },
                        "access": "read",
                    },
                    {
                        "path": {
                            "type": "path",
                            "path": str(workspace.resolve()),
                        },
                        "access": "write",
                    },
                ],
            )
            self.assertEqual(state["sandboxCwd"], workspace.resolve().as_uri())
            environment = run_process.call_args.kwargs["environment"]
            self.assertLessEqual(set(environment), {"LANG", "LC_ALL", "PATH"})

    def test_candidate_codex_uses_outer_managed_sandbox_and_private_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            benchmark_root = Path(temporary) / "benchmark"
            fixtures = benchmark_root / "fixtures"
            source = fixtures / "workspace"
            source.mkdir(parents=True)
            (source / "source.py").write_text("value = 1\n")
            case_path = fixtures / "case.json"
            case_path.write_text("{}")
            case = {
                "id": "candidate-sandbox",
                "_source": str(case_path),
                "workspace": {"source": "workspace"},
                "messages": [{"role": "user", "content": "change it"}],
            }
            candidate = {
                "id": "candidate",
                "adapter": "codex_exec",
                "model": "test/model",
                "base_url": "https://provider.invalid/v1",
                "api_key_env": "CANDIDATE_KEY",
            }
            completed = subprocess.CompletedProcess([], 0, "", "")
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "CANDIDATE_KEY": "candidate-secret",
                        "UNRELATED_SECRET": "must-not-leak",
                        "HOMECOMPUTE_BENCHMARK_ALLOWED_ORIGINS": "https://provider.invalid",
                    },
                ),
                mock.patch.object(
                    BENCHMARK_ADAPTERS.shutil,
                    "which",
                    return_value="/trusted/bin/codex",
                ),
                mock.patch.object(
                    BENCHMARK_ADAPTERS,
                    "run_process",
                    return_value=completed,
                ) as run_process,
            ):
                response = BENCHMARK_ADAPTERS.invoke_codex(
                    candidate,
                    case,
                    benchmark_root,
                )
            try:
                candidate_call = next(
                    call
                    for call in run_process.call_args_list
                    if call.args[0][:2] == ["codex", "sandbox"]
                    and json.loads(call.args[0][3])["permissionProfile"]["network"]
                    == "enabled"
                )
                outer_argv, outer_cwd, _ = candidate_call.args
                state = json.loads(outer_argv[3])
                workspace = Path(response["_workspace"])
                self.assertEqual(outer_cwd, workspace.resolve())
                self.assertEqual(state["permissionProfile"]["type"], "managed")
                self.assertEqual(state["permissionProfile"]["network"], "enabled")
                self.assertEqual(
                    state["permissionProfile"]["file_system"]["entries"],
                    [
                        {
                            "path": {
                                "type": "special",
                                "value": {"kind": "minimal"},
                            },
                            "access": "read",
                        },
                        {
                            "path": {
                                "type": "path",
                                "path": str(workspace.resolve()),
                            },
                            "access": "write",
                        },
                    ],
                )
                inner_argv = outer_argv[4:]
                self.assertEqual(inner_argv[:2], ["codex", "exec"])
                self.assertEqual(
                    inner_argv[inner_argv.index("--sandbox") + 1],
                    "workspace-write",
                )
                final_message = Path(
                    inner_argv[inner_argv.index("--output-last-message") + 1]
                )
                self.assertTrue(final_message.is_relative_to(workspace))
                environment = candidate_call.kwargs["environment"]
                self.assertEqual(environment["CANDIDATE_KEY"], "candidate-secret")
                self.assertNotIn("UNRELATED_SECRET", environment)
                self.assertLessEqual(
                    set(environment),
                    {
                        "CANDIDATE_KEY",
                        "CODEX_HOME",
                        "HOME",
                        "LANG",
                        "LC_ALL",
                        "PATH",
                        "TMPDIR",
                    },
                )
                for name in ("CODEX_HOME", "HOME", "TMPDIR"):
                    self.assertTrue(Path(environment[name]).is_relative_to(workspace))
            finally:
                shutil.rmtree(response["_temporary_root"])


    @unittest.skipUnless(
        BENCHMARK_ADAPTERS.shutil.which("codex"),
        "Codex CLI is required for the sandbox enforcement probe",
    )
    def test_codex_sandbox_allows_workspace_write_and_denies_sibling_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            inside_passed, _ = BENCHMARK_HARNESS.evaluate_check(
                "",
                {
                    "type": "command",
                    "argv": [
                        "python3",
                        "-c",
                        "from pathlib import Path; Path('inside').write_text('ok')",
                    ],
                },
                workspace,
            )
            outside_passed, _ = BENCHMARK_HARNESS.evaluate_check(
                "",
                {
                    "type": "command",
                    "argv": [
                        "python3",
                        "-c",
                        "from pathlib import Path; Path('../outside').write_text('bad')",
                    ],
                },
                workspace,
            )
            self.assertTrue(inside_passed)
            self.assertEqual((workspace / "inside").read_text(), "ok")
            self.assertFalse(outside_passed)
            self.assertFalse((root / "outside").exists())


    def test_objective_commands_fail_closed_without_codex_or_after_creating_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            with mock.patch.object(BENCHMARK_ADAPTERS.shutil, "which", return_value=None):
                with self.assertRaisesRegex(
                    BENCHMARK_HARNESS.BenchmarkError,
                    "Codex sandbox is unavailable",
                ):
                    BENCHMARK_HARNESS.evaluate_check(
                        "",
                        {"type": "command", "argv": ["python3", "-c", "pass"]},
                        workspace,
                    )

            outside = root / "outside"
            outside.write_text("outside")

            def create_unsafe_workspace(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                (workspace / "escape").symlink_to(outside)
                return subprocess.CompletedProcess([], 0, "", "")

            with (
                mock.patch.object(
                    BENCHMARK_ADAPTERS.shutil,
                    "which",
                    return_value="/trusted/bin/codex",
                ),
                mock.patch.object(
                    BENCHMARK_ADAPTERS,
                    "run_process",
                    side_effect=create_unsafe_workspace,
                ),
            ):
                with self.assertRaisesRegex(
                    BENCHMARK_HARNESS.BenchmarkError,
                    "must not contain symbolic links",
                ):
                    BENCHMARK_HARNESS.evaluate_check(
                        "",
                        {
                            "type": "command",
                            "argv": ["python3", "-c", "pass"],
                        },
                        workspace,
                    )

if __name__ == "__main__":
    unittest.main()
