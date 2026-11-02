"""Fetch exact public artifacts at image-build time; runtime has no Hub access."""
import hashlib
from pathlib import Path
from huggingface_hub import snapshot_download

ROOT = Path('/opt/omnivoice/model')
snapshot_download('k2-fsa/OmniVoice', revision='c5fdb5ccb189668d56333f77ba2629f4cd7535f4', local_dir=ROOT)
for name, expected in {
    'model.safetensors': '730839316de585f4c8298ec0e1712efc10fb19c6fa4e36eb741cb8d51ebcf6aa',
    'audio_tokenizer/model.safetensors': 'fe7c5e8785e0a05833e1bfc3e002ec7f55af21e306b2e7154a448c1f54ccfb0d',
    'tokenizer.json': '408f669b7e2b045fdf54201d815bd364e6667dbd845115da81239c40bc6dcfd1',
}.items():
    with (ROOT / name).open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != expected:
        raise ValueError('Artifact checksum mismatch')
