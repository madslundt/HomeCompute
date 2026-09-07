# Agent harness comparison for `home-core`

**Verified:** 2026-09-05

**Scope:** an always-on, headless NixOS coding host reached from a MacBook through OpenSSH over Tailscale, with interactive sessions kept alive in tmux

**Updated decision:** install both Pi and OMP under the same restricted account. Use Pi as the operational default for the current single-agent, spec-driven local-model workflow, while retaining OMP for integrated LSP/debugging and multi-agent work. Confirm the default with a repository-specific A/B test.

The update reflects clarification of the expected workflow: a frontier model
produces the plan and a single local model performs implementation. It does not
turn the Composio result into proof: that benchmark used hosted DeepSeek V4
Flash on SaaS workflows, not the local GX10 model on source repositories.

## Short answer

The proposed host topology is independent of the harness:

```text
MacBook -> SSH over Tailscale -> home-core -> unprivileged agent user
                                                -> tmux -> harness -> worktree
```

Any terminal harness continues through a laptop shutdown when its process runs inside tmux on `home-core`. None of the compared local harnesses turns tmux into reboot persistence: after a `home-core` reboot, the process is gone and a saved transcript can be resumed, but the interrupted command or model turn is not automatically continued. Durable, unattended, reboot-recovering jobs need an explicit service/job design with idempotency and checkpointing, not a different interactive TUI.

For the requested workload, OMP has the strongest combination of first-class parallel subagents, automatic per-task filesystem/worktree isolation, broad model routing, persisted sessions, and native Nix integration. Its main weakness is security posture: its documented default approval mode is `yolo`, and its task-isolation feature prevents sibling edit collisions but is not an operating-system security boundary. The dedicated unprivileged `omp` account is therefore part of the design, not an optional hardening detail.

## Assessment of the reported Pi 20/30 versus OMP 17/30 result

The screenshot accurately reproduces Composio's published result, but the
result does **not** verify the broader proposition that Pi is the better harness
for repository coding or for `home-core`.

