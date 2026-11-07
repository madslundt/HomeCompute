"""Explicit, offline CPU model export. No YAML evaluation or Hub model code."""
from __future__ import annotations

import argparse
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import re
import warnings

def sibling(name):
    path = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location('_observer_export_' + name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


C, G = sibling('common'), sibling('graph')


def dependencies():
    C.require(os.environ.get('LD_LIBRARY_PATH', '') == '' and not os.environ.get('LD_PRELOAD') and not os.environ.get('PYTHONPATH') and
              not os.environ.get('PYTHONHOME'), 'Export library paths must be sanitized')
    import sys
    C.require(sys.prefix != sys.base_prefix and 'include-system-site-packages = false' in
              Path(sys.prefix, 'pyvenv.cfg').read_text(), 'Isolated export venv required')
    for row in C.LOCK['wheels']:
        C.require(importlib.metadata.version(row['name']) == row['version'], 'Export package differs')
    import torch
    C.require(torch.__version__ == '2.7.1' and torch.version.cuda is None, 'Official CPU Torch2.7.1 required')
    from speechbrain.lobes.features import Fbank
    from speechbrain.processing.features import InputNormalization
    from speechbrain.lobes.models.ECAPA_TDNN import ECAPA_TDNN
    from speechbrain.lobes.models.Xvector import Classifier
    return torch, Fbank, InputNormalization, ECAPA_TDNN, Classifier


def configure(torch) -> None:
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(0)


def checkpoint(torch, module, path: Path) -> None:
    # Never permit pickle globals, unsafe fallbacks, key remapping or partial loads.
    state = torch.load(path, map_location='cpu', weights_only=True)
    expected = module.state_dict()
    C.require(isinstance(state, dict) and set(state) == set(expected), 'Checkpoint keys differ')
    for key, tensor in state.items():
        C.require(type(key) is str and type(tensor) is torch.Tensor and tensor.device.type == 'cpu' and
                  tensor.shape == expected[key].shape and tensor.dtype == expected[key].dtype and
                  bool(torch.isfinite(tensor).all()), 'Checkpoint tensor shape/dtype/value differs')
    module.load_state_dict(state, strict=True)
    del state


def pipeline(directory: Path, imports):
    torch, Fbank, InputNormalization, ECAPA_TDNN, Classifier = imports
    class Pipeline(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.features = Fbank(n_mels=60, left_frames=0, right_frames=0, deltas=False)
            self.normalizer = InputNormalization(norm_type='sentence', std_norm=False)
            self.embedding = ECAPA_TDNN(input_size=60, channels=[1024, 1024, 1024, 1024, 3072],
                                        kernel_sizes=[5, 3, 3, 3, 1], dilations=[1, 2, 3, 4, 1],
                                        attention_channels=128, lin_neurons=256)
            self.classifier = Classifier(input_shape=[None, None, 256], activation=torch.nn.LeakyReLU,
                                         lin_blocks=1, lin_neurons=512, out_neurons=107)
        def forward(self, pcm):
            wav = pcm.to(torch.float32) / 32768.0
            lengths = torch.ones((wav.shape[0],), dtype=torch.float32, device=wav.device)
            features = self.normalizer(self.features(wav), lengths)
            return self.classifier(self.embedding(features, lengths)).squeeze(1)
    model = Pipeline()
    checkpoint(torch, model.embedding, directory / 'embedding_model.ckpt')
    checkpoint(torch, model.classifier, directory / 'classifier.ckpt')
    return model.cpu().eval()


def tensor(torch, raw: bytes):
    # Copy to owned writable CPU storage. The graph itself performs the fixed PCM scale.
    return torch.frombuffer(bytearray(raw), dtype=torch.int16).clone().reshape(1, -1)


def output(torch, model, raw: bytes) -> list[float]:
    value = model(tensor(torch, raw))
    C.require(tuple(value.shape) == (1, 107) and value.dtype == torch.float32 and
              value.device.type == 'cpu', 'Graph output contract differs')
    result = value[0].tolist()
    C.check_numbers(result)
    return result


def export(directory: Path, out: Path, source: str) -> dict:
    C.require(re.fullmatch('[a-f0-9]{40}', source), 'Published source SHA required')
    names = C.model_files(directory)
    imports = dependencies()
    torch = imports[0]
    configure(torch)
    model = pipeline(directory, imports)
    C.require(not out.exists(), 'Export output must be new; no overwrite or retry')
    out.mkdir(mode=0o700)
    with torch.inference_mode(), warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        traced = torch.jit.trace(model, tensor(torch, C.synthetic(48000)), strict=True, check_trace=False)
        frozen = torch.jit.freeze(traced.eval())
        operators = G.validate(frozen, torch)
        samples = []
        for frames, kind in C.SYNTHETIC:
            raw = C.synthetic(frames, kind)
            expected, actual = output(torch, model, raw), output(torch, frozen, raw)
            C.close(expected, actual)
            samples.append({'frames': frames, 'kind': kind, 'pcm_sha256': C.sha(raw),
                            'official_log_posteriors': expected,
                            'maximum_absolute_log_error': max(abs(a-b) for a, b in zip(expected, actual))})
        C.require(samples[0]['official_log_posteriors'] != samples[7]['official_log_posteriors'],
                  'Duration probe must distinguish original model outputs')
        path = out / 'observer.ts'
        torch.jit.save(frozen, str(path))
    graph_bytes = C.read(path, C.PIN['maximum_graph_bytes'])
    # Do not hide trace warnings. Root must review them before graph admission.
    trace_warnings = sorted({str(item.message) for item in captured if issubclass(item.category, torch.jit.TracerWarning)})
    report = {'schema_version': 1, 'source_revision': source, 'model': C.PIN,
              'model_manifest_sha256': C.sha(C.read(C.ROOT / 'model.json', 100000)),
              'dependencies_sha256': C.sha(C.read(C.ROOT / 'dependencies.json', 100000)),
              'code_sha256': C.code_hashes(), 'graph_sha256': C.sha(graph_bytes), 'graph_bytes': len(graph_bytes),
              'labels': names, 'operators': operators, 'export_torch': torch.__version__,
              'samples': samples, 'trace_warnings': trace_warnings,
              'exporter_parity_passed': True, 'duration_discrimination_passed': True, 'nvidia_runtime_parity_passed': False,
              'classification_authorized': False, 'private_audio_used': False,
              'speaker_or_asr_output': False}
    C.save(out / 'manifest.json', report)
    return {'graph_sha256': report['graph_sha256'], 'manifest_sha256': C.sha(C.read(out / 'manifest.json', 1000000)),
            'synthetic_parity_cases': len(samples), 'trace_warning_count': len(trace_warnings)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.model, args.output, args.source), sort_keys=True))


if __name__ == '__main__':
    main()
