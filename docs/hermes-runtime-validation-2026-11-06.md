# Hermes runtime evidence — 6 November 2026

This is dated operational evidence, not approval of every Flash-Next model
promotion gate. The owner selected local `automation-moe`, approved household
classification and encrypted Synology recovery, and authorized a clean Hermes
installation. Additional profiles and Oh My Hermes remain deferred.

## Later same-day upgrade: Hermes 0.23.0

This section supersedes the initial 0.21.3 runtime below. Upstream's latest
release was rechecked as [v0.23.0](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.0),
commit `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`.

The applied image, configuration installers, Mattermost/browser connectors and
boot unit use published source `75975d2f7e342ed33865391d58d766dc5fcefc82` through
the guarded Hermes guest lifecycle. The selected owner is `hermes-personal-v23`,
container `openshell-default--hermes-personal-v23-d90b7fc4-1506-4d12-b055-f0ab2aa4dcc7`,
image `sha256:f15b60053b28b681efe555f89a6d170c7f7cd51036ed570b9951908c19f85311`.
Its private config is `~/.config/homecompute-hermes/hermes-personal-v23.env`.
The installed unit and system shutdown-ordering file were compared with their
source-rendered contents after reboot. Both matched, lingering was enabled,
and the guest reported zero failed system or user units.

The overlay retains NemoClaw 0.0.141 and OpenShell 0.0.116. It adds the pinned
Python 3.14.9 environment, Node 26.12.1/npm 12.0.2 frontend toolchain,
browser-harness 0.1.13, ffmpeg 9.0.1 and ripgrep 15.2.0 from upstream's frozen
locks. Build-owned tools are prepared directly into the sealed payload store;
installing them into the build user's writable home does not qualify the image.
The final unprivileged build check and the live post-reboot `pm.check()` both
reported no install drift. The managed control, ownership and database-home
ports are documented in [the integration directory](../deploy/hermes/latest/README.md).

The existing owner-only `hermes-agent` bot and dedicated browser profile were
reused. The retained 0.21.3 instance was natively stopped through its original
unit before enabling the new listener. Its image
`sha256:8b6f951702a24592169a02791955ee0457bc562199f91960c56a69058223bf54`,
state and original private configuration remain available for rollback. No
household history was imported into the fresh new instance. Only the synthetic
pre-cutover candidate was recreated without a state import to complete its
bundled-tool packaging; the original household instance was preserved.

Observed checks:

- 213 selected upstream Linux transport, ownership and session regressions passed;
  two tests were skipped. The separate upstream native-admission home-isolation
  failure remains recorded in `qualification.json`; actual owner DMs passed.
- All 22 focused HomeCompute Hermes lifecycle tests and the complete repository
  validation passed, including Nix evaluation. `git diff --check` passed.
- Authenticated local `automation-moe` inference, Mattermost REST/WebSocket auth,
  public `browser_exec` navigation and private-destination denial passed in the
  complete image. Native stop/start recovery passed; the earlier candidate also
  completed two additional clean stop/start/Ready cycles.
- A real owner Mattermost message ran `hermes --version`, used the browser and
  returned `HERMES_023_MM_OK` with the version and public title.
- A full graceful agents VM reboot changed the boot ID. The new unit came up
  enabled and active, its sandbox was running with zero crash restarts, and
  OpenClaw was running and healthy. The old Hermes container remained stopped.
- A new Mattermost request after that reboot invoked `browser_exec` and returned
  `HERMES_023_BOOT_OK`. A read-only query restricted to that synthetic session
  confirmed a fresh browser result and final response after the request.
- Post-reboot health passed at
  `/var/lib/homecompute-hermes/evidence/20261106T102049Z-health`.
  An earlier attempt contended with another live NemoClaw host command; retrying
  after the lock owner exited passed. No lock or authority marker was removed.
- Local model, 65,536 context, medium reasoning, deferred inference credential,
  owner messaging and browser restrictions matched the intended values.
- Homepage's existing DM card matched source, and its API health and rendered
  page checks passed. No Homepage change was needed for this upgrade.

