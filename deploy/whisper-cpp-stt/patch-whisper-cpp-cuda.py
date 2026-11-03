#!/usr/bin/env python3
"""Apply fail-closed CUDA initialization and model-placement reporting to pinned whisper.cpp."""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import subprocess


UPSTREAM_COMMIT = "f049fff95a089aa9969deb009cdd4892b3e74916"
PATCH_VERSION = "strict-gpu-buffer-proof-v1"
PATCHES = {
    "src/whisper.cpp": (
        "a884ff3eb3bbf7558958c4d06a299cd8d97d87a7d5c7aac9138b8ff6a2ef04d7",
        """    ggml_backend_t backend_gpu = whisper_backend_init_gpu(params);

    if (backend_gpu) {
        result.push_back(backend_gpu);
    }
""",
        """    ggml_backend_t backend_gpu = whisper_backend_init_gpu(params);

    if (params.use_gpu && !backend_gpu) {
        WHISPER_LOG_ERROR("%s: required GPU backend initialization failed\\n", __func__);
        return result;
    }
    if (backend_gpu) {
        result.push_back(backend_gpu);
    }
""",
    ),
    "examples/server/server.cpp": (
        "4edae36f55e3a751583cd2dfe32bb5b5516cf59a3c137254f08c7ac0bd3f5d59",
        """            const std::string health_response = "{\\\"status\\\":\\\"ok\\\"}";
            res.set_content(health_response, "application/json");
""",
        """            const size_t gpu_model_buffer_bytes = whisper_model_gpu_buffer_size(ctx);
            const std::string compute_backend = gpu_model_buffer_bytes > 0 ? "cuda" : "cpu";
            const std::string health_response = json{
                {"status", "ok"},
                {"compute_backend", compute_backend},
                {"gpu_model_buffer_bytes", gpu_model_buffer_bytes},
            }.dump();
            res.set_content(health_response, "application/json");
""",
    ),
    "include/whisper.h": (
        "6c1c70a5d4b74556f4253e51a13874ad013513b0ae62e779c0e30ffde3dc30ba",
        """    WHISPER_API const char * whisper_version(void);
""",
        """    WHISPER_API const char * whisper_version(void);

    // Bytes in model weight buffers allocated on a GPU/IGPU backend.
    // Returns zero when no model weights were placed on a GPU backend.
    WHISPER_API size_t whisper_model_gpu_buffer_size(struct whisper_context * ctx);
""",
    ),
}

IMPLEMENTATION_ANCHOR = """int whisper_model_n_vocab(struct whisper_context * ctx) {
"""
MODEL_BUFFER_ANCHOR = """    // load weights
    {
        size_t total_size = 0;

        model.n_loaded = 0;
"""
MODEL_BUFFER_CHECK = """    if (wctx.params.use_gpu && whisper_model_gpu_buffer_size(&wctx) == 0) {
        WHISPER_LOG_ERROR("%s: requested GPU has no model weight buffers\\n", __func__);
        return false;
    }

"""
IMPLEMENTATION = """size_t whisper_model_gpu_buffer_size(struct whisper_context * ctx) {
    if (ctx == nullptr) {
        return 0;
    }
    size_t size = 0;
    for (ggml_backend_buffer_t buffer : ctx->model.buffers) {
        ggml_backend_buffer_type_t buft = ggml_backend_buffer_get_type(buffer);
        ggml_backend_dev_t dev = ggml_backend_buft_get_device(buft);
        if (!ggml_backend_buffer_is_host(buffer) && dev &&
                (ggml_backend_dev_type(dev) == GGML_BACKEND_DEVICE_TYPE_GPU ||
                    ggml_backend_dev_type(dev) == GGML_BACKEND_DEVICE_TYPE_IGPU)) {
            size += ggml_backend_buffer_get_size(buffer);
        }
    }
    return size;
}

"""


def patch_bytes(original: bytes, expected_sha256: str, old: str, new: str) -> bytes:
    if sha256(original).hexdigest() != expected_sha256:
        raise ValueError("Pinned whisper.cpp source checksum changed")
    source = original.decode("utf-8")
    if source.count(old) != 1:
        raise ValueError("Pinned whisper.cpp patch anchor is missing or ambiguous")
    return source.replace(old, new, 1).encode("utf-8")


def apply(root: Path) -> None:
    revision = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
    if revision != UPSTREAM_COMMIT:
        raise ValueError("Pinned whisper.cpp source revision changed")
    updates = []
    for relative, (digest, old, new) in PATCHES.items():
        path = root / relative
        updates.append((path, patch_bytes(path.read_bytes(), digest, old, new)))
    source_path = root / "src/whisper.cpp"
    source = updates[0][1].decode("utf-8")
    if source.count(MODEL_BUFFER_ANCHOR) != 1:
        raise ValueError("Pinned whisper.cpp model buffer anchor is missing or ambiguous")
    source = source.replace(MODEL_BUFFER_ANCHOR, MODEL_BUFFER_CHECK + MODEL_BUFFER_ANCHOR, 1)
    if source.count(IMPLEMENTATION_ANCHOR) != 1:
        raise ValueError("Pinned whisper.cpp GPU placement API anchor is missing or ambiguous")
    source = source.replace(IMPLEMENTATION_ANCHOR, IMPLEMENTATION + IMPLEMENTATION_ANCHOR, 1)
    updates[0] = (source_path, source.encode("utf-8"))
    for path, content in updates:
        temporary = path.with_suffix(path.suffix + ".homecompute-tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
    if not verify(root):
        raise ValueError("Whisper.cpp CUDA patch verification failed")


def verify(root: Path) -> bool:
    whisper = (root / "src/whisper.cpp").read_text()
    server = (root / "examples/server/server.cpp").read_text()
    header = (root / "include/whisper.h").read_text()
    return ("required GPU backend initialization failed" in whisper and
            "size_t whisper_model_gpu_buffer_size(struct whisper_context * ctx)" in whisper and
            "requested GPU has no model weight buffers" in whisper and
            "ggml_backend_buffer_get_size(buffer)" in whisper and
            "gpu_model_buffer_bytes" in server and "compute_backend" in server and
            "whisper_model_gpu_buffer_size(struct whisper_context * ctx)" in header and
            server.count("whisper_model_gpu_buffer_size(ctx)") == 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify:
            if not verify(args.source):
                raise ValueError("Whisper.cpp CUDA patch verification failed")
        else:
            apply(args.source)
    except (OSError, UnicodeError, subprocess.SubprocessError, ValueError) as error:
        print(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
