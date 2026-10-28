#!/usr/bin/env python3
"""Plan or install the dedicated authenticated Open WebUI video chat Pipe."""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import uuid

CONTAINER = 'homecompute-open-webui-open-webui-1'
STATE = Path('/var/lib/homecompute-video-chat')
PIPE_ID = 'homecompute_videos'
MARKER = 'homecompute_video_studio'
CONTENT_HASH = 'homecompute_content_sha256'
NAME = 'Videos · LTX-2.5'
DESCRIPTION = 'Describe a scene, or upload a starting picture and describe its motion, to create a short video with sound. Video mode starts after active work finishes. Use ComfyUI for advanced settings.'
SOURCE = 'deploy/open-webui/functions/homecompute_videos.py'
CAPABILITIES = {'vision': True, **{key: False for key in ('image_generation', 'builtin_tools', 'web_search', 'code_interpreter', 'file_context')}}
SUGGESTIONS = [{'title': ['Create a short video', 'Fox in the snow'],
                'content': 'A red fox walks through fresh snow in warm morning light, with soft footsteps and wind.'},
               {'title': ['Upload a starting photo first', 'Animate the photo'],
                'content': 'Animate the uploaded photo with a gentle camera move and subtle natural motion, preserving the subject and scene.'}]



def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def function_form(record: dict) -> dict:
    return {key: deepcopy(record[key]) for key in ('id', 'name', 'content', 'meta')}


def owned(record: dict) -> bool:
    meta = record.get('meta') or {}
    return (record.get('id') == PIPE_ID and record.get('type') == 'pipe'
            and meta.get(MARKER) is True and isinstance(record.get('content'), str)
            and meta.get(CONTENT_HASH) == content_hash(record['content']))


def function_matches(record: dict | None, candidate: dict) -> bool:
    if not record:
        return False
    actual, expected = function_form(record), deepcopy(candidate)
    # Open WebUI derives the manifest from source frontmatter on publication.
    actual['meta'].pop('manifest', None)
    expected['meta'].pop('manifest', None)
    return actual == expected


def candidate(before: dict, content: str) -> dict:
    tree = ast.parse(content, filename=SOURCE)
    compile(tree, SOURCE, 'exec')
    pipes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Pipe']
    methods = {node.name for node in pipes[0].body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))} if len(pipes) == 1 else set()
    if len(pipes) != 1 or 'pipes' in methods or 'pipe' not in methods:
        raise ValueError('A single dedicated Pipe without a multi-model registry is required')
    current = before.get('function')
    if current and not owned(current):
        raise ValueError('Refusing an unmanaged or manually modified video function')
    result = function_form(current) if current else {'id': PIPE_ID, 'name': NAME, 'content': '', 'meta': {}}
    result.update(name=NAME, content=content)
    result['meta'].update({MARKER: True, CONTENT_HASH: content_hash(content), 'description': DESCRIPTION})
    return result


def model_form(record: dict) -> dict:
    return {key: deepcopy(value) for key, value in record.items()
            if key in {'id', 'base_model_id', 'name', 'meta', 'params', 'access_grants', 'is_active'}}


def model_owned(record: dict) -> bool:
    return record.get('id') == PIPE_ID and (record.get('meta') or {}).get(MARKER) is True


def model_matches(record: dict | None, wanted: dict) -> bool:
    def contains(actual, expected):
        return (all(key in actual and contains(actual[key], value) for key, value in expected.items())
                if isinstance(actual, dict) and isinstance(expected, dict) else actual == expected)
    def grants(value):
        return {tuple(grant.get(key) for key in ('principal_type', 'principal_id', 'permission'))
                for grant in value.get('access_grants', [])}
    return bool(record and contains({key: value for key, value in record.items() if key != 'access_grants'},
                                    {key: value for key, value in wanted.items() if key != 'access_grants'})
                and grants(record) == grants(wanted))


def model_candidate(before: dict) -> dict:
    current = before.get('model')
    if current and not model_owned(current):
        raise ValueError('Refusing an unmanaged video Workspace model')
    result = model_form(current) if current else {'id': PIPE_ID, 'meta': {}, 'params': {}}
    result.update(base_model_id=None, name=NAME, is_active=True)
    result['meta'].update({MARKER: True, 'description': DESCRIPTION, 'capabilities': deepcopy(CAPABILITIES),
                           'defaultFeatureIds': [], 'suggestion_prompts': deepcopy(SUGGESTIONS)})
    grants = [{key: grant[key] for key in ('principal_type', 'principal_id', 'permission')}
              for grant in result.get('access_grants', [])]
    signed_in = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
    if signed_in not in grants:
        grants.append(signed_in)
    result['access_grants'] = grants
    return result


