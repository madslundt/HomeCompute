"""Focused correctness tests for benchmark validation, adapters, and judging."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BENCHMARK_ADAPTERS = importlib.import_module("benchmarks.benchmark_adapters")
BENCHMARK_HARNESS = importlib.import_module("benchmarks.harness")
BENCHMARK_REPORTING = importlib.import_module("benchmarks.benchmark_reporting")


class BenchmarkCorrectnessTest(unittest.TestCase):
    def test_plan_rejects_malformed_objective_checks(self) -> None:
        invalid_checks = {
            "not-array": "invalid",
            "not-object": ["invalid"],
            "unsupported": [{"type": "unknown"}],
            "missing-value": [{"type": "contains"}],
            "missing-path": [{"type": "file_exists"}],
            "invalid-regex": [{"type": "regex", "pattern": "["}],
            "empty-command": [{"type": "command", "argv": []}],
            "invalid-weight": [{"type": "valid_json", "weight": 0}],
            "invalid-exit": [
                {"type": "command", "argv": ["true"], "expected_exit": 256}
            ],
            "invalid-timeout": [
                {"type": "command", "argv": ["true"], "timeout_seconds": 0}
            ],
        }
        for name, checks in invalid_checks.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                benchmark = Path(temporary) / "benchmark"
                plans = benchmark / "plans"
                fixtures = benchmark / "fixtures"
                plans.mkdir(parents=True)
                fixtures.mkdir()
                case = {
                    "schema_version": 1,
                    "id": "objective-case",
                    "track": "text",
                    "messages": [{"role": "user", "content": "answer"}],
                    "objective_checks": checks,
                }
                (fixtures / "case.json").write_text(json.dumps(case))
                plan = {
                    "schema_version": 1,
                    "benchmark_id": "objective-validation",
                    "version": "1",
                    "trials": 1,
                    "cases": ["../fixtures/case.json"],
                    "candidates": [{"id": "candidate"}],
                }
                plan_path = plans / "plan.json"
                plan_path.write_text(json.dumps(plan))
                with self.assertRaises(BENCHMARK_HARNESS.BenchmarkError):
                    BENCHMARK_HARNESS.load_plan(plan_path)

    def test_http_adapters_convert_invalid_json_and_unicode_to_benchmark_errors(self) -> None:
        chat_candidate = {
            "id": "chat",
            "adapter": "openai_compatible",
            "model": "test/model",
            "base_url": "https://provider.invalid/v1",
        }
        n8n_candidate = {
            "id": "n8n",
            "adapter": "n8n_webhook",
            "model": "test/model",
            "webhook_url": "https://n8n.invalid/webhook",
            "safety_acknowledgement": "synthetic-inputs-no-side-effects",
        }
        case = {
            "id": "case",
            "track": "text",
            "messages": [{"role": "user", "content": "answer"}],
        }
        calls = (
            (BENCHMARK_ADAPTERS.post_chat, (chat_candidate, case["messages"])),
            (BENCHMARK_ADAPTERS.post_n8n, (n8n_candidate, case)),
        )
        for function, arguments in calls:
            for payload in (b"{", b"\xff"):
                with self.subTest(function=function.__name__, payload=payload):
                    response = mock.MagicMock()
                    response.__enter__.return_value.read.return_value = payload
                    with (
                        mock.patch.dict(
                            os.environ,
                            {"HOMECOMPUTE_BENCHMARK_ALLOWED_ORIGINS": "https://provider.invalid,https://n8n.invalid"},
                        ),
                        mock.patch.object(
                            BENCHMARK_ADAPTERS,
                            "_open_no_redirect",
                            return_value=response,
                        ),
                    ):
                        with self.assertRaisesRegex(
                            BENCHMARK_HARNESS.BenchmarkError,
                            "invalid JSON response",
                        ):
                            function(*arguments)

    def test_http_adapter_rejects_unapproved_origin_before_sending(self) -> None:
        candidate = {
            "id": "chat",
            "adapter": "openai_compatible",
            "model": "test/model",
            "base_url": "https://unapproved.invalid/v1",
        }
        with (
            mock.patch.dict(
                os.environ,
                {"HOMECOMPUTE_BENCHMARK_ALLOWED_ORIGINS": "https://approved.invalid"},
            ),
            mock.patch.object(BENCHMARK_ADAPTERS, "_open_no_redirect") as opener,
        ):
            with self.assertRaisesRegex(
                BENCHMARK_HARNESS.BenchmarkError,
                "endpoint origin .* is not approved",
            ):
                BENCHMARK_ADAPTERS.post_chat(
                    candidate,
                    [{"role": "user", "content": "answer"}],
                )
            opener.assert_not_called()

    def test_http_redirect_handler_never_forwards_request(self) -> None:
        handler = BENCHMARK_ADAPTERS._NoRedirect()
        self.assertIsNone(
            handler.redirect_request(
                mock.sentinel.request,
                mock.sentinel.response,
                302,
                "Found",
                mock.sentinel.headers,
                "https://unapproved.invalid/redirect",
            )
        )

    def _make_judge_run(self, root: Path):
        benchmark = root / "benchmark"
        plans = benchmark / "plans"
        fixtures = benchmark / "fixtures"
        plans.mkdir(parents=True)
        fixtures.mkdir()
        case = {
            "schema_version": 1,
            "id": "judge-case",
            "track": "text",
            "messages": [{"role": "user", "content": "answer"}],
            "objective_checks": [{"type": "contains", "value": "answer"}],
            "rubric": [{"id": "quality", "weight": 1}],
        }
        (fixtures / "case.json").write_text(json.dumps(case))
        plan_value = {
            "schema_version": 1,
            "benchmark_id": "judge-retry",
            "version": "1",
            "trials": 1,
            "cases": ["../fixtures/case.json"],
            "candidates": [{"id": "candidate"}],
            "judges": [{
                "id": "judge",
                "adapter": "openai_compatible",
                "model": "judge/model",
                "artifact_ref": "judge-artifact",
                "base_url": "https://judge.invalid/v1",
            }],
        }
        plan_path = plans / "plan.json"
        plan_path.write_text(json.dumps(plan_value))
        plan = BENCHMARK_HARNESS.load_plan(plan_path)
        output = root / "run"
        output.mkdir()
        retained_cases = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in plan.cases
        ]
        run = {
            "run_id": "judge-run",
            "benchmark_id": "judge-retry",
            "benchmark_version": "1",
            "release_id": "release",
            "candidate_map": {"candidate_01": "candidate"},
        }
        generation = {
            "schema_version": 1,
            "case_id": "judge-case",
            "track": "text",
            "candidate_label": "candidate_01",
            "trial": 1,
            "status": "completed",
            "text": "answer",
            "duration_ms": 1,
            "usage": {},
            "objective": {"passed": True, "score": 100, "checks": []},
        }
        for name, value in (
            ("plan.json", plan.value),
            ("cases.json", retained_cases),
            ("run.json", run),
        ):
            (output / name).write_text(json.dumps(value))
        (output / "generations.jsonl").write_text(json.dumps(generation) + "\n")
        return plan, output

    def test_judge_retries_errors_and_rejects_fixture_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan, output = self._make_judge_run(Path(temporary))
            completed = {
                "text": json.dumps({
                    "scores": [{"id": "quality", "score": 4}],
                    "fatal_error": False,
                    "unsupported_claims": [],
                    "rationale": "correct",
                    "confidence": 1,
                }),
                "usage": {},
                "duration_ms": 1,
            }
            with mock.patch.object(
                BENCHMARK_REPORTING,
                "post_chat",
                side_effect=[
                    BENCHMARK_HARNESS.BenchmarkError("transient"),
                    completed,
                ],
            ) as post_chat:
                BENCHMARK_REPORTING.judge_run(plan, output, "judge")
                self.assertFalse((output / "judgments.jsonl").exists())
                attempts = (output / "judgment-attempts.jsonl").read_text().splitlines()
                self.assertEqual(len(attempts), 1)
                BENCHMARK_REPORTING.judge_run(plan, output, "judge")
                judgments = (output / "judgments.jsonl").read_text().splitlines()
                self.assertEqual(len(judgments), 1)
                BENCHMARK_REPORTING.judge_run(plan, output, "judge")
                self.assertEqual(post_chat.call_count, 2)

            plan.cases[0]["messages"][0]["content"] = "drifted"
            with self.assertRaisesRegex(
                BENCHMARK_HARNESS.BenchmarkError,
                "do not exactly match retained run evidence",
            ):
                BENCHMARK_REPORTING.judge_run(plan, output, "judge")
            with self.assertRaisesRegex(
                BENCHMARK_HARNESS.BenchmarkError,
                "do not exactly match retained run evidence",
            ):
                BENCHMARK_REPORTING.build_review_packet(plan, output)
            self.assertFalse((output / "review-packet.json").exists())


if __name__ == "__main__":
    unittest.main()
