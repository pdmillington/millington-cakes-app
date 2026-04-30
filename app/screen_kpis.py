# screen_kpis.py
# =============================================================================
# Business KPI dashboard
#
# Data sources (in priority order, no double-counting):
#   1. holded_monthly_revenue / holded_monthly_products (Supabase)
#      — populated from Holded Excel exports (upload monthly)
#      — authoritative for any uploaded month
#   2. holded_api.get_current_month_supplement()
#      — Holded API, current calendar month only
#      — used ONLY if current month not yet in uploaded data
#      — known limitation: retail (Shopify) may be partial
#
# Tabs:
#   1. Ingresos vs Objetivo  — monthly revenue + Blanca target ramp
#   2. Top Productos         — top 5 by units (current year vs last year)
#   3. Ingredientes          — estimated ingredient cost + weight
#   4. Gestión de datos      — upload widget + freshness check
#
# Target model (Blanca, Apr 2026 – Mar 2027):
#   Baseline = total ex-VAT revenue Apr 2025 – Mar 2026
#   Base scenario     (any growth)  → 10% capital social
#   Alto Rendimiento  (≥ 2× base)   → 15% capital social
#   Monthly ramp: 1.2× | 1.2× | 1.6× | 1.6× | 2.0× | 2.0× | 2.4×(×6)
#   Weighted average = 2.0×  ✓
# =============================================================================

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timezone
from io import BytesIO

import altair as alt
import pandas as pd
import streamlit as st
from rapidfuzz import fuzz, process

import holded_api as holded
import millington_db as db

# =============================================================================
# Constants
# =============================================================================

TARGET_START   = date(2026, 4, 1)
TARGET_END     = date(2027, 3, 31)
BASELINE_START = date(2025, 4, 1)
BASELINE_END   = date(2026, 3, 31)

TARGET_RAMP    = [1.2, 1.2, 1.6, 1.6, 2.0, 2.0, 2.4, 2.4, 2.4, 2.4, 2.4, 2.4]
ALTO_THRESHOLD = 2.0

FUZZY_THRESHOLD = 78
SKU_RE = re.compile(r'\b([A-Z]{2}-\d{2}-[A-Z]{2}-[A-Z]{2,4}(?:-[A-Z]{2})?)\b')

# Brand colours
C_BLUE    = "#3A7FBF"
C_LIGHT   = "#9BB0C5"
C_PALE    = "#CBD5E0"
C_RED     = "#E8413C"
C_GREEN   = "#2E7D52"


# =============================================================================
# Helpers
# =============================================================================

def _month_label(year: int, month: int) -> str:
    return date(year, month, 1).strftime("%b %Y")


def _monthly_targets(baseline_annual: float) -> list[dict]:
    monthly_base = baseline_annual / 12
    rows = []
    for i, mult in enumerate(TARGET_RAMP):
        month_num = (TARGET_START.month - 1 + i) % 12 + 1
        year      = TARGET_START.year + (TARGET_START.month - 1 + i) // 12
        rows.append({
            "month_label": _month_label(year, month_num),
            "month_date":  date(year, month_num, 1),
            "target":      monthly_base * mult,
            "multiplier":  mult,
        })
    return rows


def _check_data_freshness() -> tuple[bool, str]:
    """
    Returns (is_fresh, message).
    is_fresh = True if last complete month is uploaded.
    """
    today = date.today()
    # Last complete month
    if today.month == 1:
        last_year, last_month = today.year - 1, 12
    else:
        last_year, last_month = today.year, today.month - 1

    status = db.get_upload_status()
    if (last_year, last_month) in status['months']:
        return True, f"Datos actualizados hasta {_month_label(last_year, last_month)} ✓"
    else:
        return False, (
            f"⚠️ Los datos de {_month_label(last_year, last_month)} no han sido subidos. "
            f"Descarga los ficheros de Holded y súbelos en la pestaña **Gestión de datos**."
        )


def _get_all_revenue() -> list[dict]:
    """
    Build a unified month-by-month revenue list combining:
    - Uploaded data (authoritative for those months)
    - API supplement for current month if not uploaded

    Returns list of dicts:
      {year, month, month_label, ventas_ex_vat, source}
    where source is 'upload' or 'api'
    """
    uploaded = {(r['year'], r['month']): r for r in db.get_monthly_revenue()}
    today    = date.today()
    cy, cm   = today.year, today.month

    rows = []
    for (year, month), r in sorted(uploaded.items()):
        rows.append({
            "year":         year,
            "month":        month,
            "month_label":  _month_label(year, month),
            "ventas_ex_vat": float(r['ventas_ex_vat']),
            "source":       "upload",
        })

    # Add current month from API if not uploaded
    if (cy, cm) not in uploaded:
        supp = holded.get_current_month_supplement()
        if supp["revenue"] != 0 or supp["doc_count"] > 0:
            rows.append({
                "year":          cy,
                "month":         cm,
                "month_label":   _month_label(cy, cm),
                "ventas_ex_vat": supp["revenue"],
                "source":        "api",
            })

    rows.sort(key=lambda x: (x["year"], x["month"]))
    return rows