def apply_function(before: dict, desired: dict, api, snapshot, desired_model=None) -> dict:
    """Change only the owned Pipe and same-ID presentation; retain protected settings."""
    current, original_model = before['function'], before.get('model')
    desired_model = desired_model or model_candidate(before)
    stage, function_touched, model_touched = 'preflight', False, False
    published_model = None
    if digest(snapshot()) != digest(before):
        return {'ok': False, 'stage': stage, 'rollback': 'not needed'}
    try:
        function_changed = not (function_matches(current, desired) and current.get('is_active') is True)
        model_changed = not model_matches(original_model, desired_model)
        if function_changed:
            stage, function_touched = 'publish', True
            route = f'/api/v1/functions/id/{PIPE_ID}/update' if current else '/api/v1/functions/create'
            api('POST', route, desired)
            observed = snapshot()
            if not function_matches(observed['function'], desired):
                raise ValueError('Function publication verification failed')
            if observed['function'].get('is_active') is not True:
                api('POST', f'/api/v1/functions/id/{PIPE_ID}/toggle')
        if model_changed:
            stage, model_touched = 'model capabilities', True
            api('POST', '/api/v1/models/model/update' if original_model else '/api/v1/models/create', desired_model)
            published_model = snapshot().get('model')
        stage = 'verification'
        observed = snapshot()
        if (not function_matches(observed['function'], desired) or not observed['function'].get('is_active')
                or not model_matches(observed.get('model'), desired_model)
                or observed['preserved'] != before['preserved']
                or current and observed['valves'] != before['valves']):
            raise ValueError('Video function, capabilities or protected configuration verification failed')
        models = api('GET', '/api/models?refresh=true')
        if not isinstance(models, dict) or len([model for model in models.get('data', []) if model.get('id') == PIPE_ID]) != 1:
            raise ValueError('The dedicated video model is not available')
        return {'ok': True, 'changed': function_changed or model_changed, 'model_id': PIPE_ID, 'authenticated_model': True}
    except Exception:
        failures = []
        if model_touched:
            try:
                latest = snapshot().get('model')
                allowed = {digest(record) for record in (original_model, published_model) if record}
                if latest and (not model_owned(latest) or digest(latest) not in allowed):
                    raise ValueError('Workspace model changed during recovery')
                if original_model:
                    api('POST', '/api/v1/models/model/update' if latest else '/api/v1/models/create', model_form(original_model))
                elif latest:
                    api('POST', '/api/v1/models/model/delete', {'id': PIPE_ID})
                restored = snapshot().get('model')
                if (original_model and not model_matches(restored, model_form(original_model))) or (not original_model and restored):
                    raise ValueError('Workspace model recovery failed')
            except Exception:
                failures.append('model')
        if function_touched:
            try:
                latest = snapshot()['function']
                allowed = {content_hash(desired['content'])}
                if current:
                    allowed.add(content_hash(current['content']))
                if latest and (not owned(latest) or content_hash(latest['content']) not in allowed):
                    raise ValueError('Function changed during recovery')
                if current:
                    api('POST', f'/api/v1/functions/id/{PIPE_ID}/update' if latest else '/api/v1/functions/create', function_form(current))
                    restored = snapshot()
                    if restored['valves'] != before['valves']:
                        api('POST', f'/api/v1/functions/id/{PIPE_ID}/valves/update', {'valves': before['valves']})
                    if restored['function'].get('is_active') != current.get('is_active'):
                        api('POST', f'/api/v1/functions/id/{PIPE_ID}/toggle')
                elif latest:
                    api('DELETE', f'/api/v1/functions/id/{PIPE_ID}/delete')
                restored = snapshot()
                if (current and (not function_matches(restored['function'], function_form(current))
                                  or restored['function'].get('is_active') != current.get('is_active')
                                  or restored['valves'] != before['valves'])
                        or not current and restored['function'] is not None):
                    raise ValueError('Function recovery verification failed')
            except Exception:
                failures.append('function')
        rollback = ('failed; private backup retained' if failures else 'complete') if function_touched or model_touched else 'not needed'
        return {'ok': False, 'stage': stage, 'rollback': rollback}


# Admin session material exists only inside the running app container's memory.
# Authenticated /functions/list must succeed before a missing ID is considered absent.
REMOTE = '''import json,sqlite3,time,uuid,urllib.request,urllib.error,jwt
from copy import deepcopy
from pathlib import Path
request_data=json.loads(REQUEST_JSON)
db=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True)
admins=db.execute("SELECT id FROM user WHERE role='admin' ORDER BY created_at,id").fetchall()
if len(admins)!=1:raise RuntimeError('One existing administrator required')
secret=Path('/run/secrets/webui_secret_key').read_text().strip()
now=int(time.time())
authorization='Bearer '+jwt.encode({'id':admins[0][0],'iat':now,'exp':now+300,'jti':str(uuid.uuid4())},secret,algorithm='HS256')
def api(method,path,body=None,missing=False):
 req=urllib.request.Request('http://127.0.0.1:8080'+path,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':authorization,'Content-Type':'application/json'},method=method)
 try:
  with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
 except urllib.error.HTTPError as error:
  if missing and error.code in (400,404):return None
  raise RuntimeError('Open WebUI administrative request failed') from None
def snapshot():
 listing=api('GET','/api/v1/functions/list')
 if not isinstance(listing,list):raise RuntimeError('Unexpected authenticated function registry')
 matches=[row for row in listing if row.get('id')==PIPE_ID]
 if len(matches)>1:raise RuntimeError('Duplicate video function ID')
 record=api('GET','/api/v1/functions/id/'+PIPE_ID) if matches else None
 valves=api('GET','/api/v1/functions/id/'+PIPE_ID+'/valves') if record else None
 protected={name:digest(api('GET',path)) for name,path in [('images','/api/v1/images/config'),('providers','/openai/config'),('tasks','/api/v1/tasks/config')]}
 model=api('GET','/api/v1/models/model?id='+PIPE_ID,missing=True)
 return {'function':record,'valves':valves,'model':model,'preserved':protected}
if request_data['action']=='read':print(json.dumps(snapshot()))
elif request_data['action']=='apply':
 before=snapshot()
 if digest(before)!=request_data['expected']:print(json.dumps({'ok':False,'stage':'preflight','rollback':'not needed'}))
 else:print(json.dumps(apply_function(before,request_data['candidate'],api,snapshot,request_data['model'])))
else:raise RuntimeError('Unknown video administrative operation')
'''


