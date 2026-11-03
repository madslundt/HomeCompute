# Verify native OpenClaw recovery from the NAS snapshot

Run after a matching isolated NAS archive verification, four PostgreSQL imports
and both VM boot proofs. Those engine proofs explicitly do not establish native
OpenClaw data readability or authenticated gateway startup.

[`verify-openclaw-recovery.py`](../../scripts/verify-openclaw-recovery.py) boots
the restored agents disk using read-only file descriptors and a disposable QEMU
snapshot. It uses the existing protected Nix-store QEMU/xorriso GC roots, a new
synthetic NoCloud seed and the public
[guest probe](../../deploy/openclaw/recovery/inspect-restored-state.py). The
probe runs only inside the copy. The original restored disk and production VM
are not modified.

The VM has no NIC, no host shares, no management socket, no SSH forwarding and
no external network namespace. It runs as the existing unprivileged restore
reader with one CPU, 8 GiB RAM and bounded COW/log files. A collected transient
scope bounds physical memory at 10 GiB. QEMU may reserve up to 32 GiB of
virtual address space so thread stacks/I/O mappings fit without granting more
physical memory. Private diagnostics retain only its address/RSS/thread counters.
Disk throughput is bounded at 20 MB/s writes and
40 MB/s reads. Sixteen GiB of host memory and 32 GiB of disk headroom are required. Interruption
stops the owned VM and scope. Serial output and receipts remain private.

Before running, compute the expected policy digest from the privately reviewed
pre-recovery configuration using the guest probe's `policy_digest` function.
It includes agents, tools, channels, cron, hooks and browser policy, including
credential/sharing changes, while permitting native gateway-auth rotation.
Pass the digest, not the configuration or credentials. Use a root-owned exact
release published on origin/master:

```bash
sudo python3 /srv/homecompute/releases/FULL_SHA/scripts/verify-openclaw-recovery.py \
  --restore-directory /srv/restore-tests/REVIEWED_APPLICATION_DIRECTORY \
  --snapshot PRIVATE_SNAPSHOT --archive PRIVATE_ARCHIVE \
  --expected-policy-sha256 REVIEWED_POLICY_DIGEST
```

The guest checks policy, twelve roles, workspace presence and every native
SQLite file with its adjacent restored WAL/SHM, and checks actual nonempty main
conversation tables. It waits for exact native Ready metadata before invoking exec-based authenticated
gateway health/version checks, verifying the old managed image, actual core and
required plugins. Policy is rechecked after startup, and a nonce-bound aggregate
receipt is emitted. Failed startup diagnostics retain native phase, container
exit/OOM metadata and redacted error lines only from the new VM boot; historical
production container logs and full native policy dumps are excluded. A login prompt, another
nonce, missing databases, changed policy, plugin error or unauthenticated
gateway cannot pass. Cloud-init uses its documented
[base64 file and command modules](https://docs.cloud-init.io/en/25.3/reference/yaml_examples/write_files.html).

External channel/model services are deliberately unreachable. This proves native
recovery from the selected encrypted snapshot; it does not prove restored
Mattermost delivery, inference, all integrations, candidate compatibility or
that production is synchronized. Retain a failed proof and its private serial
log; never clear it or change the expected policy just to pass.
