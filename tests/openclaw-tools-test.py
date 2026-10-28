#!/usr/bin/env python3
import importlib.util
import ast
import hashlib
import types
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import tempfile
import threading
import socket
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BROWSER = load('hc_browser', ROOT / 'deploy/openclaw/tools/browser_worker.py')
BROWSER_INSTALL = load('hc_browser_install', ROOT / 'scripts/setup-openclaw-browser.py')
CLI = load('hc_cli', ROOT / 'deploy/openclaw/tools/homecompute-cli')
# Load the pure preflight functions without running the in-sandbox CLI installer.
source=ast.parse((ROOT/'scripts/enable-openclaw-tools.py').read_text())
definitions=[item for item in source.body if isinstance(item,ast.FunctionDef)]
activation={'hashlib':hashlib,'MCP_TOOLS':{'homecompute__homecompute_'+name for name in
    ('systems','system_read','task_submit','task_status','task_cancel','ha_propose','ha_status','ha_cancel')}}
exec(compile(ast.Module(body=definitions,type_ignores=[]),'activation-preflight','exec'),activation)


class ToolsTest(unittest.TestCase):
    def test_authenticated_tab_cleanup_forwards_native_plain_text_response(self):
        calls=[]
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                calls.append(self.path)
                self.send_response(200);self.end_headers();self.wfile.write(b'Target is closing')
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        original=BROWSER.CDP;BROWSER.CDP=('127.0.0.1',upstream.server_port)
        token='x'*48
        proxy=BROWSER.serve(token,'http://127.0.0.1:18800',('127.0.0.1',0))
        for server in (upstream,proxy):threading.Thread(target=server.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(proxy.server_port)
        try:
            for action in ('close','activate'):
                with urllib.request.urlopen(base+'/json/'+action+'/public-tab?token='+token,timeout=2) as response:
                    self.assertEqual(response.read(),b'Target is closing')
            self.assertEqual(calls,['/json/close/public-tab','/json/activate/public-tab'])
            for suffix in ['/json/close/public-tab?token=wrong','/json/new?token='+token,
                           '/json/close/../../version?token='+token,'/json/close/?token='+token]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base+suffix,timeout=2)
                self.assertEqual(error.exception.code,403);error.exception.close()
            self.assertEqual(len(calls),2)
        finally:
            for server in (proxy,upstream):server.shutdown();server.server_close()
            BROWSER.CDP=original

    def test_browser_internal_addresses_cannot_collide_after_restart(self):
        calls=[]
        def docker(*args):
            calls.append(args)
            if args[:2] == ('inspect',BROWSER_INSTALL.BROWSER):
                return json.dumps([{'Config':{'Hostname':BROWSER_INSTALL.BROWSER}}])
            return ''
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);(directory/'tools').mkdir()
            (directory/'tools/browser-squid.conf').write_text('reviewed fixture')
            with patch.object(BROWSER_INSTALL,'docker',side_effect=docker), \
                 patch.object(BROWSER_INSTALL.subprocess,'run',return_value=types.SimpleNamespace(returncode=1)), \
                 patch('builtins.print'):
                BROWSER_INSTALL.install(directory,'sha256:'+'a'*64)
        internal={}
        for command in calls:
            if command[0]=='create' and command[command.index('--network')+1]==BROWSER_INSTALL.BROWSER:
                name=command[command.index('--name')+1]
            elif command[:2]==('network','connect') and command[-2]==BROWSER_INSTALL.BROWSER:
                name=command[-1]
            else:
                continue
            self.assertIn('--ip',command, f'{name} would race reserved addresses on restart')
            internal[name]=command[command.index('--ip')+1]
        self.assertEqual(internal,{
            BROWSER_INSTALL.BROWSER:'172.30.188.2', BROWSER_INSTALL.EGRESS:'172.30.188.3',
            BROWSER_INSTALL.INGRESS:'172.30.188.4', BROWSER_INSTALL.DNS:'172.30.188.5'})
        self.assertEqual(len(set(internal.values())),4)

    @unittest.skipUnless(shutil.which('ruby'),'Ruby YAML parser unavailable')
    def test_compose_reserves_the_same_four_internal_addresses(self):
        result=subprocess.run(['ruby','-ryaml','-rjson','-e',
            'c=YAML.safe_load(File.read(ARGV[0])); puts JSON.generate(c.fetch("services").transform_values { |s| s.fetch("networks").fetch("browser").fetch("ipv4_address") })',
            str(ROOT/'deploy/openclaw/tools/compose.yaml')],capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),{'browser':'172.30.188.2',
            'browser-egress':'172.30.188.3','browser-ingress':'172.30.188.4','browser-dns':'172.30.188.5'})

    def test_browser_hostname_exceptions_are_finite_and_keep_private_network_denied(self):
        policy=activation['browser_ssrf_policy']()
        self.assertIs(policy['dangerouslyAllowPrivateNetwork'],False)
        hosts=policy['allowedHostnames']
        self.assertIn('127.0.0.1',hosts)
        self.assertIn('example.com',hosts)
        self.assertIn('docs.openclaw.ai',hosts)
        self.assertEqual(len(hosts),len(set(hosts)))
        self.assertTrue(all('*' not in host for host in hosts))
        self.assertEqual(set(hosts)-{'127.0.0.1'},BROWSER.PUBLIC_RESEARCH_HOSTS)
        if shutil.which('node'):
            result=subprocess.run(['node','--input-type=module','-e',
                'import {RESEARCH_HOSTS} from "./deploy/openclaw/safety-plugin/public-dns.mjs"; console.log(JSON.stringify(RESEARCH_HOSTS));'],
                cwd=ROOT,capture_output=True,text=True,check=True)
            self.assertEqual(set(json.loads(result.stdout)),BROWSER.PUBLIC_RESEARCH_HOSTS)
        self.assertFalse(any(host in hosts for host in ['home-core','169.254.169.254','10.77.20.1']))
        policy['allowedHostnames'].append('unsafe.example')
        self.assertNotIn('unsafe.example',activation['browser_ssrf_policy']()['allowedHostnames'])

    def test_public_dns_rejects_unknown_private_and_mixed_answers(self):
        calls=[]
        def resolver(hostname,_port,**_kwargs):
            calls.append(hostname)
            return [(socket.AF_INET,socket.SOCK_STREAM,0,'',('93.184.216.34',0)),
                    (socket.AF_INET6,socket.SOCK_STREAM,0,'',('2606:4700::1111',0,0,0))]
        value=BROWSER.public_dns('example.com',resolver)
        self.assertEqual({row['family'] for row in value['addresses']},{4,6})
        self.assertEqual(value['ttl'],60)
        for hostname in ['example.com.evil','10.77.20.1','169.254.169.254','example.com\n','*.example.com']:
            with self.assertRaises(ValueError):BROWSER.public_dns(hostname,resolver)
        self.assertEqual(calls,['example.com'])
        for private in ['127.0.0.1','10.77.20.1','169.254.169.254','::1','fd00::1','192.0.2.1','::ffff:10.77.20.1','::ffff:93.184.216.34']:
            def mixed(*args,**kwargs):
                rows=resolver(*args,**kwargs)
                return rows+[(socket.AF_INET6 if ':' in private else socket.AF_INET,socket.SOCK_STREAM,0,'',(private,0))]
            with self.assertRaises(ValueError):BROWSER.public_dns('example.com',mixed)

    def test_public_dns_http_requires_exact_authentication_and_scope_before_lookup(self):
        token='q'*48
        proxy=BROWSER.serve(token,'http://127.0.0.1:18800',('127.0.0.1',0))
        threading.Thread(target=proxy.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(proxy.server_port)
        answer={'hostname':'example.com','addresses':[{'address':'93.184.216.34','family':4}],'ttl':60}
        try:
            with patch.object(BROWSER,'public_dns',return_value=answer) as resolve:
                for suffix in ['/json/public-dns?hostname=example.com',
                    '/json/public-dns?hostname=example.com&token=wrong',
                    '/json/public-dns?hostname=home-core&token='+token,
                    '/json/public-dns?hostname=example.com&hostname=github.com&token='+token,
                    '/json/public-dns?hostname=example.com&token='+token+'&extra=1']:
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+suffix,timeout=2)
                    self.assertEqual(error.exception.code,403);error.exception.close()
                resolve.assert_not_called()
                with urllib.request.urlopen(base+'/json/public-dns?hostname=example.com&token='+token,timeout=2) as response:
                    self.assertEqual(json.load(response),answer)
                resolve.assert_called_once_with('example.com')
            with patch.object(BROWSER,'public_dns',side_effect=ValueError('mixed private answer')):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base+'/json/public-dns?hostname=example.com&token='+token,timeout=2)
                self.assertEqual(error.exception.code,502);error.exception.close()
        finally:proxy.shutdown();proxy.server_close()

    def test_dns_only_sidecar_has_no_cdp_and_fixed_forwarder_rechecks_every_answer(self):
        token='q'*48
        server=BROWSER.serve(token,'http://127.0.0.1:18800',('127.0.0.1',0),dns_only=True)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with patch.object(BROWSER,'public_dns') as resolve:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen('http://127.0.0.1:'+str(server.server_port)+'/json/version?token='+token,timeout=2)
                self.assertEqual(error.exception.code,403);error.exception.close();resolve.assert_not_called()
        finally:server.shutdown();server.server_close()
        value={'hostname':'example.com','ttl':60,'addresses':[{'family':4,'address':'93.184.216.34'}]}
        class Connection:
            def __init__(self,hostname,port,timeout):
                self.hostname=hostname;self.port=port;self.timeout=timeout
            def request(self,method,path):
                self.method=method;self.path=path
            def getresponse(self):
                return types.SimpleNamespace(status=200,read=lambda _max:json.dumps(value).encode())
            def close(self):pass
        created=[]
        def connect(*args,**kwargs):created.append(Connection(*args,**kwargs));return created[-1]
        with patch.object(BROWSER.http.client,'HTTPConnection',side_effect=connect):
            self.assertEqual(BROWSER.remote_public_dns('example.com',token),value)
            self.assertEqual((created[0].hostname,created[0].port,created[0].timeout),('browser-dns',18802,3))
            self.assertEqual(created[0].method,'GET')
            for private in ['10.77.20.1','::ffff:10.77.20.1','169.254.169.254']:
                value['addresses'].append({'family':6 if ':' in private else 4,'address':private})
                with self.assertRaises(ValueError):BROWSER.remote_public_dns('example.com',token)
                value['addresses'].pop()
            for malformed in [[], None]:
                value=malformed
                with self.assertRaises(ValueError):BROWSER.remote_public_dns('example.com',token)
            with self.assertRaises(ValueError):BROWSER.remote_public_dns('home-core',token)

    def test_activation_preserves_only_existing_finite_mcp_tools(self):
        before={'tools':{'alsoAllow':['read','bundle-mcp']},'agents':{'entries':{'main':{'tools':{
            'allow':['read','homecompute__homecompute_systems','homecompute__homecompute_ha_propose']}}}}}
        main,glob=activation['retained_tools'](before,['read','exec'])
        self.assertEqual(set(main),{'read','exec','homecompute__homecompute_systems','homecompute__homecompute_ha_propose'})
        self.assertEqual(glob,['read','exec','bundle-mcp'])
        before['agents']['entries']['main']['tools']['allow'].append('homecompute__homecompute_ha_execute')
        with self.assertRaises(AssertionError):activation['retained_tools'](before,['read','exec'])
        before['agents']['entries']['main']['tools']['allow']=[]
        before['tools']['alsoAllow']=['*']
        with self.assertRaises(AssertionError):activation['retained_tools'](before,['read','exec'])

    def test_guard_checks_manifest_package_ownership_modes_digests_and_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            guard=Path(folder)/'guard';guard.mkdir(mode=0o755)
            digests={}
            for name in ['index.mjs','browser-relay.mjs','openclaw.plugin.json','package.json','public-dns.mjs']:
                path=guard/name;path.write_text(name);path.chmod(0o444)
                digests[name]=hashlib.sha256(path.read_bytes()).hexdigest()
            original=Path.stat
            def root_stat(path,*args,**kwargs):
                value=original(path,*args,**kwargs)
                return types.SimpleNamespace(st_uid=0,st_mode=value.st_mode)
            with patch.object(Path,'stat',root_stat):
                activation['protected_guard'](guard,digests)
                for name in ['openclaw.plugin.json','package.json','public-dns.mjs']:
                    path=guard/name;path.chmod(0o644)
                    with self.assertRaises(AssertionError):activation['protected_guard'](guard,digests)
                    path.chmod(0o444)
                    wrong={**digests,name:'0'*64}
                    with self.assertRaises(AssertionError):activation['protected_guard'](guard,wrong)
                package=guard/'package.json';package.unlink();package.symlink_to(guard/'index.mjs')
                with self.assertRaises(AssertionError):activation['protected_guard'](guard,digests)
            if original(guard).st_uid!=0:
                with self.assertRaises(AssertionError):activation['protected_guard'](guard,digests)

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
        proxy=BROWSER.serve('x'*48,'http://127.0.0.1:18800',('127.0.0.1',0))
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
            self.assertEqual(value['webSocketDebuggerUrl'],'ws://127.0.0.1:18800/devtools/browser/synthetic?token='+'x'*48)
        finally:
            for server in [proxy,upstream]:server.shutdown();server.server_close()
            BROWSER.CDP=original


if __name__=='__main__':unittest.main()
