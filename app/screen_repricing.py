# screen_repricing.py
# =============================================================================
# Repricing analysis + inline price editor.
#
# The table shows calculated cost vs current prices for all active recipes.
# An expandable section below allows editing WS and RT prices directly,
# saving back to product_variants without refreshing the table mid-edit.
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
        "RT margins shown ex-VAT (price ÷ 1.10 ÷ cost)."
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

    # ── Build rows ─────────────────────────────────────────────────────────────
    # Iterate over ALL variants directly — handles multiple standard sizes
    # per recipe (e.g. Lemon Pie 22/26/28cm) without dict key collisions.
    rows        = []
    lines_cache: dict[str, list]  = {}
    weight_cache: dict[str, float] = {}
    recipe_by_id = {r["id"]: r for r in recipes}

    # Sort variants by recipe name then format then diameter
    sorted_variants = sorted(
        all_variants,
        key=lambda v: (
            recipe_by_id.get(v["recipe_id"], {}).get("name", ""),
            v["format"],
            float(v.get("ref_diameter_cm") or 0),
        )
    )

    for variant in sorted_variants:
        rid    = variant["recipe_id"]
        fmt    = variant["format"]
        recipe = recipe_by_id.get(rid)
        if not recipe:
            continue

        if fmt not in filter_format:
            continue

        # Load recipe lines once
        if rid not in lines_cache:
            lines_cache[rid] = db.get_recipe_lines(rid)
        lines = lines_cache[rid]

        if rid not in weight_cache:
            result = db.estimate_recipe_weight(lines)
            weight_cache[rid] = float(result.get("weight_g") or 0)
        ref_weight_g = weight_cache[rid]

        full_ing_cost, missing = _calc_ingredient_cost(lines, ing_map)
        has_missing = len(missing) > 0

        if not show_incomplete and has_missing:
            continue

        # ── Ingredient scale ──────────────────────────────────────────────────
        if fmt == "individual":
            iw    = float(recipe.get("individual_weight_g") or ind_weight_g)
            scale = iw / ref_weight_g if ref_weight_g > 0 else 0
        elif fmt == "bocado":
            bw    = float(recipe.get("bocado_weight_g") or boc_weight_g)
            scale = bw / ref_weight_g if ref_weight_g > 0 else 0
        else:
            # Standard — scale by volume if variant has its own diameter
            variant_d = _f(variant.get("ref_diameter_cm"))
            variant_h = _f(variant.get("ref_height_cm"))
            ref_d     = float(recipe.get("ref_diameter_cm") or 0)
            ref_h     = float(recipe.get("ref_height_cm") or 0)

            if variant_d and ref_d and recipe.get("size_type") == "diameter":
                target_h = variant_h or ref_h or 1.0
                base_h   = ref_h or 1.0
                scale = (variant_d ** 2 * target_h) / (ref_d ** 2 * base_h)
            else:
                scale = 1.0

        ing_cost = full_ing_cost * scale

        # ── Labour costs ──────────────────────────────────────────────────────
        ws_labour, ws_oven = _calc_labour(
            recipe, fmt, default_labour, default_oven, labour_power,
            ws_batch_large, ws_batch_ind, ws_batch_boc
        )
        rt_labour, rt_oven = _calc_labour(
            recipe, fmt, default_labour, default_oven, labour_power,
            rt_batch_large, rt_batch_ind, rt_batch_boc
        )
        ws_cost = ing_cost + ws_labour + ws_oven
        rt_cost = ing_cost + rt_labour + rt_oven

        target_ws = ws_margin
        target_rt = (
            rt_margin_large if fmt == "standard"
            else rt_margin_ind if fmt == "individual"
            else rt_margin_boc
        )

        # ── Prices ────────────────────────────────────────────────────────────
        variant_id   = variant.get("id")
        ws_price_ex  = _f(variant.get("ws_price_ex_vat"))
        rt_price_inc = _f(variant.get("rt_price_inc_vat"))
        rt_price_ex  = rt_price_inc / 1.10 if rt_price_inc else None

        ws_margin_ach = (ws_price_ex / ws_cost) if (ws_price_ex and ws_cost > 0) else None
        rt_margin_ach = (rt_price_ex / rt_cost) if (rt_price_ex and rt_cost > 0) else None

        ws_suggested     = ws_cost * target_ws
        rt_suggested_inc = rt_cost * target_rt * 1.10
        ws_gap           = (ws_price_ex - ws_suggested) if ws_price_ex else None

        # ── Traffic light ─────────────────────────────────────────────────────
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

        # ── Recipe label — include size for multi-size standard variants ───────
        variant_d  = _f(variant.get("ref_diameter_cm"))
        size_label = (
            variant.get("size_description") or
            (f"{variant_d:.0f}cm" if variant_d else "")
        )
        recipe_label = (
            f"{recipe['name']} ({size_label})"
            if size_label and fmt == "standard"
            else recipe["name"]
        )

        rows.append({
            "recipe_id":          rid,
            "variant_id":         variant_id,
            "Recipe":             recipe_label,
            "Format":             FORMAT_DISPLAY.get(fmt, fmt),
            "fmt_key":            fmt,
            "Scale":              f"{scale:.3f}×" if fmt != "standard" else "—",
            "Ing. cost":          ing_cost,
            "WS total cost":      ws_cost,
            "WS price":           ws_price_ex,
            "WS suggested":       ws_suggested,
            "WS gap":             ws_gap,
            "WS margin":          ws_margin_ach,
            "RT total cost":      rt_cost,
            "RT price (inc)":     rt_price_inc,
            "RT suggested (inc)": rt_suggested_inc,
            "RT margin":          rt_margin_ach,
            "Status":             status,
            "⚠️ Missing":          ", ".join(missing) if missing else "",
            "_missing":           has_missing,
        })

    if not rows:
        st.info("No recipes match the current filters.")
        return

    st.caption(f"{len(rows)} recipe/format combinations")

    # ── Display table ─────────────────────────────────────────────────────────
    import pandas as pd

    df         = pd.DataFrame(rows)
    display_df = df[[
        "Status", "Recipe", "Format", "Scale",
        "Ing. cost", "WS total cost", "WS price",
        "WS suggested", "WS gap", "WS margin",
        "RT total cost", "RT price (inc)",
        "RT suggested (inc)", "RT margin",
        "⚠️ Missing",
    ]].copy()

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

    n_green  = sum(1 for r in rows if r["Status"] == "🟢 On target")
    n_low    = sum(1 for r in rows if r["Status"] == "🟡 Review (low)")
    n_high   = sum(1 for r in rows if r["Status"] == "🟡 Review (high)")
    n_red    = sum(1 for r in rows if r["Status"] == "🔴 Below cost")
    n_nodata = sum(1 for r in rows if r["Status"] == "⚪ No price")
    n_miss   = sum(1 for r in rows if r["_missing"])

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

    # ── Price editor ──────────────────────────────────────────────────────────
    st.divider()
    with st.expander("✏️ Edit prices"):
        st.caption(
            "Select a recipe and format to update its current prices. "
            "Changes save directly to the database. "
            "Refresh the page to see updated margins in the table above."
        )

        # Recipe + format selectors
        recipe_names  = sorted(list(set(r["Recipe"] for r in rows)))
        edit_recipe   = st.selectbox(
            "Recipe", ["— select —"] + recipe_names,
            key="edit_price_recipe"
        )

        if edit_recipe and edit_recipe != "— select —":
            fmt_options = [
                r["fmt_key"] for r in rows
                if r["Recipe"] == edit_recipe
            ]
            fmt_labels  = [FORMAT_DISPLAY[f] for f in fmt_options]
            edit_fmt_label = st.selectbox(
                "Format", fmt_labels,
                key="edit_price_format"
            )
            edit_fmt = fmt_options[fmt_labels.index(edit_fmt_label)]

            # Find the matching row
            matching = next(
                (r for r in rows
                 if r["Recipe"] == edit_recipe
                 and r["fmt_key"] == edit_fmt),
                None
            )

            if matching:
                st.markdown(
                    f"**{edit_recipe} — {edit_fmt_label}** · "
                    f"WS cost: € {matching['WS total cost']:.2f} · "
                    f"RT cost: € {matching['RT total cost']:.2f}"
                    if isinstance(matching["WS total cost"], float)
                    else f"**{edit_recipe} — {edit_fmt_label}**"
                )

                ep1, ep2 = st.columns(2)
                with ep1:
                    new_ws = st.number_input(
                        "WS price ex-VAT (€)",
                        min_value=0.0,
                        value=float(matching["WS price"] or 0),
                        format="%.2f",
                        key="edit_ws_price",
                        help="Wholesale price excluding VAT"
                    )
                    # Show implied margin
                    ws_cost_val = matching.get("WS total cost")
                    if ws_cost_val and isinstance(ws_cost_val, float) and ws_cost_val > 0 and new_ws > 0:
                        implied_ws = new_ws / ws_cost_val
                        st.caption(f"Implied WS margin: {implied_ws:.2f}×")

                with ep2:
                    new_rt = st.number_input(
                        "RT price inc-VAT (€)",
                        min_value=0.0,
                        value=float(matching["RT price (inc)"] or 0),
                        format="%.2f",
                        key="edit_rt_price",
                        help="Retail price including 10% VAT"
                    )
                    # Show implied margin
                    rt_cost_val = matching.get("RT total cost")
                    if rt_cost_val and isinstance(rt_cost_val, float) and rt_cost_val > 0 and new_rt > 0:
                        implied_rt = (new_rt / 1.10) / rt_cost_val
                        st.caption(f"Implied RT margin (ex-VAT): {implied_rt:.2f}×")

                if st.button("💾 Save prices", type="primary",
                             key="save_edited_prices"):
                    variant_id = matching.get("variant_id")
                    if variant_id:
                        db.save_variant({
                            "id":              variant_id,
                            "ws_price_ex_vat": new_ws or None,
                            "rt_price_inc_vat": new_rt or None,
                        })
                        st.success(
                            f"Saved — {edit_recipe} {edit_fmt_label}: "
                            f"WS € {new_ws:.2f} · RT € {new_rt:.2f} inc-VAT",
                            icon="✅"
                        )
                    else:
                        # No variant row yet — create one
                        rid = matching["recipe_id"]
                        db.save_variant({
                            "recipe_id":       rid,
                            "format":          edit_fmt,
                            "channel":         "both",
                            "units_per_pack":  1,
                            "ws_price_ex_vat": new_ws or None,
                            "rt_price_inc_vat": new_rt or None,
                            "shelf_life_hours": 24,
                            "storage_instructions": "Refrigerada entre 0 - 5°C",
                            "label_approved":  False,
                        })
                        st.success("Prices saved to new variant", icon="✅")

    # ── Download ──────────────────────────────────────────────────────────────
    st.divider()
    csv = df.drop(columns=["_missing", "recipe_id", "variant_id",
                            "fmt_key"]).to_csv(index=False)
    st.download_button(
        "⬇️ Download as CSV",
        data=csv,
        file_name="millington_repricing.csv",
        mime="text/csv"
    )
    st.caption(
        "WS cost: wholesale batch assumptions · "
        "RT cost: retail batch assumptions · "
        "Packaging excluded. "
        "RT margins shown ex-VAT (price ÷ 1.10 ÷ cost)."
    )


# =============================================================================
# Helpers
# =============================================================================

def _f(val) -> float | None:
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


def _calc_ingredient_cost(lines, ing_map):
    total, missing = 0.0, []
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


def _calc_labour(recipe, fmt, labour_rate, oven_rate, power,
                 batch_large, batch_ind, batch_boc):
    if fmt == "standard":
        ref_b    = float(recipe.get("ref_batch_size") or 20)
        prep_hrs = float(recipe.get("ref_prep_hours") or 1.0)
        oven_hrs = float(recipe.get("ref_oven_hours") or 1.0)
        batch    = batch_large
    elif fmt == "individual":
        ref_b    = float(batch_ind)
        prep_hrs = float(
            recipe.get("small_batch_prep_hours") or
            recipe.get("ref_prep_hours") or 1.0
        )
        oven_hrs = float(
            recipe.get("small_batch_oven_hours") or
            recipe.get("ref_oven_hours") or 1.0
        )
        batch = batch_ind
    else:
        ref_b    = float(batch_boc)
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
    qty_factor = ((batch / ref_b) ** power) / batch
    return prep_hrs * qty_factor * labour_rate, oven_hrs * qty_factor * oven_rate
