# screen_repricing.py
# =============================================================================
# Repricing analysis — batch cost vs current price comparison for all recipes.
#
# For each recipe + active format, calculates:
#   - Ingredient cost at reference size
#   - Labour cost (wholesale batch assumptions from settings)
#   - Total cost
#   - Current WS price ex-VAT (from product_variants)
#   - Current RT price ex-VAT (from product_variants, inc-VAT / 1.10)
#   - Achieved WS and RT margins
#   - Gap to target margin
#   - Traffic light status
#
# Missing ingredient prices are flagged — costs are understated where data
# is incomplete. The report is downloadable as CSV.
# =============================================================================

import streamlit as st
import millington_db as db

# Fruit unit-to-gram conversion (same as calculator and analysis screens)
_UNIT_TO_G = {
    "limones":  100.0,
    "limas":     67.0,
    "naranja":  180.0,
    "manzanas": 182.0,
}

FORMAT_DISPLAY = {
    "standard":   "Estándar",
    "individual": "Individual",
    "bocado":     "Bocado",
}


def screen_repricing():
    st.title("Repricing analysis")
    st.caption(
        "Calculated cost vs current selling prices for all active recipes. "
        "Costs are at reference size using wholesale labour assumptions. "
        "Recipes with missing ingredient prices are flagged."
    )

    # ── Load everything once ──────────────────────────────────────────────────
    recipes     = db.get_recipes()
    settings    = db.get_settings()
    ingredients = db.get_ingredients()
    all_variants = db.get_all_variants_full()

    ing_map      = {i["name"]: i for i in ingredients}

    # Build variant lookup: {recipe_id: {format: variant}}
    var_lookup: dict[str, dict[str, dict]] = {}
    for v in all_variants:
        rid = v["recipe_id"]
        fmt = v["format"]
        var_lookup.setdefault(rid, {})[fmt] = v

    # Settings
    default_labour  = float(settings.get("default_labour_rate") or 30.0)
    default_oven    = float(settings.get("default_oven_rate")    or 2.0)
    labour_power    = float(settings.get("labour_power")         or 0.7)
    ws_margin       = float(settings.get("ws_margin")            or 2.0)
    rt_margin_large = float(settings.get("rt_margin_large")      or 3.0)
    rt_margin_ind   = float(settings.get("rt_margin_individual") or 3.0)
    rt_margin_boc   = float(settings.get("rt_margin_bocado")     or 3.0)
    ws_batch_large  = int(settings.get("ws_batch_large")         or 20)
    ws_batch_ind    = int(settings.get("ws_batch_individual")    or 100)
    ws_batch_boc    = int(settings.get("ws_batch_bocado")        or 250)

    # ── Filters ───────────────────────────────────────────────────────────────
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        filter_format = st.multiselect(
            "Format", ["standard", "individual", "bocado"],
            default=["standard", "individual", "bocado"],
            format_func=lambda x: FORMAT_DISPLAY[x]
        )
    with col_f2:
        filter_status = st.multiselect(
            "Status", ["🟢 On target", "🟡 Review", "🔴 Below cost", "⚪ No price"],
            default=["🟢 On target", "🟡 Review", "🔴 Below cost", "⚪ No price"]
        )
    with col_f3:
        show_incomplete = st.checkbox(
            "Show recipes with missing ingredient prices",
            value=True
        )

    st.divider()

    # ── Build rows ────────────────────────────────────────────────────────────
    rows = []
    recipe_lines_cache: dict[str, list] = {}

    for recipe in sorted(recipes, key=lambda r: r["name"]):
        rid     = recipe["id"]
        formats = _active_formats(recipe)

        # Fetch lines once per recipe
        if rid not in recipe_lines_cache:
            recipe_lines_cache[rid] = db.get_recipe_lines(rid)
        lines = recipe_lines_cache[rid]

        # Ingredient cost (same for all formats at reference size)
        ing_cost, missing = _calc_ingredient_cost(lines, ing_map)
        has_missing = len(missing) > 0

        if not show_incomplete and has_missing:
            continue

        for fmt in formats:
            if fmt not in filter_format:
                continue

            # Labour cost for this format
            labour_cost, oven_cost = _calc_labour_cost(
                recipe, fmt, settings,
                default_labour, default_oven, labour_power,
                ws_batch_large, ws_batch_ind, ws_batch_boc
            )
            total_cost = ing_cost + labour_cost + oven_cost

            # Target margin for this format
            target_margin = (
                ws_margin if fmt == "standard" else
                rt_margin_ind if fmt == "individual" else
                rt_margin_boc
            )

            # Current prices from product_variants
            variant = var_lookup.get(rid, {}).get(fmt, {})
            ws_price_ex = float(variant.get("ws_price_ex_vat") or 0) or None
            rt_price_inc = float(variant.get("rt_price_inc_vat") or 0) or None
            rt_price_ex  = rt_price_inc / 1.10 if rt_price_inc else None

            # Margin achieved
            ws_margin_achieved = (ws_price_ex / total_cost) if (ws_price_ex and total_cost > 0) else None
            rt_margin_achieved = (rt_price_ex / total_cost) if (rt_price_ex and total_cost > 0) else None

            # Suggested prices
            ws_suggested = total_cost * ws_margin
            rt_suggested_ex = total_cost * target_margin
            rt_suggested_inc = rt_suggested_ex * 1.10

            # Gap: current WS price vs suggested WS price
            ws_gap = (ws_price_ex - ws_suggested) if ws_price_ex else None

            # Traffic light based on WS margin (primary channel)
            if not ws_price_ex:
                status = "⚪ No price"
            elif total_cost <= 0:
                status = "⚪ No price"
            elif ws_price_ex < total_cost:
                status = "🔴 Below cost"
            elif ws_margin_achieved < ws_margin * 0.85:
                status = "🔴 Below cost"
            elif ws_margin_achieved < ws_margin:
                status = "🟡 Review"
            else:
                status = "🟢 On target"

            if status not in filter_status:
                continue

            rows.append({
                "Recipe":          recipe["name"],
                "Format":          FORMAT_DISPLAY.get(fmt, fmt),
                "Ing. cost":       ing_cost,
                "Labour":          labour_cost + oven_cost,
                "Total cost":      total_cost,
                "WS current":      ws_price_ex,
                "WS suggested":    ws_suggested,
                "WS gap":          ws_gap,
                "WS margin":       ws_margin_achieved,
                "RT current inc":  rt_price_inc,
                "RT suggested inc": rt_suggested_inc,
                "RT margin":       rt_margin_achieved,
                "Status":          status,
                "Missing prices":  ", ".join(missing) if missing else "",
                "_missing":        has_missing,
            })

    if not rows:
        st.info("No recipes match the current filters.")
        return

    st.caption(f"{len(rows)} recipe/format combinations")

    # ── Display table ─────────────────────────────────────────────────────────
    import pandas as pd

    df = pd.DataFrame(rows)
    display_df = df[[
        "Status", "Recipe", "Format",
        "Total cost", "WS current", "WS suggested", "WS gap", "WS margin",
        "RT current inc", "RT suggested inc", "RT margin",
        "Missing prices"
    ]].copy()

    # Format numeric columns
    for col in ["Total cost", "WS current", "WS suggested", "WS gap",
                "RT current inc", "RT suggested inc"]:
        display_df[col] = display_df[col].apply(
            lambda x: f"€ {x:.2f}" if x is not None and x != 0 else "—"
        )
    for col in ["WS margin", "RT margin"]:
        display_df[col] = display_df[col].apply(
            lambda x: f"{x:.2f}×" if x is not None else "—"
        )

    # Highlight rows
    def row_style(row):
        status = row["Status"]
        if "🔴" in status:
            return ["background-color: #fee2e2"] * len(row)
        elif "🟡" in status:
            return ["background-color: #fef9c3"] * len(row)
        elif "🟢" in status:
            return ["background-color: #dcfce7"] * len(row)
        return [""] * len(row)

    styled = display_df.style.apply(row_style, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── Summary metrics ───────────────────────────────────────────────────────
    st.divider()
    st.markdown("### Summary")

    n_green  = sum(1 for r in rows if r["Status"] == "🟢 On target")
    n_amber  = sum(1 for r in rows if r["Status"] == "🟡 Review")
    n_red    = sum(1 for r in rows if r["Status"] == "🔴 Below cost")
    n_nodata = sum(1 for r in rows if r["Status"] == "⚪ No price")
    n_miss   = sum(1 for r in rows if r["_missing"])

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("🟢 On target",  n_green)
    m2.metric("🟡 Review",     n_amber)
    m3.metric("🔴 Below cost", n_red)
    m4.metric("⚪ No price",   n_nodata)
    m5.metric("⚠️ Incomplete costs", n_miss,
              help="Recipes with at least one missing ingredient price")

    if n_red > 0:
        st.error(
            f"⚠️ {n_red} product/format combination(s) are priced below calculated cost. "
            "Check ingredient prices and labour times are complete before acting on this."
        )

    # ── Download ──────────────────────────────────────────────────────────────
    st.divider()
    csv = df.drop(columns=["_missing"]).to_csv(index=False)
    st.download_button(
        "⬇️ Download as CSV",
        data=csv,
        file_name="millington_repricing_analysis.csv",
        mime="text/csv"
    )

    st.caption(
        "Note: costs are at reference size with wholesale labour assumptions. "
        "Packaging not included — add a preset in the calculator for full cost. "
        "Recipes with missing ingredient prices will show understated costs."
    )


# =============================================================================
# Calculation helpers
# =============================================================================

def _active_formats(recipe: dict) -> list[str]:
    formats = ["standard"]
    if recipe.get("has_individual"):
        formats.append("individual")
    if recipe.get("has_bocado"):
        formats.append("bocado")
    return formats


def _calc_ingredient_cost(
    lines: list[dict],
    ing_map: dict
) -> tuple[float, list[str]]:
    """Returns (total_ingredient_cost, list_of_missing_ingredient_names)."""
    total   = 0.0
    missing = []

    for line in lines:
        ing_name  = line.get("ingredient_name", "")
        amount    = float(line.get("amount") or 0)
        ing       = ing_map.get(ing_name, {})
        cpu       = ing.get("cost_per_unit")
        pack_unit = (ing.get("pack_unit") or "g").lower()

        if cpu:
            eff = amount
            if pack_unit in ("kg", "g"):
                name_lower  = ing_name.lower()
                unit_weight = next(
                    (w for key, w in _UNIT_TO_G.items()
                     if key in name_lower), None
                )
                if unit_weight and amount < 20:
                    eff = amount * unit_weight
            total += cpu * eff
        elif ing_name:
            missing.append(ing_name)

    return total, missing


def _calc_labour_cost(
    recipe: dict,
    fmt: str,
    settings: dict,
    labour_rate: float,
    oven_rate: float,
    power: float,
    ws_batch_large: int,
    ws_batch_ind: int,
    ws_batch_boc: int,
) -> tuple[float, float]:
    """
    Returns (labour_cost, oven_cost) per unit at wholesale batch size.
    Uses recipe-specific batch times where available, falls back to defaults.
    """
    if fmt == "standard":
        ref_batch = float(recipe.get("ref_batch_size") or 20)
        prep_hrs  = float(recipe.get("ref_prep_hours") or 1.0)
        oven_hrs  = float(recipe.get("ref_oven_hours") or 1.0)
        batch     = ws_batch_large

    elif fmt == "individual":
        ref_batch = float(ws_batch_ind)
        prep_hrs  = float(
            recipe.get("small_batch_prep_hours") or
            recipe.get("ref_prep_hours") or 1.0
        )
        oven_hrs  = float(
            recipe.get("small_batch_oven_hours") or
            recipe.get("ref_oven_hours") or 1.0
        )
        batch = ws_batch_ind

    else:  # bocado
        ref_batch = float(ws_batch_boc)
        prep_hrs  = float(
            recipe.get("bocado_batch_prep_hours") or
            recipe.get("ref_prep_hours") or 1.0
        )
        oven_hrs  = float(
            recipe.get("bocado_batch_oven_hours") or
            recipe.get("ref_oven_hours") or 1.0
        )
        batch = ws_batch_boc

    if ref_batch <= 0:
        return 0.0, 0.0

    qty_factor    = ((batch / ref_batch) ** power) / batch
    labour_cost   = prep_hrs * qty_factor * labour_rate
    oven_cost     = oven_hrs * qty_factor * oven_rate
    return labour_cost, oven_cost
