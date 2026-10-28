# Local model access

## Active endpoint and model

For phones and browser apps over Tailscale, use
`https://home-core.tail479ad.ts.net:8443/v1`. It uses a publicly trusted
Let's Encrypt certificate and does not need a HomeCompute CA profile or an
API key. The certificate is checked daily and renewed through Tailscale before
expiry. Verify connectivity at
`https://home-core.tail479ad.ts.net:8443/v1/models`.

Use `https://ai.home.arpa/v1` without an API key from approved LAN or Tailscale
clients. The gateway supplies an internal credential limited to `automation`,
`home`, `automation-moe`, `stt-danish`, and `whisper-turbo`; `embedding` is added
with `sudo python3 scripts/setup-network-inference.py --enable-embedding`. Plapre is added to
that scope only after the explicit `--enable-plapre` migration. Explicit LiteLLM keys remain optional and retain
their own model scopes when valid. Empty, placeholder, expired, or unrecognized
credentials fall back to the common network aliases rather than preventing
keyless access. For SDKs that require a nonempty key, use the placeholder
`local`. Browser requests can originate from any origin; the gateway handles
preflight for model listing, Chat Completions, Responses, and audio transcription, including base-URL
checks, optional trailing slashes, and private-network approval headers. DNS,
certificate trust, and network admission still apply.
The current text model is selected through the stable LiteLLM alias
`automation-moe`. As of 2026-09-30, LiteLLM routes it to the owner-directed
Qwen3.8 Flash-Next quality trial (262K context); qualification is incomplete.
The old `coding` alias points to a stopped Qwen3.8 deployment. The HomeCompute
gateway and Spark inference service are already running.

Image mode stops ComfyUI after 30 minutes without image-job activity and starts
the configured text model again. Running and queued image jobs postpone this
switch; opening the image controls does not reset the idle timer.

On this Mac, the HomeCompute root CA is trusted by macOS and copied to
`~/.config/homecompute/home-core-root.crt` for Node/Python clients. Other
clients need the HomeCompute root CA installed in their OS trust store. Keep
`ai.home.arpa` as the TLS hostname. From a Tailscale-connected device, use the
subnet-routed endpoint `https://ai.home.arpa/v1`; if its DNS does not resolve,
fix the `ai.home.arpa` DNS override for the routed HomeCompute LAN before
changing client URLs. The direct Tailscale listener is also available at
`https://ai.home.arpa:8443/v1` when `ai.home.arpa` is resolved to
`100.110.248.102`.

