# Local model access

## Active endpoint and model

Use `https://ai.home.arpa/v1` with a LiteLLM virtual key as a Bearer token.
The current text model is `automation-moe` (NVIDIA Qwen3.6 35B A3B NVFP4,
64K routed context). The old `coding` alias points to a stopped Qwen3.8 deployment. The
HomeCompute gateway and Spark inference service are already running.

On this Mac, the HomeCompute root CA is trusted by macOS and copied to
`~/.config/homecompute/home-core-root.crt` for Node/Python clients. Other
clients need the HomeCompute root CA installed in their OS trust store. Keep
`ai.home.arpa` as the TLS hostname. From a Tailscale-connected device, use the
subnet-routed endpoint `https://ai.home.arpa/v1`; if its DNS does not resolve,
fix the `ai.home.arpa` DNS override for the routed HomeCompute LAN before
changing client URLs. The direct Tailscale listener is also available at
`https://ai.home.arpa:8443/v1` when `ai.home.arpa` is resolved to
`100.110.248.102`.

## Credentials

Three independently revocable, one-year LiteLLM keys are stored on this Mac in
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

The tracked stdlib example uses the separate scripts key and the installed CA:

```sh
source ~/.config/homecompute/clients.env
python3 scripts/homecompute_chat.py "Reply with READY"
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
`api_key=os.environ["HOMECOMPUTE_SCRIPT_API_KEY"]`, choose an alias allowed by
that key, and pass `tools=[...]` with `tool_choice="auto"` or `"required"`.
Read `message.tool_calls` when present; ordinary responses continue to use
`message.content`. Required/automatic function selection and returned tool
calls are exercised by each model profile's guarded smoke command.
