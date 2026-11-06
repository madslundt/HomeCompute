"""Guarded, metadata-only diagnostics for the pinned native decoder."""

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
    ("#include <cstdio>\n", ("#include <cstdio>\n#include <cerrno>\n#include <cstdlib>\n"
     "#include <fcntl.h>\n#include <sys/file.h>\n#include <sys/stat.h>\n#include <unistd.h>\n")),
    (TRACE_REQUEST_ANCHOR, TRACE_WRITER + TRACE_REQUEST_ANCHOR + TRACE_INITIALIZE),
    (TRACE_SEEK_ANCHOR, TRACE_SEEK_ANCHOR + TRACE_RECORD),
)


def patch_window_trace(source: str) -> str:
    for old, new in WINDOW_TRACE_PATCHES:
        if source.count(old) != 1:
            raise ValueError("Pinned whisper.cpp window trace anchor is missing or ambiguous")
        source = source.replace(old, new, 1)
    return source


DECODER_HELPERS = r"""// Decoder audit retains scalars/categories only; it never changes decoder state.
static void whisper_write_window_trace(const char * line, size_t length);
struct whisper_decoder_audit {
    int stop_step = -1, active_at_stop = 0, raw_count = 0, result_len = 0;
    int discarded_ordinary = 0, timestamp_index = -1, timestamp_offset = -1;
    bool completed = false, failed = false, eot = false, max_tokens = false, audio_end = false;
    char raw_suffix[5] = {}, retained_suffix[5] = {};
    double full_sum = NAN, prefix_sum = NAN, prefix_rank = NAN, complete_rank = NAN, eot_logprob = NAN;
};

static char whisper_audit_category(whisper_context * ctx, whisper_token id) {
    if (id == whisper_token_eot(ctx)) return 'E';
    if (id >= whisper_token_beg(ctx)) return 'T';
    return id < whisper_token_eot(ctx) ? 'O' : 'S';
}

static double whisper_audit_rank(double sum, int length, float alpha) {
    if (length <= 0) return NAN;
    return sum / (alpha > 0.0f ? pow((5.0 + length) / 6.0, alpha) : length);
}

static void whisper_capture_decoder_audit(whisper_context * ctx, const whisper_decoder & decoder,
        const whisper_full_params & params, int seek, int seek_end, int delta_min,
        int step, int active, whisper_decoder_audit & audit) {
    const int saved_errno = errno;
    const auto & tokens = decoder.sequence.tokens;
    audit.stop_step = step;
    audit.active_at_stop = active;
    audit.raw_count = static_cast<int>(tokens.size());
    audit.result_len = decoder.sequence.result_len;
    audit.completed = decoder.completed;
    audit.failed = decoder.failed;
    audit.eot = !tokens.empty() && tokens.back().id == whisper_token_eot(ctx);
    audit.max_tokens = params.max_tokens > 0 && step >= params.max_tokens;
    audit.audio_end = decoder.has_ts && seek + decoder.seek_delta + delta_min >= seek_end;
    const int retained = std::max(0, std::min(audit.result_len, audit.raw_count));
    audit.prefix_sum = 0.0;
    for (int k = 0; k < audit.raw_count; ++k) {
        const char category = whisper_audit_category(ctx, tokens[k].id);
        if (k < retained) audit.prefix_sum += tokens[k].plog;
        else if (category == 'O') ++audit.discarded_ordinary;
        if (category == 'T') {
            audit.timestamp_index = k;
            audit.timestamp_offset = tokens[k].id - whisper_token_beg(ctx);
        }
        if (k >= audit.raw_count - 4) audit.raw_suffix[k - std::max(0, audit.raw_count - 4)] = category;
        if (k < retained && k >= retained - 4) audit.retained_suffix[k - std::max(0, retained - 4)] = category;
    }
    audit.full_sum = decoder.sequence.sum_logprobs_all;
    audit.prefix_rank = whisper_audit_rank(audit.prefix_sum, audit.result_len, params.length_penalty);
    if (audit.eot) {
        audit.eot_logprob = tokens.back().plog;
        audit.complete_rank = whisper_audit_rank(audit.full_sum, audit.raw_count - 1, params.length_penalty);
    }
    errno = saved_errno;
}

static void whisper_emit_decoder_audit(const whisper_decoder_audit & audit, int seek, int seek_end,
        int attempt, int id, int selected, int active_final, bool failed_after_rank) {
    const int saved_errno = errno;
    char scores[5][32];
    const double values[] = {audit.full_sum, audit.prefix_sum, audit.prefix_rank,
                            audit.complete_rank, audit.eot_logprob};
    for (int k = 0; k < 5; ++k) {
        if (std::isfinite(values[k])) std::snprintf(scores[k], sizeof(scores[k]), "%.17g", values[k]);
        else std::snprintf(scores[k], sizeof(scores[k]), "null");
    }
    const char * reason = audit.failed ? "failed" : audit.eot ? "eot" :
            audit.completed && audit.max_tokens ? "max_tokens" :
            audit.completed && audit.audio_end ? "audio_end" :
            audit.completed ? "other_completed" : "decode_budget";
    char line[1024];
    const int length = std::snprintf(line, sizeof(line),
            "{\"version\":2,\"event\":\"decoder\",\"seek_before_cs\":%d,\"seek_end_cs\":%d,"
            "\"attempt\":%d,\"decoder_id\":%d,\"selected_decoder\":%d,"
            "\"stop_step\":%d,\"active_beams_at_stop\":%d,\"active_beams_at_final_step\":%d,"
            "\"pretrim_count\":%d,\"result_len\":%d,\"discarded_ordinary_count\":%d,"
            "\"last_timestamp_index\":%d,\"last_timestamp_offset\":%d,"
            "\"raw_suffix\":\"%s\",\"retained_suffix\":\"%s\",\"termination\":\"%s\","
            "\"completed\":%s,\"failed_before_rank\":%s,\"failed_after_rank\":%s,"
            "\"ended_eot\":%s,\"ended_max_tokens\":%s,\"ended_audio\":%s,"
            "\"complete_sum\":%s,\"prefix_sum\":%s,\"prefix_rank\":%s,"
            "\"complete_path_rank\":%s,\"terminal_eot_logprob\":%s}\n",
            seek, seek_end, attempt, id, selected, audit.stop_step, audit.active_at_stop, active_final,
            audit.raw_count, audit.result_len, audit.discarded_ordinary, audit.timestamp_index,
            audit.timestamp_offset, audit.raw_suffix, audit.retained_suffix, reason,
            audit.completed ? "true" : "false", audit.failed ? "true" : "false",
            failed_after_rank ? "true" : "false", audit.eot ? "true" : "false",
            audit.max_tokens ? "true" : "false", audit.audio_end ? "true" : "false",
            scores[0], scores[1], scores[2], scores[3], scores[4]);
    if (length > 0 && static_cast<size_t>(length) < sizeof(line)) {
        whisper_write_window_trace(line, static_cast<size_t>(length));
    }
    errno = saved_errno;
}

"""

