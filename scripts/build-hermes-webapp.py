#!/usr/bin/env python3
"""Build the pinned browser voice frontend in a disposable qualified-image container."""
import argparse
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BUILD = r'''set -eu
hermes_node_dir="$(/opt/hermes/.venv/bin/python -c 'from pm import installed_package; print(installed_package("node").binary.parent)')"
hermes_npm_cli="$(/opt/hermes/.venv/bin/python -c 'from pm import installed_package; print(installed_package("npm").binary.parent.parent/"lib/node_modules/npm/bin/npm-cli.js")')"
export PATH="$hermes_node_dir:$PATH" npm_execpath="$hermes_npm_cli"
test "$(node --version)" = v26.12.1
test "$(node "$npm_execpath" --version)" = 12.0.2
mkdir /tmp/hermes-webapp-source
tar -xzf /out/source.tar.gz --strip-components=1 -C /tmp/hermes-webapp-source
cd /tmp/hermes-webapp-source
node apps/desktop/scripts/build-webapp.mjs
tar -czf /out/hermes-webapp.tar.gz -C apps/desktop/dist-webapp .
chown "$HOMECOMPUTE_BUILD_UID:$HOMECOMPUTE_BUILD_GID" /out/hermes-webapp.tar.gz
chmod 0600 /out/hermes-webapp.tar.gz
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    pin = json.loads((ROOT / 'config/hermes-omh.json').read_bytes())
    image = pin['runtime_image_id']
    if subprocess.check_output(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'], text=True).strip() != image:
        raise ValueError('qualified image must already exist locally')
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    if output.stat().st_mode & 0o077 or any(p.is_symlink() for p in (args.output, *args.output.parents)):
        raise ValueError('private real build directory required')
    if (output / 'hermes-webapp.tar.gz').exists():
        raise ValueError('new build directory required; preserve prior artifacts')
    source = urllib.request.urlopen('https://codeload.github.com/NousResearch/hermes-agent/tar.gz/'
        '2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19', timeout=90).read()
    if hashlib.sha256(source).hexdigest() != '0250ce85c071ebceefc5b9d1b58a7a52f71d2849a6ef1d0a70af07d108ef08dc':
        raise ValueError('qualified upstream source archive required')
    (output / 'source.tar.gz').write_bytes(source)
    with (output / 'build.log').open('xb') as log:
        os.chmod(output / 'build.log', 0o600)
        subprocess.run(['docker', 'run', '--rm', '--cpus=1', '--memory=3g', '--user', 'root',
            '--env', 'HOMECOMPUTE_BUILD_UID=' + str(os.getuid()),
            '--env', 'HOMECOMPUTE_BUILD_GID=' + str(os.getgid()),
            '--mount', 'type=bind,src=' + str(output) + ',dst=/out', '--entrypoint', '/bin/sh',
            image, '-c', BUILD], stdout=log, stderr=log, check=True)
    artifact = output / 'hermes-webapp.tar.gz'
    # Normalize container filesystem timestamps and gzip headers so recovery
    # builds from the same source/toolchain have the same artifact digest.
    normalized = io.BytesIO()
    with tarfile.open(artifact) as source, gzip.GzipFile(fileobj=normalized, mode='wb', mtime=0, filename='') as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as target:
            for member in sorted(source.getmembers(), key=lambda row: row.name):
                if member.isfile():
                    info = tarfile.TarInfo(str(Path(member.name)))
                    data = source.extractfile(member).read()
                    info.size = len(data); info.mode = 0o644
                    target.addfile(info, io.BytesIO(data))
    artifact.write_bytes(normalized.getvalue())
    artifact.chmod(0o600)
    print(json.dumps({'image_id': image, 'name': artifact.name,
                      'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(), 'bytes': artifact.stat().st_size}))


if __name__ == '__main__': main()
