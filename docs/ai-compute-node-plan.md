# AI compute node (`ai-compute-01`) plan

**Version:** 0.1  
**Date:** 2026-08-30  
**Hardware:** ASUS GX10 / NVIDIA GB10  
**Status:** Deployment scaffold ready; physical execution and qualification pending

## Role

`ai-compute-01` is a rebuildable inference appliance. It provides GPU-backed
text, STT, TTS, and later diarization services to `ai-gateway-01` over a private
network. It does not host Caddy/LiteLLM, n8n, Home Assistant, MCP servers,
repositories, generic databases/queues, agent memory, meeting libraries, or
canonical workflow state.

Consumers use `https://ai.home` and logical aliases. Direct access to
`ai-compute-01` exists only for qualification and recovery diagnostics.

## Target naming and network

| Setting | Target |
| --- | --- |
| Hostname | `ai-compute-01` |
| DNS | `ai-compute-01.home.arpa` |
| Trusted-management address | Reserved during the network worksheet |
| Private compute address | `10.77.10.10/24` proposed |
| Private peer | `ai-gateway-01` at `10.77.10.2/24` |
| Production client endpoint | `https://ai.home` |

The private link has no default gateway. Bind inference ports only to loopback
during direct qualification, then to the private address with a firewall rule
allowing only `ai-gateway-01`.

## Execution checklist

### C0.1 — Prepare the hardware

1. Record serial number, exact product/SKU, firmware, SSD, MAC addresses, power
   supply, and warranty/support information.
2. Place the node in a ventilated location within the documented temperature
   range and connect it to the UPS.
3. Connect management Ethernet and the dedicated private-compute Ethernet.
4. Verify physical console/display access before changing firmware or network.
5. Photograph or export the factory recovery information.

**Pass:** Hardware identity, cabling, recovery, cooling, and UPS ownership are
recorded.

### C0.2 — Establish the supported platform baseline

1. Boot the vendor-supported DGX OS image supplied for the device.
2. Set hostname `ai-compute-01`, timezone `Europe/Copenhagen`, NTP, reserved
   management IP, DNS, and private address `10.77.10.10/24`.
3. Apply vendor-supported firmware and OS updates in the documented order.
4. Reboot and record DGX OS, kernel, firmware, driver, CUDA, Docker, NVIDIA
   Container Toolkit, GPU, memory, storage, and power mode.
5. Verify `nvidia-smi`, container GPU access, clock synchronization, SMART/NVMe
   health, network link speed, DNS, and free disk.
6. Configure SSH public-key access and restrict management to the administrator
   network/VPN. Do not expose runtime or SSH ports to the internet.

Run the repository's read-only check:

```bash
sudo ./scripts/setup-compute-node.sh preflight --env /etc/gb10-ai/gb10.env
```

**Pass:** The supported platform tuple is captured and all preflight checks
pass without changing the installed GPU stack.

### C0.3 — Prepare immutable deployment inputs

1. Copy this repository to `ai-compute-01` and review the current commit/files.
2. Initialize the external operator configuration:

   ```bash
   sudo ./scripts/setup-compute-node.sh init --env /etc/gb10-ai/gb10.env
   sudoedit /etc/gb10-ai/gb10.env
   sudoedit /etc/gb10-ai/secrets/hf_token
   ```

3. Resolve and record the exact linux/arm64 NGC image digest.
4. Resolve full model, tokenizer, and remote-code commits.
5. Record model provenance URL, license, weight format, quantization, chat
   template hash, parsers, attention/MoE backends, context, concurrency, and MTP
   state.
6. Generate the vLLM API key and keep all secrets outside the repository.
7. Start loopback-only with MTP disabled and conservative memory/context values.
8. Retain at least 100 GB storage headroom and define artifact cleanup rules.

Validate before any image pull or service start:

```bash
sudo ./scripts/setup-compute-node.sh validate --env /etc/gb10-ai/gb10.env
```

**Pass:** No floating image/model/template input, placeholder, insecure secret,
wildcard bind, or unrecorded artifact remains.

### C1.1 — Deploy the first direct inference tuple

1. Re-run preflight and validation after the final configuration edit.
2. Install the pinned tuple:

   ```bash
   sudo ./scripts/setup-compute-node.sh install \
     --env /etc/gb10-ai/gb10.env \
     --wait 1200
   ```

3. Record pull/download duration, artifact sizes, startup time, memory use, and
   release manifest.
4. Confirm container identity, read-only filesystem, dropped capabilities,
   secret permissions, bounded logs, health status, and loopback-only bind.
5. Run status, smoke, and log checks:

   ```bash
   sudo ./scripts/setup-compute-node.sh status --env /etc/gb10-ai/gb10.env
   sudo ./scripts/setup-compute-node.sh smoke --env /etc/gb10-ai/gb10.env
   sudo ./scripts/setup-compute-node.sh logs --env /etc/gb10-ai/gb10.env
   ```

**Pass:** The exact pinned service starts, reports healthy, lists only expected
logical names, and completes a minimal Responses API request.

### C1.2 — Qualify the direct API

Test with sanitized fixtures and retain results:

1. Non-streaming and streaming `/v1/responses`.
2. Single, parallel, and namespaced tool calls with arguments.
3. Cancellation, timeout, retry, malformed request, authentication failure,
   compaction/long context, and concurrent sessions.