def _get_all_products_by_month() -> list[dict]:
    """
    Returns all product/month rows from uploaded data + current month API supplement.
    Current month from API only if not uploaded.
    """
    uploaded_months = {(r['year'], r['month'])
                       for r in db.get_monthly_revenue()}
    today  = date.today()
    cy, cm = today.year, today.month

    rows = list(db.get_monthly_products())

    if (cy, cm) not in uploaded_months:
        supp = holded.get_current_month_supplement()
        for name, units in supp.get("products", {}).items():
            if units <= 0:
                continue
            sku_match = SKU_RE.search(name)
            sku = sku_match.group(1) if sku_match else None
            clean = re.sub(r'\s*[-–]\s*' + re.escape(sku) + r'\s*$', '', name).strip() if sku else name
            rows.append({
                "year":         cy,
                "month":        cm,
                "product_name": clean,
                "sku":          sku,
                "units":        units,
            })

    return rows


def _build_sku_to_price(skus: list[dict]) -> dict[str, float]:
    """Map sku_code → price (ex-VAT) from Supabase."""
    return {s["sku_code"]: float(s.get("base_price") or s.get("price") or 0)
            for s in skus if s.get("sku_code")}


def _build_sku_map(skus: list[dict]) -> dict[str, str]:
    return {s["sku_code"]: s["recipe_id"]
            for s in skus if s.get("sku_code") and s.get("recipe_id")}


def _build_fuzzy_map(recipes: list[dict]) -> tuple[list[str], dict[str, str]]:
    names   = [r["name"] for r in recipes if r.get("name")]
    name_id = {r["name"]: r["id"] for r in recipes if r.get("name") and r.get("id")}
    return names, name_id


def _match_recipe(sku, name, sku_map, recipe_names, name_id_map,
                  name_to_sku: dict | None = None):
    """
    Resolve a Holded product name/sku to a recipe_id.

    Priority order:
      1. Exact SKU match (from product data or inventory table)
      2. Name → SKU via holded_products inventory table (exact)
      3. Fuzzy name match against recipe names (fallback for old/unknown products)
    """
    # 1. Direct SKU match
    if sku and sku in sku_map:
        return sku_map[sku], "exact"

    # 2. Name → SKU via inventory table
    if name and name_to_sku:
        inv_sku = name_to_sku.get(name)
        if inv_sku and inv_sku in sku_map:
            return sku_map[inv_sku], "exact"

    # 3. Fuzzy fallback
    if name and recipe_names:
        result = process.extractOne(name, recipe_names, scorer=fuzz.token_sort_ratio)
        if result and result[1] >= FUZZY_THRESHOLD:
            return name_id_map.get(result[0]), "fuzzy"

    return None, "none"


# =============================================================================
# Tab 1 — Revenue vs Target
# =============================================================================

