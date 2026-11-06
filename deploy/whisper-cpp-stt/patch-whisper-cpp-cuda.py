#!/usr/bin/env python3
"""Apply guarded CUDA decoding and opt-in metadata tracing to pinned whisper.cpp."""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import subprocess


UPSTREAM_COMMIT = "f049fff95a089aa9969deb009cdd4892b3e74916"
PATCH_VERSION = "strict-gpu-window-audit-v4"
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
        """            std::unique_lock<std::mutex> inference_probe(whisper_mutex, std::try_to_lock);
            const bool inference_busy = !inference_probe.owns_lock();
            json health = {{"status", "ok"}, {"inference_busy", inference_busy}};
            if (!inference_busy) {
                const size_t gpu_model_buffer_bytes = whisper_model_gpu_buffer_size(ctx);
                health["compute_backend"] = gpu_model_buffer_bytes > 0 ? "cuda" : "cpu";
                health["gpu_model_buffer_bytes"] = gpu_model_buffer_bytes;
            }
            const std::string health_response = health.dump();
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

SAMPLING_PATCHES = (
    (
        """    // TAGS: WHISPER_DECODER_INIT
    for (int j = 1; j < n_decoders; j++) {
""",
        """    // Each request starts from the same sampling state as a fresh decoder.
    state->decoders[0].rng = std::mt19937(0);

    // TAGS: WHISPER_DECODER_INIT
    for (int j = 1; j < n_decoders; j++) {
""",
    ),
    (
        """                logits_id.begin() + k, logits_id.end(),
                [](const pair_type & a, const pair_type & b) {
            return a.first > b.first;
""",
        """                logits_id.begin() + k, logits_id.end(),
                [](const pair_type & a, const pair_type & b) {
            return a.first != b.first ? a.first > b.first : a.second < b.second;
""",
    ),
    (
        """    std::discrete_distribution<> dist(probs.begin(), probs.end());

    for (int i = 0; i < k; ++i) {
        const auto id = dist(decoder.rng);
""",
        """    for (int i = 0; i < k; ++i) {
        const auto id = logits_id[i].second;
""",
    ),
)


TRACE_REQUEST_ANCHOR = """int whisper_full_with_state(
        struct whisper_context * ctx,
          struct whisper_state * state,
    struct whisper_full_params   params,
                   const float * samples,
                           int   n_samples) {
    // clear old results
    auto & result_all = state->result_all;

    result_all.clear();
"""

TRACE_WRITER = r"""// Private, opt-in metadata only. Trace failure never changes inference.
static void whisper_write_window_trace(const char * line, size_t length) {
    constexpr off_t limit = 1024 * 1024;
    if (length == 0 || length > 1024) {
        return;
    }
    const int fd = open("/tmp/whisper-window-trace.jsonl",
            O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0600);
    if (fd < 0) {
        return;
    }
    if (flock(fd, LOCK_EX | LOCK_NB) == 0) {
        struct stat status;
        if (fstat(fd, &status) == 0 && S_ISREG(status.st_mode) &&
                status.st_uid == geteuid() && status.st_nlink == 1 &&
                (status.st_mode & 0777) == 0600 && status.st_size >= 0 &&
                status.st_size <= limit - static_cast<off_t>(length)) {
            const auto written = write(fd, line, length);
            if (written > 0 && static_cast<size_t>(written) != length) {
                // Do not leave a partial JSON line after a short write.
                (void) ftruncate(fd, status.st_size);
            }
        }
        (void) flock(fd, LOCK_UN);
    }
    (void) close(fd);
}

"""

TRACE_INITIALIZE = """
    const char * window_trace_env = std::getenv("WHISPER_CPP_TRACE_WINDOWS");
    const bool trace_windows = window_trace_env != nullptr && std::strcmp(window_trace_env, "1") == 0;
"""

TRACE_SEEK_ANCHOR = """            // update audio window
            seek += seek_delta;
"""

TRACE_RECORD = r"""
            if (trace_windows) {
                const int trace_errno = errno;
                int timestamp_id_offset = -1;
                int timestamp_tid_offset = -1;
                for (auto token = tokens_cur.rbegin(); token != tokens_cur.rend(); ++token) {
                    if (token->id >= whisper_token_beg(ctx)) {
                        timestamp_id_offset = token->id - whisper_token_beg(ctx);
                        if (token->tid >= whisper_token_beg(ctx)) {
                            timestamp_tid_offset = token->tid - whisper_token_beg(ctx);
                        }
                        break;
                    }
                }
                char probability[32] = "null";
                if (std::isfinite(state->no_speech_prob)) {
                    std::snprintf(probability, sizeof(probability), "%.9g", state->no_speech_prob);
                }
                const char * language = whisper_lang_str(state->lang_id);
                char line[1024];
                const int length = std::snprintf(line, sizeof(line),
                        "{\"version\":1,\"seek_start_cs\":%d,\"seek_end_cs\":%d,"
                        "\"seek_before_cs\":%d,\"seek_after_cs\":%d,"
                        "\"seek_delta_before_cs\":%d,\"seek_delta_after_cs\":%d,"
                        "\"result_len\":%d,\"last_timestamp_id_offset\":%d,"
                        "\"last_timestamp_tid_offset\":%d,\"single_timestamp_ending\":%s,"
                        "\"max_tokens_timestamp_ending\":%s,\"is_no_speech\":%s,"
                        "\"no_speech_probability\":%s,\"language\":\"%s\"}\n",
                        seek_start, seek_end, seek - seek_delta, seek,
                        best_decoder.seek_delta, seek_delta, result_len,
                        timestamp_id_offset, timestamp_tid_offset,
                        single_timestamp_ending ? "true" : "false",
                        max_tokens_timestamp_ending ? "true" : "false",
                        is_no_speech ? "true" : "false", probability,
                        language != nullptr ? language : "unknown");
                if (length > 0 && static_cast<size_t>(length) < sizeof(line)) {
                    whisper_write_window_trace(line, static_cast<size_t>(length));
                }
                errno = trace_errno;
            }
"""

WINDOW_TRACE_PATCHES = (
    ("#include <cstdio>\n", "#include <cstdio>\n#include <cerrno>\n#include <cstdlib>\n"
     "#include <fcntl.h>\n#include <sys/file.h>\n#include <sys/stat.h>\n#include <unistd.h>\n"),
    (TRACE_REQUEST_ANCHOR, TRACE_WRITER + TRACE_REQUEST_ANCHOR + TRACE_INITIALIZE),
    (TRACE_SEEK_ANCHOR, TRACE_SEEK_ANCHOR + TRACE_RECORD),
)


def patch_window_trace(source: str) -> str:
    for old, new in WINDOW_TRACE_PATCHES:
        if source.count(old) != 1:
            raise ValueError("Pinned whisper.cpp window trace anchor is missing or ambiguous")
        source = source.replace(old, new, 1)
    return source


def patch_sampling(source: str) -> str:
    for old, new in SAMPLING_PATCHES:
        if source.count(old) != 1:
            raise ValueError("Pinned whisper.cpp sampling anchor is missing or ambiguous")
        source = source.replace(old, new, 1)
    return source


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
    source = patch_sampling(source)
    source = patch_window_trace(source)
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
    return (all(whisper.count(new) == 1 for _old, new in SAMPLING_PATCHES + WINDOW_TRACE_PATCHES) and
            SAMPLING_PATCHES[-1][0] not in whisper and
            "required GPU backend initialization failed" in whisper and
            "size_t whisper_model_gpu_buffer_size(struct whisper_context * ctx)" in whisper and
            "requested GPU has no model weight buffers" in whisper and
            "std::unique_lock<std::mutex> inference_probe(whisper_mutex, std::try_to_lock)" in server and
            "if (!inference_busy)" in server and 'health["gpu_model_buffer_bytes"] = gpu_model_buffer_bytes;' in server and
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
