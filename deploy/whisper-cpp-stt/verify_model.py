#!/usr/bin/env python3
"""Verify the externally provisioned, unquantized full Large-v3 GGML artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import struct
import sys


PIN = {
    "repository": "ggerganov/whisper.cpp",
    "snapshot": "c521a4b02f422512d734391fdf08bb08c0862f68",
    "filename": "ggml-large-v3.bin",
    "sha256": "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2",
    "size_bytes": 3095033483,
}
EXPECTED_HEADER = {
    "n_vocab": 51866,
    "n_audio_layer": 32,
    "n_text_layer": 32,
    "n_mels": 128,
    "ftype": 1,  # GGML_FTYPE_MOSTLY_F16 in the pinned whisper.cpp source.
}
MAGIC_LE = 0x67676D6C
HEADER_BYTES = 4 + 11 * 4


class ModelPinError(ValueError):
    pass


def header_values(data: bytes) -> dict[str, int]:
    if len(data) < HEADER_BYTES:
        raise ModelPinError("model header is truncated")
    values = struct.unpack("<12i", data[:HEADER_BYTES])
    if values[0] != MAGIC_LE:
        raise ModelPinError("model magic does not match GGML")
    names = (
        "n_vocab", "n_audio_ctx", "n_audio_state", "n_audio_head", "n_audio_layer",
        "n_text_ctx", "n_text_state", "n_text_head", "n_text_layer", "n_mels", "ftype",
    )
    return dict(zip(names, values[1:], strict=True))


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model(path: str | Path, *, expected_sha256: str = PIN["sha256"],
                 expected_size: int = PIN["size_bytes"]) -> dict[str, object]:
    model = Path(path)
    try:
        info = model.lstat()
    except OSError:
        raise ModelPinError("model file is unavailable") from None
    if not stat.S_ISREG(info.st_mode) or model.is_symlink():
        raise ModelPinError("model path must be a regular non-symlink file")
    if info.st_size != expected_size:
        raise ModelPinError("model size does not match the pinned artifact")
    with model.open("rb") as stream:
        header = header_values(stream.read(HEADER_BYTES))
    for key, value in EXPECTED_HEADER.items():
        if header.get(key) != value:
            raise ModelPinError(f"model header {key} does not match full Large-v3 F16")
    digest = _digest(model)
    if digest != expected_sha256:
        raise ModelPinError("model checksum does not match the pinned artifact")
    return {
        "repository": PIN["repository"],
        "snapshot": PIN["snapshot"],
        "filename": PIN["filename"],
        "sha256": digest,
        "size_bytes": info.st_size,
        "header": header,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_model(args.model), sort_keys=True))
    except ModelPinError as exc:
        print(f"model verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
