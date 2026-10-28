#!/usr/bin/env python3
"""Actual pinned CPU image: Wyoming clients, HTTP auth, and ffmpeg PCM conversion.

Build deploy/control-plane/Dockerfile.speech-adapters first. No network, model,
household action, or private data is used; fixture inference runs on loopback.
"""
from __future__ import annotations

import subprocess
import unittest
import json
from pathlib import Path

IMAGE = 'homecompute/speech-adapters:wyoming-1.10.2'
FIXTURE = r'''
import asyncio, contextlib, importlib.util, json, math, struct, sys, threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioStart, AudioChunk, AudioStop
from wyoming.client import AsyncClient
from wyoming.error import Error
from wyoming.server import AsyncServer
from wyoming.tts import Synthesize, SynthesizeVoice

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec); sys.modules[name]=value
    spec.loader.exec_module(value); return value

stt=module('stt','/opt/homecompute/wyoming-openai-stt.py')
tts=module('tts','/opt/homecompute/plapre-wyoming.py')
requests={'stt':0,'tts':0}
pcm=b''.join(struct.pack('<h',int(2000*math.sin(2*math.pi*220*i/24000))) for i in range(24000))
class Inference(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        body=self.rfile.read(int(self.headers['Content-Length']))
        if self.path=='/v1/audio/transcriptions':
            assert self.headers['Authorization']=='Bearer fixture-key'
            assert b'RIFF' in body and b'stt-danish' in body and b'\r\n\r\nda\r\n' in body
            requests['stt']+=1; output=json.dumps({'text':'synthetic transcript'}).encode()
            self.send_response(200); self.send_header('Content-Type','application/json')
        else:
            assert self.path=='/v1/audio/speech'
            value=json.loads(body); assert value=={'text':'Hej fra testen.','speaker':'tor'}
            requests['tts']+=1; output=pcm
            self.send_response(200)
            for key,value in {'Content-Type':'audio/pcm','X-Sample-Rate':'24000','X-Channels':'1','X-Bit-Depth':'16'}.items():
                self.send_header(key,value)
        self.send_header('Content-Length',str(len(output))); self.end_headers(); self.wfile.write(output)

http=ThreadingHTTPServer(('127.0.0.1',8004),Inference)
threading.Thread(target=http.serve_forever,daemon=True).start()
async def main():
    cfg=stt.Config('0.0.0.0',10301,'http://127.0.0.1:8004/v1/audio/transcriptions',b'fixture-key','stt-danish','da',10000000,60,120,4)
    gateway=stt.OpenAITranscriber(cfg)
    asr=await asyncio.start_server(lambda r,w:stt.handle_client(r,w,cfg,gateway),'127.0.0.1',10301)
    tcfg=tts.Config('tcp://127.0.0.1:10201','http://127.0.0.1:8004','danish-default','tor',1.2,2000,48000000,300,4096)
    client=tts.PlapreHttpClient(tcfg.upstream_url,300,48000000,tcfg.tempo)
    task=asyncio.create_task(AsyncServer.from_uri(tcfg.uri).run(partial(tts.PlapreEventHandler,client,tcfg)))
    try:
        async with AsyncClient.from_uri('tcp://127.0.0.1:10301',connect_timeout=5,read_timeout=5) as c:
            await c.write_event(Transcribe(name='stt-danish',language='da').event())
            await c.write_event(AudioStart(rate=16000,width=2,channels=1).event())
            await c.write_event(AudioChunk(rate=16000,width=2,channels=1,audio=b'\0\0'*8000).event())
            await c.write_event(AudioStop().event())
            event=await c.read_event(); assert Transcript.is_type(event.type)
            assert Transcript.from_event(event).text=='synthetic transcript'
        async with AsyncClient.from_uri(tcfg.uri,connect_timeout=5,read_timeout=10) as c:
            await c.write_event(Synthesize(text='Hej fra testen.',voice=SynthesizeVoice(name='danish-default')).event())
            total=0
            while True:
                event=await c.read_event(); assert event is not None and not Error.is_type(event.type)
                if AudioStart.is_type(event.type):
                    a=AudioStart.from_event(event); assert (a.rate,a.width,a.channels)==(24000,2,1)
                if AudioChunk.is_type(event.type): total+=len(AudioChunk.from_event(event).audio)
                if AudioStop.is_type(event.type): break
            assert 0<total<len(pcm) and total%2==0
            await c.write_event(Synthesize(text='Hej fra testen.',voice=SynthesizeVoice(name='unapproved')).event())
            event=await c.read_event(); assert Error.is_type(event.type)
        assert requests=={'stt':1,'tts':1}
    finally:
        asr.close(); await asr.wait_closed(); task.cancel()
        with contextlib.suppress(asyncio.CancelledError): await task
asyncio.run(main()); http.shutdown()
print('PASS: real Wyoming STT/auth, TTS/PCM/tempo, and unapproved voice denial')
'''


class SpeechBoundaryContainerTest(unittest.TestCase):
    def test_rendered_ingress_tmpfs_is_accepted_by_engine_without_starting(self):
        if subprocess.run(['docker','image','inspect',IMAGE],capture_output=True).returncode:
            self.skipTest('Build the pinned speech adapter CPU image first')
        root=Path(__file__).resolve().parents[1]
        config=json.loads(subprocess.check_output(['docker','compose','--env-file',
            str(root/'config/plapre-tts.env.example'),'-f',
            str(root/'deploy/compute-node/plapre/compose.yaml'),'config','--format','json']))
        arguments=['docker','create','--network','none','--read-only','--cap-drop','ALL',
                   '--memory','64m','--pids-limit','64']
        for mount in config['services']['plapre-http']['tmpfs']: arguments+=['--tmpfs',mount]
        result=subprocess.run([*arguments,IMAGE,'python3','-c','pass'],capture_output=True,text=True,timeout=15)
        identifier=result.stdout.strip() if result.returncode==0 else None
        try:
            self.assertEqual(result.returncode,0,result.stderr)
            state=json.loads(subprocess.check_output(['docker','inspect',identifier]))[0]['State']
            self.assertEqual(state['Status'],'created')
            self.assertFalse(state['Running'])
        finally:
            if identifier: subprocess.run(['docker','rm',identifier],check=True,capture_output=True)

    def test_real_protocol_and_tempo_in_cpu_image(self):
        image = subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True)
        if image.returncode:
            self.skipTest('Build the pinned speech adapter CPU image first')
        result = subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--memory', '512m',
            '--pids-limit', '64', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=8m', IMAGE,
            'python3', '-'], input=FIXTURE, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASS:', result.stdout)


if __name__ == '__main__':
    unittest.main()