4. Codex initial and follow-up task delivery using the pinned supported client.
5. Coding correctness, edit/build/test loops, and repository-scale context.
6. Danish/general prompts, structured automation output, and Home Assistant
   tool selection without executing real actions.
7. Cold/warm latency, tokens per second, time to first token, CPU/GPU/memory,
   thermals, disk, and error rates.
8. 10% or greater memory headroom at target context/concurrency.
9. Restart, crash recovery, host reboot, network loss, and repeated startup.
10. MTP off/on as separate immutable tuples; do not promote MTP-on without the
    complete soak and recovery suite.

**Pass:** Gate C1 evidence meets the thresholds in the verification strategy.
A smoke test alone does not pass this gate.

### C1.3 — Prove rollback

1. Retain the last known-good environment/manifest and image/model artifacts.
2. Deploy one controlled challenger or configuration change.
3. Trigger rollback:

   ```bash
   sudo ./scripts/setup-compute-node.sh rollback \
     --env /etc/gb10-ai/releases/previous.env \
     --wait 1200
   ```

4. Confirm the previous API tuple, health, model aliases, authentication, and
   performance return within the agreed recovery time.
5. Verify rollback does not delete models, caches, manifests, secrets, or
   unrelated artifacts.

**Pass:** A failed release can return to the exact prior tuple predictably.

### C2 — Validate Home Assistant tools without migration

1. Use a restricted test Home Assistant integration and test entities.
2. Verify deterministic Assist paths remain independent of the model.
3. Test allowed/denied entity selection, ambiguous commands, confirmation,
   unavailable compute, and prompt-injection fixtures.
4. Confirm the model only proposes calls and Home Assistant validates/executes.

**Pass:** No unauthorized or unconfirmed physical action occurs; this does not
move Home Assistant to either node.

### C3 — Connect through `ai-gateway-01`

1. Change the runtime bind from loopback to `10.77.10.10` only after the
   private bridge/link and firewall are ready.
2. Set `GATEWAY_CIDR` to the `ai-gateway-01` private address/CIDR and
   document the exact host firewall rule.
3. Set `FIREWALL_CONFIRMED=true`, revalidate, and redeploy the same tuple.
4. From `ai-gateway-01`, test health, authentication, Responses/tool streaming,
   latency, cancellation, and large/long requests.
5. From ordinary LAN, `automation-01`, and `toolbox-01`, prove the runtime port
   is unreachable.
6. Pull the private cable and verify the gateway exposes the intended failure:
   private aliases fail closed; only approved public aliases may use explicit
   cloud routing.

**Pass:** Only `ai-gateway-01` can reach compute services and proxied behavior
matches the direct qualified baseline.

### D — Select production runtime/model tuples

1. Execute the benchmark framework against the first candidate, required
   quantized baseline, and approved challengers.
2. Score coding, general/Danish, automation, home, meeting, assistant, long
   context, mixed load, latency, memory, thermals, and recovery.
3. Decide whether one shared text model meets every role or whether a reserved
   role needs a separate tuple.
4. Promote aliases only after measured acceptance; record rejected candidates
   and reasons.
5. Freeze the selected manifest and retain one rollback tuple.

**Pass:** Production aliases map to evidence-backed immutable tuples.

### E — Add audio capabilities incrementally

1. Select and pin the STT image/model/runtime tuple.
2. Validate Danish and English word/error quality, punctuation, latency,
   concurrency, privacy, and Meeting Assistant compatibility.
3. Select and pin the Danish TTS tuple; validate intelligibility, voice quality,
   latency, and long-text behavior.
4. Add diarization only when the Meeting Assistant/Plaud workflow requires it.
5. Repeat mixed-load and memory-headroom tests with text and audio together.
6. Keep each audio route fixed-path and private; do not introduce hidden cloud
   fallback.

**Pass:** Each audio service has its own manifest, quality evidence, recovery,
and private gateway route.

### F — Operational acceptance

1. Configure metadata-only health/metrics collection from `ai-services-01`.
2. Alert on service health, error rate, latency, temperature, memory/storage
   headroom, and disk health without recording prompts or outputs.
3. Define OS/runtime/model update windows and require requalification after
   tuple changes.
4. Run power-loss/UPS, reboot, private-link loss, container crash, failed
   upgrade, disk-capacity, and rollback drills.
5. Run the agreed mixed-load soak and record stability/thermal results.
6. Store configuration, release manifests, sanitized evidence, and recovery
   instructions off-node. Treat model caches as rebuildable rather than backed
   up canonical data.

**Pass:** `ai-compute-01` is reproducible, observable, isolated, recoverable,
and has no canonical application state.

## Normal operations

```bash
sudo ./scripts/setup-compute-node.sh status --env /etc/gb10-ai/gb10.env
sudo ./scripts/setup-compute-node.sh smoke --env /etc/gb10-ai/gb10.env
sudo ./scripts/setup-compute-node.sh logs --env /etc/gb10-ai/gb10.env
sudo ./scripts/setup-compute-node.sh down --env /etc/gb10-ai/gb10.env
```

Do not update DGX OS, drivers, CUDA, runtime images, model revisions, parser
settings, quantization, context, concurrency, or MTP as an unrecorded in-place
change. Each changes the qualified platform or artifact tuple.
