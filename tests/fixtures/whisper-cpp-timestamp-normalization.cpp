/*
MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
*/

// Exact excerpts from whisper.cpp f049fff95a089aa9969deb009cdd4892b3e74916.
// Full src/whisper.cpp SHA256 a884ff3eb3bbf7558958c4d06a299cd8d97d87a7d5c7aac9138b8ff6a2ef04d7.
// BEGIN PROBABILITIES
static void whisper_compute_logprobs(
                const std::vector<float> & logits,
                              const int    n_logits,
                      std::vector<float> & logprobs) {
    const float logit_max = *std::max_element(logits.begin(), logits.end());
    float logsumexp = 0.0f;
    for (int i = 0; i < n_logits; ++i) {
        if (logits[i] > -INFINITY) {
            logsumexp += expf(logits[i] - logit_max);
        }
    }
    logsumexp = logf(logsumexp) + logit_max;

    for (int i = 0; i < n_logits; ++i) {
        if (logits[i] > -INFINITY) {
            logprobs[i] = logits[i] - logsumexp;
        } else {
            logprobs[i] = -INFINITY;
        }
    }
}

static void whisper_compute_probs(
    const std::vector<float> & logits,
                  const int    n_logits,
    const std::vector<float> & logprobs,
          std::vector<float> & probs)     {
    for (int i = 0; i < n_logits; ++i) {
        if (logits[i] == -INFINITY) {
            probs[i] = 0.0f;
        } else {
            probs[i] = expf(logprobs[i]);
        }
    }
}


// END PROBABILITIES
// BEGIN TEMPERATURE
        if (temperature > 0.0f) {
            for (int i = 0; i < n_logits; i++) {
                logits[i] /= temperature;
            }
        }

// END TEMPERATURE
// BEGIN TIMESTAMP_MASK
        // populate the logprobs array (log_softmax)
        whisper_compute_logprobs(logits, n_logits, logprobs);

        // if sum of probability over timestamps is above any other token, sample timestamp
        // ref: https://github.com/openai/whisper/blob/0b1ba3d46ebf7fe6f953acfd8cad62a4f851b49f/whisper/decoding.py#L431-L437
        {
            // logsumexp over timestamps
            float timestamp_logprob = -INFINITY;
            {
                float logsumexp = 0.0f;
                const float logprob_max = *std::max_element(logprobs.begin() + vocab.token_beg, logprobs.end());
                for (int i = vocab.token_beg; i < n_logits; ++i) {
                    if (logprobs[i] > -INFINITY) {
                        logsumexp += expf(logprobs[i] - logprob_max);
                    }
                }
                if (logsumexp > 0.0f) {
                    timestamp_logprob = logf(logsumexp) + logprob_max;
                }
            }

            const float max_text_token_logprob = *std::max_element(logprobs.begin(), logprobs.begin() + vocab.token_beg);

            //WHISPER_LOG_INFO("timestamp_logprob=%f max_text_token_logprob=%f\n", timestamp_logprob, max_text_token_logprob);

            if (timestamp_logprob > max_text_token_logprob) {
                for (int i = 0; i < vocab.token_beg; ++i) {
                    logits[i]   = -INFINITY;
                    logprobs[i] = -INFINITY;
                }
            } else {
                if (params.n_grammar_rules > 0) {
                    whisper_suppress_invalid_grammar(ctx, params, logits, decoder.grammar);

                    // populate the logprobs array (log_softmax)
                    {
                        const float logit_max = *std::max_element(logits.begin(), logits.end());
                        float logsumexp = 0.0f;
                        for (int i = 0; i < n_logits; ++i) {
                            if (logits[i] > -INFINITY) {
                                logsumexp += expf(logits[i] - logit_max);
                            }
                        }
                        logsumexp = logf(logsumexp) + logit_max;

                        for (int i = 0; i < n_logits; ++i) {
                            if (logits[i] > -INFINITY) {
                                logprobs[i] = logits[i] - logsumexp;
                            } else {
                                logprobs[i] = -INFINITY;
                            }
                        }
                    }
                }
            }
        }
    }

    // compute probs
    whisper_compute_probs(logits, n_logits, logprobs, probs);

// END TIMESTAMP_MASK
