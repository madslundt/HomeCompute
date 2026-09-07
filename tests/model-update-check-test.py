#!/usr/bin/env python3
"""Behavioral tests for the dependency-free scheduled update monitor."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import os
import urllib.request
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("model_update_check", SCRIPTS / "check-model-updates.py")
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)
from update_check_http import (
    SafeRedirectHandler,
    SourceError,
    ValidationError,
    fetch_json,
    validate_request_url,
)

PUBLIC_RESOLVER = lambda host: ["93.184.216.34"]
NOW = lambda: datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def source(source_id: str, model: str, benchmark_id: str | None = None) -> dict[str, Any]:
    value = {
        "id": source_id,
        "kind": "huggingface_model",
        "label": source_id,
        "role": "test candidate",
        "request_url": f"https://huggingface.co/api/models/{model}",
        "project_url": f"https://huggingface.co/{model}",
    }
    if benchmark_id:
        value["benchmark_artifact_source"] = f"https://huggingface.co/{model}"
        value["benchmark_candidate_ids"] = [benchmark_id]
    return value


def watchlist() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project": "HomeCompute",
        "policy": {"mode": "review-only", "automatic_upgrade": False, "reason": "Human review is required."},
        "sources": [
            source("primary-text-model", "nvidia/primary", "active-benchmark"),
            source("quality-candidate", "Qwen/challenger", "challenger-benchmark"),
            {
                "id": "vllm-runtime",
                "kind": "github_release",
                "label": "vLLM",
                "role": "runtime",
                "request_url": "https://api.github.com/repos/vllm-project/vllm/releases/latest",
                "project_url": "https://github.com/vllm-project/vllm/releases",
            },
        ],
    }


def evidence(quality: float, duration_ms: float = 10) -> dict[str, Any]:
    return {
        "expected_evaluations": 2,
        "completed_evaluations": 2,
        "expected_judgments": 2,
        "completed_judgments": 2,
        "fatal_judgments": 0,
        "failed_objective_evaluations": 0,
        "rubric_total": quality,
        "rubric_maximum": 100,
        "quality": quality,
        "objective_pass_rate": 100,
        "median_duration_ms": duration_ms,
    }


def artifact(artifact_ref: str, model: str, revision: str) -> dict[str, Any]:
    retained = {
        "id": artifact_ref,
        "source": f"https://huggingface.co/{model}",
        "revision": revision,
        "runtime": "local vLLM",
        "quantization": "test quantization",
    }
    return {
        "artifact_ref": artifact_ref,
        "source": retained["source"],
        "revision": revision,
        "runtime": retained["runtime"],
        "quantization": retained["quantization"],
        "artifact_sha256": hashlib.sha256(
            json.dumps(retained, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def replace_artifact(document: dict[str, Any], candidate_index: int, **changes: str) -> None:
    candidate = document["candidates"][candidate_index]
    retained = {
        "id": candidate["artifact"]["artifact_ref"],
        "source": candidate["artifact"]["source"],
        "revision": candidate["artifact"]["revision"],
        "runtime": candidate["artifact"]["runtime"],
        "quantization": candidate["artifact"]["quantization"],
    }
    retained.update({"id" if key == "artifact_ref" else key: value for key, value in changes.items()})
    candidate["artifact"] = {
        "artifact_ref": retained["id"],
        "source": retained["source"],
        "revision": retained["revision"],
        "runtime": retained["runtime"],
        "quantization": retained["quantization"],
        "artifact_sha256": hashlib.sha256(
            json.dumps(retained, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    for ranking in document["rankings"]:
        if ranking["candidate_id"] == candidate["candidate_id"]:
            ranking["artifact"] = candidate["artifact"]
    if document["winner"] and document["winner"]["candidate_id"] == candidate["candidate_id"]:
        document["winner"]["artifact"] = candidate["artifact"]


def selection() -> dict[str, Any]:
    rankings = [
        {
            "rank": 1,
            "candidate_id": "challenger-benchmark",
            "candidate_label": "challenger",
            "quality": 90,
            "objective_pass_rate": 100,
            "median_duration_ms": 10,
            "artifact": artifact("challenger-artifact", "Qwen/challenger", "b" * 40),
        },
        {
            "rank": 2,
            "candidate_id": "active-benchmark",
            "candidate_label": "active",
            "quality": 80,
            "objective_pass_rate": 100,
            "median_duration_ms": 12,
            "artifact": artifact("active-artifact", "nvidia/primary", "a" * 40),
        },
    ]
    return {
        "schema_version": 1,
        "document_type": "benchmark_selection",
        "identity": {
            "run_id": "run-1",
            "benchmark_id": "bench",
            "benchmark_version": "1.0.0",
            "plan_sha256": "a" * 64,
            "release_id": "release-1",
            "release_sha256": "b" * 64,
            "tracks": ["code"],
        },
        "parameters": {"minimum_quality": 70, "minimum_objective_pass_rate": 100},
        "candidates": [
            {
                "candidate_id": "challenger-benchmark",
                "candidate_label": "challenger",
                "eligible": True,
                "ineligibility_reasons": [],
                "evidence": evidence(90),
                "artifact": artifact("challenger-artifact", "Qwen/challenger", "b" * 40),
                "rank": 1,
            },
            {
                "candidate_id": "active-benchmark",
                "candidate_label": "active",
                "eligible": True,
                "ineligibility_reasons": [],
                "evidence": evidence(80, 12),
                "artifact": artifact("active-artifact", "nvidia/primary", "a" * 40),
                "rank": 2,
            },
        ],
        "rankings": rankings,
        "outcome": "winner_selected",
        "winner": rankings[0],
    }


class FakeFetcher:
    def __init__(self) -> None:
        self.revisions = {"primary": "a" * 40, "challenger": "b" * 40, "releases/latest": "v1.0.0"}
        self.fail_fragment: str | None = None

    def __call__(self, url: str, kind: str, resolver: Any) -> Any:
        if self.fail_fragment and self.fail_fragment in url:
            raise SourceError("network_error", "upstream request failed (timeout)")
        for fragment, revision in self.revisions.items():
            if fragment in url:
                if kind == "huggingface_model":
                    return {"sha": revision, "cardData": {"prompt": "content-canary"}}
                return {"tag_name": revision, "body": "content-canary"}
        raise AssertionError(f"unhandled fake URL: {url}")


class ModelUpdateCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.watchlist_path = self.directory / "watchlist.json"
        self.state_path = self.directory / "state.json"
        self.report_path = self.directory / "report.json"
        self.watchlist_path.write_text(json.dumps(watchlist()))
        self.fetcher = FakeFetcher()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_check(self, pins: Path | None = None, selection_path: Path | None = None) -> dict[str, Any]:
        return CHECKER.execute(
            self.watchlist_path,
            self.state_path,
            self.report_path,
            pins,
            selection_path,
            fetcher=self.fetcher,
            resolver=PUBLIC_RESOLVER,
            clock=NOW,
        )

    def test_first_run_baselines_and_repeated_unchanged_run_is_quiet(self) -> None:
        first = self.run_check()
        self.assertEqual(first["status"], "baseline")
        self.assertEqual(first["summary"]["baselined"], 3)
        second = self.run_check()
        self.assertEqual(second["status"], "quiet")
        self.assertEqual(second["changes"], [])
        self.assertNotIn("content-canary", self.report_path.read_text())
        self.assertNotIn("content-canary", self.state_path.read_text())

    def test_changes_and_pin_drift_are_reported(self) -> None:
        self.run_check()
        self.fetcher.revisions["challenger"] = "c" * 40
        changed = self.run_check()
        self.assertEqual([row["source_id"] for row in changed["changes"]], ["quality-candidate"])
        pins = self.directory / "pins.json"
        pins.write_text(json.dumps({
            "schema_version": 1,
            "active_source_id": "primary-text-model",
            "pins": {"primary-text-model": "0" * 40},
        }))
        drift = self.run_check(pins)
        self.assertEqual([row["source_id"] for row in drift["pin_drift"]], ["primary-text-model"])

    def test_fetch_error_is_reported_without_erasing_last_success(self) -> None:
        self.run_check()
        before = json.loads(self.state_path.read_text())["sources"]["quality-candidate"]
        self.fetcher.fail_fragment = "challenger"
        report = self.run_check()
        after = json.loads(self.state_path.read_text())["sources"]["quality-candidate"]
        self.assertEqual(after, before)
        self.assertEqual(report["source_errors"][0]["source_id"], "quality-candidate")
        self.assertEqual(report["status"], "attention")

    def test_malformed_watchlist_target_creates_no_state_or_report(self) -> None:
        document = watchlist()
        document["sources"][0]["request_url"] = "http://huggingface.co/api/models/nvidia/primary"
        self.watchlist_path.write_text(json.dumps(document))
        with self.assertRaises(ValidationError):
            self.run_check()
        self.assertFalse(self.state_path.exists())
        self.assertFalse(self.report_path.exists())

    def test_non_allowlisted_private_and_redirect_targets_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            validate_request_url("https://127.0.0.1/api/models/x/y", "huggingface_model", PUBLIC_RESOLVER)
        with self.assertRaises(ValidationError):
            validate_request_url("https://huggingface.co/api/models/x/y", "huggingface_model", lambda host: ["169.254.1.1"])
        handler = SafeRedirectHandler("huggingface_model", PUBLIC_RESOLVER)
        with self.assertRaises(ValidationError):
            handler.redirect_request(None, None, 302, "Found", {}, "https://example.com/api/models/x/y")

    def test_http_fetcher_disables_ambient_proxy_inheritance(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://proxy.invalid:8080",
                "HTTPS_PROXY": "http://proxy.invalid:8080",
            },
        ):
            with patch("update_check_http.urllib.request.build_opener") as build_opener:
                build_opener.return_value.open.side_effect = TimeoutError()
                with self.assertRaises(SourceError):
                    fetch_json(
                        "https://huggingface.co/api/models/nvidia/primary",
                        "huggingface_model",
                        PUBLIC_RESOLVER,
                    )
        handlers = build_opener.call_args.args
        proxy_handler = next(
            handler for handler in handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        )
        self.assertEqual(proxy_handler.proxies, {})

    def test_benchmark_winner_is_evidence_and_never_promotion(self) -> None:
        pins = self.directory / "pins.json"
        pins.write_text(json.dumps({
            "schema_version": 1,
            "active_source_id": "primary-text-model",
            "pins": {"primary-text-model": "a" * 40},
        }))
        selection_path = self.directory / "selection.json"
        selection_path.write_text(json.dumps(selection()))
        report = self.run_check(pins, selection_path)
        classifications = {row["source_id"]: row for row in report["candidate_classifications"]}
        self.assertEqual(classifications["quality-candidate"]["classification"], "outperforms_active")
        self.assertEqual(classifications["primary-text-model"]["classification"], "not_better")
        self.assertFalse(classifications["quality-candidate"]["promotion_allowed"])
        self.assertFalse(report["automatic_download"])
        self.assertFalse(report["automatic_promotion"])

    def test_missing_artifact_forged_eligibility_and_forged_rank_are_rejected(self) -> None:
        forged_eligibility = selection()
        forged_eligibility["candidates"][0]["evidence"]["fatal_judgments"] = 1
        missing_artifact = selection()
        del missing_artifact["candidates"][0]["artifact"]
        forged_rank = selection()
        forged_rank["rankings"].reverse()
        for rank, ranking in enumerate(forged_rank["rankings"], 1):
            ranking["rank"] = rank
            next(
                candidate for candidate in forged_rank["candidates"]
                if candidate["candidate_id"] == ranking["candidate_id"]
            )["rank"] = rank
        forged_rank["winner"] = dict(forged_rank["rankings"][0])
        for name, document in (
            ("eligibility", forged_eligibility),
            ("rank", forged_rank),
            ("missing-artifact", missing_artifact),
        ):
            with self.subTest(name=name):
                path = self.directory / f"{name}.json"
                path.write_text(json.dumps(document))
                with self.assertRaises(ValidationError):
                    CHECKER.load_selection(path)

    def test_unbound_benchmark_artifacts_cannot_outperform_active(self) -> None:
        pins = self.directory / "pins.json"
        pins.write_text(json.dumps({
            "schema_version": 1,
            "active_source_id": "primary-text-model",
            "pins": {"primary-text-model": "a" * 40},
        }))
        scenarios = (
            (1, {"revision": "c" * 40}, "active_artifact_revision_mismatch"),
            (0, {"revision": "c" * 40}, "challenger_artifact_revision_mismatch"),
            (0, {"source": "OpenRouter Qwen/challenger"}, "challenger_artifact_source_mismatch"),
        )
        for index, changes, expected_reason in scenarios:
            with self.subTest(reason=expected_reason):
                document = selection()
                replace_artifact(document, index, **changes)
                path = self.directory / "selection.json"
                path.write_text(json.dumps(document))
                report = self.run_check(pins, path)
                challenger = next(
                    row for row in report["candidate_classifications"]
                    if row["source_id"] == "quality-candidate"
                )
                self.assertEqual(challenger["classification"], "unbenchmarked")
                self.assertEqual(challenger["reason"], expected_reason)
                self.assertEqual(report["summary"]["outperforms_active"], 0)

    def test_malformed_selection_cannot_claim_a_winner(self) -> None:
        invalid = selection()
        invalid["winner"] = dict(invalid["rankings"][1])
        path = self.directory / "selection.json"
        path.write_text(json.dumps(invalid))
        with self.assertRaises(ValidationError):
            CHECKER.load_selection(path)


if __name__ == "__main__":
    unittest.main()