The experiment used 30 one-shot SaaS workflows across Gmail, Calendar, Sheets,
Slack, GitHub, Airtable, and PostHog through Composio's hosted MCP router. It did
not test software changes in C#, .NET, Python, TypeScript, or this repository.
The verifier was programmatic and each harness received isolated fixtures,
which are good properties, but no public raw run corpus or reproducible harness
configuration was located ([Composio benchmark method](https://composio.dev/content/best-agent-harness-deepseek-v4-flash)).

It was also not a controlled same-model-only comparison. Composio records that
Pi used `high` reasoning while OMP used `max`, and that 24 of Pi's 30 trials used
DeepSeek's official API while the OMP route used OpenRouter. Composio itself says
this limits direct cost comparison, and warns that harnesses may count runtime
tokens differently ([Pi versus OMP details](https://composio.dev/content/pi-vs-omp),
[token-count caveat](https://composio.dev/content/best-agent-harness-deepseek-v4-flash)).

The apparent accuracy lead is weak evidence at this sample size. Both harnesses
passed the same 16 tasks and failed the same nine; only five tasks differed, with
Pi winning four and OMP one. Under an exact paired sign/McNemar test, that split
has a two-sided p-value of `0.375`, so it is compatible with run-to-run chance.
There were no repeated trials from which to estimate task-level variance.

The defensible conclusion is narrower: **Pi was faster, cheaper, and passed
three more tasks in this particular DeepSeek V4 Flash + Composio SaaS run.** It
is a useful signal in favor of testing Pi locally, not sufficient evidence to
replace OMP for the different repository-agent workload.

## Decision matrix

| Harness | Remote/headless continuity | Sessions/resume | Parallelism and edit isolation | Security controls | Models/auth | NixOS fit | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Oh My Pi (OMP)** | Excellent in tmux; also has collaboration and RPC features, though neither is needed for the baseline | Native JSONL sessions, `--continue`, `--resume`, cross-project lookup, and foreign Claude/Codex session import | Best fit: built-in task fan-out, configurable concurrency, background jobs, agent steering/revival, and automatic worktree/overlay/copy isolation with patch or branch merging | Tool-level allow/deny/prompt exists, but the documented default is `yolo`; isolated task workspaces are collision isolation, not confinement of the process | Broadest here: 60+ providers, multiple models by role, OAuth/API keys, Ollama/LM Studio/llama.cpp/vLLM, and custom OpenAI-compatible endpoints | Best: upstream exposes a flake package, overlay, NixOS module, and Home Manager module | **Keep as primary** |
| **Upstream Pi** | Excellent in tmux; TUI, print, JSON, and RPC modes | Native JSONL sessions with tree branching, `--continue`, and `--resume` | Subagents are supplied as an example extension rather than the opinionated built-in OMP system; isolation/permission behavior depends on extensions and external containment | Explicitly runs with the launching user's permissions and intentionally has no built-in sandbox; current releases add project-trust gating. Containers or a micro-VM are recommended for stronger boundaries | Very broad provider support, subscription OAuth, API keys, llama.cpp, and custom OpenAI/Anthropic/Google-compatible models | No first-party deployment flake/module was found; official install is npm or installer, so packaging is more work | **Default for the current workflow**, subject to a local repository A/B test |
| **Codex CLI** | Excellent in tmux; interactive and non-interactive modes | Interactive and exec sessions can resume by ID/name or most recent session | First-class parallel subagents and configurable concurrency; official guidance warns that parallel write-heavy agents can conflict, so separate top-level worktrees remain necessary | Strongest built-in option: native Linux sandbox modes, approval policies, protected writable roots, network controls, and inherited subagent policy | Best with OpenAI/ChatGPT auth. Custom providers are supported and Ollama/LM Studio are reserved built-ins, but it is less provider-neutral than OMP/Pi/OpenCode | Upstream contains a buildable development flake for four platforms, but not an OMP-like Home Manager module | **Best optional second harness** when sandbox policy matters more than provider breadth or automatic task worktrees |
| **OpenCode** | Strongest alternate remote UX: a headless HTTP server, web UI, API, and attachable TUI, in addition to ordinary tmux use | Persisted sessions, session picker/resume aliases, fork APIs, and `run --continue/--session` | Built-in background/foreground subagents and granular task permissions; no documented automatic Git-worktree isolation comparable to OMP was found | Fine-grained allow/ask/deny rules, including external-directory and command patterns; its official V2 specification says the shell is not sandboxed | 75+ providers, local models, custom base URLs, and credentials in its local auth store | Strong: Nixpkgs package plus a Home Manager module and web-service option | **Best pilot challenger** if native remote attachment is worth another service |
| **Claude Code** | Excellent in tmux; interactive and print/SDK modes | `--continue` and `--resume` by session ID; sessions are continuously persisted | First-class concurrent subagents, per-agent worktree isolation, top-level `--worktree`, and batch workflows | Real Linux bubblewrap sandbox plus allow/ask/deny permissions; configure failure closed because sandbox unavailability otherwise falls back with a warning | Primarily Claude through Anthropic, Bedrock, Vertex, Azure, or an Anthropic-format gateway; not a general local/OpenAI-compatible harness | Official Linux installer/npm/native binary, but no first-party Nix module was found | **Best Anthropic-first alternative**, but not provider-neutral |
| **Aider** | Works in tmux and is operationally simple | Chat history can be retained, but it is oriented toward a single pair-programming loop | Architect/editor two-model mode is not a general subagent scheduler or concurrent worktree manager | Git-centric safety and explicit editing flow; Docker is the documented isolation route | Broad through LiteLLM, Ollama, and OpenAI-compatible endpoints | Available from Nixpkgs/Home Manager | **Not a replacement** for this multi-agent requirement |
| **Goose** | Works in tmux; persistent CLI/Desktop sessions and scheduled recipes | Resume by session name/id, with reusable recipes for repeated work | Parallel subagents are supported, but worktree isolation remains an external operating convention | Auto/manual/smart permission modes and injection detection; safeguards are not an OS sandbox | Broad cloud and local provider support, including OpenAI-compatible endpoints | Upstream ships a Nix flake | **Future job-runner candidate**, not a better interactive remote harness today |

## Evidence and trade-offs

### OMP is the closest match to the intended operating model

OMP is now substantially more than a cosmetic Pi distribution. Its upstream README documents first-class task fan-out, an agent hub for inspection/steering, and isolated worktrees for sibling tasks. The detailed task reference shows a session-scoped concurrency semaphore, async background jobs, multiple isolation backends, and patch or branch integration ([OMP README](https://github.com/can1357/oh-my-pi#05--first-class-subagents), [task tool reference](https://github.com/can1357/oh-my-pi/blob/main/docs/tools/task.md)). Its session implementation supports `--resume`, `--continue`, cross-project session selection, and import of Claude and Codex sessions ([OMP session operations](https://github.com/can1357/oh-my-pi/blob/main/docs/session-operations-export-share-fork-resume.md)).

OMP also has the cleanest declarative installation route for this repository: upstream publishes `packages.<system>.omp`, an overlay, a NixOS module, and a Home Manager module ([OMP installation](https://github.com/can1357/oh-my-pi#install)). It supports direct APIs, subscription OAuth, locally hosted Ollama/LM Studio/llama.cpp/vLLM, and arbitrary OpenAI-compatible providers, with separate model roles for normal, cheap, slow, task, vision, and advisor work ([OMP providers](https://github.com/can1357/oh-my-pi#sixty-plus-providers-a-thousand-models-one-model-away)). That maps well to a future split where `home-core` runs the harness and a compute node exposes an OpenAI-compatible inference endpoint.

The security caveat is unusually important. OMP's settings reference says `tools.approvalMode` defaults to `yolo`; per-tool `allow`, `deny`, and `prompt` rules are available, but those remain application policy ([OMP settings](https://github.com/can1357/oh-my-pi/blob/main/docs/settings.md#tools-and-approvals)). The documented task isolation creates a separate filesystem/worktree and safely gathers edits; it does not state that the child process is prevented from reading other host paths, using the network, or invoking available credentials ([OMP task side effects](https://github.com/can1357/oh-my-pi/blob/main/docs/tools/task.md#side-effects)). Therefore run OMP as the dedicated non-admin account and use approval rules as defense in depth.

### Upstream Pi is attractive for minimalism, not for this requirement

Pi describes itself as a minimal terminal coding harness whose capabilities are extended through TypeScript extensions, skills, prompts, themes, and packages. It persists tree-structured JSONL sessions and supports continue/resume, print, JSON-event, and RPC modes ([Pi documentation index](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/index.md), [Pi session format](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/session.md)). It supports subscription logins, many API-key providers, llama.cpp, and custom providers/models ([Pi providers](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/providers.md)).

The upstream security policy is explicit: Pi runs inside the launching user's trust boundary, intentionally has no sandbox, and recommends a container, VM, or another sandbox for untrusted work. Current releases add a project-trust decision before project-local configuration and executable extensions are loaded ([Pi security policy](https://github.com/earendil-works/pi/security), [project-trust advisory and fix](https://github.com/earendil-works/pi/security/advisories/GHSA-mqxh-6gq7-558m)). Subagents, permission gates, SSH execution, and sandbox routing are extension examples rather than an opinionated built-in system, and packages/extensions execute with full access ([Pi coding-agent README](https://github.com/earendil-works/pi/tree/main/packages/coding-agent), [subagent example](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/examples/extensions/subagent)).

That trade is coherent for someone who wants a small programmable core. It is not better for this deployment: recreating OMP's task fan-out, lifecycle UI, LSP/debug tools, provider routing, and isolation would add configuration and extension supply-chain surface, while the dedicated Unix account would still be required.

### Codex is the meaningful security-first alternative

Codex CLI supports resuming interactive and non-interactive sessions, including resuming by ID/name and selecting the most recent session by working directory ([Codex command reference](https://developers.openai.com/codex/cli/reference/)). Current local releases provide first-class parallel subagents, inspection/steering, custom agent models, and a configurable concurrency cap. The official guidance explicitly recommends parallelism first for read-heavy work and warns that parallel write-heavy work can conflict ([Codex subagents](https://developers.openai.com/codex/subagents/)).

Its differentiator is native execution control. Codex exposes read-only, workspace-write, and danger-full-access sandbox modes; workspace-write can separately control network access and additional writable roots. Approval policy can be interactive or never, and unattended local work is explicitly advised to use workspace-write instead of bypassing the sandbox ([Codex configuration reference](https://developers.openai.com/codex/config-reference/), [Codex command safety tips](https://developers.openai.com/codex/cli/reference/#flag-combinations-and-safety-tips)). The upstream repository contains a Nix development/build flake for x86-64 Linux and the other major Linux/macOS architectures ([Codex flake](https://github.com/openai/codex/blob/main/flake.nix)).

Codex can define custom model providers and reserves built-in provider IDs for OpenAI, Ollama, and LM Studio, but its product and authentication path remain OpenAI-centered ([Codex configuration reference](https://developers.openai.com/codex/config-reference/#configuration-reference)). It is worth installing only if there is a concrete need to compare its sandbox or OpenAI-native agent behavior; it does not invalidate the OMP deployment.

### OpenCode is the meaningful remote-interface alternative

OpenCode's architectural advantage is a real client/server split. `opencode serve` exposes a headless HTTP/OpenAPI server, and `opencode web` can share the same sessions with an attached terminal client. The server binds to loopback by default and supports HTTP basic authentication ([OpenCode server](https://opencode.ai/docs/server/), [OpenCode web](https://opencode.ai/docs/web/)). This is a better native remote experience than reattaching a TUI when browser access, IDE clients, or programmatic session control are explicit requirements.

OpenCode also supports persisted session selection/continuation, background child sessions, per-agent models, and granular allow/ask/deny rules for edits, shell commands, external directories, and subagent invocation ([OpenCode CLI](https://opencode.ai/docs/cli/), [OpenCode agents](https://opencode.ai/docs/agents), [OpenCode permissions](https://opencode.ai/docs/permissions/)). It supports more than 75 model providers, local models, and custom base URLs ([OpenCode providers](https://opencode.ai/docs/providers/)). The project's own V2 session specification is explicit that Bash is not sandboxed and runs with the host user's filesystem, process, and network authority ([OpenCode V2 session specification](https://github.com/anomalyco/opencode/blob/dev/specs/v2/session.md)).

Unlike the other challengers, OpenCode already has good declarative integration outside its own repository: Nixpkgs packages it, and Home Manager exposes `programs.opencode` plus a web-service unit and `EnvironmentFile` support ([Home Manager OpenCode options](https://nix-community.github.io/home-manager/options.xhtml#opt-programs.opencode.enable)). That makes it the most credible pilot challenger to OMP on `home-core`, particularly if native client reconnection would replace routine tmux attachment.

Those benefits do not justify adding another remotely reachable service now. SSH plus tmux already meets the stated availability need with a smaller exposed surface. If OpenCode is evaluated later, bind it to `127.0.0.1` and reach it through an SSH tunnel initially; do not bind an unauthenticated server to the LAN or tailnet.

### Claude Code, Aider, and Goose do not displace OMP here

Claude Code has solid continuation and non-interactive support, true Linux sandboxing, concurrent subagents, and first-class Git worktrees. Agents can request `isolation: worktree`, top-level sessions can start with `--worktree`, and batch workflows fan out isolated agents ([Claude Code CLI](https://code.claude.com/docs/en/cli-usage), [Claude Code subagents](https://code.claude.com/docs/en/sub-agents), [Claude Code worktrees](https://code.claude.com/docs/en/worktrees), [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing)). This is a strong implementation, but its supported backends are centered on Claude through Anthropic, Bedrock, Vertex, Azure, or an Anthropic-format gateway ([Claude Code setup and authentication](https://code.claude.com/docs/en/setup), [LLM gateway](https://code.claude.com/docs/en/llm-gateway)). It is a sensible choice for a Claude-first organization, not a stronger provider-neutral match for this home compute environment.

Aider supports arbitrary OpenAI-compatible APIs, Ollama, and a two-model architect/editor mode, but architect/editor mode is a sequential editing technique rather than general subagent orchestration ([Aider OpenAI-compatible APIs](https://aider.chat/docs/llms/openai-compat.html), [Aider architect mode](https://aider.chat/docs/usage/modes.html)). Its simpler pair-programming workflow is useful, but it does not address the requested parallel always-on agent model better than OMP.

Goose is the most relevant additional runner-style alternative. It persists and resumes sessions, can delegate parallel subagents, supports local/OpenAI-compatible providers, and adds reusable or scheduled recipes ([Goose session management](https://goose-docs.ai/docs/guides/sessions/session-management/), [Goose subagents](https://goose-docs.ai/docs/guides/context-engineering/subagents/), [Goose providers](https://goose-docs.ai/docs/getting-started/providers/), [Goose upstream flake](https://github.com/aaif-goose/goose/blob/main/flake.nix)). Its permission modes and prompt-injection detection are application safeguards rather than a replacement for an unprivileged OS account ([Goose permissions](https://goose-docs.ai/docs/guides/goose-permissions/), [prompt-injection detection](https://goose-docs.ai/docs/guides/security/prompt-injection-detection/)). It is worth revisiting for reboot-aware scheduled recipes, but it is not a more direct answer than OMP plus tmux for interactive coding.

## Authentication and persistence rules

No evaluated harness makes MacBook credentials magically portable to `home-core`. Authenticate on the remote account or provide credentials through the host's secret-management path. OMP relocates all configuration, session, and auth state with `PI_CODING_AGENT_DIR`; Pi stores OAuth tokens below its agent directory; OpenCode stores provider credentials under `~/.local/share/opencode/auth.json`; Codex and Claude Code likewise maintain machine-local login state ([OMP settings](https://github.com/can1357/oh-my-pi/blob/main/docs/settings.md), [Pi providers](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/providers.md), [OpenCode providers](https://opencode.ai/docs/providers/), [Codex authentication](https://developers.openai.com/codex/auth/)).

For `home-core` this means:

1. Keep all harness state owned and readable only by the dedicated agent user.
2. Do not copy a whole MacBook dot-directory or keychain export to the server.
3. Perform subscription OAuth interactively while SSHed to the server, or materialize scoped API credentials through the repository's existing secret mechanism.
4. Back up transcripts only if their source-code and command-output contents are acceptable in the encrypted backup boundary.
5. Treat Git credentials independently from model credentials; use a repository-scoped deploy key or narrowly scoped token where possible.

## Recommendation

Implement the reviewed harness-neutral topology and keep it deliberately boring:

```text
OpenSSH/Tailscale -> dedicated `agent` user -> tmux -> Pi or OMP -> one top-level worktree per task
```

Start with Pi for the spec-driven single-agent implementation loop. Use OMP's
built-in isolated task workers when a task actually benefits from fan-out, set
a bounded concurrency limit appropriate for `home-core`, and keep OMP's global
approval mode away from the documented `yolo` default. Keep system activation,
secrets, Docker control, and privileged NixOS operations outside the agent
account.

Do not remove OMP merely because Pi is the initial default: the two harnesses
optimize for different jobs, and neither removes the need for OS-level
isolation. Run the comparison on matched repository tasks, model settings,
provider route, tool permissions, and fresh worktrees; model quality, prompt
policy, and tool permissions otherwise dominate subjective harness impressions.