Clients choosing `ai.home.arpa` still need private CA trust. An iPhone must trust
this CA independently of the Mac. Transfer the public CA
or a certificate-only profile to the phone, install it under Settings > Profile
Downloaded, then enable the Caddy root under Settings > General > About >
Certificate Trust Settings. [Apple's certificate trust instructions](https://support.apple.com/en-us/102390).
A certificate error stops the request before HTTP/CORS; verify that
`https://ai.home.arpa/v1/models` opens directly on the phone before testing the app.

## Text embeddings

Use `https://ai.home.arpa/v1` or the Tailscale gateway base URL above, with
`model: "embedding"` and no API key from approved networks. Send
`POST /v1/embeddings` with `input` as a string or array of strings and
`encoding_format: "float"`. The route serves `microsoft/harrier-oss-v1-0.6b`
at revision `f9b9dc8d367d443f2479d27aa5d8d2850c0774ee` on home-core's CPU.
Vectors have 1,024 dimensions; inputs are limited to 2,048 tokens and batches
to 16 inputs. The gateway does not add query instructions: document text is
plain, while retrieval queries need the model's `Instruct: ...\nQuery: ` prefix.

Daythread currently uses the same model through its private internal endpoint,
`http://homecompute-embeddings:8080/v1`. Its completed recording index remains
unchanged; changing the endpoint or model alias selects a new index and starts
automatic backfill.

OpenClaw memory uses Harrier through the authenticated capabilities bridge at
`https://homecompute-capabilities.home.arpa:19445/v1`. The bridge adds the memory
query instruction only to `input_type: query`, splits long native document
chunks and normalizes their pooled vectors. Each specialist searches its own
memory; session transcript indexing remains disabled. Apply the reviewed native
migration with `setup-openclaw-embeddings.py` after the bridge is available.

Open WebUI uses a separate embedding-only LiteLLM key. Its entrypoint backs up
and migrates the pinned version's saved RAG settings. A changed embedding identity
with existing indexed content requires a reviewed reindex before startup.

School and public research tools retain separate, expiring archives of passages
already returned by successful reads. Operations retrieval indexes reviewed
runbooks and recent infrastructure metadata receipts. Archive results include
provenance and are historical evidence; use live reads for changing facts.
Household searches rank current snapshots, and the Travel automation ranks
eligible offers against its configured descriptive preference. Embedding
outages preserve keyword search or the existing offer order.

As `homecompute-openclaw`, use
`python3 /opt/homecompute/openclaw-communication/scripts/semantic-index.py school status`
to inspect source IDs, replacing `school` with `research` or `operations` for
those domains. `warm` backfills up to 80 pending passages without a query;
`delete --source SOURCE_ID` removes that source and its vectors and prevents
replay. Restore deletion tombstones with the archive before serving searches.
Archive coverage is bounded and populated by reads; it is not a complete import
of school history or all conversations.

## Speech transcription

Use the same base URL `https://home-core.tail479ad.ts.net:8443/v1`, model
`stt-danish`, and no API key. Send a multipart `POST /v1/audio/transcriptions`
with `file` and `model=stt-danish`. JSON is the default response format;
`language=da` and `response_format=json` may also be supplied explicitly.
The model is the running Spark GPU service `syvai/hviske-v5.3`, pinned at
`5d1a09822018702dc51d763e3a867b62d26b3501`. Home-core routes uploads through
LiteLLM and a dedicated SSH forward to the Spark's loopback HTTP relay. The
existing Home Assistant Wyoming service stays separate. CORS and OPTIONS
support applies to transcription, including placeholder authorization headers.
The gateway bounds the request body at 32 MB; the Spark HTTP relay accepts
recordings up to 10 MB and 60 seconds, with four concurrent transcriptions.
It converts common phone formats, including M4A/AAC, to PCM WAV before
calling the model.

For English or mixed-language recordings, choose `model=whisper-turbo` on the
same endpoint. It runs `openai/whisper-large-v3-turbo`, pinned at
`41f01f3fe87f28c78e2fbf8b568835947dd65ed9`, in its own Spark GPU container.
`language=en` is optional; omit the language for automatic detection. JSON is
the default, and the same 10 MB/60 second recording limits apply. The Whisper
relay admits one transcription at a time; concurrent requests may return 429.
The model uses eager execution and a bounded 4% GPU memory allocation.
An acceptance check on 2026-10-14 ran Whisper, Hviske, and a roughly 3,000-word
LLM prompt concurrently. All returned HTTP 200; Whisper transcribed 16 seconds
of synthetic English speech in 5.6 seconds. Memory monitoring recorded about
7.4 GiB available and no swap-out. This checks normal coexistence, not maximum
LLM context/concurrency or accuracy on the owner's recordings.

`whisper-1` and `stt-english` are not aliases. Requests never silently fall back
to home-core's CPU Faster Whisper service.

## Speech synthesis

The candidate Plapre alias uses the same OpenAI-compatible base URL. Send
`POST /v1/audio/speech` with `model: "plapre"`, `voice: "danish-default"`,
`input`, and `response_format: "wav"`. The gateway's private adapter translates
this request to Plapre's Wyoming service. It is available after the
`speech-proxies` profile is built and started, the Plapre upstream is healthy,
and the network key has been migrated with
`sudo python3 scripts/setup-network-inference.py --enable-plapre`. Plapre is
still a candidate pending its voice, latency, and pronunciation qualification.

## Credentials

Optional independently revocable, one-year LiteLLM keys are stored on this Mac in
`~/.config/homecompute/client-keys.json` and exported from
`~/.config/homecompute/clients.env`. The directory is mode 0700 and both files
are mode 0600. Each key can call only `automation-moe`; none is the LiteLLM
master key. New zsh login shells load these environment variables automatically.
For the current shell, run:

```sh
source ~/.config/homecompute/clients.env
```

The variables are `HOMECOMPUTE_PI_API_KEY`, `HOMECOMPUTE_API_KEY` (Codex),
and `HOMECOMPUTE_SCRIPT_API_KEY`. Revoke an individual key through the
LiteLLM key-management API on `home-core`; do not share the administrative key
with a client.

## Pi

The Pi `homecompute` provider is configured in `~/.pi/agent/models.json` and
uses `openai-completions`. Select the running model with:

```sh
pi --provider homecompute --model automation-moe
```

`NODE_EXTRA_CA_CERTS` is set in the local client environment so Node trusts the
HomeCompute CA. The Pi config file is mode 0600.

## Codex CLI

The `scripts/codex-homecompute` launcher uses a separate Codex home at
`~/.config/homecompute/codex`, defaults to `automation-moe`, and avoids loading
the regular Codex plugins, MCP servers, and skill catalog into local requests.
This keeps its request below the live model's 64K limit and leaves your regular
Codex configuration and default cloud model untouched. Run it with a prompt or
use Codex CLI options as usual, for example:

```sh
source ~/.config/homecompute/clients.env
scripts/codex-homecompute
# or a one-shot task:
scripts/codex-homecompute exec "Summarize this repository"
```

This is a separate CLI profile; it does not change the Codex desktop app's
current thread or model.

## Codex desktop app

The existing `gb10` provider in `~/.codex/config.toml` points at HomeCompute.
The Codex-only scoped key is also in `~/.codex/.env` (mode 0600), which the
app reads at startup. To make the desktop app use the local model for new
sessions, change the top-level model settings in `~/.codex/config.toml` to:

```toml
model = "automation-moe"
model_provider = "gb10"
```

Then fully quit and reopen the desktop app and start a new Codex session. To
return to the current cloud default, restore `model = "gpt-6-luna"` and remove
or restore the `model_provider` line. The app has not been switched or restarted
as part of this setup. The current custom provider may need a short prompt and
fewer connected tools to stay within the model's 64K context; the isolated CLI
launcher is the verified path if a desktop request reports a context-length
error.

## Python API example

The tracked stdlib example uses the installed CA and sends a scripts key only
when `HOMECOMPUTE_SCRIPT_API_KEY` is set. A keyless request is:

```sh
env -u HOMECOMPUTE_SCRIPT_API_KEY python3 scripts/homecompute_chat.py \
  --model automation-moe "Reply with READY"
```

The example accepts any alias granted to the API key with `--model`; it keeps
the existing `automation-moe` default. Plain text remains the default; opt into
the example function schema and choose whether the model may decide to call it
(`auto`) or must call it (`required`):

```sh
python3 scripts/homecompute_chat.py --model automation --tools --tool-choice auto \
  "Read the temperature in the kitchen"
```

When a call is returned, the helper prints the structured `tool_calls` JSON.
It does not execute functions; the calling application owns tool execution and
must send the tool result back as the next conversation turn. Use
`--tool-choice required` for a required-call request. The normal text-only
request shape is unchanged when `--tools` is omitted.

For another OpenAI-compatible SDK, set `base_url="https://ai.home.arpa/v1"`,
`api_key="local"` for keyless network access, choose an exposed alias, and pass
`tools=[...]` with `tool_choice="auto"` or `"required"`. An explicit scoped key
can also be used and restricts aliases according to its permissions.
Read `message.tool_calls` when present; ordinary responses continue to use
`message.content`. Required/automatic function selection and returned tool
calls are exercised by each model profile's guarded smoke command.
