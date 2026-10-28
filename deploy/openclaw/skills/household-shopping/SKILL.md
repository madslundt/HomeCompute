---
name: household-shopping
description: Read or change the household Home Assistant shopping list, including quantities, notes and completion state.
---

# Household shopping

Use `homecompute__homecompute_shopping_list` for current list evidence. Its
optional `query` narrows results; `kind: shopping` selects shopping items,
`watches` selects discount watches, and `all` includes both. Keep these item
types distinct. For offer searches or watch policies, load `discount-shopping`.

Query results may include semantic candidates with different names. Check the
actual product, package and unit; clarify alternatives before a change. Use an
unfiltered current list when exact completeness matters.

An explicit owner request authorizes the corresponding routine list change.
Ask for clarification when the intended item, quantity or change is ambiguous.
Read the current item first and use its exact `item_id` and `expected_digest`;
preserve fields the owner did not ask to change.

- Add with `homecompute__homecompute_shopping_add`: `name`, integer `amount`
  from 1 through 9999, and `request_id`. Clarify quantities the integer model
  cannot represent instead of silently converting them.
- Edit with `homecompute__homecompute_shopping_update`: the item identity and
  digest, plus only the requested `name`, `amount` or `note` changes.
- Mark bought or reopen with `homecompute__homecompute_shopping_complete`, using
  the item identity, digest and exact `completed` boolean.
- Delete the specifically requested item with
  `homecompute__homecompute_shopping_remove`, using its identity and digest.

Choose one version-4 UUID `request_id` per requested change and retain it with that change.
A conflict requires a fresh read and reconciliation of the owner's intent.
For an interrupted or uncertain write, inspect
`homecompute__homecompute_household_request_status` with that same UUID, then
read current state. Reuse the UUID when retrieving/replaying its completed
receipt; never create a new UUID to force an uncertain write through.
If the receipt and current state leave the outcome uncertain, stop mutations
and report that uncertainty.

Report the affected item and whether read-back confirmed the requested state.
An `uncertain` outcome remains uncertain until reconciled. List completion does
not prove purchase, delivery, a partner notification or n8n synchronization.
Bulk clearing is unsupported; handle specifically identified item changes.