DECODER_TRACE_PATCHES = (
    (TRACE_WRITER, DECODER_HELPERS + TRACE_WRITER),
    ("        int best_decoder_id = 0;\n", """        int best_decoder_id = 0;
        whisper_decoder_audit decoder_audits[WHISPER_MAX_DECODERS];
        int audit_count = 0, audit_attempt = 0, audit_active = 0, audit_step = -1;
"""),
    ("            n_decoders_cur = std::max(1, n_decoders_cur);\n", """            n_decoders_cur = std::max(1, n_decoders_cur);
            if (trace_windows) {
                audit_count = n_decoders_cur;
                audit_attempt = it;
                for (auto & audit : decoder_audits) audit = {};
            }
"""),
    ("                // sampling\n                // TODO: avoid memory allocations, optimize, avoid threads?\n", """                if (trace_windows) {
                    audit_step = i;
                    audit_active = 0;
                    for (int j = 0; j < n_decoders_cur; ++j) {
                        if (!state->decoders[j].completed && !state->decoders[j].failed) ++audit_active;
                    }
                }
                // sampling
                // TODO: avoid memory allocations, optimize, avoid threads?
"""),
    ("                // check if all decoders have finished (i.e. completed or failed)\n", """                if (trace_windows) {
                    for (int j = 0; j < n_decoders_cur; ++j) {
                        const auto & decoder = state->decoders[j];
                        if (decoder_audits[j].stop_step < 0 && (decoder.completed || decoder.failed)) {
                            whisper_capture_decoder_audit(ctx, decoder, params, seek, seek_end,
                                    delta_min, i, audit_active, decoder_audits[j]);
                        }
                    }
                }
                // check if all decoders have finished (i.e. completed or failed)
"""),
    ("""                for (int j = 0; j < n_decoders_cur; ++j) {
                    auto & decoder = state->decoders[j];

                    if (decoder.failed) {
""", """                for (int j = 0; j < n_decoders_cur; ++j) {
                    auto & decoder = state->decoders[j];
                    if (trace_windows && decoder_audits[j].stop_step < 0) {
                        whisper_capture_decoder_audit(ctx, decoder, params, seek, seek_end,
                                delta_min, audit_step, audit_active, decoder_audits[j]);
                    }

                    if (decoder.failed) {
"""),
    (TRACE_RECORD, TRACE_RECORD + """
            if (trace_windows) {
                for (int j = 0; j < audit_count; ++j) {
                    whisper_emit_decoder_audit(decoder_audits[j], seek - seek_delta, seek_end,
                            audit_attempt, j, best_decoder_id, audit_active, state->decoders[j].failed);
                }
            }
"""),
)


def patch_decoder_trace(source: str) -> str:
    for old, new in DECODER_TRACE_PATCHES:
        if source.count(old) != 1:
            raise ValueError("Pinned whisper.cpp decoder trace anchor is missing or ambiguous")
        source = source.replace(old, new, 1)
    return source
