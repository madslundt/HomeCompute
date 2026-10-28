---
name: discount-shopping
description: Compare current store offers or manage household discount watches, including target unit prices.
---

# Discount shopping

Search with `homecompute__homecompute_shopping_search`; use
`shopping_stores` to resolve a store and `shopping_offers` for that store's
offers, with the same `homecompute__homecompute_` tool prefix. Check the source,
retrieval time, validity dates, store, package quantity and unit before comparing
prices. Distinguish package price from unit price; explain missing quantities
or expired offers instead of presenting an unsupported cheapest option.
Results are bounded: an omitted offer is not proof that no offer exists.

Read existing watches and captured matches through
`homecompute__homecompute_discounts_read`, optionally with `item_id`. Use
`homecompute__homecompute_shopping_list` with `kind: watches` for current watch
identities and digests. An explicit owner request authorizes the corresponding
routine watch change; clarify an ambiguous product or price threshold.

Use `homecompute__homecompute_discount_watch` with `name` and a version-4 UUID
`request_id`. Optional `max_unit_price` is a positive number up to 1000000;
use `clear_max_unit_price: true` to remove a threshold, separately from setting
one. Editing an existing watch requires both its current `item_id` and
`expected_digest` and its actual watched name. Preserve unspecified policy.
Remove a requested watch with `homecompute__homecompute_discount_unwatch`, its
current identity/digest and `request_id`; ordinary shopping items use the
household-shopping workflow.

Keep the same UUID for receipt recovery through
`homecompute__homecompute_household_request_status`. For uncertainty, inspect
that receipt and current watch state; a new UUID must not force a repeated write.
If the outcome remains uncertain, stop mutations and report that uncertainty.
Report confirmed policy state separately from captured offer freshness. A watch
edit may wait for the existing daily n8n refresh; do not claim a refresh, alert
or push was delivered from the edit receipt alone.
