"""Pinned request-scoped local EOT ranking; old public parameter ABI is untouched."""
from __future__ import annotations

NATIVE_HELPERS = r'''
struct whisper_local_eot_candidate {
    double score = -INFINITY;
    int64_t endpoint = -1;
    bool sole_eot = false;
};

static bool whisper_valid_endpoint_spans(const int32_t * spans, int count, int n_samples) {
    if (n_samples <= 0 || n_samples > 30*WHISPER_SAMPLE_RATE || count < 0 ||
            count > (n_samples + 511)/512 || (count > 0 && spans == nullptr)) return false;
    int32_t previous = -1;
    for (int i = 0; i < count; ++i) {
        const int32_t start = spans[2*i], end = spans[2*i + 1];
        if (start < 0 || start >= end || end > n_samples || start <= previous ||
                start % 512 != 0 || (end % 512 != 0 && end != n_samples)) return false;
        previous = end;
    }
    return true;
}

static int64_t whisper_retained_endpoint(whisper_context * ctx,
        const whisper_decoder & decoder, int seek, int n_samples) {
    const auto & sequence = decoder.sequence;
    if (!decoder.completed || decoder.failed || sequence.result_len <= 0 ||
            sequence.tokens.size() < static_cast<size_t>(sequence.result_len)) return -1;
    // An earlier timestamp followed by ordinary tokens is NOT a sequence endpoint.
    const auto id = sequence.tokens[sequence.result_len - 1].id;
    const auto first = whisper_token_beg(ctx);
    if (id <= first || id > first + 1500 || seek < 0) return -1;
    const int64_t sample = static_cast<int64_t>(seek)*160 + static_cast<int64_t>(id - first)*320;
    return sample > 0 && sample < n_samples ? sample : -1;
}

static int whisper_local_eot_winner(const whisper_local_eot_candidate * paths, int count,
        int legacy, int complete, const int32_t * spans, int n_spans, int n_samples,
        int32_t & span_start, int32_t & span_end) {
    span_start = span_end = -1;
    if (legacy < 0 || legacy >= count || complete < 0 || complete >= count) return legacy;
    const int64_t a = paths[legacy].endpoint, b = paths[complete].endpoint;
    if (0 < a && a < n_samples && 0 < b && b < n_samples) {
        for (int i = 0; i < n_spans; ++i) {
            if (spans[2*i] < a && a < spans[2*i + 1] && spans[2*i] < b && b < spans[2*i + 1]) {
                span_start = spans[2*i]; span_end = spans[2*i + 1];
                break;
            }
        }
    }
    return span_start >= 0 && b > a && paths[legacy].sole_eot && paths[complete].sole_eot &&
            std::isfinite(paths[legacy].score) && std::isfinite(paths[complete].score) ? complete : legacy;
}

static void whisper_trace_local_eot(int seek, int attempt, int legacy, int complete, int chosen,
        const whisper_local_eot_candidate * paths, int32_t span_start, int32_t span_end) {
    const int saved_errno = errno;
    char line[768];
    const int n = std::snprintf(line, sizeof(line),
            "{\"version\":3,\"event\":\"local_eot_rank\",\"seek_before_cs\":%d,\"attempt\":%d,"
            "\"legacy_decoder\":%d,\"complete_decoder\":%d,\"chosen_decoder\":%d,"
            "\"legacy_endpoint_sample\":%lld,\"complete_endpoint_sample\":%lld,"
            "\"same_speech_segment\":%s,\"strictly_later\":%s,"
            "\"span_start_sample\":%d,\"span_end_sample\":%d}\n",
            seek, attempt, legacy, complete, chosen,
            static_cast<long long>(paths[legacy].endpoint), static_cast<long long>(paths[complete].endpoint),
            span_start >= 0 ? "true" : "false",
            paths[complete].endpoint > paths[legacy].endpoint && paths[legacy].endpoint > 0 ? "true" : "false",
            span_start, span_end);
    if (n > 0 && static_cast<size_t>(n) < sizeof(line)) whisper_write_window_trace(line, static_cast<size_t>(n));
    errno = saved_errno;
}
'''

SIGNATURE = '''int whisper_full_with_state(
        struct whisper_context * ctx,
          struct whisper_state * state,
    struct whisper_full_params   params,
                   const float * samples,
                           int   n_samples) {'''