def remote(action: str, **values) -> dict:
    functions = (digest, content_hash, function_form, owned, function_matches, model_form, model_owned, model_matches, model_candidate, apply_function)
    constants = '\n'.join(f'{name}={value!r}' for name, value in
                          [('PIPE_ID', PIPE_ID), ('MARKER', MARKER), ('CONTENT_HASH', CONTENT_HASH), ('NAME', NAME),
                           ('DESCRIPTION', DESCRIPTION), ('CAPABILITIES', CAPABILITIES), ('SUGGESTIONS', SUGGESTIONS)])
    program = ('import hashlib\n' + constants + '\nREQUEST_JSON=' + repr(json.dumps({'action': action, **values}))
               + '\n' + '\n'.join(inspect.getsource(function) for function in functions) + '\n' + REMOTE)
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'python', '-'], input=program,
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Private Open WebUI administrative operation failed; no credentials were logged')
    return json.loads(result.stdout)


def safe(path: Path, *, directory=False) -> None:
    for parent in [*reversed(path.absolute().parents), path]:
        if parent.is_symlink():
            raise ValueError('Symlink deployment paths are not permitted')
        if parent.exists() and (parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022):
            raise ValueError('Root-owned, non-writable deployment paths required')
    if path.exists() and not (path.is_dir() if directory else path.is_file()):
        raise ValueError('Unexpected deployment path type')


def private_backup(before: dict, desired: dict, plan: dict) -> Path:
    safe(STATE, directory=True)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)
    backup = STATE / uuid.uuid4().hex
    backup.mkdir(mode=0o700)
    for name, value in [('function.before.private.json', before), ('candidate.private.json', desired), ('manifest.json', plan)]:
        path = backup / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
    return backup


def run(source: Path, *, apply=False, expected_plan=None) -> dict:
    if platform.system() != 'Linux' or socket.gethostname().split('.')[0] != 'home-core' or os.geteuid() != 0:
        raise ValueError('Run as root on Linux home-core')
    path = source / SOURCE
    if any(parent.is_symlink() for parent in [*path.absolute().parents, path]):
        raise ValueError('The reviewed function source must not use symlinks')
    content = path.read_text()
    before = remote('read')
    desired = candidate(before, content)
    desired_model = model_candidate(before)
    fingerprint = digest({'before': before, 'candidate': desired, 'model': desired_model})
    changed = not (function_matches(before['function'], desired) and before['function'].get('is_active') is True
                   and model_matches(before.get('model'), desired_model))
    plan = {'read_only': not apply, 'plan_sha256': fingerprint, 'source_sha256': content_hash(content),
            'function_id': PIPE_ID, 'model_id': PIPE_ID, 'name': NAME, 'changes_needed': changed,
            'access': 'Same-ID video model shared read-only with signed-in users; no public access changes',
            'preserved': ['image generation and editing', 'provider connections', 'task settings', 'existing function valves'],
            'actions': ['Back up the dedicated function, valves and Workspace model privately',
                        'Create/update and activate only the owned video Pipe and its same-ID capability metadata',
                        'Verify the model appears once and protected settings are unchanged']}
    if not apply:
        return plan
    if not expected_plan or expected_plan != fingerprint:
        raise ValueError('Apply requires the matching --expected-plan from the read-only plan')
    backup = private_backup(before, {'function': desired, 'model': desired_model}, plan) if changed else None
    try:
        result = remote('apply', expected=digest(before), candidate=desired, model=desired_model)
    except Exception:
        raise RuntimeError(f'Private video administrative operation failed; recovery backup: {backup}') from None
    if not result.get('ok'):
        raise RuntimeError(f'Video install failed at {result.get("stage")}; recovery: {result.get("rollback")}; private backup: {backup}')
    return {**result, 'plan_sha256': fingerprint, 'backup': str(backup) if backup else None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-plan')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.source, apply=args.apply, expected_plan=args.expected_plan), indent=2))
    except Exception as error:
        parser.exit(1, f'{error}\n')
