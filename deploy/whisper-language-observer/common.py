"""Bounded metadata, PCM and artifact validation. No model imports."""
from __future__ import annotations

import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys

ROOT = Path(__file__).resolve().parent
PIN = json.loads((ROOT / 'model.json').read_text())
LOCK = json.loads((ROOT / 'dependencies.json').read_text())
CODE = ('common.py', 'graph.py', 'export.py', 'classify.py', 'installer_check.py', 'model.json',
        'dependencies.json', 'requirements.lock')


def require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(path: Path, maximum: int, *, expected: str | None = None) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                0 <= before.st_size <= maximum, 'Bounded regular artifact required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(fd)
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        require(identity(before) == identity(after) and len(raw) == before.st_size,
                'Artifact changed during verification')
        require(expected is None or sha(raw) == expected, 'Artifact checksum differs')
        return raw
    finally:
        os.close(fd)


def load(path: Path, maximum: int = 1_000_000) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Duplicate JSON key')
            value[key] = item
        return value
    value = json.loads(read(path, maximum), object_pairs_hook=unique)
    require(isinstance(value, dict), 'JSON object required')
    return value


def save(path: Path, value: dict) -> None:
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def code_hashes() -> dict[str, str]:
    return {name: sha(read(ROOT / name, 2_000_000)) for name in CODE}


def labels(raw: bytes) -> list[str]:
    rows = raw.decode('utf-8').splitlines()
    require(rows[-2:] == ['================', "'starting_index' => 0"], 'Label metadata differs')
    result = []
    for index, row in enumerate(rows[:-2]):
        match = re.fullmatch(r"'([a-z]{2,3}: [^'\r\n]+)' => ([0-9]+)", row)
        require(match and int(match[2]) == index, 'Label mapping differs')
        result.append(match[1])
    require(len(result) == PIN['classes'] and len(set(result)) == len(result) and
            result[17] == 'da: Danish' and result[20] == 'en: English', '107 exact labels required')
    return result


def model_files(directory: Path) -> list[str]:
    require(not directory.is_symlink() and directory.is_dir(), 'Model directory required')
    require({p.name for p in directory.iterdir()} == {x['name'] for x in PIN['files']},
            'Only the three pinned model assets are permitted')
    for item in PIN['files']:
        raw = read(directory / item['name'], item['size'], expected=item['sha256'])
        require(len(raw) == item['size'], 'Model size differs')
    return labels(read(directory / 'label_encoder.txt', 3000))


def pcm(raw: bytes) -> bytes:
    """Accept one canonical PCM16 WAV; no conversion, gain, crop or padding."""
    require(44 <= len(raw) <= 44 + PIN['maximum_frames'] * 2, 'WAV exceeds its bound')
    require(raw[:4] == b'RIFF' and raw[8:16] == b'WAVEfmt ' and
            struct.unpack_from('<IHHIIHH', raw, 16) == (16, 1, 1, 16000, 32000, 2, 16) and
            raw[36:40] == b'data', 'Canonical mono16k PCM16 WAV required')
    require(struct.unpack_from('<I', raw, 4)[0] == len(raw) - 8 and
            struct.unpack_from('<I', raw, 40)[0] == len(raw) - 44,
            'Complete WAV data required')
    result = raw[44:]
    require(len(result) % 2 == 0 and PIN['minimum_frames'] <= len(result) // 2 <= PIN['maximum_frames'],
            'PCM must contain3–30 seconds')
    return result


def synthetic(frames: int, kind: str = 'speechlike') -> bytes:
    """Public, deterministic integer waveforms; no private audio or references."""
    require(PIN['minimum_frames'] <= frames <= PIN['maximum_frames'], 'Synthetic duration out of bounds')
    data = array.array('h', (0 for _ in range(frames)))
    if kind != 'zero':
        require(kind in ('speechlike', 'quiet'), 'Unknown synthetic waveform')
        scale = 4 if kind == 'quiet' else 9000
        for i in range(frames):
            t = i / 16000
            envelope = (0.2 + 0.8 * ((i // 2371) % 7) / 6) * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * t))
            value = math.sin(2 * math.pi * (137 * t + 19 * t * t)) + 0.25 * math.sin(2 * math.pi * 311 * t)
            data[i] = round(scale * envelope * value)
    if sys.byteorder != 'little':
        data.byteswap()
    return data.tobytes()


SYNTHETIC = ((48000, 'speechlike'), (48001, 'speechlike'), (49664, 'speechlike'),
             (72448, 'speechlike'), (120320, 'speechlike'), (181921, 'speechlike'),
             (479999, 'speechlike'), (480000, 'speechlike'), (48000, 'zero'), (120320, 'quiet'))


def check_numbers(values: list[float]) -> None:
    require(isinstance(values, list) and len(values) == 107 and
            all(type(x) in (int, float) and math.isfinite(x) and x <= 0.0001 for x in values),
            '107 finite log posteriors required')
    require(abs(sum(math.exp(x) for x in values) - 1) <= 0.0001, 'Posterior normalization differs')


def close(reference: list[float], actual: list[float]) -> None:
    check_numbers(reference)
    check_numbers(actual)
    rule = PIN['parity']
    require(all(abs(a - b) <= rule['absolute_log_tolerance'] + rule['relative_log_tolerance'] * abs(a)
                for a, b in zip(reference, actual)), 'Full-posterior parity failed')
    require(max(range(107), key=reference.__getitem__) == max(range(107), key=actual.__getitem__),
            'Posterior argmax changed')
