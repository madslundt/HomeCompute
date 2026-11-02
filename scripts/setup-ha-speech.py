#!/usr/bin/env python3
"""Guarded core adapter and native HA registry/pipeline migration to Whisper.

Only the adapter source bind, STT alias, two primary display names and affected
STT pipeline references change. No .storage edits, entry deletion or model work.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/homecompute/ha-speech')
OLD = 'stt.hviske_vllm'
NEW = 'stt.whisper'
TTS = 'tts.omnivoice'
SERVICE = 'hviske-wyoming-proxy'
CONTAINER = 'homecompute-control-plane-' + SERVICE + '-1'
SCRIPT = 'scripts/wyoming-openai-stt.py'


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, **kwargs)
    if result.returncode:
        raise RuntimeError('Managed operation failed: ' + args[0])
    return result.stdout


def write(value, name):
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.is_symlink() or STATE.stat().st_mode & 0o077:
        raise ValueError('Private state directory required')
    path = STATE / (name + '-' + uuid.uuid4().hex + '.json')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())


def published(revision):
    spec = importlib.util.spec_from_file_location('whisper_lifecycle', ROOT / 'scripts/setup-whisper-stt.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.published(revision)


def pipeline_candidate(before):
    if before['stt_engine'] not in {OLD, NEW}:
        raise ValueError('Unexpected primary STT selection')
    if (before['stt_language'] != 'da' or before['tts_engine'] != TTS
            or before['tts_voice'] != 'danish-default'):
        raise ValueError('Unexpected primary speech policy')
    return {**before, 'stt_engine': NEW}


def adapter(revision):
    actual = json.loads(run(['docker', 'inspect', CONTAINER]))[0]
    labels = actual['Config'].get('Labels') or {}
    if (labels.get('com.docker.compose.project') != 'homecompute-control-plane'
            or labels.get('com.docker.compose.service') != SERVICE
            or not actual['State']['Running']):
        raise ValueError('Expected active owned STT adapter')
    code = run(['docker', 'exec', CONTAINER, 'cat', '/opt/homecompute/wyoming-openai-stt.py'])
    prior = run(['git', '-C', str(ROOT), 'show', '29fbc8b279927dfca2972857abd81e898df515e5:' + SCRIPT])
    if code not in (prior, (ROOT / SCRIPT).read_text()):
        raise ValueError('Unexpected adapter code drift; preserved')
    args = ['docker', 'compose', '--profile', 'speech-proxies', '--env-file', '/etc/homecompute/control-plane.env',
            '-f', str(ROOT / 'deploy/control-plane/compose.yaml')]
    desired = json.loads(run([*args, 'config', '--format', 'json']))['services'][SERVICE]
    env = dict(item.split('=', 1) for item in actual['Config']['Env'])
    ports = {}
    for port in desired['ports']:
        ports.setdefault(str(port['target'])+'/'+port.get('protocol','tcp'), []).append(
            {'HostIp':port.get('host_ip',''), 'HostPort':str(port['published'])})
    if (actual['HostConfig']['PortBindings'] != ports
            or set(actual['NetworkSettings']['Networks']) != {'homecompute-control-plane_edge'}
            or actual['NetworkSettings']['Networks']['homecompute-control-plane_edge']['IPAddress'] != desired['networks']['edge']['ipv4_address']
            or any(m['Destination'] not in {'/run/secrets/compute_api_key','/opt/homecompute/wyoming-openai-stt.py'} for m in actual['Mounts'])):
        raise ValueError('Unexpected adapter networking or mounts; preserved')

    if (actual['Config']['Image'] != desired['image'] or actual['Config']['Entrypoint'] != desired['entrypoint']
            or not actual['HostConfig']['ReadonlyRootfs'] or actual['HostConfig']['Privileged']
            or env.get('STT_MODEL') not in {'stt-danish', 'whisper'}
            or any(env.get(k) != v for k, v in desired['environment'].items() if k != 'STT_MODEL')):
        raise ValueError('Unexpected adapter runtime drift; preserved')
    mount = next((m for m in actual['Mounts'] if m['Destination'] == '/opt/homecompute/wyoming-openai-stt.py'), None)
    if env['STT_MODEL'] == 'whisper' and code == (ROOT / SCRIPT).read_text() and mount:
        print(json.dumps({'adapter': 'current', 'revision': revision})); return
    connection_probe = "from pathlib import Path;print(sum(int(r.split()[1].split(':')[1],16)==10301 and r.split()[3]=='01' for r in Path('/proc/net/tcp').read_text().splitlines()[1:]))"
    for _ in range(10):
        if run(['docker','exec',CONTAINER,'python3','-c',connection_probe]).strip() == '0':
            break
        time.sleep(1)
    else:
        raise ValueError('STT adapter is busy; no service change applied')
    write({'revision': revision, 'image': actual['Image'], 'source_labels': labels,
           'script_sha256': hashlib.sha256(code.encode()).hexdigest(), 'model': env['STT_MODEL']}, 'adapter-before')
    run([*args, 'up', '-d', '--no-deps', '--no-build', '--wait', '--wait-timeout', '90', SERVICE])
    installed = run(['docker', 'exec', CONTAINER, 'cat', '/opt/homecompute/wyoming-openai-stt.py'])
    if installed != (ROOT / SCRIPT).read_text():
        raise ValueError('Adapter installed source mismatch')
    print(json.dumps({'adapter': 'whisper', 'revision': revision}))


def ha(apply=False):
    from openclaw_ha_websocket import HomeAssistantSocket
    token = Path('/var/lib/homecompute-openclaw/config/household-ha-token').read_text().strip()
    allowed = {}
    pipeline_ids = set()
    class Maintenance(HomeAssistantSocket):
        @staticmethod
        def _validate(c):
            kind = c.get('type')
            if c in ({'type':'assist_pipeline/pipeline/list'}, {'type':'wyoming/info'},
                     {'type':'config_entries/get','domain':'wyoming'}):return False
            if kind == 'config/entity_registry/get_entries' and c.get('entity_ids') == [OLD,NEW,TTS]:return False
            if kind == 'config_entries/update' and c.get('entry_id') in allowed and c.get('title') == allowed[c['entry_id']]:return True
            if kind == 'config/entity_registry/update':
                if c == {'type':kind,'entity_id':OLD,'new_entity_id':NEW,'name':'Whisper'}:return True
                if c == {'type':kind,'entity_id':NEW,'name':'Whisper'}:return True
                if c == {'type':kind,'entity_id':TTS,'name':'OmniVoice'}:return True
            if (kind == 'assist_pipeline/pipeline/update' and c.get('pipeline_id') in pipeline_ids
                    and c.get('stt_engine') == NEW and c.get('tts_engine') == TTS
                    and c.get('stt_language') == 'da' and c.get('tts_voice') == 'danish-default'):return True
            raise ValueError('Unreviewed HA operation')
    def call(command):
        with Maintenance(token) as ws:return ws.call(command)
    def registry():return call({'type':'config/entity_registry/get_entries','entity_ids':[OLD,NEW,TTS]})
    rows = registry()
    stt = rows[OLD] or rows[NEW]
    if not stt or not rows[TTS] or rows[OLD] and rows[NEW]:
        raise ValueError('Missing or colliding primary speech registry entries')
    stt_id, tts_id = stt['config_entry_id'], rows[TTS]['config_entry_id']
    if stt['unique_id'] != stt_id+'-stt' or rows[TTS]['unique_id'] != tts_id+'-tts':
        raise ValueError('Unexpected Wyoming identity')
    entries = call({'type':'config_entries/get','domain':'wyoming'})
    by_id = {e['entry_id']:e for e in entries}
    if stt_id not in by_id or tts_id not in by_id:
        raise ValueError('Primary entities must belong to Wyoming')
    allowed.update({stt_id:'Whisper',tts_id:'OmniVoice'})
    before = call({'type':'assist_pipeline/pipeline/list'})
    targets = [p for p in before['pipelines'] if p['stt_engine'] in {OLD,NEW}]
    if not any(p['name'] == 'Home AI' for p in targets):
        raise ValueError('Home AI primary speech selection requires review')
    for row in targets:pipeline_candidate(row);pipeline_ids.add(row['id'])
    if not apply:
        print(json.dumps({'apply':False,'pipelines':len(targets),'stt':NEW,'tts':TTS}));return
    write({'pipelines':before,'entries':entries,'registry':rows}, 'ha-before')
    request = urllib.request.Request('http://192.168.30.30/api/config/config_entries/entry/'+stt_id+'/reload',
        data=b'{}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=30) as response:
        if json.load(response).get('require_restart'):raise ValueError('HA reload requires restart')
    info = call({'type':'wyoming/info'})['info']
    if (info[stt_id]['asr'][0]['name'] != 'Whisper'
            or info[tts_id]['tts'][0]['name'].lower() != 'omnivoice'):
        raise ValueError('Actual Wyoming metadata does not identify Whisper/OmniVoice')
    for identity,title in allowed.items():
        if by_id[identity]['title'] != title:
            call({'type':'config_entries/update','entry_id':identity,'title':title})
    if rows[OLD]:call({'type':'config/entity_registry/update','entity_id':OLD,'new_entity_id':NEW,'name':'Whisper'})
    else:call({'type':'config/entity_registry/update','entity_id':NEW,'name':'Whisper'})
    call({'type':'config/entity_registry/update','entity_id':TTS,'name':'OmniVoice'})
    for identity in sorted(pipeline_ids):
        fresh = call({'type':'assist_pipeline/pipeline/list'})
        row = next(p for p in fresh['pipelines'] if p['id']==identity)
        expected = pipeline_candidate(row)
        if row != expected:
            command={k:v for k,v in expected.items() if k!='id'}
            call({**command,'type':'assist_pipeline/pipeline/update','pipeline_id':identity})
        after=call({'type':'assist_pipeline/pipeline/list'})
        if next(p for p in after['pipelines'] if p['id']==identity)!=expected or after['preferred_pipeline']!=fresh['preferred_pipeline']:
            raise ValueError('Pipeline readback mismatch')
    final=registry()
    if final[OLD] or final[NEW]['unique_id']!=stt['unique_id'] or final[NEW]['name']!='Whisper' or final[TTS]['name']!='OmniVoice':
        raise ValueError('Registry readback mismatch')
    entries=call({'type':'config_entries/get','domain':'wyoming'})
    if any(e['title']!=allowed[e['entry_id']] for e in entries if e['entry_id'] in allowed):
        raise ValueError('Integration title readback mismatch')
    write({'revision':ROOT.name,'stt':NEW,'tts':TTS,'pipelines':sorted(pipeline_ids)}, 'verified')
    print(json.dumps({'apply':True,'pipelines':len(targets),'stt':NEW,'tts':TTS,'identity_preserved':True}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('adapter','plan','apply'))
    parser.add_argument('--revision',required=True)
    args=parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname()!='home-core':raise SystemExit('Run as root on home-core')
    published(args.revision)
    with open('/var/lib/homecompute/deploy.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.action=='adapter':adapter(args.revision)
        else:ha(args.action=='apply')
