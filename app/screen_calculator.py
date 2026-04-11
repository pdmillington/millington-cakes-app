# screen_calculator.py
# =============================================================================
# Cost calculator screen — the daily-use tool.
# Mobile-first: single column layout, clear sections, large inputs.
#
# Calculation logic:
#   Ingredients — scale by volume (diameter²×height) or weight/portions ratio
#   Labour      — power law scaling with batch reference times, size factor
#                 for diameter recipes, intensity factor from size tier
#   Oven        — same power law, no size factor
#   Packaging   — linear per unit (preset or manual)
# =============================================================================

import streamlit as st
from math import pi
import database as db


def screen_calculator():
    st.title("Cost calculator")
    st.caption("Calculate ingredient, labour and packaging costs for any cake")

    # ── Load reference data ───────────────────────────────────────────────────
    recipes    = db.get_recipes()
    settings   = db.get_settings()
    size_tiers = db.get_size_tiers()
    presets    = db.get_packaging_presets()
    consumables = db.get_consumables()

    # Build lookups
    recipe_map   = {r["name"]: r for r in recipes}
    tier_map     = {t["code"]: t for t in size_tiers}
    preset_names = ["— none —"] + [p["name"] for p in presets]

    # Defaults from settings
    default_labour = float(settings.get("default_labour_rate") or 30.0)
    default_oven   = float(settings.get("default_oven_rate") or 2.0)
    default_margin = float(settings.get("default_margin") or 3.0)
    labour_power   = float(settings.get("labour_power") or 0.7)

    # ── Section 1: Recipe & size ──────────────────────────────────────────────
    st.markdown("### 1 — Recipe & size")

    recipe_names = sorted([r["name"] for r in recipes])
    selected_recipe_name = st.selectbox(
        "Recipe", recipe_names,
        key="calc_recipe"
    )
    recipe = recipe_map.get(selected_recipe_name, {})

    size_type = recipe.get("size_type", "diameter")

    # Price channel determines batch assumption
    channel = st.radio(
        "Price channel",
        ["Wholesale", "Retail"],
        horizontal=True,
        key="calc_channel",
        help="Wholesale uses large batch labour assumptions. "
             "Retail uses small run assumptions."
    )

    # Order quantity
    order_qty = st.number_input(
        "Number of cakes / units to price",
        min_value=1, value=1,
        key="calc_order_qty"
    )

    st.divider()

    # ── Section 2: Size ───────────────────────────────────────────────────────
    st.markdown("### 2 — Size")

    ref_diameter = float(recipe.get("ref_diameter_cm") or 22)
    ref_height   = float(recipe.get("ref_height_cm") or 0)
    ref_weight   = float(recipe.get("ref_weight_kg") or 1)
    ref_portions = int(recipe.get("ref_portions") or 1)

    # Size tier selector — filters to relevant tiers for this recipe
    if size_type == "diameter":
        relevant_tiers = [
            t for t in size_tiers
            if t.get("size_type") == "diameter" or t.get("is_numeric")
        ]
        tier_labels = [f"{t['code']} — {t['label']}" for t in relevant_tiers]
        # Default to LA if available
        default_tier = next(
            (i for i, t in enumerate(relevant_tiers) if t["code"] == "LA"), 0
        )
        selected_tier_label = st.selectbox(
            "Size tier", tier_labels, index=default_tier,
            key="calc_tier"
        )
        selected_tier = next(
            (t for t in relevant_tiers
             if f"{t['code']} — {t['label']}" == selected_tier_label), {}
        )

        if selected_tier.get("is_numeric"):
            target_diameter = float(selected_tier.get("numeric_value") or ref_diameter)
            st.caption(f"Fixed diameter: {target_diameter} cm")
        else:
            # Use midpoint of tier range as default
            tier_min = float(selected_tier.get("min_value") or ref_diameter)
            tier_max = float(selected_tier.get("max_value") or ref_diameter)
            default_diam = (tier_min + tier_max) / 2
            target_diameter = st.number_input(
                "Target diameter (cm)",
                min_value=1.0,
                value=default_diam,
                key="calc_diameter"
            )

        target_height = st.number_input(
            "Target height (cm)",
            min_value=0.0,
            value=ref_height if ref_height else 5.0,
            key="calc_height",
            help="Enter the finished cake height. "
                 "Used for volume-based ingredient scaling."
        )

        # Scaling factor
        if ref_height and target_height:
            scale = (target_diameter ** 2 * target_height) / \
                    (ref_diameter ** 2 * ref_height)
            st.info(
                f"Scaling by volume: "
                f"({target_diameter:.0f}² × {target_height:.1f}) / "
                f"({ref_diameter:.0f}² × {ref_height:.1f}) "
                f"= **{scale:.3f}×** reference recipe"
            )
        else:
            scale = (target_diameter ** 2) / (ref_diameter ** 2)
            st.warning(
                f"⚠️ No reference height set — scaling by area only "
                f"({target_diameter:.0f}² / {ref_diameter:.0f}²"
                f" = {scale:.3f}×). "
                f"Add height in the recipe editor for accurate results."
            )

        # Size labour factor — linear ratio of diameters
        size_labour_factor = target_diameter / ref_diameter

    elif size_type == "weight":
        target_weight = st.number_input(
            "Target weight (kg)",
            min_value=0.1,
            value=ref_weight,
            key="calc_weight"
        )
        scale = target_weight / ref_weight
        size_labour_factor = 1.0  # no size factor for weight recipes
        selected_tier = tier_map.get("LA", {})
        st.info(f"Scaling by weight: {target_weight:.2f} / "
                f"{ref_weight:.2f} = **{scale:.3f}×** reference recipe")

    else:  # portions
        target_portions = st.number_input(
            "Number of portions",
            min_value=1,
            value=ref_portions,
            key="calc_portions"
        )
        scale = target_portions / ref_portions
        size_labour_factor = 1.0
        selected_tier = tier_map.get("IN", {})
        st.info(f"Scaling by portions: {target_portions} / "
                f"{ref_portions} = **{scale:.3f}×** reference recipe")

    st.divider()

    # ── Section 3: Labour & oven ──────────────────────────────────────────────
    st.markdown("### 3 — Labour & oven")

    # Get batch reference times from recipe, fall back to defaults
    ref_batch_size  = float(recipe.get("ref_batch_size") or 20)
    ref_prep_hours  = float(recipe.get("ref_prep_hours") or 1.0)
    ref_oven_hours  = float(recipe.get("ref_oven_hours") or 1.0)

    # Tier intensity factor
    tier_intensity = float(selected_tier.get("labour_intensity") or 1.0)

    # Wholesale vs retail batch reference
    if channel == "Wholesale":
        ws_batch = int(settings.get("ws_batch_large") or 20)
        display_batch = ws_batch
    else:
        rt_batch = int(settings.get("rt_batch_large") or 1)
        display_batch = rt_batch

    st.caption(
        f"Reference: {ref_batch_size:.0f} cakes — "
        f"{ref_prep_hours:.1f}h prep, {ref_oven_hours:.1f}h oven. "
        f"Labour intensity: {tier_intensity:.1f}×. "
        f"Size factor: {size_labour_factor:.2f}×."
    )

    col_l, col_o = st.columns(2)
    with col_l:
        labour_rate = st.number_input(
            "Labour rate (€/hr)",
            min_value=0.0,
            value=default_labour,
            key="calc_labour_rate"
        )
    with col_o:
        oven_rate = st.number_input(
            "Oven rate (€/hr)",
            min_value=0.0,
            value=default_oven,
            key="calc_oven_rate"
        )

    st.divider()

    # ── Section 4: Packaging ──────────────────────────────────────────────────
    st.markdown("### 4 — Packaging")

    use_preset = st.checkbox(
        "Use packaging preset", value=bool(presets),
        key="calc_use_preset"
    )

    if use_preset and presets:
        selected_preset_name = st.selectbox(
            "Packaging preset", preset_names,
            key="calc_preset"
        )
        if selected_preset_name != "— none —":
            preset = next(
                (p for p in presets if p["name"] == selected_preset_name), None
            )
            if preset:
                preset_lines = db.get_preset_lines(preset["id"])
                for line in preset_lines:
                    st.caption(
                        f"  {line['consumable_name']} × "
                        f"{line['quantity']:.0f} — "
                        f"€ {(line['consumable_cost_per_unit'] or 0) * line['quantity']:.4f}"
                    )
        else:
            preset_lines = []
    else:
        preset_lines = []
        st.caption("Select up to 3 consumables manually:")
        con_names = ["— none —"] + [c["name"] for c in consumables]
        for i in range(1, 4):
            cc1, cc2 = st.columns([3, 1])
            with cc1:
                st.selectbox(
                    f"Consumable {i}", con_names,
                    key=f"calc_con_{i}"
                )
            with cc2:
                st.number_input(
                    "Qty", min_value=0.0, value=1.0,
                    key=f"calc_con_qty_{i}"
                )

    st.divider()

    # ── Section 5: Margin ─────────────────────────────────────────────────────
    st.markdown("### 5 — Margin")
    margin = st.number_input(
        "Margin multiplier",
        min_value=1.0,
        value=default_margin,
        step=0.1,
        key="calc_margin",
        help="Suggested retail price = total cost × this multiplier"
    )

    st.divider()

    # ── Calculate ─────────────────────────────────────────────────────────────
    if st.button("Calculate cost", type="primary", use_container_width=True):

        ingredients = db.get_ingredients()
        ing_map     = {i["name"]: i for i in ingredients}

        lines = db.get_recipe_lines(recipe["id"])

        # ── Ingredient cost ───────────────────────────────────────────────────
        ingredient_cost = 0.0
        missing_prices  = []

        for line in lines:
            ing_name = line.get("ingredient_name", "")
            amount   = float(line.get("amount") or 0)
            ing      = ing_map.get(ing_name, {})
            cpu      = ing.get("cost_per_unit")

            if cpu:
                ingredient_cost += cpu * amount * scale * order_qty
            elif ing_name:
                missing_prices.append(ing_name)

        # ── Labour cost ───────────────────────────────────────────────────────
        # Formula:
        #   prep_hours_for_order = ref_prep_hours
        #                          × (order_qty / ref_batch_size) ^ labour_power
        #                          × size_labour_factor
        #                          × tier_intensity
        #   labour_cost = prep_hours_for_order × labour_rate

        if ref_batch_size > 0:
            prep_hours = (
                ref_prep_hours
                * ((order_qty / ref_batch_size) ** labour_power)
                * size_labour_factor
                * tier_intensity
            )
            oven_hours = (
                ref_oven_hours
                * ((order_qty / ref_batch_size) ** labour_power)
            )
        else:
            prep_hours = ref_prep_hours * order_qty
            oven_hours = ref_oven_hours * order_qty

        labour_cost = prep_hours * labour_rate
        oven_cost   = oven_hours * oven_rate

        # ── Packaging cost ────────────────────────────────────────────────────
        packaging_cost = 0.0

        if use_preset and preset_lines:
            for line in preset_lines:
                cpu = line.get("consumable_cost_per_unit") or 0
                qty = float(line.get("quantity") or 1)
                packaging_cost += cpu * qty * order_qty
        else:
            for i in range(1, 4):
                con_name = st.session_state.get(f"calc_con_{i}", "— none —")
                con_qty  = float(st.session_state.get(f"calc_con_qty_{i}", 1))
                if con_name and con_name != "— none —":
                    con = next(
                        (c for c in consumables if c["name"] == con_name), {}
                    )
                    cpu = con.get("cost_per_unit") or 0
                    packaging_cost += cpu * con_qty * order_qty

        # ── Totals ────────────────────────────────────────────────────────────
        total_cost      = ingredient_cost + labour_cost + oven_cost + packaging_cost
        suggested_price = total_cost * margin
        cost_per_unit   = total_cost / order_qty if order_qty else 0
        price_per_unit  = suggested_price / order_qty if order_qty else 0

        # ── Display results ───────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("### Result")

        if missing_prices:
            st.warning(
                f"⚠️ Missing prices for: {', '.join(missing_prices)}. "
                "Ingredient cost is understated — add prices in the "
                "Ingredients screen."
            )

        # Per-unit breakdown
        st.markdown(f"**Per cake ({order_qty} × {selected_recipe_name})**")

        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("Ingredients", f"€ {ingredient_cost/order_qty:.4f}")
            st.metric("Labour",      f"€ {labour_cost/order_qty:.4f}")
            st.metric("Oven",        f"€ {oven_cost/order_qty:.4f}")
            st.metric("Packaging",   f"€ {packaging_cost/order_qty:.4f}")
        with col_b:
            st.metric("Cost per cake",  f"€ {cost_per_unit:.2f}")
            st.metric("Suggested price per cake",
                      f"€ {price_per_unit:.2f}",
                      help=f"Cost × {margin:.1f} margin")

        st.divider()

        # Total for the order
        st.markdown(f"**Total for {order_qty} cake(s)**")
        col_c, col_d = st.columns(2)
        with col_c:
            st.metric("Total cost",  f"€ {total_cost:.2f}")
        with col_d:
            st.metric("Suggested total price", f"€ {suggested_price:.2f}")

        # Labour detail — helpful for sense-checking
        with st.expander("Labour calculation detail"):
            st.markdown(f"""
- Reference batch: **{ref_batch_size:.0f} cakes** — 
  {ref_prep_hours:.1f}h prep, {ref_oven_hours:.1f}h oven
- Order quantity: **{order_qty}** cakes
- Quantity factor: ({order_qty} / {ref_batch_size:.0f})^{labour_power} 
  = **{(order_qty/ref_batch_size)**labour_power:.3f}**
- Size labour factor: **{size_labour_factor:.3f}**
- Tier intensity: **{tier_intensity:.1f}×**
- Prep hours for order: **{prep_hours:.3f}h**
- Oven hours for order: **{oven_hours:.3f}h**
- Labour cost: {prep_hours:.3f}h × €{labour_rate:.2f}/hr 
  = **€ {labour_cost:.4f}**
- Oven cost: {oven_hours:.3f}h × €{oven_rate:.2f}/hr 
  = **€ {oven_cost:.4f}**
            """)

        # Channel reference
        st.caption(
            f"Priced on {channel} assumptions. "
            f"Scaling factor: {scale:.3f}×. "
            f"Margin: {margin:.1f}×."
        )
