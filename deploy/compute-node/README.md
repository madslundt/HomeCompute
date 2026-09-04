# Compute-node deployment

`compose.yaml` defines the Phase C text-inference baseline for
`home-spark`. It starts one pinned vLLM container, exposes logical aliases,
uses external secret files, runs with a read-only root filesystem and dropped
capabilities, bounds logs, and publishes to loopback by default.

`model-fetch` is a one-shot acquisition profile with outbound access and the
model-registry token. `text-primary` mounts the resulting Hugging Face cache
read-only, enables offline mode, has no registry token, and runs on an internal
Docker network. This keeps publisher remote code away from registry credentials
and internet egress during inference. Acquisition also verifies the configured
chat-template hash before the runtime is started.

The vLLM API key protects the OpenAI-compatible paths, not every endpoint on
the process. The host firewall must therefore allow the published port only
from `home-core`, and the gateway must expose only its route allow-list.
See the [vLLM security guidance](https://docs.vllm.ai/en/stable/usage/security/).

Do not run the Compose file directly for a production installation. Use
[`setup-compute-node.sh`](../../scripts/setup-compute-node.sh), which validates
the complete release tuple, host, paths, service identity, secrets, bind
policy, and provenance before deployment.

Changing the model, image, revision, tokenizer, template, parser,
quantization, context, backend, or speculative-decoding setting creates a new
tuple that must pass the full qualification suite.