After clearing unused build cache, Docker reported 3.039 GB reclaimed and the
77 GiB guest filesystem had about 18 GiB available. Running and retained images,
containers and durable volumes were preserved. At one idle sample Hermes used
675 MiB and about 1% CPU; its browser used 318 MiB. These are bounded observations,
not an overnight soak or a model-quality/performance qualification.

The platform `status`, `services`, `doctor` and `drift` inventories were inspected.
Both host release markers remain older than current Git; this dedicated Hermes
rollout does not establish a full NixOS/Spark reconciliation. Core's platform
health monitor currently flags NAS backup readiness: the scheduled backup is
retrying with exit 75 because active HTTP use requires recovery preparation to
defer. The last completed encrypted archive is still the 00:20:48 UTC snapshot
`093a7dff9ce129319a459a625536b386a8dadef89f5e298a17a4bcbb6f42b30b`.
It predates this upgrade. No new full-VM archive or restore of the final 0.23
state is claimed; the existing approved restore admission and retained previous
instance remain the recovery evidence. The scheduled backup was not forced
through active workload use.

Concurrent household MCP setup is owned by its separate guarded lifecycle and
receipts. Its commits were preserved when publishing this evidence. It is not
part of the Mattermost/browser/boot qualification above; do not infer working
Aula or Home Assistant tools from these upgrade checks.

## Initial 0.21.3 source and runtime

| Concern | Source / lifecycle |
| --- | --- |
| Managed image, Mattermost and browser | `26eba2fc5592e19e9c52bc557297fa8a2ee58896`, guarded recreation and connection commands |
| Native boot unit and guest system ordering | `26eba2fc5592e19e9c52bc557297fa8a2ee58896`, `install-startup` |
| Explicit authenticated health checks | `517cfa76a6e098180bb6fc84c1d0c534a135f98e`, `health` |
| Dedicated browser worker assets | `29948c36157d63b1c39e0f1eed49a018b3931ed1`, `setup-openclaw-browser.py --principal hermes` |
| Homepage card | `07ccb1c515ab9bcca2acb3c8b8cebb0343217a2c`, guarded `setup-hermes-homepage.py` |

The initial sandbox was `hermes-personal` on OpenShell gateway 8080. OpenClaw keeps
gateway 9123, its existing browser and its existing Mattermost credentials.
The dedicated bot is `hermes-agent`; the n8n bot named `hermes` is independent.
The Homepage card opens the dedicated Mattermost DM. Dashboard forwards remain
private.
The initial managed image ID was
`sha256:8b6f951702a24592169a02791955ee0457bc562199f91960c56a69058223bf54`.

The initial tuple was NemoClaw 0.0.141, OpenShell 0.0.116 and managed Hermes 0.21.3.
Standalone upstream Hermes 0.22.7 is newer and remains unqualified with the
managed integration. This installation does not satisfy a claim that standalone
0.22.7 is deployed. See [the pinned tuple](../config/hermes-release.json) and
[upstream release](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.7).

A clean checkout of upstream release commit
`b200f5bc60e31588ba2cb60e288029c4a54ba5fa` failed `git apply --check` for NVIDIA's
`security-dependencies.patch` and `whatsapp-proxy.patch` at the pinned NemoClaw
commit. Conflicts included dependency/lock files, `tools/browser_tool.py`,
`tools/lazy_deps.py`, `hermes_cli/oneshot.py` and a removed Hindsight plugin path.
No upstream code was executed and the live image was not replaced by this
offline compatibility check. This demonstrates integration drift, not that
upstream Hermes itself is unsafe; a latest-version rollout still needs a port
and qualification of the managed patches.

## Behavior observed

- Authenticated Mattermost REST identity and WebSocket challenge passed.
- A real Mattermost owner DM invoked browsing and received a successful reply.
- After native stop/start, the bot invoked `browser_navigate`, returned the
  reported title `Example Domain`, and returned the synthetic restart marker.
