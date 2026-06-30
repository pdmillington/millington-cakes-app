# screen_analysis.py
# =============================================================================
# Recipe analysis screen — cost breakdown pie charts for collaborators.
# Shows wholesale and retail cost breakdowns at reference size,
# plus a current price vs cost chart where Shopify data is available.
# =============================================================================

import streamlit as st
import millington_db as db
from millington_db import (
    get_allergen_declaration,
    get_ingredient_label_text,
    ALLERGEN_DISPLAY_ES,
)
from core.constants import FORMAT_TIER_CODES
from core.settings import load_settings
from core.pricing_engine import calc_ingredient_cost, calc_labour_cost
from ui.components import missing_prices_warning

def screen_analysis(recipe_id: str | None = None):
    """
    Recipe cost analysis.
    Pass recipe_id when embedding inside the Recipes screen tab —
    skips the recipe selector and uses that recipe directly.
    """
    if not recipe_id:
        st.title("Recipe analysis")
    st.caption(
        "Cost breakdown at reference size — useful for understanding "
        "what drives cost and discussing labour times with the team."
    )

    # ── Load data ─────────────────────────────────────────────────────────────
    # include_deprecated=True so old recipes remain available for calibration
    recipes     = db.get_recipes(include_deprecated=True)
    ingredients = db.get_ingredients()
    presets     = db.get_packaging_presets()
    cake_codes  = db.get_cake_codes()
    s = load_settings()

    recipe_map  = {r["name"]: r for r in recipes}
    ing_map     = {i["name"]: i for i in ingredients}
    code_by_id  = {cc["id"]: cc["code"] for cc in cake_codes}

    # ── Selectors — skipped when embedded with a recipe_id ───────────────────
    if recipe_id:
        recipe = next((r for r in recipes if r["id"] == recipe_id), {})
        if not recipe:
            st.error("Recipe not found.")
            return
        preset_names    = ["— none —"] + [p["name"] for p in presets]
        selected_preset = st.selectbox(
            "Packaging preset", preset_names,
            key=f"ana_preset_embedded_{recipe_id}"
        )
    else:
        col_sel1, col_sel2 = st.columns(2)
        with col_sel1:
            recipe_names  = sorted([
                f"🚫 {r['name']}" if r.get("deprecated") else r["name"]
                for r in recipes
            ])
            selected_name = st.selectbox("Recipe", recipe_names, key="ana_recipe")
        with col_sel2:
            preset_names    = ["— none —"] + [p["name"] for p in presets]
            selected_preset = st.selectbox(
                "Packaging preset", preset_names, key="ana_preset"
            )
        # Strip deprecation prefix before lookup
        lookup_name = selected_name.removeprefix("🚫 ")
        recipe = recipe_map.get(lookup_name, {})
        if not recipe:
            st.info("Select a recipe to continue.")
            return
        if recipe.get("deprecated"):
            st.warning("🚫 Esta receta está deprecada — disponible solo para comparación de costes.")

    st.divider()

    # ── Recipe reference data ─────────────────────────────────────────────────
    ref_batch_size = float(recipe.get("ref_batch_size") or 20)
    ref_prep_hours = float(recipe.get("ref_prep_hours") or 1.0)
    ref_oven_hours = float(recipe.get("ref_oven_hours") or 1.0)
    size_type      = recipe.get("size_type", "diameter")
    ref_diameter   = float(recipe.get("ref_diameter_cm") or 22)

    # Reference info display
    ref_desc = ""
    if size_type == "diameter":
        ref_desc = f"{ref_diameter:.0f}cm diameter"
        if recipe.get("ref_height_cm"):
            ref_desc += f" × {recipe['ref_height_cm']:.0f}cm tall"
    elif size_type == "weight":
        ref_desc = f"{recipe.get('ref_weight_kg', 1):.2f}kg"
    else:
        ref_desc = f"{recipe.get('ref_portions', 1)} portions"

    st.caption(f"Reference size: {ref_desc} · "
               f"Batch reference: {ref_batch_size:.0f} cakes · "
               f"{ref_prep_hours:.1f}h prep · {ref_oven_hours:.1f}h oven")

    # ── Ingredient cost at reference size ─────────────────────────────────────
    lines = db.get_recipe_lines(recipe["id"])

    # Build component cost map for any component recipe lines
    component_map: dict = {}
    for line in lines:
        if line.get("is_component_line"):
            comp_id = line.get("component_recipe_id")
            if comp_id and comp_id not in component_map:
                component_map[comp_id] = db.calc_component_cost_per_g(
                    comp_id, s.default_labour_rate
                )

    result          = calc_ingredient_cost(lines, ing_map, component_map=component_map)
    ingredient_cost = result.total          # scale is 1.0 — analysis always uses reference size
    ing_breakdown   = result.breakdown
    missing_prices  = result.missing_prices

    if missing_prices:
        missing_prices_warning(missing_prices)

    # ── Component labour cost (tracked as labour, not ingredients) ────────────
    # Component recipes may carry their own labour_per_kg (e.g. crema de limón).
    # That cost is added to the labour bucket here so it appears as labour in
    # charts rather than being folded into the ingredient cost.
    component_labour_cost = 0.0
    for line in lines:
        if line.get("is_component_line"):
            lpkg   = float(line.get("component_labour_per_kg") or 0)
            amount = float(line.get("amount") or 0)
            component_labour_cost += (lpkg * s.default_labour_rate / 1000.0) * amount

    # ── Packaging cost ────────────────────────────────────────────────────────
    packaging_cost = 0.0
    units_per_pack = 1
    if selected_preset != "— none —":
        preset_data = next(
            (p for p in presets if p["name"] == selected_preset), None
        )
        if preset_data:
            preset_lines   = db.get_preset_lines(preset_data["id"])
            units_per_pack = int(preset_data.get("units_per_pack") or 1)
            for pl in preset_lines:
                cpu = pl.get("consumable_cost_per_unit") or 0
                qty = float(pl.get("quantity") or 1)
                packaging_cost += (cpu * qty) / units_per_pack

    # ── Labour cost — wholesale and retail ────────────────────────────────────    
    ws = calc_labour_cost(s.ws_batch_large, ref_batch_size, ref_prep_hours, ref_oven_hours, s)
    rt = calc_labour_cost(s.rt_batch_large, ref_batch_size, ref_prep_hours, ref_oven_hours, s)
    ws_labour, ws_oven = ws.labour_cost, ws.oven_cost
    rt_labour, rt_oven = rt.labour_cost, rt.oven_cost

    ws_total = ingredient_cost + ws_labour + ws_oven + component_labour_cost + packaging_cost
    rt_total = ingredient_cost + rt_labour + rt_oven + component_labour_cost + packaging_cost

    # ── Current price lookup ──────────────────────────────────────────────────
    cake_code_id = recipe.get("cake_code_id")
    code_str     = code_by_id.get(cake_code_id, "")
    live_prices  = db.get_current_prices(code_str) if code_str else []
    if err := st.session_state.pop("_current_prices_error", None):
        st.warning(f"⚠️ No se pudieron cargar precios actuales: `{err}`")

    def find_ws_price():
        matches = [
            p for p in live_prices
            if p["channel"] in ("WS", "MD")
            and any(f"-{fc}-" in p["sku_code"]
                    for fc in FORMAT_TIER_CODES['Standard'])
        ]
        if not matches:
            return None, None
        best = next((p for p in matches if p["channel"] == "WS"), matches[0])
        return float(best["price_ex_vat"]), best["sku_code"]

    def find_rt_price():
        matches = [
            p for p in live_prices
            if p["channel"] == "GW"
            and any(f"-{fc}-" in p["sku_code"]
                    for fc in FORMAT_TIER_CODES['Standard'])
        ]
        if not matches:
            return None, None
        return float(matches[0]["price_ex_vat"]), matches[0]["sku_code"]

    current_ws_ex, current_ws_sku = find_ws_price()
    current_rt_ex, current_rt_sku = find_rt_price()

    # ── Pie charts ────────────────────────────────────────────────────────────
    try:
        import plotly.graph_objects as go
        has_plotly = True
    except ImportError:
        has_plotly = False

    COLOURS = {
        "Ingredients": "#2d6a4f",
        "Labour":      "#74c69d",
        "Oven":        "#b7e4c7",
        "Packaging":   "#d8f3dc",
        "Profit":      "#1b4332",
        "Loss":        "#dc2626",
    }

    def make_pie(labels, values, title, colours):
        if not has_plotly:
            return None
        fig = go.Figure(data=[go.Pie(
            labels=labels,
            values=values,
            marker_colors=colours,
            hole=0.35,
            textinfo="label+percent",
            textfont_size=12,
            hovertemplate="%{label}: € %{value:.4f}<extra></extra>",
        )])
        fig.update_layout(
            title_text=title,
            title_x=0.5,
            showlegend=False,
            margin=dict(t=50, b=10, l=10, r=10),
            height=320,
        )
        return fig

    if not has_plotly:
        st.warning("Install plotly to see charts: `pip install plotly`")

    # ── Row 1: Wholesale and retail cost breakdown ────────────────────────────
    st.markdown("### Cost breakdown at reference size")

    col1, col2 = st.columns(2)

    with col1:
        labels  = ["Ingredients", "Labour", "Oven", "Packaging"]
        values  = [ingredient_cost, ws_labour + component_labour_cost, ws_oven, packaging_cost]
        colours = [COLOURS[l] for l in labels]
        raw_vals = values[:]
        values  = [v for v in values if v > 0]
        labels  = [l for l, v in zip(labels, raw_vals) if v > 0]
        colours = [COLOURS[l] for l in labels]

        if has_plotly:
            fig = make_pie(
                labels, values,
                f"Wholesale cost  ·  € {ws_total:.2f} / cake",
                colours
            )
            st.plotly_chart(fig, width='stretch')

        comp_lab_note = f" + € {component_labour_cost:.4f} component" if component_labour_cost else ""
        st.caption(
            f"Labour: batch of {s.ws_batch_large} · "
            f"€ {ws_labour:.4f} labour + € {ws_oven:.4f} oven{comp_lab_note}"
        )

    with col2:
        rt_labels  = ["Ingredients", "Labour", "Oven", "Packaging"]
        rt_vals    = [ingredient_cost, rt_labour + component_labour_cost, rt_oven, packaging_cost]
        rt_raw     = rt_vals[:]
        rt_labels  = [l for l, v in zip(rt_labels, rt_raw) if v > 0]
        rt_colours = [COLOURS[l] for l in rt_labels]
        rt_vals    = [v for v in rt_vals if v > 0]

        if has_plotly:
            fig = make_pie(
                rt_labels, rt_vals,
                f"Retail cost  ·  € {rt_total:.2f} / cake",
                rt_colours
            )
            st.plotly_chart(fig, width='stretch')

        st.caption(
            f"Labour: batch of {s.rt_batch_large} · "
            f"€ {rt_labour:.4f} labour + € {rt_oven:.4f} oven{comp_lab_note}"
        )

    # ── Row 2: Current price breakdown ───────────────────────────────────────
    if current_ws_ex or current_rt_ex:
        st.markdown("### Current price vs cost")

        col3, col4 = st.columns(2)

        def price_pie(current_price_ex, cost_total, sku, channel_label):
            if not current_price_ex:
                return
            profit = current_price_ex - cost_total
            labels = ["Ingredients", "Labour", "Oven", "Packaging"]
            costs  = [ingredient_cost,
                      (ws_labour if "holesale" in channel_label else rt_labour) + component_labour_cost,
                      ws_oven   if "holesale" in channel_label else rt_oven,
                      packaging_cost]
            labels = [l for l, v in zip(labels, costs) if v > 0]
            vals   = [v for v in costs if v > 0]
            colours = [COLOURS[l] for l in labels]

            if profit > 0:
                labels.append("Profit")
                vals.append(profit)
                colours.append(COLOURS["Profit"])
                margin_x = current_price_ex / cost_total if cost_total else 0
                subtitle = (
                    f"{channel_label}  ·  € {current_price_ex:.2f} ex-VAT  "
                    f"({margin_x:.2f}× cost)  [{sku}]"
                )
            else:
                labels.append("Loss")
                vals.append(abs(profit))
                colours.append(COLOURS["Loss"])
                subtitle = (
                    f"{channel_label}  ·  € {current_price_ex:.2f} ex-VAT  "
                    f"⚠️ below cost  [{sku}]"
                )

            if has_plotly:
                fig = make_pie(labels, vals, subtitle, colours)
                st.plotly_chart(fig, width='stretch')

            # Key numbers
            st.caption(
                f"Cost: € {cost_total:.2f} · "
                f"Price: € {current_price_ex:.2f} · "
                f"Margin: € {profit:.2f}"
            )

        with col3:
            price_pie(current_ws_ex, ws_total, current_ws_sku, "Wholesale")

        with col4:
            price_pie(current_rt_ex, rt_total, current_rt_sku, "Retail")

    else:
        st.caption(
            "No current Shopify prices found for this recipe — "
            "assign a cake code in the recipe editor to enable price comparison."
        )

    # ── Ingredient breakdown table ────────────────────────────────────────────
    st.markdown("### Ingredient cost breakdown")
    st.caption("At reference size · sorted by cost descending")

    if ing_breakdown:
        ing_breakdown.sort(key=lambda x: x["line_cost"], reverse=True)
        total_ing = sum(i["line_cost"] for i in ing_breakdown)

        # Headers
        h1, h2, h3, h4, h5 = st.columns([3, 1, 1, 1, 1])
        h1.markdown("**Ingredient**")
        h2.markdown("**Amount**")
        h3.markdown("**€/unit**")
        h4.markdown("**Line cost**")
        h5.markdown("**% of total**")

        for row in ing_breakdown:
            pct  = (row["line_cost"] / total_ing * 100) if total_ing else 0
            c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
            c1.write(row["name"])
            c2.write(f"{row['amount']:.0f}")
            c3.write(f"€ {row['cpu']:.5f}")
            c4.write(f"€ {row['line_cost']:.4f}")
            c5.write(f"{pct:.1f}%")

        st.divider()
        st.markdown(f"**Total ingredient cost: € {total_ing:.4f}**")

    else:
        st.caption("No ingredient costs available — add prices in the Ingredients screen.")

    # ── Labour cost breakdown ─────────────────────────────────────────────────
    st.markdown("### Labour cost breakdown")
    st.caption(f"Wholesale batch of {s.ws_batch_large} · rate € {s.default_labour_rate:.2f}/hr · oven € {s.default_oven_rate:.2f}/hr")

    lh1, lh2, lh3 = st.columns([4, 1, 1])
    lh1.markdown("**Source**")
    lh2.markdown("**hr/kg**")
    lh3.markdown("**Cost**")

    if ws_labour > 0:
        lc1, lc2, lc3 = st.columns([4, 1, 1])
        lc1.write(f"Recipe prep ({ref_prep_hours:.2f}h ref × batch factor {ws.qty_factor:.4f})")
        lc2.write("—")
        lc3.write(f"€ {ws_labour:.4f}")

    if ws_oven > 0:
        lc1, lc2, lc3 = st.columns([4, 1, 1])
        lc1.write(f"Recipe oven ({ref_oven_hours:.2f}h ref × batch factor {ws.qty_factor:.4f})")
        lc2.write("—")
        lc3.write(f"€ {ws_oven:.4f}")

    for line in lines:
        if line.get("is_component_line"):
            lpkg   = float(line.get("component_labour_per_kg") or 0)
            amount = float(line.get("amount") or 0)
            name   = line.get("ingredient_name", "")
            comp_cost = (lpkg * s.default_labour_rate / 1000.0) * amount
            lc1, lc2, lc3 = st.columns([4, 1, 1])
            lc1.write(f"{name} ({amount:.0f}g component)")
            lc2.write(f"{lpkg:.3f}" if lpkg else "—")
            lc3.write(f"€ {comp_cost:.4f}" if lpkg else "—")

    st.divider()
    total_labour_ws = ws_labour + ws_oven + component_labour_cost
    st.markdown(f"**Total labour: € {total_labour_ws:.4f}**")

    # ── Allergen declaration preview ──────────────────────────────────────────
    st.markdown("### Declaración de alérgenos")
    st.caption("Calculado automáticamente a partir de los ingredientes de la receta.")

    declaration = db.get_allergen_declaration(recipe["id"])

    if declaration["warnings"]:
        for w in declaration["warnings"]:
            st.warning(w)

    col_c, col_p = st.columns(2)
    with col_c:
        st.markdown("**Contiene:**")
        if declaration["contiene"]:
            for item in declaration["contiene"]:
                st.markdown(f"- {item.capitalize()}")
        else:
            st.caption("Ninguno detectado")

    with col_p:
        st.markdown("**Puede contener:**")
        if declaration["puede_contener"]:
            for item in declaration["puede_contener"]:
                st.markdown(f"- {item.capitalize()}")
        else:
            st.caption("Ninguno detectado")

    st.divider()
    st.markdown("### Borrador — lista de ingredientes")
    st.caption(
        "Ordenado por peso descendente (EU 1169/2011). "
        "Revisa y aprueba en el editor de variantes antes de generar el ficha."
    )

    label_data = db.get_ingredient_label_text(recipe["id"])

    if label_data["label_text"]:
        st.markdown(f"*{label_data['label_text']}*")
        st.caption("Los alérgenos aparecerán en negrita en el ficha generado.")
    else:
        st.caption("Sin datos de etiqueta disponibles.")