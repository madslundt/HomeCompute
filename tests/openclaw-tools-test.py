#!/usr/bin/env python3
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BROWSER = load('hc_browser', ROOT / 'deploy/openclaw/tools/browser_worker.py')
CLI = load('hc_cli', ROOT / 'deploy/openclaw/tools/homecompute-cli')


class ToolsTest(unittest.TestCase):
    def test_cli_numeric_input_cannot_execute_python(self):
        self.assertEqual(CLI.calculate('(6 * 7) + 1'), 43)
        for text in ["__import__('os').system('id')", '2 ** 1000', 'True', '1 / 0', '1e200']:
            with self.assertRaises((ValueError, ArithmeticError)):
                CLI.calculate(text)
        with self.assertRaises(ValueError):
            CLI.main(['bash', '-c', 'id'])

    def test_cli_workspace_paths_reject_parent_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'workspace';root.mkdir()
            (root / 'escape').symlink_to(Path(folder))
            for path in ['../private', 'escape/private', '/etc/passwd']:
                with self.assertRaises(ValueError):
                    CLI.path(path, root)
            self.assertEqual(CLI.path('skills', root), root.resolve() / 'skills')

    def test_cdp_token_is_required_before_upstream_access(self):
        calls = []
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                calls.append(self.path)
                data=json.dumps({'Browser':'Synthetic Chromium',
                    'webSocketDebuggerUrl':'ws://127.0.0.1:18801/devtools/browser/synthetic'}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        original=BROWSER.CDP;BROWSER.CDP=upstream.server_address
        proxy=BROWSER.serve('x'*48,'http://172.18.0.1:18800',('127.0.0.1',0))
        for server in [upstream,proxy]:
            threading.Thread(target=server.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(proxy.server_port)
        try:
            for suffix in ['/json/version','/json/version?token=wrong','/etc/passwd?token='+'x'*48,
                           '/json/version?token='+'x'*48+'&token='+'x'*48]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base+suffix,timeout=2)
                self.assertEqual(error.exception.code,403)
                error.exception.close()
            self.assertEqual(calls,[])
            with urllib.request.urlopen(base+'/json/version?token='+'x'*48,timeout=2) as response:
                value=json.load(response)
            self.assertEqual(calls,['/json/version'])
            self.assertEqual(value['webSocketDebuggerUrl'],'ws://172.18.0.1:18800/devtools/browser/synthetic?token='+'x'*48)
        finally:
            for server in [proxy,upstream]:server.shutdown();server.server_close()
            BROWSER.CDP=original


if __name__=='__main__':unittest.main()
