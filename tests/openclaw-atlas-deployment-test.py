"""Scoped rollout planning fences; no host commands or external services."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('atlas_deploy', ROOT/'scripts/deploy-openclaw-atlas-tasks.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); (self.root/'scripts').mkdir()
        self.live = self.root/'live'; self.live.mkdir()
        for name in m.TARGETS:
            (self.root/'scripts'/name).write_text('VERSION = 2\n')
            if name != 'openclaw_task_followups.py': (self.live/name).write_text('VERSION = 1\n')
        self.old = {n:(self.live/n).read_bytes() if (self.live/n).exists() else None for n in m.TARGETS}
        self.states = {n:{'active':n not in m.SENDERS,'unit_sha256':'u'*64} for n in (*m.TIMERS,*m.SENDERS,*m.SERVICES)}

    def plan(self, **extra):
        def read(path,optional=False):
            if path.parent==self.live or path.parent==self.root/'scripts':
                return path.read_bytes() if path.exists() else None
            return b'unit'
        def run(argv,**kw): return b'VERSION = 1\n'
        with patch.object(m,'ROOT',self.root),patch.object(m,'CORE',self.live),patch.object(m.os,'geteuid',return_value=0),patch.object(m.socket,'gethostname',return_value='home-core'),patch.object(m,'source_guard',return_value='b'*64),patch.object(m,'protected',side_effect=read),patch.object(m,'run',side_effect=run),patch.object(m,'unit',side_effect=lambda n:self.states[n]),patch.object(m,'database_counts',return_value={'owner':{'tasks':3}}),patch.object(Path,'is_symlink',return_value=True),patch.object(Path,'readlink',autospec=True,side_effect=lambda p: Path('/etc/systemd/system.attached')/p.name):
            return m.core_plan('a'*40)

    def test_plan_fences_all_four_modules_without_mutating_live_records(self):
        plan = self.plan()
        self.assertEqual({r['name'] for r in plan['modules']},set(m.TARGETS))
        self.assertEqual(plan['task_counts'],{'owner':{'tasks':3}})
        self.assertEqual(self.old,{n:(self.live/n).read_bytes() if (self.live/n).exists() else None for n in m.TARGETS})

    def test_unreviewed_module_and_active_dispatch_are_refused(self):
        (self.live/m.TARGETS[0]).write_text('LOCAL = True\n')
        with self.assertRaisesRegex(ValueError,'Unreviewed'): self.plan()
        (self.live/m.TARGETS[0]).write_bytes(self.old[m.TARGETS[0]])
        self.states[m.SENDERS[0]]['active']=True
        with self.assertRaisesRegex(ValueError,'dispatch'): self.plan()

    def test_stale_apply_plan_writes_no_code_or_backup(self):
        state=self.root/'state'
        with patch.object(m,'STATE',state),patch.object(m,'core_plan',return_value={'plan_sha256':'current'}):
            with self.assertRaisesRegex(ValueError,'plan changed'):m.core_apply('a'*40,'stale')
        self.assertFalse(list(state.glob('backup-*')))

    def test_release_guard_rejects_workstation_and_short_revisions(self):
        for revision in ('master','a'*40):
            with self.assertRaisesRegex(ValueError,'release directory'):m.source_guard(revision)

    def test_native_principal_is_checked_separately_from_host_operator(self):
        context=Mock();context.container='exact-retained-container'
        context.run.return_value=(0,b'998:998\n')
        self.assertEqual(m.guest_owner(context),998)
        context.run.return_value=(0,b'1000:1000\n')
        with self.assertRaisesRegex(ValueError,'sandbox principal'):m.guest_owner(context)

    def test_routine_inventory_renders_idempotently_and_does_not_enable_native_cron(self):
        spec=importlib.util.spec_from_file_location('routines',ROOT/'scripts/setup-openclaw-routines.py')
        r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
        path,content=r.render();self.assertEqual(path.read_text(),content)
        cfg=json.loads((ROOT/'config/openclaw-routines.json').read_text())
        self.assertFalse(cfg['native_cron_enabled']);self.assertFalse(cfg['ambient_heartbeats_enabled'])
        self.assertEqual(len(cfg['existing_routines']),4)


if __name__=='__main__':unittest.main()