IMPL_SIGNATURE = '''static int whisper_full_with_state_impl(
        struct whisper_context * ctx,
          struct whisper_state * state,
    struct whisper_full_params   params,
                   const float * samples,
                           int   n_samples,
                 const int32_t * endpoint_spans, int n_endpoint_spans, bool endpoint_gate_enabled) {'''
WRAPPERS = '''int whisper_full_with_state(
        struct whisper_context * ctx,
          struct whisper_state * state,
    struct whisper_full_params   params,
                   const float * samples,
                           int   n_samples) {
    return whisper_full_with_state_impl(ctx, state, params, samples, n_samples, nullptr, 0, false);
}

int whisper_full_with_endpoint_spans(
        struct whisper_context * ctx,
    struct whisper_full_params   params,
                   const float * samples,
                           int   n_samples,
                 const int32_t * spans,
                           int   n_spans) {
    // Validate in the core too: callers other than the server cannot bypass this.
    if (ctx == nullptr || samples == nullptr || params.no_timestamps || params.vad ||
            params.detect_language || params.offset_ms != 0 || params.duration_ms != 0 ||
            !whisper_valid_endpoint_spans(spans, n_spans, n_samples)) return -12;
    return whisper_full_with_state_impl(ctx, ctx->state, params, samples, n_samples, spans, n_spans, true);
}

'''
HEADER_DECLARATION = '''    // Optional request-scoped raw-Silero sample spans; no parameter-struct ABI change.
    // Synchronous call: caller retains ownership of 2*n_spans int32 values.
    WHISPER_API int whisper_full_with_endpoint_spans(
                struct whisper_context * ctx,
            struct whisper_full_params   params,
                           const float * samples,
                                   int   n_samples,
                         const int32_t * spans,
                                   int   n_spans);

'''
RANK_INITIAL = '''                double best_score = -INFINITY;

                for (int j = 0; j < n_decoders_cur; ++j) {'''
RANK_LOCAL = '''                double best_score = -INFINITY;
                double complete_score = -INFINITY;
                int complete_decoder_id = best_decoder_id;
                whisper_local_eot_candidate local_paths[WHISPER_MAX_DECODERS];

                for (int j = 0; j < n_decoders_cur; ++j) {'''
TRIM = '''                    decoder.sequence.tokens.resize(decoder.sequence.result_len);
                    whisper_sequence_score(params, decoder.sequence);
'''
TRIM_LOCAL = '''                    double terminal_eot = 0.0;
                    if (endpoint_gate_enabled) {
                        auto & candidate = local_paths[j];
                        candidate.endpoint = whisper_retained_endpoint(ctx, decoder, seek, n_samples);
                        candidate.sole_eot = decoder.completed && decoder.sequence.result_len > 0 &&
                                decoder.sequence.tokens.size() == static_cast<size_t>(decoder.sequence.result_len) + 1 &&
                                decoder.sequence.tokens.back().id == whisper_token_eot(ctx);
                        if (candidate.sole_eot) terminal_eot = decoder.sequence.tokens.back().plog;
                    }
                    decoder.sequence.tokens.resize(decoder.sequence.result_len);
                    whisper_sequence_score(params, decoder.sequence);
'''
SELECT = '''                    if (best_score < decoder.sequence.score) {
                        best_score = decoder.sequence.score;
                        best_decoder_id = j;
                    }
'''
SELECT_LOCAL = SELECT + '''                    if (endpoint_gate_enabled) {
                        auto & candidate = local_paths[j];
                        candidate.score = decoder.sequence.score;
                        if (candidate.sole_eot) {
                            double penalty = decoder.sequence.result_len;
                            if (params.length_penalty > 0.0f) penalty = pow((5.0 + penalty)/6.0, params.length_penalty);
                            candidate.score = (decoder.sequence.sum_logprobs + terminal_eot)/penalty;
                        }
                        if (complete_score < candidate.score) {
                            complete_score = candidate.score;
                            complete_decoder_id = j;
                        }
                    }
'''
BEST = '''                WHISPER_LOG_DEBUG("%s: best decoder = %d\\n", __func__, best_decoder_id);'''
BEST_LOCAL = '''                if (endpoint_gate_enabled) {
                    const int legacy_decoder_id = best_decoder_id;
                    int32_t span_start = -1, span_end = -1;
                    best_decoder_id = whisper_local_eot_winner(local_paths, n_decoders_cur,
                            legacy_decoder_id, complete_decoder_id, endpoint_spans, n_endpoint_spans,
                            n_samples, span_start, span_end);
                    if (trace_windows) whisper_trace_local_eot(seek, it, legacy_decoder_id,
                            complete_decoder_id, best_decoder_id, local_paths, span_start, span_end);
                }
''' + BEST
RANK_PATCHES = ((RANK_INITIAL, RANK_LOCAL), (TRIM, TRIM_LOCAL), (SELECT, SELECT_LOCAL), (BEST, BEST_LOCAL))

