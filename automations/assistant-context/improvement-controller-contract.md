# Assistant policy improvement controller contract

The controller is a separate root-owned process. Its configuration is disabled
by default; the restricted systemd unit opts in with
`assistant-context-improve.py sweep --enable-controller`. The assistant can read
only the bounded status projection. It cannot approve, publish, or apply a
candidate.

Each sweep snapshots AttentionLedger through a read-only SQLite backup. It
accepts owner/personal/private candidates in the two configured classes and
patches exactly one file, `config/assistant-briefings.json`:

* `briefing-format`: `formatting.max_bullets`, `max_chars`, and
  `include_source_links` only; automatic promotion keeps source links enabled.
* `attention-threshold`: `trusted_adaptation.minimum_relevance` and
  `minimum_repeat_evidence` only, with automatic changes allowed to tighten
  these thresholds.

The controller runs the fixed briefing test and repository validator in an
isolated archive of a trusted `origin/master` source revision, followed by a held-out synthetic `ContextStore` fixture run through the actual
`BriefingBuilder`, then a bounded canary. The replay measures rendered source
coverage, citation revisions, retained calendar collisions and Daythread
follow-ups, output bounds, and that interpretations carry no action authority. Candidate-authored reports,
model prose, source references, and private content are not evidence and are
never included in the patch, branch metadata, pull request, or status projection.
Failures remain local to controller state; the source AttentionLedger is not
mutated.

Publication requires a separate operator-provisioned, HMAC-verified standing
grant scoped to owner/personal/private, allowed classes, expiry, and a maximum
of ten candidates. The controller creates or recovers only
`codex/assistant-improvement-<candidate-id>`, with the single allowlisted config
file. It queues a pull request for protected CI and human review; it never
merges. The branch and pull request are reconciled only when their exact head,
base, protected `static-validation` check, merge commit, and merged file hash
all match the candidate. A interrupted publisher operation is retried only
after verifying that an existing branch is the exact one-file candidate commit.

Git deploy key, pinned host keys, GitHub token, standing grant key, and grant
are external files under `/var/lib/homecompute-assistant-policy-controller`
or systemd credentials. The HTTPS inference credential is independent and is
not used by this controller. `config/assistant-improvements.json` remains
disabled by default; enabling its Nix option is an explicit operator action.
