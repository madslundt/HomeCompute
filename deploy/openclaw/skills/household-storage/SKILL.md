---
name: household-storage
description: Find or maintain physical household stock, locations, quantities and expiry dates in the Storage service.
---

# Household storage

This inventory describes physical household items. Infrastructure capacity and
disk storage belong to the operator's HomeCompute operations tools.

Read `homecompute__homecompute_storage_list`, optionally narrowing by `query`
or `location_id`. Resolve the actual item and location before a change. Use the
returned 32-character hexadecimal IDs and current revision as `expected_revision`;
a partial result does not establish that an item or location is absent.

Query results may include semantic candidates such as synonyms or translations.
Confirm the actual item, location and unit before selecting its current identity
for a write. An unfiltered read retains the full current snapshot behavior.

An explicit owner request authorizes the corresponding routine inventory change.
Clarify an ambiguous item, location, quantity or unit, and preserve unspecified
metadata. Use the following tools with the `homecompute__homecompute_` prefix:

- `storage_add`: `location_id`, `name`, `quantity`, `unit`, and `request_id`.
- `storage_update`: `item_id`, `expected_revision`, `request_id`, and only the
  requested name, location, quantity, unit or expiry fields.
- `storage_adjust`: the current item/revision, signed nonzero `delta`, and
  `request_id`, for adding or consuming a known quantity.
- `storage_remove`: the specific item/revision and `request_id`.

Quantities and deltas are decimal strings with at most three decimal places;
quantities range from 0 through 1000000000. Preserve the explicit unit without
implicit conversion. A unit change also requires the owner's intended quantity.
Optional `expiry_date` is `YYYY-MM-DD` or an empty string to clear it;
`expiry_kind` is `best_before`, `use_by`, or an empty string. Keep best-before and
use-by meanings distinct; resolve conflicting or incomplete expiry instructions.

Choose one version-4 UUID `request_id` per change. After a revision conflict, read current
state and reconcile the intended change. For uncertain delivery, inspect
`homecompute__homecompute_household_request_status` using the same UUID and read
current inventory. Reuse that UUID for a completed receipt; never use a new UUID
to force a repeated adjustment. Report read-back confirmed quantities separately
from uncertain outcomes, and stop mutations while the outcome remains uncertain.
Inventory records do not prove a physical item moved.
