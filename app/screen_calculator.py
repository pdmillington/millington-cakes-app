
import streamlit as st
from math import pi
import millington_db as db
from core.constants import UNIT_TO_G, FORMAT_TIER_CODES, VAT_MULTIPLIER
from core.settings import load_settings


def screen_calculator():
    st.title("Cost calculator")
    st.caption("Per-cake cost and suggested price for any recipe and format")

    # ── Load reference data ───────────────────────────────────────────────────
    recipes     = db.get_recipes()
    presets     = db.get_packaging_presets()
    consumables = db.get_consumables()
    ingredients = db.get_ingredients()
    s = load_settings()

    recipe_map = {r["name"]: r for r in recipes}
    ing_map    = {i["name"]: i for i in ingredients}

    # ── Section 1: Recipe ─────────────────────────────────────────────────────
    st.markdown("### 1 — Recipe")

    recipe_names  = sorted([r["name"] for r in recipes])
    selected_name = st.selectbox("Recipe", recipe_names, key="calc_recipe")
    recipe        = recipe_map.get(selected_name, {})

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
    ind_weight     = float(recipe.get("individual_weight_g") or s.individual_weight_g)
    boc_weight     = float(recipe.get("bocado_weight_g") or s.bocado_weight_g)

    # Labour reference times
    ref_batch_size    = float(recipe.get("ref_batch_size") or 20)
    ref_prep_hours    = float(recipe.get("ref_prep_hours") or 1.0)
    ref_oven_hours    = float(recipe.get("ref_oven_hours") or 1.0)
    small_prep_hours  = float(recipe.get("small_batch_prep_hours") or 0.0)
    small_oven_hours  = float(recipe.get("small_batch_oven_hours") or 0.0)
    bocado_prep_hours = float(recipe.get("bocado_batch_prep_hours") or 0.0)
    bocado_oven_hours = float(recipe.get("bocado_batch_oven_hours") or 0.0)

    # Pre-compute reference weight for Individual/Bocado ingredient scaling
    if has_individual or has_bocado:
        _lines_for_weight = db.get_recipe_lines(recipe["id"])
        _weight_result    = db.estimate_recipe_weight(_lines_for_weight)
        ref_weight_g      = _weight_result["weight_g"]
        _weight_notes     = _weight_result["notes"]
        _weight_excl      = _weight_result["excluded"]
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

    # ── Parameters from format + channel ─────────────────────────────────────
    if selected_format == "Standard":
        batch_size       = s.ws_batch_large if channel == "Wholesale" else s.rt_batch_large
        margin           = s.ws_margin if channel == "Wholesale" else s.rt_margin_large
        labour_ref_prep  = ref_prep_hours
        labour_ref_oven  = ref_oven_hours
        labour_ref_batch = ref_batch_size

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

        else:
            target_portions = st.number_input(
                "Portions", min_value=1,
                value=ref_portions, key="calc_portions"
            )
            scale              = target_portions / ref_portions
            size_labour_factor = 1.0
            st.info(f"Portion scaling: {target_portions} / "
                    f"{ref_portions} = **{scale:.3f}×**")

    elif selected_format == "Individual":
        batch_size       = s.ws_batch_individual if channel == "Wholesale" else s.rt_batch_individual
        margin           = s.ws_margin if channel == "Wholesale" else s.rt_margin_individual
        labour_ref_prep  = small_prep_hours if small_prep_hours else ref_prep_hours
        labour_ref_oven  = small_oven_hours if small_oven_hours else ref_oven_hours
        labour_ref_batch = s.ws_batch_individual
        scale              = ind_weight / ref_weight_g if ref_weight_g else 0
        size_labour_factor = 1.0

        st.info(
            f"Individual: {ind_weight:.0f}g — "
            f"reference ≈ {ref_weight_g:.0f}g — "
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
        if not small_prep_hours:
            st.warning(
                "⚠️ No individual labour times set — using large format "
                "times as fallback. Add individual batch times in the "
                "recipe editor for accurate pricing."
            )

    else:  # Bocado
        batch_size       = s.ws_batch_bocado if channel == "Wholesale" else s.rt_batch_bocado
        margin           = s.ws_margin if channel == "Wholesale" else s.rt_margin_bocado
        labour_ref_prep  = bocado_prep_hours if bocado_prep_hours else ref_prep_hours
        labour_ref_oven  = bocado_oven_hours if bocado_oven_hours else ref_oven_hours
        labour_ref_batch = s.ws_batch_bocado
        scale              = boc_weight / ref_weight_g if ref_weight_g else 0
        size_labour_factor = 1.0

        st.info(
            f"Bocado: {boc_weight:.0f}g — "
            f"reference ≈ {ref_weight_g:.0f}g — "
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
        if not bocado_prep_hours:
            st.warning(
                "⚠️ No bocado labour times set — using large format "
                "times as fallback. Add bocado batch times in the "
                "recipe editor for accurate pricing."
            )

    st.divider()

    # ── Section 4: Packaging ──────────────────────────────────────────────────
    st.markdown("### 4 — Packaging")

    preset_names = ["— none —"] + [p["name"] for p in presets]
    selected_preset_name = st.selectbox(
        "Packaging preset", preset_names, key="calc_preset"
    )

    preset_lines   = []
    units_per_pack = 1

    if selected_preset_name != "— none —":
        preset_data = next(
            (p for p in presets if p["name"] == selected_preset_name), None
        )
        if preset_data:
            preset_lines   = db.get_preset_lines(preset_data["id"])
            units_per_pack = int(preset_data.get("units_per_pack") or 1)
            for line in preset_lines:
                cpu = line.get("consumable_cost_per_unit") or 0
                qty = float(line.get("quantity") or 1)
                st.caption(
                    f"  {line['consumable_name']} × {qty:.0f} "
                    f"— € {cpu * qty:.4f}"
                )
            if units_per_pack > 1:
                st.caption(f"  Shared across {units_per_pack} units per pack")
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

    # ── Section 5: Order quantity (secondary) ─────────────────────────────────
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
            ing_name  = line.get("ingredient_name", "")
            amount    = float(line.get("amount") or 0)
            ing       = ing_map.get(ing_name, {})
            cpu       = ing.get("cost_per_unit")  # always per gram/ml/unit
            pack_unit = (ing.get("pack_unit") or "g").lower()
        
            if cpu:
                effective_amount = amount
                # If ingredient is bought by weight (kg/g) but recipe amount
                # is in units, convert using known fruit weights.
                # Guard of < 20 ensures e.g. 150g of juice is not
                # misread as 150 lemons.
                if pack_unit in ("kg", "g"):
                    name_lower = ing_name.lower()
                    unit_weight = next(
                        (w for key, w in UNIT_TO_G.items()
                         if key in name_lower),
                        None
                    )
                    if unit_weight and amount < 20:
                        effective_amount = amount * unit_weight
                ingredient_cost += cpu * effective_amount * scale
            elif ing_name:
                missing_prices.append(ing_name)

        # ── Labour cost per unit ──────────────────────────────────────────────
        if labour_ref_batch > 0:
            qty_factor = (
                (batch_size / labour_ref_batch) ** s.labour_power
            ) / batch_size
        else:
            qty_factor = 1.0 / max(batch_size, 1)

        prep_per_unit = labour_ref_prep * qty_factor * size_labour_factor
        oven_per_unit = labour_ref_oven * qty_factor

        labour_cost = prep_per_unit * s.default_labour_rate
        oven_cost   = oven_per_unit * s.default_oven_rate

        # ── Packaging cost per unit ───────────────────────────────────────────
        packaging_cost = 0.0

        if preset_lines:
            for line in preset_lines:
                cpu = line.get("consumable_cost_per_unit") or 0
                qty = float(line.get("quantity") or 1)
                packaging_cost += (cpu * qty) / units_per_pack
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

        # ── Fetch current prices for comparison ──────────────────────────────
        cake_code_id = recipe.get("cake_code_id")
        cake_codes   = db.get_cake_codes()
        code_by_id   = {cc["id"]: cc["code"] for cc in cake_codes}
        code_str     = code_by_id.get(cake_code_id, "")


        relevant_codes = FORMAT_TIER_CODES.get(selected_format, [])
        live_prices    = db.get_current_prices(code_str) if code_str else []

        def find_price(chan):
            """Return best matching current price ex-VAT for channel.
            For WS, also checks MD as fallback since MD prices mirror WS."""
            channels = [chan]
            if chan == "WS":
                channels.append("MD")
            matches = [
                p for p in live_prices
                if p["channel"] in channels
                and any(f"-{fc}-" in p["sku_code"] for fc in relevant_codes)
            ]
            if not matches:
                return None, None
            ws_match = next((p for p in matches if p["channel"] == "WS"), None)
            best     = ws_match if ws_match else matches[0]
            return float(best["price_ex_vat"]), best["sku_code"]

        current_ws_ex, current_ws_sku = find_price("WS")
        current_gw_ex, current_gw_sku = find_price("GW")

        # ── Display results ───────────────────────────────────────────────────
        st.markdown("---")
        st.markdown(f"### {selected_name} — {selected_format} — {channel}")

        if missing_prices:
            st.warning(
                f"⚠️ Missing prices for: {', '.join(missing_prices)}. "
                "Ingredient cost is understated."
            )

        # Cost per unit — always shown
        st.metric("Cost per unit", f"€ {cost_per_unit:.2f}")

        st.divider()

        if channel == "Wholesale":
            # ── Wholesale view ────────────────────────────────────────────────
            c1, c2 = st.columns(2)
            with c1:
                st.metric(
                    "Suggested wholesale (ex-VAT)",
                    f"€ {price_per_unit:.2f}",
                    help=f"Cost × {s.ws_margin:.1f}× margin"
                )
            with c2:
                if current_ws_ex:
                    ws_margin_achieved = current_ws_ex / cost_per_unit \
                        if cost_per_unit > 0 else 0
                    st.metric(
                        f"Current price (ex-VAT) [{current_ws_sku}]",
                        f"€ {current_ws_ex:.2f}",
                        delta=f"{ws_margin_achieved:.2f}× cost",
                        delta_color="off"
                    )
                else:
                    st.metric("Current wholesale price", "—")

            if current_ws_ex and cost_per_unit > 0 \
                    and cost_per_unit > current_ws_ex:
                st.error(
                    f"⚠️ Calculated cost (€ {cost_per_unit:.2f}) exceeds "
                    f"current wholesale price (€ {current_ws_ex:.2f}). "
                    "Check ingredient prices and labour times."
                )

            if order_qty > 1:
                st.divider()
                st.markdown(f"**Total for {order_qty} unit(s)**")
                col_g, col_h = st.columns(2)
                col_g.metric("Total cost",
                             f"€ {cost_per_unit * order_qty:.2f}")
                col_h.metric("Total wholesale",
                             f"€ {price_per_unit * order_qty:.2f}")

        else:
            # ── Retail view ───────────────────────────────────────────────────
            rt_margin_used = (
                s.rt_margin_large if selected_format == "Standard"
                else s.rt_margin_individual if selected_format == "Individual"
                else s.rt_margin_bocado
            )
            rt_price_ex  = cost_per_unit * rt_margin_used
            rt_price_inc = rt_price_ex * VAT_MULTIPLIER

            c1, c2 = st.columns(2)
            with c1:
                st.metric(
                    "Suggested retail (ex-VAT)",
                    f"€ {rt_price_ex:.2f}",
                    help=f"Cost × {rt_margin_used:.1f}× margin"
                )
                st.metric(
                    "Suggested retail (inc-VAT 10%)",
                    f"€ {rt_price_inc:.2f}"
                )
            with c2:
                if current_gw_ex:
                    gw_margin_achieved = current_gw_ex / cost_per_unit \
                        if cost_per_unit > 0 else 0
                    st.metric(
                        f"Current price ex-VAT [{current_gw_sku}]",
                        f"€ {current_gw_ex:.2f}",
                        delta=f"{gw_margin_achieved:.2f}× cost",
                        delta_color="off"
                    )
                    st.metric(
                        "Current price inc-VAT",
                        f"€ {current_gw_ex * VAT_MULTIPLIER:.2f}"
                    )
                else:
                    st.metric("Current retail price", "—")

            if current_gw_ex and cost_per_unit > 0 \
                    and cost_per_unit > current_gw_ex:
                st.error(
                    f"⚠️ Calculated cost (€ {cost_per_unit:.2f}) exceeds "
                    f"current retail price (€ {current_gw_ex:.2f} ex-VAT). "
                    "Check ingredient prices and labour times."
                )

            if order_qty > 1:
                st.divider()
                st.markdown(f"**Total for {order_qty} unit(s)**")
                col_g, col_h = st.columns(2)
                col_g.metric("Total cost",
                             f"€ {cost_per_unit * order_qty:.2f}")
                col_h.metric("Total retail (inc-VAT)",
                             f"€ {rt_price_inc * order_qty:.2f}")

        # ── Cost breakdown ────────────────────────────────────────────────────
        st.divider()
        st.markdown("**Cost breakdown**")
        col_c, col_d, col_e, col_f = st.columns(4)
        col_c.metric("Ingredients", f"€ {ingredient_cost:.4f}")
        col_d.metric("Labour",      f"€ {labour_cost:.4f}")
        col_e.metric("Oven",        f"€ {oven_cost:.4f}")
        col_f.metric("Packaging",   f"€ {packaging_cost:.4f}")

        with st.expander("Labour calculation detail"):
            st.markdown(f"""
**Format:** {selected_format} · **Channel:** {channel}

**Labour reference:** {labour_ref_batch:.0f} units — 
{labour_ref_prep:.2f}h prep · {labour_ref_oven:.2f}h oven
(rates from settings: €{s.default_labour_rate:.2f}/hr labour · €{s.default_oven_rate:.2f}/hr oven)

**Pricing batch:** {batch_size} units

- qty_factor: ({batch_size} / {labour_ref_batch:.0f})^{s.labour_power} 
  / {batch_size} = **{qty_factor:.5f}**
- Size labour factor: **{size_labour_factor:.3f}**
- Prep per unit: {labour_ref_prep:.2f} × {qty_factor:.5f} × 
  {size_labour_factor:.3f} = **{prep_per_unit:.5f}h**
- Oven per unit: {labour_ref_oven:.2f} × {qty_factor:.5f} = 
  **{oven_per_unit:.5f}h**
- Labour: **€ {labour_cost:.4f}** · Oven: **€ {oven_cost:.4f}**
- Packaging: **€ {packaging_cost:.4f}** 
  (÷ {units_per_pack} units per pack)
- Margin: **{margin:.1f}×** ({channel})
- Ingredient scale: **{scale:.5f}×**
            """)

        st.caption(
            f"Labour: €{s.default_labour_rate:.2f}/hr · "
            f"Oven: €{s.default_oven_rate:.2f}/hr · "
            f"Scale: {scale:.4f}×"
        )
