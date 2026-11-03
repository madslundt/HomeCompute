#!/usr/bin/env python3
"""Guarded Large-v3 replacement; preserve stopped STT containers for rollback.

Run only from a clean published release. This lifecycle does not modify TTS,
text engines, recordings, credentials, host release pointers or capacity receipts.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import importlib.util
import json
import os
import platform
from pathlib import Path
import pwd
import re
import secrets
import shlex
import signal
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/homecompute/whisper-stt')
OLD = ('gb10-hviske-http', 'gb10-whisper-http', 'gb10-hviske-primary', 'gb10-whisper-primary')
NEW = ('gb10-whisper-primary', 'gb10-whisper-http')
CANDIDATE = 'gb10-whisper-candidate'
CANDIDATE_IMAGE = 'homecompute/whisper-vllm-candidate:0.19.0-hc1'
CUDA_CANDIDATE = 'gb10-whisper-cpp-primary'
CUDA_CANDIDATE_IMAGE = 'homecompute/whisper-cpp-stt:1.9.1-large-v3-f16-cuda121a-hc3'
CUDA_CANDIDATE_PROJECT = 'whisper-cpp-gb10-cuda'
CPP_QUALIFIED_IMAGE_ID = 'sha256:d6da07a313bc18c336e4ccb9c2680f209d80c079a5fb42e9eb6b522e244448b3'
CPP_QUALIFIED_SOURCE_REVISION = '50ee4f9666c26df89bccab30c83e9cd6dcc0faba'
CPP_GATE_FAILURE_RECOVERY_REVISION = '3f5c3228e4e245b389306926930a8376640b80c9'
CPP_GATE_FAILURE_RECOVERY_ID = 'cec07a2399ea'
CPP_GATE_FAILURE_RELAY_IMAGE = 'sha256:6049300173bcc9505cd9779d214e498a83ffaeffcdc5620c9aad75d2e95f5c3a'
CUDA_BASE_IMAGE = 'nvcr.io/nvidia/pytorch@sha256:8014e4ffc674ae9cc9aecad04b64b29c3d311ed738e54ac3042d2d45690d19e9'
CPP_MODEL_SHA256 = '64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2'
CPP_MODEL_SNAPSHOT = 'c521a4b02f422512d734391fdf08bb08c0862f68'
CPP_MODEL_PATH = Path('/srv/gb10-ai/cache/whisper-cpp/ggml-large-v3.bin')
CPP_API_KEY_PATH = Path('/etc/gb10-ai/secrets/whisper_cpp_api_key')
CPP_ROLE_API_KEY_PATH = Path('/etc/gb10-ai/secrets/whisper_cpp_role_key')
VLLM_API_KEY_PATH = Path('/etc/gb10-ai/secrets/vllm_api_key')
CPP_MODEL_SIZE = 3_095_033_483
CPP_MODEL_URL = ('https://huggingface.co/ggerganov/whisper.cpp/resolve/'
                 'c521a4b02f422512d734391fdf08bb08c0862f68/ggml-large-v3.bin')
CPP_MODEL_FREE_SPACE_RESERVE = 512 * 1024 * 1024
CPP_DOWNLOAD_TIMEOUT = 60
CPP_DOWNLOAD_MAX_SECONDS = 3600
CPP_RUNTIME_COMMIT = 'f049fff95a089aa9969deb009cdd4892b3e74916'
CPP_CUDA_PATCH_VERSION = 'strict-gpu-buffer-idle-proof-v2'
CPP_CUDA_PATCHER_SHA256 = 'd68e0e16aca14d3169501a751b054a7e85def93aa7679bb7f26f209236b9d7ef'
REVISION = '06f233fe06e710322aca913c1bc4249a0d71fce1'
MODEL = 'openai/whisper-large-v3'
LEGACY_REPOSITORIES = ('syvai/hviske-v5.3', 'openai/whisper-large-v3-turbo')
CANDIDATE_STAGES = frozenset({
    'preflight', 'install-intake-gate', 'drain-primary', 'stop-primary',
    'pre-load-memory-check', 'candidate-start', 'candidate-health-check',
    'synthetic-relay-smoke', 'post-load-memory-check', 'release-intake-gate',
    'candidate-running', 'candidate-rollback', 'gate-cleanup',
})
CANDIDATE_ROLLBACK_STAGES = frozenset({
    'inspect-candidate', 'disable-candidate-restart', 'stop-candidate',
    'remove-candidate', 'inspect-primary', 'restore-primary-restart',
    'start-primary', 'wait-primary-health', 'complete',
})
CUDA_CANDIDATE_STAGES = frozenset({
    'preflight', 'install-intake-gate', 'drain-primary', 'stop-primary',
    'pre-load-memory-check', 'candidate-start', 'candidate-health-check',
    'synthetic-candidate-smoke', 'post-load-memory-check', 'release-intake-gate',
    'recording-relay-update', 'recording-relay-smoke', 'persist-candidate', 'candidate-running', 'candidate-rollback', 'gate-cleanup',
})
CUDA_ROLLBACK_STAGES = frozenset({
    'inspect-candidate', 'wait-candidate-idle', 'disable-candidate-restart', 'stop-candidate', 'remove-candidate',
    'inspect-network', 'remove-network', 'inspect-primary', 'restore-primary-restart',
    'start-primary', 'wait-primary-health', 'restore-relay', 'remove-promoted-relay', 'restore-original-relay', 'wait-relay-health', 'complete',
})
SAFE_EXECUTABLES = frozenset({'docker', 'python3', 'git', 'iptables'})
FAILURE_CLASSIFICATIONS = frozenset({
    'cuda-out-of-memory', 'vllm-memory-budget-refusal', 'model-cache-integrity',
    'container-health', 'permission-denied', 'host-memory-headroom', 'relay-smoke', 'unknown',
})
MAX_CANDIDATE_LOG_BYTES = 32 * 1024
MAX_CANDIDATE_LOG_LINES = 120
MAX_PRIVATE_DIAGNOSTIC_BYTES = 32 * 1024


class ManagedCommandFailure(RuntimeError):
    """Command failure containing only a safe executable name and return code."""

    def __init__(self, executable, exit_code=None, *, failure_kind='nonzero', diagnostic_text=''):
        name = Path(str(executable)).name
        self.executable = name if name in SAFE_EXECUTABLES else 'unknown'
        self.exit_code = exit_code if isinstance(exit_code, int) else None
        self.failure_kind = failure_kind if failure_kind in {'nonzero', 'timeout', 'launch'} else 'unknown'
        self.classification = classify_diagnostic(diagnostic_text)
        # Kept only for the root-only diagnostic receipt. Never include this in
        # the exception message, journal, command metadata, or public output.
        self.private_diagnostic = bounded_private_excerpt(diagnostic_text)
        super().__init__('Managed command failed')


class CandidateQualificationFailure(RuntimeError):
    """Safe summary for a candidate qualification failure."""

    def __init__(self, metadata):
        self.metadata = metadata
        super().__init__('Whisper candidate qualification failed')


def run(args, *, timeout=60, check=True, environment=None):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=environment)
    except subprocess.TimeoutExpired:
        raise ManagedCommandFailure(args[0], failure_kind='timeout') from None
    except OSError:
        raise ManagedCommandFailure(args[0], failure_kind='launch') from None
    if check and result.returncode:
        raise ManagedCommandFailure(args[0], result.returncode,
                                    diagnostic_text=(result.stderr or '') + '\n' + (result.stdout or ''))
    return result


def run_with_input(args, payload, *, timeout):
    try:
        result = subprocess.run(args, input=payload, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ManagedCommandFailure(args[0], failure_kind='timeout') from None
    except OSError:
        raise ManagedCommandFailure(args[0], failure_kind='launch') from None
    stdout = result.stdout or b''
    stderr = result.stderr or b''
    if result.returncode:
        diagnostic = (stderr + b'\n' + stdout).decode('utf-8', 'replace')
        raise ManagedCommandFailure(args[0], result.returncode, diagnostic_text=diagnostic)
    try:
        return type('ManagedInputResult', (), {'stdout': stdout.decode('utf-8', 'strict')})()
    except UnicodeDecodeError:
        raise ManagedCommandFailure(args[0], result.returncode, diagnostic_text='invalid UTF-8 output') from None


def candidate_stage(tx, stage):
    if stage not in CANDIDATE_STAGES:
        raise ValueError('Unknown candidate stage')
    tx['candidate_stage'] = stage
    save_candidate(tx)


def safe_class_name(error):
    name = type(error).__name__
    return name if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', name) else 'Error'


def safe_command_metadata(error):
    if not isinstance(error, ManagedCommandFailure):
        return {'executable': None, 'exit_code': None}
    return {'executable': error.executable,
            'exit_code': error.exit_code if error.exit_code is None or -255 <= error.exit_code <= 255 else None}


def bounded_private_excerpt(text):
    """Return a bounded raw excerpt for the root-only receipt, never stdout."""
    encoded = str(text).encode('utf-8', 'replace')[-MAX_PRIVATE_DIAGNOSTIC_BYTES:]
    return encoded.decode('utf-8', 'ignore')


def classify_diagnostic(text, *, stage=None):
    normalized = str(text).lower()
    if any(marker in normalized for marker in ('permission denied', 'operation not permitted')):
        return 'permission-denied'
    if any(marker in normalized for marker in (
            'cuda out of memory', 'cuda error: out of memory', 'failed to allocate cuda',
            'cublas_status_alloc_failed', 'cudaerroroutofmemory')):
        return 'cuda-out-of-memory'
    if ('free memory' in normalized and any(marker in normalized for marker in (
            'less than', 'not enough', 'insufficient')) and
            any(marker in normalized for marker in ('gpu', 'device', 'vllm'))):
        return 'vllm-memory-budget-refusal'
    if any(marker in normalized for marker in (
            'checksum mismatch', 'checksum failed', 'sha256 mismatch',
            'sha256 verification failed', 'model cache integrity failure')):
        return 'model-cache-integrity'
    if any(marker in normalized for marker in ('unhealthy', 'health check failed', 'healthcheck failed')):
        return 'container-health'
    if stage in {'pre-load-memory-check', 'post-load-memory-check'}:
        return 'host-memory-headroom'
    if stage == 'synthetic-relay-smoke':
        return 'relay-smoke'
    return 'unknown'


def record_candidate_rollback_failure(tx, error):
    command = safe_command_metadata(error)
    tx.update(
        state='rollback-failed', rollback_status='failed',
        rollback_failure_stage=tx.get('rollback_stage'),
        rollback_failure_class=safe_class_name(error),
        rollback_failure_classification=(error.classification
            if isinstance(error, ManagedCommandFailure) and error.classification != 'unknown'
            else classify_diagnostic('', stage=tx.get('rollback_stage'))),
        rollback_failure_executable=command['executable'],
        rollback_failure_exit_code=command['exit_code'],
    )
    try:
        save_candidate(tx)
    except Exception:
        pass
    return safe_candidate_metadata(tx)


def candidate_diagnostic_path(diagnostic_id):
    if not re.fullmatch(r'[0-9a-f]{12}', str(diagnostic_id)):
        raise ValueError('Invalid candidate diagnostic identifier')
    return STATE / ('candidate-diagnostic-' + diagnostic_id + '.json')


def write_candidate_diagnostic(diagnostic_id, report):
    protected(STATE, True)
    target = candidate_diagnostic_path(diagnostic_id)
    temporary = STATE / ('.candidate-diagnostic-' + diagnostic_id + '.tmp')
    if target.exists() or target.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise ValueError('Candidate diagnostic already exists or is incomplete')
    with temporary.open('x', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(report, stream, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, target)


def capture_candidate_log_excerpt(tx):
    try:
        candidate = inspect(CANDIDATE)
        if not candidate:
            return 'unavailable', ''
        labels = candidate.get('Config', {}).get('Labels') or {}
        if (candidate.get('Image') != tx.get('candidate_image_id') or
                labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or
                labels.get('com.docker.compose.service') != 'whisper-candidate'):
            return 'withheld-identity-mismatch', ''
        result = run(['docker', 'logs', '--tail', str(MAX_CANDIDATE_LOG_LINES), CANDIDATE],
                     timeout=15, check=False)
        text = (result.stdout or '') + '\n' + (result.stderr or '')
        return ('captured' if result.returncode == 0 else 'unavailable', bounded_private_excerpt(text))
    except Exception:
        return 'unavailable', ''


def record_candidate_failure(tx, error, *, stage=None):
    failure_stage = stage or tx.get('candidate_stage', 'preflight')
    if failure_stage not in CANDIDATE_STAGES:
        failure_stage = 'preflight'
    command = safe_command_metadata(error)
    diagnostic_id = os.urandom(6).hex()
    logs_status, logs = capture_candidate_log_excerpt(tx)
    classification = (error.classification if isinstance(error, ManagedCommandFailure) and
                      error.classification != 'unknown' else classify_diagnostic(logs, stage=failure_stage))
    report = {
        'diagnostic_id': diagnostic_id,
        'failure_stage': failure_stage,
        'failure_class': safe_class_name(error),
        'failure_classification': classification,
        'failure_diagnostic_excerpt': bounded_private_excerpt(str(error)),
        'command_executable': command['executable'],
        'command_exit_code': command['exit_code'],
        'command_failure_kind': error.failure_kind if isinstance(error, ManagedCommandFailure) else None,
        'command_diagnostic_excerpt': (error.private_diagnostic
                                       if isinstance(error, ManagedCommandFailure) else ''),
        'candidate_log_status': logs_status,
        'candidate_log_excerpt': logs,
    }
    try:
        write_candidate_diagnostic(diagnostic_id, report)
        diagnostic_available = True
    except Exception:
        diagnostic_available = False
    tx.update(
        failure_id=diagnostic_id,
        failure_stage=failure_stage,
        failure_class=safe_class_name(error),
        failure_classification=classification,
        failure_executable=command['executable'],
        failure_exit_code=command['exit_code'],
        failure_diagnostic_available=diagnostic_available,
        rollback_status='pending',
    )
    try:
        save_candidate(tx)
    except Exception:
        # The root-only diagnostic file remains available if the journal itself
        # cannot be updated; rollback must still be attempted by the caller.
        pass
    return safe_candidate_metadata(tx)


def safe_candidate_metadata(tx):
    failure_id = tx.get('failure_id')
    if not isinstance(failure_id, str) or not re.fullmatch(r'[0-9a-f]{12}', failure_id):
        failure_id = None
    stage = tx.get('failure_stage')
    if stage not in CANDIDATE_STAGES:
        stage = None
    rollback_stage = tx.get('rollback_failure_stage')
    if rollback_stage not in CANDIDATE_ROLLBACK_STAGES:
        rollback_stage = None
    def public_exit(value):
        return value if isinstance(value, int) and -255 <= value <= 255 else None
    return {
        'failure_id': failure_id,
        'failure_stage': stage,
        'failure_class': tx.get('failure_class') if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', str(tx.get('failure_class', ''))) else None,
        'failure_classification': tx.get('failure_classification') if tx.get('failure_classification') in FAILURE_CLASSIFICATIONS else 'unknown',
        'failure_executable': tx.get('failure_executable') if tx.get('failure_executable') in SAFE_EXECUTABLES else None,
        'failure_exit_code': public_exit(tx.get('failure_exit_code')),
        'state': tx.get('state') if tx.get('state') in {'prepared', 'primary-stopped', 'candidate-running', 'rolled-back', 'rollback-failed', 'gate-cleanup-failed'} else 'unknown',
        'rollback_status': tx.get('rollback_status') if tx.get('rollback_status') in {'pending', 'completed', 'failed', 'not-attempted'} else None,
        'rollback_failure_stage': rollback_stage,
        'rollback_failure_class': tx.get('rollback_failure_class') if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', str(tx.get('rollback_failure_class', ''))) else None,
        'rollback_failure_classification': tx.get('rollback_failure_classification') if tx.get('rollback_failure_classification') in FAILURE_CLASSIFICATIONS else 'unknown',
        'rollback_failure_executable': tx.get('rollback_failure_executable') if tx.get('rollback_failure_executable') in SAFE_EXECUTABLES else None,
        'rollback_failure_exit_code': public_exit(tx.get('rollback_failure_exit_code')),
        'gate_cleanup_failure_class': tx.get('gate_cleanup_failure_class') if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', str(tx.get('gate_cleanup_failure_class', ''))) else None,
        'failure_diagnostic_available': bool(tx.get('failure_diagnostic_available')),
    }


def format_candidate_failure(metadata):
    return ('Whisper candidate failure'
            f" id={metadata.get('failure_id') or 'unavailable'}"
            f" stage={metadata.get('failure_stage') or 'unknown'}"
            f" class={metadata.get('failure_class') or 'Error'}"
            f" classification={metadata.get('failure_classification') or 'unknown'}"
            f" executable={metadata.get('failure_executable') or 'none'}"
            f" exit_code={metadata.get('failure_exit_code')}"
            f" rollback={metadata.get('rollback_status') or 'unknown'}"
            f" rollback_stage={metadata.get('rollback_failure_stage') or 'none'}"
            f" rollback_class={metadata.get('rollback_failure_class') or 'none'}"
            f" rollback_classification={metadata.get('rollback_failure_classification') or 'unknown'}"
            f" gate_cleanup_class={metadata.get('gate_cleanup_failure_class') or 'none'}")


def protected(path, directory=False):
    info = path.lstat()
    if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Untrusted managed path')
    if directory and not path.is_dir():
        raise ValueError('Expected managed directory')


def published(revision):
    if not re.fullmatch('[0-9a-f]{40}', revision) or ROOT != Path('/srv/homecompute/releases') / revision:
        raise ValueError('Run from the selected immutable release')
    for path in [*reversed(ROOT.parents), ROOT]:
        protected(path, True)
    if (run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).stdout.strip() != revision or
            run(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=all']).stdout.strip()):
        raise ValueError('Dirty or mismatched release')
    run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', revision, 'origin/master'])


def load_cuda_qualification_helper():
    helper_path = ROOT / 'scripts/whisper-cuda-qualification.py'
    spec = importlib.util.spec_from_file_location('_homecompute_whisper_cuda_qualification', helper_path)
    if spec is None or spec.loader is None:
        raise ValueError('Whisper CUDA qualification helper is unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(value):
    target = STATE / 'transaction.json'
    temporary = STATE / 'transaction.tmp'
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('Incomplete journal write requires inspection')
    with temporary.open('x') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, target)


def inspect(name):
    result = run(['docker', 'inspect', name], check=False)
    if result.returncode:
        # Distinguish an absent container from unavailable Docker.
        run(['docker', 'info', '--format', '{{.ServerVersion}}'])
        if not any(reason in result.stderr.lower() for reason in ('no such object', 'no such container')):
            raise ValueError('Container state is unavailable')
        return None
    return json.loads(result.stdout)[0]


def compose(*arguments, timeout=1200, relay_image=None):
    identity = pwd.getpwnam('gb10-ai')
    environment = {**os.environ, 'GB10_ROOT': '/srv/gb10-ai',
                   'GB10_RUNTIME_UID': str(identity.pw_uid), 'GB10_RUNTIME_GID': str(identity.pw_gid),
                   'VLLM_API_KEY_FILE': str(VLLM_API_KEY_PATH),
                   'WHISPER_CPP_ROLE_API_KEY_FILE': str(CPP_ROLE_API_KEY_PATH),
                   'WHISPER_HTTP_IMAGE': relay_image or 'homecompute/stt-http:2.0.0'}
    if relay_image is not None:
        environment['WHISPER_HTTP_IMAGE'] = relay_image
    return run(['docker', 'compose', '--project-name', 'whisper-large-v3-stt',
                '-f', str(ROOT / 'deploy/whisper-stt/compose.yaml'),
                *arguments], timeout=timeout, environment=environment)


def compose_cuda(*arguments, timeout=3600, revision='', image_id='', image_source_revision=None,
                 api_key_path=None, production_network=False,
                 max_audio_seconds=180, max_upload_bytes=6_000_000,
                 native_timeout_seconds=900):
    identity = pwd.getpwnam('gb10-ai')
    environment = {**os.environ,
                   'WHISPER_CPP_HOME_COMPUTE_REVISION': image_source_revision or revision,
                   'WHISPER_CPP_IMAGE_ID': image_id,
                   'WHISPER_CPP_MODEL_FILE': str(CPP_MODEL_PATH),
                   'WHISPER_CPP_API_KEY_FILE': str(api_key_path or CPP_API_KEY_PATH),
                   'WHISPER_CPP_CUDA_PATCHER_SHA256': CPP_CUDA_PATCHER_SHA256,
                   'WHISPER_CPP_MAX_AUDIO_SECONDS': str(max_audio_seconds),
                   'WHISPER_CPP_MAX_UPLOAD_BYTES': str(max_upload_bytes),
                   'WHISPER_CPP_NATIVE_TIMEOUT_SECONDS': str(native_timeout_seconds),
                   'WHISPER_CPP_UPLOAD_TIMEOUT_SECONDS': os.environ.get('WHISPER_CPP_UPLOAD_TIMEOUT_SECONDS', '120'),
                   'GB10_RUNTIME_UID': str(identity.pw_uid), 'GB10_RUNTIME_GID': str(identity.pw_gid)}
    command = ['docker', 'compose', '--project-name', CUDA_CANDIDATE_PROJECT,
               '-f', str(ROOT / 'deploy/whisper-cpp-stt/compose-cuda.yaml')]
    if production_network:
        command.extend(('-f', str(ROOT / 'deploy/whisper-cpp-stt/compose-cuda-production.yaml')))
    return run([*command, *arguments], timeout=timeout, environment=environment)


def guards():
    for path in (Path('/var/lib/homecompute-speech-capacity/active.json'),
                 Path('/var/lib/homecompute-speech-finalization/active.json')):
        if path.exists() or path.is_symlink():
            raise ValueError('Reconcile the active speech capacity candidate through its owning lifecycle first')
    if list(Path('/var/lib/homecompute-docker-maintenance').glob('*.active')):
        raise ValueError('Reconcile Docker maintenance overrides first')
    for path in Path('/var/lib/homecompute-speech-finalization').glob('*/journal.json'):
        protected(path)
        if json.loads(path.read_text()).get('phase') != 'completed':
            raise ValueError('Recover the pending speech finalization transaction first')


def ensure_no_cuda_candidate_transaction(action):
    """Keep other Whisper lifecycles from disturbing a CUDA qualification."""
    journal = STATE / 'cuda-candidate.json'
    if not journal.exists() and not journal.is_symlink():
        return
    protected(journal)
    tx = json.loads(journal.read_text())
    if tx.get('state') != 'rolled-back':
        raise ValueError('Recover the Whisper.cpp CUDA candidate with cuda-candidate-rollback before ' + action)


def prepare():
    guards()
    provision_cpp_role_key()
    print('Preparing pinned full Whisper cache, relay, and opt-in runtime candidate; GPU services remain unchanged', flush=True)
    for path in ('/srv/gb10-ai/cache/huggingface', '/srv/gb10-ai/manifests'):
        if Path(path).is_symlink() or not Path(path).is_dir():
            raise ValueError('Existing cache and manifest directories are required')
    compose('--profile', 'prepare', 'run', '--rm', 'whisper-fetch', timeout=3600)
    compose('--profile', 'prepare', 'run', '--rm', 'whisper-accept')
    compose('build', 'whisper-http')
    print('Whisper cache accepted and relay built', flush=True)


def prepare_runtime_candidate():
    guards()
    cache = Path('/srv/gb10-ai/manifests/accepted-whisper-large-v3-cache.json')
    protected(cache)
    manifest = json.loads(cache.read_text())
    if manifest.get('repo_id') != MODEL or manifest.get('revision') != REVISION:
        raise ValueError('Accepted full Whisper cache is required before candidate preparation')
    compose('--profile', 'candidate', 'build', 'whisper-candidate')
    candidate_image_id()
    print('Pinned Whisper runtime candidate image built and verified; no model was started', flush=True)


def check_cuda_host():
    if platform.machine() != 'aarch64':
        raise ValueError('GB10 CUDA candidate requires native ARM64; emulation is refused')
    base = json.loads(run(['docker', 'image', 'inspect', CUDA_BASE_IMAGE]).stdout)[0]
    if base.get('Os') != 'linux' or base.get('Architecture') != 'arm64':
        raise ValueError('Pinned CUDA build base is not a native ARM64 image')
    rows = run(['nvidia-smi', '--query-gpu=name,compute_cap,driver_version', '--format=csv,noheader']).stdout.strip().splitlines()
    if len(rows) != 1:
        raise ValueError('Exactly one local GB10 CUDA device is required')
    fields = [field.strip() for field in rows[0].split(',')]
    if len(fields) != 3 or fields[0] != 'NVIDIA GB10' or fields[1] != '12.1':
        raise ValueError('Local CUDA device is not the pinned GB10 compute capability')
    match = re.match(r'^(\d+)', fields[2])
    if not match or int(match.group(1)) < 580:
        raise ValueError('CUDA 13 requires NVIDIA Linux driver 580 or newer')


def cuda_candidate_image_id(revision):
    image = json.loads(run(['docker', 'image', 'inspect', CUDA_CANDIDATE_IMAGE]).stdout)[0]
    labels = image.get('Config', {}).get('Labels') or {}
    expected = {
        'org.opencontainers.image.revision': CPP_RUNTIME_COMMIT,
        'io.homecompute.source-revision': revision,
        'io.homecompute.whisper-cpp.runtime-commit': CPP_RUNTIME_COMMIT,
        'io.homecompute.whisper-cpp.model-sha256': CPP_MODEL_SHA256,
        'io.homecompute.whisper-cpp.backend': 'cuda-121a',
        'io.homecompute.whisper-cpp.cuda-patch': CPP_CUDA_PATCH_VERSION,
        'io.homecompute.whisper-cpp.cuda-patcher-sha256': CPP_CUDA_PATCHER_SHA256,
        'io.homecompute.whisper-cpp.cuda-base': CUDA_BASE_IMAGE,
    }
    if (image.get('Os') != 'linux' or image.get('Architecture') != 'arm64' or
            any(labels.get(key) != value for key, value in expected.items())):
        raise ValueError('Whisper.cpp CUDA candidate image identity or pinned build labels differ')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image.get('Id', '')):
        raise ValueError('Whisper.cpp CUDA candidate image ID is invalid')
    return image['Id']


def qualified_cuda_candidate_image_id():
    image = json.loads(run(['docker', 'image', 'inspect', CUDA_CANDIDATE_IMAGE]).stdout)[0]
    labels = image.get('Config', {}).get('Labels') or {}
    expected = {
        'org.opencontainers.image.revision': CPP_RUNTIME_COMMIT,
        'io.homecompute.source-revision': CPP_QUALIFIED_SOURCE_REVISION,
        'io.homecompute.whisper-cpp.runtime-commit': CPP_RUNTIME_COMMIT,
        'io.homecompute.whisper-cpp.model-sha256': CPP_MODEL_SHA256,
        'io.homecompute.whisper-cpp.backend': 'cuda-121a',
        'io.homecompute.whisper-cpp.cuda-patch': CPP_CUDA_PATCH_VERSION,
        'io.homecompute.whisper-cpp.cuda-patcher-sha256': CPP_CUDA_PATCHER_SHA256,
        'io.homecompute.whisper-cpp.cuda-base': CUDA_BASE_IMAGE,
    }
    if (image.get('Os') != 'linux' or image.get('Architecture') != 'arm64' or
            image.get('Id') != CPP_QUALIFIED_IMAGE_ID or
            any(labels.get(key) != value for key, value in expected.items())):
        raise ValueError('Qualified Whisper.cpp CUDA image identity or build pins differ')
    return CPP_QUALIFIED_IMAGE_ID


def verify_cpp_model(path, *, expected_sha256=None, expected_size=None):
    verifier_path = ROOT / 'deploy/whisper-cpp-stt/verify_model.py'
    spec = importlib.util.spec_from_file_location('_homecompute_whisper_cpp_model_verifier', verifier_path)
    if spec is None or spec.loader is None:
        raise ValueError('Pinned GGML model verifier is unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_model(
        path,
        expected_sha256=CPP_MODEL_SHA256 if expected_sha256 is None else expected_sha256,
        expected_size=CPP_MODEL_SIZE if expected_size is None else expected_size,
    )


def ensure_private_directory(path, *, gid):
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError('Artifact parent directory is unavailable or unsafe')
    parent_info = parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != 0 or parent_info.st_mode & 0o022:
        raise ValueError('Artifact parent directory ownership or permissions are unsafe')
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Artifact directory ownership or permissions are unsafe')
        return
    path.mkdir(mode=0o750)
    os.chown(path, 0, gid)
    os.chmod(path, 0o750)


def validate_provisioned_key(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 10001 or
            stat.S_IMODE(info.st_mode) != 0o440 or not 16 <= info.st_size <= 512):
        raise ValueError('CUDA qualification key must be root:10001 mode 0440 and 16–512 bytes')
    value = path.read_bytes()
    if any(byte < 0x21 or byte > 0x7e for byte in value):
        raise ValueError('CUDA qualification key must contain printable non-whitespace ASCII')


def provision_cpp_key(path=CPP_API_KEY_PATH):
    ensure_private_directory(path.parent, gid=10001)
    if path.exists() or path.is_symlink():
        validate_provisioned_key(path)
        return 'existing'
    value = secrets.token_urlsafe(48).encode('ascii')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    try:
        os.fchown(fd, 0, 10001)
        os.fchmod(fd, 0o440)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        raise
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    validate_provisioned_key(path)
    return 'created'


def provision_cpp_role_key(path=CPP_ROLE_API_KEY_PATH, source=VLLM_API_KEY_PATH):
    """Install the existing Whisper role credential for the promoted adapter."""
    ensure_private_directory(path.parent, gid=10001)
    descriptor = None
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                stat.S_IMODE(info.st_mode) & 0o022 or not 16 <= info.st_size <= 512):
            raise ValueError('Existing Whisper role credential is unsafe')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            value = stream.read(513).strip()
    except OSError:
        raise ValueError('Existing Whisper role credential is unavailable') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not value or any(byte < 0x21 or byte > 0x7e for byte in value):
        raise ValueError('Existing Whisper role credential is invalid')
    if path.exists() or path.is_symlink():
        validate_provisioned_key(path)
        if not secrets.compare_digest(path.read_bytes(), value):
            raise ValueError('Installed Whisper role credential differs; reconcile it before promotion')
        return 'existing'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o440)
    try:
        os.fchown(fd, 0, 10001)
        os.fchmod(fd, 0o440)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    validate_provisioned_key(path)
    return 'copied'


def provision_cpp_model(path=CPP_MODEL_PATH, *, url=CPP_MODEL_URL):
    ensure_private_directory(path.parent, gid=10001)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 10001 or
                stat.S_IMODE(info.st_mode) != 0o440):
            raise ValueError('Existing GGML model must be root:10001 mode 0440')
        verify_cpp_model(path)
        return 'existing'
    free_bytes = shutil.disk_usage(path.parent).free
    if free_bytes < CPP_MODEL_SIZE + CPP_MODEL_FREE_SPACE_RESERVE:
        raise ValueError('Insufficient free space for pinned GGML model and staging reserve')
    descriptor, temporary_name = tempfile.mkstemp(prefix='.ggml-large-v3.', suffix='.partial', dir=path.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    received = 0
    try:
        with os.fdopen(descriptor, 'wb') as output:
            request = urllib.request.Request(url, headers={'User-Agent': 'HomeCompute-whisper-candidate/1'})
            try:
                response = urllib.request.urlopen(request, timeout=CPP_DOWNLOAD_TIMEOUT)
            except (OSError, urllib.error.URLError, TimeoutError):
                raise RuntimeError('Pinned GGML download could not be opened') from None
            with response:
                length = response.headers.get('Content-Length')
                if length is not None and (not length.isdecimal() or int(length) != CPP_MODEL_SIZE):
                    raise ValueError('Pinned GGML download length differs from the declared artifact')
                started = time.monotonic()
                while True:
                    if time.monotonic() - started > CPP_DOWNLOAD_MAX_SECONDS:
                        raise TimeoutError('Pinned GGML download exceeded its bounded duration')
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > CPP_MODEL_SIZE:
                        raise ValueError('Pinned GGML download exceeds the declared artifact size')
                    output.write(block)
                    digest.update(block)
            output.flush()
            os.fsync(output.fileno())
        if received != CPP_MODEL_SIZE or digest.hexdigest() != CPP_MODEL_SHA256:
            raise ValueError('Pinned GGML download size or checksum does not match')
        verify_cpp_model(temporary)
        os.chown(temporary, 0, 10001)
        os.chmod(temporary, 0o440)
        os.link(temporary, path)
        temporary.unlink()
        return 'downloaded'
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def cuda_prepare_failure(stage, error):
    diagnostic_id = os.urandom(6).hex()
    command = safe_command_metadata(error)
    if isinstance(error, ManagedCommandFailure):
        private = error.private_diagnostic
        classification = error.classification
        failure_kind = error.failure_kind
    else:
        private = bounded_private_excerpt(f'{type(error).__name__}: {error}')
        classification = 'unknown'
        failure_kind = None
    report = {
        'diagnostic_id': diagnostic_id, 'stage': stage,
        'failure_class': safe_class_name(error), 'classification': classification,
        'executable': command['executable'], 'exit_code': command['exit_code'],
        'failure_kind': failure_kind, 'private_detail': private,
    }
    try:
        write_candidate_diagnostic(diagnostic_id, report)
        available = True
    except Exception:
        available = False
    return {'diagnostic_id': diagnostic_id, 'stage': stage,
            'failure_class': safe_class_name(error), 'classification': classification,
            'executable': command['executable'], 'exit_code': command['exit_code'],
            'diagnostic_available': available}


def format_cuda_prepare_failure(metadata):
    return ('Whisper.cpp CUDA preparation failed: '
            f"id={metadata['diagnostic_id']} stage={metadata['stage']} "
            f"class={metadata['failure_class']} classification={metadata['classification']} "
            f"executable={metadata['executable']} exit_code={metadata['exit_code']} "
            f"diagnostic_available={str(metadata['diagnostic_available']).lower()}")


class CudaPrepareFailure(RuntimeError):
    """Safe summary for model/key provisioning or CUDA image preparation."""

    def __init__(self, metadata):
        self.metadata = metadata
        super().__init__('Whisper.cpp CUDA candidate preparation failed')


class CudaQualificationFailure(RuntimeError):
    """Safe public summary for one-command private CUDA qualification."""

    def __init__(self, metadata):
        self.metadata = metadata
        super().__init__('Whisper.cpp CUDA qualification did not complete cleanly')


def format_cuda_qualification_failure(metadata):
    stage = metadata.get('stage') if isinstance(metadata, dict) else None
    failure = metadata.get('failure') if isinstance(metadata, dict) else None
    if not isinstance(stage, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', stage):
        stage = failure.get('failure_stage') if isinstance(failure, dict) else None
    if not isinstance(stage, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', stage):
        stage = 'unknown'
    if not isinstance(metadata, dict):
        metadata = {}
    return ('Whisper.cpp qualification'
            f" id={metadata.get('qualification_id') or 'unavailable'}"
            f" state={metadata.get('state') or 'unknown'}"
            f" stage={stage}"
            f" rollback={metadata.get('rollback') or 'unknown'}"
            f" receipt={metadata.get('receipt') or 'unavailable'}")


def prepare_cuda_candidate(revision):
    stage = 'preflight'
    try:
        guards()
        stage = 'host-preflight'
        check_cuda_host()
        stage = 'provision-model'
        model_status = provision_cpp_model()
        stage = 'provision-key'
        key_status = provision_cpp_key()
        stage = 'verify-artifacts'
        validate_cpp_artifact_files()
        stage = 'build-image'
        compose_cuda('--profile', 'cuda-candidate', 'build', '--pull=false', 'whisper-cpp-cuda',
                     revision=revision)
        stage = 'verify-image'
        image_id = cuda_candidate_image_id(revision)
    except Exception as error:
        raise CudaPrepareFailure(cuda_prepare_failure(stage, error)) from None
    print(json.dumps({'state': 'prepared', 'image_id': image_id,
                      'model': model_status, 'qualification_key': key_status,
                      'gpu_model_started': False}, sort_keys=True), flush=True)


def validate_cpp_artifact_files():
    for path in (CPP_MODEL_PATH, CPP_API_KEY_PATH):
        try:
            info = path.lstat()
        except OSError:
            raise ValueError('Whisper.cpp CUDA model or qualification key is unavailable') from None
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 10001 or
                stat.S_IMODE(info.st_mode) != 0o440):
            raise ValueError('Whisper.cpp CUDA artifact must be root:10001 mode 0440')
    validate_provisioned_key(CPP_API_KEY_PATH)
    verify_cpp_model(CPP_MODEL_PATH)


def validate_cuda_artifacts(image_id, revision, *, api_key_path=CPP_API_KEY_PATH):
    for path in (CPP_MODEL_PATH, api_key_path):
        try:
            info = path.lstat()
        except OSError:
            raise ValueError('Whisper.cpp CUDA model or qualification credential is unavailable') from None
        if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != 0 or
                info.st_gid != 10001 or stat.S_IMODE(info.st_mode) != 0o440):
            raise ValueError('Whisper.cpp CUDA model and credential must be root:10001 mode 0440 files')
    if api_key_path.stat().st_size < 16 or api_key_path.stat().st_size > 512:
        raise ValueError('Whisper.cpp CUDA qualification credential size is invalid')
    validate_provisioned_key(api_key_path)
    command = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop=ALL',
               '--security-opt=no-new-privileges', '--user', '10001:10001', '--memory=512m',
               '--pids-limit=16', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=8m',
               '--mount', f'type=bind,src={CPP_MODEL_PATH},dst=/models/ggml-large-v3.bin,readonly',
               '--entrypoint', 'python3', image_id, '-I', '-B',
               '/opt/homecompute/verify_model.py', '/models/ggml-large-v3.bin']
    run(command, timeout=1800)
    key_check = cuda_candidate_key_check_program()
    run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop=ALL',
         '--security-opt=no-new-privileges', '--user', '10001:10001', '--memory=64m',
         '--pids-limit=16', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=8m',
         '--mount', f'type=bind,src={api_key_path},dst=/run/secrets/whisper_cpp_api_key,readonly',
         '--entrypoint', 'python3', image_id, '-I', '-B', '-c', key_check,
         '/run/secrets/whisper_cpp_api_key'], timeout=30)


def validate_cuda_request_limits(max_audio_seconds, max_upload_bytes, native_timeout_seconds):
    if (not 1 <= max_audio_seconds <= 7200 or not 1 <= max_upload_bytes <= 256 * 1024 * 1024 or
            max_upload_bytes < max_audio_seconds * 32_000 + 44 or
            not 1 <= native_timeout_seconds <= 14_400):
        raise ValueError('Whisper.cpp CUDA request limits are invalid or inconsistent')


def save_cuda_candidate(tx):
    target = STATE / 'cuda-candidate.json'
    temporary = STATE / 'cuda-candidate.tmp'
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('Incomplete CUDA candidate journal write requires inspection')
    with temporary.open('x') as stream:
        os.chmod(temporary, 0o600)
        json.dump(tx, stream, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, target)


def cuda_candidate_stage(tx, stage):
    if stage not in CUDA_CANDIDATE_STAGES:
        raise ValueError('Unknown CUDA candidate stage')
    tx['candidate_stage'] = stage
    save_cuda_candidate(tx)


def cuda_candidate_was_exposed(tx):
    # Treat every stage that may have sent audio as exposed, including journals
    # from releases that did not persist the explicit marker before smoke.
    exposed_stages = {'synthetic-candidate-smoke', 'post-load-memory-check',
                      'recording-relay-update', 'recording-relay-smoke',
                      'persist-candidate', 'candidate-running', 'candidate-rollback',
                      'gate-cleanup'}
    return tx.get('candidate_exposed') is True or tx.get('candidate_stage') in exposed_stages


def promoted_relay_smoke_program(image_source_revision):
    expected = json.dumps({
        'X-HomeCompute-STT-Model-SHA256': CPP_MODEL_SHA256,
        'X-HomeCompute-STT-Model-Snapshot': CPP_MODEL_SNAPSHOT,
        'X-HomeCompute-STT-Runtime': 'whisper.cpp',
        'X-HomeCompute-STT-Runtime-Commit': CPP_RUNTIME_COMMIT,
        'X-HomeCompute-STT-HomeCompute-Revision': image_source_revision,
        'X-HomeCompute-STT-Image-ID': CPP_QUALIFIED_IMAGE_ID,
    }, sort_keys=True)
    return '''import io,json,math,socket,time,urllib.error,urllib.request,wave
key=open('/run/secrets/whisper_cpp_api_key','rb').read().decode('ascii')
def pcm(seconds=1):
 b=io.BytesIO()
 with wave.open(b,'wb') as w:
  w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(bytes(seconds*32000))
 return b.getvalue()
def error_enum(body, route):
 try:
  value=json.loads(body[:4096])
  message=value.get('error') if isinstance(value,dict) else None
  if isinstance(message,dict): message=message.get('message')
 except (ValueError,UnicodeError):
  message=None
 if not isinstance(message,str): return 'http_error_unclassified'
 direct={
  'Speech backend returned out-of-bounds segment timestamps':'timestamp_out_of_bounds',
  'Speech backend returned invalid verbose JSON':'invalid_verbose_json',
  'Speech backend returned an invalid segment':'invalid_segment',
  'Speech backend returned invalid text':'invalid_text',
  'Speech backend is unavailable':'backend_unavailable',
  'Speech backend rejected the request':'backend_rejected',
  'Audio duration exceeds the limit':'audio_duration_exceeded',
  'Audio must be mono 16 kHz PCM16 WAV':'audio_format_rejected',
 }
 relay={
  'Speech backend provenance is incomplete':'missing_provenance',
  'Speech backend rejected the request':'upstream_rejected',
  'Speech backend is unavailable':'upstream_unavailable',
  'Recording recognition is unavailable':'recording_unavailable',
 }
 return (direct if route=='recording-adapter' else relay).get(message,'http_error_unclassified')
def post(probe,base,path,fields,verbose=False,provenance=False,duration=1.0):
 global can_post
 boundary='promotion-smoke-boundary'
 body=b''.join((b'--'+boundary.encode()+b'\\r\\nContent-Disposition: form-data; name="'+k.encode()+b'"\\r\\n\\r\\n'+v.encode()+b'\\r\\n') for k,v in fields)
 body+=b'--'+boundary.encode()+b'\\r\\nContent-Disposition: form-data; name="file"; filename="audio.wav"\\r\\nContent-Type: audio/wav\\r\\n\\r\\n'+pcm(int(duration))+b'\\r\\n--'+boundary.encode()+b'--\\r\\n'
 req=urllib.request.Request(base+path,data=body,headers={'Authorization':'Bearer '+key,'Content-Type':'multipart/form-data; boundary='+boundary})
 record={'probe':probe,'status':None,'error':'none','timestamp_bounds_valid':None,'timestamp_bound_violation_count':None,'audio_duration_seconds':duration,'inference_idle_confirmed':None}
 if provenance: record['provenance_headers_present']={name:False for name in ''' + expected + '''}
 if not can_post:
  record['error']='inference_idle_unconfirmed'
  print(json.dumps(record,sort_keys=True,separators=(',',':')),flush=True)
  return record
 try:
  with urllib.request.urlopen(req,timeout=180) as response:
   record['status']=response.status
   raw=response.read(2097152+1)
   if len(raw)>2097152: raise ValueError
   result=json.loads(raw)
   if not isinstance(result,dict) or not isinstance(result.get('text'),str): record['error']='response_shape_invalid'
   elif verbose and not isinstance(result.get('segments'),list): record['error']='response_shape_invalid'
   elif verbose:
    violations=0
    for segment in result['segments']:
     if not isinstance(segment,dict): record['error']='response_shape_invalid';break
     start=segment.get('start');end=segment.get('end')
     if (not isinstance(start,(int,float)) or isinstance(start,bool) or not isinstance(end,(int,float)) or
         isinstance(end,bool) or not math.isfinite(start) or not math.isfinite(end)):
      record['error']='invalid_segment';break
     if start<0 or end<start or end>record['audio_duration_seconds']: violations+=1
    record['timestamp_bound_violation_count']=violations
    record['timestamp_bounds_valid']=record['error']!='invalid_segment' and record['error']!='response_shape_invalid' and violations==0
    if violations and record['error']=='none': record['error']='timestamp_out_of_bounds'
   if provenance:
    present={name:response.headers.get(name)==value for name,value in ''' + expected + '''.items()}
    record['provenance_headers_present']={name:response.headers.get(name) is not None for name in ''' + expected + '''}
    if not all(record['provenance_headers_present'].values()) and record['error']=='none': record['error']='missing_provenance'
    elif not all(present.values()) and record['error']=='none': record['error']='provenance_mismatch'
 except urllib.error.HTTPError as e:
  record['status']=e.code
  try: body=e.read(4096)
  except OSError: body=b''
  record['error']=error_enum(body,probe)
  e.close()
  if record['error']=='timestamp_out_of_bounds': record['timestamp_bounds_valid']=False
 except (TimeoutError,socket.timeout): record['error']='timeout'
 except urllib.error.URLError as e:
  record['error']='timeout' if isinstance(getattr(e,'reason',None),(TimeoutError,socket.timeout)) else 'connection_error'
 except (OSError,ValueError,UnicodeError): record['error']='response_or_connection_invalid'
 record['inference_idle_confirmed']=wait_idle()
 if record['inference_idle_confirmed'] is not True:
  can_post=False
  if record['error']=='none': record['error']='inference_idle_unconfirmed'
 print(json.dumps(record,sort_keys=True,separators=(',',':')),flush=True)
 return record
def wait_idle():
 for _ in range(5):
  try:
   request=urllib.request.Request('http://127.0.0.1:8007/health')
   with urllib.request.urlopen(request,timeout=1) as response:
    value=json.loads(response.read(4096))
   if not isinstance(value,dict): time.sleep(0.2);continue
   native=value.get('native_inference_busy')
   device=value.get('device')
   if (value.get('status')=='ok' and value.get('inference_busy') is False and
       device in ('cpu','cuda') and (native is False or device=='cpu' and native is None)):
    return True
   if native is not False and device=='cuda': time.sleep(0.2);continue
  except urllib.error.HTTPError as e:
   e.close()
   time.sleep(0.2);continue
  except (TimeoutError,socket.timeout,urllib.error.URLError,OSError,ValueError,UnicodeError):
   time.sleep(0.2);continue
  return False
 return False
can_post=True
standard=[('model','whisper-large-v3'),('response_format','json'),('language','auto'),('max_context','0'),('max_len','-1'),('beam_size','5'),('best_of','5')]
verbose=[('model','whisper'),('response_format','verbose_json'),('language','auto'),('temperature','0'),('temperature_inc','0.2'),('max_context','0'),('max_len','-1'),('beam_size','5'),('best_of','5'),('carry_initial_prompt','false'),('translate','false'),('no_timestamps','false')]
direct=post('recording-adapter','http://whisper-cpp-recording:8000','/v1/audio/transcriptions',verbose,True,True,30.0)
recording=post('recording-relay','http://127.0.0.1:8007','/v1/recording-transcriptions',verbose,True,True,30.0)
ordinary=post('standard-relay','http://127.0.0.1:8007','/v1/audio/transcriptions',standard)
if any(item['error']!='none' or item['status']!=200 for item in (direct,recording,ordinary)): raise SystemExit(1)
'''


class PromotedRelaySmokeFailure(RuntimeError):
    def __init__(self, probe, error, status, *, adapter_error='unknown', relay_error='unknown'):
        self.probe = probe if probe in {'recording-adapter', 'recording-relay', 'standard-relay'} else 'unknown'
        self.error = error if error in {
            'timestamp_out_of_bounds', 'invalid_verbose_json', 'invalid_segment', 'invalid_text',
            'backend_unavailable', 'backend_rejected', 'audio_duration_exceeded', 'audio_format_rejected',
            'missing_provenance', 'upstream_rejected', 'upstream_unavailable', 'recording_unavailable',
            'http_error_unclassified', 'provenance_mismatch', 'response_shape_invalid', 'timeout',
            'connection_error', 'response_or_connection_invalid', 'inference_idle_unconfirmed',
            'smoke_receipt_invalid',
        } else 'smoke_receipt_invalid'
        self.status = status if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else None
        allowed = {'none', 'timestamp_out_of_bounds', 'invalid_verbose_json', 'invalid_segment',
                   'invalid_text', 'backend_unavailable', 'backend_rejected', 'audio_duration_exceeded',
                   'audio_format_rejected', 'missing_provenance', 'upstream_rejected',
                   'upstream_unavailable', 'recording_unavailable', 'http_error_unclassified',
                   'provenance_mismatch', 'response_shape_invalid', 'timeout', 'connection_error',
                   'response_or_connection_invalid', 'inference_idle_unconfirmed'}
        self.adapter_error = adapter_error if adapter_error in allowed else 'unknown'
        self.relay_error = relay_error if relay_error in allowed else 'unknown'
        super().__init__('Promoted STT relay smoke failed')


def promoted_relay_smoke():
    result = run(['docker', 'exec', 'gb10-whisper-cpp-http', 'python3', '-c',
                    promoted_relay_smoke_program(CPP_QUALIFIED_SOURCE_REVISION)], timeout=570, check=False)
    allowed_probes = {'recording-adapter', 'recording-relay', 'standard-relay'}
    allowed_errors = {'none', 'timestamp_out_of_bounds', 'invalid_verbose_json', 'invalid_segment',
                      'invalid_text', 'backend_unavailable', 'backend_rejected', 'audio_duration_exceeded',
                      'audio_format_rejected', 'missing_provenance', 'upstream_rejected',
                      'upstream_unavailable', 'recording_unavailable', 'http_error_unclassified',
                      'provenance_mismatch', 'response_shape_invalid', 'timeout', 'connection_error',
                      'response_or_connection_invalid', 'inference_idle_unconfirmed'}
    receipts = []
    expected_headers = {
        'X-HomeCompute-STT-Model-SHA256', 'X-HomeCompute-STT-Model-Snapshot',
        'X-HomeCompute-STT-Runtime', 'X-HomeCompute-STT-Runtime-Commit',
        'X-HomeCompute-STT-HomeCompute-Revision', 'X-HomeCompute-STT-Image-ID',
    }
    for line in result.stdout.splitlines():
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if (not isinstance(value, dict) or not isinstance(value.get('probe'), str) or
                not isinstance(value.get('error'), str) or value.get('probe') not in allowed_probes or
                value.get('error') not in allowed_errors or
                value.get('status') is not None and
                    (not isinstance(value.get('status'), int) or isinstance(value.get('status'), bool) or
                     not 100 <= value['status'] <= 599)):
            continue
        if type(value.get('inference_idle_confirmed')) not in {bool, type(None)}:
            continue
        headers = value.get('provenance_headers_present')
        if value['probe'] in {'recording-adapter', 'recording-relay'} and (
                not isinstance(headers, dict) or set(headers) != expected_headers or
                any(type(flag) is not bool for flag in headers.values()) or
                type(value.get('timestamp_bounds_valid')) not in {bool, type(None)} or
                type(value.get('timestamp_bound_violation_count')) not in {int, type(None)} or
                isinstance(value.get('timestamp_bound_violation_count'), bool) or
                type(value.get('inference_idle_confirmed')) not in {bool, type(None)} or
                type(value.get('audio_duration_seconds')) not in {int, float} or
                isinstance(value.get('audio_duration_seconds'), bool) or
                value.get('audio_duration_seconds') != 30.0):
            continue
        receipts.append(value)
    if len(receipts) != 3 or len({item['probe'] for item in receipts}) != 3:
        raise PromotedRelaySmokeFailure('unknown', 'smoke_receipt_invalid', None)
    by_probe = {item['probe']: item for item in receipts}
    adapter_error = by_probe['recording-adapter']['error']
    relay_error = by_probe['recording-relay']['error']
    failures = [item for item in receipts if item['error'] != 'none' or item['status'] != 200 or
                item.get('inference_idle_confirmed') is not True or
                item['probe'] in {'recording-adapter', 'recording-relay'} and
                    (item.get('timestamp_bounds_valid') is not True or
                     item.get('timestamp_bound_violation_count') != 0)]
    if failures or result.returncode:
        # Prefer the public relay path in the summary while retaining the
        # direct adapter's allowlisted result for diagnosis.
        failed = next((by_probe[name] for name in ('recording-relay', 'recording-adapter', 'standard-relay')
                       if by_probe[name]['error'] != 'none' or by_probe[name]['status'] != 200), None)
        if failed is None:
            failed = {'probe': 'unknown', 'error': 'smoke_receipt_invalid', 'status': None}
        failure_error = failed['error']
        if failure_error == 'none' and failed.get('inference_idle_confirmed') is not True:
            failure_error = 'inference_idle_unconfirmed'
        if failure_error == 'none' and failed['probe'] in {'recording-adapter', 'recording-relay'}:
            failure_error = ('timestamp_out_of_bounds' if failed.get('timestamp_bounds_valid') is False or
                             failed.get('timestamp_bound_violation_count') not in {0, None}
                             else 'smoke_receipt_invalid')
        if failure_error == 'none':
            failure_error = 'smoke_receipt_invalid'
        raise PromotedRelaySmokeFailure(failed['probe'], failure_error, failed['status'],
                                        adapter_error=adapter_error, relay_error=relay_error)
    if any(item.get('timestamp_bounds_valid') is not True for item in receipts
           if item['probe'] in {'recording-adapter', 'recording-relay'}):
        raise PromotedRelaySmokeFailure('recording-relay', 'smoke_receipt_invalid', 200,
                                        adapter_error=adapter_error, relay_error=relay_error)


def promote_relay(tx):
    original = inspect('gb10-whisper-http')
    labels = (original or {}).get('Config', {}).get('Labels') or {}
    if (not original or not original.get('State', {}).get('Running') or
            original.get('State', {}).get('Health', {}).get('Status') != 'healthy' or
            labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or
            labels.get('com.docker.compose.service') != 'whisper-http' or
            not tx.get('relay_image') or not tx.get('relay_candidate_image_id')):
        raise ValueError('Healthy owned STT relay and exact candidate image are required')
    expected = tx.get('relay_pretransition')
    if expected and any(original.get(key) != expected.get(key) for key in
                         ('Id', 'Image', 'Name')):
        raise ValueError('Original STT relay changed after promotion preflight')
    if expected and restart_policy(original) != expected.get('restart'):
        raise ValueError('Original STT relay restart policy changed after promotion preflight')
    backup = 'gb10-whisper-http-before-cpp-' + tx['revision'][:12]
    if inspect(backup):
        raise ValueError('Previous STT relay backup name is already occupied')
    tx['relay_original'] = {
        'id': original['Id'], 'image': original['Image'],
        'restart': restart_policy(original), 'name': '/gb10-whisper-http',
        'backup_name': '/' + backup, 'was_running': True,
    }
    tx['relay_update_attempted'] = True
    save_cuda_candidate(tx)
    run(['docker', 'update', '--restart', 'no', original['Id']])
    run(['docker', 'stop', '--time', '30', original['Id']], timeout=45)
    run(['docker', 'rename', original['Id'], backup])
    compose('--profile', 'cpp-production', 'up', '-d', '--no-build', '--pull', 'never',
            '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '60',
            'whisper-cpp-http', timeout=90,
            relay_image=tx['relay_image'])
    promoted = inspect('gb10-whisper-cpp-http')
    labels = (promoted or {}).get('Config', {}).get('Labels') or {}
    if (not promoted or promoted.get('Image') != tx['relay_candidate_image_id'] or
            labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or
            labels.get('com.docker.compose.service') != 'whisper-cpp-http' or
            promoted.get('State', {}).get('Health', {}).get('Status') != 'healthy'):
        raise ValueError('Promoted STT relay identity or health differs')
    tx['relay_candidate_id'] = promoted['Id']
    tx['relay_candidate_exposed'] = True
    run(['docker', 'update', '--restart', 'no', promoted['Id']])
    save_cuda_candidate(tx)


def restore_promoted_relay(tx, stage):
    original = tx.get('relay_original')
    if not isinstance(original, dict) or not original.get('id'):
        raise ValueError('Original STT relay identity is missing; rollback held')
    current = inspect('gb10-whisper-cpp-http')
    if current and current.get('Id') != original['id']:
        labels = current.get('Config', {}).get('Labels') or {}
        if (current.get('Id') != tx.get('relay_candidate_id') or
                current.get('Image') != tx.get('relay_candidate_image_id') or
                labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or
                labels.get('com.docker.compose.service') != 'whisper-cpp-http'):
            raise ValueError('Current STT relay is not the journaled promoted container; rollback held')
        stage('remove-promoted-relay')
        run(['docker', 'update', '--restart', 'no', current['Id']])
        if current.get('State', {}).get('Running'):
            run(['docker', 'stop', '--time', '30', current['Id']], timeout=45)
        run(['docker', 'rm', current['Id']])
    prior = inspect(original['id'])
    if not prior or prior.get('Image') != original['image']:
        raise ValueError('Exact original STT relay container is unavailable; rollback held')
    name = prior.get('Name')
    if name == original['backup_name']:
        stage('restore-original-relay')
        run(['docker', 'rename', original['id'], 'gb10-whisper-http'])
    elif name != original['name']:
        raise ValueError('Original STT relay name changed; rollback held')
    run(['docker', 'update', '--restart', original['restart'], original['id']])
    if original.get('was_running') and not prior.get('State', {}).get('Running'):
        run(['docker', 'start', original['id']])
    if original.get('was_running'):
        stage('wait-relay-health')
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            restored = inspect(original['id'])
            if restored and restored.get('State', {}).get('Running') and \
                    restored.get('State', {}).get('Health', {}).get('Status') == 'healthy':
                return
            time.sleep(2)
        raise ValueError('Exact original STT relay did not become healthy')


def cuda_candidate_failure(tx, error, *, stage=None, rollback=False):
    failure_stage = stage or (tx.get('rollback_stage') if rollback else tx.get('candidate_stage'))
    allowed = CUDA_ROLLBACK_STAGES if rollback else CUDA_CANDIDATE_STAGES
    if failure_stage not in allowed:
        failure_stage = 'preflight'
    command = safe_command_metadata(error)
    if rollback:
        tx.update(state='rollback-failed', rollback_status='failed',
                  rollback_failure_stage=failure_stage,
                  rollback_failure_class=safe_class_name(error),
                  rollback_failure_classification=(error.classification
                      if isinstance(error, ManagedCommandFailure) and
                      error.classification in FAILURE_CLASSIFICATIONS else
                      classify_diagnostic('', stage=failure_stage)),
                  rollback_failure_executable=command['executable'],
                  rollback_failure_exit_code=command['exit_code'])
    else:
        diagnostic_id = os.urandom(6).hex()
        private_text = (error.private_diagnostic if isinstance(error, ManagedCommandFailure)
                        else bounded_private_excerpt(error))
        diagnostic = {
            'diagnostic_id': diagnostic_id, 'failure_stage': failure_stage,
            'failure_class': safe_class_name(error),
            'failure_classification': (error.classification if isinstance(error, ManagedCommandFailure)
                and error.classification != 'unknown' else classify_diagnostic('', stage=failure_stage)),
            'command_executable': command['executable'], 'command_exit_code': command['exit_code'],
            'command_diagnostic_excerpt': private_text,
        }
        try:
            write_candidate_diagnostic(diagnostic_id, diagnostic)
            diagnostic_available = True
        except Exception:
            diagnostic_available = False
        tx.update(state='rollback-pending', failure_stage=failure_stage,
                  failure_class=safe_class_name(error), failure_executable=command['executable'],
                  failure_exit_code=command['exit_code'], failure_id=diagnostic_id,
                  failure_classification=diagnostic['failure_classification'],
                  failure_diagnostic_available=diagnostic_available, rollback_status='pending')
        if isinstance(error, PromotedRelaySmokeFailure):
            tx.update(failure_probe=error.probe, failure_probe_error=error.error,
                      recording_adapter_error=error.adapter_error,
                      recording_relay_error=error.relay_error,
                      failure_http_status=error.status)
    try:
        save_cuda_candidate(tx)
    except Exception:
        pass
    return {key: tx.get(key) for key in (
        'failure_id', 'failure_stage', 'failure_class', 'failure_executable', 'failure_exit_code',
        'failure_classification', 'failure_diagnostic_available', 'rollback_status',
        'failure_probe', 'failure_probe_error', 'recording_adapter_error',
        'recording_relay_error', 'failure_http_status',
        'rollback_failure_stage', 'rollback_failure_class',
        'rollback_failure_classification',
        'rollback_failure_executable', 'rollback_failure_exit_code')}


class CudaCandidateFailure(RuntimeError):
    def __init__(self, metadata):
        self.metadata = metadata
        super().__init__('Whisper.cpp CUDA candidate qualification failed')


def cuda_candidate_rollback(tx, *, idle_timeout=None):
    def stage(name):
        if name not in CUDA_ROLLBACK_STAGES:
            raise ValueError('Unknown CUDA candidate rollback stage')
        tx['rollback_stage'] = name
        save_cuda_candidate(tx)

    stage('inspect-candidate')
    candidate = inspect(CUDA_CANDIDATE)
    if candidate:
        labels = candidate.get('Config', {}).get('Labels') or {}
        if (candidate.get('Image') != tx.get('candidate_image_id') or
                labels.get('com.docker.compose.project') != CUDA_CANDIDATE_PROJECT or
                labels.get('com.docker.compose.service') != 'whisper-cpp-cuda'):
            raise ValueError('Whisper.cpp CUDA candidate ownership changed; rollback held')
        if cuda_candidate_was_exposed(tx):
            tx['candidate_exposed'] = True
            save_cuda_candidate(tx)
            if idle_timeout is None:
                idle_timeout = min(int(tx.get('native_timeout_seconds', 900)) + 120, 18_000)
            deadline = time.monotonic() + idle_timeout
            stable = 0
            check = ('import json,urllib.request; d=json.load(urllib.request.urlopen('
                     '"http://127.0.0.1:8000/health",timeout=2)); '
                     'print(json.dumps({"busy":d.get("inference_busy")}))')
            while time.monotonic() < deadline:
                stage('wait-candidate-idle')
                try:
                    result = run(['docker', 'exec', CUDA_CANDIDATE, 'python3', '-c', check], timeout=5)
                    health = json.loads(result.stdout)
                    connections = run(['ss', '-Htn', 'state', 'established',
                                       '( sport = :8003 or sport = :8007 )'], timeout=5)
                    no_connections = not connections.stdout.strip()
                    stable = stable + 1 if health == {'busy': False} and no_connections else 0
                except Exception:
                    # Unknown, malformed or unavailable health is never considered idle.
                    stable = 0
                if stable >= 3:
                    break
                time.sleep(1)
            else:
                raise ValueError('Whisper.cpp CUDA candidate did not become idle; rollback held')
        stage('disable-candidate-restart')
        run(['docker', 'update', '--restart', 'no', candidate['Id']])
        if candidate['State']['Running']:
            stage('stop-candidate')
            run(['docker', 'stop', '--time', '180', candidate['Id']], timeout=210)
        stage('remove-candidate')
        run(['docker', 'rm', candidate['Id']])
    if inspect(CUDA_CANDIDATE):
        raise ValueError('Whisper.cpp CUDA candidate remains after project cleanup')
    stage('inspect-network')
    network_ids = run(['docker', 'network', 'ls', '-q', '--filter',
                       'label=com.docker.compose.project=' + CUDA_CANDIDATE_PROJECT]).stdout.split()
    for network_id in network_ids:
        network = json.loads(run(['docker', 'network', 'inspect', network_id]).stdout)[0]
        labels = network.get('Labels') or {}
        if (network.get('Name') != CUDA_CANDIDATE_PROJECT + '_candidate' or
                labels.get('com.docker.compose.project') != CUDA_CANDIDATE_PROJECT or
                labels.get('com.docker.compose.network') != 'candidate' or
                not network.get('Internal') or network.get('Containers')):
            raise ValueError('Whisper.cpp CUDA candidate network ownership changed; cleanup held')
        stage('remove-network')
        run(['docker', 'network', 'rm', network_id])
    stage('inspect-primary')
    original = inspect(tx['primary_id'])
    if (not original or original.get('Image') != tx['primary_image'] or
            original.get('Name') != '/gb10-whisper-primary'):
        raise ValueError('Original Whisper primary changed or disappeared; rollback held')
    stage('restore-primary-restart')
    run(['docker', 'update', '--restart', tx['primary_restart'], tx['primary_id']])
    if not original['State']['Running']:
        stage('start-primary')
        run(['docker', 'start', tx['primary_id']])
    stage('wait-primary-health')
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        current = inspect(tx['primary_id'])
        if (current and current['State']['Running'] and
                current.get('State', {}).get('Health', {}).get('Status') == 'healthy'):
            if tx.get('relay_update_attempted'):
                stage('restore-relay')
                restore_promoted_relay(tx, stage)
            tx.update(state='rolled-back', rollback_status='completed')
            stage('complete')
            return
        time.sleep(2)
    raise ValueError('Original Whisper primary did not become healthy; rollback is incomplete')


def cuda_candidate_key_check_program():
    return ('import os,stat,sys; p=sys.argv[1] if len(sys.argv)>1 else "/run/secrets/whisper_cpp_api_key"; i=os.stat(p,follow_symlinks=False); '
            'assert stat.S_ISREG(i.st_mode) and stat.S_IMODE(i.st_mode)==0o440; '
            'b=open(p,"rb").read(); assert 16<=len(b)<=512 and b.strip() and b"\\n" not in b and b"\\r" not in b; '
            'print("Qualification credential verified")')


def cuda_candidate_smoke_program():
    return '''import io,json,os,urllib.request,wave
key_path=os.environ.get('WHISPER_CPP_API_KEY_FILE','/run/secrets/whisper_cpp_api_key')
key=open(key_path,'rb').read().decode('ascii').strip()
audio=io.BytesIO()
with wave.open(audio,'wb') as w:
 w.setparams((1,2,16000,0,'NONE','PCM'));w.writeframes(bytes(32000))
crlf=bytes((13,10))
body=(b'--smoke'+crlf+b'Content-Disposition: form-data; name="model"'+crlf+crlf+b'whisper-large-v3'+crlf
      +b'--smoke'+crlf+b'Content-Disposition: form-data; name="response_format"'+crlf+crlf+b'json'+crlf
      +b'--smoke'+crlf+b'Content-Disposition: form-data; name="file"; filename="audio.wav"'+crlf+b'Content-Type: audio/wav'+crlf+crlf
      +audio.getvalue()+crlf+b'--smoke--'+crlf)
r=urllib.request.Request('http://127.0.0.1:8000/v1/audio/transcriptions',body,
 headers={'Authorization':'Bearer '+key,'Content-Type':'multipart/form-data; boundary=smoke'})
d=json.load(urllib.request.urlopen(r,timeout=120))
assert isinstance(d.get('text'),str)
print('Authenticated CUDA candidate smoke passed; content withheld')
'''


def cuda_candidate_smoke():
    code = cuda_candidate_smoke_program()
    run(['docker', 'exec', CUDA_CANDIDATE, 'python3', '-c', code], timeout=150)


def cuda_candidate_start(revision, *, max_audio_seconds=180, max_upload_bytes=6_000_000,
                         native_timeout_seconds=900, promotion=False, relay_image=None,
                         relay_image_id=None, maintenance_bundle_sha256=None):
    guards()
    ensure_no_cuda_candidate_transaction('cuda-candidate')
    validate_cuda_request_limits(max_audio_seconds, max_upload_bytes, native_timeout_seconds)
    if promotion and not re.fullmatch(r'[0-9a-f]{64}', str(maintenance_bundle_sha256 or '')):
        raise ValueError('Verified Spark maintenance ownership bundle is required for promotion')
    base_tx_path = STATE / 'transaction.json'
    protected(base_tx_path)
    base_tx = json.loads(base_tx_path.read_text())
    if base_tx.get('state') not in {'applied', 'legacy-retired'}:
        raise ValueError('The full Whisper lifecycle must be applied or legacy-retired before CUDA qualification')
    check_cuda_host()
    primary = inspect(NEW[0])
    if (not primary or not primary['State']['Running'] or
            primary.get('State', {}).get('Health', {}).get('Status') != 'healthy' or
            MODEL not in ' '.join(primary['Config'].get('Cmd') or []) or
            REVISION not in ' '.join(primary['Config'].get('Cmd') or []) or
            primary.get('Config', {}).get('Image') !=
            'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d'):
        raise ValueError('Healthy pinned full Whisper primary must be restored before CUDA qualification')
    relay_pretransition = None
    if promotion:
        relay = inspect('gb10-whisper-http')
        relay_labels = (relay or {}).get('Config', {}).get('Labels') or {}
        if (not relay or not relay.get('State', {}).get('Running') or
                relay.get('State', {}).get('Health', {}).get('Status') != 'healthy' or
                relay.get('Name') != '/gb10-whisper-http' or
                relay.get('Config', {}).get('Image') != 'homecompute/stt-http:2.0.0' or
                relay_labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or
                relay_labels.get('com.docker.compose.service') != 'whisper-http'):
            raise ValueError('Healthy owned original STT relay is required before promotion')
        relay_pretransition = {'Id': relay['Id'], 'Image': relay['Image'],
                               'Name': relay['Name'], 'restart': restart_policy(relay)}
    if inspect(CANDIDATE) or inspect(CUDA_CANDIDATE):
        raise ValueError('Another Whisper candidate container exists; inspect and rollback it first')
    prior = STATE / 'runtime-candidate.json'
    if prior.exists() or prior.is_symlink():
        protected(prior)
        if json.loads(prior.read_text()).get('state') != 'rolled-back':
            raise ValueError('The existing vLLM candidate transaction must be rolled back first')
    image_id = qualified_cuda_candidate_image_id() if promotion else cuda_candidate_image_id(revision)
    api_key_path = CPP_ROLE_API_KEY_PATH if promotion else CPP_API_KEY_PATH
    image_source_revision = CPP_QUALIFIED_SOURCE_REVISION if promotion else revision
    validate_cuda_artifacts(image_id, image_source_revision, api_key_path=api_key_path)
    journal = STATE / 'cuda-candidate.json'
    if journal.exists() or journal.is_symlink():
        protected(journal)
        previous = json.loads(journal.read_text())
        if previous.get('state') != 'rolled-back':
            raise ValueError('Existing CUDA candidate transaction requires rollback/status review')
        journal.rename(STATE / ('cuda-candidate-rolled-back-' + os.urandom(6).hex() + '.json'))
    chain = 'HC-STT-' + os.urandom(6).hex()
    tx = {'revision': revision, 'state': 'prepared', 'chain': chain,
          'candidate_image_id': image_id, 'primary_id': primary['Id'],
          'primary_image': primary['Image'], 'primary_restart': restart_policy(primary),
          'model_sha256': CPP_MODEL_SHA256, 'runtime_commit': CPP_RUNTIME_COMMIT,
          'native_timeout_seconds': native_timeout_seconds,
          'promotion': promotion, 'image_source_revision': image_source_revision,
          'maintenance_bundle_sha256': maintenance_bundle_sha256 if promotion else None,
          'relay_pretransition': relay_pretransition,
          'relay_image': relay_image if promotion else None,
          'relay_candidate_image_id': relay_image_id if promotion else None}
    save_cuda_candidate(tx)
    gate_attempted = False
    failure = None
    try:
        cuda_candidate_stage(tx, 'install-intake-gate')
        gate_attempted = True
        gate(chain, True)
        tx['gate_held'] = True
        save_cuda_candidate(tx)
        cuda_candidate_stage(tx, 'drain-primary')
        quiet([primary])
        cuda_candidate_stage(tx, 'stop-primary')
        run(['docker', 'update', '--restart', 'no', primary['Id']])
        run(['docker', 'stop', '--time', '180', primary['Id']], timeout=210)
        cuda_candidate_stage(tx, 'pre-load-memory-check')
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(memory['MemAvailable'].split()[0]) < 12 * 1024 * 1024:
            raise ValueError('Less than 12 GiB available before CUDA candidate model load')
        tx['state'] = 'primary-stopped'; save_cuda_candidate(tx)
        cuda_candidate_stage(tx, 'candidate-start')
        compose_cuda('--profile', 'cuda-candidate', 'up', '-d', '--no-build', '--pull', 'never',
                     '--wait', '--wait-timeout', '900', 'whisper-cpp-cuda',
                     revision=revision, image_source_revision=image_source_revision,
                     api_key_path=api_key_path, production_network=promotion,
                     image_id=image_id, timeout=1200,
                     max_audio_seconds=max_audio_seconds, max_upload_bytes=max_upload_bytes,
                     native_timeout_seconds=native_timeout_seconds)
        cuda_candidate_stage(tx, 'candidate-health-check')
        candidate = inspect(CUDA_CANDIDATE)
        labels = candidate.get('Config', {}).get('Labels') or {} if candidate else {}
        if (not candidate or candidate.get('Image') != image_id or not candidate['State']['Running'] or
                candidate.get('State', {}).get('Health', {}).get('Status') != 'healthy' or
                labels.get('com.docker.compose.project') != CUDA_CANDIDATE_PROJECT or
                labels.get('com.docker.compose.service') != 'whisper-cpp-cuda'):
            raise ValueError('Whisper.cpp CUDA candidate failed image, ownership or health verification')
        health = run(['docker', 'exec', CUDA_CANDIDATE, 'python3', '-c',
                      'import json,urllib.request;print(json.dumps(json.load(urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=3))))'], timeout=5)
        metadata = json.loads(health.stdout)
        if (metadata.get('status') != 'ok' or metadata.get('device') != 'cuda' or
                metadata.get('compute_backend') != 'cuda' or
                metadata.get('strict_gpu_patch') != CPP_CUDA_PATCH_VERSION or
                metadata.get('strict_gpu_patcher_sha256') != CPP_CUDA_PATCHER_SHA256 or
                type(metadata.get('native_inference_busy')) is not bool or
                not isinstance(metadata.get('gpu_model_buffer_bytes'), int) or
                isinstance(metadata.get('gpu_model_buffer_bytes'), bool) or
                metadata.get('gpu_model_buffer_bytes', 0) <= 0 or
                metadata.get('model_sha256') != CPP_MODEL_SHA256 or
                metadata.get('runtime_commit') != CPP_RUNTIME_COMMIT or
                metadata.get('homecompute_revision') != image_source_revision or metadata.get('image_id') != image_id):
            raise ValueError('Whisper.cpp CUDA health provenance differs')
        cuda_candidate_stage(tx, 'synthetic-candidate-smoke')
        tx['candidate_exposed'] = True
        save_cuda_candidate(tx)
        cuda_candidate_smoke()
        cuda_candidate_stage(tx, 'post-load-memory-check')
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(memory['MemAvailable'].split()[0]) / int(memory['MemTotal'].split()[0]) < 0.10:
            raise ValueError('CUDA candidate failed the 10 percent host headroom gate')
        if promotion:
            cuda_candidate_stage(tx, 'recording-relay-update')
            promote_relay(tx)
            cuda_candidate_stage(tx, 'recording-relay-smoke')
            promoted_relay_smoke()
            cuda_candidate_stage(tx, 'persist-candidate')
            run(['docker', 'update', '--restart', 'unless-stopped', CUDA_CANDIDATE])
            run(['docker', 'update', '--restart', 'unless-stopped', tx['relay_candidate_id']])
            tx['candidate_restart'] = 'unless-stopped'
            tx['relay_candidate_restart'] = 'unless-stopped'
        tx['state'] = 'promoted' if promotion else 'candidate-running'
        cuda_candidate_stage(tx, 'candidate-running')
    except BaseException as error:
        failure = cuda_candidate_failure(tx, error)
        try:
            cuda_candidate_rollback(tx)
            tx['failure_rollback_status'] = tx.get('rollback_status', 'completed')
            save_cuda_candidate(tx)
        except BaseException as rollback_error:
            failure = cuda_candidate_failure(tx, rollback_error, rollback=True)
    finally:
        if gate_attempted:
            release_safe = tx.get('state') in {'rolled-back', 'candidate-running', 'promoted'}
            if release_safe:
                try:
                    gate(chain, False)
                    tx['gate_held'] = False
                    save_cuda_candidate(tx)
                except BaseException as cleanup_error:
                    tx['gate_cleanup_failure_class'] = safe_class_name(cleanup_error)
                    try:
                        tx['gate_held'] = gate_is_active(chain)
                    except BaseException:
                        tx['gate_held'] = None
                    if failure is None:
                        failure = cuda_candidate_failure(tx, cleanup_error, stage='gate-cleanup')
                    try:
                        save_cuda_candidate(tx)
                    except Exception:
                        pass
            else:
                # An incomplete or busy-held rollback must keep intake closed.
                try:
                    gate(chain, True)
                    tx['gate_held'] = True
                except BaseException:
                    try:
                        tx['gate_held'] = gate_is_active(chain)
                    except BaseException:
                        tx['gate_held'] = None
                try:
                    save_cuda_candidate(tx)
                except Exception:
                    pass
    if failure is not None:
        failure = public_cuda_failure_metadata(tx)
        raise CudaCandidateFailure(failure) from None
    if promotion:
        print(json.dumps({'state': 'promoted', 'image_id': image_id,
                          'image_source_revision': image_source_revision,
                          'model_sha256': CPP_MODEL_SHA256}, sort_keys=True), flush=True)
    else:
        print('Whisper.cpp CUDA candidate active for qualification; run cuda-candidate-rollback afterward', flush=True)


def ensure_spark_maintenance_ownership():
    source = ROOT / 'scripts'
    installer = source / 'setup-infrastructure-maintenance.py'
    command = [sys.executable, '-I', '-B', str(installer), '--source', str(source)]
    try:
        preview = json.loads(run(command, timeout=120).stdout)
        if not isinstance(preview, dict):
            raise ValueError('Maintenance bundle preview is invalid')
        digest = preview.get('bundle_sha256')
        if (preview.get('installed') is not False or
                not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError('Source maintenance bundle was not pinned')
        installed = json.loads(run([*command, '--bundle-sha256', digest], timeout=240).stdout)
        if not isinstance(installed, dict):
            raise ValueError('Maintenance installer receipt is invalid')
        if (installed.get('host') != 'home-spark' or installed.get('installed') is not True or
                installed.get('root_key_installed') is not False or
                type(installed.get('updates_run')) is not int or installed['updates_run'] != 0):
            raise ValueError('Spark maintenance owners were not installed without key changes')
        verified = json.loads(run([*command, '--verify-bundle-sha256', digest], timeout=120).stdout)
        if not isinstance(verified, dict):
            raise ValueError('Maintenance verification receipt is invalid')
        if (verified.get('host') != 'home-spark' or verified.get('verified') is not True or
                verified.get('bundle_sha256') != digest):
            raise ValueError('Installed Spark maintenance owners do not match the published bundle')
        return digest
    except (ManagedCommandFailure, OSError, ValueError, TypeError, KeyError):
        raise ValueError('Spark maintenance ownership install or verification failed; model transition refused') from None


def promote_qualified_cuda_candidate(revision):
    guards()
    qualified_cuda_candidate_image_id()
    recover_pretransition_promotion_failure(revision)
    provision_cpp_role_key()
    verify_cpp_model(CPP_MODEL_PATH)
    relay_image = 'homecompute/stt-http:2.0.0-cpp-' + revision[:12]
    compose('--profile', 'cpp-production', 'build', 'whisper-cpp-http', relay_image=relay_image)
    relay_image_id = json.loads(run(['docker', 'image', 'inspect', relay_image]).stdout)[0]['Id']
    maintenance_bundle_sha256 = ensure_spark_maintenance_ownership()
    cuda_candidate_start(revision, max_audio_seconds=1200, max_upload_bytes=40_000_000,
                         native_timeout_seconds=1800, promotion=True,
                         relay_image=relay_image, relay_image_id=relay_image_id,
                         maintenance_bundle_sha256=maintenance_bundle_sha256)


def recover_pretransition_promotion_failure(revision):
    """Recover only a gate-install failure before either inference service changed."""
    journal = STATE / 'cuda-candidate.json'
    if not journal.exists() and not journal.is_symlink():
        return
    protected(journal)
    tx = json.loads(journal.read_text())
    if tx.get('state') == 'rolled-back' and tx.get('failure_stage') != 'install-intake-gate':
        return
    eligible_states = {'rollback-pending', 'rolled-back'}
    rollback_stage = tx.get('rollback_stage')
    if (tx.get('revision') != CPP_GATE_FAILURE_RECOVERY_REVISION or
            tx.get('revision') == revision or tx.get('state') not in eligible_states or
            tx.get('promotion') is not True or
            tx.get('failure_stage') != 'install-intake-gate' or
            tx.get('candidate_stage') != 'install-intake-gate' or
            tx.get('failure_id') != CPP_GATE_FAILURE_RECOVERY_ID or
            (tx.get('state') == 'rollback-pending' and rollback_stage is not None) or
            (tx.get('state') == 'rolled-back' and
             (rollback_stage != 'complete' or tx.get('rollback_status') != 'completed')) or
            tx.get('candidate_exposed') is True or tx.get('relay_update_attempted') is True or
            tx.get('candidate_image_id') != CPP_QUALIFIED_IMAGE_ID or
            tx.get('image_source_revision') != CPP_QUALIFIED_SOURCE_REVISION or
            not re.fullmatch(r'HC-STT-[0-9a-f]{12}', str(tx.get('chain') or ''))):
        raise ValueError('Existing CUDA transaction is not a recoverable pre-transition gate failure')

    primary = inspect(tx.get('primary_id', ''))
    if (not primary or primary.get('Id') != tx.get('primary_id') or
            primary.get('Image') != tx.get('primary_image') or
            primary.get('Name') != '/gb10-whisper-primary' or
            not primary.get('State', {}).get('Running') or
            primary.get('State', {}).get('Health', {}).get('Status') != 'healthy' or
            restart_policy(primary) != tx.get('primary_restart') or
            MODEL not in ' '.join(primary.get('Config', {}).get('Cmd') or []) or
            REVISION not in ' '.join(primary.get('Config', {}).get('Cmd') or []) or
            primary.get('Config', {}).get('Image') !=
            'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d'):
        raise ValueError('Original pinned Whisper primary is not unchanged and healthy; recovery held')
    if inspect(CUDA_CANDIDATE) or inspect(CANDIDATE) or inspect('gb10-whisper-cpp-http'):
        raise ValueError('A Whisper.cpp service exists; pre-transition recovery held')
    relay = inspect('gb10-whisper-http')
    labels = (relay or {}).get('Config', {}).get('Labels') or {}
    if (not relay or not relay.get('State', {}).get('Running') or
            relay.get('State', {}).get('Health', {}).get('Status') != 'healthy' or
            relay.get('Name') != '/gb10-whisper-http' or
            relay.get('Image') != CPP_GATE_FAILURE_RELAY_IMAGE or
            restart_policy(relay) != 'unless-stopped' or
            relay.get('Config', {}).get('Image') != 'homecompute/stt-http:2.0.0' or
            labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or
            labels.get('com.docker.compose.service') != 'whisper-http'):
        raise ValueError('Original STT relay is not unchanged and healthy; recovery held')
    relay_snapshot = tx.get('relay_pretransition')
    if relay_snapshot and (relay.get('Id') != relay_snapshot.get('Id') or
                           relay.get('Image') != relay_snapshot.get('Image') or
                           relay.get('Name') != relay_snapshot.get('Name') or
                           restart_policy(relay) != relay_snapshot.get('restart')):
        raise ValueError('Journaled original STT relay identity changed; recovery held')

    # The nonce chain may be absent or may have been partially created. `gate`
    # first validates exact chain ownership against successful iptables listings.
    gate(tx['chain'], False)
    if gate_is_active(tx['chain']):
        raise ValueError('Prior intake gate remains active; recovery held')
    tx.update(state='rolled-back', rollback_status='completed',
              failure_rollback_status='completed', gate_held=False,
              pretransition_gate_recovered=True)
    save_cuda_candidate(tx)


def cuda_candidate_stop(revision, *, idle_timeout=None):
    journal = STATE / 'cuda-candidate.json'
    protected(journal)
    tx = json.loads(journal.read_text())
    if tx.get('revision') != revision:
        raise ValueError('No matching recoverable Whisper.cpp CUDA candidate transaction')
    chain = tx['chain']
    if tx.get('state') == 'rolled-back' and not gate_is_active(chain):
        raise ValueError('No matching recoverable Whisper.cpp CUDA candidate transaction')
    gate(chain, True)
    tx['gate_held'] = True
    save_cuda_candidate(tx)
    completed = False
    failure = None
    try:
        cuda_candidate_rollback(tx, idle_timeout=idle_timeout)
        completed = tx.get('state') == 'rolled-back'
    except BaseException as error:
        failure = cuda_candidate_failure(tx, error, rollback=True)
        tx['gate_held'] = True
        save_cuda_candidate(tx)
    if completed:
        try:
            gate(chain, False)
            tx['gate_held'] = False
            save_cuda_candidate(tx)
        except BaseException as cleanup_error:
            tx['gate_cleanup_failure_class'] = safe_class_name(cleanup_error)
            try:
                tx['gate_held'] = gate_is_active(chain)
            except BaseException:
                tx['gate_held'] = None
            save_cuda_candidate(tx)
            if failure is None:
                failure = cuda_candidate_failure(tx, cleanup_error, stage='gate-cleanup')
    if failure is not None:
        raise CudaCandidateFailure(failure) from None
    print('Whisper.cpp CUDA candidate stopped; exact original Whisper primary restored', flush=True)


def cuda_candidate_status(revision):
    journal = STATE / 'cuda-candidate.json'
    if not journal.exists() and not journal.is_symlink():
        print(json.dumps({'revision': revision, 'state': 'absent'}, sort_keys=True))
        return
    protected(journal)
    tx = json.loads(journal.read_text())
    if tx.get('revision') != revision:
        raise ValueError('CUDA candidate transaction belongs to another release')
    state = tx.get('state')
    candidate = inspect(CUDA_CANDIDATE)
    candidate_owned = bool(candidate and candidate.get('Image') == tx.get('candidate_image_id') and
                           (candidate.get('Config', {}).get('Labels') or {}).get('com.docker.compose.project') == CUDA_CANDIDATE_PROJECT and
                           (candidate.get('Config', {}).get('Labels') or {}).get('com.docker.compose.service') == 'whisper-cpp-cuda')
    relay = inspect('gb10-whisper-cpp-http') if tx.get('relay_candidate_id') else None
    relay_owned = bool(relay and relay.get('Id') == tx.get('relay_candidate_id') and
                       relay.get('Image') == tx.get('relay_candidate_image_id') and
                       (relay.get('Config', {}).get('Labels') or {}).get('com.docker.compose.project') == 'whisper-large-v3-stt' and
                       (relay.get('Config', {}).get('Labels') or {}).get('com.docker.compose.service') == 'whisper-cpp-http')
    original_relay = tx.get('relay_original')
    original_relay_item = inspect(original_relay['id']) if isinstance(original_relay, dict) and original_relay.get('id') else None
    original_relay_retained = bool(original_relay_item and
        original_relay_item.get('Id') == original_relay.get('id') and
        original_relay_item.get('Image') == original_relay.get('image') and
        original_relay_item.get('Name') in {original_relay.get('name'), original_relay.get('backup_name')})
    try:
        current_gate_held = gate_is_active(tx.get('chain', ''))
    except Exception:
        current_gate_held = None
    safe_classes = {'ValueError', 'RuntimeError', 'ManagedCommandFailure', 'PromotedRelaySmokeFailure', 'KeyboardInterrupt',
                    'InterruptedError', 'OSError', 'TimeoutExpired', 'PermissionError',
                    'FileNotFoundError', 'CalledProcessError', 'JSONDecodeError'}
    def safe_class(value):
        return value if value in safe_classes else None
    def safe_exit(value):
        return value if isinstance(value, int) and -255 <= value <= 255 else None
    print(json.dumps({
        'revision': revision,
        'state': state if state in {
            'prepared', 'primary-stopped', 'candidate-running', 'promoted', 'rollback-pending',
            'rolled-back', 'rollback-failed'} else 'unknown',
        'desired_backend': 'whisper-cpp-cuda' if state == 'promoted' else
                           'whisper-large-v3' if state == 'rolled-back' else 'transition',
        'candidate_running': bool(candidate_owned and candidate.get('State', {}).get('Running')),
        'candidate_restart_policy': restart_policy(candidate) if candidate_owned else None,
        'promoted_relay_running': bool(relay_owned and relay.get('State', {}).get('Running')),
        'promoted_relay_healthy': bool(relay_owned and relay.get('State', {}).get('Health', {}).get('Status') == 'healthy'),
        'promoted_relay_restart_policy': restart_policy(relay) if relay_owned else None,
        'maintenance_bundle_sha256': tx.get('maintenance_bundle_sha256')
            if re.fullmatch(r'[0-9a-f]{64}', str(tx.get('maintenance_bundle_sha256') or '')) else None,
        'original_relay_retained': original_relay_retained,
        'gate_held': current_gate_held,
        'candidate_stage': tx.get('candidate_stage') if tx.get('candidate_stage') in CUDA_CANDIDATE_STAGES else None,
        'candidate_exposed': cuda_candidate_was_exposed(tx),
        'failure_id': tx.get('failure_id') if re.fullmatch(r'[0-9a-f]{12}', str(tx.get('failure_id') or '')) else None,
        'failure_stage': tx.get('failure_stage') if tx.get('failure_stage') in CUDA_CANDIDATE_STAGES else None,
        'failure_class': safe_class(tx.get('failure_class')),
        'failure_classification': tx.get('failure_classification') if tx.get('failure_classification') in FAILURE_CLASSIFICATIONS else 'unknown',
        'failure_diagnostic_available': bool(tx.get('failure_diagnostic_available')),
        'failure_probe': tx.get('failure_probe') if tx.get('failure_probe') in {'recording-adapter', 'recording-relay', 'standard-relay'} or
            tx.get('failure_probe') == 'unknown' and tx.get('failure_probe_error') == 'smoke_receipt_invalid' else None,
        'failure_probe_error': tx.get('failure_probe_error') if tx.get('failure_probe_error') in {
            'timestamp_out_of_bounds', 'invalid_verbose_json', 'invalid_segment', 'invalid_text',
            'backend_unavailable', 'backend_rejected', 'audio_duration_exceeded', 'audio_format_rejected',
            'missing_provenance', 'upstream_rejected', 'upstream_unavailable', 'recording_unavailable',
            'http_error_unclassified', 'provenance_mismatch', 'response_shape_invalid', 'timeout',
            'connection_error', 'response_or_connection_invalid', 'inference_idle_unconfirmed',
            'smoke_receipt_invalid'} else None,
        'recording_adapter_error': tx.get('recording_adapter_error') if tx.get('recording_adapter_error') in {
            'none', 'timestamp_out_of_bounds', 'invalid_verbose_json', 'invalid_segment', 'invalid_text',
            'backend_unavailable', 'backend_rejected', 'audio_duration_exceeded', 'audio_format_rejected',
            'missing_provenance', 'upstream_rejected', 'upstream_unavailable', 'recording_unavailable',
            'http_error_unclassified', 'provenance_mismatch', 'response_shape_invalid', 'timeout',
            'connection_error', 'response_or_connection_invalid', 'inference_idle_unconfirmed'} else None,
        'recording_relay_error': tx.get('recording_relay_error') if tx.get('recording_relay_error') in {
            'none', 'timestamp_out_of_bounds', 'invalid_verbose_json', 'invalid_segment', 'invalid_text',
            'backend_unavailable', 'backend_rejected', 'audio_duration_exceeded', 'audio_format_rejected',
            'missing_provenance', 'upstream_rejected', 'upstream_unavailable', 'recording_unavailable',
            'http_error_unclassified', 'provenance_mismatch', 'response_shape_invalid', 'timeout',
            'connection_error', 'response_or_connection_invalid', 'inference_idle_unconfirmed'} else None,
        'failure_http_status': tx.get('failure_http_status') if isinstance(tx.get('failure_http_status'), int) and not isinstance(tx.get('failure_http_status'), bool) and 100 <= tx['failure_http_status'] <= 599 else None,
        'failure_executable': tx.get('failure_executable') if tx.get('failure_executable') in SAFE_EXECUTABLES else None,
        'failure_exit_code': safe_exit(tx.get('failure_exit_code')),
        'rollback_status': tx.get('rollback_status') if tx.get('rollback_status') in {'pending', 'completed', 'failed'} else None,
        'rollback_failure_stage': tx.get('rollback_failure_stage') if tx.get('rollback_failure_stage') in CUDA_ROLLBACK_STAGES else None,
        'rollback_failure_class': safe_class(tx.get('rollback_failure_class')),
        'rollback_failure_classification': tx.get('rollback_failure_classification') if tx.get('rollback_failure_classification') in FAILURE_CLASSIFICATIONS else 'unknown',
        'rollback_failure_executable': tx.get('rollback_failure_executable') if tx.get('rollback_failure_executable') in SAFE_EXECUTABLES else None,
        'rollback_failure_exit_code': safe_exit(tx.get('rollback_failure_exit_code')),
        'gate_cleanup_failure_class': safe_class(tx.get('gate_cleanup_failure_class')),
    }, sort_keys=True))


def format_cuda_candidate_failure(metadata):
    return ('Whisper.cpp CUDA candidate failure'
            f" id={metadata.get('failure_id') or 'unavailable'}"
            f" stage={metadata.get('failure_stage') or 'unknown'}"
            f" class={metadata.get('failure_class') or 'Error'}"
            f" executable={metadata.get('failure_executable') or 'none'}"
            f" exit_code={metadata.get('failure_exit_code')}"
            f" rollback={metadata.get('rollback_status') or 'unknown'}"
            f" rollback_stage={metadata.get('rollback_failure_stage') or 'none'}"
            f" rollback_class={metadata.get('rollback_failure_class') or 'none'}"
            f" rollback_classification={metadata.get('rollback_failure_classification') or 'unknown'}"
            f" probe={metadata.get('failure_probe') or 'none'}"
            f" probe_error={metadata.get('failure_probe_error') or 'none'}"
            f" adapter_error={metadata.get('recording_adapter_error') or 'none'}"
            f" relay_error={metadata.get('recording_relay_error') or 'none'}"
            f" probe_status={metadata.get('failure_http_status')}")


def public_cuda_failure_metadata(tx):
    result = {key: tx.get(key) for key in (
        'failure_id', 'failure_stage', 'failure_class', 'failure_executable',
        'failure_exit_code', 'failure_classification', 'failure_diagnostic_available',
        'rollback_status', 'rollback_failure_stage', 'rollback_failure_class',
        'rollback_failure_classification', 'rollback_failure_executable',
        'rollback_failure_exit_code', 'gate_cleanup_failure_class')}
    probes = {'recording-adapter', 'recording-relay', 'standard-relay'}
    errors = {'none', 'timestamp_out_of_bounds', 'invalid_verbose_json', 'invalid_segment',
              'invalid_text', 'backend_unavailable', 'backend_rejected', 'audio_duration_exceeded',
              'audio_format_rejected', 'missing_provenance', 'upstream_rejected',
              'upstream_unavailable', 'recording_unavailable', 'http_error_unclassified',
              'provenance_mismatch', 'response_shape_invalid', 'timeout', 'connection_error',
              'response_or_connection_invalid', 'inference_idle_unconfirmed', 'smoke_receipt_invalid'}
    if (tx.get('failure_probe') in probes or
            tx.get('failure_probe') == 'unknown' and tx.get('failure_probe_error') == 'smoke_receipt_invalid'):
        result.update(
            failure_probe=tx['failure_probe'],
            failure_probe_error=tx.get('failure_probe_error') if tx.get('failure_probe_error') in errors else None,
            recording_adapter_error=tx.get('recording_adapter_error') if tx.get('recording_adapter_error') in errors else None,
            recording_relay_error=tx.get('recording_relay_error') if tx.get('recording_relay_error') in errors else None,
            failure_http_status=tx.get('failure_http_status') if isinstance(tx.get('failure_http_status'), int)
                and not isinstance(tx.get('failure_http_status'), bool) and 100 <= tx['failure_http_status'] <= 599 else None)
    return result


def parse_iptables_listing(result, *, chain=None):
    """Parse successful iptables -S output; failures never imply absence."""
    if result.returncode != 0:
        raise ValueError('Intake gate state is unavailable')
    parsed = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError:
            raise ValueError('Intake gate listing is malformed') from None
        if (not tokens or tokens[0] not in {'-P', '-N', '-A'} or len(tokens) < 2 or
                tokens[0] == '-P' and len(tokens) != 3 or
                tokens[0] == '-N' and len(tokens) != 2 or
                tokens[0] == '-A' and len(tokens) < 3):
            raise ValueError('Intake gate listing is malformed')
        if chain is None or tokens[1] == chain:
            parsed.append(tokens)
    policies = [row for row in parsed if row[0] == '-P']
    expected_policies = {'INPUT', 'FORWARD', 'OUTPUT'} if chain is None else {chain} if chain == 'OUTPUT' else set()
    if expected_policies and {row[1] for row in policies} != expected_policies:
        raise ValueError('Intake gate listing is incomplete')
    if len(policies) != len(expected_policies):
        raise ValueError('Intake gate listing is incomplete')
    return parsed


def filter_chain_absent(chain):
    listing = run(['iptables', '-w', '-S'], check=False)
    rows = parse_iptables_listing(listing)
    if any(row[1] == chain and row[0] in {'-N', '-A'} for row in rows):
        return False
    return True


def output_jump_present(chain):
    listing = run(['iptables', '-w', '-S', 'OUTPUT'], check=False)
    rows = parse_iptables_listing(listing, chain='OUTPUT')
    matches = []
    for row in rows:
        if row[0] != '-A' or row[1] != 'OUTPUT':
            continue
        targets = [row[index + 1] for index, token in enumerate(row[:-1])
                   if token in {'-j', '--jump', '-g', '--goto'}]
        if chain in targets:
            if row != ['-A', 'OUTPUT', '-j', chain]:
                raise ValueError('Intake gate jump drift requires inspection')
            matches.append(row)
    if len(matches) > 1:
        raise ValueError('Intake gate jump drift requires inspection')
    return bool(matches)


def chain_exists(result, chain):
    if result.returncode == 0:
        rows = parse_iptables_listing(result, chain=chain)
        if not rows or not any(row[0] == '-N' and row[1] == chain for row in rows):
            raise ValueError('Intake gate listing is malformed')
        return True
    if filter_chain_absent(chain):
        return False
    raise ValueError('Intake gate state changed during inspection')


def jump_exists(result, chain):
    if result.returncode == 0:
        if output_jump_present(chain):
            return True
        raise ValueError('Intake gate jump changed during inspection')
    if not output_jump_present(chain):
        return False
    raise ValueError('Intake gate jump changed during inspection')


def owned_gate_rule_ports(rules, chain):
    accepted = set()
    ports = set()
    for port in ('8003', '8007'):
        base = ['-A', chain, '-p', 'tcp', '-m', 'conntrack', '--ctstate', 'NEW',
                '--ctorigdst', '127.0.0.1', '--ctorigdstport', port, '-j', 'REJECT']
        accepted.add(tuple(base))
        accepted.add(tuple(base + ['--reject-with', 'icmp-port-unreachable']))
    rules_only = [tuple(rule) for rule in rules if rule[0] == '-A']
    for rule in rules_only:
        if rule not in accepted:
            return None
        ports.add(rule[rule.index('--ctorigdstport') + 1])
    if len(rules_only) != len(ports):
        return None
    return ports


def gate(chain, install):
    if not re.fullmatch('HC-STT-[0-9a-f]{12}', chain):
        raise ValueError('Invalid intake gate')
    chain_result = run(['iptables', '-w', '-S', chain], check=False)
    exists = chain_exists(chain_result, chain)
    if exists:
        rules = parse_iptables_listing(chain_result, chain=chain)
        valid_rules = owned_gate_rule_ports(rules, chain)
        expected_ports = {'8003', '8007'}
        complete = valid_rules == expected_ports
        valid = valid_rules is not None and (complete if install else True)
        if (len(rules) != len([rule for rule in rules if rule[0] == '-A']) + 1 or not valid):
            raise ValueError('Intake gate drift requires inspection')
    if install:
        if not exists:
            run(['iptables', '-w', '-N', chain])
            for port in ('8003', '8007'):
                # conntrack retains the requested host address/port after Docker DNAT.
                run(['iptables', '-w', '-A', chain, '-p', 'tcp', '-m', 'conntrack', '--ctstate', 'NEW',
                     '--ctorigdst', '127.0.0.1', '--ctorigdstport', port, '-j', 'REJECT'])
        jump = run(['iptables', '-w', '-C', 'OUTPUT', '-j', chain], check=False)
        if not jump_exists(jump, chain):
            run(['iptables', '-w', '-I', 'OUTPUT', '1', '-j', chain])
            if not output_jump_present(chain):
                raise ValueError('Intake gate jump could not be verified')
    else:
        # Delete only our exact nonce-named chain and jump; never flush a shared chain.
        if not exists:
            jump = run(['iptables', '-w', '-C', 'OUTPUT', '-j', chain], check=False)
            if jump_exists(jump, chain):
                raise ValueError('Intake gate jump exists without its chain')
            return
        jump = run(['iptables', '-w', '-C', 'OUTPUT', '-j', chain], check=False)
        if jump_exists(jump, chain):
            deleted = run(['iptables', '-w', '-D', 'OUTPUT', '-j', chain], check=False)
            if deleted.returncode and output_jump_present(chain):
                raise ValueError('Intake gate jump removal failed')
        if output_jump_present(chain):
            raise ValueError('Intake gate jump remains after removal')
        run(['iptables', '-w', '-F', chain])
        removed = run(['iptables', '-w', '-X', chain], check=False)
        if removed.returncode and not filter_chain_absent(chain):
            raise ValueError('Intake gate removal failed')


def gate_is_active(chain):
    result = run(['iptables', '-w', '-S', chain], check=False)
    if not chain_exists(result, chain):
        jump = run(['iptables', '-w', '-C', 'OUTPUT', '-j', chain], check=False)
        if jump_exists(jump, chain):
            raise ValueError('Intake gate jump exists without its chain')
        return False
    rules = parse_iptables_listing(result, chain=chain)
    ports = owned_gate_rule_ports(rules, chain)
    if len(rules) != 3 or ports != {'8003', '8007'}:
        raise ValueError('Intake gate drift requires inspection')
    jump = run(['iptables', '-w', '-C', 'OUTPUT', '-j', chain], check=False)
    return jump_exists(jump, chain)


def quiet(containers):
    probe = '''import json,urllib.request
raw=urllib.request.urlopen('http://127.0.0.1:8000/metrics',timeout=10).read().decode()
rows=[float(line.split()[-1]) for line in raw.splitlines()
      if line.startswith(('vllm:num_requests_running{','vllm:num_requests_waiting{'))]
assert len(rows)>=2
print(json.dumps({'idle':all(v==0 for v in rows)}))
'''
    stable = 0
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        idle = True
        for item in containers:
            if item['State']['Running'] and item['Name'].endswith(('-primary', '-candidate')):
                result = run(['docker', 'exec', item['Id'], 'python3', '-c', probe])
                idle = idle and json.loads(result.stdout) == {'idle': True}
        connections = run(['ss', '-Htn', 'state', 'established', '( sport = :8003 or sport = :8007 )']).stdout
        stable = stable + 1 if idle and not connections.strip() else 0
        if stable >= 3:
            return
        time.sleep(1)
    raise ValueError('STT requests did not drain; no model may be replaced')


def candidate_image_id():
    result = run(['docker', 'image', 'inspect', CANDIDATE_IMAGE])
    image = json.loads(result.stdout)[0]
    labels = image.get('Config', {}).get('Labels') or {}
    if (labels.get('io.homecompute.whisper-runtime-patch') != '0.19.0-hc1' or
            labels.get('io.homecompute.whisper-runtime-patcher-sha256') !=
            'fed6ae01930b2689fbfdcb039ffdb024778f84dc67ba61d0d84fb7fc54935c74' or
            labels.get('io.homecompute.whisper-source-prompt-sha256') !=
            '44d196b1d3ec3182de51f4b5f87764a33a1b58c83e6afb5e452f52f4bb653dcf' or
            labels.get('io.homecompute.whisper-source-serving-sha256') !=
            '7b9b47511ba3643ccae14ae8a4095153959a35a671eb3cc07707dfd1f10ebfa3' or
            labels.get('org.opencontainers.image.base.name') != \
            'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d'):
        raise ValueError('Patched candidate image identity or base pin differs')
    return image['Id']


def candidate_rollback(tx):
    def stage(name):
        if name not in CANDIDATE_ROLLBACK_STAGES:
            raise ValueError('Unknown candidate rollback stage')
        tx['rollback_stage'] = name
        save_candidate(tx)

    stage('inspect-candidate')
    candidate = inspect(CANDIDATE)
    if candidate:
        labels = candidate.get('Config', {}).get('Labels') or {}
        if (candidate['Image'] != tx['candidate_image_id'] or
                labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or
                labels.get('com.docker.compose.service') != 'whisper-candidate'):
            raise ValueError('Candidate container ownership changed; rollback held')
        if candidate['State']['Running']:
            stage('disable-candidate-restart')
            run(['docker', 'update', '--restart', 'no', candidate['Id']])
            stage('stop-candidate')
            run(['docker', 'stop', '--time', '180', candidate['Id']], timeout=210)
        stage('remove-candidate')
        run(['docker', 'rm', candidate['Id']])
    stage('inspect-primary')
    original = inspect(tx['primary_id'])
    if not original or original['Image'] != tx['primary_image'] or \
            original['Name'] != '/gb10-whisper-primary':
        raise ValueError('Original Whisper primary changed or disappeared; rollback held')
    stage('restore-primary-restart')
    run(['docker', 'update', '--restart', tx['primary_restart'], tx['primary_id']])
    if not original['State']['Running']:
        stage('start-primary')
        run(['docker', 'start', tx['primary_id']])
    stage('wait-primary-health')
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        current = inspect(tx['primary_id'])
        if current and current['State']['Running'] and \
                current.get('State', {}).get('Health', {}).get('Status') == 'healthy':
            tx['state'] = 'rolled-back'
            tx['rollback_status'] = 'completed'
            stage('complete')
            save_candidate(tx)
            return
        time.sleep(2)
    raise ValueError('Original Whisper primary did not become healthy; rollback is incomplete')


def candidate_start(revision):
    guards()
    ensure_no_cuda_candidate_transaction('runtime-candidate')
    base_tx_path = STATE / 'transaction.json'
    protected(base_tx_path)
    base_tx = json.loads(base_tx_path.read_text())
    if base_tx.get('state') not in {'applied', 'legacy-retired'}:
        raise ValueError('The full Whisper lifecycle must be applied or legacy-retired before runtime qualification')
    primary = inspect(NEW[0])
    if (not primary or not primary['State']['Running'] or
            primary.get('State', {}).get('Health', {}).get('Status') != 'healthy' or
            MODEL not in ' '.join(primary['Config'].get('Cmd') or []) or
            REVISION not in ' '.join(primary['Config'].get('Cmd') or []) or
            primary.get('Config', {}).get('Image') !=
            'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d'):
        raise ValueError('Expected healthy pinned NVIDIA Whisper primary before candidate qualification')
    image_id = candidate_image_id()
    if inspect(CANDIDATE):
        raise ValueError('A candidate container already exists; inspect it before qualification')
    journal = STATE / 'runtime-candidate.json'
    if journal.exists() or journal.is_symlink():
        protected(journal)
        previous = json.loads(journal.read_text())
        if previous.get('state') != 'rolled-back':
            raise ValueError('Existing runtime candidate transaction requires rollback/status review')
        journal.rename(STATE / ('runtime-candidate-rolled-back-' + os.urandom(6).hex() + '.json'))
    chain = 'HC-STT-' + os.urandom(6).hex()
    tx = {'revision': revision, 'state': 'prepared', 'chain': chain, 'candidate_image_id': image_id,
          'primary_id': primary['Id'], 'primary_image': primary['Image'],
          'primary_restart': restart_policy(primary)}
    save_candidate(tx)
    gate_attempted = False
    failure = None
    try:
        candidate_stage(tx, 'install-intake-gate')
        gate_attempted = True
        gate(chain, True)
        candidate_stage(tx, 'drain-primary')
        quiet([primary])
        candidate_stage(tx, 'stop-primary')
        run(['docker', 'update', '--restart', 'no', primary['Id']])
        run(['docker', 'stop', '--time', '180', primary['Id']], timeout=210)
        candidate_stage(tx, 'pre-load-memory-check')
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(memory['MemAvailable'].split()[0]) < 12 * 1024 * 1024:
            raise ValueError('Less than 12 GiB available before candidate model load')
        tx['state'] = 'primary-stopped'; save_candidate(tx)
        candidate_stage(tx, 'candidate-start')
        compose('--profile', 'candidate', 'up', '-d', '--no-build', '--pull', 'never', '--wait',
                '--wait-timeout', '900', 'whisper-candidate')
        candidate_stage(tx, 'candidate-health-check')
        candidate = inspect(CANDIDATE)
        if (not candidate or candidate['Image'] != image_id or not candidate['State']['Running'] or
                candidate.get('State', {}).get('Health', {}).get('Status') != 'healthy' or
                MODEL not in ' '.join(candidate['Config'].get('Cmd') or []) or
                REVISION not in ' '.join(candidate['Config'].get('Cmd') or []) or
                candidate.get('Config', {}).get('Labels', {}).get('com.docker.compose.project') !=
                'whisper-large-v3-stt' or
                candidate.get('Config', {}).get('Labels', {}).get('com.docker.compose.service') !=
                'whisper-candidate'):
            raise ValueError('Patched candidate failed image, model or health verification')
        candidate_stage(tx, 'synthetic-relay-smoke')
        smoke(CANDIDATE)
        candidate_stage(tx, 'post-load-memory-check')
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(memory['MemAvailable'].split()[0]) / int(memory['MemTotal'].split()[0]) < 0.10:
            raise ValueError('Candidate failed the 10 percent host headroom gate')
        tx['state'] = 'candidate-running'
        candidate_stage(tx, 'candidate-running')
    except BaseException as error:
        failure = record_candidate_failure(tx, error)
        try:
            candidate_rollback(tx)
            failure = safe_candidate_metadata(tx)
        except BaseException as rollback_error:
            failure = record_candidate_rollback_failure(tx, rollback_error)
    finally:
        if gate_attempted:
            try:
                if failure is None:
                    candidate_stage(tx, 'release-intake-gate')
                gate(chain, False)
            except BaseException as cleanup_error:
                if failure is None:
                    failure = record_candidate_failure(tx, cleanup_error, stage='release-intake-gate')
                    try:
                        candidate_rollback(tx)
                        failure = safe_candidate_metadata(tx)
                    except BaseException as rollback_error:
                        failure = record_candidate_rollback_failure(tx, rollback_error)
                tx['gate_cleanup_failure_class'] = safe_class_name(cleanup_error)
                try:
                    save_candidate(tx)
                except Exception:
                    pass
                try:
                    gate(chain, False)
                except BaseException as retry_error:
                    tx['gate_cleanup_retry_class'] = safe_class_name(retry_error)
                    try:
                        save_candidate(tx)
                    except Exception:
                        pass
                failure = safe_candidate_metadata(tx)
    if failure is not None:
        raise CandidateQualificationFailure(failure) from None
    print('Patched Whisper candidate active for qualification; run runtime-candidate-rollback afterward', flush=True)


def save_candidate(value):
    target = STATE / 'runtime-candidate.json'
    temporary = STATE / 'runtime-candidate.tmp'
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('Incomplete candidate journal write requires inspection')
    with temporary.open('x') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, target)


def candidate_stop(revision):
    ensure_no_cuda_candidate_transaction('runtime-candidate-rollback')
    journal = STATE / 'runtime-candidate.json'; protected(journal)
    tx = json.loads(journal.read_text())
    if tx.get('revision') != revision or tx.get('state') == 'rolled-back':
        raise ValueError('No matching recoverable runtime candidate transaction')
    chain = tx['chain']
    gate(chain, True)
    try:
        candidate = inspect(CANDIDATE)
        if candidate and candidate['State']['Running']:
            quiet([candidate])
        candidate_rollback(tx)
    finally:
        gate(chain, False)


def candidate_status(revision):
    journal = STATE / 'runtime-candidate.json'
    if not journal.exists() and not journal.is_symlink():
        print(json.dumps({
            'revision': revision, 'state': 'absent', 'failure_id': None,
            'failure_stage': None, 'failure_class': None, 'failure_executable': None,
            'failure_exit_code': None, 'failure_classification': None, 'rollback_status': None,
            'rollback_failure_stage': None, 'rollback_failure_class': None,
            'rollback_failure_classification': None, 'rollback_failure_executable': None, 'rollback_failure_exit_code': None,
            'gate_cleanup_failure_class': None, 'failure_diagnostic_available': False,
        }, sort_keys=True))
        return
    protected(journal)
    tx = json.loads(journal.read_text())
    if tx.get('revision') != revision:
        raise ValueError('Candidate transaction belongs to another release')
    print(json.dumps({'revision': revision, **safe_candidate_metadata(tx)}, sort_keys=True))


def smoke(container=NEW[0]):
    item = inspect(container)
    if not item or MODEL not in ' '.join(item['Config'].get('Cmd') or []) or \
            REVISION not in ' '.join(item['Config'].get('Cmd') or []):
        raise ValueError('Full Whisper model/pin is not installed')
    code = '''import io,json,urllib.request,wave
key=open('/run/secrets/vllm_api_key').read().strip()
audio=io.BytesIO()
with wave.open(audio,'wb') as w:
 w.setparams((1,2,16000,0,'NONE','PCM'));w.writeframes(b'\\0\\0'*16000)
body=(b'--smoke\\r\\nContent-Disposition: form-data; name="model"\\r\\n\\r\\nwhisper-large-v3\\r\\n'
      b'--smoke\\r\\nContent-Disposition: form-data; name="file"; filename="audio.wav"\\r\\nContent-Type: audio/wav\\r\\n\\r\\n'
      +audio.getvalue()+b'\\r\\n--smoke--\\r\\n')
r=urllib.request.Request('http://whisper-http:8007/v1/audio/transcriptions',body,
 headers={'Authorization':'Bearer '+key,'Content-Type':'multipart/form-data; boundary=smoke'})
d=json.load(urllib.request.urlopen(r,timeout=120));assert isinstance(d.get('text'),str)
print('Authenticated relay transcription passed; content withheld')
'''
    run(['docker', 'exec', container, 'python3', '-c', code], timeout=150)


def rollback(tx):
    ensure_no_cuda_candidate_transaction('rollback')
    # Only remove replacement containers owned by this lifecycle's exact release.
    for name in reversed(NEW):
        item = inspect(name)
        if item and item['Id'] not in {row['id'] for row in tx['old']}:
            labels = item['Config'].get('Labels', {})
            if labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or \
                    labels.get('com.docker.compose.project.config_files') != str(ROOT / 'deploy/whisper-stt/compose.yaml'):
                raise ValueError('Replacement ownership changed; rollback held')
            run(['docker', 'rm', '-f', item['Id']])
    for row in reversed(tx['old']):
        item = inspect(row['id'])
        if not item or item['Image'] != row['image']:
            raise ValueError('Retained rollback container changed or disappeared')
        if item['Name'].lstrip('/') != row['name']:
            run(['docker', 'rename', row['id'], row['name']])
        run(['docker', 'update', '--restart', row['restart'], row['id']])
        if row['running']:
            run(['docker', 'start', row['id']])
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if all(not row['running'] or inspect(row['id'])['State'].get('Health', {}).get('Status') == 'healthy'
               for row in tx['old']):
            break
        time.sleep(2)
    else:
        raise ValueError('Previous STT containers did not become healthy; rollback is incomplete')
    tx['state'] = 'rolled-back'; save(tx)


def apply(revision):
    guards()
    ensure_no_cuda_candidate_transaction('apply')
    provision_cpp_role_key()
    journal = STATE / 'transaction.json'
    if journal.exists():
        protected(journal)
        tx = json.loads(journal.read_text())
        if tx['revision'] == revision and tx['state'] == 'applied':
            smoke(); return
        if tx['state'] != 'rolled-back':
            raise ValueError('Existing transaction requires status/rollback review')
        if run(['iptables', '-w', '-C', 'OUTPUT', '-j', tx['chain']], check=False).returncode == 0:
            raise ValueError('Rollback intake gate still exists; recover before retrying')
        for row in tx['old']:
            current = inspect(row['id'])
            if (not current or current['Name'] != '/' + row['name'] or current['Image'] != row['image'] or
                    current['State']['Running'] != row['running'] or restart_policy(current) != row['restart'] or
                    row['running'] and current['State'].get('Health', {}).get('Status') != 'healthy'):
                raise ValueError('Previous rollback is not fully restored; retry refused')
        journal.rename(STATE / ('rolled-back-' + tx['revision'] + '-' + os.urandom(6).hex() + '.json'))
    containers = [inspect(name) for name in OLD]
    if not all(containers):
        raise ValueError('Expected both existing STT stacks; inspect drift before migration')
    for item in containers:
        command = ' '.join(item['Config'].get('Cmd') or [])
        if item['Name'].endswith('-primary') and not any(pin in command for pin in
                ('5d1a09822018702dc51d763e3a867b62d26b3501', '41f01f3fe87f28c78e2fbf8b568835947dd65ed9')):
            raise ValueError('Unexpected previous STT model pin')
    tx = {'revision': revision, 'state': 'prepared', 'chain': 'HC-STT-' + os.urandom(6).hex(),
          'old': [{'id': c['Id'], 'name': c['Name'].lstrip('/'), 'image': c['Image'],
                   'running': c['State']['Running'], 'restart': restart_policy(c)} for c in containers]}
    save(tx)
    try:
        print('Draining existing STT requests before replacement', flush=True)
        gate(tx['chain'], True)
        quiet(containers)
        tx['state'] = 'replacing'; save(tx)
        for row in tx['old']:
            run(['docker', 'update', '--restart', 'no', row['id']])
            if row['running']:
                run(['docker', 'stop', '--time', '180', row['id']], timeout=210)
            if row['name'] in NEW:
                run(['docker', 'rename', row['id'], row['name'] + '-before-large-v3-' + revision[:12]])
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(memory['MemAvailable'].split()[0]) < 12 * 1024 * 1024:
            raise ValueError('Less than 12 GiB available after retiring old STT engines')
        compose('up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '900',
                'whisper-primary', 'whisper-http')
        smoke()
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(memory['MemAvailable'].split()[0]) / int(memory['MemTotal'].split()[0]) < 0.10:
            raise ValueError('Full Whisper failed the post-start 10 percent host headroom gate')
        for row in tx['old']:
            if inspect(row['id'])['State']['Running']:
                raise ValueError('Retired STT engine is still running')
        tx['state'] = 'verified'; save(tx)
    except BaseException:
        rollback(tx)
        raise
    finally:
        gate(tx['chain'], False)
    tx['state'] = 'applied'; save(tx)
    print('Full Whisper applied; previous STT containers retained stopped for rollback', flush=True)


def restart_policy(item):
    policy = item['HostConfig']['RestartPolicy']
    name = policy['Name'] or 'no'
    return name + (':' + str(policy['MaximumRetryCount']) if name == 'on-failure' and policy['MaximumRetryCount'] else '')


def retirement_paths():
    cache = Path('/srv/gb10-ai/cache/huggingface/hub')
    return [cache / ('models--' + repo.replace('/', '--')) for repo in LEGACY_REPOSITORIES] + [
        cache / '.locks' / ('models--' + repo.replace('/', '--')) for repo in LEGACY_REPOSITORIES
    ] + [Path('/srv/gb10-ai/cache/vllm/hviske')]


def retire(revision):
    """Explicit owner-requested deletion, independent of current inference intake."""
    guards()
    ensure_no_cuda_candidate_transaction('retire')
    journal = STATE / 'transaction.json'; protected(journal)
    tx = json.loads(journal.read_text())
    if tx['state'] not in {'applied', 'retiring-legacy', 'legacy-retired'}:
        raise ValueError('Complete or recover the full Whisper migration first')
    run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', tx['revision'], revision])
    current = inspect(NEW[0])
    if (not current or not current['State']['Running'] or
            current['State'].get('Health', {}).get('Status') != 'healthy' or
            MODEL not in ' '.join(current['Config'].get('Cmd') or []) or
            REVISION not in ' '.join(current['Config'].get('Cmd') or []) or
            current['Config'].get('Labels', {}).get('com.docker.compose.project') != 'whisper-large-v3-stt'):
        raise ValueError('Healthy pinned full Whisper is required before deletion')
    targets = [dict(row) for row in tx['old']]
    adapter = inspect('gb10-hviske-wyoming')
    if adapter:
        labels = adapter['Config'].get('Labels', {})
        if (labels.get('com.docker.compose.project') != 'hviske-stt' or
                labels.get('com.docker.compose.service') != 'hviske-wyoming'):
            raise ValueError('Unknown legacy adapter ownership; deletion refused')
        targets.append({'id': adapter['Id'], 'image': adapter['Image'], 'name': 'gb10-hviske-wyoming'})
    for row in targets:
        actual = inspect(row['id'])
        if actual and (actual['State']['Running'] or actual['Image'] != row['image']):
            raise ValueError('Legacy container is running or changed; deletion refused')
    ids = run(['docker', 'ps', '-aq']).stdout.split()
    containers = json.loads(run(['docker', 'inspect', *ids]).stdout) if ids else []
    for item in containers:
        command = ' '.join(item['Config'].get('Cmd') or [])
        manifests = {Path(mount['Source']).name for mount in item.get('Mounts', [])}
        if (any(repo in command for repo in LEGACY_REPOSITORIES) or
                manifests & {'accepted-hviske-cache.json', 'accepted-whisper-turbo-cache.json'}):
            if item['Id'] not in {row['id'] for row in targets}:
                raise ValueError('Another container references a legacy model; deletion refused')
    paths = retirement_paths()
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('Descriptor-safe directory removal is required')
    for path in paths:
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise ValueError('Legacy cache path changed type; deletion refused')
    manifests = [Path('/srv/gb10-ai/manifests') / name for name in
                 ('accepted-hviske-cache.json', 'accepted-whisper-turbo-cache.json')]
    for path, repository in zip(manifests, LEGACY_REPOSITORIES):
        if path.exists() or path.is_symlink():
            protected(path)
            if json.loads(path.read_text()).get('repo_id') != repository:
                raise ValueError('Legacy accepted manifest identity differs')
    if tx['state'] == 'legacy-retired':
        if any(inspect(row['id']) for row in targets) or any(path.exists() for path in paths + manifests):
            raise ValueError('Retired legacy assets reappeared; inspect drift')
        print('Hviske and Turbo remain removed'); return
    # Deletion cannot be rolled back without refetching. Journal before removing
    # anything; interruption resumes only these exact finite targets.
    tx.update(state='retiring-legacy', retirement_revision=revision); save(tx)
    for row in targets:
        if inspect(row['id']): run(['docker', 'rm', row['id']])
    for path in paths:
        if path.exists(): shutil.rmtree(path)
    for path in manifests:
        if path.exists(): path.unlink()
    for tag in ('homecompute/hviske-http:1.0.0', 'homecompute/stt-http:1.1.0'):
        # Never force-remove a shared image or touch the NVIDIA runtime used by
        # full Whisper. Docker refuses a still-referenced legacy image.
        run(['docker', 'image', 'rm', tag], check=False)
    tx.update(state='legacy-retired', rollback_containers_retained=False); save(tx)
    print(json.dumps({'state': tx['state'], 'retirement_revision': revision,
                      'removed_models': list(LEGACY_REPOSITORIES), 'full_whisper_running': True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'prepare-runtime-candidate', 'prepare-cuda-candidate',
                                           'apply', 'install', 'retire', 'rollback', 'status', 'smoke',
                                           'runtime-candidate', 'runtime-candidate-rollback',
                                           'runtime-candidate-status', 'cuda-candidate',
                                           'cuda-candidate-rollback', 'cuda-candidate-status',
                                           'cuda-candidate-qualify', 'cuda-candidate-promote'))
    parser.add_argument('--revision', required=True)
    parser.add_argument('--max-audio-seconds', type=int)
    parser.add_argument('--max-upload-bytes', type=int)
    parser.add_argument('--native-timeout-seconds', type=int)
    parser.add_argument('--qualification-manifest')
    args = parser.parse_args()
    if args.action == 'cuda-candidate-qualify':
        if not args.qualification_manifest:
            raise ValueError('Qualification manifest is required')
        if any(value is not None for value in (args.max_audio_seconds, args.max_upload_bytes,
                                               args.native_timeout_seconds)):
            raise ValueError('Qualification request limits must come only from its manifest')
    elif args.qualification_manifest:
        raise ValueError('Qualification manifest is accepted only for cuda-candidate-qualify')
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-spark':
        raise ValueError('Run through sudo on home-spark')
    published(args.revision)
    if args.action in {'runtime-candidate-status', 'cuda-candidate-status'}:
        if not STATE.exists() and not STATE.is_symlink():
            print(json.dumps({'revision': args.revision, 'state': 'absent', 'failure_id': None,
                              'candidate_exposed': False,
                              'gate_held': False,
                              'failure_stage': None, 'failure_class': None, 'failure_classification': None,
                              'failure_executable': None, 'failure_exit_code': None,
                              'rollback_status': None, 'rollback_failure_stage': None,
                              'rollback_failure_class': None, 'rollback_failure_classification': None,
                              'rollback_failure_executable': None, 'rollback_failure_exit_code': None,
                              'gate_cleanup_failure_class': None, 'failure_diagnostic_available': False},
                             sort_keys=True))
            return
        protected(STATE, True)
        read_locks = ('/var/lib/homecompute/home-spark-deploy.lock',
                      '/var/lib/homecompute-spark-rollout/lock',
                      '/var/lib/homecompute-speech-capacity/lock', str(STATE / 'lock'))
        with ExitStack() as stack:
            for name in read_locks:
                if not Path(name).is_file() or Path(name).is_symlink():
                    raise ValueError('Candidate status lock unavailable; no state changed')
                stream = stack.enter_context(open(name, 'r'))
                fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
            if args.action == 'runtime-candidate-status':
                candidate_status(args.revision)
            else:
                cuda_candidate_status(args.revision)
        return
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700); protected(STATE, True)
    with ExitStack() as stack:
        for name in ('/var/lib/homecompute/home-spark-deploy.lock',
                     '/var/lib/homecompute-spark-rollout/lock',
                     '/var/lib/homecompute-speech-capacity/lock', str(STATE / 'lock')):
            Path(name).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stream = stack.enter_context(open(name, 'a'))
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda *_: (_ for _ in ()).throw(InterruptedError('Interrupted')))
        if args.action == 'prepare': prepare()
        elif args.action == 'prepare-runtime-candidate': prepare_runtime_candidate()
        elif args.action == 'prepare-cuda-candidate': prepare_cuda_candidate(args.revision)
        elif args.action == 'apply': apply(args.revision)
        elif args.action == 'install': prepare(); apply(args.revision)
        elif args.action == 'retire': retire(args.revision)
        elif args.action == 'smoke': smoke()
        elif args.action == 'runtime-candidate': candidate_start(args.revision)
        elif args.action == 'runtime-candidate-rollback': candidate_stop(args.revision)
        elif args.action == 'runtime-candidate-status': candidate_status(args.revision)
        elif args.action == 'cuda-candidate':
            cuda_candidate_start(args.revision,
                                 max_audio_seconds=args.max_audio_seconds if args.max_audio_seconds is not None else 180,
                                 max_upload_bytes=args.max_upload_bytes if args.max_upload_bytes is not None else 6_000_000,
                                 native_timeout_seconds=args.native_timeout_seconds if args.native_timeout_seconds is not None else 900)
        elif args.action == 'cuda-candidate-rollback': cuda_candidate_stop(args.revision)
        elif args.action == 'cuda-candidate-status': cuda_candidate_status(args.revision)
        elif args.action == 'cuda-candidate-qualify':
            helper = load_cuda_qualification_helper()
            try:
                outcome = helper.qualify(SimpleNamespace(**globals()), args.qualification_manifest,
                                         args.revision, operator_uid=int(os.environ.get('SUDO_UID', '0')))
            except helper.QualificationError as error:
                raise CudaQualificationFailure({'qualification_id': None, 'state': 'refused',
                                                'stage': error.stage, 'failure_class': error.failure_class,
                                                'rollback': 'not-started', 'receipt': None,
                                                'results': []}) from None
            print(json.dumps(outcome, sort_keys=True), flush=True)
            if outcome['state'] != 'completed' or outcome['rollback'] != 'completed':
                raise CudaQualificationFailure(outcome)
        elif args.action == 'cuda-candidate-promote': promote_qualified_cuda_candidate(args.revision)
        else:
            journal = STATE / 'transaction.json'; protected(journal)
            tx = json.loads(journal.read_text())
            if args.revision not in {tx['revision'], tx.get('retirement_revision')}:
                raise ValueError('Transaction belongs to another release')
            if args.action == 'rollback':
                ensure_no_cuda_candidate_transaction('rollback')
                if tx['state'] in {'retiring-legacy', 'legacy-retired'}:
                    raise ValueError('Owner retired legacy assets; historical rollback requires an explicit refetch')
                gate(tx['chain'], True) if tx['state'] == 'applied' else None
                try:
                    quiet([inspect(name) for name in NEW if inspect(name)])
                    rollback(tx)
                finally: gate(tx['chain'], False)
            blocked = run(['iptables', '-w', '-C', 'OUTPUT', '-j', tx['chain']], check=False).returncode == 0
            print(json.dumps({'revision': tx['revision'], 'state': tx['state'], 'model': MODEL,
                              'intake_blocked': blocked,
                              'model_revision': REVISION,
                              'rollback_containers_retained': tx['state'] not in {'retiring-legacy', 'legacy-retired'}}))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        if isinstance(error, CandidateQualificationFailure):
            message = format_candidate_failure(error.metadata)
        elif isinstance(error, CudaCandidateFailure):
            message = format_cuda_candidate_failure(error.metadata)
        elif isinstance(error, CudaPrepareFailure):
            message = format_cuda_prepare_failure(error.metadata)
        elif isinstance(error, CudaQualificationFailure):
            message = format_cuda_qualification_failure(error.metadata)
        else:
            message = ('Whisper lifecycle refused: ' + str(error) if isinstance(error, ValueError)
                       else 'Whisper lifecycle failed: ' + type(error).__name__)
        print(message, file=__import__('sys').stderr)
        raise SystemExit(1) from None
