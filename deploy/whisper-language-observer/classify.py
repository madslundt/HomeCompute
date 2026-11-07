"""Offline Torch-only CPU graph verification/classification. No ASR or service."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time

def sibling(name):
    path = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location('_observer_classify_' + name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


C, G = sibling('common'), sibling('graph')


def configure():
    import torch
    C.require(torch.__version__ == C.PIN['runtime_torch'], 'Exact NVIDIA Torch runtime required')
    C.require(not any(name == 'speechbrain' or name.startswith('speechbrain.') or
                      name == 'torchaudio' or name.startswith('torchaudio.') for name in sys.modules),
              'Classification runtime must not import SpeechBrain or torchaudio')
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    return torch


def manifest(directory: Path, expected: str) -> dict:
    raw = C.read(directory / 'manifest.json', 1_000_000, expected=expected)
    value = json.loads(raw)
    C.require(value['schema_version'] == 1 and value['model'] == C.PIN and
              value['code_sha256'] == C.code_hashes() and
              value['source_revision'] == os.environ.get('HOME_COMPUTE_REVISION') and
              value['exporter_parity_passed'] is True and value['duration_discrimination_passed'] is True and value['private_audio_used'] is False,
              'Export source, model or parity identity differs')
    C.require(value['labels'][17] == 'da: Danish' and value['labels'][20] == 'en: English' and
              len(value['labels']) == 107 and len(set(value['labels'])) == 107,
              'Export label mapping differs')
    C.require([(x['frames'], x['kind']) for x in value['samples']] == list(C.SYNTHETIC),
              'Complete synthetic duration set required')
    graph = C.read(directory / 'observer.ts', C.PIN['maximum_graph_bytes'], expected=value['graph_sha256'])
    C.require(len(graph) == value['graph_bytes'], 'Graph size differs')
    return value


def graph(directory: Path, value: dict, torch):
    # Source-produced and hash-verified before deserialization; JIT has no weights_only switch.
    model = torch.jit.load(str(directory / 'observer.ts'), map_location='cpu').eval()
    C.require(G.validate(model, torch) == value['operators'], 'Exact graph operator manifest differs')
    C.require(not torch.cuda.is_initialized(), 'Unexpected CUDA initialization')
    return model


def output(model, torch, raw: bytes) -> list[float]:
    pcm = torch.frombuffer(bytearray(raw), dtype=torch.int16).clone().reshape(1, -1)
    with torch.inference_mode():
        values = model(pcm)
    C.require(values.dtype == torch.float32 and values.device.type == 'cpu' and
              tuple(values.shape) == (1, 107), 'CPU1x107 output required')
    result = values[0].tolist()
    C.check_numbers(result)
    C.require(not torch.cuda.is_initialized(), 'Unexpected CUDA initialization')
    return result


def verify(directory: Path, expected: str, report: Path) -> dict:
    value = manifest(directory, expected)
    torch = configure()
    model = graph(directory, value, torch)
    results = []
    for sample in value['samples']:
        raw = C.synthetic(sample['frames'], sample['kind'])
        C.require(C.sha(raw) == sample['pcm_sha256'], 'Synthetic input changed')
        actual = output(model, torch, raw)
        C.close(sample['official_log_posteriors'], actual)
        results.append({'frames': sample['frames'], 'kind': sample['kind'],
                        'maximum_absolute_log_error': max(abs(a-b) for a,b in
                            zip(sample['official_log_posteriors'], actual))})
    result = {'passed': True, 'scope': 'public-synthetic-runtime-parity-only',
              'manifest_sha256': expected, 'graph_sha256': value['graph_sha256'],
              'source_revision': value['source_revision'], 'runtime_torch': torch.__version__,
              'torch_threads': torch.get_num_threads(), 'cuda_initialized': False,
              'samples': results, 'classification_authorized': False}
    C.save(report, result)
    return result


def classify(directory: Path, expected: str, parity: Path, parity_sha: str,
             approved_warning_sha: str, wav: bytes) -> dict:
    pcm = C.pcm(wav)
    value = manifest(directory, expected)
    proof = json.loads(C.read(parity, 1_000_000, expected=parity_sha))
    C.require(proof.get('passed') is True and proof.get('manifest_sha256') == expected and
              proof.get('graph_sha256') == value['graph_sha256'] and
              proof.get('runtime_torch') == C.PIN['runtime_torch'] and
              proof.get('source_revision') == value['source_revision'] and
              [(x.get('frames'), x.get('kind')) for x in proof.get('samples', [])] == list(C.SYNTHETIC) and
              proof.get('cuda_initialized') is False and proof.get('torch_threads') == 1, 'Actual NVIDIA runtime parity required')
    warnings_sha = C.sha(json.dumps(value['trace_warnings'], sort_keys=True).encode())
    C.require(approved_warning_sha == warnings_sha, 'Root-reviewed trace-warning identity required')
    torch = configure()
    model = graph(directory, value, torch)
    started = time.monotonic()
    scores = output(model, torch, pcm)
    top = max(range(107), key=scores.__getitem__)
    return {'scope': 'auxiliary-language-observation-only', 'wav_sha256': C.sha(wav),
            'pcm_sha256': C.sha(pcm), 'frames': len(pcm) // 2, 'sample_rate': 16000,
            'duration_seconds': len(pcm) / 32000, 'top_index': top,
            'top_language': value['labels'][top], 'log_posteriors': scores,
            'posteriors': [math.exp(x) for x in scores], 'labels': value['labels'],
            'model_repository': C.PIN['repository'], 'model_revision': C.PIN['revision'],
            'model_files': C.PIN['files'], 'graph_sha256': value['graph_sha256'],
            'manifest_sha256': expected, 'runtime_parity_sha256': parity_sha,
            'source_revision': value['source_revision'], 'code_sha256': C.code_hashes(),
            'runtime_torch': torch.__version__, 'device': 'cpu', 'torch_threads': 1,
            'elapsed_seconds': time.monotonic() - started,
            'calibrated_language_correctness_claim': False, 'asr_or_speaker_output': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('verify', 'classify'))
    parser.add_argument('--graph-directory', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--parity-report', type=Path)
    parser.add_argument('--parity-sha256')
    parser.add_argument('--approved-warning-sha256')
    args = parser.parse_args()
    if args.mode == 'verify':
        C.require(args.report is not None, 'Parity report output required')
        result = verify(args.graph_directory, args.manifest_sha256, args.report)
    else:
        C.require(args.parity_report and args.parity_sha256 and args.approved_warning_sha256,
                  'Frozen runtime parity and reviewed warnings required')
        wav = sys.stdin.buffer.read(44 + C.PIN['maximum_frames'] * 2 + 1)
        result = classify(args.graph_directory, args.manifest_sha256, args.parity_report,
                          args.parity_sha256, args.approved_warning_sha256, wav)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'failed': True, 'failure_class': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
