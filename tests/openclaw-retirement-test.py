"""Synthetic tests for the exact attached-unit retirement and rollback."""
import importlib.util
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retire_openclaw', ROOT / 'scripts/retire-openclaw-core.py')
retire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retire)
gateway_spec = importlib.util.spec_from_file_location(
    'retire_gateway', ROOT / 'scripts/retire-openclaw-gateway.py')
gateway = importlib.util.module_from_spec(gateway_spec)
gateway_spec.loader.exec_module(gateway)


class RetirementTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.enterContext(patch.object(retire, 'ATTACHED', root))
        self.enterContext(patch.object(retire, 'STATE', root / 'receipts'))
        self.enterContext(patch.object(retire, 'ROOT_UID', os.getuid()))
        self.active = set(retire.UNITS)
        self.stopped = []
        self.dropin = None
        self.enterContext(patch.object(retire, 'command', side_effect=self.command))
        for index, unit in enumerate(retire.UNITS):
            fragment = root / unit
            fragment.write_text(unit)
            link = retire.boot_link(unit)
            link.parent.mkdir(exist_ok=True)
            link.symlink_to('../' + unit if index % 2 == 0 else fragment)

    def command(self, manager, verb, unit=None, *args):
        self.assertEqual(manager, 'systemctl')
        if verb == 'show':
            return ('LoadState=loaded\n'
                    'UnitFileState=disabled\n'
                    f'FragmentPath={retire.ATTACHED / unit}\n'
                    f'DropInPaths={self.dropin if unit.endswith("access-proxy.service") and self.dropin else ""}\n'
                    f'ActiveState={"active" if unit in self.active else "inactive"}\n')
        if verb == 'stop':
            self.stopped.append(unit)
            self.active.remove(unit)
        if verb == 'start':
            self.active.add(unit)
        return ''

    def test_exact_native_paths_and_dropins_guard(self):
        unit = retire.UNITS[0]
        link = retire.boot_link(unit)
        link.unlink()
        link.symlink_to('../../elsewhere/' + unit)
        with self.assertRaisesRegex(ValueError, 'unknown boot link'):
            retire.inspect_unit(unit)
        self.dropin = '/run/systemd/system/50-restore-recovery.conf'
        with self.assertRaisesRegex(ValueError, 'managed attachment'):
            retire.inspect_unit('homecompute-openclaw-access-proxy.service')

    def test_known_recovery_dropin_is_included_in_the_plan(self):
        dropin = retire.ATTACHED / '50-restore-recovery.conf'
        dropin.write_text('reviewed recovery override')
        self.dropin = str(dropin)
        checksum = retire.hashlib.sha256(dropin.read_bytes()).hexdigest()
        with patch.object(retire, 'RECOVERY_DROPIN', dropin), patch.object(
                retire, 'RECOVERY_DROPIN_SHA256', checksum):
            self.assertEqual(retire.inspect_unit(retire.ACCESS_PROXY)['recovery_dropin_sha256'],
                             checksum)
            dropin.write_text('changed')
            with self.assertRaisesRegex(ValueError, 'managed attachment'):
                retire.inspect_unit(retire.ACCESS_PROXY)

    def test_gateway_removes_only_the_reviewed_alias(self):
        remaining = 'mcp_servers:\n  hermes:\n    transport: http\n'
        self.assertEqual(gateway.patch(remaining + gateway.MCP_ALIAS, remaining), remaining)
        self.assertEqual(gateway.patch(remaining, remaining), remaining)
        with self.assertRaisesRegex(ValueError, 'unrecognized active'):
            gateway.patch(remaining + gateway.MCP_ALIAS.replace('http\n', 'sse\n'), remaining)

    def test_stop_order_and_exact_rollback_after_partial_apply(self):
        reviewed = retire.plan('a' * 40)
        stop = self.command
        proxy = 'homecompute-openclaw-access-proxy.service'
        backend = 'homecompute-openclaw-access.service'

        def interrupt(manager, verb, unit=None, *args):
            if verb == 'stop' and unit == backend:
                raise OSError('simulated failure')
            return stop(manager, verb, unit, *args)

        with patch.object(retire, 'command', side_effect=interrupt):
            with self.assertRaisesRegex(ValueError, 'reconcile from receipt'):
                retire.apply(reviewed, retire.digest(reviewed))
        self.assertIn(proxy, self.stopped)
        self.assertIn(backend, self.active)
        retire.restore(reviewed)
        self.assertEqual(retire.plan('a' * 40), reviewed)

if __name__ == '__main__':
    unittest.main()
