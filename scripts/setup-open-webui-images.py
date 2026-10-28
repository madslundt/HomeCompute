#!/usr/bin/env python3
"""Install private image chat acknowledgements and configure native Open WebUI images."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
import uuid

CONTAINER = 'homecompute-open-webui-open-webui-1'
ROOT = Path('/opt/homecompute/image-chat')
STATE = Path('/var/lib/homecompute-image-chat')
UNITS = Path('/etc/systemd/system.attached')
BIN = '/run/current-system/sw/bin'
CONNECTION = 'http://homecompute-control-plane-caddy-1:8193/v1'
COMFY = 'http://homecompute-control-plane-caddy-1:8188'
ALIAS = 'homecompute-images'
MODEL = ALIAS
LEGACY_MODEL = 'image-studio'
DESCRIPTION = 'Upload an image and describe a change, or type a prompt to create one. Start image mode on the Image studio card first.'
SUGGESTIONS = [
    {'title': ['Create an image', 'Watercolor fox'],
     'content': 'Create a watercolor fox reading beneath an autumn tree.'},
    {'title': ['Upload a photo first', 'Make it watercolor'],
     'content': 'Make the uploaded photo look like a watercolor painting.'},
    {'title': ['Upload a photo first', 'Sunny beach background'],
     'content': "Change the uploaded photo's background to a sunny beach."},
]
DEFAULT_PROFILE = 'qwen21-quality'
PROFILES = {DEFAULT_PROFILE: {'workflow_prefix': 'qwen-image-21', 'size': '2048x2048', 'steps': 40}}


def model_matches(actual: dict, expected: dict) -> bool:
    """Compare configuration and exact permission meaning, not server grant IDs/timestamps."""
    def contains(value, wanted):
        return (all(key in value and contains(value[key], child) for key, child in wanted.items())
                if isinstance(wanted, dict) and isinstance(value, dict) else value == wanted)
    def grants(model):
        return {tuple(grant.get(key) for key in ('principal_type', 'principal_id', 'permission'))
                for grant in model.get('access_grants', [])}
    return (contains({key: value for key, value in actual.items() if key != 'access_grants'},
                     {key: value for key, value in expected.items() if key != 'access_grants'})
            and grants(actual) == grants(expected))

# Runs inside the existing container. Session material stays inside its memory
# and the caller's private backup/token files, never argv or service logs.
REMOTE = '''import json,sqlite3,time,uuid,urllib.request,urllib.error,jwt
from pathlib import Path
request_data=json.loads(REQUEST_JSON)
db=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True)
admins=db.execute("SELECT id FROM user WHERE role='admin' ORDER BY created_at,id").fetchall()
if len(admins)!=1: raise RuntimeError('One existing administrator required')
secret=Path('/run/secrets/webui_secret_key').read_text().strip()
def token(seconds):
 now=int(time.time())
 return jwt.encode({'id':admins[0][0],'iat':now,'exp':now+seconds,'jti':str(uuid.uuid4())},secret,algorithm='HS256')
authorization='Bearer '+token(180)
def api(method,path,body=None,missing=False):
 req=urllib.request.Request('http://127.0.0.1:8080'+path,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':authorization,'Content-Type':'application/json'},method=method)
 try:
  with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
 except urllib.error.HTTPError as error:
  if missing and error.code in (400,404):return None
  raise RuntimeError('Open WebUI administrative API refused the request') from None
def state():return {'images':api('GET','/api/v1/images/config'),'openai':api('GET','/openai/config'),'tasks':api('GET','/api/v1/tasks/config'),'alias':api('GET','/api/v1/models/model?id=homecompute-images',missing=True)}
def model_access():return {**{key:api('GET','/api/v1/models/model?id='+name,missing=True) for key,name in [('alias','homecompute-images'),('base','image-studio')]},'openai':api('GET','/openai/config')}
action=request_data['action']
if action=='read':print(json.dumps(state()))
elif action=='comfy-info':
 result={}
 for name in request_data['classes']:
  with urllib.request.urlopen('http://homecompute-control-plane-caddy-1:8188/object_info/'+name,timeout=20) as response:result.update(json.load(response))
 print(json.dumps(result))
elif action=='qa-token':print(json.dumps({'token':token(1200)}))
elif action=='model-read':print(json.dumps(api('GET','/api/v1/models/model?id=homecompute-images')))
elif action=='model-access-read':print(json.dumps(model_access()))
elif action=='model-access-apply':
 import hashlib
 digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 current=model_access()
 if digest(current)!=request_data['expected']:raise RuntimeError('Image model access changed')
 candidate=request_data['candidate']
 if current['base'] and not current['base'].get('meta',{}).get('homecompute_image_studio'):raise RuntimeError('Refusing unmanaged legacy override')
 api('POST','/openai/config/update',candidate['openai'])
 api('POST','/api/v1/models/model/update',candidate['alias'])
 models=api('GET','/api/models?refresh=true')['data']
 if len([m for m in models if m['id']=='homecompute-images'])!=1:raise RuntimeError('Canonical image model not available')
 if current['base']:
  if digest(api('GET','/api/v1/models/model?id=image-studio'))!=digest(current['base']):raise RuntimeError('Legacy override changed')
  api('POST','/api/v1/models/model/delete',{'id':'image-studio'})
 observed=model_access()
 if observed['base'] or observed['alias']['base_model_id'] is not None:raise RuntimeError('Legacy base chain remains')
 model=observed['alias']
 if not any(g.get('principal_type')=='user' and g.get('principal_id')=='*' and g.get('permission')=='read' for g in model['access_grants']):raise RuntimeError('Signed-in model read grant missing')
 print(json.dumps({'models_shared_read_only':['homecompute-images'],'legacy_override_removed':current['base'] is not None,'authentication_changed':False}))
elif action=='model-meta':
 import hashlib
 current=api('GET','/api/v1/models/model?id=homecompute-images')
 if not current.get('meta',{}).get('homecompute_image_studio'):raise RuntimeError('Managed image alias required')
 digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 if digest(current)!=request_data['expected']:raise RuntimeError('Image alias changed')
 patch=request_data['patch']
 if set(patch)!={'description','suggestion_prompts'}:raise RuntimeError('Only image alias presentation metadata can change')
 candidate={k:v for k,v in current.items() if k in {'id','base_model_id','name','meta','params','access_grants','is_active'}}
 candidate['meta'].update(patch)
 result=api('POST','/api/v1/models/model/update',candidate)
 if any(result['meta'].get(k)!=v for k,v in patch.items()):raise RuntimeError('Image metadata verification failed')
 print(json.dumps({'model_metadata_updated':True,'alias':'homecompute-images','suggestion_count':len(patch['suggestion_prompts'])}))
elif action=='apply':
 import hashlib
 current=state()
 digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 if digest(current)!=request_data['expected']:raise RuntimeError('Administrative configuration changed')
 candidate=request_data['candidate']
 api('POST','/api/v1/images/config/update',candidate['images'])
 api('POST','/openai/config/update',candidate['openai'])
 api('POST','/api/v1/models/model/update' if current['alias'] else '/api/v1/models/create',candidate['alias'])
 observed=state()
 if observed['images']!=candidate['images'] or observed['openai']!=candidate['openai']:raise RuntimeError('Image configuration verification failed')
 if not model_matches(observed['alias'],candidate['alias']):raise RuntimeError('Image model verification failed')
 print(json.dumps({'configured':True,'alias':'homecompute-images'}))
else:raise RuntimeError('Unknown administrative operation')
'''


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def remote(action: str, **values) -> dict:
    source = 'REQUEST_JSON=' + repr(json.dumps({'action': action, **values})) + '\n' + inspect.getsource(model_matches) + '\n' + REMOTE
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'python', '-'], input=source,
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Private Open WebUI administrative operation failed; no credentials were logged')
    return json.loads(result.stdout)


def build_candidate(before: dict, workflows: Path, profile: str = DEFAULT_PROFILE) -> dict:
    if profile not in PROFILES:
        raise ValueError('Unknown reviewed image quality profile')
    selected = PROFILES[profile]
    if before['tasks'].get('TASK_MODEL_EXTERNAL') not in {None, '', MODEL, LEGACY_MODEL}:
        raise ValueError('A global external task override would route image-chat tasks to a text model')
    candidate = deepcopy(before)
    images = candidate['images']
    prefix = selected['workflow_prefix']
    edit = json.loads((workflows / (prefix + '-edit-api.json')).read_text())
    create = json.loads((workflows / (prefix + '-text-api.json')).read_text())
    create_nodes = json.loads((workflows / (prefix + '-text-nodes.json')).read_text())
    edit_nodes = json.loads((workflows / (prefix + '-edit-nodes.json')).read_text())
    if ({n['type'] for n in edit_nodes} != {'model', 'prompt', 'image', 'seed'} or
            create['11']['class_type'] != 'SaveImage' or edit['11']['class_type'] != 'SaveImage'):
        raise ValueError('Reviewed native image workflow mappings required')
    images.update({'ENABLE_IMAGE_GENERATION': True, 'ENABLE_IMAGE_PROMPT_GENERATION': False,
                   'IMAGE_GENERATION_ENGINE': 'comfyui', 'IMAGE_GENERATION_MODEL': create['1']['inputs']['unet_name'],
                   'IMAGE_SIZE': selected['size'], 'IMAGE_STEPS': selected['steps'], 'COMFYUI_BASE_URL': COMFY, 'COMFYUI_API_KEY': '',
                   'COMFYUI_WORKFLOW': json.dumps(create), 'COMFYUI_WORKFLOW_NODES': create_nodes,
                   'ENABLE_IMAGE_EDIT': True, 'IMAGE_EDIT_ENGINE': 'comfyui',
                   'IMAGE_EDIT_MODEL': edit['1']['inputs']['unet_name'], 'IMAGE_EDIT_SIZE': '',
                   'IMAGES_EDIT_COMFYUI_BASE_URL': COMFY, 'IMAGES_EDIT_COMFYUI_API_KEY': '',
                   'IMAGES_EDIT_COMFYUI_WORKFLOW': json.dumps(edit), 'IMAGES_EDIT_COMFYUI_WORKFLOW_NODES': edit_nodes})
    connection = candidate['openai']
    urls = connection['OPENAI_API_BASE_URLS']
    if len(urls) != len(connection['OPENAI_API_KEYS']) or urls.count(CONNECTION) > 1:
        raise ValueError('Existing provider configuration requires reconciliation')
    if CONNECTION not in urls:
        urls.append(CONNECTION)
        connection['OPENAI_API_KEYS'].append('')
    index = str(urls.index(CONNECTION))
    connection['OPENAI_API_CONFIGS'].setdefault(index, {}).update(
        {'enable': True, 'model_ids': [MODEL], 'connection_type': 'external', 'auth_type': 'none'})
    original_alias = before.get('alias')
    if original_alias and not original_alias.get('meta', {}).get('homecompute_image_studio'):
        raise ValueError('Existing model alias is not owned by this image setup')
    alias = {key: deepcopy(value) for key, value in (original_alias or {}).items()
             if key in {'id', 'base_model_id', 'name', 'meta', 'params', 'access_grants', 'is_active'}}
    alias.update({'id': ALIAS, 'base_model_id': None, 'name': 'Images', 'is_active': True})
    alias.setdefault('params', {})['function_calling'] = 'legacy'
    meta = alias.setdefault('meta', {})
    meta.update({'homecompute_image_studio': True, 'description': DESCRIPTION,
                 'suggestion_prompts': deepcopy(SUGGESTIONS),
                 'defaultFeatureIds': ['image_generation'],
                 'capabilities': {'vision': True, 'image_generation': True, 'builtin_tools': False,
                                  'web_search': False, 'code_interpreter': False, 'file_context': False}})
    alias['access_grants'] = signed_in_read_grants(alias.get('access_grants', []))
    candidate['alias'] = alias
    return candidate


def native_workflows(candidate: dict) -> list[dict]:
    return [json.loads(candidate['images'][key]) for key in
            ('COMFYUI_WORKFLOW', 'IMAGES_EDIT_COMFYUI_WORKFLOW')]


def validate_native_workflows(workflows: list[dict], info: dict) -> None:
    """Check live native schemas/assets without submitting a prompt or loading weights."""
    for workflow in workflows:
        visiting, complete = set(), set()
        def visit(identifier: str) -> None:
            if identifier in visiting:
                raise ValueError('Image workflow contains a cycle')
            if identifier in complete:
                return
            visiting.add(identifier)
            node = workflow[identifier]
            schema = info.get(node['class_type'])
            if not schema or schema.get('api_node'):
                raise ValueError('Required local ComfyUI node is not available: ' + node['class_type'])
            inputs = node['inputs']
            definitions = {**schema['input'].get('required', {}), **schema['input'].get('optional', {})}
            for key, definition in list(definitions.items()):
                if definition[0] == 'COMFY_AUTOGROW_V3':
                    template = definition[1]['template']
                    del definitions[key]
                    for name in template['names']:
                        definitions[key + '.' + name] = next(iter(template['input']['required'].values()))
                elif key in schema['input'].get('required', {}) and key not in inputs:
                    raise ValueError('Required image workflow input missing: ' + key)
            for key, value in inputs.items():
                if key not in definitions:
                    raise ValueError('Unknown image workflow input: ' + key)
                kind = definitions[key][0]
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and type(value[1]) is int:
                    origin, output = value
                    if origin not in workflow:
                        raise ValueError('Image workflow link points to a missing node')
                    outputs = info.get(workflow[origin]['class_type'], {}).get('output', [])
                    if output < 0 or output >= len(outputs) or outputs[output] != kind:
                        raise ValueError('Image workflow link has an incompatible output')
                    visit(origin)
                elif isinstance(kind, list):
                    if value not in kind:
                        raise ValueError('Required image asset or option is not installed: ' + str(value))
                elif kind == 'INT' and type(value) is not int or kind == 'FLOAT' and type(value) not in (int, float):
                    raise ValueError('Invalid numeric image workflow input: ' + key)
                elif kind == 'STRING' and not isinstance(value, str):
                    raise ValueError('Invalid text image workflow input: ' + key)
            visiting.remove(identifier)
            complete.add(identifier)
        for identifier in workflow:
            visit(identifier)


def signed_in_read_grants(grants: list) -> list:
    result = [{key: deepcopy(grant[key]) for key in ('principal_type', 'principal_id', 'permission')}
              for grant in grants]
    if not any(g.get('principal_type') == 'user' and g.get('principal_id') == '*' and
               g.get('permission') == 'read' for g in result):
        result.append({'principal_type': 'user', 'principal_id': '*', 'permission': 'read'})
    return result


def build_access_candidate(before: dict) -> dict:
    current = before['alias']
    if not current or not current.get('meta', {}).get('homecompute_image_studio'):
        raise ValueError('Existing owned image profile required')
    legacy = before.get('base')
    if legacy and not legacy.get('meta', {}).get('homecompute_image_studio'):
        raise ValueError('Refusing to change an unmanaged legacy model')
    form = {k: deepcopy(v) for k, v in current.items()
            if k in {'id', 'base_model_id', 'name', 'meta', 'params', 'access_grants', 'is_active'}}
    if not form['is_active']:
        raise ValueError('Image model must already be enabled')
    form['base_model_id'] = None
    form['access_grants'] = signed_in_read_grants(form.get('access_grants', []))
    connection = deepcopy(before['openai'])
    urls = connection['OPENAI_API_BASE_URLS']
    if urls.count(CONNECTION) != 1:
        raise ValueError('Exactly one existing image connection required')
    config = connection['OPENAI_API_CONFIGS'][str(urls.index(CONNECTION))]
    if set(config.get('model_ids', [])) not in ({LEGACY_MODEL}, {MODEL}):
        raise ValueError('Unexpected image connection model registry')
    config['model_ids'] = [MODEL]
    return {'alias': form, 'openai': connection}


def share_models() -> dict:
    before = remote('model-access-read')
    candidate = build_access_candidate(before)
    backup = STATE / 'access-backups' / uuid.uuid4().hex
    backup.mkdir(mode=0o700, parents=True)
    write(backup / 'models.before.private.json', json.dumps(before).encode())
    return remote('model-access-apply', expected=digest(before), candidate=candidate)


def write(path: Path, data: bytes, mode: int = 0o600) -> None:
    if path.is_symlink() or (path.exists() and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
        raise ValueError('Untrusted installed path')
    temporary = path.with_name(path.name + '.install-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def install_shim(source: Path) -> None:
    ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
    UNITS.mkdir(mode=0o755, parents=True, exist_ok=True)
    write(ROOT / 'image_chat_shim.py', (source / 'scripts/image_chat_shim.py').read_bytes(), 0o444)
    unit = f'''[Unit]
Description=Private deterministic acknowledgements for image chat
After=docker.service homecompute-comfyui-firewall.service
Requires=homecompute-comfyui-firewall.service
StartLimitIntervalSec=0
[Service]
User=nobody
ExecStart={BIN}/python3 {ROOT}/image_chat_shim.py
Environment=PYTHONDONTWRITEBYTECODE=1
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_UNIX
MemoryMax=256M
TasksMax=32
[Install]
WantedBy=multi-user.target
'''
    target = UNITS / 'homecompute-image-chat.service'
    write(target, unit.encode(), 0o644)
    wants = UNITS / 'multi-user.target.wants'
    wants.mkdir(mode=0o755, exist_ok=True)
    link = wants / target.name
    if link.is_symlink() and link.resolve() != target:
        raise ValueError('Unmanaged boot link')
    if not link.exists():
        link.symlink_to(target)
    subprocess.run(['systemd-analyze', 'verify', str(target)], check=True, capture_output=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'restart', target.name], check=True)


def ensure_shim(source: Path) -> bool:
    installed = ROOT / 'image_chat_shim.py'
    unit = UNITS / 'homecompute-image-chat.service'
    if installed.is_file() and unit.is_file() and installed.read_bytes() == (source / 'scripts/image_chat_shim.py').read_bytes():
        return False
    install_shim(source)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--profile', choices=sorted(PROFILES), default=DEFAULT_PROFILE)
    parser.add_argument('--validate-comfy', action='store_true', help='Read live native node/asset schemas without running image inference')
    parser.add_argument('--install-shim-only', action='store_true', help='Refresh the service without changing persistent user settings')
    parser.add_argument('--refresh-model-metadata', action='store_true', help='Update only the image alias description and suggestions')
    parser.add_argument('--share-models', action='store_true', help='Use one canonical image model shared read-only with signed-in users')
    parser.add_argument('--qa-token', action='store_true', help='Write an existing-admin token privately for 20-minute browser QA')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        raise ValueError('Run as root on home-core')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (STATE, ROOT.parent, UNITS):
        if directory.is_symlink() or directory.stat().st_uid != 0 or directory.stat().st_mode & 0o022:
            raise ValueError('Root-owned managed directories required')
    if args.install_shim_only:
        install_shim(args.source)
        print(json.dumps({'service_installed': True, 'user_configuration_changed': False}))
        return
    if args.refresh_model_metadata:
        before = remote('model-read')
        backup = STATE / 'metadata-backups' / uuid.uuid4().hex
        backup.mkdir(mode=0o700, parents=True)
        write(backup / 'model.before.private.json', json.dumps(before).encode())
        print(json.dumps(remote('model-meta', expected=digest(before),
                                patch={'description': DESCRIPTION, 'suggestion_prompts': SUGGESTIONS})))
        return
    if args.share_models:
        print(json.dumps(share_models()))
        return
    if args.qa_token:
        token = remote('qa-token')['token']
        write(STATE / 'qa-token.private', token.encode())
        print(json.dumps({'qa_token_file': str(STATE / 'qa-token.private'), 'expires_in_seconds': 1200}))
        return
    before = remote('read')
    candidate = build_candidate(before, args.source / 'deploy/comfyui-control/workflows', args.profile)
    if args.apply or args.validate_comfy:
        workflows = native_workflows(candidate)
        classes = sorted({node['class_type'] for workflow in workflows for node in workflow.values()})
        validate_native_workflows(workflows, remote('comfy-info', classes=classes))
    summary = {'alias': ALIAS, 'connection': CONNECTION, 'comfyui': COMFY,
               'profile': args.profile, 'size': PROFILES[args.profile]['size'], 'steps': PROFILES[args.profile]['steps'],
               'image_generation': True, 'image_edit': True, 'prompt_rewrite': False, 'applied': False}
    if args.apply:
        backup = STATE / 'config-backups' / uuid.uuid4().hex
        backup.mkdir(mode=0o700, parents=True)
        write(backup / 'before.private.json', json.dumps(before).encode())
        write(backup / 'candidate.private.json', json.dumps(candidate).encode())
        shim_restarted = ensure_shim(args.source)
        remote('apply', expected=digest(before), candidate=candidate)
        share_models()
        summary.update(applied=True, backup=str(backup), shim_restarted=shim_restarted)
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
