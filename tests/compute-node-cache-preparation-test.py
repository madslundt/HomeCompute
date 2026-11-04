#!/usr/bin/env python3
"""Exercise the retained-model cache-only owner path without Docker or host state."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts/setup-compute-node.sh"


class CachePreparationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="compute-cache-prepare-")
        self.root = Path(self.temp.name)
        self.manifests = self.root / "manifests"
        self.manifests.mkdir()
        self.tuple = self.manifests / "accepted-model-cache-test.json"
        self.active = self.manifests / "accepted-model-cache.json"
        self.tuple_bytes = b'{"immutable":"accepted tuple"}\n'
        self.tuple.write_bytes(self.tuple_bytes)
        self.calls = self.root / "calls.log"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def harness(self, body: str, *, setup: str = "") -> str:
        return f'''\
source {str(SETUP)!r}
GB10_ROOT={str(self.root)!r}
MODEL_DEPLOYMENT_ID=general-spark-qwen38
MODEL_ID=unsloth/Qwen3.8-27B-NVFP4
MODEL_REVISION=57926baca9a82b4d6906b43f2750d55315f5b10f
TOKENIZER_REVISION="$MODEL_REVISION"
CODE_REVISION="$MODEL_REVISION"
VLLM_IMAGE=nvcr.io/example/model@sha256:{'a' * 64}
TUPLE_PATH={str(self.tuple)!r}
ACTIVE_PATH={str(self.active)!r}
CALL_LOG={str(self.calls)!r}
record() {{ printf '%s\\n' "$*" >>"$CALL_LOG"; }}
require_root() {{ record root; }}
acquire_spark_deployment_lock() {{ record deployment-lock; }}
require_active_immutable_release() {{ record active-release; }}
acquire_text_model_runtime_lock() {{ record text-lock; {setup} }}
load_env() {{ record load-env; }}
validate_config() {{ record validate-config; }}
preflight() {{ record preflight; }}
tuple_cache_manifest() {{ printf '%s\\n' "$TUPLE_PATH"; }}
active_cache_manifest() {{ printf '%s\\n' "$ACTIVE_PATH"; }}
config_path_is_trusted() {{ [[ -f "$1" && ! -L "$1" ]]; }}
verify_fetch_cache_helper() {{ record compatible-helper; }}
verify_model_cache() {{ record verify-model-cache; }}
cache_integrity() {{ record "integrity-$1"; return 0; }}
accept_model_cache() {{
  record "accept $1"
  [[ "$1" == true ]] || return 90
  cp -- "$TUPLE_PATH" "$ACTIVE_PATH"
}}
docker() {{
  record "docker $1 $2 $3"
  [[ "$#" == 5 && "$1" == image && "$2" == inspect && "$3" == --format && "$4" == '{{{{.Id}}}}' && "$5" == "$VLLM_IMAGE" ]] || return 91
  [[ "${{DOCKER_FAIL:-false}}" != true ]] || return 1
  printf 'sha256:%s\\n' "{'b' * 64}"
}}
compose() {{
  record "compose $1 $2 $3 $4 $5 $6 $7 $8"
  [[ "$#" == 8 && "$1" == --profile && "$2" == prepare && "$3" == run && "$4" == --pull && "$5" == never && "$6" == --no-deps && "$7" == --rm && "$8" == model-fetch ]] || return 92
}}
{body}
'''

    def run_shell(self, body: str, *, setup: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", self.harness(body, setup=setup)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def run_acceptance(self, manifest_exists: bool) -> subprocess.CompletedProcess[str]:
        if not manifest_exists:
            self.tuple.unlink(missing_ok=True)
        script = f'''\
source {str(SETUP)!r}
GB10_ROOT={str(self.root)!r}
MODEL_ID=unsloth/Qwen3.8-27B-NVFP4
MODEL_REVISION=57926baca9a82b4d6906b43f2750d55315f5b10f
TOKENIZER_REVISION="$MODEL_REVISION"
CODE_REVISION="$MODEL_REVISION"
TUPLE_PATH={str(self.tuple)!r}
ACTIVE_PATH={str(self.active)!r}
config_path_is_trusted() {{ [[ -f "$1" && ! -L "$1" ]]; }}
tuple_cache_manifest() {{ printf '%s\\n' "$TUPLE_PATH"; }}
active_cache_manifest() {{ printf '%s\\n' "$ACTIVE_PATH"; }}
cache_integrity() {{ [[ "$1" == verify && "$2" == --manifest && "$3" == "$TUPLE_PATH" ]]; }}
activate_cache_manifest() {{ cp -- "$1" "$ACTIVE_PATH"; }}
accept_model_cache true
'''
        return subprocess.run(["bash", "-c", script], cwd=ROOT, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def run_helper_check(self, installed_bytes: bytes) -> subprocess.CompletedProcess[str]:
        runtime = self.root / "runtime"
        runtime.mkdir(exist_ok=True)
        (runtime / "model-cache-integrity.py").write_bytes(installed_bytes)
        script = f'''\
source {str(SETUP)!r}
GB10_ROOT={str(self.root)!r}
CACHE_INTEGRITY_HELPER={str(ROOT / 'scripts/model-cache-integrity.py')!r}
config_path_is_trusted() {{ [[ -f "$1" && ! -L "$1" ]]; }}
verify_fetch_cache_helper
'''
        return subprocess.run(["bash", "-c", script], cwd=ROOT, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def call_log(self) -> list[str]:
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []

    def test_fetches_only_pinned_prepare_job_then_activates_unchanged_acceptance(self) -> None:
        result = self.run_shell("prepare_cache_only")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.call_log()
        self.assertLess(calls.index("deployment-lock"), calls.index("text-lock"))
        self.assertLess(calls.index("active-release"), calls.index("text-lock"))
        self.assertIn("compose --profile prepare run --pull never --no-deps --rm model-fetch", calls)
        self.assertEqual(calls.count("compose --profile prepare run --pull never --no-deps --rm model-fetch"), 1)
        self.assertFalse(any(" stop " in f" {call} " or " up " in f" {call} " for call in calls))
        self.assertEqual(self.tuple.read_bytes(), self.tuple_bytes)
        self.assertEqual(self.active.read_bytes(), self.tuple_bytes)

    def test_matching_active_manifest_is_verified_without_rewriting_either_manifest(self) -> None:
        self.active.write_bytes(self.tuple_bytes)
        tuple_stat = self.tuple.stat()
        active_stat = self.active.stat()
        result = self.run_shell("prepare_cache_only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("accept true", self.call_log())
        self.assertEqual(self.tuple.read_bytes(), self.tuple_bytes)
        self.assertEqual(self.active.read_bytes(), self.tuple_bytes)
        self.assertEqual((self.tuple.stat().st_ino, self.tuple.stat().st_mtime_ns),
                         (tuple_stat.st_ino, tuple_stat.st_mtime_ns))
        self.assertEqual((self.active.stat().st_ino, self.active.stat().st_mtime_ns),
                         (active_stat.st_ino, active_stat.st_mtime_ns))

    def test_missing_accepted_tuple_refuses_before_fetch_or_acceptance(self) -> None:
        self.tuple.unlink()
        result = self.run_shell("prepare_cache_only")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("compose --profile prepare run --pull never --no-deps --rm model-fetch", self.call_log())
        self.assertFalse(any(call.startswith("accept ") for call in self.call_log()))

    def test_conflicting_active_manifest_refuses_before_fetch(self) -> None:
        self.active.write_text("different accepted tuple\n", encoding="utf-8")
        result = self.run_shell("prepare_cache_only")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("compose --profile prepare run --pull never --no-deps --rm model-fetch", self.call_log())
        self.assertEqual(self.tuple.read_bytes(), self.tuple_bytes)

    def test_pending_recovery_lock_refuses_before_config_or_fetch(self) -> None:
        result = self.run_shell("prepare_cache_only", setup="return 1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("validate-config", self.call_log())
        self.assertNotIn("compose --profile prepare run --pull never --no-deps --rm model-fetch", self.call_log())

    def test_wrong_model_tuple_refuses_before_fetch(self) -> None:
        result = self.run_shell("MODEL_ID=another/model; prepare_cache_only")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("preflight", self.call_log())
        self.assertNotIn("compose --profile prepare run --pull never --no-deps --rm model-fetch", self.call_log())

    def test_uncached_pinned_image_refuses_without_compose_pull_or_runtime_start(self) -> None:
        result = self.run_shell("DOCKER_FAIL=true; prepare_cache_only")
        self.assertNotEqual(result.returncode, 0)
        calls = self.call_log()
        self.assertTrue(any(call.startswith("docker image inspect ") for call in calls))
        self.assertNotIn("compose --profile prepare run --pull never --no-deps --rm model-fetch", calls)

    def test_existing_acceptance_activates_only_after_verification_and_never_recreates_missing_tuple(self) -> None:
        original_sha = hashlib.sha256(self.tuple_bytes).hexdigest()
        result = self.run_acceptance(manifest_exists=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tuple.read_bytes(), self.tuple_bytes)
        self.assertEqual(self.active.read_bytes(), self.tuple_bytes)
        self.assertEqual(hashlib.sha256(self.tuple.read_bytes()).hexdigest(), original_sha)

        self.active.unlink()
        missing = self.run_acceptance(manifest_exists=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse(self.tuple.exists())
        self.assertFalse(self.active.exists())

    def test_fetch_helper_accepts_current_pin_and_rejects_unknown_installed_bytes(self) -> None:
        current = (ROOT / "scripts/model-cache-integrity.py").read_bytes()
        accepted = self.run_helper_check(current)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        rejected = self.run_helper_check(b"unreviewed helper bytes\n")
        self.assertNotEqual(rejected.returncode, 0)


if __name__ == "__main__":
    unittest.main()
