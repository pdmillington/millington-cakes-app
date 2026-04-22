# screen_repricing.py
# =============================================================================
# Repricing analysis — batch cost vs current price for all active recipes.
#
# Cost calculation mirrors the calculator screen exactly:
#   - Individual/bocado ingredient costs scaled by portion weight / recipe weight
#   - WS cost uses wholesale batch assumptions (20/100/250)
#   - RT cost uses retail batch assumptions (1/4/25)
#   - WS margin compares WS price to WS cost
#   - RT margin compares RT price (ex-VAT) to RT cost
#
# Downloadable as CSV.
# =============================================================================

import streamlit as st
import millington_db as db

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
        "Calculated cost vs current prices for all active recipes. "
        "WS cost uses wholesale batch assumptions; RT cost uses retail batch assumptions. "
        "Recipes with missing ingredient prices show understated costs."
    )

    # ── Load data ─────────────────────────────────────────────────────────────
    recipes      = db.get_recipes()
    settings     = db.get_settings()
    ingredients  = db.get_ingredients()
    all_variants = db.get_all_variants_full()

    ing_map    = {i["name"]: i for i in ingredients}
    var_lookup: dict[str, dict[str, dict]] = {}
    for v in all_variants:
        var_lookup.setdefault(v["recipe_id"], {})[v["format"]] = v

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
    rt_batch_large  = int(settings.get("rt_batch_large")         or 1)
    rt_batch_ind    = int(settings.get("rt_batch_individual")    or 4)
    rt_batch_boc    = int(settings.get("rt_batch_bocado")        or 25)
    ind_weight_g    = float(settings.get("individual_weight_g")  or 100)
    boc_weight_g    = float(settings.get("bocado_weight_g")      or 30)

    # ── Filters ───────────────────────────────────────────────────────────────
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        filter_format = st.multiselect(
            "Format",
            ["standard", "individual", "bocado"],
            default=["standard", "individual", "bocado"],
            format_func=lambda x: FORMAT_DISPLAY[x]
        )
    with col_f2:
        filter_status = st.multiselect(
            "WS Status",
            ["🟢 On target", "🟡 Review (low)", "🟡 Review (high)",
             "🔴 Below cost", "⚪ No price"],
            default=["🟢 On target", "🟡 Review (low)", "🟡 Review (high)",
                     "🔴 Below cost", "⚪ No price"]
        )
    with col_f3:
        show_incomplete = st.checkbox(
            "Include recipes with missing ingredient prices",
            value=True
        )

    st.divider()

    # ── Build rows ────────────────────────────────────────────────────────────
    rows        = []
    lines_cache: dict[str, list] = {}
    weight_cache: dict[str, float] = {}

    for recipe in sorted(recipes, key=lambda r: r["name"]):
        rid     = recipe["id"]
        formats = _active_formats(recipe)

        # Load lines once per recipe
        if rid not in lines_cache:
            lines_cache[rid] = db.get_recipe_lines(rid)

        lines = lines_cache[rid]

        # Estimate recipe weight once (for individual/bocado scaling)
        if rid not in weight_cache:
            result = db.estimate_recipe_weight(lines)
            weight_cache[rid] = float(result.get("weight_g") or 0)

        ref_weight_g = weight_cache[rid]

        # Full recipe ingredient cost (unscaled)
        full_ing_cost, missing = _calc_ingredient_cost(lines, ing_map)
        has_missing = len(missing) > 0

        if not show_incomplete and has_missing:
            continue

        for fmt in formats:
            if fmt not in filter_format:
                continue

            # Ingredient scale for this format
            if fmt == "individual":
                iw    = float(recipe.get("individual_weight_g") or ind_weight_g)
                scale = iw / ref_weight_g if ref_weight_g > 0 else 0
            elif fmt == "bocado":
                bw    = float(recipe.get("bocado_weight_g") or boc_weight_g)
                scale = bw / ref_weight_g if ref_weight_g > 0 else 0
            else:
                scale = 1.0

            ing_cost = full_ing_cost * scale

            # WS labour cost
            ws_labour, ws_oven = _calc_labour(
                recipe, fmt, default_labour, default_oven, labour_power,
                ws_batch_large, ws_batch_ind, ws_batch_boc,
                ref_batch="ws"
            )
            ws_cost = ing_cost + ws_labour + ws_oven

            # RT labour cost (different batch sizes)
            rt_labour, rt_oven = _calc_labour(
                recipe, fmt, default_labour, default_oven, labour_power,
                rt_batch_large, rt_batch_ind, rt_batch_boc,
                ref_batch="rt"
            )
            rt_cost = ing_cost + rt_labour + rt_oven

            # Target margins
            target_ws = ws_margin
            target_rt = (
                rt_margin_large if fmt == "standard"
                else rt_margin_ind if fmt == "individual"
                else rt_margin_boc
            )

            # Current prices from product_variants
            variant     = var_lookup.get(rid, {}).get(fmt, {})
            ws_price_ex = _f(variant.get("ws_price_ex_vat"))
            rt_price_inc = _f(variant.get("rt_price_inc_vat"))
            rt_price_ex  = rt_price_inc / 1.10 if rt_price_inc else None

            # Achieved margins
            ws_margin_ach = (ws_price_ex / ws_cost) if (ws_price_ex and ws_cost > 0) else None
            rt_margin_ach = (rt_price_ex / rt_cost) if (rt_price_ex and rt_cost > 0) else None

            # Suggested prices
            ws_suggested     = ws_cost * target_ws
            rt_suggested_ex  = rt_cost * target_rt
            rt_suggested_inc = rt_suggested_ex * 1.10

            # WS gap
            ws_gap = (ws_price_ex - ws_suggested) if ws_price_ex else None

            # Traffic light — symmetric ±5% band around target WS margin
            if not ws_price_ex or ws_cost <= 0:
                status = "⚪ No price"
            elif ws_price_ex < ws_cost:
                status = "🔴 Below cost"
            elif ws_margin_ach < target_ws * 0.95:
                status = "🟡 Review (low)"
            elif ws_margin_ach > target_ws * 1.05:
                status = "🟡 Review (high)"
            else:
                status = "🟢 On target"

            if status not in filter_status:
                continue

            rows.append({
                "Recipe":          recipe["name"],
                "Format":          FORMAT_DISPLAY.get(fmt, fmt),
                "Scale":           f"{scale:.3f}×" if fmt != "standard" else "—",
                "Ing. cost":       ing_cost,
                "WS labour":       ws_labour + ws_oven,
                "WS total cost":   ws_cost,
                "WS price":        ws_price_ex,
                "WS suggested":    ws_suggested,
                "WS gap":          ws_gap,
                "WS margin":       ws_margin_ach,
                "RT labour":       rt_labour + rt_oven,
                "RT total cost":   rt_cost,
                "RT price (inc)":  rt_price_inc,
                "RT suggested (inc)": rt_suggested_inc,
                "RT margin":       rt_margin_ach,
                "Status":          status,
                "⚠️ Missing":       ", ".join(missing) if missing else "",
                "_missing":        has_missing,
            })

    if not rows:
        st.info("No recipes match the current filters.")
        return

    st.caption(f"{len(rows)} recipe/format combinations")

    # ── Display ───────────────────────────────────────────────────────────────
    import pandas as pd

    df         = pd.DataFrame(rows)
    display_df = df[[
        "Status", "Recipe", "Format", "Scale",
        "Ing. cost", "WS total cost", "WS price",
        "WS suggested", "WS gap", "WS margin",
        "RT total cost", "RT price (inc)", "RT suggested (inc)", "RT margin",
        "⚠️ Missing",
    ]].copy()

    # Format numeric columns
    for col in ["Ing. cost", "WS total cost", "WS price",
                "WS suggested", "WS gap",
                "RT total cost", "RT price (inc)", "RT suggested (inc)"]:
        display_df[col] = display_df[col].apply(
            lambda x: f"€ {x:.2f}" if x is not None else "—"
        )
    for col in ["WS margin", "RT margin"]:
        display_df[col] = display_df[col].apply(
            lambda x: f"{x:.2f}×" if x is not None else "—"
        )

    def row_style(row):
        s = row["Status"]
        if "🔴" in s:
            return ["background-color: #fee2e2"] * len(row)
        elif "🟡" in s:
            return ["background-color: #fef9c3"] * len(row)
        elif "🟢" in s:
            return ["background-color: #dcfce7"] * len(row)
        return [""] * len(row)

    st.dataframe(
        display_df.style.apply(row_style, axis=1),
        width='stretch',
        hide_index=True
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("### Summary")

    n_green   = sum(1 for r in rows if r["Status"] == "🟢 On target")
    n_low     = sum(1 for r in rows if r["Status"] == "🟡 Review (low)")
    n_high    = sum(1 for r in rows if r["Status"] == "🟡 Review (high)")
    n_red     = sum(1 for r in rows if r["Status"] == "🔴 Below cost")
    n_nodata  = sum(1 for r in rows if r["Status"] == "⚪ No price")
    n_miss    = sum(1 for r in rows if r["_missing"])

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("🟢 On target",        n_green)
    m2.metric("🟡 Review (low)",     n_low)
    m3.metric("🟡 Review (high)",    n_high)
    m4.metric("🔴 Below cost",       n_red)
    m5.metric("⚪ No price",         n_nodata)
    m6.metric("⚠️ Incomplete costs", n_miss)

    if n_red > 0:
        st.error(
            f"{n_red} combination(s) priced below calculated cost. "
            "Verify ingredient prices and labour times are complete before acting."
        )

    # ── Download ──────────────────────────────────────────────────────────────
    st.divider()
    csv = df.drop(columns=["_missing"]).to_csv(index=False)
    st.download_button(
        "⬇️ Download as CSV",
        data=csv,
        file_name="millington_repricing.csv",
        mime="text/csv"
    )
    st.caption(
        "WS cost: wholesale batch assumptions · "
        "RT cost: retail batch assumptions · "
        "Packaging excluded from both."
    )


# =============================================================================
# Helpers
# =============================================================================

def _f(val) -> float | None:
    """Safely convert to float, returning None for zero/null."""
    try:
        v = float(val)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _active_formats(recipe: dict) -> list[str]:
    formats = ["standard"]
    if recipe.get("has_individual"):
        formats.append("individual")
    if recipe.get("has_bocado"):
        formats.append("bocado")
    return formats


def _calc_ingredient_cost(
    lines: list[dict],
    ing_map: dict,
) -> tuple[float, list[str]]:
    """Full recipe ingredient cost (unscaled). Returns (cost, missing_names)."""
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
                uw = next(
                    (w for k, w in _UNIT_TO_G.items()
                     if k in ing_name.lower()), None
                )
                if uw and amount < 20:
                    eff = amount * uw
            total += cpu * eff
        elif ing_name:
            missing.append(ing_name)
    return total, missing


def _calc_labour(
    recipe: dict,
    fmt: str,
    labour_rate: float,
    oven_rate: float,
    power: float,
    batch_large: int,
    batch_ind: int,
    batch_boc: int,
    ref_batch: str = "ws",  # "ws" or "rt" — determines which ref batch to use
) -> tuple[float, float]:
    """
    Returns (labour_cost, oven_cost) per unit.

    ref_batch="ws" uses the recipe's large/small reference batch sizes.
    ref_batch="rt" still uses the same recipe reference times but the
    pricing batch changes to reflect retail quantities.
    The recipe's stored reference times (how long a production run takes)
    are always from the wholesale reference — we scale the per-unit cost
    by the ratio of the retail batch to that reference.
    """
    if fmt == "standard":
        ref_b    = float(recipe.get("ref_batch_size") or 20)
        prep_hrs = float(recipe.get("ref_prep_hours") or 1.0)
        oven_hrs = float(recipe.get("ref_oven_hours") or 1.0)
        batch    = batch_large

    elif fmt == "individual":
        ref_b    = float(batch_ind)   # reference is always the WS ind batch (100)
        prep_hrs = float(
            recipe.get("small_batch_prep_hours") or
            recipe.get("ref_prep_hours") or 1.0
        )
        oven_hrs = float(
            recipe.get("small_batch_oven_hours") or
            recipe.get("ref_oven_hours") or 1.0
        )
        batch = batch_ind

    else:  # bocado
        ref_b    = float(batch_boc)   # reference is always the WS boc batch (250)
        prep_hrs = float(
            recipe.get("bocado_batch_prep_hours") or
            recipe.get("ref_prep_hours") or 1.0
        )
        oven_hrs = float(
            recipe.get("bocado_batch_oven_hours") or
            recipe.get("ref_oven_hours") or 1.0
        )
        batch = batch_boc

    if ref_b <= 0:
        return 0.0, 0.0

    qty_factor  = ((batch / ref_b) ** power) / batch
    labour_cost = prep_hrs * qty_factor * labour_rate
    oven_cost   = oven_hrs * qty_factor * oven_rate
    return labour_cost, oven_cost
