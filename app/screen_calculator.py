# screen_calculator.py
# =============================================================================
# Cost calculator — daily-use tool, mobile-first single column layout.
#
# Pricing logic:
#   Wholesale — always 20 cake batch, 2× margin
#   Retail    — 1 cake batch, 3× margin (large), 3.5× (individual), 4× (bocado)
#
# Ingredient scaling:
#   Diameter recipes — by volume (r² × h ratio)
#   Weight/portion   — by weight or portion ratio
#   Individual/Bocado — by weight (individual_weight_g / ref_weight_equivalent)
#
# Labour scaling:
#   Power law: (order_qty / batch_size) ^ labour_power
#   Size factor: target_diameter / ref_diameter (diameter recipes only)
#   Intensity: from size tier table
# =============================================================================

import streamlit as st
import millington_db as db


# Unit conversion to base units (g for weight, ml for volume)
_UNIT_TO_BASE = {"g": 1.0, "kg": 1000.0, "ml": 1.0, "l": 1000.0, "units": 1.0}


def screen_calculator():
    st.title("Cost calculator")
    st.caption("Per-cake cost and suggested price for any recipe and format")

    # ── Load reference data ───────────────────────────────────────────────────
    recipes     = db.get_recipes()
    settings    = db.get_settings()
    size_tiers  = db.get_size_tiers()
    presets     = db.get_packaging_presets()
    consumables = db.get_consumables()
    ingredients = db.get_ingredients()

    recipe_map  = {r["name"]: r for r in recipes}
    tier_map    = {t["code"]: t for t in size_tiers}
    ing_map     = {i["name"]: i for i in ingredients}

    # Settings
    default_labour  = float(settings.get("default_labour_rate") or 20.0)
    default_oven    = float(settings.get("default_oven_rate") or 2.0)
    labour_power    = float(settings.get("labour_power") or 0.7)
    ws_margin       = float(settings.get("ws_margin") or 2.0)
    rt_margin_large = float(settings.get("rt_margin_large") or 3.0)
    rt_margin_ind   = float(settings.get("rt_margin_individual") or 3.5)
    rt_margin_boc   = float(settings.get("rt_margin_bocado") or 4.0)
    ws_batch_large  = int(settings.get("ws_batch_large") or 20)
    ws_batch_ind    = int(settings.get("ws_batch_individual") or 100)
    ws_batch_boc    = int(settings.get("ws_batch_bocado") or 250)
    rt_batch_large  = int(settings.get("rt_batch_large") or 1)
    rt_batch_ind    = int(settings.get("rt_batch_individual") or 4)
    rt_batch_boc    = int(settings.get("rt_batch_bocado") or 10)
    ind_weight_g    = float(settings.get("individual_weight_g") or 100)
    boc_weight_g    = float(settings.get("bocado_weight_g") or 30)

    # ── Section 1: Recipe ─────────────────────────────────────────────────────
    st.markdown("### 1 — Recipe")

    recipe_names = sorted([r["name"] for r in recipes])
    selected_name = st.selectbox(
        "Recipe", recipe_names, key="calc_recipe"
    )
    recipe = recipe_map.get(selected_name, {})

    if not recipe:
        st.info("Select a recipe to continue.")
        return

    size_type    = recipe.get("size_type", "diameter")
    ref_diameter = float(recipe.get("ref_diameter_cm") or 22)
    ref_height   = float(recipe.get("ref_height_cm") or 0)
    ref_weight   = float(recipe.get("ref_weight_kg") or 1)
    ref_portions = int(recipe.get("ref_portions") or 1)
    has_individual = bool(recipe.get("has_individual"))
    has_bocado     = bool(recipe.get("has_bocado"))
    ind_weight     = float(recipe.get("individual_weight_g") or ind_weight_g)
    boc_weight     = float(recipe.get("bocado_weight_g") or boc_weight_g)

    # Labour reference times
    ref_batch_size = float(recipe.get("ref_batch_size") or 20)
    ref_prep_hours = float(recipe.get("ref_prep_hours") or 1.0)
    ref_oven_hours = float(recipe.get("ref_oven_hours") or 1.0)

    # ── Section 2: Channel ────────────────────────────────────────────────────
    st.markdown("### 2 — Price channel")

    channel = st.radio(
        "Channel",
        ["Wholesale", "Retail"],
        horizontal=True,
        key="calc_channel"
    )

    st.divider()

    # ── Section 3: Format ─────────────────────────────────────────────────────
    st.markdown("### 3 — Format")

    # Build available format buttons
    formats = ["Standard"]
    if has_individual:
        formats.append("Individual")
    if has_bocado:
        formats.append("Bocado")

    if len(formats) == 1:
        selected_format = "Standard"
        st.caption("Only standard format available for this recipe.")
    else:
        selected_format = st.radio(
            "Format", formats, horizontal=True, key="calc_format"
        )

    # Determine scale, batch size, intensity and margin from format + channel
    if selected_format == "Standard":
        tier        = tier_map.get("LA", {})
        intensity   = float(tier.get("labour_intensity") or 1.0)
        batch_size  = ws_batch_large if channel == "Wholesale" else rt_batch_large
        margin      = ws_margin if channel == "Wholesale" else rt_margin_large

        if size_type == "diameter":
            # Show size inputs
            st.markdown("**Size**")
            c1, c2 = st.columns(2)
            with c1:
                target_diameter = st.number_input(
                    "Diameter (cm)",
                    min_value=1.0,
                    value=ref_diameter,
                    key="calc_diameter"
                )
            with c2:
                target_height = st.number_input(
                    "Height (cm)",
                    min_value=0.0,
                    value=ref_height if ref_height else 5.0,
                    key="calc_height"
                )

            if ref_height and target_height:
                scale = (target_diameter ** 2 * target_height) / \
                        (ref_diameter ** 2 * ref_height)
            else:
                scale = (target_diameter ** 2) / (ref_diameter ** 2)
                st.warning(
                    "⚠️ No reference height — scaling by area only. "
                    "Add height in the recipe editor for accurate results."
                )

            size_labour_factor = target_diameter / ref_diameter

        elif size_type == "weight":
            target_weight = st.number_input(
                "Weight (kg)",
                min_value=0.1,
                value=ref_weight,
                key="calc_weight"
            )
            scale              = target_weight / ref_weight
            size_labour_factor = 1.0

        else:  # portions
            target_portions = st.number_input(
                "Portions",
                min_value=1,
                value=ref_portions,
                key="calc_portions"
            )
            scale              = target_portions / ref_portions
            size_labour_factor = 1.0

    elif selected_format == "Individual":
        tier       = tier_map.get("IN", {})
        intensity  = float(tier.get("labour_intensity") or 1.5)
        batch_size = ws_batch_ind if channel == "Wholesale" else rt_batch_ind
        margin     = ws_margin if channel == "Wholesale" else rt_margin_ind

        # Scale by weight: individual_weight / reference_weight_equivalent
        # Reference weight equivalent derived from reference size
        if size_type == "diameter":
            # Approximate reference cake weight from volume × density (0.4 g/cm³)
            import math
            ref_vol_cm3    = math.pi * (ref_diameter / 2) ** 2 * \
                             (ref_height if ref_height else 5.0)
            ref_weight_g   = ref_vol_cm3 * 0.4
        else:
            ref_weight_g   = ref_weight * 1000

        scale              = ind_weight / ref_weight_g
        size_labour_factor = 1.0
        st.caption(
            f"Individual portion: {ind_weight:.0f}g "
            f"(reference cake ≈ {ref_weight_g:.0f}g) — "
            f"scale factor: {scale:.4f}×"
        )

    else:  # Bocado
        tier       = tier_map.get("BO", {})
        intensity  = float(tier.get("labour_intensity") or 2.5)
        batch_size = ws_batch_boc if channel == "Wholesale" else rt_batch_boc
        margin     = ws_margin if channel == "Wholesale" else rt_margin_boc

        if size_type == "diameter":
            import math
            ref_vol_cm3  = math.pi * (ref_diameter / 2) ** 2 * \
                           (ref_height if ref_height else 5.0)
            ref_weight_g = ref_vol_cm3 * 0.4
        else:
            ref_weight_g = ref_weight * 1000

        scale              = boc_weight / ref_weight_g
        size_labour_factor = 1.0
        st.caption(
            f"Bocado: {boc_weight:.0f}g "
            f"(reference cake ≈ {ref_weight_g:.0f}g) — "
            f"scale factor: {scale:.4f}×"
        )

    st.divider()

    # ── Section 4: Packaging ──────────────────────────────────────────────────
    st.markdown("### 4 — Packaging")

    preset_names = ["— none —"] + [p["name"] for p in presets]
    selected_preset_name = st.selectbox(
        "Packaging preset", preset_names, key="calc_preset"
    )

    preset_lines = []
    if selected_preset_name != "— none —":
        preset = next(
            (p for p in presets if p["name"] == selected_preset_name), None
        )
        if preset:
            preset_lines = db.get_preset_lines(preset["id"])
            for line in preset_lines:
                cpu = line.get("consumable_cost_per_unit") or 0
                qty = float(line.get("quantity") or 1)
                st.caption(
                    f"  {line['consumable_name']} × {qty:.0f} "
                    f"— € {cpu * qty:.4f}"
                )
    else:
        st.caption("Or select consumables manually:")
        con_names = ["— none —"] + [c["name"] for c in consumables]
        for i in range(1, 4):
            cc1, cc2 = st.columns([3, 1])
            with cc1:
                st.selectbox(
                    f"Consumable {i}", con_names,
                    key=f"calc_con_{i}",
                    label_visibility="collapsed"
                )
            with cc2:
                st.number_input(
                    "Qty", min_value=0.0, value=1.0,
                    key=f"calc_con_qty_{i}",
                    label_visibility="collapsed"
                )

    st.divider()

    # ── Section 5: Labour rates ───────────────────────────────────────────────
    st.markdown("### 5 — Labour rates")
    st.caption("Pre-filled from settings — adjust per session if needed.")

    col_l, col_o = st.columns(2)
    with col_l:
        labour_rate = st.number_input(
            "Labour (€/hr)", min_value=0.0,
            value=default_labour, key="calc_labour_rate"
        )
    with col_o:
        oven_rate = st.number_input(
            "Oven (€/hr)", min_value=0.0,
            value=default_oven, key="calc_oven_rate"
        )

    st.divider()

    # ── Section 6: Number of units (secondary) ────────────────────────────────
    with st.expander("Order quantity (optional — for total cost)"):
        order_qty = st.number_input(
            "Number of cakes / units",
            min_value=1, value=1, key="calc_order_qty"
        )
    order_qty = st.session_state.get("calc_order_qty", 1)

    st.divider()

    # ── Calculate ─────────────────────────────────────────────────────────────
    if st.button("Calculate", type="primary", use_container_width=True):

        lines = db.get_recipe_lines(recipe["id"])

        # ── Ingredient cost per unit ──────────────────────────────────────────
        ingredient_cost = 0.0
        missing_prices  = []

        for line in lines:
            ing_name = line.get("ingredient_name", "")
            amount   = float(line.get("amount") or 0)
            ing      = ing_map.get(ing_name, {})
            cpu      = ing.get("cost_per_unit")

            if cpu:
                ingredient_cost += cpu * amount * scale
            elif ing_name:
                missing_prices.append(ing_name)

        # ── Labour cost per unit ──────────────────────────────────────────────
        # batch_size already set from channel + format above
        if ref_batch_size > 0:
            qty_factor = (1 / ref_batch_size) ** labour_power
        else:
            qty_factor = 1.0

        prep_hours = (
            ref_prep_hours
            * qty_factor
            * size_labour_factor
            * intensity
        )
        oven_hours = (
            ref_oven_hours
            * qty_factor
        )

        labour_cost = prep_hours * labour_rate
        oven_cost   = oven_hours * oven_rate

        # ── Packaging cost per unit ───────────────────────────────────────────
        packaging_cost = 0.0

        if preset_lines:
            for line in preset_lines:
                cpu = line.get("consumable_cost_per_unit") or 0
                qty = float(line.get("quantity") or 1)
                packaging_cost += cpu * qty
        else:
            for i in range(1, 4):
                con_name = st.session_state.get(f"calc_con_{i}", "— none —")
                con_qty  = float(st.session_state.get(f"calc_con_qty_{i}", 1))
                if con_name and con_name != "— none —":
                    con = next(
                        (c for c in consumables if c["name"] == con_name), {}
                    )
                    cpu = con.get("cost_per_unit") or 0
                    packaging_cost += cpu * con_qty

        # ── Per unit totals ───────────────────────────────────────────────────
        cost_per_unit  = ingredient_cost + labour_cost + oven_cost + packaging_cost
        price_per_unit = cost_per_unit * margin

        # ── Display results ───────────────────────────────────────────────────
        st.markdown("---")
        st.markdown(
            f"### {selected_name} — {selected_format} — {channel}"
        )

        if missing_prices:
            st.warning(
                f"⚠️ Missing prices for: {', '.join(missing_prices)}. "
                "Ingredient cost is understated."
            )

        # Headline metrics — per unit
        col_a, col_b = st.columns(2)
        with col_a:
            st.metric(
                "Cost per cake",
                f"€ {cost_per_unit:.2f}"
            )
        with col_b:
            st.metric(
                f"{'Wholesale' if channel == 'Wholesale' else 'Retail'} "
                f"price per cake",
                f"€ {price_per_unit:.2f}",
                help=f"Cost × {margin:.1f}× margin"
            )

        # Breakdown
        st.markdown("**Cost breakdown**")
        col_c, col_d, col_e, col_f = st.columns(4)
        col_c.metric("Ingredients", f"€ {ingredient_cost:.4f}")
        col_d.metric("Labour",      f"€ {labour_cost:.4f}")
        col_e.metric("Oven",        f"€ {oven_cost:.4f}")
        col_f.metric("Packaging",   f"€ {packaging_cost:.4f}")

        # Order total if quantity > 1
        if order_qty > 1:
            st.divider()
            st.markdown(f"**Total for {order_qty} unit(s)**")
            col_g, col_h = st.columns(2)
            col_g.metric("Total cost",  f"€ {cost_per_unit * order_qty:.2f}")
            col_h.metric("Total price", f"€ {price_per_unit * order_qty:.2f}")

        # Labour detail expander — for sense-checking
        with st.expander("Labour calculation detail"):
            st.markdown(f"""
- Reference batch: **{ref_batch_size:.0f}** cakes — 
  {ref_prep_hours:.1f}h prep · {ref_oven_hours:.1f}h oven
- Batch assumption ({channel}): **{batch_size}** cakes
- Quantity factor: (1 / {ref_batch_size:.0f})^{labour_power} 
  = **{qty_factor:.4f}**
- Size labour factor: **{size_labour_factor:.3f}**
- Tier intensity: **{intensity:.1f}×**
- Prep hours (per unit): **{prep_hours:.4f}h** 
  × €{labour_rate:.2f}/hr = **€ {labour_cost:.4f}**
- Oven hours (per unit): **{oven_hours:.4f}h** 
  × €{oven_rate:.2f}/hr = **€ {oven_cost:.4f}**
- Margin: **{margin:.1f}×** ({channel})
- Scale factor: **{scale:.4f}×**
            """)
