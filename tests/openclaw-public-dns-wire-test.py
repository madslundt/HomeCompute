#!/usr/bin/env python3
"""Exercise real prebound UDP/TCP descriptors without privileged ports."""
import json
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import unittest

MODULE=Path(__file__).resolve().parents[1]/'deploy/openclaw/safety-plugin/public-dns.mjs'

def question(host='example.com',kind=1,qclass=1):
    return struct.pack('!HHHHHH',123,256,1,0,0,0)+b''.join(bytes([len(s)])+s.encode() for s in host.split('.'))+b'\0'+struct.pack('!HH',kind,qclass)

class InheritedDnsTests(unittest.TestCase):
    def setUp(self):
        code="import {startInheritedDns,createDnsEngine} from "+json.dumps(MODULE.as_uri())+";const stop=await startInheritedDns(createDnsEngine(hostname=>({hostname,ttl:60,addresses:[{family:4,address:'93.184.216.34'},{family:6,address:'2606:4700::1111'}]})));console.log('ready');process.once('SIGTERM',()=>stop().then(()=>process.exit(0)));"
        bootstrap="""import os,socket,sys
u=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);u.bind(('127.0.0.1',0))
t=socket.socket();t.bind(('127.0.0.1',u.getsockname()[1]));t.listen(16)
assert u.fileno()==3 and t.fileno()==4
os.set_inheritable(3,True);os.set_inheritable(4,True)
print(u.getsockname()[1],flush=True)
os.execv(sys.argv[1],[sys.argv[1],'--input-type=module','-e',sys.argv[2]])
"""
        self.child=subprocess.Popen([sys.executable,'-c',bootstrap,shutil.which('node'),code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.port=int(self.child.stdout.readline());self.assertEqual(self.child.stdout.readline().strip(),'ready')
    def tearDown(self):
        self.child.terminate();_,stderr=self.child.communicate(timeout=3);self.assertEqual(self.child.returncode,0,stderr)
    def udp(self,payload):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as conn:
            conn.settimeout(2);conn.sendto(payload,('127.0.0.1',self.port));return conn.recv(4096)
    def test_udp_a_aaaa_unknown_type_class_and_compression(self):
        for kind in [1,28]:
            answer=self.udp(question(kind=kind));self.assertEqual(struct.unpack('!H',answer[2:4])[0]&15,0);self.assertEqual(answer[6:8],b'\0\1')
        for payload in [question('home-core'),question(kind=5),question(qclass=3)]:
            self.assertEqual(struct.unpack('!H',self.udp(payload)[2:4])[0]&15,3)
        self.assertEqual(struct.unpack('!H',self.udp(question()[:12]+b'\xc0\x0c\0\1\0\1')[2:4])[0]&15,1)
    def test_tcp_bounds_and_valid_length_framing(self):
        with socket.create_connection(('127.0.0.1',self.port),timeout=2) as conn:
            payload=question();conn.sendall(struct.pack('!H',len(payload))+payload)
            size=struct.unpack('!H',conn.recv(2))[0];answer=b''
            while len(answer)<size:answer+=conn.recv(size-len(answer))
            self.assertEqual(struct.unpack('!H',answer[2:4])[0]&15,0)
        for size in [0,11,4097,65535]:
            with socket.create_connection(('127.0.0.1',self.port),timeout=2) as conn:
                conn.sendall(struct.pack('!H',size));self.assertEqual(conn.recv(1),b'')

if __name__=='__main__':unittest.main()
