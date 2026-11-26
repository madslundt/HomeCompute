# Personal Sol agent on Mads home-core

Create **Sol Orchestrator** on the existing OpenBot team server. Its custom ACP
provider sends approved text to HomeCompute's `chatgpt-sol-latest` alias at
Medium effort. Sol can request one local Hermes memory lookup per message.
The adapter uses the existing private HTTPS gateway with certificate
verification and OpenBot's model-only credential. The authenticated route can
answer when Spark's keyless intake check is unavailable. The adapter filters
out OpenBot's tool registrations and context. It
rejects attachments, teammate messages, routines, and conversation handoffs.
OpenBot 0.73.0 prepends memory and instructions in separate blocks. The adapter
drops those blocks and accepts one direct user text block. OpenBot tools,
including files, shell, browser, and connectors, are unavailable to Sol.

OpenBot's Workspace only setting restricts writes and leaves file reads open.
The text-only provider is the boundary for what Sol can ask the server to do.
It runs on the existing OpenBot server with its regular OS user.

## Install and configure

From a clean checkout of the reviewed, published `origin/master` revision:

```sh
./scripts/setup-openbot-guest.sh prepare-sol-acp
```

If the guest has no model-only credential, provision it using
`python3 scripts/provision-openbot-chatgpt-key.py` first. The installer keeps a
mode-0600 copy in the owner container's private data, outside Git and outside
the agent workspace. The credential permits the existing ChatGPT subscription
aliases and denies MCP access. Rerun the installer after rotating it.

The command verifies the running team container's data mount and installs the
reviewed adapter under a content-addressed path in that mount. It prints the
arguments for a Python launcher that verifies the exact source bytes at every
start. It leaves the VM and OpenBot service running.

In **Mads home-core → Providers → Custom agents**, configure this provider:

| Field | Value |
| --- | --- |
| Display name | HomeCompute Sol |
| Agent ID | `homecompute-sol` |
| Command | `/usr/bin/python3` |
| Arguments | Use the argument line printed by `prepare-sol-acp` |
| Environment variables | Add `SOL_CHAT_AGENT_ID` and `SOL_HERMES_AGENT_ID` for the approved local handoff |
| Usage command | Empty |

On the same server, keep the existing **Sol Orchestrator** profile. Choose
**HomeCompute Sol → ChatGPT Sol** (`homecompute-sol/chatgpt-sol-latest`).
Set access to **Ask first** and turn **Computer Use** and **Local scripts** off.
Leave routines, backup models, channel memberships, memories, attached files,
and assigned skills empty. The provider implements its own short instruction
and ignores OpenBot's injected profile, memory and workspace blocks.

Before enabling the lookup, verify that Mads is the only owner or administrator
of this server. In the server's agent access settings, set **Everyone** to
**No access** for Sol Orchestrator, and give no custom role access to it.
Keep the existing Hermes profile on its local `automation-moe` model. Set
the two environment variables to the stable agent IDs from the owner server's
inventory. These IDs bind the local memory store and the private Sol workspace.
The lookup stays unavailable if these checks fail. Remove the two provider
environment variables if the server gains another administrator.

## Review and share context

1. Write a direct message in a Sol Orchestrator chat. The preview shows the
   text before it leaves OpenBot. Type `approve CODE` in that chat when ready.
2. When Sol needs owner context, it requests a preference, project fact, or
   practical constraint. A disposable instance of the installed Hermes runtime
   uses the owner's saved, non-site-specific memories on `automation-moe`.
   This lookup has no tools and touches neither Hermes's chats nor its routines.
3. Review the exact excerpt and type its separate `approve CODE` to send it to
   Sol. Type `withhold CODE` to discard the excerpt. Codes expire in 10 minutes
   and apply once, in the chat that showed them. A new draft replaces an older
   preview. Send a new plain-text message after a restart or an OpenBot handoff.

The provider reuses a bounded history of approved, ordinary chat turns.
Hermes excerpts and answers informed by them are excluded from that history.
They remain visible in the owner's OpenBot transcript. Copied text you send as
a new message goes through the same preview. Local lookup uses the existing
embedded Hermes dependency installation and a temporary home with no private
gateway credentials or conversation store.

## Verify and withdraw

- Verify OpenBot 0.73.0 or recheck the upstream prompt block layout before use
  on another version. Confirm the selected server, provider ID and alias.
- Use a fresh private thread and a synthetic prompt. Check that the first
  turn shows a preview and the approved prompt receives a Sol reply. Use the
  tests' synthetic memory fixture to verify the second preview, denial,
  expiration, and exact disclosure. Real owner memory stays behind the owner's
  approval. Confirm Hermes retains `automation-moe` and its active routines.
- To withdraw the configuration, archive the Sol profile and remove the
  custom provider on the team server. The archived chat and adapter versions
  can be kept for recovery.