- Native browsing, accessibility snapshots and the Python dialog/frame
  supervisor worked through the fixed OpenShell CDP adapter.
- Browsing `http://192.168.30.30/` was rejected as a private destination.
- The browser backend is explicitly the string `off`: this selects the bundled
  `browser_*` tools and avoids automatic uvx Browser Use installation.
- Inference uses only `automation-moe`, a dedicated alias-scoped key, medium
  reasoning and a 65,536-token Hermes context cap. No cloud fallback is configured.
- Inference credentials stay as `${COMPATIBLE_API_KEY}` in durable settings;
  runtime-scoped OpenShell references are resolved after each start.
- Upstream error-path request dumps are disabled by a drift-checked image patch.
- The source-owned user boot unit is enabled, the vendor gateway boot unit is
  enabled, and operator lingering is enabled. Native unit restart and a complete
  VM reboot passed. After reboot, a real Mattermost request browsed the public
  page and returned the reported title and synthetic boot marker.
- The final health run passed at
  `/var/lib/homecompute-hermes/evidence/20261106T003842Z-health`.
  Native status can intentionally suppress inference probes in its ordinary
  read-only path. Health therefore requires a separate authenticated synthetic
  inference response; a suppressed status alone never establishes readiness.
- The browser worker's own egress proxy returned HTTP 403 for the private
  destination, independently of Hermes's URL guard.

The first full boot exposed a user-target ordering cycle, which static unit-only
verification missed. The fixed unit explicitly follows `default.target`, matching
the vendor gateway. The installer checks the full boot transaction and rejects
cycle warnings even when `systemd-analyze` exits zero. A second shutdown issue
was Docker stopping before the user's native sandbox stop. The tracked system
drop-in orders `user@1000.service` after Docker, reversing shutdown safely.
The final reboot journal records native `Stopped` with workspace preservation
before shutdown, followed by automatic startup with no manual intervention.
The source boot regression reproduced the bad graph and passed the corrected
graph using real systemd on the guest.

The older synthetic messages showing provider failures were retained in chat.
They predate the corrected restart-scoped credential configuration.

## Model and resource screening

Three trials of
[`hermes-local-runtime-smoke.json`](../benchmarks/plans/hermes-local-runtime-smoke.json)
passed all objective checks. Each used 16 sequential tool calls, one deliberate
temporary tool error, exact required tools/arguments and strict final JSON.
Mean trial duration was 49.14 seconds. A separate synthetic 64K input request
used 64,046 prompt tokens and returned the correct calibration label in 31.77
seconds. These are bounded operational screens, not a comprehensive quality
comparison or an overnight soak.
The model harness simulated these tool results; it did not connect Hermes to
real Aula or any household MCP service.

The earlier A/B fixture completed 16 calls in each trial but failed its objective
gate. Its prompt permits a different order from its asserted positional tool
results, and output also included Markdown/prose instead of strict JSON. Those
results remain diagnostic; they were not relabeled as passing qualification.
The new fixture specifies the sequence and final format explicitly.

Observed Spark image:
`sha256:b80718ed664f12514a24363894c61c5d36f9ea8ca294244a149ddb672df2dc14`.
The tokenizer config SHA-256 matched the catalog:
`792fa3f0cb88b111e54ef3134c873531008c4df471d108da17903426e308aa7b`.
The complete weight contents/revision were not independently rehashed.
Spark had about 18 GiB available memory and 7 GiB allocated swap; four one-second
samples showed zero active swap-in/swap-out. No swap setting was changed.

A final post-boot sample showed about 413 MiB for Hermes and 317 MiB for Chromium,
plus small DNS/relay/proxy containers. Docker reported no Hermes OOM kill. The
guest had 34 GiB free of its 77 GiB filesystem. No disk expansion was necessary.

Private synthetic run evidence is retained in the guest under
`~/.cache/hermes-tool-screen-j7v2skl5/`. Raw generations and credentials are not
included in source. ADR 025's broader artifact quality, Danish/coding, 128K and
overnight qualification remain separate; no global model promotion was made.

## Recovery and retained state

