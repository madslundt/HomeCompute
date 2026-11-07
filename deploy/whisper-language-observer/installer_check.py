"""Build-time package metadata checks; never import model packages."""
from __future__ import annotations
import importlib.metadata
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def main() -> None:
    pin = json.loads((ROOT / 'model.json').read_text())
    if sys.argv[1:] == ['export']:
        expected = {row['name']: row['version'] for row in json.loads((ROOT / 'dependencies.json').read_text())['wheels']}
        if sys.prefix == sys.base_prefix or any(importlib.metadata.version(name) != version for name, version in expected.items()):
            raise ValueError('Isolated export dependency identity differs')
        config = Path(sys.prefix, 'pyvenv.cfg').read_text()
        if 'include-system-site-packages = false' not in config:
            raise ValueError('Export environment includes NVIDIA system packages')
    elif len(sys.argv) in (2, 3) and sys.argv[1] in ('base', 'snapshot', 'unchanged'):
        for name, key in [('torch', 'runtime_torch'), ('torchvision', 'runtime_torchvision')]:
            if importlib.metadata.version(name) != pin[key]:
                raise ValueError('NVIDIA base package metadata changed')
        if sys.argv[1] != 'base':
            snapshot = {}
            for name in ('torch', 'torchvision'):
                dist = importlib.metadata.distribution(name)
                for item in sorted(dist.files or []):
                    path = Path(dist.locate_file(item))
                    if path.is_file():
                        digest = hashlib.sha256()
                        with path.open('rb') as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                                digest.update(chunk)
                        snapshot[name + '/' + str(item)] = digest.hexdigest()
            if not snapshot:
                raise ValueError('NVIDIA package file manifest unavailable')
            target = Path(sys.argv[2])
            if sys.argv[1] == 'snapshot':
                with target.open('x') as stream:
                    json.dump(snapshot, stream, sort_keys=True)
            elif json.loads(target.read_text()) != snapshot:
                raise ValueError('Global NVIDIA package files changed')
    else:
        raise ValueError('Explicit metadata mode required')
    print(json.dumps({'metadata_verified': True, 'model_imports': 0}))


if __name__ == '__main__':
    main()
