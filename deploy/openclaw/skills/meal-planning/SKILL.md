---
name: meal-planning
description: Plans meals from household stock, expiry records and current offers. Use when choosing recipes, planning dinners or preparing ingredient and shopping proposals.
---

# Meals and pantry planning

1. Establish the number of diners, meals/dates, available cooking time, budget
   and explicitly stated dietary requirements. Clarify missing details that
   change the plan; label reasonable optional assumptions. Confirm allergies
   rather than inferring them from preferences or earlier meal choices.
2. Read current evidence with the granted `homecompute__homecompute_` tools:
   `storage_list` for quantities, units, locations and expiry records;
   `shopping_list` for items already planned; `discounts_read` for watch results;
   and `shopping_search`, `shopping_stores`, `shopping_offers` for current offers.
   Resolve actual products and units, and distinguish partial or stale results
   from complete inventory. Missing stock is unknown, not zero.
3. Build feasible recipes around confirmed ingredients. Prioritize items nearing
   expiry while keeping best-before and use-by labels distinct. An inventory
   record cannot establish food safety. Show uncertain stock, substitutions and
   quantities to check before cooking. Scale recipe amounts to the stated diners;
   keep recipe quantities separate from whole packages that must be bought.
4. For public recipe or offer research, load `public-web-research` and follow its
   protected browser fallback. Cite sources and retrieval dates. Keep household
   inventory and private dietary details out of external queries unless the
   owner explicitly authorizes that disclosure. Separate verified offer prices
   from estimates and identify store, package size and validity when available.
5. Return the meal plan, ingredient quantities, stock assumptions and a proposed
   shopping list. Give a cost estimate only for the covered ingredients and
   identify missing prices. Requested shopping edits go through Atlas to Vega;
   stock edits go through Atlas to Ceres. Hestia's tools are read-only: a proposed
   list or meal plan is not a saved list, inventory adjustment or order.

Example: for three dinners for four people, compare current pantry quantities
with each recipe, show which ingredients can be reused, and list purchases by
package with estimated leftovers. Mark unverified quantities and prices.
