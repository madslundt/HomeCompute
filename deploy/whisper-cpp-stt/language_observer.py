"""Bounded, request-scoped CPU transport for an operator-pinned LID bundle."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from types import MappingProxyType
from collections.abc import Mapping

CODE = ('common.py', 'graph.py', 'export.py', 'classify.py', 'installer_check.py',
        'model.json', 'dependencies.json', 'requirements.lock')
MAX_COUNT = 400
MAX_FRAMES = 1200 * 16000
MAX_INPUT = 4 + MAX_COUNT * 48 + MAX_FRAMES * 2
MAX_RESULT = 8192
MAX_STDERR = 4096


def _require(value, message):
    if not value:
        raise ValueError(message)


@dataclass(frozen=True)
class ObserverBundle:
    source_directory: Path
    graph_directory: Path
    manifest_sha256: str
    parity_report: Path
    parity_sha256: str
    approved_warning_sha256: str
    source_revision: str
    code_sha256: Mapping[str, str]
    python_executable: str = sys.executable

    def __post_init__(self):
        for name in ('source_directory', 'graph_directory', 'parity_report'):
            path = Path(getattr(self, name))
            _require(path.is_absolute(), 'Absolute operator artifact paths required')
            object.__setattr__(self, name, path)
        hashes = dict(self.code_sha256)
        _require(set(hashes) == set(CODE), 'Complete observer source pins required')
        for value in (*hashes.values(), self.manifest_sha256, self.parity_sha256,
                      self.approved_warning_sha256):
            _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'Invalid observer pin')
        _require(re.fullmatch('[0-9a-f]{40}', self.source_revision), 'Invalid observer source revision')
        _require(Path(self.python_executable).is_absolute(), 'Absolute observer Python required')
        object.__setattr__(self, 'code_sha256', MappingProxyType(hashes))

    def _wire(self):
        return {name: (dict(value) if name == 'code_sha256' else str(value))
                for name, value in vars(self).items()}


def _frames(wav):
    _require(type(wav) is bytes and 44 <= len(wav) <= 44 + 480000 * 2, 'Invalid observer WAV')
    _require(wav[:4] == b'RIFF' and wav[8:16] == b'WAVEfmt ' and wav[36:40] == b'data'
             and struct.unpack_from('<IHHIIHH', wav, 16) == (16, 1, 1, 16000, 32000, 2, 16)
             and struct.unpack_from('<I', wav, 4)[0] == len(wav) - 8
             and struct.unpack_from('<I', wav, 40)[0] == len(wav) - 44,
             'Canonical observer WAV required')
    frames, extra = divmod(len(wav) - 44, 2)
    _require(not extra and 48000 <= frames <= 480000, 'Observer WAV must contain 3–30 seconds')
    return frames


def _payload(wavs):
    _require(type(wavs) is tuple and 1 <= len(wavs) <= MAX_COUNT, 'Invalid observer batch count')
    _require(sum(_frames(wav) for wav in wavs) <= MAX_FRAMES, 'Observer batch exceeds 1200 seconds')
    return struct.pack('<I', len(wavs)) + b''.join(struct.pack('<I', len(wav)) + wav for wav in wavs)


def _exchange(command, payload, environment, deadline):
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=environment, start_new_session=True)
    output = {child.stdout: bytearray(), child.stderr: bytearray()}
    sent = 0
    complete = False
    try:
        with selectors.DefaultSelector() as selector:
            for stream, event in ((child.stdin, selectors.EVENT_WRITE), (child.stdout, selectors.EVENT_READ),
                                  (child.stderr, selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                _require(remaining > 0, 'Observer deadline exceeded')
                try:
                    ready = selector.select(remaining)
                except InterruptedError:
                    continue
                for key, _event in ready:
                    stream = key.fileobj
                    if stream is child.stdin:
                        try:
                            sent += os.write(stream.fileno(), memoryview(payload)[sent:sent + 65536])
                        except (BlockingIOError, InterruptedError):
                            continue
                        except BrokenPipeError:
                            selector.unregister(stream)
                            stream.close()
                            continue
                        if sent == len(payload):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        try:
                            data = os.read(stream.fileno(), 65536)
                        except (BlockingIOError, InterruptedError):
                            continue
                        if not data:
                            selector.unregister(stream)
                            continue
                        output[stream].extend(data)
                        limit = MAX_RESULT if stream is child.stdout else MAX_STDERR
                        _require(len(output[stream]) <= limit, 'Observer output exceeds bound')
            remaining = deadline - time.monotonic()
            _require(remaining > 0, 'Observer deadline exceeded')
            child.wait(timeout=remaining)
        _require(child.returncode == 0 and sent == len(payload) and not output[child.stderr],
                 'Observer child failed')
        complete = True
        return bytes(output[child.stdout])
    finally:
        if not complete:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait(timeout=2)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()


class LanguageObserver:
    def __init__(self, bundle: ObserverBundle):
        _require(isinstance(bundle, ObserverBundle), 'Operator observer bundle required')
        self.bundle = bundle

    def _run(self, payload, deadline, mode):
        environment = {'PATH': os.defpath, 'HOME': '/tmp', 'LOGNAME': 'language-observer',
                       'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'void',
                       'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                       'HOME_COMPUTE_REVISION': self.bundle.source_revision}
        if 'LD_LIBRARY_PATH' in os.environ:
            environment['LD_LIBRARY_PATH'] = os.environ['LD_LIBRARY_PATH']
        command = [self.bundle.python_executable, '-I', '-B', str(Path(__file__).resolve()),
                   mode, json.dumps(self.bundle._wire(), sort_keys=True)]
        try:
            raw = _exchange(command, payload, environment, deadline)
            result = json.loads(raw)
            _require(time.monotonic() < deadline, 'Observer deadline exceeded')
            return result
        except Exception:
            raise RuntimeError('Independent language observation unavailable') from None

    @staticmethod
    def _deadline(timeout):
        _require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
                 'Invalid observer deadline')
        return time.monotonic() + timeout

    def __call__(self, wavs: tuple[bytes, ...], remaining_timeout: float) -> list[str]:
        deadline = self._deadline(remaining_timeout)
        payload = _payload(wavs)
        _require(time.monotonic() < deadline, 'Observer deadline exceeded')
        result = self._run(payload, deadline, '--child')
        if not (type(result) is list and len(result) == len(wavs)
                and all(type(code) is str and re.fullmatch('[a-z]{2,3}', code) for code in result)
                and time.monotonic() < deadline):
            raise RuntimeError('Independent language observation unavailable')
        return result

    def preflight(self, timeout: float) -> bool:
        result = self._run(b'', self._deadline(timeout), '--preflight')
        if not (type(result) is dict and set(result) == {'preflight_passed'}
                and result['preflight_passed'] is True):
            raise RuntimeError('Independent language observation unavailable')
        return True


def _source(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 2_000_000,
                 'Invalid observer source artifact')
        raw = stream.read(2_000_001)
        after = os.fstat(stream.fileno())
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    _require(identity(before) == identity(after) and len(raw) == before.st_size
             and hashlib.sha256(raw).hexdigest() == expected, 'Observer source identity differs')
    return raw


def _inputs(raw):
    _require(4 <= len(raw) <= MAX_INPUT, 'Invalid observer IPC input')
    count, = struct.unpack_from('<I', raw)
    _require(1 <= count <= MAX_COUNT, 'Invalid observer IPC count')
    cursor, frames, result = 4, 0, []
    for _ in range(count):
        _require(cursor + 4 <= len(raw), 'Incomplete observer IPC input')
        size, = struct.unpack_from('<I', raw, cursor)
        cursor += 4
        _require(44 <= size <= 960044 and cursor + size <= len(raw), 'Invalid observer IPC WAV bound')
        wav = raw[cursor:cursor + size]
        frames += _frames(wav)
        _require(frames <= MAX_FRAMES, 'Observer IPC audio exceeds bound')
        result.append(wav)
        cursor += size
    _require(cursor == len(raw), 'Trailing observer IPC input')
    return result


@contextmanager
def _metadata(bundle):
    _require(not bundle.source_directory.is_symlink() and not bundle.graph_directory.is_symlink(),
             'Direct observer bundle directories required')
    sources = {name: _source(bundle.source_directory / name, bundle.code_sha256[name]) for name in CODE}
    # Snapshot only verified source bytes. The exported graph stays on its trusted
    # operator mount and is checked by the existing manifest/graph helpers.
    with tempfile.TemporaryDirectory(prefix='observer-code-') as snapshot:
        directory = Path(snapshot)
        for name, raw in sources.items():
            (directory / name).write_bytes(raw)
        spec = importlib.util.spec_from_file_location('_verified_observer_classify', directory / 'classify.py')
        classifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(classifier)
        C = classifier.C
        _require(C.code_hashes() == dict(bundle.code_sha256), 'Loaded observer code differs')
        value = classifier.manifest(bundle.graph_directory, bundle.manifest_sha256)
        proof = json.loads(C.read(bundle.parity_report, 1_000_000, expected=bundle.parity_sha256))
        C.require(proof.get('passed') is True and proof.get('manifest_sha256') == bundle.manifest_sha256
                  and proof.get('graph_sha256') == value['graph_sha256']
                  and proof.get('runtime_torch') == C.PIN['runtime_torch']
                  and proof.get('runtime_torch_module') == C.PIN['runtime_torch_module']
                  and proof.get('source_revision') == value['source_revision']
                  and [(x.get('frames'), x.get('kind')) for x in proof.get('samples', [])] == list(C.SYNTHETIC)
                  and proof.get('cuda_initialized') is False and proof.get('torch_threads') == 1,
                  'Actual NVIDIA runtime parity required')
        C.require(bundle.approved_warning_sha256 == C.sha(json.dumps(value['trace_warnings'], sort_keys=True).encode()),
                  'Reviewed observer warnings required')
        C.require(len(C.SYNTHETIC) == len(value['samples']) == len(proof['samples']) == 10,
                  'Ten complete synthetic parity samples required')
        for sample in value['samples']:
            C.check_numbers(sample['official_log_posteriors'])
        C.require(importlib.metadata.version('torch') == C.PIN['runtime_torch'],
                  'Exact installed NVIDIA Torch metadata required')
        C.require(not any(name.split('.', 1)[0] in ('torch', 'torchaudio', 'speechbrain')
                          for name in sys.modules), 'Metadata verification must not import model runtimes')
        yield classifier, value


def _classify(bundle, wavs):
    with _metadata(bundle) as (classifier, value):
        C = classifier.C
        torch = classifier.configure()
        model = classifier.graph(bundle.graph_directory, value, torch)
        result = []
        for wav in wavs:
            scores = classifier.output(model, torch, C.pcm(wav))
            C.check_numbers(scores)
            code = value['labels'][max(range(107), key=scores.__getitem__)].split(':', 1)[0]
            _require(re.fullmatch('[a-z]{2,3}', code), 'Invalid observer language code')
            result.append(code)
        return result


def _preflight(bundle):
    with _metadata(bundle):
        return {'preflight_passed': True}


def _child():
    try:
        _require(len(sys.argv) == 3 and sys.argv[1] in ('--child', '--preflight'), 'Invalid observer child invocation')
        bundle = ObserverBundle(**json.loads(sys.argv[2]))
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if sys.argv[1] == '--preflight':
            _require(raw == b'', 'Preflight requires empty input')
            result = _preflight(bundle)
        else:
            result = _classify(bundle, _inputs(raw))
        sys.stdout.write(json.dumps(result, separators=(',', ':')) + '\n')
        return 0
    except Exception:
        sys.stderr.write('Independent language observation failed\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(_child())
