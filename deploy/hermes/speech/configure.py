"""Configure only native speech settings, retaining a private rollback preimage."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path('/sandbox/.local/share/homecompute/speech')
CONFIG = Path('/sandbox/.hermes/config.yaml')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data, mode=0o600):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('real private path required')
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    with temporary.open('xb') as stream:
        os.chmod(temporary, mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def candidate(before):
    after = copy.deepcopy(before)
    client = '/opt/hermes/.venv/bin/python ' + str(ROOT / 'client.py')
    after['stt'] = {'enabled': True, 'provider': 'homecompute-whisper', 'streaming': False,
        'providers': {'homecompute-whisper': {'type': 'command', 'timeout': 120,
            'env_passthrough': ['HOMECOMPUTE_HERMES_MCP_TOKEN', 'SSL_CERT_FILE'],
            'command': client + ' stt {input_path} {output_path}', 'output_format': 'txt'}}}
    after['tts'] = {'provider': 'homecompute-omnivoice',
        'providers': {'homecompute-omnivoice': {'type': 'command', 'timeout': 120,
            'env_passthrough': ['HOMECOMPUTE_HERMES_MCP_TOKEN', 'SSL_CERT_FILE'],
            'command': client + ' tts {input_path} {output_path} --voice {voice}',
            'voice': 'auto', 'output_format': 'wav', 'max_text_length': 2000}}}
    after['voice'] = {**before.get('voice', {}), 'auto_tts': True, 'client_direct': False,
        'voice_chat_mode': 'chained', 'silence_duration': 1.5, 'barge_in': True,
        'stop_phrases': ['stop', 'stop samtalen', 'goodbye hermes']}
    after['webapp'] = {'enabled': True, 'host': '127.0.0.1', 'port': 18796,
                       'public_url': 'https://agents.home.arpa/hermes'}
    headers = after.get('mcp_servers', {}).get('homecompute', {}).get('headers', {})
    current = headers.get('Authorization', '')
    if current.startswith('Bearer openshell:resolve:'):
        if 'HOMECOMPUTE_HERMES_MCP_TOKEN' not in current:
            raise ValueError('unexpected managed MCP credential reference')
        headers['Authorization'] = 'Bearer ${HOMECOMPUTE_HERMES_MCP_TOKEN}'
    telemetry = after.setdefault('telemetry', {})
    telemetry['shared_metrics'] = {**telemetry.get('shared_metrics', {}),
                                   'enabled': False, 'send': False, 'offer_version': 2}
    return after


def encode(config):
    import yaml
    class Dumper(yaml.SafeDumper):
        def increase_indent(self, flow=False, indentless=False):
            return super().increase_indent(flow, False)
    return yaml.dump(config, Dumper=Dumper, sort_keys=False).encode()


def privacy_upgrade(raw, record, original):
    """Accept only the exact prior source or its explicit No-thanks UI choice."""
    import yaml
    if sha(original) != record['config_before']:
        raise ValueError('verified original speech preimage required')
    preimage = yaml.safe_load(original)
    previous = candidate(preimage)
    if 'telemetry' in preimage:
        previous['telemetry'] = copy.deepcopy(preimage['telemetry'])
    else:
        previous.pop('telemetry', None)
    if sha(encode(previous)) != record['config_after']:
        raise ValueError('receipt is not the exact prior source configuration')
    selected = candidate(previous)
    if yaml.safe_load(raw) not in (previous, selected):
        raise ValueError('configuration changes extend beyond the source privacy choice')
    return encode(selected)


def configure(request):
    import yaml
    sys.path.insert(0, '/opt/hermes')
    from hermes_cli.config import get_env_value, save_env_value, remove_env_value
    if not (get_env_value('HOMECOMPUTE_HERMES_MCP_TOKEN') or '').startswith('openshell:'):
        raise ValueError('existing opaque owner credential required')
    pin = request['pin']
    raw = CONFIG.read_bytes()
    before = yaml.safe_load(raw)
    if before['model']['default'] != 'automation-moe' or not before.get('dashboard', {}).get('basic_auth'):
        raise ValueError('authenticated qualified owner required')
    receipt = ROOT / 'receipt.json'
    if receipt.exists():
        record = json.loads(receipt.read_bytes())
        if not request.get('rollback') and (record['config_after'] != sha(raw) or candidate(before) != before):
            encoded = privacy_upgrade(raw, record, (ROOT / 'config-before.yaml').read_bytes())
            for asset in pin['assets']:
                if sha((ROOT / asset['name']).read_bytes()) != asset['sha256']:
                    raise ValueError('installed speech dependency differs')
            write(ROOT / 'config-before-privacy.yaml', raw)
            write(ROOT / 'receipt-before-privacy.json', receipt.read_bytes())
            write(CONFIG, encoded, 0o640)
            record.update(config_after=sha(encoded), privacy_revision=request['revision'])
            write(receipt, json.dumps(record).encode())
            return {'privacy_source_reconciled': True, 'speech_settings_preserved': True}
        if record['config_after'] != sha(raw):
            raise ValueError('speech settings changed; preserve for review')
        if request.get('rollback'):
            original = (ROOT / 'config-before.yaml').read_bytes()
            if sha(original) != record['config_before']:
                raise ValueError('verified speech preimage required')
            write(CONFIG, original, 0o640)
            if record['webapp_dist_before'] is None:
                remove_env_value('HERMES_WEBAPP_DIST')
            else:
                save_env_value('HERMES_WEBAPP_DIST', record['webapp_dist_before'])
            record['rolled_back'] = True
            write(receipt, json.dumps(record).encode())
            return {'rolled_back': True}
        if candidate(before) != before:
            raise ValueError('source speech settings differ from installed settings')
        return {'already_current': True}
    if request.get('rollback') or request.get('reconcile_privacy_only') or any(before.get(k) for k in ('stt', 'tts')):
        raise ValueError('unknown existing speech settings require review')
    for asset in pin['assets']:
        data = (ROOT / asset['name']).read_bytes()
        if sha(data) != asset['sha256']:
            raise ValueError('speech dependency digest mismatch')
        if asset['name'] == 'hermes-webapp.tar.gz':
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                for member in archive.getmembers():
                    relative = Path(member.name)
                    if relative.is_absolute() or '..' in relative.parts or member.issym() or member.islnk():
                        raise ValueError('regular frontend files required')
                    if member.isfile():
                        write(ROOT / 'webapp' / relative, archive.extractfile(member).read())
            if not (ROOT / 'webapp/index.html').is_file():
                raise ValueError('built native Webapp required')
        elif asset['name'].endswith('.whl'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                write(ROOT / 'lib/six.py', archive.read('six.py'))
        else:
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                for member in archive.getmembers():
                    relative = Path(*Path(member.name).parts[1:])
                    parts = relative.parts
                    if (member.isfile() and len(parts) >= 2 and parts[0] == 'langdetect'
                            and '..' not in parts and not relative.is_absolute()
                            and ('tests' not in parts) and (relative.suffix in {'.py', '.properties'}
                                or str(relative) in {'langdetect/profiles/da', 'langdetect/profiles/en'})):
                        write(ROOT / 'lib' / relative, archive.extractfile(member).read())
    after = candidate(before)
    # Use the same list indentation as the OMH-owned configuration writer.
    encoded = encode(after)
    if yaml.safe_load(encoded) != after or CONFIG.read_bytes() != raw:
        raise ValueError('configuration changed during speech preparation')
    write(ROOT / 'config-before.yaml', raw)
    dist_before = get_env_value('HERMES_WEBAPP_DIST')
    if dist_before not in (None, str(ROOT / 'webapp')):
        raise ValueError('unknown existing Webapp bundle requires review')
    save_env_value('HERMES_WEBAPP_DIST', str(ROOT / 'webapp'))
    write(CONFIG, encoded, 0o640)
    record = {'config_before': sha(raw), 'config_after': sha(encoded), 'revision': request['revision'],
              'webapp_dist_before': dist_before, 'local_only': True,
              'languages': ['da', 'en'], 'automatic_voice_selection': True}
    write(receipt, json.dumps(record).encode())
    return {k: v for k, v in record.items() if k not in {'config_before', 'config_after'}}


if __name__ == '__main__':
    try:
        print(json.dumps(configure(json.load(sys.stdin))))
    except Exception as error:
        # Keep private diagnostic traces local to the protected installation.
        import traceback
        with (ROOT / 'failure.log').open('a') as log:
            os.chmod(ROOT / 'failure.log', 0o600)
            traceback.print_exc(file=log)
        raise SystemExit('Hermes speech configuration held: ' + type(error).__name__) from None
