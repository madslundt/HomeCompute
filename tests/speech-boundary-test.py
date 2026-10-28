#!/usr/bin/env python3
"""Check inference-only Spark and the core Wyoming migration/rollback contract."""
from __future__ import annotations

import importlib.util
from contextlib import nullcontext
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('speech_boundary', ROOT / 'scripts/setup-speech-boundary.py')
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


def compose(path: str, environment: str, *profiles: str) -> dict:
    args = ['docker', 'compose', '--env-file', str(ROOT / environment), '-f', str(ROOT / path)]
    for profile in profiles:
        args += ['--profile', profile]
    result = subprocess.run(args + ['config', '--format', 'json'], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


class SpeechBoundaryTest(unittest.TestCase):
    def test_core_runs_real_cpu_adapters_at_existing_ha_addresses(self):
        document = compose('deploy/control-plane/compose.yaml', 'config/control-plane.env.example', 'speech-proxies')
        for name, port, address in [('plapre-wyoming-proxy', 10201, '172.28.200.4'),
                                    ('hviske-wyoming-proxy', 10301, '172.28.200.5')]:
            service = document['services'][name]
            self.assertEqual(service['ports'][0]['target'], port)
            self.assertEqual(service['networks']['edge']['ipv4_address'], address)
            self.assertIn('speech-adapter-health.py', ' '.join(service['healthcheck']['test']))
            self.assertTrue(service['read_only'])
            self.assertEqual(service['cap_drop'], ['ALL'])
            self.assertNotIn('devices', service)
            self.assertNotIn('deploy', service)
        tts = document['services']['plapre-wyoming-proxy']
        self.assertIn('/opt/homecompute/plapre-wyoming.py', tts['entrypoint'])
        self.assertIn('--tempo=1.20', tts['command'])
        self.assertIn('--voice-alias=danish-default', tts['command'])
        self.assertIn('--max-pcm-bytes=48000000', tts['command'])
        stt = document['services']['hviske-wyoming-proxy']
        self.assertIn('/opt/homecompute/wyoming-openai-stt.py', stt['entrypoint'])
        self.assertEqual(stt['environment']['STT_MAX_AUDIO_SECONDS'], '60')
        self.assertEqual(stt['environment']['STT_MAX_CONNECTIONS'], '4')
        self.assertEqual(stt['environment']['STT_API_KEY_FILE'], '/run/secrets/compute_api_key')
        self.assertEqual(stt['secrets'][0]['source'], 'compute_api_key')

    def test_spark_normal_profiles_have_no_wyoming(self):
        plapre = compose('deploy/compute-node/plapre/compose.yaml', 'config/plapre-tts.env.example')
        self.assertEqual(set(plapre['services']), {'plapre-primary', 'plapre-http'})
        self.assertIn('@sha256:', plapre['services']['plapre-http']['image'])
        self.assertEqual(plapre['services']['plapre-http']['ports'][0]['host_ip'], '127.0.0.1')
        self.assertEqual(plapre['services']['plapre-http']['tmpfs'],
                         ['/tmp:rw,noexec,nosuid,nodev,size=8m'])
        hviske = compose('deploy/hviske-stt/compose.yaml', 'config/hviske-stt.env.example', 'hviske', 'hviske-http')
        self.assertNotIn('hviske-wyoming', hviske['services'])
        self.assertIn('hviske-http', hviske['services'])

    def test_prepare_errors_classify_without_relaying_private_docker_output(self):
        result = subprocess.CompletedProcess([], 1, '',
            "private-token=do-not-relay invalid mount path: 'nodev' mount path must be absolute")
        with patch.object(INSTALLER.subprocess, 'run', return_value=result):
            with self.assertRaises(INSTALLER.OperationError) as caught:
                INSTALLER.run(['docker', 'compose', 'up'])
        self.assertEqual(caught.exception.reason, 'invalid-mount')
        self.assertNotIn('do-not-relay', str(caught.exception))

    def test_legacy_profiles_remain_explicit_for_rollback(self):
        plapre = compose('deploy/compute-node/plapre/compose.yaml', 'config/plapre-tts.env.example', 'legacy-wyoming')
        self.assertIn('plapre-wyoming', plapre['services'])
        self.assertIn('plapre-edge', plapre['services'])
        hviske = compose('deploy/hviske-stt/compose.yaml', 'config/hviske-stt.env.example', 'hviske', 'legacy-wyoming')
        self.assertIn('hviske-wyoming', hviske['services'])

    def test_tunnel_patch_retains_every_unrelated_forward_and_is_idempotent(self):
        source = 'keep flags\n:18003:127.0.0.1:8003\n:18005:127.0.0.1:18300\n:18201:127.0.0.1:10201\n:18301:127.0.0.1:10301\n'
        changed = INSTALLER.tunnel_patch(source)
        self.assertIn('18003:127.0.0.1:8003', changed)
        self.assertIn('18005:127.0.0.1:18400', changed)
        self.assertIn('18201:127.0.0.1:8004', changed)
        self.assertIn('18301:127.0.0.1:8003', changed)
        self.assertEqual(changed, INSTALLER.tunnel_patch(changed))
        self.assertTrue(changed.startswith('keep flags\n'))

    def test_unknown_tunnel_or_undrained_cutover_fails_before_mutation(self):
        with self.assertRaises(ValueError):
            INSTALLER.tunnel_patch('unexpected tunnel')
        with patch.object(INSTALLER, 'run') as run:
            with self.assertRaises(ValueError): INSTALLER.core_apply(False)
            run.assert_not_called()
        with patch.object(INSTALLER, 'run') as run:
            with self.assertRaises(ValueError): INSTALLER.spark_legacy(False, False)
            run.assert_not_called()

    def test_rollback_preserves_each_independently_managed_adapter_release_and_image(self):
        rows = []
        for index, service in enumerate(INSTALLER.CORE_SERVICES):
            revision = str(index + 1) * 40
            image = 'sha256:' + str(index + 3) * 64
            rows.append({'Name': '/homecompute-control-plane-' + service + '-1',
                'Image': image, 'Config': {'Labels': {
                    'com.docker.compose.project.config_files': f'/srv/homecompute/releases/{revision}/deploy/control-plane/compose.yaml',
                    'com.docker.compose.project.environment_file': str(INSTALLER.CORE_ENV)}}})
        previous = INSTALLER.previous_services(rows)
        self.assertNotEqual(previous[INSTALLER.CORE_SERVICES[0]]['compose'],
                            previous[INSTALLER.CORE_SERVICES[1]]['compose'])
        self.assertNotEqual(previous[INSTALLER.CORE_SERVICES[0]]['image_id'],
                            previous[INSTALLER.CORE_SERVICES[1]]['image_id'])
        self.assertEqual(set(previous), set(INSTALLER.CORE_SERVICES))
        rows[0]['Config']['Labels']['com.docker.compose.project.config_files'] = '/home/operator/dirty/compose.yaml'
        with self.assertRaises(ValueError): INSTALLER.previous_services(rows)

    def test_staging_does_not_change_active_units_or_what_a_future_restart_executes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            (state / 'before.json').write_text('{}')
            base = ':18005:127.0.0.1:18400\n:18201:127.0.0.1:10201\n:18301:127.0.0.1:10301\n'
            with patch.object(INSTALLER, 'STATE', state), \
                 patch.object(INSTALLER, 'deployment_lock', return_value=nullcontext()), \
                 patch.object(INSTALLER, 'DROPIN', state / 'active-not-created.conf'), \
                 patch.object(INSTALLER, 'INSTALL', state / 'active-runner-not-created'), \
                 patch.object(INSTALLER, 'base_runner', return_value=(None, base)), \
                 patch.object(INSTALLER, 'stage_rollback'), \
                 patch.object(INSTALLER, 'write') as write, patch.object(INSTALLER, 'run') as run:
                INSTALLER.core_stage('1' * 40)
                for call in run.call_args_list:
                    self.assertEqual(call.args[0][0], 'docker')
                    self.assertNotIn('up', call.args[0])
                    self.assertNotIn('restart', call.args[0])
                self.assertEqual({call.args[0] for call in write.call_args_list},
                                 {state / 'runner.pending.py', state / 'tunnel.pending.conf'})
                self.assertFalse((state / 'active-runner-not-created').exists())
                self.assertFalse((state / 'active-not-created.conf').exists())

    def test_rollback_executes_only_selected_service_documents_without_pulls(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            previous = {name: {'compose': '/srv/homecompute/releases/' + str(i + 1) * 40 + '/deploy/control-plane/compose.yaml',
                               'image_id': 'sha256:' + str(i + 3) * 64}
                        for i, name in enumerate(INSTALLER.CORE_SERVICES)}
            before={'previous':previous,'environment':str(state/'environment.before')}
            with patch.object(INSTALLER,'STATE',state),patch.object(INSTALLER,'write') as write, \
                 patch.object(INSTALLER,'previous_receipt',return_value=before), \
                 patch.object(INSTALLER,'validate_rollback') as validate, \
                 patch.object(INSTALLER,'DROPIN',state/'unused-dropin'), \
                 patch.object(INSTALLER,'base_runner',return_value=(None,'old Wyoming base')), \
                 patch.object(INSTALLER,'run') as run:
                INSTALLER.core_rollback(True)
                validate.assert_called_once_with(False)
                calls=[c.args[0] for c in run.call_args_list if c.args[0][:2]==['docker','compose']]
                self.assertEqual(len(calls),2)
                for command,name in zip(calls,INSTALLER.CORE_SERVICES):
                    self.assertIn(str(state/('rollback-'+name+'.json')),command)
                    self.assertNotIn(previous[name]['compose'],command)
                    self.assertEqual(command[-1],name)
                    for value in ('--no-deps','--no-build','--pull','never','/dev/null'):self.assertIn(value,command)
                write.assert_not_called()

    def test_denied_raw_forward_rolls_back_tunnel_before_replacing_any_adapter(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary)
            (state/'before.json').write_text('{}')
            (state/'runner.pending.py').write_bytes(Path(INSTALLER.__file__).read_bytes())
            (state/'tunnel.pending.conf').write_text('reviewed pending tunnel')
            with patch.object(INSTALLER,'STATE',state),patch.object(INSTALLER,'INSTALL',state/'installed'), \
                 patch.object(INSTALLER,'DROPIN',state/'active.conf'),patch.object(INSTALLER,'trusted'), \
                 patch.object(INSTALLER,'write'),patch.object(INSTALLER,'run') as run, \
                 patch.object(INSTALLER,'deployment_lock',return_value=nullcontext()), \
                 patch.object(INSTALLER,'core_maintenance',return_value=nullcontext()), \
                 patch.object(INSTALLER,'validate_rollback'), \
                 patch.object(INSTALLER,'raw_upstreams_ready',side_effect=RuntimeError('forward denied')), \
                 patch.object(INSTALLER,'core_rollback') as rollback:
                with self.assertRaisesRegex(RuntimeError,'forward denied'): INSTALLER.core_apply(True)
                rollback.assert_called_once_with(True)
                self.assertIn(['systemctl','restart','homecompute-compute-ssh-tunnel.service'],
                              [call.args[0] for call in run.call_args_list])
                self.assertFalse(any(call.args[0][:2]==['docker','compose'] for call in run.call_args_list))

    def test_connection_inspection_counts_active_main_http_but_ignores_wyoming(self):
        header='  sl  local_address rem_address st\n'
        def row(port,state='01',address='01C81CAC'):
            return f' 0: {address}:{port:04X} 03C81CAC:1234 {state}\n'
        table=header+row(18005)+row(18003,'08')+row(18006,'03')+row(18201)+row(18301)+row(18005,'0A')+row(18007,'06')+row(18005,address='0100007F')
        self.assertEqual(INSTALLER.main_http_connections(table),3)
        for table in ('',header+'invalid row',header+'0: 01C81CAC:4655 peer UNKNOWN'):
            with self.assertRaises(ValueError):INSTALLER.main_http_connections(table)

    def test_drain_restarts_quiet_counter_if_late_active_traffic_appears(self):
        header='sl local_address rem_address st\n'
        active=header+'0: 01C81CAC:4655 03C81CAC:1234 01\n'
        values=[active,header,header,active,header,header,header]
        with patch.object(INSTALLER.Path,'read_text',side_effect=values) as read, \
             patch.object(INSTALLER.time,'monotonic',side_effect=range(10)),patch.object(INSTALLER.time,'sleep'):
            INSTALLER.drain_main_http(10)
        self.assertEqual(read.call_count,7)

    def test_active_main_http_times_out_instead_of_claiming_a_drain(self):
        active='sl local_address rem_address st\n0: 01C81CAC:4655 03C81CAC:1234 01\n'
        with patch.object(INSTALLER.Path,'read_text',return_value=active), \
             patch.object(INSTALLER.time,'monotonic',side_effect=range(5)),patch.object(INSTALLER.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'tunnel was not restarted'):INSTALLER.drain_main_http(3)

    def test_maintenance_scopes_only_new_main_http_and_preserves_existing_policy(self):
        calls=[]
        def run(args,**kwargs):calls.append(args);return ''
        with patch.object(INSTALLER,'run',side_effect=run),patch.object(INSTALLER,'drain_main_http'):
            with INSTALLER.core_maintenance(120):
                self.assertFalse(any('-D' in c for c in calls))
        rejects=[c for c in calls if '-A' in c and c[-1]=='DROP']
        self.assertEqual(len(rejects),1)
        rule=rejects[0]
        for value in ('172.28.200.3/32','172.28.200.1/32','18003,18005,18006,18007','NEW'):
            self.assertIn(value,rule)
        self.assertNotIn('ESTABLISHED',rule)
        self.assertIn('mangle',rule)
        self.assertFalse(any('INPUT' in c for c in calls))
        self.assertFalse(any('HC-COMPUTE-TUNNEL' in c for c in calls))
        self.assertEqual([c[5] for c in calls[-3:]],['-D','-F','-X'])
        self.assertTrue(all(c[-1]==INSTALLER.MAINTENANCE_CHAIN for c in calls[-3:]))

    def test_signal_during_successful_jump_install_removes_that_jump_and_restores_handlers(self):
        import signal
        calls=[]; original=signal.getsignal(signal.SIGTERM)
        def run(args,**kwargs):
            calls.append(args)
            if '-I' in args:signal.raise_signal(signal.SIGTERM)
            return ''
        with patch.object(INSTALLER,'run',side_effect=run),patch.object(INSTALLER,'drain_main_http') as drain:
            with self.assertRaises(InterruptedError):
                with INSTALLER.core_maintenance(120):self.fail('cannot enter cutover after SIGTERM')
        drain.assert_not_called()
        self.assertEqual([c[5] for c in calls[-3:]],['-D','-F','-X'])
        self.assertEqual(signal.getsignal(signal.SIGTERM),original)

    def test_busy_drain_does_not_install_reload_restart_or_rollback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary)
            (state/'before.json').write_text('{}')
            (state/'runner.pending.py').write_bytes(Path(INSTALLER.__file__).read_bytes())
            (state/'tunnel.pending.conf').write_text('pending')
            calls=[]
            def run(args,**kwargs):calls.append(args);return ''
            with patch.object(INSTALLER,'STATE',state),patch.object(INSTALLER,'DEPLOY_LOCK',state/'deploy.lock'), \
                 patch.object(INSTALLER,'run',side_effect=run),patch.object(INSTALLER,'write') as write, \
                 patch.object(INSTALLER,'core_rollback') as rollback, \
                 patch.object(INSTALLER,'validate_rollback'), \
                 patch.object(INSTALLER,'drain_main_http',side_effect=ValueError('still active')):
                with self.assertRaisesRegex(ValueError,'still active'):INSTALLER.core_apply(True,3)
            write.assert_not_called();rollback.assert_not_called()
            self.assertTrue(all(c[0]=='iptables' for c in calls))
            self.assertEqual([c[5] for c in calls[-3:]],['-D','-F','-X'])

    def test_deploy_lock_contention_refuses_before_maintenance_mutation(self):
        import tempfile,fcntl
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'deploy.lock'
            with path.open('a') as held,patch.object(INSTALLER,'DEPLOY_LOCK',path):
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    with INSTALLER.deployment_lock():self.fail('must not overlap a deployment or backup')

    def test_staging_cannot_replace_rollback_plan_during_a_locked_cutover(self):
        import tempfile,fcntl
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'deploy.lock'
            with path.open('a') as held,patch.object(INSTALLER,'DEPLOY_LOCK',path), \
                 patch.object(INSTALLER,'write') as write,patch.object(INSTALLER,'run') as run:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):INSTALLER.core_stage('1'*40)
                write.assert_not_called();run.assert_not_called()

    def test_interrupted_apply_rolls_back_before_releasing_maintenance_gate(self):
        import tempfile
        from contextlib import contextmanager
        events=[]
        @contextmanager
        def maintenance(_):
            events.append('gated-and-drained')
            try:yield
            finally:events.append('gate-removed')
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary)
            (state/'before.json').write_text('{}')
            (state/'runner.pending.py').write_bytes(Path(INSTALLER.__file__).read_bytes())
            (state/'tunnel.pending.conf').write_text('pending')
            with patch.object(INSTALLER,'STATE',state),patch.object(INSTALLER,'DEPLOY_LOCK',state/'deploy.lock'), \
                 patch.object(INSTALLER,'INSTALL',state/'installed'),patch.object(INSTALLER,'trusted'), \
                 patch.object(INSTALLER,'write'),patch.object(INSTALLER,'run'), \
                 patch.object(INSTALLER,'core_maintenance',side_effect=maintenance), \
                 patch.object(INSTALLER,'validate_rollback'), \
                 patch.object(INSTALLER,'raw_upstreams_ready'), \
                 patch.object(INSTALLER,'verify_core',side_effect=InterruptedError('synthetic signal')), \
                 patch.object(INSTALLER,'core_rollback',side_effect=lambda _:events.append('rollback')):
                with self.assertRaises(InterruptedError):INSTALLER.core_apply(True)
        self.assertEqual(events,['gated-and-drained','rollback','gate-removed'])


class SpeechReadinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('speech_health', ROOT / 'scripts/speech-adapter-health.py')
        cls.health = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.health)

    def listener(self, capability='tts'):
        data = json.dumps({capability: [{'installed': True}]}).encode()
        header = json.dumps({'type': 'info', 'data_length': len(data)}).encode()
        connection = MagicMock()
        connection.__enter__.return_value.makefile.return_value = io.BytesIO(header+b'\n'+data)
        return connection

    def test_readiness_requires_both_model_health_and_expected_wyoming_capability(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.dict('os.environ', {'PLAPRE_UPSTREAM_URL': 'http://172.28.200.1:18201'}), \
             patch.object(self.health.urllib.request, 'urlopen', return_value=response) as http, \
             patch.object(self.health.socket, 'create_connection', return_value=self.listener()) as connection:
            self.health.probe('tts')
            self.assertEqual(http.call_args.args[0].full_url, 'http://172.28.200.1:18201/health')
            connection.assert_called_once_with(('127.0.0.1', 10201), timeout=5)
        with patch.dict('os.environ', {'PLAPRE_UPSTREAM_URL': 'http://172.28.200.1:18201'}), \
             patch.object(self.health.urllib.request, 'urlopen', return_value=response), \
             patch.object(self.health.socket, 'create_connection', return_value=self.listener('asr')):
            with self.assertRaises(ValueError): self.health.probe('tts')

    def test_dead_dependency_and_unapproved_destination_fail_readiness(self):
        with patch.dict('os.environ', {'PLAPRE_UPSTREAM_URL': 'http://172.28.200.1:18201'}), \
             patch.object(self.health.urllib.request, 'urlopen', side_effect=OSError('unavailable')), \
             patch.object(self.health.socket, 'create_connection') as connection:
            with self.assertRaises(OSError): self.health.probe('tts')
            connection.assert_not_called()
        with patch.dict('os.environ', {'PLAPRE_UPSTREAM_URL': 'http://example.com:8004'}), \
             patch.object(self.health.urllib.request, 'urlopen') as http:
            with self.assertRaises(ValueError): self.health.probe('tts')
            http.assert_not_called()


if __name__ == '__main__':
    unittest.main()
