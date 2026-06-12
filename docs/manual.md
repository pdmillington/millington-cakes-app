# Pricing Manager — User Guide

---

## Overview

The app is divided into three sections in the sidebar:

- **Daily use** — the screens you open most often
- **Manage** — master data (recipes, ingredients, prices)
- **Config** — one-time setup that rarely changes

---

## Workflow 1 — An ingredient price has changed

Use this when a supplier invoice comes in with a new price.

1. **Ingredients** → find the ingredient, click to edit
2. Update the pack price and save — cost per unit recalculates automatically
3. Open **Price review** to see which products are now off-target
4. If prices need adjusting, continue with Workflow 2

---

## Workflow 2 — Pricing review

Use this periodically (monthly, or after cost changes) to check margins across the range.

1. **Price review** shows calculated cost vs current prices for every product and format
2. Traffic lights show status at a glance:
   - **On target** — margin is within 5% of the target multiplier
   - **Review** — margin is outside the 5% tolerance (low or high)
   - **Below cost** — price does not cover calculated cost
   - **No price** — no price set yet
3. Use the **Edit prices** expander at the bottom to update working prices directly
4. Go to **Prices → Price matrix** to review and approve the changes when ready
5. Approved prices are used in catalogue PDFs — working prices are not

> Working prices can be edited freely without committing. Only approval pushes them into the catalogue.

---

## Workflow 3 — A new recipe is being developed

Use this when adding a new product to the range.

1. **Recipes** → add a new recipe, assign a cake code
   - Set the reference size (diameter / weight / portions) and reference batch size
   - Add all ingredient lines with amounts and set labour times
2. **Ingredients** → check all ingredients used have a current price set
3. **Packaging presets** → create a preset if this product has specific packaging
4. **Product formats** → set up the format(s) — standard, individual, bocado
   - Fill in shelf life, storage instructions and packaging
   - SKUs are built from cake code, size and channel segments
5. **Recipe cost breakdown** → verify the cost makes sense
6. **Cost calculator** → price up specific sizes and order quantities
7. **Prices** → set working prices, review, then approve
8. **Wholesale catalogue** → regenerate to include the new product

---

## Workflow 4 — A customer wants a quote

Use this for bespoke orders or unusual sizes.

1. **Cost calculator** → select the recipe
2. Choose channel (Wholesale / Retail) and format
3. For a non-standard size, adjust diameter or weight — the app scales costs automatically
4. Select the packaging preset, or pick consumables manually
5. Enter order quantity to see total cost and price
6. The current live price is shown alongside the calculated price for comparison

---

## Screen Reference

### Daily use

| Screen | Purpose |
|---|---|
| **Cost calculator** | Price any recipe at any size and quantity. Scales ingredient and labour costs automatically. |
| **Recipe cost breakdown** | Visual breakdown of what drives cost for a given recipe. Shows current price vs cost where available. |
| **Price review** | Full margin table across all products. Traffic-light status, edit prices inline, download as CSV. |
| **Business KPIs** | Revenue vs target, top products, ingredient spend. Primary data from monthly Excel uploads; current month supplemented from Holded API. |

### Manage

| Screen | Purpose |
|---|---|
| **Recipes** | Add and edit recipes, ingredient lines, labour times and cake code assignments. |
| **Ingredients** | Add and edit ingredients with supplier, pack size and price. Cost per unit is calculated automatically. |
| **Consumables** | Packaging materials and other consumables used in cost calculations. |
| **Prices** | Edit working prices and approve them for use in catalogues. Client-specific price overrides managed here. |
| **Wholesale catalogue** | Generate a branded PDF price list. Only approved prices appear. Supports client-specific pricing. |

### Config

| Screen | Purpose |
|---|---|
| **Product formats** | Technical product sheet per recipe and format — SKUs, shelf life, storage, allergen declaration, label approval. |
| **Packaging presets** | Named bundles of consumables (e.g. Caja tarta grande). Attach to recipes in the calculator. |
| **Settings** | Labour and oven rates, batch size assumptions, margin targets. Changes apply immediately to all calculations. |

---

## Key Concepts

### Working price vs approved price

Prices have two states. A **working price** is a draft — edit it freely in the Price matrix or Price review screen. An **approved price** is committed — it gets a timestamp and is the version that goes into catalogue PDFs and product sheets. You can update working prices as many times as needed before approving.

### Batch scaling

Labour cost is not linear — making 20 cakes takes less than 20× the time of making 1. The app uses a power-law formula (configurable in Settings) to reflect this. The reference batch size and labour hours are set per recipe in the Recipes screen.

### Ingredient scaling

For standard cakes, costs scale by volume (diameter² × height). For individual and bocado formats, they scale by weight relative to the estimated total recipe weight. The calculator shows the scale factor applied so you can verify it looks right.

### Cake codes and SKUs

Every product has a cake code (e.g. `CC` for Chocolate Crocanti). SKUs are built as `CODE-VERSION-SIZE-CHANNEL` (e.g. `CC-01-LA-GW`). SKUs link the pricing app to Holded and Shopify — keep them consistent.
