# Compute-node deployment

`compose.yaml` defines the Phase C text-inference baseline for
`ai-compute-01`. It starts one pinned vLLM container, exposes logical aliases,
uses external secret files, runs with a read-only root filesystem and dropped
capabilities, bounds logs, and publishes to loopback by default.

Do not run the Compose file directly for a production installation. Use
[`setup-compute-node.sh`](../../scripts/setup-compute-node.sh), which validates
the complete release tuple, host, paths, service identity, secrets, bind
policy, and provenance before deployment.

Changing the model, image, revision, tokenizer, template, parser,
quantization, context, backend, or speculative-decoding setting creates a new
tuple that must pass the full qualification suite.