SERVER_PARSER = r'''static bool parse_endpoint_spans(const std::string & raw, int n_samples, std::vector<int32_t> & out) {
    if (raw.size() > 32768 || n_samples <= 0 || n_samples > 480000) return false;
    std::vector<int32_t> parsed;
    size_t position = 0;
    int32_t previous = -1;
    auto integer = [&](int32_t & result) {
        const size_t begin = position;
        int32_t value = 0;
        while (position < raw.size() && raw[position] >= '0' && raw[position] <= '9') {
            const int digit = raw[position++] - '0';
            if (value > (n_samples - digit)/10) return false;
            value = value*10 + digit;
            if (value > n_samples) return false;
        }
        result = value;
        return position > begin;
    };
    while (position < raw.size()) {
        int32_t start, end;
        if (parsed.size()/2 >= static_cast<size_t>((n_samples + 511)/512) || !integer(start) ||
                position == raw.size() || raw[position++] != ':' || !integer(end)) return false;
        if (start >= end || start <= previous || start % 512 != 0 ||
                (end % 512 != 0 && end != n_samples)) return false;
        parsed.push_back(start); parsed.push_back(end); previous = end;
        if (position < raw.size() && (raw[position++] != ';' || position == raw.size())) return false;
    }
    out.swap(parsed);
    return true;
}

'''
SERVER_CALL = '''            if (whisper_full_parallel(ctx, wparams, pcmf32.data(), pcmf32.size(), params.n_processors) != 0) {'''
SERVER_LOCAL = '''            std::vector<int32_t> endpoint_spans;
            const bool local_eot = req.has_file("homecompute_eot_spans");
            if (local_eot && (req.files.count("homecompute_eot_spans") != 1 || params.n_processors != 1 ||
                    wparams.no_timestamps || wparams.vad || wparams.detect_language ||
                    wparams.offset_ms != 0 || wparams.duration_ms != 0 ||
                    !parse_endpoint_spans(req.get_file_value("homecompute_eot_spans").content,
                                          static_cast<int>(pcmf32.size()), endpoint_spans))) {
                res.status = 400;
                res.set_content("{\\"error\\":\\"invalid endpoint spans\\"}", "application/json");
                return;
            }
            const int status = local_eot ? whisper_full_with_endpoint_spans(ctx, wparams,
                    pcmf32.data(), static_cast<int>(pcmf32.size()),
                    endpoint_spans.empty() ? nullptr : endpoint_spans.data(), static_cast<int>(endpoint_spans.size()/2)) :
                    whisper_full_parallel(ctx, wparams, pcmf32.data(), pcmf32.size(), params.n_processors);
            if (status != 0) {'''
FILE_PATCHES = {
    'src/whisper.cpp': ((SIGNATURE, NATIVE_HELPERS + '\n' + IMPL_SIGNATURE),
                        ('int whisper_full(\n', WRAPPERS + 'int whisper_full(\n')) + RANK_PATCHES,
    'include/whisper.h': (('    WHISPER_API int whisper_full_with_state(\n', HEADER_DECLARATION + '    WHISPER_API int whisper_full_with_state(\n'),),
    'examples/server/server.cpp': (('void get_req_parameters(const Request & req, whisper_params & params)\n', SERVER_PARSER + 'void get_req_parameters(const Request & req, whisper_params & params)\n'), (SERVER_CALL, SERVER_LOCAL)),
}

def patch_rank(source: str) -> str:
    return _replace(source, RANK_PATCHES)

def _replace(source, patches):
    for old, new in patches:
        if source.count(old) != 1:
            raise ValueError('Pinned local EOT patch anchor is missing or ambiguous')
        source = source.replace(old, new, 1)
    return source

def apply_file(source: str, relative: str) -> str:
    return _replace(source, FILE_PATCHES[relative])

def undo_file(source: str, relative: str) -> str:
    return _replace(source, tuple((new, old) for old, new in reversed(FILE_PATCHES[relative])))
