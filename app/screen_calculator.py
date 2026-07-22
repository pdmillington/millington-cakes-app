
import streamlit as st
from math import pi
import millington_db as db
from core.constants import FORMAT_TIER_CODES, VAT_MULTIPLIER
from core.settings import load_settings
from core.pricing_engine import (
    calc_ingredient_cost, calc_labour_cost,
    AnchorPoint, InterpolatedCostResult, calc_interpolated_cost,
)
from ui.components import missing_prices_warning, cost_breakdown_metrics, weight_estimate_expander


def _pick_variant_weight(variants: list, default_weight: float, widget_key: str) -> float:
    """
    Resolve the reference weight to cost against for an individual/bocado
    format. If the recipe has just one (or zero) variant of that format,
    behaviour is unchanged — the recipe-level default weight is used unless
    that single variant carries its own ref_weight_g override. If there are
    several (e.g. a standard individual plus a heavier client-specific one),
    show a selector so the user picks which variant they're costing.
    """
    with_weight = [v for v in variants if v.get("ref_weight_g")]
    if len(with_weight) <= 1:
        only = with_weight[0] if with_weight else None
        return float(only["ref_weight_g"]) if only else default_weight

    def _label(v):
        w    = float(v["ref_weight_g"])
        code = v.get("sku_ws") or v.get("sku_gw") or "sin SKU"
        return f"{w:.0f}g — {code}"

    labels = [_label(v) for v in with_weight]
    choice = st.selectbox(
        "Variant (this recipe has more than one weight for this format)",
        labels, key=widget_key
    )
    return float(with_weight[labels.index(choice)]["ref_weight_g"])


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

    # A recipe can have more than one individual/bocado variant with its own
    # weight (e.g. a standard individual plus a heavier client-specific one)
    # — each variant's own ref_weight_g (set in Variantes) takes priority over
    # the recipe-level default below when there's more than one to choose from.
    try:
        recipe_variants_by_fmt: dict[str, list] = {}
        for v in db.get_variants_for_recipe(recipe["id"]):
            recipe_variants_by_fmt.setdefault(v.get("format"), []).append(v)
    except Exception:
        recipe_variants_by_fmt = {}

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

        # ── Sizing mode toggle ────────────────────────────────────────────────
        # Custom weight mode is only available when the recipe has at least one
        # small format (individual or bocado) to use as an interpolation anchor.
        if has_individual or has_bocado:
            size_mode = st.radio(
                "Sizing mode",
                ["By dimensions", "By weight — custom (interpolated)"],
                horizontal=True, key="calc_size_mode",
            )
        else:
            size_mode = "By dimensions"

        using_interpolation  = size_mode != "By dimensions"
        target_weight_interp = 0.0
        scale                = 1.0      # default; overwritten in non-interp path
        size_labour_factor   = 1.0

        if not using_interpolation:
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

        else:
            # Custom weight — interpolate between bocado / individual / standard
            _std_ref_g = ref_weight_kg * 1000 if ref_weight_kg > 0 else ref_weight_g
            _default_w = max(ind_weight * 1.5, 100.0) if ind_weight else 150.0
            target_weight_interp = st.number_input(
                "Target weight (g)",
                min_value=1.0,
                value=float(min(_default_w, _std_ref_g * 0.4) if _std_ref_g > 0 else _default_w),
                step=10.0,
                key="calc_interp_weight",
            )
            _anchor_labels = []
            if has_bocado:
                _anchor_labels.append(f"bocado ({boc_weight:.0f}g)")
            if has_individual:
                _anchor_labels.append(f"individual ({ind_weight:.0f}g)")
            _anchor_labels.append(
                f"standard ({_std_ref_g:.0f}g)" if _std_ref_g > 0 else "standard (ref)"
            )
            st.info(
                f"Interpolating between: {' → '.join(_anchor_labels)}. "
                "Ingredients scale linearly; labour scales by power law."
            )

    elif selected_format == "Individual":
        using_interpolation  = False
        target_weight_interp = 0.0
        batch_size       = s.ws_batch_individual if channel == "Wholesale" else s.rt_batch_individual
        margin           = s.ws_margin if channel == "Wholesale" else s.rt_margin_individual
        labour_ref_prep  = small_prep_hours if small_prep_hours else ref_prep_hours
        labour_ref_oven  = small_oven_hours if small_oven_hours else ref_oven_hours
        labour_ref_batch = s.ws_batch_individual
        ind_weight = _pick_variant_weight(
            recipe_variants_by_fmt.get("individual", []), ind_weight, "calc_ind_variant"
        )
        scale              = ind_weight / ref_weight_g if ref_weight_g else 0
        size_labour_factor = 1.0

        st.info(
            f"Individual: {ind_weight:.0f}g — "
            f"reference ≈ {ref_weight_g:.0f}g — "
            f"scale: **{scale:.4f}×**"
        )
        
        weight_estimate_expander(ref_weight_g, _weight_notes, _weight_excl)
        
        if not small_prep_hours:
            st.warning(
                "⚠️ No individual labour times set — using large format "
                "times as fallback. Add individual batch times in the "
                "recipe editor for accurate pricing."
            )

    else:  # Bocado
        using_interpolation  = False
        target_weight_interp = 0.0
        batch_size       = s.ws_batch_bocado if channel == "Wholesale" else s.rt_batch_bocado
        margin           = s.ws_margin if channel == "Wholesale" else s.rt_margin_bocado
        labour_ref_prep  = bocado_prep_hours if bocado_prep_hours else ref_prep_hours
        labour_ref_oven  = bocado_oven_hours if bocado_oven_hours else ref_oven_hours
        labour_ref_batch = s.ws_batch_bocado
        boc_weight = _pick_variant_weight(
            recipe_variants_by_fmt.get("bocado", []), boc_weight, "calc_boc_variant"
        )
        scale              = boc_weight / ref_weight_g if ref_weight_g else 0
        size_labour_factor = 1.0

        st.info(
            f"Bocado: {boc_weight:.0f}g — "
            f"reference ≈ {ref_weight_g:.0f}g — "
            f"scale: **{scale:.4f}×**"
        )
        
        weight_estimate_expander(ref_weight_g, _weight_notes, _weight_excl)
        
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

    st.divider()

    # ── Calculate ─────────────────────────────────────────────────────────────
    if st.button("Calculate", type="primary", use_container_width=True):

        lines = db.get_recipe_lines(recipe["id"])

        # Component (sub-recipe) cost — shared with screen_analysis.py and
        # screen_repricing.py. Without this, recipes built from a component
        # (e.g. a sponge or cream base) silently dropped that cost.
        component_map, component_labour_cost = db.build_component_context(
            lines, s.default_labour_rate
        )
        ing_result = calc_ingredient_cost(lines, ing_map, component_map=component_map)
        missing_prices = ing_result.missing_prices

        # ── Packaging cost (shared by both paths) ─────────────────────────────
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

        # ── Cost computation ──────────────────────────────────────────────────
        interp_result        = None
        anchors              = []
        ingredient_cost_per_g = 0.0
        qty_factor    = 0.0
        prep_per_unit = 0.0
        oven_per_unit = 0.0

        if using_interpolation:
            # --- Interpolated path: power-law between anchor formats ----------
            std_ref_weight_g = ref_weight_kg * 1000 if ref_weight_kg > 0 else ref_weight_g
            if std_ref_weight_g <= 0:
                st.error(
                    "Cannot interpolate: reference weight is 0. "
                    "Set ref_weight_kg in the recipe editor."
                )
                return
            # Component labour scales linearly with weight (same as
            # ingredients — it's driven by the same recipe-line amount), so
            # for interpolation purposes it's folded into the per-gram rate
            # rather than power-law scaled like prep/oven labour below.
            ingredient_cost_per_g = (ing_result.total + component_labour_cost) / std_ref_weight_g

            all_variants    = db.get_all_variants_full()
            recipe_variants = [v for v in all_variants if v.get("recipe_id") == recipe["id"]]

            def _approved(fmt):
                key = "ws_price_approved" if channel == "Wholesale" else "rt_price_approved"
                for v in recipe_variants:
                    if (v.get("format") or "").lower() == fmt.lower():
                        p = v.get(key)
                        if p:
                            return float(p)
                return None

            if has_bocado:
                r = calc_labour_cost(
                    s.ws_batch_bocado if channel == "Wholesale" else s.rt_batch_bocado,
                    s.ws_batch_bocado,
                    bocado_prep_hours or ref_prep_hours,
                    bocado_oven_hours or ref_oven_hours,
                    s,
                )
                anchors.append(AnchorPoint(
                    label="bocado", weight_g=boc_weight,
                    labour_cost=r.labour_cost, oven_cost=r.oven_cost,
                    ingredient_cost=ingredient_cost_per_g * boc_weight,
                    approved_price=_approved("bocado"),
                ))

            if has_individual:
                r = calc_labour_cost(
                    s.ws_batch_individual if channel == "Wholesale" else s.rt_batch_individual,
                    s.ws_batch_individual,
                    small_prep_hours or ref_prep_hours,
                    small_oven_hours or ref_oven_hours,
                    s,
                )
                anchors.append(AnchorPoint(
                    label="individual", weight_g=ind_weight,
                    labour_cost=r.labour_cost, oven_cost=r.oven_cost,
                    ingredient_cost=ingredient_cost_per_g * ind_weight,
                    approved_price=_approved("individual"),
                ))

            r = calc_labour_cost(batch_size, labour_ref_batch, labour_ref_prep, labour_ref_oven, s)
            anchors.append(AnchorPoint(
                label="standard", weight_g=std_ref_weight_g,
                labour_cost=r.labour_cost, oven_cost=r.oven_cost,
                ingredient_cost=ing_result.total + component_labour_cost,
                approved_price=_approved("standard"),
            ))

            interp_result   = calc_interpolated_cost(target_weight_interp, anchors, ingredient_cost_per_g)
            ingredient_cost = interp_result.ingredient_cost
            labour_cost     = interp_result.labour_cost
            oven_cost       = interp_result.oven_cost

        else:
            # --- Normal path: direct scale from format -----------------------
            labour          = calc_labour_cost(
                batch_size, labour_ref_batch, labour_ref_prep, labour_ref_oven, s,
                size_labour_factor=size_labour_factor,
            )
            ingredient_cost = ing_result.total * scale
            # Component labour scales the same way ingredient cost does (same
            # underlying line amount) and is tracked as labour, matching the
            # analysis and repricing screens.
            labour_cost     = labour.labour_cost + component_labour_cost * scale
            oven_cost       = labour.oven_cost
            qty_factor      = labour.qty_factor
            prep_per_unit   = labour.prep_per_unit
            oven_per_unit   = labour.oven_per_unit

        # ── Totals ────────────────────────────────────────────────────────────
        cost_per_unit = ingredient_cost + labour_cost + oven_cost + packaging_cost

        if interp_result and interp_result.implied_margin is not None:
            price_per_unit = cost_per_unit * interp_result.implied_margin
        else:
            price_per_unit = cost_per_unit * margin

        # ── Live price lookup (not applicable for custom weight sizes) ────────
        if not using_interpolation:
            cake_code_id   = recipe.get("cake_code_id")
            cake_codes     = db.get_cake_codes()
            code_by_id     = {cc["id"]: cc["code"] for cc in cake_codes}
            code_str       = code_by_id.get(cake_code_id, "")
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
        else:
            current_ws_ex = current_ws_sku = None
            current_gw_ex = current_gw_sku = None

        # ── Display results ───────────────────────────────────────────────────
        st.markdown("---")
        if using_interpolation:
            st.markdown(
                f"### {selected_name} — {target_weight_interp:.0f}g (custom) — {channel}"
            )
        else:
            st.markdown(f"### {selected_name} — {selected_format} — {channel}")

        missing_prices_warning(missing_prices)

        # Interpolation margin info
        if interp_result:
            for w in interp_result.warnings:
                st.warning(w)
            if interp_result.implied_margin is not None:
                st.info(
                    f"Margin: {interp_result.margin_source} → "
                    f"**{interp_result.implied_margin:.2f}×**"
                )
            else:
                st.warning(
                    f"Margin unavailable ({interp_result.margin_source}). "
                    f"Falling back to settings margin {margin:.1f}×."
                )

        st.metric("Cost per unit", f"€ {cost_per_unit:.2f}")
        st.divider()

        if channel == "Wholesale":
            # ── Wholesale view ────────────────────────────────────────────────
            c1, c2 = st.columns(2)
            with c1:
                _margin_label = (
                    f"{interp_result.implied_margin:.2f}× (interpolated)"
                    if interp_result and interp_result.implied_margin is not None
                    else f"{margin:.1f}× (settings)"
                )
                st.metric(
                    "Suggested wholesale (ex-VAT)",
                    f"€ {price_per_unit:.2f}",
                    help=f"Cost × {_margin_label}",
                )
            with c2:
                if current_ws_ex:
                    ws_margin_achieved = current_ws_ex / cost_per_unit \
                        if cost_per_unit > 0 else 0
                    st.metric(
                        f"Current price (ex-VAT) [{current_ws_sku}]",
                        f"€ {current_ws_ex:.2f}",
                        delta=f"{ws_margin_achieved:.2f}× cost",
                        delta_color="off",
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
                col_g.metric("Total cost",      f"€ {cost_per_unit * order_qty:.2f}")
                col_h.metric("Total wholesale", f"€ {price_per_unit * order_qty:.2f}")

        else:
            # ── Retail view ───────────────────────────────────────────────────
            if interp_result and interp_result.implied_margin is not None:
                rt_margin_used = interp_result.implied_margin
            else:
                rt_margin_used = (
                    s.rt_margin_large      if selected_format == "Standard"
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
                    help=f"Cost × {rt_margin_used:.2f}× margin",
                )
                st.metric("Suggested retail (inc-VAT 10%)", f"€ {rt_price_inc:.2f}")
            with c2:
                if current_gw_ex:
                    gw_margin_achieved = current_gw_ex / cost_per_unit \
                        if cost_per_unit > 0 else 0
                    st.metric(
                        f"Current price ex-VAT [{current_gw_sku}]",
                        f"€ {current_gw_ex:.2f}",
                        delta=f"{gw_margin_achieved:.2f}× cost",
                        delta_color="off",
                    )
                    st.metric(
                        "Current price inc-VAT",
                        f"€ {current_gw_ex * VAT_MULTIPLIER:.2f}",
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
                col_g.metric("Total cost",           f"€ {cost_per_unit * order_qty:.2f}")
                col_h.metric("Total retail (inc-VAT)", f"€ {rt_price_inc * order_qty:.2f}")

        # ── Cost breakdown ────────────────────────────────────────────────────
        st.divider()
        cost_breakdown_metrics(ingredient_cost, labour_cost, oven_cost, packaging_cost)

        if interp_result:
            with st.expander("Interpolation detail"):
                lo = interp_result.lower_anchor
                hi = interp_result.upper_anchor
                lo_total = lo.ingredient_cost + lo.labour_cost + lo.oven_cost
                hi_total = hi.ingredient_cost + hi.labour_cost + hi.oven_cost
                st.markdown(f"""
**Target weight:** {target_weight_interp:.0f}g — bracketed by
**{lo.label}** ({lo.weight_g:.0f}g) and **{hi.label}** ({hi.weight_g:.0f}g)

**Ingredient cost per gram:** €{ingredient_cost_per_g:.6f}
(= recipe ingredient cost €{ing_result.total:.4f} ÷ {std_ref_weight_g:.0f}g ref weight)

**Anchor labour costs per unit:**
- {lo.label} ({lo.weight_g:.0f}g): labour €{lo.labour_cost:.4f} · oven €{lo.oven_cost:.4f}
- {hi.label} ({hi.weight_g:.0f}g): labour €{hi.labour_cost:.4f} · oven €{hi.oven_cost:.4f}

**Interpolated at {target_weight_interp:.0f}g:**
- Ingredients: €{interp_result.ingredient_cost:.4f} (linear)
- Labour: €{interp_result.labour_cost:.4f} (power-law)
- Oven: €{interp_result.oven_cost:.4f} (power-law)

**Margin:** {interp_result.margin_source}
                """)
                st.markdown("**All anchors:**")
                for a in sorted(anchors, key=lambda x: x.weight_g):
                    a_total    = a.ingredient_cost + a.labour_cost + a.oven_cost
                    price_str  = f"€{a.approved_price:.2f}" if a.approved_price else "—"
                    implied_m  = (
                        f"{a.approved_price / a_total:.2f}×"
                        if a.approved_price and a_total > 0 else "—"
                    )
                    st.caption(
                        f"**{a.label}** {a.weight_g:.0f}g  |  "
                        f"cost €{a_total:.4f}  |  "
                        f"approved {price_str}  |  "
                        f"implied margin {implied_m}"
                    )
        else:
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
            + (
                f"Weight: {target_weight_interp:.0f}g (interpolated)"
                if using_interpolation
                else f"Scale: {scale:.4f}×"
            )
        )
