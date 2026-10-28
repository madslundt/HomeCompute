#!/usr/bin/env python3
"""Real kernel regression in disposable Docker namespaces, with no host network.

Build tests/fixtures/Dockerfile.speech-cutover as homecompute/speech-cutover-test.
The fixture needs network/mount namespace capabilities only inside that fresh
container; it receives no Docker socket, host network, or writable host mounts.
"""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'homecompute/speech-cutover-test'
FIXTURE = r'''
import os,runpy,socket,subprocess,sys,threading
m=runpy.run_path('/opt/setup-speech-boundary.py')
# Functions keep the dictionary supplied by runpy as their globals.
g=m['core_maintenance'].__wrapped__.__globals__
def command(*args):subprocess.run(args,check=True,capture_output=True,timeout=10)
def canonical(reset=False):
    command('iptables','-F' if reset else '-N','HC-COMPUTE-TUNNEL')
    command('iptables','-A','HC-COMPUTE-TUNNEL','-s','172.28.200.3/32','-d','172.28.200.1/32',
            '-p','tcp','--dport','18005','-m','conntrack','--ctstate','NEW,ESTABLISHED','-j','ACCEPT')
    command('iptables','-A','HC-COMPUTE-TUNNEL','-j','REJECT')
def prepend():
    command('iptables','-I','INPUT','1','-d','172.28.200.1/32','-p','tcp','--dport','18005','-j','HC-COMPUTE-TUNNEL')
command('ip','netns','add','hc-client')
command('ip','link','add','hc-server','type','veth','peer','name','hc-client')
command('ip','link','set','hc-client','netns','hc-client')
command('ip','address','add','172.28.200.1/24','dev','hc-server')
command('ip','link','set','hc-server','up')
command('ip','-n','hc-client','address','add','172.28.200.3/24','dev','hc-client')
command('ip','-n','hc-client','link','set','hc-client','up')
command('ip','-n','hc-client','link','set','lo','up')
server=socket.socket();server.bind(('172.28.200.1',18005));server.listen()
def echo(c):
    with c:
        while b:=c.recv(1):c.sendall(b)
def accept():
    while True:
        c,_=server.accept();threading.Thread(target=echo,args=(c,),daemon=True).start()
threading.Thread(target=accept,daemon=True).start()
canonical();prepend()
client=r"""import socket,sys
c=socket.create_connection(('172.28.200.1',18005),timeout=2);print('ready',flush=True)
for line in sys.stdin:
 c.sendall(b'x');assert c.recv(1)==b'x';print('alive',flush=True)
"""
held=subprocess.Popen(['ip','netns','exec','hc-client','python3','-u','-c',client],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
assert held.stdout.readline().strip()=='ready'
def existing_alive():
    held.stdin.write('check\n');held.stdin.flush();assert held.stdout.readline().strip()=='alive'
def new_allowed():
    code="import socket; c=socket.create_connection(('172.28.200.1',18005),timeout=.4); c.close()"
    return subprocess.run(['ip','netns','exec','hc-client','python3','-c',code],capture_output=True,timeout=3).returncode==0
if os.environ['PLACEMENT']=='historical-input':
    original=g['run']
    def old_run(args,**kwargs):
        args=list(args)
        if '-t' in args:
            i=args.index('-t');del args[i:i+2]
        args=['INPUT' if part=='PREROUTING' else part for part in args]
        return original(args,**kwargs)
    g['run']=old_run
# Real active socket preservation is tested here; the separately tested drain
# refuses to yield while this deliberate synthetic connection remains open.
g['drain_main_http']=lambda timeout:None
try:
    with m['core_maintenance'](3):
        existing_alive();assert not new_allowed()
        prepend()  # Canonical tunnel startup inserts above filter INPUT gates.
        canonical(reset=True)  # The permanent chain is also rebuilt on start.
        existing_alive()
        allowed=new_allowed()
        assert allowed==(os.environ['PLACEMENT']=='historical-input')
    assert new_allowed();existing_alive()
finally:
    held.terminate();held.wait(timeout=3);server.close()
    command('ip','netns','delete','hc-client')
print('PASS: existing traffic preserved; canonical filter rewrite comparison matched')
'''


class SpeechCutoverFirewallTest(unittest.TestCase):
    def test_real_gate_survives_filter_rewrite_and_preserves_established_stream(self):
        self.fixture('mangle-prerouting')

    def test_historical_input_gate_is_bypassed_by_canonical_prepend(self):
        self.fixture('historical-input')

    def fixture(self, placement):
        if subprocess.run(['docker','image','inspect',IMAGE],capture_output=True).returncode:
            self.skipTest('Build the pinned isolated speech cutover test image first')
        result=subprocess.run(['docker','run','--rm','-i','--network','none','--read-only',
            '--cap-drop','ALL','--cap-add','NET_ADMIN','--cap-add','NET_RAW','--cap-add','SYS_ADMIN',
            '--security-opt','seccomp=unconfined','--security-opt','apparmor=unconfined',
            '--memory','256m','--pids-limit','64','--cpus','1',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=8m','--tmpfs','/run:rw,nosuid,nodev,size=8m',
            '-e','PYTHONDONTWRITEBYTECODE=1','-e','PLACEMENT='+placement,
            '--mount','type=bind,source='+str(ROOT/'scripts/setup-speech-boundary.py')+',target=/opt/setup-speech-boundary.py,readonly',
            IMAGE,'python3','-'],input=FIXTURE,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PASS:',result.stdout)


if __name__=='__main__':unittest.main()
