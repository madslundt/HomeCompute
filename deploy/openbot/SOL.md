# Personal Sol agent on Mads home-core

Create **Sol Orchestrator** on the existing OpenBot team server. Its custom ACP
provider sends text to HomeCompute's `chatgpt-sol-latest` alias at Medium
effort. It has no model-callable filesystem, terminal, browser, delegation, or
MCP tools. The adapter uses the existing private HTTPS gateway with certificate
verification and filters out OpenBot's tool registrations and context. It
rejects attachments, teammate messages, routines, and conversation handoffs.
OpenBot 0.73.0 prepends memory and instructions in separate blocks. The adapter
drops those blocks and forwards one direct user text block. It retains a
bounded in-memory history for the ACP session.

OpenBot's Workspace only setting restricts writes and leaves file reads open.
The text-only provider is the boundary for what Sol can ask the server to do.
It runs on the existing OpenBot server with its regular OS user.

## Install and configure

From a clean checkout of the reviewed, published `origin/master` revision:

```sh
./scripts/setup-openbot-guest.sh prepare-sol-acp
```

The command verifies the running team container's data mount and installs the
reviewed adapter under a content-addressed path in that mount. It prints the
arguments for a Python launcher that verifies the exact source bytes at every
start. It leaves the VM and OpenBot service running.

In **Mads home-core → Providers → Custom agents**, create this provider:

| Field | Value |
| --- | --- |
| Display name | HomeCompute Sol |
| Agent ID | `homecompute-sol` |
| Command | `/usr/bin/python3` |
| Arguments | Use the argument line printed by `prepare-sol-acp` |
| Environment variables | Empty at onboarding |
| Usage command | Empty |

On the same server, create a new profile named **Sol Orchestrator**. Choose
**HomeCompute Sol → ChatGPT Sol** (`homecompute-sol/chatgpt-sol-latest`).
Set access to **Ask first** and turn **Computer Use** and **Local scripts** off.
Leave routines, backup models, channel memberships, memories, attached files,
and assigned skills empty. The provider implements its own short instruction
and ignores OpenBot's injected profile, memory and workspace blocks.

Use the agent for personal planning, drafting and questions in its own chat.
Send a new plain-text message when OpenBot wraps a reply or restores a chat.
To add durable preferences, get the owner to approve the exact text for Sol
first, then set `SOL_APPROVED_PREFERENCES` on this provider to that approved
text (3,000 characters maximum). Keep Hermes memories and history in its
local profile. The adapter forwards typed text and accepts a fresh session
after an OpenBot runtime restart.

## Verify and withdraw

- Verify OpenBot 0.73.0 or recheck the upstream prompt block layout before use
  on another version. Confirm the selected server, provider ID and alias.
- Use a fresh thread and a synthetic prompt. Check the returned reply, then
  verify a second synthetic message recalls a fact from that same thread.
  Confirm a local file attachment produces an error and that the agent
  has no model-callable tools. Check that Hermes still uses local
  `automation-moe` and retains its active routines.
- To withdraw the configuration, archive the Sol profile and remove the
  custom provider on the team server. The archived chat and adapter versions
  can be kept for recovery.