def _tab_revenue():
    st.markdown("### Ingresos mensuales vs objetivo")

    revenue_rows = _get_all_revenue()
    if not revenue_rows:
        st.info("No hay datos de ingresos. Sube los ficheros de Holded en la pestaña Gestión de datos.")
        return

    # Baseline: Apr 2025 – Mar 2026
    baseline_annual = sum(
        r["ventas_ex_vat"] for r in revenue_rows
        if BASELINE_START <= date(r["year"], r["month"], 1) <= BASELINE_END
    )

    targets      = _monthly_targets(baseline_annual) if baseline_annual > 0 else []
    target_by_ml = {t["month_label"]: t["target"] for t in targets}

    # All months across uploaded + target period
    all_month_labels = sorted(
        set([r["month_label"] for r in revenue_rows] + list(target_by_ml.keys())),
        key=lambda ml: datetime.strptime(ml, "%b %Y")
    )

    # Build chart dataframe
    rev_by_ml = {r["month_label"]: r for r in revenue_rows}
    chart_rows = []
    for ml in all_month_labels:
        d   = datetime.strptime(ml, "%b %Y").date()
        rev = rev_by_ml.get(ml)
        tgt = target_by_ml.get(ml)
        period = (
            "Período base"  if BASELINE_START <= d <= BASELINE_END else
            "Año objetivo"  if TARGET_START   <= d <= TARGET_END   else
            "Histórico"
        )
        is_api = rev and rev.get("source") == "api"
        chart_rows.append({
            "Mes":            ml,
            "Ingresos (€)":   rev["ventas_ex_vat"] if rev else None,
            "Objetivo (€)":   tgt,
            "Período":        period,
            "Fuente":         ("API (parcial)" if is_api else "Excel") if rev else None,
        })

    df        = pd.DataFrame(chart_rows)
    df_actual = df.dropna(subset=["Ingresos (€)"])
    df_target = df.dropna(subset=["Objetivo (€)"])

    # ── KPI metrics ────────────────────────────────────────────────────────────
    if baseline_annual > 0:
        today     = date.today()
        alto_thresh = baseline_annual * ALTO_THRESHOLD

        ytd_revenue = sum(
            r["ventas_ex_vat"] for r in revenue_rows
            if TARGET_START <= date(r["year"], r["month"], 1) <= today
        )
        elapsed = max(1, (today.year - TARGET_START.year) * 12
                         + today.month - TARGET_START.month + 1)
        ytd_target   = sum(t["target"] for t in targets[:elapsed]) if targets else 0
        pct          = ytd_revenue / ytd_target * 100 if ytd_target else 0
        annualised   = ytd_revenue / elapsed * 12
        pct_alto     = min(annualised / alto_thresh * 100, 100) if alto_thresh else 0

        if annualised >= alto_thresh:
            scenario = "🏆 Alto Rendimiento"
        elif pct >= 90:
            scenario = "✅ En camino"
        else:
            scenario = "⚠️ Por debajo"

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Base anual (Abr25–Mar26)", f"€{baseline_annual:,.0f}")
        m2.metric("Ingresos YTD (desde Abr26)", f"€{ytd_revenue:,.0f}")
        m3.metric("Objetivo YTD", f"€{ytd_target:,.0f}", delta=f"{pct-100:+.0f}%")
        m4.metric("Escenario", scenario)

        st.caption(
            f"Umbral Alto Rendimiento (2× base): **€{alto_thresh:,.0f}** · "
            f"Ritmo anualizado: **€{annualised:,.0f}** ({pct_alto:.0f}%)"
        )
        st.progress(pct_alto / 100)
        st.divider()
    else:
        st.info(
            "Sin datos del período base (Abr 2025 – Mar 2026). "
            "Sube los ficheros de 2025 para activar el objetivo."
        )

    # ── Chart ─────────────────────────────────────────────────────────────────
    colour_scale = alt.Scale(
        domain=["Histórico", "Período base", "Año objetivo"],
        range= [C_PALE,      C_LIGHT,        C_BLUE]
    )

    bars = (
        alt.Chart(df_actual)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("Mes:N", sort=all_month_labels,
                    axis=alt.Axis(labelAngle=-45, title=None)),
            y=alt.Y("Ingresos (€):Q",
                    axis=alt.Axis(title="€ ex-IVA", format=",.0f")),
            color=alt.Color("Período:N", scale=colour_scale,
                            legend=alt.Legend(title="Período")),
            opacity=alt.condition(
                alt.datum["Fuente"] == "API (parcial)",
                alt.value(0.6), alt.value(1.0)
            ),
            tooltip=[
                alt.Tooltip("Mes:N"),
                alt.Tooltip("Ingresos (€):Q", format=",.2f"),
                alt.Tooltip("Período:N"),
                alt.Tooltip("Fuente:N"),
            ],
        )
    )

    layers = [bars]
    if not df_target.empty:
        layers.append(
            alt.Chart(df_target)
            .mark_line(color=C_RED, strokeWidth=2.5,
                       strokeDash=[6, 3], interpolate="step-after", point=True)
            .encode(
                x=alt.X("Mes:N", sort=all_month_labels),
                y=alt.Y("Objetivo (€):Q"),
                tooltip=[alt.Tooltip("Mes:N"),
                         alt.Tooltip("Objetivo (€):Q", format=",.2f")],
            )
        )

    st.altair_chart(
        alt.layer(*layers).properties(height=400)
        .configure_axis(labelFontSize=11, titleFontSize=12),
        use_container_width=True
    )
    st.caption(
        "Barras semitransparentes = datos vía API (mes actual, pueden ser parciales). "
        "Línea roja = objetivo contractual. Ex-IVA."
    )

    if targets:
        with st.expander("Ver desglose del objetivo mes a mes"):
            tdf = pd.DataFrame(targets)[["month_label", "multiplier", "target"]]
            tdf.columns = ["Mes", "Multiplicador", "Objetivo (€)"]
            tdf["Objetivo (€)"]  = tdf["Objetivo (€)"].map(lambda x: f"€{x:,.2f}")
            tdf["Multiplicador"] = tdf["Multiplicador"].map(lambda x: f"{x}×")
            st.dataframe(tdf, hide_index=True, width='stretch')


