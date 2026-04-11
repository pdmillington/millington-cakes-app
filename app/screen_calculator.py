# screen_calculator.py
import streamlit as st
from math import pi
import millington_db as db


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

    recipe_map = {r["name"]: r for r in recipes}
    tier_map   = {t["code"]: t for t in size_tiers}
    ing_map    = {i["name"]: i for i in ingredients}

    # Settings
    default_labour  = float(settings.get("default_labour_rate") or 30.0)
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
    selected_name = st.selectbox("Recipe", recipe_names, key="calc_recipe")
    recipe = recipe_map.get(selected_name, {})

    if not recipe:
        st.info("Select a recipe to continue.")
        return

    size_type      = recipe.get("size_type", "diameter")
    ref_diameter   = float(recipe.get("ref_diameter_cm") or 22)
    ref_height     = float(recipe.get("ref_height_cm") or 0)
    ref_weight_kg  = float(recipe.get("ref_weight_kg") or 1)
    ref_portions   = int(recipe.get("ref_portions") or 1)
    has_individual = bool(recipe.get("has_individual"))
    has_bocado     = bool(recipe.get("has_bocado"))
    ind_weight     = float(recipe.get("individual_weight_g") or ind_weight_g)
    boc_weight     = float(recipe.get("bocado_weight_g") or boc_weight_g)
    ref_batch_size = float(recipe.get("ref_batch_size") or 20)
    ref_prep_hours = float(recipe.get("ref_prep_hours") or 1.0)
    ref_oven_hours = float(recipe.get("ref_oven_hours") or 1.0)

    # Estimate finished weight from ingredient sum
    # Only needed if recipe has Individual or Bocado formats
    if has_individual or has_bocado:
        _lines_for_weight = db.get_recipe_lines(recipe["id"])
        _weight_result    = db.estimate_recipe_weight(
            _lines_for_weight, ingredients
        )
        ref_weight_g  = _weight_result["weight_g"]
        _weight_notes = _weight_result["notes"]
        _weight_excl  = _weight_result["excluded"]
    else:
        ref_weight_g  = 0.0
        _weight_notes = []
        _weight_excl  = []

    # ── Section 2: Channel ────────────────────────────────────────────────────
    st.markdown("### 2 — Price channel")

    channel = st.radio(
        "Channel", ["Wholesale", "Retail"],
        horizontal=True, key="calc_channel"
    )

    st.divider()

    # ── Section 3: Format ─────────────────────────────────────────────────────
    st.markdown("### 3 — Format")

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

    # ── Determine scale, batch, intensity and margin from format + channel ────
    if selected_format == "Standard":
        tier       = tier_map.get("LA", {})
        intensity  = float(tier.get("labour_intensity") or 1.0)
        batch_size = ws_batch_large if channel == "Wholesale" else rt_batch_large
        margin     = ws_margin if channel == "Wholesale" else rt_margin_large

        if size_type == "diameter":
            st.markdown("**Size**")
            c1, c2 = st.columns(2)
            with c1:
                target_diameter = st.number_input(
                    "Diameter (cm)", min_value=1.0,
                    value=ref_diameter, key="calc_diameter"
                )
            with c2:
                target_height = st.number_input(
                    "Height (cm)", min_value=0.0,
                    value=ref_height if ref_height else 5.0,
                    key="calc_height"
                )
            if ref_height and target_height:
                scale = (target_diameter ** 2 * target_height) / \
                        (ref_diameter ** 2 * ref_height)
                st.info(
                    f"Volume scaling: ({target_diameter:.0f}² × "
                    f"{target_height:.1f}) / ({ref_diameter:.0f}² × "
                    f"{ref_height:.1f}) = **{scale:.3f}×**"
                )
            else:
                scale = (target_diameter ** 2) / (ref_diameter ** 2)
                st.warning(
                    f"⚠️ No reference height — scaling by area only "
                    f"({scale:.3f}×). Add height in recipe editor."
                )
            size_labour_factor = target_diameter / ref_diameter

        elif size_type == "weight":
            target_weight = st.number_input(
                "Weight (kg)", min_value=0.1,
                value=ref_weight_kg, key="calc_weight"
            )
            scale              = target_weight / ref_weight_kg
            size_labour_factor = 1.0
            st.info(f"Weight scaling: {target_weight:.2f} / "
                    f"{ref_weight_kg:.2f} = **{scale:.3f}×**")

        else:  # portions
            target_portions = st.number_input(
                "Portions", min_value=1,
                value=ref_portions, key="calc_portions"
            )
            scale              = target_portions / ref_portions
            size_labour_factor = 1.0
            st.info(f"Portion scaling: {target_portions} / "
                    f"{ref_portions} = **{scale:.3f}×**")

    elif selected_format == "Individual":
        tier       = tier_map.get("IN", {})
        intensity  = float(tier.get("labour_intensity") or 1.5)
        batch_size = ws_batch_ind if channel == "Wholesale" else rt_batch_ind
        margin     = ws_margin if channel == "Wholesale" else rt_margin_ind
        scale              = ind_weight / ref_weight_g
        size_labour_factor = 1.0
        st.info(
            f"Individual: {ind_weight:.0f}g portion — "
            f"reference cake ≈ {ref_weight_g:.0f}g — "
            f"scale: **{scale:.4f}×**"
        )
        if _weight_notes or _weight_excl:
            with st.expander("Weight estimate detail"):
                st.caption(f"Estimated recipe weight: {ref_weight_g:.0f}g")
                for note in _weight_notes:
                    st.caption(f"  {note}")
                if _weight_excl:
                    st.warning(
                        f"Excluded (unknown unit weight): "
                        f"{', '.join(_weight_excl)}"
                    )

    else:  # Bocado
        tier       = tier_map.get("BO", {})
        intensity  = float(tier.get("labour_intensity") or 2.5)
        batch_size = ws_batch_boc if channel == "Wholesale" else rt_batch_boc
        margin     = ws_margin if channel == "Wholesale" else rt_margin_boc
        scale              = boc_weight / ref_weight_g
        size_labour_factor = 1.0
        st.info(
            f"Bocado: {boc_weight:.0f}g — "
            f"reference cake ≈ {ref_weight_g:.0f}g — "
            f"scale: **{scale:.4f}×**"
        )
        if _weight_notes or _weight_excl:
            with st.expander("Weight estimate detail"):
                st.caption(f"Estimated recipe weight: {ref_weight_g:.0f}g")
                for note in _weight_notes:
                    st.caption(f"  {note}")
                if _weight_excl:
                    st.warning(
                        f"Excluded (unknown unit weight): "
                        f"{', '.join(_weight_excl)}"
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

    # ── Section 6: Order quantity (secondary) ─────────────────────────────────
    with st.expander("Order quantity (for total cost breakdown)"):
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
        # Total time for a batch of `batch_size` scales as power law
        # from the reference batch time.
        # Per-unit cost = total batch time / batch_size
        #
        # qty_factor = (batch_size / ref_batch_size)^power / batch_size
        #
        # Wholesale (batch_size=20, ref=20):
        #   (20/20)^0.7 / 20 = 1.0/20 = 0.05hr/cake
        # Retail (batch_size=1, ref=20):
        #   (1/20)^0.7 / 1  = 0.096hr/cake

        if ref_batch_size > 0:
            qty_factor = (
                (batch_size / ref_batch_size) ** labour_power
            ) / batch_size
        else:
            qty_factor = 1.0 / max(batch_size, 1)

        prep_per_unit = ref_prep_hours * qty_factor * size_labour_factor * intensity
        oven_per_unit = ref_oven_hours * qty_factor

        labour_cost = prep_per_unit * labour_rate
        oven_cost   = oven_per_unit * oven_rate

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

        # ── Totals ────────────────────────────────────────────────────────────
        cost_per_unit  = (ingredient_cost + labour_cost
                          + oven_cost + packaging_cost)
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

        # Headline per-unit metrics
        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("Cost per cake", f"€ {cost_per_unit:.2f}")
        with col_b:
            channel_label = "Wholesale" if channel == "Wholesale" else "Retail"
            st.metric(
                f"{channel_label} price per cake",
                f"€ {price_per_unit:.2f}",
                help=f"Cost × {margin:.1f}× margin"
            )

        # Cost breakdown
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

        # Labour detail — for sense-checking
        with st.expander("Labour calculation detail"):
            st.markdown(f"""
**Reference:** {ref_batch_size:.0f} cakes — 
{ref_prep_hours:.1f}h prep · {ref_oven_hours:.1f}h oven

**Pricing assumption ({channel}):** batch of {batch_size} cakes

**Formula:** (batch / ref_batch)^power / batch × intensity × size_factor

- Quantity factor: ({batch_size} / {ref_batch_size:.0f})^{labour_power} 
  / {batch_size} = **{qty_factor:.5f}**
- Size labour factor: **{size_labour_factor:.3f}**
- Tier intensity: **{intensity:.1f}×**
- Prep per cake: {ref_prep_hours:.2f} × {qty_factor:.5f} × 
  {size_labour_factor:.3f} × {intensity:.1f} = **{prep_per_unit:.5f}h**
- Oven per cake: {ref_oven_hours:.2f} × {qty_factor:.5f} = 
  **{oven_per_unit:.5f}h**
- Labour cost: {prep_per_unit:.5f}h × €{labour_rate:.2f} = 
  **€ {labour_cost:.4f}**
- Oven cost: {oven_per_unit:.5f}h × €{oven_rate:.2f} = 
  **€ {oven_cost:.4f}**
- Margin: **{margin:.1f}×** ({channel})
- Ingredient scale factor: **{scale:.5f}×**
            """)

        st.caption(
            f"Channel: {channel} · Format: {selected_format} · "
            f"Scale: {scale:.4f}× · Margin: {margin:.1f}×"
        )
