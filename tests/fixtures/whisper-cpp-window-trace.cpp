// Exact excerpts from whisper.cpp f049fff95a089aa9969deb009cdd4892b3e74916.
// Full src/whisper.cpp SHA256 a884ff3eb3bbf7558958c4d06a299cd8d97d87a7d5c7aac9138b8ff6a2ef04d7.
#include <cstdio>
// BEGIN REQUEST
int whisper_full_with_state(
        struct whisper_context * ctx,
          struct whisper_state * state,
    struct whisper_full_params   params,
                   const float * samples,
                           int   n_samples) {
    // clear old results
    auto & result_all = state->result_all;

    result_all.clear();
// END REQUEST
// BEGIN RESULT
            const auto & best_decoder = state->decoders[best_decoder_id];

            auto seek_delta = best_decoder.seek_delta;
            const auto result_len = best_decoder.sequence.result_len;

            const auto & tokens_cur = best_decoder.sequence.tokens;

            // [EXPERIMENTAL] Token-level timestamps with DTW
            const auto n_segments_before = state->result_all.size();

            const bool is_no_speech = (state->no_speech_prob > params.no_speech_thold &&
                best_decoder.sequence.avg_logprobs < params.logprob_thold);

// END RESULT
// BEGIN SEEK
            // ref: https://github.com/ggml-org/whisper.cpp/pull/2629
            const bool max_tokens_timestamp_ending = params.max_tokens > 0 &&
                !params.single_segment &&
                tokens_cur.size() > (size_t) params.max_tokens;

            const bool single_timestamp_ending = tokens_cur.size() > 1 &&
                !max_tokens_timestamp_ending &&
                tokens_cur[tokens_cur.size() - 2].id < whisper_token_beg(ctx) &&
                tokens_cur[tokens_cur.size() - 1].id > whisper_token_beg(ctx);
            if (single_timestamp_ending) {
                WHISPER_LOG_DEBUG("single timestamp ending - skip entire chunk\n");
                seek_delta = std::min(seek_end - seek, WHISPER_CHUNK_SIZE * 100);
            }

            // update audio window
            seek += seek_delta;

            WHISPER_LOG_DEBUG("seek = %d, seek_delta = %d\n", seek, seek_delta);
// END SEEK