# =============================================================================
# Tab 2 — Top Products
# =============================================================================

def _tab_products():
    st.markdown("### Top productos — unidades vendidas")

    today = date.today()
    cy    = today.year

    # Same period last year
    ly = cy - 1

    all_rows = _get_all_products_by_month()
    if not all_rows:
        st.info("No hay datos de productos. Sube los ficheros de Holded.")
        return

    # Filter to current year YTD months and same period last year
    ytd_months  = {(r["year"], r["month"]) for r in _get_all_revenue()
                   if r["year"] == cy and date(r["year"], r["month"], 1) <= today}
    ly_months   = {(ly, m) for _, m in ytd_months}

    def agg_units(rows, month_set):
        totals: dict[str, float] = defaultdict(float)
        for r in rows:
            if (r["year"], r["month"]) in month_set:
                totals[r["product_name"]] += float(r["units"])
        return dict(totals)

    ytd_units = agg_units(all_rows, ytd_months)
    ly_units  = agg_units(all_rows, ly_months)

    n = st.slider("Mostrar top N productos", 5, 15, 5, key="top_n_products")

    top = sorted(ytd_units.items(), key=lambda x: x[1], reverse=True)[:n]
    if not top:
        st.info("Sin datos de unidades para el año actual.")
        return

    chart_rows = []
    for name, units_cy in top:
        units_ly = ly_units.get(name, 0)
        chart_rows.append({
            "Producto":       name[:35],
            f"Uds {cy}":      units_cy,
            f"Uds {ly}":      units_ly if units_ly else None,
        })

    df_top = pd.DataFrame(chart_rows)
    df_melt = pd.melt(
        df_top,
        id_vars=["Producto"],
        value_vars=[f"Uds {cy}", f"Uds {ly}"],
        var_name="Año",
        value_name="Unidades",
    ).dropna(subset=["Unidades"])

    chart = (
        alt.Chart(df_melt)
        .mark_bar()
        .encode(
            x=alt.X("Producto:N",
                    sort=[r["Producto"] for r in chart_rows],
                    axis=alt.Axis(labelAngle=-20, title=None, labelLimit=220)),
            y=alt.Y("Unidades:Q",
                    axis=alt.Axis(title="Unidades vendidas")),
            color=alt.Color("Año:N",
                            scale=alt.Scale(
                                domain=[f"Uds {cy}", f"Uds {ly}"],
                                range=[C_BLUE, C_LIGHT]
                            )),
            xOffset="Año:N",
            tooltip=[alt.Tooltip("Producto:N"),
                     alt.Tooltip("Año:N"),
                     alt.Tooltip("Unidades:Q", format=".1f")],
        )
        .properties(height=360)
    )
    st.altair_chart(chart, use_container_width=True)

    st.divider()
    st.markdown("**Detalle**")
    detail = []
    for name, units_cy in top:
        units_ly = ly_units.get(name, 0)
        change   = ((units_cy - units_ly) / units_ly * 100) if units_ly else None
        detail.append({
            "Producto":        name,
            f"Uds {cy}":       f"{units_cy:.1f}",
            f"Uds {ly}":       f"{units_ly:.1f}" if units_ly else "—",
            "Variación":       f"{change:+.1f}%" if change is not None else "nuevo",
        })
    st.dataframe(pd.DataFrame(detail), hide_index=True, width='stretch')

    # Period info
    uploaded_cy = [(r["year"], r["month"]) for r in _get_all_revenue() if r["year"] == cy]
    if uploaded_cy:
        first = min(uploaded_cy, key=lambda x: x[1])
        last  = max(uploaded_cy, key=lambda x: x[1])
        st.caption(
            f"Período {cy}: {_month_label(*first)} → {_month_label(*last)} · "
            f"Mismo período {ly} para comparativa."
        )


# =============================================================================
# Tab 3 — Ingredient Spend
# =============================================================================