The fresh encrypted Synology restore used snapshot
`3b8cfb7cd434f1205fbaf3269dd439c8b4b5d01482fbb874b5e7b13351ac9edd`
and `homecompute-applications-20261104T040901.512220Z.tar`. Archive/SQLite checks,
four PostgreSQL imports and both isolated VM boots passed. The isolated VM proof
established kernel/userspace startup, not application service readiness.
Receipt: `/srv/restore-tests/engines-p3naqtl2/receipt.json`.

The encrypted NAS backup completed successfully at
`2026-11-06T00:20:48.731095+00:00`, snapshot
`093a7dff9ce129319a459a625536b386a8dadef89f5e298a17a4bcbb6f42b30b`.
Its archive is `homecompute-applications-20261105T233847.431774Z.tar`
(109,930,557,440 bytes). All paused services resumed, including the OpenClaw
proxy and certificate watcher; no resume journal remained. This archive
precedes the final clean Hermes recovery and boot-order repair, so it is not
evidence of a ready-to-run restoration of the final instance. Reconcile the
published configuration during recovery; final-instance application restore and
arbitrary terminal-Error recovery remain unverified.

Both reproducible scratch copies from the completed restore drill were removed:
`/srv/restore-tests/nas-archive-20261105-hermes` and its extracted
`-application` tree. Cleanup verified retained archives/receipts, excluded active
mounts and open files, and retained private maintenance receipts. Original
archives, encrypted NAS snapshots and engine verification receipts remain.
The host then had about 303 GiB free. The earlier guarded OpenClaw cleanup
removed redundant generated recovery copies while preserving its recovery set.

The failed `hermes-owner` native restore and earlier failed custom-image attempts
remain retained and stopped. NemoClaw's nonempty kanban merge failure was not
declared repaired; complete encrypted VM recovery remains the supported boundary.
No live state was restored into the final clean sandbox.

## Oh My Hermes assessment

[Oh My Hermes](https://github.com/rlaope/oh-my-hermes) adds skill routing,
parallel task graphs, per-lane model chains and separately reviewed project
memory. It is an optional operating layer, not a prerequisite for Mattermost
or browsing. Its README currently states that no measured Hermes-versus-OMH
product A/B run has been published.

For this deployment, defer installation until a separate qualification of the
managed integration. Any future trial should pin one reviewed commit, restrict
every model chain to `automation-moe` with no cloud fallback, bound fan-out to
the measured Spark capacity, and benchmark against the working base runtime.
The documented reviewed-memory feature fits the owner's interest in learning;
performance or reliability improvements have not been established here.

## Validation and deployment limits

The full `scripts/validate-repository.sh` passed for final health revision
`517cfa76a6e098180bb6fc84c1d0c534a135f98e`, including Nix evaluation.
All 16 Hermes lifecycle/health tests, two system-ordering
installer tests and real-socket Node CDP adapter tests passed. The real systemd
boot regression also passed separately on the Linux guest.
`git diff --check` passed. One earlier DNS socket test failed transiently and
passed standalone and in subsequent full runs; it was not changed for Hermes.

Hermes and Homepage used their dedicated source-owned lifecycles. The whole
host release markers remain older (`home-core` at `7df6e792262ac330027b47b188eb9c156eaa68b8`,
Spark at `469618169ca43659ffe5e7e3ac7ee3589160811b`). A full NixOS/Spark rollout
was not performed. In particular the canonical household classification,
agents network and NAS declarations added during this task remain pending a
normal host release; the dedicated Hermes backup/network admission records and
actual client checks were used. Do not infer complete platform synchronization
from the successful Hermes lifecycle.
Final `homecompute status`, `services`, `doctor` and `drift` checks completed.
Both hosts reported zero failed units, and the guest user manager reported zero
failed units. OpenClaw's native supervisor reached `ready`, and its container
was healthy. Homepage's card matched its selected source and both its API health
and rendered HTML checks passed. The global selected OpenShell gateway remained
`nemoclaw-9123`, preserving the existing OpenClaw operator default.
