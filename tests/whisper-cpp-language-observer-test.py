#!/usr/bin/env python3
"""Real IPC against synthetic, hash-pinned helpers. No model or private audio."""
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('observer_transport', ROOT / 'deploy/whisper-cpp-stt/language_observer.py')
O = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = O
SPEC.loader.exec_module(O)

COMMON = '''import hashlib,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parent
CODE=('common.py','graph.py','export.py','classify.py','installer_check.py','model.json','dependencies.json','requirements.lock')
PIN={'runtime_torch':'synthetic-nvidia-runtime', 'runtime_torch_module':'synthetic-nvidia-module'}
SYNTHETIC=((48000,'speechlike'),(48001,'speechlike'),(49664,'speechlike'),(72448,'speechlike'),(120320,'speechlike'),(181921,'speechlike'),(479999,'speechlike'),(480000,'speechlike'),(48000,'zero'),(120320,'quiet'))
def require(value,message):
 if not value:raise ValueError(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(path,maximum,expected=None):
 raw=Path(path).read_bytes();require(len(raw)<=maximum and (expected is None or sha(raw)==expected),'fixture hash differs');return raw
def code_hashes():return {name:sha((ROOT/name).read_bytes()) for name in CODE}
def pcm(wav):return wav[44:]
def check_numbers(values):
 require(len(values)==107 and all(math.isfinite(x) for x in values) and abs(sum(math.exp(x) for x in values)-1)<.0001,'107 output differs')
'''
CLASSIFIER = '''import importlib.util,json,math,os,struct,subprocess,sys,time
from pathlib import Path
s=importlib.util.spec_from_file_location('fixture_common',Path(__file__).with_name('common.py'));C=importlib.util.module_from_spec(s);s.loader.exec_module(C)
VALUE=None
DIRECTORY=None
def marker(value):
 with (DIRECTORY/'events').open('a') as f:f.write(value+'\\n')
def manifest(directory,expected):
 global VALUE,DIRECTORY
 DIRECTORY=directory;VALUE=json.loads(C.read(directory/'manifest.json',100000,expected))
 C.require(VALUE['source_revision']==os.environ['HOME_COMPUTE_REVISION'],'revision differs')
 C.read(directory/'observer.ts',100000,VALUE['graph_sha256'])
 return VALUE
def configure():
 C.require(os.environ['CUDA_VISIBLE_DEVICES']=='' and os.environ['NVIDIA_VISIBLE_DEVICES']=='void','GPU visible')
 C.require(os.environ['LOGNAME']=='language-observer','LOGNAME differs')
 C.require(all(os.environ[k]=='1' for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS')),'threads differ')
 marker('configure');(DIRECTORY/'pid').write_text(str(os.getpid()))
 if VALUE['mode']=='descendant':
  child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)'])
  (DIRECTORY/'descendant').write_text(str(child.pid));raise SystemExit(0)
 if VALUE['mode']=='slow':time.sleep(5)
 if VALUE['mode'] in ('stderr','stdout'):
  stream=sys.stderr if VALUE['mode']=='stderr' else sys.stdout
  stream.write('synthetic-private-marker'*1000);stream.flush();time.sleep(5)
 return object()
def graph(directory,value,torch):
 C.require(C.sha((directory/'observer.ts').read_bytes())==value['graph_sha256'],'graph differs')
 marker('graph');return object()
def output(model,torch,raw):
 if VALUE['mode']=='bad_scores':return [float('nan')]*107
 marker(C.sha(raw));top={1:17,2:20,3:0}[struct.unpack_from('<h',raw)[0]]
 return [math.log(.9 if i==top else .1/106) for i in range(107)]
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def wav(frames=48000, sample=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(struct.pack('<h', sample) * frames)
    return output.getvalue()


class ObserverTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.metadata = self.root / 'metadata'
        self.metadata.mkdir()
        distribution = self.metadata / 'torch-0.0.0.dist-info'
        distribution.mkdir()
        self.torch_metadata = distribution / 'METADATA'
        self.torch_metadata.write_text('Name: torch\nVersion: synthetic-nvidia-runtime\n')
        self.python = self.root / 'python-synthetic'
        bootstrap = ('import runpy,sys;sys.path.insert(0,' + repr(str(self.metadata)) + ');'
                     'sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name="__main__")')
        self.python.write_text('#!' + sys.executable + '\nimport os,sys\n'
                               'os.execv(' + repr(sys.executable) + ',[' + repr(sys.executable)
                               + ',"-I","-B","-c",' + repr(bootstrap) + ',*sys.argv[3:]])\n')
        self.python.chmod(0o700)
        self.source = self.root / 'source'
        self.graph = self.root / 'graph'
        self.source.mkdir()
        self.graph.mkdir()
        for name in O.CODE:
            (self.source / name).write_text(COMMON if name == 'common.py' else CLASSIFIER if name == 'classify.py' else '{}')
        (self.graph / 'observer.ts').write_bytes(b'synthetic graph')
        labels = [f'{chr(97+i//26)}{chr(97+i%26)}: synthetic' for i in range(107)]
        labels[17], labels[20] = 'da: Danish', 'en: English'
        self.manifest = {'graph_sha256': sha(b'synthetic graph'), 'source_revision': 'a'*40,
                         'trace_warnings': ['reviewed'], 'labels': labels, 'mode': 'normal',
                         'samples': [{'frames': frames, 'kind': kind,
                                      'official_log_posteriors': [math.log(.9 if i == 0 else .1/106) for i in range(107)]}
                                     for frames, kind in [(48000,'speechlike'),(48001,'speechlike'),
                                         (49664,'speechlike'),(72448,'speechlike'),(120320,'speechlike'),
                                         (181921,'speechlike'),(479999,'speechlike'),(480000,'speechlike'),
                                         (48000,'zero'),(120320,'quiet')]]}
        self.parity = {'passed': True, 'graph_sha256': self.manifest['graph_sha256'],
                       'runtime_torch': 'synthetic-nvidia-runtime', 'runtime_torch_module': 'synthetic-nvidia-module',
                       'source_revision': 'a'*40,
                       'samples': [{'frames': sample['frames'], 'kind': sample['kind'], 'maximum_absolute_log_error': 0}
                                   for sample in self.manifest['samples']],
                       'cuda_initialized': False, 'torch_threads': 1}

    def tearDown(self):
        self.tmp.cleanup()

    def bundle(self, **changes):
        raw = json.dumps(self.manifest).encode()
        (self.graph / 'manifest.json').write_bytes(raw)
        self.parity['manifest_sha256'] = sha(raw)
        proof = json.dumps(self.parity).encode()
        (self.graph / 'parity.json').write_bytes(proof)
        arguments = dict(source_directory=self.source, graph_directory=self.graph, manifest_sha256=sha(raw),
                         parity_report=self.graph / 'parity.json', parity_sha256=sha(proof),
                         approved_warning_sha256=sha(json.dumps(['reviewed'], sort_keys=True).encode()),
                         source_revision='a'*40,
                         code_sha256={name:sha((self.source/name).read_bytes()) for name in O.CODE},
                         python_executable=str(self.python))
        return O.ObserverBundle(**{**arguments, **changes})

    def test_real_preflight_checks_metadata_without_runtime_configuration_or_load(self):
        observer = O.LanguageObserver(self.bundle())
        self.assertTrue(observer.preflight(5))
        self.assertFalse((self.graph/'events').exists())
        self.assertFalse((self.graph/'pid').exists())
        with self.assertRaises(ValueError):
            observer((), 5)
        with self.assertRaises(RuntimeError):
            observer._run(O._payload((wav(),)), time.monotonic() + 5, '--preflight')

    def test_preflight_refuses_missing_and_tampered_artifacts_before_model_load(self):
        for location in ('source', 'manifest', 'graph', 'parity', 'metadata'):
            for action in ('missing', 'tampered'):
                with self.subTest(location=location, action=action):
                    observer = O.LanguageObserver(self.bundle())
                    path = {'source': self.source/'export.py', 'manifest': self.graph/'manifest.json',
                            'graph': self.graph/'observer.ts', 'parity': self.graph/'parity.json',
                            'metadata': self.torch_metadata}[location]
                    original = path.read_bytes()
                    try:
                        path.unlink() if action == 'missing' else path.write_bytes(b'unreviewed')
                        with self.assertRaises(RuntimeError):
                            observer.preflight(5)
                        self.assertFalse((self.graph/'events').exists())
                    finally:
                        path.write_bytes(original)

    def test_preflight_requires_ten_full_posterior_samples(self):
        self.manifest['samples'][0]['official_log_posteriors'] = [0.0] * 106
        with self.assertRaises(RuntimeError):
            O.LanguageObserver(self.bundle()).preflight(5)
        self.assertFalse((self.graph/'events').exists())

    def test_real_batch_loads_once_preserves_pcm_order_and_full107_top(self):
        inputs = tuple(wav(sample=sample) for sample in (1, 2, 3))
        observer = O.LanguageObserver(self.bundle())
        self.assertEqual(observer(inputs, 5), ['da', 'en', 'aa'])
        self.assertEqual((self.graph/'events').read_text().splitlines(),
                         ['configure', 'graph', *[sha(value[44:]) for value in inputs]])

    def test_all_eight_sources_verified_before_loading_helpers(self):
        bundle = self.bundle()
        (self.source/'export.py').write_text('unreviewed')
        with self.assertRaisesRegex(RuntimeError, '^Independent language observation unavailable$'):
            O.LanguageObserver(bundle)((wav(),), 5)
        self.assertFalse((self.graph/'events').exists())

    def test_parity_warning_and_graph_mismatch_fail_closed(self):
        for failure in ('parity', 'warning', 'graph'):
            with self.subTest(failure=failure):
                self.parity['passed'] = failure != 'parity'
                bundle = self.bundle(**({'approved_warning_sha256':'0'*64} if failure == 'warning' else {}))
                if failure == 'graph':
                    (self.graph/'observer.ts').write_bytes(b'wrong')
                with self.assertRaises(RuntimeError):
                    O.LanguageObserver(bundle)((wav(),), 5)
                self.assertNotIn('graph', (self.graph/'events').read_text().splitlines()
                                 if (self.graph/'events').exists() else [])

    def test_deadline_kills_and_reaps_actual_child(self):
        self.manifest['mode'] = 'slow'
        observer = O.LanguageObserver(self.bundle())
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, '^Independent language observation unavailable$'):
            observer((wav(),), 1)
        self.assertLess(time.monotonic() - start, 2)
        pid = int((self.graph/'pid').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)

    def test_timeout_kills_group_after_leader_exits_with_descendant_pipes_open(self):
        self.manifest['mode'] = 'descendant'
        observer = O.LanguageObserver(self.bundle())
        with patch.object(O.os, 'killpg', wraps=os.killpg) as terminate:
            with self.assertRaises(RuntimeError):
                observer((wav(),), 1)
        leader = int((self.graph/'pid').read_text())
        descendant = int((self.graph/'descendant').read_text())
        self.assertEqual(terminate.call_args.args, (leader, O.signal.SIGKILL))
        with self.assertRaises(ChildProcessError):
            os.waitpid(leader, os.WNOHANG)
        # The orphan is reaped by the host init. An already dead zombie is also
        # sufficient to prove it cannot retain a live pipe or execute further.
        end = time.monotonic() + 1
        while True:
            state = subprocess.run(['ps', '-o', 'stat=', '-p', str(descendant)],
                                   capture_output=True, timeout=1).stdout.strip()
            if not state or state.startswith(b'Z'):
                break
            self.assertLess(time.monotonic(), end)
            time.sleep(.01)

    def test_output_bounds_kill_without_returning_child_content(self):
        for mode in ('stderr', 'stdout'):
            with self.subTest(mode=mode):
                self.manifest['mode'] = mode
                start = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, '^Independent language observation unavailable$') as failure:
                    O.LanguageObserver(self.bundle())((wav(),), 3)
                self.assertNotIn('synthetic-private-marker', str(failure.exception))
                self.assertLess(time.monotonic() - start, 2)
                with self.assertRaises(ProcessLookupError):
                    os.kill(int((self.graph/'pid').read_text()), 0)

    def test_nonblocking_read_write_races_keep_the_same_exchange(self):
        real_read, real_write = os.read, os.write
        read_errors = iter((BlockingIOError, InterruptedError))
        write_errors = iter((BlockingIOError, InterruptedError))
        def read(fd, size):
            error = next(read_errors, None) if size == 65536 else None
            if error is not None:
                raise error()
            return real_read(fd, size)
        def write(fd, data):
            error = next(write_errors, None) if isinstance(data, memoryview) else None
            if error is not None:
                raise error()
            return real_write(fd, data)
        observer = O.LanguageObserver(self.bundle())
        with patch.object(O.os, 'read', side_effect=read), patch.object(O.os, 'write', side_effect=write):
            self.assertEqual(observer((wav(),), 5), ['da'])
        self.assertEqual((self.graph/'events').read_text().splitlines().count('graph'), 1)

    def test_invalid_full_posterior_never_returns_a_language(self):
        self.manifest['mode'] = 'bad_scores'
        with self.assertRaises(RuntimeError):
            O.LanguageObserver(self.bundle())((wav(),), 5)

    def test_audio_count_total_and_canonical_bounds_precede_launch(self):
        observer = O.LanguageObserver(self.bundle())
        bad = [(), (wav(),)*401, (wav(480000),)*41, (wav(47999),), (wav(480001),),
               (wav() + b'trailing',), (b'not audio',), [wav()]]
        with patch.object(O.subprocess, 'Popen') as spawn:
            for inputs in bad:
                with self.subTest(count=len(inputs)), self.assertRaises(ValueError):
                    observer(inputs, 5)
            for timeout in (0, -1, float('inf'), float('nan'), True):
                with self.assertRaises(ValueError):
                    observer((wav(),), timeout)
        spawn.assert_not_called()

    def test_exact400_three_second_inputs_fit_shared1200_second_limit(self):
        audio = wav()
        result = O.LanguageObserver(self.bundle())((audio,)*400, 5)
        self.assertEqual(result, ['da']*400)
        self.assertEqual((self.graph/'events').read_text().splitlines().count('graph'), 1)

    def test_binary_framing_refuses_truncation_or_trailing_data(self):
        value = wav()
        payload = O._payload((value,))
        self.assertEqual(O._inputs(payload), [value])
        for bad in (payload[:-1], payload+b'x', struct.pack('<I',401), b''):
            with self.assertRaises(ValueError):
                O._inputs(bad)


if __name__ == '__main__':
    unittest.main()
