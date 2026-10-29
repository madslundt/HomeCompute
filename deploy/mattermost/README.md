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

## Remaining channel consolidation

The approved layout retains the existing destination IDs:

| Retained channel | Archived sources | Contact |
| --- | --- | --- |
| Family (`family`) | Aula, Planning, Calendar, Tasks, Correspondence | Atlas coordinates Luna |
| Home (`home`) | Energy, Home Assistant | Orion |
| Money & paperwork (`finances`) | Household Admin | Plutus for budgets; Themis for documents |
| Research & news (`research`) | News | Nova |
| OpenClaw (`openclaw`) | Agent Playground | Orion and Apollo |

Inbox remains the fallback; Town Square moves from Favorites to Channels.
Historical posts stay in their original archived rooms, with replacement links
and links back from retained channels. Follow-ups start in the retained room.

Family, Aula and Planning already have the same two human members. This rollout
consolidates that existing adult audience; it grants no access to private DMs,
other agent workspaces, another adult's private sources or work data. Family
uses Atlas/Luna, matching the former Aula/Planning contact. **Disable Gaia's
native account before moving school/calendar delivery**, then remove Gaia's
Family membership. Retain its disabled account, tool restrictions, workspace
and historical task state. Shared family task records remain separate from
owner task records. An explicitly shared task still uses the shared store.
The existing household reminder destination moves from Planning to Family;
its existing audience is unchanged. Private provider credentials and DM sender
allowlists remain unchanged. OpenClaw retains its owner-only Systems audience;
archiving Playground grants Kristina no Systems membership.

Deploy from an exact published checkout, preserving the prior active workflow
versions and private configuration backups. Use this order:

1. Inspect affected channel audiences. `organize-mattermost.py` refuses a merge
   unless each shared source and destination has the same human audience.
2. In the native sandbox, plan `setup-openclaw-mattermost.py
   --retire-family-contact --owner-id OWNER --partner-id PARTNER
   --family-channel-id FAMILY`. Apply with the reviewed `--expected-config-sha256`
   and `--helper-sha256`. This changes only Gaia's enabled/group-policy fields.
3. Refresh Atlas and Luna with `setup-openclaw-team.py
   --coordinator-identity-only --identity-role main --identity-role personal`,
   reviewing the source configuration hash before `--apply`. Compare their old
   managed blocks with the preceding published render. Use the supported native
   reload lifecycle and confirm the disabled account before admitting school data.
4. Run `setup-mattermost.py install`, then `setup-mattermost-n8n.py` to prepare
   target bot memberships. The topic reconciler allows the explicitly retiring
   topic memberships during this transition; unknown memberships still refuse.
5. Apply `mattermost_channel_destinations.patch` to fresh n8n workflow details
   through atomic `update_workflow` operations, then publish only previously
   active workflows. Require draft and active versions to match before editing.
   The finite registry covers Aula, weekly review, battery monitoring, daily
   news and tomorrow preparation. Preserve credentials, message rendering,
   schedules, retry rules, claim keys, private system-review routing and other
   nodes. Do not run notification workflows as tests.
6. Run `organize-mattermost.py --task-destination --apply` to update the existing
   service-owned reminder configuration, preserving its owner, mode and token
   path. Then run the organization, topic, household, partner-sidebar and
   directory commands above. Destination changes precede channel archival.
7. Verify source and published workflow graphs contain the new destination IDs,
   both adults' authenticated access, Systems exclusion, archived history,
   directory/sidebar readback and native account policy. Use a fresh synthetic
   native routing check without delivery. Repeating identity/delivery plans
   should report no changes. Report broader host health separately.

The n8n version history retains each previous active graph for guarded recovery.
Restore only after checking for later edits and pause the affected producer if
its destination is unavailable. The reminder helper retains a private
`task-destination-*.json` backup; restore its one destination field only after
checking for subsequent configuration changes. Keep durable claims and task
records; do not replay notifications or merge private conversation transcripts.
