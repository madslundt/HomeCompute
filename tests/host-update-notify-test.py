import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('host_update_notify', ROOT / 'scripts/host-update-notify.py')
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)


def report(host='home-core', items=(), summary=None):
    return {'schema_version': 1, 'host': host,
            'generated_at': '2026-11-11T05:55:01Z', 'items': list(items),
            'summary': summary or {'current': len(items)}}


def item(component, status, **extra):
    return dict(component=component, kind='packages', status=status, **extra)


class HostUpdateNotifyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.path = Path(self.temp.name) / 'report.json'
        real_lstat = Path.lstat
        def root_lstat(path):
            info = list(real_lstat(path))
            info[0] = (info[0] & ~0o170000) | stat.S_IFREG | 0o644
            info[4] = 0
            return os.stat_result(info)
        lstat = patch.object(Path, 'lstat', root_lstat)
        lstat.start()
        self.addCleanup(lstat.stop)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        self.path.write_bytes(json.dumps(value).encode())

    def run_main(self, argv, env=None):
        environ = {name: value for name, value in env.items() if value is not None}
        with patch.dict(os.environ, environ, clear=True), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = notify.main(argv)
        return code, output.getvalue()

    def test_current_report_is_silent_success(self):
        self.write(report(items=[item('packages', 'current')]))
        opened = Mock()
        with patch.object(notify, 'urlopen', opened):
            code, output = self.run_main(['--host', 'home-core', '--report', str(self.path)],
                                         {'HOMECOMPUTE_HOST_UPDATE_WEBHOOK_URL': 'https://events.invalid/hc'})
        self.assertEqual(code, 0)
        opened.assert_not_called()
        self.assertIn('host update current', output)

    def test_attention_without_url_stays_in_journal(self):
        self.write(report(items=[item('nvidia-driver', 'approval_required', candidate='580.76')]))
        opened = Mock()
        with patch.object(notify, 'urlopen', opened):
            code, output = self.run_main(['--host', 'home-core', '--report', str(self.path)], {})
        self.assertEqual(code, 0)
        opened.assert_not_called()
        self.assertIn('host update attention', output)

    def test_attention_posts_bounded_https_payload(self):
        items = [item('nvidia-driver', 'approval_required', candidate='580.76', installed='580.60'),
                 item('immich', 'failed', reason='rollback_completed'),
                 item('packages', 'current')]
        self.write(report(host='home-spark', items=items, summary={'current': 1, 'approval_required': 1, 'failed': 1}))
        response = Mock()
        response.__enter__ = lambda stream: stream
        response.__exit__ = Mock(return_value=False)
        response.status = 204
        opened = Mock(return_value=response)
        with patch.object(notify, 'urlopen', opened):
            code, output = self.run_main(['--host', 'home-spark', '--report', str(self.path)],
                                         {'HOMECOMPUTE_HOST_UPDATE_WEBHOOK_URL': 'https://events.invalid/hc'})
        self.assertEqual(code, 0)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, 'https://events.invalid/hc')
        payload = json.loads(request.data)
        self.assertEqual(payload['type'], 'homecompute.host-update')
        self.assertEqual(payload['data']['host'], 'home-spark')
        self.assertEqual(payload['data']['summary'], {'approval_required': 1, 'current': 1, 'failed': 1})
        self.assertEqual({row['component'] for row in payload['data']['attention']},
                         {'nvidia-driver', 'immich'})

    def test_configured_secret_signs_the_delivery(self):
        import hashlib
        import hmac
        self.write(report(items=[item('nvidia-driver', 'approval_required')]))
        response = Mock()
        response.__enter__ = lambda stream: stream
        response.__exit__ = Mock(return_value=False)
        response.status = 202
        opened = Mock(return_value=response)
        with patch.object(notify, 'urlopen', opened):
            code, _ = self.run_main(['--host', 'home-core', '--report', str(self.path)],
                                    {'HOMECOMPUTE_HOST_UPDATE_WEBHOOK_URL': 'https://events.invalid/hc',
                                     'HOMECOMPUTE_HOST_UPDATE_WEBHOOK_SECRET': 's3cret'})
        self.assertEqual(code, 0)
        request = opened.call_args.args[0]
        headers = {name.lower(): value for name, value in request.headers.items()}
        signed = ('%s.%s.' % (headers['x-openbot-timestamp'],
                              headers['x-openbot-delivery-id'])).encode() + request.data
        self.assertEqual(headers['x-openbot-signature'], 'sha256='
                         + hmac.new(b's3cret', signed, hashlib.sha256).hexdigest())

    def test_refused_status_is_a_visible_failure(self):
        self.write(report(items=[item('docker-immich', 'blocked', reason='operator_reconciliation_required')]))
        response = Mock()
        response.__enter__ = lambda stream: stream
        response.__exit__ = Mock(return_value=False)
        response.status = 503
        with patch.dict(os.environ, {'HOMECOMPUTE_HOST_UPDATE_WEBHOOK_URL': 'https://events.invalid/hc'}), \
             patch.object(notify, 'urlopen', return_value=response), \
             contextlib.redirect_stdout(io.StringIO()):
            code = notify.main(['--host', 'home-core', '--report', str(self.path)])
        self.assertEqual(code, 1)

    def test_delivery_failure_is_visible(self):
        self.write(report(items=[item('docker-immich', 'blocked', reason='operator_reconciliation_required')]))
        with patch.dict(os.environ, {'HOMECOMPUTE_HOST_UPDATE_WEBHOOK_URL': 'https://events.invalid/hc'}), \
             patch.object(notify, 'urlopen', side_effect=OSError('unreachable')), \
             contextlib.redirect_stdout(io.StringIO()):
            code = notify.main(['--host', 'home-core', '--report', str(self.path)])
        self.assertEqual(code, 1)

    def test_plain_http_or_padded_url_is_refused(self):
        self.write(report(items=[item('x', 'review')]))
        for url in ('http://events.invalid/hc', ' https://events.invalid/hc'):
            with self.assertRaises(ValueError):
                self.run_main(['--host', 'home-core', '--report', str(self.path)],
                              {'HOMECOMPUTE_HOST_UPDATE_WEBHOOK_URL': url})

    def test_host_mismatch_and_unsupported_schema_are_refused(self):
        self.write(report(host='home-spark', items=[item('x', 'review')]))
        with self.assertRaises(ValueError):
            self.run_main(['--host', 'home-core', '--report', str(self.path)], {})
        broken = report(items=[item('x', 'review')])
        broken['schema_version'] = 99
        self.write(broken)
        with self.assertRaises(ValueError):
            self.run_main(['--host', 'home-core', '--report', str(self.path)], {})

    def test_attention_rows_are_bounded_and_drop_unbounded_text(self):
        rows = notify.attention_items([item('wide', 'review', reason='r' * 300),
                                       item('odd', 'review', installed=7)])
        self.assertNotIn('reason', rows[0])
        self.assertNotIn('installed', rows[1])
        self.assertTrue(all(len(text) <= 256 for row in rows for text in row.values()))
        long_rows = notify.attention_items([item('c%03d' % n, 'review') for n in range(200)])
        self.assertEqual(len(long_rows), notify.MAX_ITEMS)


if __name__ == '__main__':
    unittest.main()
