#!/usr/bin/env python3
"""Offline contract tests for the household Hermes guest lifecycle helper."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup-hermes-guest.sh"
MANIFEST = ROOT / "config" / "hermes-release.json"


class HermesGuestRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-runtime-test-")
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.ca = self.root / "home-core-root.crt"
        self.ca.write_text("test-only-ca\n", encoding="utf-8")
        self.ca.chmod(0o600)
        self.key = self.root / "litellm-key"
        self.secret = "sk-test-only-0123456789abcdef"
        self.key.write_text(self.secret + "\n", encoding="utf-8")
        self.key.chmod(0o600)
        self.mm_token = self.root / "mattermost-token"
        self.mm_token.write_text("test-mattermost-token\n", encoding="utf-8")
        self.mm_token.chmod(0o600)
        self.evidence = self.root / "evidence"
        self.backup_gate = self.root / "off-host-backup.json"
        self.network_gate = self.root / "agents-network.json"
        self.write_gate(self.backup_gate, "off-host-backup")
        self.write_gate(self.network_gate, "agents-network")
        self.config = self.root / "hermes.env"
        self.write_config()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_config(self, extra: str = "", **overrides: str) -> None:
        values = {
            "HERMES_SANDBOX_NAME": "hermes-owner",
            "HERMES_ENDPOINT_URL": "http://ai.home.arpa:18080/v1",
            "HERMES_MODEL": "automation-moe",
            "HERMES_TRUSTED_PRIVATE_HOSTS": "ai.home.arpa,home-core.tail479ad.ts.net",
            "HERMES_CA_BUNDLE": str(self.ca),
            "HERMES_LITELLM_API_KEY_FILE": str(self.key),
            "HERMES_EVIDENCE_DIR": str(self.evidence),
            "HERMES_RESTORE_TARGET": "hermes-owner",
            "HERMES_BACKUP_READINESS_FILE": str(self.backup_gate),
            "HERMES_NETWORK_READINESS_FILE": str(self.network_gate),
            "HERMES_MATTERMOST_URL": "https://home-core.tail479ad.ts.net:9443",
            "HERMES_MATTERMOST_TOKEN_FILE": str(self.mm_token),
            "HERMES_MATTERMOST_ALLOWED_USERS": "1oipaix7gin73jczp97tcfmyac",
        }
        values.update(overrides)
        content = "\n".join(f"{key}={value}" for key, value in values.items())
        self.config.write_text(content + "\n" + extra, encoding="utf-8")
        self.config.chmod(0o600)

    @staticmethod
    def write_gate(path: Path, gate: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "gate": gate,
                    "status": "ready",
                    "scope": "hermes-household",
                    "observed_at": "2026-09-26T12:00:00Z",
                    "evidence": "test-only readiness evidence",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        return subprocess.run(
            ["bash", str(SCRIPT), *args, "--config", str(self.config)],
            cwd=ROOT,
            env=process_env,
            text=True,
            capture_output=True,
            check=False,
        )

    def make_mock_nemohermes(self) -> tuple[Path, Path]:
        bin_dir = self.root / "bin"
        bin_dir.mkdir(mode=0o700)
        log = self.root / "nemohermes.log"
        mock = bin_dir / "nemohermes"
        mock.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -eu
                printf '%s\\n' "$*" >>{log!s}
                case "$*" in
                  --version) printf 'nemohermes v0.0.141\\n' ;;
                  doctor\\ --json) printf '{{"schemaVersion":1,"ok":true}}\\n' ;;
                  hermes-owner\\ status\\ --json|hermes-owner-verify\\ status\\ --json)
                    printf '{{"agent":"hermes","phase":"Ready","openshellVersion":"0.0.116","inferenceHealth":{{"ok":true}},"recordedRoute":{{"model":"automation-moe"}}}}\\n'
                    ;;
                  hermes-owner\\ inference\\ get\\ --json|hermes-owner-verify\\ inference\\ get\\ --json)
                    printf '{{"model":"automation-moe","provider":"custom"}}\\n'
                    ;;
                  hermes-owner\\ exec\\ --\\ hermes\\ --version|hermes-owner-verify\\ exec\\ --\\ hermes\\ --version)
                    printf 'Hermes Agent v0.21.3\\n'
                    ;;
                  hermes-owner\\ connect\\ --probe-only|hermes-owner-verify\\ connect\\ --probe-only)
                    printf 'Probe timing: result=ready\\n'
                    ;;
                  hermes-owner\\ channels\\ status\\ *) printf '{{"connected":true}}\\n' ;;
                  hermes-owner\\ policy\\ add\\ *) printf 'policy applied\\n' ;;
                  hermes-owner\\ channels\\ add\\ mattermost)
                    test "${{MATTERMOST_URL:-}}" = https://home-core.tail479ad.ts.net:9443
                    test "${{MATTERMOST_TOKEN:-}}" = test-mattermost-token
                    test "${{MATTERMOST_ALLOWED_USERS:-}}" = 1oipaix7gin73jczp97tcfmyac
                    printf 'channel added\\n'
                    ;;
                  list\\ --json) printf '{{"sandboxes":[{{"name":"hermes-owner"}}]}}\\n' ;;
                  hermes-owner\\ snapshot\\ restore\\ *) printf 'restored\\n' ;;
                  hermes-owner\\ gateway\\ restart) printf 'restarted\\n' ;;
                  hermes-owner\\ snapshot\\ create\\ --name\\ *) printf 'created\\n' ;;
                  onboard\\ --agent\\ hermes\\ --name\\ hermes-owner\\ --from\\ *)
                    test "${{COMPATIBLE_API_KEY:-}}" = '{self.secret}'
                    test "${{NEMOCLAW_POLICY_TIER:-}}" = restricted
                    test "${{NEMOCLAW_WEB_SEARCH_PROVIDER:-}}" = none
                    test "${{NEMOCLAW_ENDPOINT_URL:-}}" = http://ai.home.arpa:18080/v1
                    test "${{NEMOCLAW_TRUSTED_PRIVATE_HOSTS:-}}" = ai.home.arpa,home-core.tail479ad.ts.net
                    test "${{NEMOCLAW_CORPORATE_CA_BUNDLE:-}}" = {self.ca!s}
                    test "${{NODE_EXTRA_CA_CERTS:-}}" = {self.ca!s}
                    test -s "${{CURL_CA_BUNDLE:-}}"
                    test "${{CURL_CA_BUNDLE:-}}" = "${{SSL_CERT_FILE:-}}"
                    test "${{CURL_CA_BUNDLE:-}}" = "${{REQUESTS_CA_BUNDLE:-}}"
                    test "${{CURL_CA_BUNDLE:-}}" = "${{GIT_SSL_CAINFO:-}}"
                    test -z "${{DISCORD_BOT_TOKEN:-}}"
                    test -z "${{TAVILY_API_KEY:-}}"
                    printf 'onboarded\\n'
                    ;;
                  *) printf 'unexpected invocation: %s\\n' "$*" >&2; exit 90 ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        mock.chmod(0o700)
        return bin_dir, log

    def sourced_call(self, body: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        program = textwrap.dedent(
            f"""\
            set -Eeuo pipefail
            source {SCRIPT!s}
            load_runtime_config {self.config!s}
            validate_runtime_config
            {body}
            """
        )
        return subprocess.run(
            ["bash", "-c", program],
            cwd=ROOT,
            env=process_env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_release_tuple_is_exact(self) -> None:
        release = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(release["nemoclaw"]["commit"], "b7dcc9bb76197fb28ec673867c35974ac799eb15")
        self.assertEqual(release["managed_components"]["hermes_version"], "0.21.3")
        self.assertEqual(release["managed_components"]["openshell_version"], "0.0.116")
        self.assertEqual(release["policy"]["tier"], "restricted")
        self.assertTrue(release["policy"]["messaging"])
        self.assertTrue(release["policy"]["custom_image"])

    def test_validate_accepts_only_the_approved_household_contract(self) -> None:
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.write_config(HERMES_ENDPOINT_URL="https://ai.home.arpa/v1")
        result = self.run_cli("validate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("endpoint must be", result.stderr)

    def test_config_parser_rejects_unknown_keys(self) -> None:
        self.write_config(extra="SURPRISE_SECRET=must-not-load\n")
        result = self.run_cli("validate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown key", result.stderr)

    def test_secret_file_requires_private_permissions_and_safe_value(self) -> None:
        self.key.chmod(0o644)
        result = self.sourced_call("validate_api_key_file")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("permissions are too broad", result.stderr)
        self.key.chmod(0o600)
        self.key.write_text('sk-unsafe-"value\n', encoding="utf-8")
        result = self.sourced_call("validate_api_key_file")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe", result.stderr)

    def test_onboard_passes_secret_only_in_child_environment(self) -> None:
        bin_dir, log = self.make_mock_nemohermes()
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DISCORD_BOT_TOKEN": "must-be-cleared",
            "TAVILY_API_KEY": "must-be-cleared",
        }
        result = self.sourced_call("preflight_runtime() { :; }\nonboard_canary", env)
        self.assertEqual(result.returncode, 0, result.stderr)
        invocation = log.read_text(encoding="utf-8")
        self.assertIn("onboard --agent hermes --name hermes-owner", invocation)
        self.assertNotIn(self.secret, invocation)
        self.assertNotIn("must-be-cleared", result.stdout + result.stderr + invocation)

    def test_onboard_refuses_to_mutate_without_backup_gate(self) -> None:
        bin_dir, log = self.make_mock_nemohermes()
        self.backup_gate.unlink()
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        result = self.sourced_call("preflight_runtime() { :; }\nonboard_canary", env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backup readiness record", result.stderr)
        self.assertFalse(log.exists(), "NemoClaw was invoked before prerequisites passed")

    def test_mattermost_connection_keeps_token_out_of_command_and_output(self) -> None:
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("connector", ROOT / "scripts/connect-hermes-mattermost.py")
        connector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(connector)
        environment = {"MATTERMOST_TOKEN": "test-mattermost-token",
                       "HERMES_OPENSHELL_GATEWAY": "nemoclaw",
                       "MATTERMOST_URL": "https://home-core.tail479ad.ts.net:9443",
                       "MATTERMOST_ALLOWED_USERS": "owner", "MATTERMOST_REPLY_MODE": "thread",
                       "MATTERMOST_REQUIRE_MENTION": "true"}
        absent = subprocess.CompletedProcess([], 1, "", "provider not found")
        with patch.dict(os.environ, environment), patch.object(connector, "call") as calls, \
                patch.object(connector.subprocess, "run", return_value=absent):
            connector.main()
        for invocation in calls.call_args_list:
            self.assertNotIn("test-mattermost-token", repr(invocation.args))
            self.assertNotIn("test-mattermost-token", invocation.kwargs.get("input", ""))
        creates = [c for c in calls.call_args_list if "create" in c.args[0]]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0].kwargs["env"]["MATTERMOST_TOKEN"], "test-mattermost-token")
        self.assertNotIn("--provider", repr(calls.call_args_list))
        # Execute the real generated configuration patch against a publisher
        # fixture whose named custom provider still selects inference.local.
        import types, sys
        home = self.root / 'native-home'; home.mkdir()
        (home / '.env').write_text('OPENAI_BASE_URL=https://inference.local/v1\n')
        (home / 'config.yaml').write_text(json.dumps({'model': {}, 'providers': {'custom': {}},
            'custom_providers': [{'name': 'custom', 'base_url': 'https://inference.local/v1'}]}))
        yaml = types.ModuleType('yaml'); yaml.safe_load = json.loads
        yaml.safe_dump = lambda value, **kwargs: json.dumps(value)
        program = next(c.kwargs['input'] for c in calls.call_args_list if 'input' in c.kwargs)
        with patch.dict(sys.modules, {'yaml': yaml}), patch.dict(os.environ, {
                'COMPATIBLE_API_KEY': 'openshell:resolve:env:COMPATIBLE_API_KEY',
                'HTTP_PROXY': 'http://10.200.0.1:3128', 'HTTPS_PROXY': 'http://10.200.0.1:3128'}):
            exec(program.replace('/sandbox/.hermes', str(home)), {})
        configured = json.loads((home / 'config.yaml').read_text())
        for lane in (configured['model'], configured['providers']['custom'], configured['custom_providers'][0]):
            self.assertEqual(lane['base_url'], 'http://ai.home.arpa:18080/v1')
        self.assertEqual((home / '.env').stat().st_mode & 0o777, 0o640)

    def test_backup_gate_requires_household_off_host_scope(self) -> None:
        document = json.loads(self.backup_gate.read_text(encoding="utf-8"))
        document["scope"] = "hermes-synthetic-canary"
        self.backup_gate.write_text(json.dumps(document) + "\n", encoding="utf-8")
        result = self.sourced_call("require_mutation_gates")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fresh off-host Synology restore gate", result.stderr)

    def test_health_captures_and_validates_pinned_versions(self) -> None:
        bin_dir, _ = self.make_mock_nemohermes()
        result = self.run_cli("health", env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence_dirs = list(self.evidence.iterdir())
        self.assertEqual(len(evidence_dirs), 1)
        evidence = evidence_dirs[0]
        self.assertTrue((evidence / "release-manifest.json").is_file())
        self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o700)
        for item in evidence.iterdir():
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600)

    def test_health_rejects_api_key_in_evidence(self) -> None:
        bin_dir, _ = self.make_mock_nemohermes()
        mock = bin_dir / "nemohermes"
        original = mock.read_text(encoding="utf-8")
        mock.write_text(
            original.replace(
                "printf 'Probe timing: result=ready\\n'",
                f"printf 'Probe timing: result=ready key={self.secret}\\n'",
            ),
            encoding="utf-8",
        )
        mock.chmod(0o700)
        result = self.run_cli("health", env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("credential appeared", result.stderr)

    def test_snapshot_and_restore_verify_managed_image_in_place(self) -> None:
        bin_dir, log = self.make_mock_nemohermes()
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        snapshot = self.run_cli("snapshot", "pre-restore-test", env=env)
        self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
        restored = self.run_cli("restore-verify", "pre-restore-test", env=env)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        invocations = log.read_text(encoding="utf-8")
        self.assertIn("snapshot create --name pre-restore-test", invocations)
        self.assertIn("snapshot restore pre-restore-test", invocations)
        self.assertIn("hermes-owner gateway restart", invocations)
        self.assertNotIn("--to", invocations)
        self.assertNotIn("--force", invocations)
        self.assertNotIn("destroy", invocations)


if __name__ == "__main__":
    unittest.main()