def _tab_ingredients():
    st.markdown("### Ingredientes — coste estimado y consumo")

    # ── Period selector ────────────────────────────────────────────────────────
    col_p1, col_p2, _ = st.columns([1.5, 1.5, 3])
    with col_p1:
        year_sel = st.selectbox(
            "Año",
            options=sorted({r["year"] for r in db.get_monthly_revenue()}, reverse=True),
            key="ing_year"
        )
    with col_p2:
        months_available = sorted(
            {r["month"] for r in db.get_monthly_revenue()
             if r["year"] == year_sel}
        )
        month_options = ["Todo el año"] + [_month_label(year_sel, m) for m in months_available]
        month_sel = st.selectbox("Mes", month_options, key="ing_month")

    month_filter = None
    if month_sel != "Todo el año":
        month_filter = datetime.strptime(month_sel, "%b %Y").month

    product_rows = db.get_monthly_products(year=year_sel, month=month_filter)
    if not product_rows:
        st.info("Sin datos de productos para el período seleccionado.")
        return

    # ── Load recipe matching data ──────────────────────────────────────────────
    skus      = db.get_sku_to_recipe_map()
    recipes        = db.get_recipes()
    sku_map        = _build_sku_map(skus)
    recipe_names, name_id_map = _build_fuzzy_map(recipes)
    name_to_sku    = db.get_name_to_sku_map()
    products    = db.get_holded_products()
    sku_to_pack = {p["sku"]: p.get("units_per_pack", 1) for p in products}
    sku_map   = _build_sku_map(skus)

    # Warn about ambiguous product names (same name, multiple SKUs)
    ambiguous = st.session_state.get('_holded_ambiguous_names', {})
    if ambiguous:
        with st.expander(
            f"⚠️ {len(ambiguous)} producto(s) con nombre ambiguo — revisar en Holded",
            expanded=False
        ):
            st.caption(
                "Estos productos tienen el mismo nombre pero distintos SKUs en Holded. "
                "El cálculo de ingredientes usa el primer SKU alfabético, lo que puede "
                "ser incorrecto. Solución: asignar nombres únicos por variante en Holded."
            )
            amb_df = pd.DataFrame([
                {"Nombre": name, "SKUs": ", ".join(skus), "Usado": skus[0]}
                for name, skus in sorted(ambiguous.items())
            ])
            st.dataframe(amb_df, hide_index=True, width='stretch')

    @st.cache_data(ttl=300)
    def _ing_lines():
        return db.get_ingredient_lines_all()
    ing_lines = _ing_lines()

    recipe_ing: dict[str, list] = defaultdict(list)
    for il in ing_lines:
        recipe_ing[il["recipe_id"]].append(il)

    # ── Recipe total weights (sum of ingredient amounts ≈ recipe weight in g) ─
    recipe_total_g: dict[str, float] = {
        rid: sum(float(il.get("amount") or 0) for il in lines)
        for rid, lines in recipe_ing.items()
    }

    # ── Recipe individual/bocado weights from recipes table ───────────────────
    recipe_by_id = {r["id"]: r for r in recipes}

    # ── Scale factor by SKU size code ─────────────────────────────────────────
    def _scale_factor(recipe_id: str, sku: str) -> float:
        """
        Scale full-recipe ingredient amounts to the actual size sold.
        LA/XL/XX/DC = 1.0 (full recipe); TI/IN/MI/BO use weight ratios.
        """
        parts = (sku or "").split("-")
        size  = parts[2].upper() if len(parts) >= 3 else "LA"
        if size in ("LA", "XL", "XX", "DC"):
            return 1.0
        total_g = recipe_total_g.get(recipe_id, 0)
        if total_g <= 0:
            return 1.0
        recipe = recipe_by_id.get(recipe_id, {})
        ind_g  = float(recipe.get("individual_weight_g") or 100)
        boc_g  = float(recipe.get("bocado_weight_g")     or 30)
        return {
            "TI": ind_g / total_g,
            "IN": (ind_g * 4) / total_g,
            "MI": boc_g / total_g,
            "BO": (boc_g * 25) / total_g,
        }.get(size, 1.0)

    # ── Unit conversions (count → grams) ──────────────────────────────────────
    _UNIT_TO_G = {
        "limones":  100.0,
        "limas":     67.0,
        "naranja":  180.0,
        "manzanas": 182.0,
    }

    # Accumulators
    cost_acc:  dict[str, float] = defaultdict(float)
    kg_acc:    dict[str, float] = defaultdict(float)
    litre_acc: dict[str, float] = defaultdict(float)
    unit_acc:  dict[str, float] = defaultdict(float)

    n_exact = n_fuzzy = n_unmatched = 0

    for row in product_rows:
        recipe_id, match_type = _match_recipe(
            row.get("sku"), row["product_name"],
            sku_map, recipe_names, name_id_map,
            name_to_sku=name_to_sku,
        )
        if not recipe_id:
            n_unmatched += 1
            continue
        n_exact   += match_type == "exact"
        n_fuzzy   += match_type == "fuzzy"

        inv_sku    = name_to_sku.get(row["product_name"], row.get("sku") or "")
        pack_size  = sku_to_pack.get(inv_sku, 1)
        scale      = _scale_factor(recipe_id, inv_sku)
        units_sold = float(row["units"]) * pack_size * scale

        for il in recipe_ing.get(recipe_id, []):
            ing_name  = il.get("ingredient_name", "Unknown")
            amount    = float(il.get("amount") or 0)
            cost_pu   = float(il.get("cost_per_unit") or 0)
            pack_unit = (il.get("pack_unit") or "").lower()

            name_lower  = ing_name.lower()
            unit_weight = next(
                (w for key, w in _UNIT_TO_G.items() if key in name_lower), None
            )
            effective_amount = (
                amount * unit_weight if (unit_weight and amount < 20) else amount
            )

            if cost_pu:
                cost_acc[ing_name] += cost_pu * effective_amount * units_sold

            if pack_unit in ("g", "kg") or unit_weight:
                kg_acc[ing_name] += (effective_amount / 1000) * units_sold
            elif pack_unit in ("l", "ml"):
                # Liquid ingredients: recipe amounts are in ml → litres
                litre_acc[ing_name] += (amount / 1000) * units_sold
            elif pack_unit == "units":
                # Pure unit-count ingredients (eggs etc.) → raw count
                unit_acc[ing_name] += amount * units_sold
            # null pack_unit → cost only, no volume tracked

    if not cost_acc:
        st.info("No se pudo calcular el consumo — sin coincidencias de ingredientes.")
        return

    n = st.slider("Top N ingredientes", 4, 20, 8, key="ing_n")

    # Build main dataframe sorted by cost
    all_ingredients = sorted(cost_acc.keys(), key=lambda k: cost_acc[k], reverse=True)
    df = pd.DataFrame({
        "ingredient":   all_ingredients,
        "est_cost_eur": [cost_acc[k] for k in all_ingredients],
        "kg":           [kg_acc.get(k, 0) for k in all_ingredients],
        "litres":       [litre_acc.get(k, 0) for k in all_ingredients],
    })

    # ── Match quality ──────────────────────────────────────────────────────────
    ma, mb, mc = st.columns(3)
    ma.metric("SKU exacto",          n_exact)
    mb.metric("Coincidencia aprox.", n_fuzzy)
    mc.metric("Sin identificar",     n_unmatched,
              help="Incluidos en ingresos pero excluidos del cálculo de ingredientes")
    st.divider()

    # ── Chart row: cost + kg/L volume ─────────────────────────────────────────
    top_cost = df.head(n)

    # Volume chart: combine kg and litres with a type label
    vol_rows = []
    for k in all_ingredients:
        if kg_acc.get(k, 0) > 0:
            vol_rows.append({"ingredient": k, "amount": kg_acc[k], "unit": "kg"})
        if litre_acc.get(k, 0) > 0:
            vol_rows.append({"ingredient": k, "amount": litre_acc[k], "unit": "L"})
    df_vol = (pd.DataFrame(vol_rows)
              .sort_values("amount", ascending=False)
              .head(n) if vol_rows else pd.DataFrame())

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**Por coste estimado (€)**")
        st.altair_chart(
            alt.Chart(top_cost)
            .mark_bar(color=C_RED, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
            .encode(
                x=alt.X("est_cost_eur:Q", axis=alt.Axis(title="€", format=",.2f")),
                y=alt.Y("ingredient:N", sort="-x", axis=alt.Axis(title=None)),
                tooltip=[alt.Tooltip("ingredient:N", title="Ingrediente"),
                         alt.Tooltip("est_cost_eur:Q", title="€", format=",.2f")],
            )
            .properties(height=max(200, n * 32)),
            use_container_width=True
        )

    with c2:
        st.markdown("**Por volumen/peso (kg y L)**")
        if df_vol.empty:
            st.info("Sin datos de volumen.")
        else:
            st.altair_chart(
                alt.Chart(df_vol)
                .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                .encode(
                    x=alt.X("amount:Q", axis=alt.Axis(title="Cantidad", format=",.3f")),
                    y=alt.Y("ingredient:N", sort="-x", axis=alt.Axis(title=None)),
                    color=alt.Color("unit:N",
                                    scale=alt.Scale(
                                        domain=["kg", "L"],
                                        range=[C_BLUE, C_LIGHT]
                                    ),
                                    legend=alt.Legend(title="Unidad")),
                    tooltip=[alt.Tooltip("ingredient:N", title="Ingrediente"),
                             alt.Tooltip("amount:Q", format=",.3f"),
                             alt.Tooltip("unit:N", title="Unidad")],
                )
                .properties(height=max(200, n * 32)),
                use_container_width=True
            )

    # ── Unit-count ingredients (eggs etc.) ────────────────────────────────────
    if unit_acc:
        st.divider()
        st.markdown("**Ingredientes por unidades**")
        unit_df = pd.DataFrame([
            {"Ingrediente": k, "Unidades": f"{v:,.0f} ud."}
            for k, v in sorted(unit_acc.items(), key=lambda x: x[1], reverse=True)
        ])
        st.dataframe(unit_df, hide_index=True, width='stretch')

    # ── Full detail table ──────────────────────────────────────────────────────
    st.divider()
    with st.expander("Ver tabla completa"):
        def _fmt_vol(row):
            if row["kg"] > 0:
                return f"{row['kg']:.3f} kg"
            if row["litres"] > 0:
                return f"{row['litres']:.3f} L"
            k = row["ingredient"]
            if unit_acc.get(k, 0) > 0:
                return f"{unit_acc[k]:,.0f} ud."
            return "—"

        display = df.copy()
        display["Consumo"] = display.apply(_fmt_vol, axis=1)
        display["est_cost_eur"] = display["est_cost_eur"].map(lambda x: f"€{x:,.2f}")
        display = display[["ingredient", "est_cost_eur", "Consumo"]]
        display.columns = ["Ingrediente", "Coste est. (€)", "Consumo"]
        st.dataframe(display, hide_index=True, width='stretch')

    st.caption(
        "⚠️ Aproximación: usa la receta de referencia sin escalar por tamaño. "
        "Kg y L no son comparables — se muestran en gráficos separados por color. "
        "Útil para ranking relativo y planificación de compras."
    )

    # ── Mapping audit ─────────────────────────────────────────────────────────
    with st.expander("🔍 Ver mapeo de productos → recetas"):
        audit_rows = []
        for row in product_rows:
            recipe_id, match_type = _match_recipe(
                row.get("sku"), row["product_name"],
                sku_map, recipe_names, name_id_map,
                name_to_sku=name_to_sku,
            )
            score = None
            if match_type == "fuzzy" and recipe_names:
                result = process.extractOne(
                    row["product_name"], recipe_names,
                    scorer=fuzz.token_sort_ratio
                )
                if result:
                    score = result[1]

            matched_recipe = None
            if recipe_id:
                matched_recipe = next(
                    (r["name"] for r in recipes if r["id"] == recipe_id), recipe_id
                )

            audit_rows.append({
                "Producto Holded":  row["product_name"],
                "Receta mapeada":   matched_recipe or "— sin mapeo —",
                "Tipo":             match_type,
                "Score":            f"{score:.0f}" if score else ("SKU" if match_type == "exact" else "—"),
                "Uds vendidas":     float(row["units"]),
            })

        adf = pd.DataFrame(audit_rows)

        def _highlight(row):
            if row["Tipo"] == "fuzzy":
                try:
                    s = float(row["Score"])
                    if s < 85:
                        return ["background-color: #fff3cd"] * len(row)
                    return ["background-color: #d4edda"] * len(row)
                except:
                    pass
            if row["Tipo"] == "none":
                return ["background-color: #f8d7da"] * len(row)
            return [""] * len(row)

        st.dataframe(
            adf.style.apply(_highlight, axis=1),
            hide_index=True,
            width='stretch'
        )
        st.caption(
            "🟡 Amarillo = coincidencia aproximada con score bajo (<85) — revisar. "
            "🔴 Rojo = sin mapeo. 🟢 Verde = coincidencia aproximada con buen score."
        )

# =============================================================================
# Tab 4 — Data Management
# =============================================================================

def _tab_data():
    st.markdown("### Gestión de datos — subida de ficheros de Holded")

    # ── Freshness check ────────────────────────────────────────────────────────
    is_fresh, msg = _check_data_freshness()
    if is_fresh:
        st.success(msg)
    else:
        st.warning(msg)

    st.divider()

    # ── Upload widget ──────────────────────────────────────────────────────────
    st.markdown(
        "Descarga los dos informes desde Holded → **Ventas** → **Informes** "
        "y súbelos aquí. Puedes subir cualquier año — los datos se añaden sin borrar lo anterior."
    )

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**1. Fichero de Ventas mensuales**")
        st.caption("Holded → Ventas → Informes → Ventas → Exportar Excel")
        file_ventas = st.file_uploader(
            "Ventas (ingresos por mes)",
            type=["xlsx"],
            key="upload_ventas",
        )

    with col2:
        st.markdown("**2. Fichero de Ventas por producto**")
        st.caption("Holded → Ventas → Informes → Ventas por producto → "
                   "seleccionar **Unidades** → Exportar Excel")
        file_productos = st.file_uploader(
            "Ventas por producto (unidades por mes)",
            type=["xlsx"],
            key="upload_productos",
        )

    if st.button("⬆️ Subir ficheros de ventas", type="primary",
                 disabled=(file_ventas is None and file_productos is None)):
        errors = []

        if file_ventas:
            try:
                rows = db.parse_ventas_excel(file_ventas.read())
                n    = db.upsert_monthly_revenue(rows)
                st.success(f"✓ Ingresos: {n} meses subidos "
                           f"({rows[0]['year'] if rows else '?'}–{rows[-1]['year'] if rows else '?'})")
            except Exception as e:
                errors.append(f"Fichero de Ventas: {e}")

        if file_productos:
            try:
                rows = db.parse_productos_excel(file_productos.read())
                n    = db.upsert_monthly_products(rows)
                st.success(f"✓ Productos: {n} filas de producto/mes subidas")
            except Exception as e:
                errors.append(f"Fichero de Productos: {e}")

        for err in errors:
            st.error(err)

        if not errors:
            st.cache_data.clear()
            st.rerun()

    st.divider()

    # ── Inventory / product catalogue upload ──────────────────────────────────
    st.markdown("#### Catálogo de productos (Inventario)")
    st.caption(
        "Sube el fichero de inventario de Holded para mapear nombres de productos a SKUs. "
        "Actualiza cuando añadas nuevos productos. "
        "Holded → Inventario → Exportar Excel."
    )
    file_inv = st.file_uploader(
        "Inventario Holded (SKU + nombre)",
        type=["xlsx"],
        key="upload_inventory",
    )
    if st.button("⬆️ Subir inventario", type="primary", disabled=file_inv is None):
        try:
            rows = db.parse_inventory_excel(file_inv.read())
            n    = db.upsert_holded_products(rows)
            st.success(f"✓ Catálogo actualizado: {n} productos subidos")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Error al procesar inventario: {e}")

    # Show current catalogue summary
    products = db.get_holded_products()
    if products:
        st.caption(f"Catálogo actual: {len(products)} productos activos")
        with st.expander("Ver catálogo completo"):
            pdf = pd.DataFrame(products)[["sku", "name", "price_ex_vat"]]
            pdf.columns = ["SKU", "Nombre", "Precio ex-IVA"]
            pdf["Precio ex-IVA"] = pdf["Precio ex-IVA"].map(
                lambda x: f"€{x:.2f}" if x else "—"
            )
            st.dataframe(pdf, hide_index=True, width='stretch')

    st.divider()

    # ── Current data status ────────────────────────────────────────────────────
    st.markdown("#### Datos actualmente en la base de datos")

    rev_rows = db.get_monthly_revenue()
    if not rev_rows:
        st.info("Sin datos subidos todavía.")
        return

    # Summary by year
    by_year: dict[int, dict] = defaultdict(
        lambda: {"months": 0, "ventas": 0.0, "units": 0.0}
    )
    for r in rev_rows:
        by_year[r["year"]]["months"] += 1
        by_year[r["year"]]["ventas"] += float(r["ventas_ex_vat"])
        by_year[r["year"]]["units"]  += float(r["units"])

    summary = pd.DataFrame([
        {
            "Año":            str(year),
            "Meses":          v["months"],
            "Ventas ex-IVA":  f"€{v['ventas']:,.2f}",
            "Unidades":       f"{v['units']:,.0f}",
        }
        for year, v in sorted(by_year.items())
    ])
    st.dataframe(summary, hide_index=True, width='stretch')

    # API supplement status
    supp = holded.get_current_month_supplement()
    synced = holded.last_synced()
    today  = date.today()
    cy, cm = today.year, today.month

    st.divider()
    st.markdown("#### Suplemento API (mes actual)")
    if (cy, cm) not in {(r["year"], r["month"]) for r in rev_rows}:
        a1, a2, a3 = st.columns(3)
        a1.metric(f"Ingresos {_month_label(cy, cm)} vía API",
                  f"€{supp['revenue']:,.2f}")
        a2.metric("Documentos descargados", supp["doc_count"])
        a3.metric("Última sync", synced or "no sincronizado")
        if supp["note"]:
            st.caption(supp["note"])
        if st.button("🔄 Refrescar API", key="refresh_api"):
            holded.get_current_month_supplement(force_refresh=True)
            st.rerun()
    else:
        st.caption(
            f"✓ {_month_label(cy, cm)} ya está cubierto por datos subidos — "
            "API no se usa para evitar doble conteo."
        )


# =============================================================================
# Main screen
# =============================================================================

def screen_kpis():
    st.title("Business KPIs")

    # Freshness banner at top level
    is_fresh, msg = _check_data_freshness()
    if not is_fresh:
        st.warning(msg)

    tabs = st.tabs([
        "📈 Ingresos vs Objetivo",
        "🎂 Top Productos",
        "🧂 Ingredientes",
        "📁 Gestión de datos",
    ])

    with tabs[0]:
        _tab_revenue()
    with tabs[1]:
        _tab_products()
    with tabs[2]:
        _tab_ingredients()
    with tabs[3]:
        _tab_data()
