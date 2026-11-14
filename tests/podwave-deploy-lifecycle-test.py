#!/usr/bin/env python3
"""Keep PodWave's isolated deployment guarded and observable."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PodWaveDeployLifecycleTests(unittest.TestCase):
    def test_standalone_lifecycle_uses_release_lock_and_checks_prerequisites(self):
        script = (ROOT / "scripts/deploy-podwave.sh").read_text()
        backup = (ROOT / "scripts/lib/podwave-backup.sh").read_text()
        self.assertIn('[[ $EUID == 0 && $(hostname) == home-core ]] ||', script)
        self.assertIn('flock -n 9', script)
        self.assertIn('git clone https://github.com/madslundt/HomeCompute.git', script)
        self.assertIn('git -C "$selected_release" checkout --detach "$selected_revision"', script)
        self.assertIn('source "$helper_release/scripts/lib/podwave-backup.sh"', script)
        self.assertIn('docker network inspect homecompute-podwave', script)
        self.assertIn('/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt', script)
        self.assertIn('"${podwave[@]}" config --quiet', script)
        self.assertIn('"${podwave[@]}" build --pull podwave', script)
        self.assertIn('"${compose[@]}" stop podwave', backup)
        self.assertIn('tar --numeric-owner --xattrs --acls --gzip --create --file "$backup_path"', backup)
        self.assertIn('sha256sum --check --status "$backup_path.sha256"', backup)
        self.assertIn('"${podwave[@]}" up -d --wait --wait-timeout 180', script)
        self.assertIn('setup-podwave-server-settings.py', script)
        self.assertIn("https://podwave.home.arpa/api/health", script)
        self.assertLess(script.index('backup_podwave_state'), script.index('"${podwave[@]}" up -d'))
        self.assertIn('PodWave state backup: %s\\n', script)
        self.assertIn('mv -f "$marker_tmp" /var/lib/homecompute/podwave-deployed-revision', script)
        self.assertIn('The full home-core release marker was not changed.', script)
        self.assertNotIn('nixos-rebuild', script)
        self.assertNotIn('deploy/automation/', script)

    def test_full_release_advances_podwave_marker_only_at_success(self):
        script = (ROOT / "scripts/deploy-home-core.sh").read_text()
        final_marker = script.index("printf 'Healthy deployment: %s\\n' \"$revision\"")
        podwave_marker = script.index('mv -f "$podwave_marker_tmp" /var/lib/homecompute/podwave-deployed-revision')
        self.assertIn('backup_podwave_state "$revision" "${podwave[@]}"', script)
        self.assertLess(podwave_marker, final_marker)
        self.assertIn('mv -Tf "$podwave_current_link" /srv/homecompute/podwave-current', script)

    def test_workstation_cli_exposes_podwave_only_release(self):
        cli = (ROOT / "scripts/homecompute.py").read_text()
        self.assertIn('sub.add_parser("deploy-podwave")', cli)
        self.assertIn('entrypoint="deploy-podwave.sh"', cli)


if __name__ == "__main__":
    unittest.main()
