# Mattermost channel organization

Mattermost is a private household service on `home-core`. Persistent settings
come from the setup scripts; credentials and content remain under
`/srv/state/mattermost`, outside Git. See
[HomeCompute operations](../../docs/homecompute-operations.md) for host access
and [Git deployment](../../docs/git-deployment.md) for immutable releases.

## Food, supplies and general shopping

The existing `shopping` channel ID and URL remain the destination for
**Food & household supplies**. It combines recipes, meal planning, groceries,
cleaning products, toiletries, shopping lists, offers and physical stock.
Atlas selects Hestia for meals, Vega for lists/offers and Ceres for stock.
`meals` is archived with a link to this channel; its original threads stay in
read-only history. Delivery aliases `meals` and `storage` resolve to the retained
household channel. Existing hard-coded grocery destinations stay valid.

**Shopping**, at `general-shopping`, is a separate private household channel
for electronics, clothes, gifts, furniture and other purchases. Atlas uses Nova
for public product research and comparisons. Creating this room adds no tools,
credentials, purchasing authority or new specialist. Mads and Kristina share it
under the existing non-Systems audience policy. Family's separate identity and
school/calendar privacy boundary are unaffected by this migration.

## Authorized channel rollout

Use a clean exact-SHA checkout of the reviewed commit contained in
`origin/master`; do not run uncommitted workstation copies on the server.
Preserve the prior release and inspect current channels, audience, directory
receipt and workflow destination IDs first. Compare installed helper hashes
with their owning release. Retain sanitized evidence of any difference before
choosing what to reconcile. Neither a host code checkout nor these helpers
changes the host-wide release marker.

From that published checkout on `home-core`, run:

```bash
sudo python3 scripts/organize-mattermost.py
sudo python3 scripts/setup-mattermost.py install
sudo python3 scripts/organize-mattermost.py --apply
sudo python3 scripts/setup-mattermost-n8n.py
sudo python3 scripts/setup-mattermost-household.py --apply
sudo python3 scripts/organize-mattermost.py --household-sidebar --apply
sudo python3 scripts/organize-mattermost.py --refresh-directory
sudo python3 scripts/organize-mattermost.py --refresh-directory --apply
```

Organization saves a private backup before changes. It creates `general-shopping`
once, updates retained mappings, archives Meals, and arranges the owner's sidebar.
Topic reconciliation admits the existing bots and approved partner; the household
step verifies the exact audience and keeps Systems excluded. The directory step
edits only its retained post after checking its previous content hash, and never
creates a new message. Run directory refresh from the release checkout so its
Markdown source is available.

Mattermost assigns new channel IDs. Record the returned `general-shopping` ID
in `config/openclaw-team.json` as `{"name":"general-shopping","agent":"researcher"}`,
validate and publish that follow-up before installing the trusted routing map.
Do not derive authority from the visible channel name.

In the managed OpenClaw sandbox, use NemoClaw's supported upload/exec lifecycle
to install the matching published source and run:

```bash
python3 scripts/setup-openclaw-team.py --coordinator-identity-only
python3 scripts/setup-openclaw-team.py --coordinator-identity-only \
  --expected-config-sha256 REVIEWED_CONFIG_SHA256 --apply
```

This refreshes only Atlas's managed instructions and channel-ID map, with a
private backup and no native configuration changes. Compare the existing managed
identity block with the previous published render before applying; reconcile
unknown instruction drift first. Preserve any unmanaged text surrounding the
block. Run at a quiet point; verify fresh native turns use the new instructions.

Afterward check both adults' authenticated channel access, exact human and bot
memberships, archived Meals/history links, sidebar placement, directory content,
delivery aliases and actual workflow destinations. Compare installed helpers
with source and verify native configuration bytes remain unchanged. Inspect
`homecompute status`, `services`, `doctor` and `drift`; host markers alone do not
verify this dedicated lifecycle. Repeating the setup must retain channel IDs.

## Recovery

Organization backups `private/organization-*.json` contain the previous channel
metadata, owner sidebar and private exports. Directory backups retain the old
post and receipt. Native team backups retain Atlas's previous identity and native
configuration. Preserve these backups; never restore an application database
just to undo channel organization.

To reverse the migration, first stop producers targeting affected destinations
if any. Review current state against the retained backups, restore Meals through
the Mattermost channel restore API, and restore only the affected metadata,
audience, mappings and sidebar entries. Restore Atlas's previous identity only
after checking for later instruction changes. Restore the directory post and
receipt together. Archive the new Shopping room only after preserving any new
threads and reconciling its consumers. A Git rollback alone does not reverse
Mattermost API changes. Never delete channels or move/replay conversation posts.
