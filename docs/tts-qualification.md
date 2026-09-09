# Danish TTS qualification

This repo-only plan qualifies Plapre Nano v2 on the GB10 for Danish output and
keeps Piper `da_DK-talesyntese-medium` on `home-core` CPU as the independent
fallback. It does not install a runtime, download weights, or activate a route.

Under representative simultaneous LLM load, each candidate must have at least
20 warm samples, zero request failures, warm p95 first audio no greater than
750 ms, and warm p95 real-time factor no greater than 0.5. Every clip needs
ratings from at least three native Danish reviewers, at least 95% pronunciation
passes, and mean naturalness of at least 3.5/5. The set covers Danish names,
addresses, numbers, times, devices, compounds, and mixed English tokens.

The measured first-audio value runs from request start until the first WAV body
byte. The harness derives RTF from total request time divided by decoded WAV
duration. If a service buffers the complete utterance, report that behavior;
do not present an internal model timestamp as user-perceived first audio.

## Measurement and listening

Start each already-installed candidate yourself and keep it warm. Use the same
authenticated OpenAI-compatible boundary for each:

```bash
python3 scripts/tts-qualification.py measure \
  --config config/tts-qualification.json \
  --candidate plapre-nano-v2-gb10 \
  --endpoint https://HOST/v1/audio/speech \
  --api-key-file /PATH/TO/KEY \
  --audio-root /PRIVATE/PATH/audio \
  --output /PRIVATE/PATH/plapre-measurements.jsonl
```

Repeat for `piper-talesyntese-home-core`. Run the complete warm matrix while
the normal Qwen text model is serving representative concurrent work. Keep
cold-start results separate.

Create the blinded packet after all WAVs exist:

```bash
python3 scripts/tts-qualification.py prepare \
  --config config/tts-qualification.json \
  --audio-root /PRIVATE/PATH/audio \
  --output /PRIVATE/PATH/listening-packet
```

Give reviewers only `clips/`, `ratings.csv`, and `preferences.csv`. Keep the
mapping private until scoring is final. Private directories must be `0700` and
files `0600`; the harness refuses symlinks and pre-existing output artifacts.

Evaluate the combined evidence:

```bash
python3 scripts/tts-qualification.py evaluate \
  --config config/tts-qualification.json \
  --measurements /PRIVATE/PATH/all-measurements.jsonl \
  --mapping /PRIVATE/PATH/listening-packet/private-mapping.json \
  --ratings /PRIVATE/PATH/all-ratings.csv \
  --preferences /PRIVATE/PATH/all-preferences.csv \
  --output /PRIVATE/PATH/qualification-result.json
```

## Mis-speaking recovery gate

Promotion additionally requires four integration scenarios at the stable TTS
boundary:

1. Hviske ASR verifies the first Plapre synthesis and it is returned.
2. ASR detects a mismatch; the service resynthesizes exactly once and the
   verified second result is returned.
3. ASR rejects the second synthesis; the request falls back to Piper.
4. Plapre times out or is unavailable; the request falls back to Piper.

Record route, attempt count, verifier outcome, latency, and request ID without
placing user audio or text in the repository. The fallback must remain usable
during GB10 contention and restart. Danish requests must never reach Qwen3-TTS.
The route remains inactive until both the quantitative/listening result and
these recovery scenarios pass.
