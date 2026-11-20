#!/usr/bin/env python3
"""Plan/apply local Open WebUI audio through its authenticated configuration API."""
from __future__ import annotations
import argparse
from copy import deepcopy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/homecompute-open-webui-speech')
BASE = 'https://llm.home.arpa/v1'
LEGACY_BASE = 'http://litellm:4000/v1'
CONTAINER = 'homecompute-open-webui-open-webui-1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def candidate(before):
    after = deepcopy(before)
    for kind in ('stt', 'tts'):
        current = after[kind]
        if (current['ENGINE'] not in ('', 'openai') or current['OPENAI_API_BASE_URL'] not in
                ('https://api.openai.com/v1', BASE, LEGACY_BASE)):
            raise ValueError('Existing audio provider requires review')
        current.update(ENGINE='openai', OPENAI_API_BASE_URL=BASE, OPENAI_API_KEY='local')
    after['stt'].update(MODEL='whisper', OPENAI_API_REQUEST_FORMAT='multipart')
    if set(after['tts'].get('OPENAI_PARAMS') or {}) - {'response_format'}:
        raise ValueError('Existing speech parameters require review')
    after['tts'].update(MODEL='tts-danish', VOICE='danish-default', OPENAI_PARAMS={'response_format': 'wav'})
    return after


REMOTE = '''import json,sqlite3,time,uuid,urllib.request,jwt,hashlib
from pathlib import Path
value=json.loads(REQUEST_JSON)
with sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True) as db:
 admins=db.execute("SELECT id FROM user WHERE role='admin' ORDER BY created_at,id").fetchall()
if len(admins)!=1:raise RuntimeError('One existing administrator required')
secret=Path('/run/secrets/webui_secret_key').read_text().strip()
now=int(time.time())
authorization='Bearer '+jwt.encode({'id':admins[0][0],'iat':now,'exp':now+300,'jti':str(uuid.uuid4())},secret,algorithm='HS256')
def api(method,path,body=None):
 req=urllib.request.Request('http://127.0.0.1:8080'+path,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':authorization,'Content-Type':'application/json'},method=method)
 with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
before=api('GET','/api/v1/audio/config')
if value['action']=='read':print(json.dumps(before))
else:
 if digest(before)!=value['expected']:raise RuntimeError('Audio configuration changed')
 desired=value['candidate']
 try:
  api('POST','/api/v1/audio/config/update',desired)
  after=api('GET','/api/v1/audio/config')
  if after!=desired:raise RuntimeError('Audio readback mismatch')
 except Exception:
  current=api('GET','/api/v1/audio/config')
  if current==desired:api('POST','/api/v1/audio/config/update',before)
  raise
 print(json.dumps({'verified':True}))
'''


def remote(action, **values):
    code = 'REQUEST_JSON=' + repr(json.dumps({'action': action, **values})) + '\n' + REMOTE
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'python', '-'],
                            input=code, text=True, capture_output=True, timeout=100)
    if result.returncode:
        raise RuntimeError('Open WebUI audio API operation failed; inspect private backup and current state')
    return json.loads(result.stdout)


def private_write(path, data):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('Private path must not be a symlink')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


def setup(apply=False, revision=None):
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        raise ValueError('Run as root on home-core')
    if apply:
        spec = importlib.util.spec_from_file_location('speech_lifecycle', ROOT / 'scripts/setup-omnivoice.py')
        lifecycle = importlib.util.module_from_spec(spec); spec.loader.exec_module(lifecycle)
        lifecycle.published(revision)
    before = remote('read')
    proposed = candidate(before)
    changed = proposed != before
    if apply:
        if changed:
            STATE.mkdir(mode=0o700, exist_ok=True)
            private_write(STATE / ('before-'+str(uuid.uuid4())+'.json'), json.dumps(before).encode())
            remote('apply', expected=digest(before), candidate=proposed)
        if remote('read') != proposed:
            raise ValueError('Speech configuration verification failed')
    return {'applied': apply, 'changed': changed, 'stt_model': 'whisper', 'tts_model': 'tts-danish',
            'voice': 'danish-default', 'format': 'wav', 'endpoint': BASE}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--revision')
    args=parser.parse_args()
    try:
        with open('/var/lib/homecompute/deploy.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            print(json.dumps(setup(args.apply,args.revision)))
    except Exception:
        raise SystemExit('Open WebUI speech setup refused; inspect private state without exposing credentials') from None
