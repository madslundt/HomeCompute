#!/usr/bin/env python3
"""Apply guarded CUDA decoding and opt-in metadata tracing to pinned whisper.cpp."""
from __future__ import annotations

import argparse
import subprocess
from hashlib import sha256
from pathlib import Path

UPSTREAM_COMMIT = "f049fff95a089aa9969deb009cdd4892b3e74916"
PATCH_VERSION = "strict-gpu-timestamp-normalization-v8"
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


# Execute exactly the reviewed helper bytes, without a second file read.
TRACE_PATCHER_SHA256 = "244bfc3fcd3bef4c6cf1dfe0fc98a69aaab98fb1626c2a8c339d0a12d975bf04"
trace_path = Path(__file__).with_name("whisper-cpp-trace-patch.py")
trace_bytes = trace_path.read_bytes()
if sha256(trace_bytes).hexdigest() != TRACE_PATCHER_SHA256:
    raise ValueError("Pinned whisper.cpp trace helper checksum changed")
trace_namespace = {}
exec(compile(trace_bytes, str(trace_path), "exec"), trace_namespace)  # noqa: S102 - checksum-verified source
TRACE_REQUEST_ANCHOR = trace_namespace["TRACE_REQUEST_ANCHOR"]
TRACE_WRITER = trace_namespace["TRACE_WRITER"]
TRACE_INITIALIZE = trace_namespace["TRACE_INITIALIZE"]
TRACE_SEEK_ANCHOR = trace_namespace["TRACE_SEEK_ANCHOR"]
TRACE_RECORD = trace_namespace["TRACE_RECORD"]
WINDOW_TRACE_PATCHES = trace_namespace["WINDOW_TRACE_PATCHES"]
DECODER_TRACE_PATCHES = trace_namespace["DECODER_TRACE_PATCHES"]
patch_window_trace = trace_namespace["patch_window_trace"]
patch_decoder_trace = trace_namespace["patch_decoder_trace"]


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


TIMESTAMP_NORMALIZATION_PATCHES = (
    ("""            if (timestamp_logprob > max_text_token_logprob) {
                for (int i = 0; i < vocab.token_beg; ++i) {
                    logits[i]   = -INFINITY;
                    logprobs[i] = -INFINITY;
                }
            } else {
""", """            if (timestamp_logprob > max_text_token_logprob) {
                for (int i = 0; i < vocab.token_beg; ++i) {
                    logits[i]   = -INFINITY;
                    logprobs[i] = -INFINITY;
                }
                // Score the surviving conditional distribution after this mask.
                whisper_compute_logprobs(logits, n_logits, logprobs);
            } else {
"""),
)


def patch_timestamp_normalization(source: str) -> str:
    for old, new in TIMESTAMP_NORMALIZATION_PATCHES:
        if source.count(old) != 1:
            raise ValueError("Pinned whisper.cpp timestamp normalization anchor is missing or ambiguous")
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
    source = patch_decoder_trace(patch_window_trace(source))
    source = patch_timestamp_normalization(source)
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
    return (language_ok and all(whisper.count(new) == 1 for _old, new in
                               SAMPLING_PATCHES + WINDOW_TRACE_PATCHES + DECODER_TRACE_PATCHES + TIMESTAMP_NORMALIZATION_PATCHES) and
            SAMPLING_PATCHES[-1][0] not in whisper and
            TIMESTAMP_NORMALIZATION_PATCHES[0][0] not in whisper and
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
