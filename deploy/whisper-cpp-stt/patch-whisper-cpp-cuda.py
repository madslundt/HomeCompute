#!/usr/bin/env python3
"""Apply guarded CUDA decoding and opt-in metadata tracing to pinned whisper.cpp."""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import subprocess


UPSTREAM_COMMIT = "f049fff95a089aa9969deb009cdd4892b3e74916"
PATCH_VERSION = "strict-gpu-language-candidates-v5"
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


LANGUAGE_HELPERS = r"""static bool whisper_valid_language_candidates(const int * ids, int count) {
    if (count < 0 || count > 16 || (count > 0 && ids == nullptr)) return false;
    for (int i = 0; i < count; ++i) {
        if (ids[i] < 0 || ids[i] > whisper_lang_max_id() || whisper_lang_str(ids[i]) == nullptr) return false;
        for (int j = 0; j < i; ++j) if (ids[i] == ids[j]) return false;
    }
    return true;
}

static int whisper_select_language_candidate(const float * probs, const int * ids, int count) {
    int selected = -1;
    for (int i = 0; i < count; ++i) {
        const int id = ids[i];
        if (!std::isfinite(probs[id]) || probs[id] < 0.0f || probs[id] > 1.0f) return -1;
        if (selected < 0 || probs[id] > probs[selected] ||
                (probs[id] == probs[selected] && id < selected)) selected = id;
    }
    return selected;
}

"""
LANGUAGE_PARSER = r"""static bool parse_language_candidates(const std::string & csv, std::vector<int> & ids) {
    ids.clear();
    if (csv.empty() || csv.size() > 63) return false;
    std::vector<int> parsed;
    std::string previous;
    size_t begin = 0;
    while (begin < csv.size()) {
        const size_t end = csv.find(',', begin);
        const auto code = csv.substr(begin, end == std::string::npos ? end : end - begin);
        if (code.size() < 2 || code.size() > 3 || parsed.size() >= 16 ||
                (!previous.empty() && code <= previous)) return false;
        for (const char c : code) if (c < 'a' || c > 'z') return false;
        const int id = whisper_lang_id(code.c_str());
        if (id < 0 || code != whisper_lang_str(id)) return false;
        parsed.push_back(id);
        previous = code;
        if (end == std::string::npos) { ids = parsed; return true; }
        begin = end + 1;
    }
    return false;
}

"""
LANGUAGE_PATCHES = {
    "include/whisper.h": (
        ("    WHISPER_API const char * whisper_version(void);\n", """    struct whisper_language_selection {
        int detected_id, selected_id;
        float detected_probability, selected_probability;
    };
    WHISPER_API struct whisper_language_selection whisper_full_get_language_selection(struct whisper_context * ctx);
    WHISPER_API const char * whisper_version(void);
"""),
        ("        bool detect_language;\n", """        bool detect_language;
        // Optional valid unique language IDs; explicit language takes precedence.
        const int * language_candidates;
        int n_language_candidates; // 0 means unrestricted; otherwise 1..16.
"""),
    ),
    "src/whisper.cpp": (
        ("    int lang_id = 0; // english by default\n", """    int lang_id = 0; // english by default
    whisper_language_selection language_selection = {-1, -1, 0.0f, 0.0f};
"""),
        ("        /*.detect_language   =*/ false,\n", """        /*.detect_language   =*/ false,
        /*.language_candidates =*/ nullptr,
        /*.n_language_candidates =*/ 0,
"""),
        ("static bool whisper_vad(\n", LANGUAGE_HELPERS + "static bool whisper_vad(\n"),
        ("    if (n_samples > 0) {\n        // compute log mel spectrogram\n", """    state->language_selection = {-1, -1, 0.0f, 0.0f};
    const bool automatic_language = params.language == nullptr || strlen(params.language) == 0 || strcmp(params.language, "auto") == 0;
    if (!whisper_valid_language_candidates(params.language_candidates, params.n_language_candidates)) {
        WHISPER_LOG_ERROR("%s: invalid language candidates\\n", __func__);
        return -3;
    }
    if (params.n_language_candidates > 0 && !automatic_language) {
        const int explicit_id = whisper_lang_id(params.language);
        if (explicit_id < 0) return -3;
        state->lang_id = explicit_id;
        state->language_selection.selected_id = explicit_id;
    }
    if (n_samples > 0) {
        // compute log mel spectrogram
"""),
        ('    if (params.language == nullptr || strlen(params.language) == 0 || strcmp(params.language, "auto") == 0 || params.detect_language) {\n',
         '    if (automatic_language || (params.detect_language && params.n_language_candidates == 0)) {\n'),
        ("        const auto lang_id = whisper_lang_auto_detect_with_state(ctx, state, 0, params.n_threads, probs.data());\n", """        const auto detected_id = whisper_lang_auto_detect_with_state(ctx, state, 0, params.n_threads, probs.data());
        int lang_id = detected_id;
        if (detected_id >= 0 && params.n_language_candidates > 0) {
            lang_id = whisper_select_language_candidate(probs.data(), params.language_candidates, params.n_language_candidates);
            if (lang_id >= 0) state->language_selection = {detected_id, lang_id, probs[detected_id], probs[lang_id]};
        }
"""),
        ("int whisper_full_lang_id(struct whisper_context * ctx) {\n", """struct whisper_language_selection whisper_full_get_language_selection(struct whisper_context * ctx) {
    return ctx->state->language_selection;
}

int whisper_full_lang_id(struct whisper_context * ctx) {
"""),
    ),
    "examples/server/server.cpp": (
        ('    std::string language               = "en";\n', '    std::string language               = "en";\n    std::vector<int> language_candidates;\n'),
        ("void get_req_parameters(const Request & req, whisper_params & params)\n", LANGUAGE_PARSER + "void get_req_parameters(const Request & req, whisper_params & params)\n"),
        ("        get_req_parameters(req, params);\n", """        get_req_parameters(req, params);
        if (req.has_file("language_candidates") &&
                (req.files.count("language_candidates") != 1 ||
                 !parse_language_candidates(req.get_file_value("language_candidates").content, params.language_candidates) ||
                 params.n_processors != 1)) {
            res.status = 400;
            res.set_content("{\\\"error\\\":\\\"invalid language candidates\\\"}", "application/json");
            return;
        }
"""),
        ("            wparams.detect_language  = params.detect_language;\n", """            wparams.detect_language  = params.detect_language;
            wparams.language_candidates = params.language_candidates.empty() ? nullptr : params.language_candidates.data();
            wparams.n_language_candidates = static_cast<int>(params.language_candidates.size());
"""),
        ("            // Only compute language probabilities if requested (expensive operation)\n", """            if (!params.language_candidates.empty()) {
                const auto selection = whisper_full_get_language_selection(ctx);
                json diagnostic = {{"mode", selection.detected_id >= 0 ? "candidate_auto" : "explicit"},
                    {"candidates", json::array()}, {"selected", whisper_lang_str(whisper_full_lang_id(ctx))}};
                for (const int id : params.language_candidates) diagnostic["candidates"].push_back(whisper_lang_str(id));
                if (selection.detected_id >= 0) {
                    diagnostic["detected"] = whisper_lang_str(selection.detected_id);
                    diagnostic["detected_probability"] = selection.detected_probability;
                    diagnostic["selected_probability"] = selection.selected_probability;
                }
                jres["language_selection"] = diagnostic;
            }
            // Only compute language probabilities if requested (expensive operation)
"""),
    ),
}


def patch_language_candidates(source: str, relative: str) -> str:
    for old, new in LANGUAGE_PATCHES[relative]:
        if source.count(old) != 1:
            raise ValueError("Pinned whisper.cpp language candidate anchor is missing or ambiguous")
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
    updates = [(path, patch_language_candidates(content.decode("utf-8"), str(path.relative_to(root))).encode("utf-8"))
               for path, content in updates]
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
    language_ok = all((root / relative).read_text().count(new) == 1
                      for relative, patches in LANGUAGE_PATCHES.items() for _old, new in patches)
    return (language_ok and all(whisper.count(new) == 1 for _old, new in SAMPLING_PATCHES + WINDOW_TRACE_PATCHES) and
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
