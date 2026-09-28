# DGX Spark model manager

This service provides the lifecycle surface shown in the HomeCompute console
concept. React serves the page; FastAPI exposes a same-origin API and invokes
the Spark adapter through one SSH connection per operation. The browser never
chooses a host, command, recipe, model repository, image, revision, or path.

## Controls

- View catalog deployments and Sparkrun runtime state.
- Prepare a deployment already marked eligible by both the local model catalog
  and the Spark adapter.
- Load or unload a catalog deployment.
- Replace a running deployment with a different eligible deployment, after an
  explicit confirmation.
- View recent operations for the lifetime of this manager process.

Adding a new upstream model requires a reviewed HomeCompute catalog entry and a
qualified Sparkrun recipe first. This UI does not accept Hugging Face IDs or
download URLs. Models absent from the adapter's eligible list cannot be loaded.

## Adapter contract

The server sends one newline-terminated JSON object on SSH stdin. It sends no
remote command, and it does not allocate a TTY or forward ports. The SSH key
must have a server-side forced command to the HomeCompute adapter, with
forwarding, agent access, and interactive shells disabled. The helper currently
lives at `scripts/sparkrun-model-manager.py` in the deployed release.

Requests are restricted to these shapes:

```json
{"action":"list"}
{"action":"status"}
{"action":"prepare","deployment":"automation-spark-primary"}
{"action":"load","deployment":"home-spark-primary"}
{"action":"unload","deployment":"home-spark-primary"}
{"action":"replace","from":"home-spark-primary","to":"automation-spark-primary"}
```

Success responses use `{"ok":true,"data":{...}}`; errors use
`{"ok":false,"error":{"code":"...","message":"..."}}`. The backend
unwraps `data`, discards unknown IDs, and maps only IDs present in the mounted
`config/model-catalog.json`. The adapter remains the authority for the final
eligibility check at execution time.

## Deployment prerequisites

1. Deploy the Spark adapter and its root-owned Sparkrun allowlist separately.
   The control-plane Compose project must create the external
   `homecompute-control-plane_clients` network. This Compose project creates a
   dedicated `br-hc-mm-ssh` bridge with address `172.28.205.2`; the
   host firewall must restrict it to `192.168.30.126:22` only.
2. Provision four secret files on home-core: manager username, manager
   password, dedicated Spark SSH private key, and pinned Spark `known_hosts`.
   Configure the SSH public key on Spark with the adapter as its forced command
   and no other SSH capability. Set the readiness flag to `1` only after the
   forced-command path and sudo allowlist have been verified; the example keeps
   it at `0`, and the container refuses to start until it is enabled.
3. Copy `config/model-manager.env.example` to a protected Compose environment
   file, then set its file paths, SSH target, and exact public HTTPS origin.
4. Add a Caddy route for the chosen hostname to `model-manager:8080` on the
   shared clients network, then start this Compose project from this directory.

The process reads auth credentials and SSH material from Compose secrets. Its
entrypoint stages those files into a private in-memory directory, changes them
to UID 10001, and drops to that UID before starting FastAPI. The container has a
read-only root, no Docker socket, no host network, no GPU devices, and only the
model catalog as a read-only bind mount. It joins the shared clients network for
Caddy ingress and the dedicated SSH bridge for the Spark forced-command path.
The host firewall must permit only manager traffic to the Spark SSH address and
port; the bridge itself is not a host firewall policy.
Uvicorn trusts forwarded headers only from Caddy's static address on the
clients network.

## API

Data and mutation `/api` routes require a signed-in session; `/api/health` and
the sign-in endpoint are public. The login endpoint compares credentials with mounted secret files and issues an HttpOnly,
Secure, SameSite=Strict cookie. Mutations require the exact configured Origin
and a JSON body. The catalog and adapter each validate deployment IDs before
an action is sent.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/session` | Sign in with operator credentials |
| `DELETE` | `/api/session` | End current session |
| `GET` | `/api/models` | Approved catalog entries and Sparkrun eligibility |
| `GET` | `/api/status` | Filtered deployment and port status |
| `GET` | `/api/operations` | Recent in-memory operations |
| `POST` | `/api/actions` | Submit a prepare/load/unload/replace action |
| `GET` | `/api/operations/{id}` | Read operation progress |

## Integration limits

- Current compose-managed vLLM instances are not automatically adopted. A port
  reported unavailable blocks the load button and identifies the migration
  prerequisite; stop/migrate the Compose owner through its existing guarded
  workflow before Sparkrun can take over that deployment.
- Status memory fields remain blank until the adapter exposes GPU memory.
- Recent operation history is held in process memory and clears on restart.
- The HTTPS hostname and Caddy route are intentionally owned by the control
  plane deployment, outside this directory.

No live host is contacted by the frontend. The only networked lifecycle path
is the backend's fixed SSH target and server-restricted forced command.
