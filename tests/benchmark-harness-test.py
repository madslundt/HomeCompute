#!/usr/bin/env python3
"""Integration tests for the dependency-free benchmark CLI."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HARNESS = ROOT / "benchmarks" / "harness.py"
PLAN = ROOT / "benchmarks" / "plans" / "smoke.json"
RELEASE = ROOT / "benchmarks" / "manifests" / "release.example.json"
N8N_PLAN = ROOT / "benchmarks" / "plans" / "n8n-smoke.example.json"
N8N_RELEASE = ROOT / "benchmarks" / "manifests" / "n8n-openrouter.example.json"
N8N_WORKFLOW = ROOT / "automations" / "model-benchmark" / "n8n-workflow.json"
N8N_AULA_REAL_PLAN = ROOT / "benchmarks" / "plans" / "n8n-aula-real-mcp.example.json"
N8N_AULA_REAL_WORKFLOW = (
    ROOT / "automations" / "model-benchmark" / "aula-real-mcp-evaluation.workflow.ts"
)
N8N_LAB_WORKFLOW = ROOT / "automations" / "model-benchmark" / "benchmark-lab.workflow.ts"
CODE_PLAN = ROOT / "benchmarks" / "plans" / "code-openrouter.example.json"
CODE_RELEASE = ROOT / "benchmarks" / "manifests" / "code-openrouter.example.json"


class BenchmarkHarnessTest(unittest.TestCase):
    def command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(HARNESS), *arguments],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def hash_json(value: object) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    def make_completed_run(
        self,
        root: Path,
        specifications: dict[str, dict[str, object]],
    ) -> Path:
        output = root / "run"
        output.mkdir()
        case = {
            "schema_version": 1,
            "id": "selection-case",
            "track": "coding",
            "messages": [{"role": "user", "content": "answer"}],
            "objective_checks": [{"id": "required", "type": "contains", "value": "answer"}],
            "rubric": [{"id": "quality", "weight": 1}],
        }
        plan = {
            "schema_version": 1,
            "benchmark_id": "selection-contract",
            "version": "1.0.0",
            "trials": 1,
            "cases": ["selection-case.json"],
            "candidates": [
                {"id": candidate_id, "artifact_ref": f"{candidate_id}-artifact"}
                for candidate_id in specifications
            ],
            "judges": [{"id": "judge-1", "enabled": True}],
        }
        release = {
            "schema_version": 1,
            "release_id": "release-2026-09-05",
            "artifacts": [
                {
                    "id": f"{candidate_id}-artifact",
                    "source": f"source/{candidate_id}",
                    "revision": f"revision-{candidate_id}",
                    "runtime": "benchmark-test-runtime",
                    "quantization": "test-quantization",
                }
                for candidate_id in specifications
            ],
        }
        candidate_map = {
            f"candidate_{index:02d}": candidate_id
            for index, candidate_id in enumerate(specifications, start=1)
        }
        run = {
            "schema_version": 1,
            "run_id": "retained-run",
            "benchmark_id": plan["benchmark_id"],
            "benchmark_version": plan["version"],
            "plan_sha256": self.hash_json(plan),
            "release_id": release["release_id"],
            "release_sha256": self.hash_json(release),
            "trials": 1,
            "candidate_map": candidate_map,
        }
        for name, value in (
            ("run.json", run),
            ("plan.json", plan),
            ("release.json", release),
            ("cases.json", [case]),
        ):
            (output / name).write_text(json.dumps(value))

        generations = []
        judgments = []
        for label, candidate_id in candidate_map.items():
            specification = specifications[candidate_id]
            status = specification.get("status", "completed")
            objective_passed = specification.get("objective_passed", True)
            generation = {
                "schema_version": 1,
                "case_id": case["id"],
                "track": case["track"],
                "case_sha256": self.hash_json(case),
                "candidate_label": label,
                "trial": 1,
                "status": status,
                "objective": {
                    "score": 100 if objective_passed else 0,
                    "passed": objective_passed,
                    "checks": [{"id": "required", "passed": objective_passed, "weight": 1}],
                },
            }
            if status == "completed":
                generation["duration_ms"] = specification.get("duration_ms", 100)
                judgment_status = specification.get("judgment_status", "completed")
                judgment = {
                    "schema_version": 1,
                    "judge_id": "judge-1",
                    "case_id": case["id"],
                    "candidate_label": label,
                    "trial": 1,
                    "status": judgment_status,
                }
                if judgment_status == "completed":
                    judgment["judgment"] = {
                        "scores": {"quality": specification.get("quality_score", 4)},
                        "fatal_error": specification.get("fatal_error", False),
                    }
                judgments.append(judgment)
            generations.append(generation)
        (output / "generations.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in generations)
        )
        (output / "judgments.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in judgments)
        )
        return output

    def select(
        self,
        output: Path,
        minimum_quality: int = 0,
        minimum_pass_rate: int = 0,
        destination: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            "select",
            "--run",
            str(output),
            "--minimum-quality",
            str(minimum_quality),
            "--minimum-objective-pass-rate",
            str(minimum_pass_rate),
        ]
        if destination is not None:
            arguments.extend(["--output", str(destination)])
        return self.command(*arguments)

    def test_selection_ranks_quality_before_speed_and_emits_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self.make_completed_run(Path(temporary), {
                "faster-lower-quality": {"quality_score": 3, "duration_ms": 10},
                "slower-higher-quality": {"quality_score": 4, "duration_ms": 500},
            })
            destination = Path(temporary) / "selection.json"
            result = self.select(
                output,
                minimum_quality=70,
                minimum_pass_rate=100,
                destination=destination,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), destination.resolve())
            retained = destination.read_text()
            selection = json.loads(retained)
            self.assertEqual(selection["schema_version"], 1)
            self.assertEqual(selection["document_type"], "benchmark_selection")
            self.assertEqual(selection["identity"]["benchmark_id"], "selection-contract")
            self.assertEqual(selection["identity"]["release_id"], "release-2026-09-05")
            self.assertEqual(selection["identity"]["tracks"], ["coding"])
            self.assertEqual(selection["outcome"], "winner_selected")
            self.assertEqual(selection["winner"]["candidate_id"], "slower-higher-quality")
            self.assertEqual(
                [item["candidate_id"] for item in selection["rankings"]],
                ["slower-higher-quality", "faster-lower-quality"],
            )
            release = json.loads((output / "release.json").read_text())
            retained_artifact = next(
                artifact
                for artifact in release["artifacts"]
                if artifact["id"] == "slower-higher-quality-artifact"
            )
            expected_provenance = {
                "artifact_ref": retained_artifact["id"],
                "source": retained_artifact["source"],
                "revision": retained_artifact["revision"],
                "runtime": retained_artifact["runtime"],
                "quantization": retained_artifact["quantization"],
                "artifact_sha256": self.hash_json(retained_artifact),
            }
            self.assertEqual(selection["winner"]["artifact"], expected_provenance)
            self.assertEqual(selection["rankings"][0]["artifact"], expected_provenance)
            self.assertEqual(
                next(
                    candidate["artifact"]
                    for candidate in selection["candidates"]
                    if candidate["candidate_id"] == "slower-higher-quality"
                ),
                expected_provenance,
            )
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            repeated = self.select(
                output,
                minimum_quality=70,
                minimum_pass_rate=100,
                destination=destination,
            )
            self.assertEqual(repeated.returncode, 2)
            self.assertEqual(destination.read_text(), retained)

    def test_selection_thresholds_can_produce_no_eligible_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self.make_completed_run(Path(temporary), {
                "below-quality": {"quality_score": 3},
                "below-pass-rate": {"quality_score": 4, "objective_passed": False},
            })
            result = self.select(output, minimum_quality=80, minimum_pass_rate=100)
            self.assertEqual(result.returncode, 0, result.stderr)
            selection = json.loads(result.stdout)
            evidence = {item["candidate_id"]: item for item in selection["candidates"]}
            self.assertEqual(selection["parameters"], {
                "minimum_objective_pass_rate": 100.0,
                "minimum_quality": 80.0,
            })
            self.assertEqual(selection["outcome"], "no_eligible_winner")
            self.assertIsNone(selection["winner"])
            self.assertEqual(selection["rankings"], [])
            self.assertIn(
                "below_minimum_quality",
                evidence["below-quality"]["ineligibility_reasons"],
            )
            self.assertIn(
                "below_minimum_objective_pass_rate",
                evidence["below-pass-rate"]["ineligibility_reasons"],
            )

    def test_selection_excludes_fatal_incomplete_and_objective_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self.make_completed_run(Path(temporary), {
                "fatal": {"quality_score": 4, "fatal_error": True},
                "incomplete-trial": {"status": "error"},
                "incomplete-judgment": {"judgment_status": "error"},
                "failed-objective": {"quality_score": 4, "objective_passed": False},
                "eligible": {"quality_score": 1},
            })
            result = self.select(output)
            self.assertEqual(result.returncode, 0, result.stderr)
            selection = json.loads(result.stdout)
            evidence = {item["candidate_id"]: item for item in selection["candidates"]}
            self.assertEqual(selection["winner"]["candidate_id"], "eligible")
            self.assertIn("fatal_judgment", evidence["fatal"]["ineligibility_reasons"])
            self.assertIn(
                "incomplete_trials",
                evidence["incomplete-trial"]["ineligibility_reasons"],
            )
            self.assertIn(
                "incomplete_judgments",
                evidence["incomplete-judgment"]["ineligibility_reasons"],
            )
            self.assertIn(
                "failed_objective_checks",
                evidence["failed-objective"]["ineligibility_reasons"],
            )

    def test_selection_uses_candidate_id_as_final_tie_break(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self.make_completed_run(Path(temporary), {
                "zeta": {"quality_score": 4, "duration_ms": 100},
                "alpha": {"quality_score": 4, "duration_ms": 100},
            })
            first = self.select(output)
            second = self.select(output)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout, second.stdout)
            selection = json.loads(first.stdout)
            self.assertEqual(
                [item["candidate_id"] for item in selection["rankings"]],
                ["alpha", "zeta"],
            )

    def test_selection_rejects_missing_trial_and_judgment_evidence(self) -> None:
        for missing_file, message in (
            ("generations.jsonl", "generation evidence is missing"),
            ("judgments.jsonl", "judgment evidence is missing"),
        ):
            with self.subTest(missing_file=missing_file):
                with tempfile.TemporaryDirectory() as temporary:
                    output = self.make_completed_run(
                        Path(temporary),
                        {"candidate": {"quality_score": 4}},
                    )
                    (output / missing_file).write_text("")
                    result = self.select(output)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(message, result.stderr)

    def test_selection_rejects_missing_and_ambiguous_artifact_linkage(self) -> None:
        for mutation, message in (
            ("missing", "has no release artifact linkage"),
            ("ambiguous", "has ambiguous release artifact linkage"),
        ):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    output = self.make_completed_run(
                        Path(temporary),
                        {"candidate": {"quality_score": 4}},
                    )
                    plan_path = output / "plan.json"
                    release_path = output / "release.json"
                    run_path = output / "run.json"
                    plan = json.loads(plan_path.read_text())
                    release = json.loads(release_path.read_text())
                    run = json.loads(run_path.read_text())
                    if mutation == "missing":
                        plan["candidates"][0].pop("artifact_ref")
                        run["plan_sha256"] = self.hash_json(plan)
                        plan_path.write_text(json.dumps(plan))
                    else:
                        release["artifacts"].append(dict(release["artifacts"][0]))
                        run["release_sha256"] = self.hash_json(release)
                        release_path.write_text(json.dumps(release))
                    run_path.write_text(json.dumps(run))
                    result = self.select(output)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(message, result.stderr)


    def test_validate_and_run_smoke_suite(self) -> None:
        validated = self.command("validate", "--plan", str(PLAN), "--release", str(RELEASE))
        self.assertEqual(validated.returncode, 0, validated.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            result = self.command(
                "run", "--plan", str(PLAN), "--release", str(RELEASE), "--output", str(output)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = [json.loads(line) for line in (output / "generations.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(row["objective"]["passed"] for row in rows))
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["candidates"][0]["objective_pass_rate"], 100.0)
            packet_result = self.command(
                "review-packet", "--plan", str(PLAN), "--run", str(output)
            )
            self.assertEqual(packet_result.returncode, 0, packet_result.stderr)
            packet_text = (output / "review-packet.json").read_text()
            self.assertNotIn("mock-control", packet_text)
            self.assertIn("candidate_01", packet_text)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            for artifact in output.iterdir():
                if artifact.is_file():
                    self.assertEqual(
                        stat.S_IMODE(artifact.stat().st_mode),
                        0o600,
                        artifact.name,
                    )

    def test_run_rejects_a_preexisting_output_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            output = root / "run"
            output.symlink_to(target, target_is_directory=True)
            result = self.command(
                "run",
                "--plan",
                str(PLAN),
                "--release",
                str(RELEASE),
                "--output",
                str(output),
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("output directory must be a real directory", result.stderr)
            self.assertEqual(list(target.iterdir()), [])


    def test_release_manifest_is_mandatory_and_matches_enabled_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary) / "release.json"
            release.write_text(json.dumps({
                "schema_version": 1,
                "release_id": "bad",
                "benchmark_commit": "test",
                "environment": {
                    "kind": "test",
                    "hardware": "test",
                    "operating_system": "test"
                },
                "artifacts": [{
                    "id": "different-candidate",
                    "source": "test",
                    "revision": "1",
                    "runtime": "test",
                    "quantization": "none"
                }]
            }))
            result = self.command("validate", "--plan", str(PLAN), "--release", str(release))
            self.assertEqual(result.returncode, 2)
            self.assertIn("no matching release artifact", result.stderr)

    def test_n8n_plan_and_workflow_are_side_effect_free(self) -> None:
        validated = self.command(
            "validate", "--plan", str(N8N_PLAN), "--release", str(N8N_RELEASE)
        )
        self.assertEqual(validated.returncode, 0, validated.stderr)
        workflow = json.loads(N8N_WORKFLOW.read_text())
        self.assertFalse(workflow["active"])
        node_types = {node["type"] for node in workflow["nodes"]}
        forbidden = {
            "n8n-nodes-base.scheduleTrigger",
            "n8n-nodes-base.telegram",
            "n8n-nodes-base.notion",
            "n8n-nodes-base.microsoftOutlook",
            "@n8n/n8n-nodes-langchain.mcpClientTool",
        }
        self.assertTrue(node_types.isdisjoint(forbidden))
        self.assertFalse(any("credentials" in node for node in workflow["nodes"]))

    def test_code_plan_uses_disposable_codex_adapter(self) -> None:
        validated = self.command(
            "validate", "--plan", str(CODE_PLAN), "--release", str(CODE_RELEASE)
        )
        self.assertEqual(validated.returncode, 0, validated.stderr)
        plan = json.loads(CODE_PLAN.read_text())
        self.assertTrue(all(candidate["adapter"] == "codex_exec" for candidate in plan["candidates"]))
        self.assertTrue(all(candidate["wire_api"] == "responses" for candidate in plan["candidates"]))

    def test_real_aula_plan_requires_explicit_authorization_and_has_no_actions(self) -> None:
        validated = self.command(
            "validate", "--plan", str(N8N_AULA_REAL_PLAN), "--release", str(N8N_RELEASE)
        )
        self.assertEqual(validated.returncode, 0, validated.stderr)
        plan = json.loads(N8N_AULA_REAL_PLAN.read_text())
        for candidate in plan["candidates"]:
            self.assertEqual(
                candidate["safety_acknowledgement"], "real-read-only-data-no-side-effects"
            )
            self.assertEqual(
                candidate["webhook_body"]["data_authorization"],
                "real-aula-data-approved-for-configured-model-provider",
            )
        source = N8N_AULA_REAL_WORKFLOW.read_text()
        self.assertIn("@n8n/n8n-nodes-langchain.mcpClientTool", source)
        self.assertNotIn("n8n-nodes-base.telegram", source)
        self.assertNotIn("n8n-nodes-base.notion", source)

    def test_consolidated_lab_has_three_safe_trigger_branches(self) -> None:
        source = N8N_LAB_WORKFLOW.read_text()
        self.assertEqual(source.count("type: 'n8n-nodes-base.webhook'"), 3)
        self.assertIn("@n8n/n8n-nodes-langchain.mcpClientTool", source)
        self.assertIn("@tavily/n8n-nodes-tavily.tavilyTool", source)
        self.assertNotIn("n8n-nodes-base.telegram", source)
        self.assertNotIn("n8n-nodes-base.notion", source)
        self.assertNotIn("n8n-nodes-base.scheduleTrigger", source)


if __name__ == "__main__":
    unittest.main()
