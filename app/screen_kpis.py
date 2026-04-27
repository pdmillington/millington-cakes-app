# screen_kpis.py
# =============================================================================
# Business KPI dashboard — powered by Holded (sales) + Supabase (recipes).
#
# Four tabs:
#   1. Revenue vs Target  — monthly actuals + stepped contractual target
#   2. Top Clients        — YTD top 5 vs same period last year
#   3. New Clients        — new contacts by month
#   4. Ingredient Spend   — estimated ingredient cost + weight from sales
#
# Target model (Blanca participation conditions, starting Apr 2026):
#   Baseline   = total revenue Apr 2025 – Mar 2026
#   Base       = any growth             → 10 % capital social
#   Alto Rend. = ≥ 2× baseline annual  → 15 % capital social
#
#   Monthly targets use a ramp so the average hits 2.0× over the year:
#     Months  1–2  (Apr–May 26): 1.2× of monthly baseline
#     Months  3–4  (Jun–Jul 26): 1.6×
#     Months  5–6  (Aug–Sep 26): 2.0×
#     Months  7–12 (Oct 26–Mar 27): 2.4×
#   Weighted average: (2×1.2 + 2×1.6 + 2×2.0 + 6×2.4) / 12 = 2.0×  ✓
# =============================================================================

import calendar
from collections import defaultdict
from datetime import datetime, date, timezone

import altair as alt
import pandas as pd
import streamlit as st
from rapidfuzz import process, fuzz

import holded_api as holded
import millington_db as db


# =============================================================================
# Constants
# =============================================================================

# Target year: Apr 2026 → Mar 2027 (month index 0 = April 2026)
TARGET_START    = date(2026, 4, 1)
TARGET_END      = date(2027, 3, 31)
BASELINE_START  = date(2025, 4, 1)
BASELINE_END    = date(2026, 3, 31)

# Monthly multipliers over baseline/12 (index 0 = April 2026)
TARGET_RAMP = [1.2, 1.2, 1.6, 1.6, 2.0, 2.0, 2.4, 2.4, 2.4, 2.4, 2.4, 2.4]

ALTO_THRESHOLD = 2.0   # ≥ 2× baseline annual = Alto Rendimiento

FUZZY_THRESHOLD = 78   # minimum score for a name → recipe fuzzy match


# =============================================================================
# Data helpers
# =============================================================================

def _ts_to_date(ts: int | float) -> date:
    """Convert a Unix timestamp (Holded format) to a date object."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def _month_label(d: date) -> str:
    return d.strftime("%b %Y")


def _filter_by_period(items: list[dict], start: date, end: date) -> list[dict]:
    """Keep line items whose invoice date falls within [start, end]."""
    return [
        r for r in items
        if start <= _ts_to_date(r["date"]) <= end
    ]


def _revenue_by_month(line_items: list[dict]) -> dict[str, float]:
    """
    Return {month_label: revenue} for all product line items.
    Delivery / non-product lines (is_product=False) are excluded.
    """
    totals: dict[str, float] = defaultdict(float)
    for row in line_items:
        if not row["is_product"]:
            continue
        d = _ts_to_date(row["date"])
        totals[_month_label(d)] += row["line_revenue"]
    return dict(totals)


def _build_sku_map(skus: list[dict]) -> dict[str, str]:
    """Return {sku_code: recipe_id} for all known SKUs."""
    return {s["sku_code"]: s["recipe_id"] for s in skus if s.get("sku_code") and s.get("recipe_id")}


def _build_fuzzy_map(recipes: list[dict]) -> tuple[list[str], dict[str, str]]:
    """Return (recipe_names, {recipe_name: recipe_id}) for fuzzy matching."""
    names   = [r["name"] for r in recipes if r.get("name")]
    name_id = {r["name"]: r["id"] for r in recipes if r.get("name") and r.get("id")}
    return names, name_id


def _match_recipe(sku: str, name: str,
                  sku_map: dict[str, str],
                  recipe_names: list[str],
                  name_id_map: dict[str, str]) -> tuple[str | None, str]:
    """
    Resolve a line item to a recipe_id.
    Returns (recipe_id | None, match_type) where match_type is one of:
      'exact'  — SKU matched directly
      'fuzzy'  — name matched via rapidfuzz
      'none'   — no match found
    """
    if sku and sku in sku_map:
        return sku_map[sku], "exact"

    if name and recipe_names:
        result = process.extractOne(
            name, recipe_names,
            scorer=fuzz.token_sort_ratio,
        )
        if result and result[1] >= FUZZY_THRESHOLD:
            recipe_name = result[0]
            return name_id_map.get(recipe_name), "fuzzy"

    return None, "none"


@st.cache_data(ttl=300)
def _get_ingredient_lines() -> list[dict]:
    """
    Return all recipe ingredient lines joined with ingredient data.
    Cached 5 min to avoid repeated Supabase round-trips.
    """
    return db.get_ingredient_lines_all()


def _compute_ingredient_spend(
    line_items:   list[dict],
    sku_map:      dict[str, str],
    recipe_names: list[str],
    name_id_map:  dict[str, str],
    ing_lines:    list[dict],
) -> tuple[pd.DataFrame, int, int]:
    """
    Aggregate estimated ingredient cost and weight from sold line items.

    Returns:
      df          — DataFrame with columns: ingredient, est_cost_eur, weight_kg
      n_exact     — number of line items matched by exact SKU
      n_fuzzy     — number of line items matched by fuzzy name
    """
    # Build recipe → ingredient lines lookup
    recipe_ing: dict[str, list[dict]] = defaultdict(list)
    for il in ing_lines:
        recipe_ing[il["recipe_id"]].append(il)

    cost_acc:   dict[str, float] = defaultdict(float)
    weight_acc: dict[str, float] = defaultdict(float)
    n_exact = n_fuzzy = 0

    for row in line_items:
        if not row["is_product"]:
            continue
        recipe_id, match_type = _match_recipe(
            row["sku"], row["name"], sku_map, recipe_names, name_id_map
        )
        if not recipe_id:
            continue
        if match_type == "exact":
            n_exact += 1
        else:
            n_fuzzy += 1

        units_sold = row["units"]
        for il in recipe_ing.get(recipe_id, []):
            ing_name     = il.get("ingredient_name", "Unknown")
            amount       = float(il.get("amount") or 0)
            cost_pu      = float(il.get("cost_per_unit") or 0)
            pack_unit    = (il.get("pack_unit") or "").lower()

            cost_acc[ing_name]   += amount * cost_pu * units_sold

            # Weight: convert to kg where possible
            if pack_unit == "g":
                weight_acc[ing_name] += (amount / 1000) * units_sold
            elif pack_unit == "kg":
                weight_acc[ing_name] += amount * units_sold

    if not cost_acc:
        return pd.DataFrame(columns=["ingredient", "est_cost_eur", "weight_kg"]), 0, 0

    df = pd.DataFrame({
        "ingredient":   list(cost_acc.keys()),
        "est_cost_eur": list(cost_acc.values()),
        "weight_kg":    [weight_acc.get(k, 0) for k in cost_acc.keys()],
    }).sort_values("est_cost_eur", ascending=False).reset_index(drop=True)

    return df, n_exact, n_fuzzy


# =============================================================================
# Target helpers
# =============================================================================

def _monthly_targets(baseline_annual: float) -> list[dict]:
    """
    Return a list of 12 dicts {month_label, target, multiplier} for the
    target year Apr 2026 – Mar 2027.
    """
    monthly_base = baseline_annual / 12
    rows = []
    for i, mult in enumerate(TARGET_RAMP):
        # Month offset from April 2026
        month_num = (TARGET_START.month - 1 + i) % 12 + 1
        year      = TARGET_START.year + (TARGET_START.month - 1 + i) // 12
        d         = date(year, month_num, 1)
        rows.append({
            "month_label": _month_label(d),
            "month_date":  d,
            "target":      monthly_base * mult,
            "multiplier":  mult,
        })
    return rows


def _scenario_status(ytd_revenue: float, ytd_target_sum: float,
                     baseline_annual: float) -> tuple[str, str]:
    """
    Return (scenario_label, colour) based on annualised run rate
    vs Alto Rendimiento threshold.
    """
    if baseline_annual <= 0:
        return "Sin datos base", "gray"

    ratio = ytd_revenue / baseline_annual if baseline_annual > 0 else 0

    if ratio >= ALTO_THRESHOLD:
        return "🏆 Alto Rendimiento", "green"
    elif ytd_revenue >= ytd_target_sum * 0.9:
        return "✅ En camino", "blue"
    else:
        return "⚠️ Por debajo del objetivo", "orange"


# =============================================================================
# Tab renderers
# =============================================================================

def _tab_revenue(line_items: list[dict], baseline_annual: float):
    st.markdown("### Ingresos mensuales vs objetivo")

    if baseline_annual <= 0:
        st.warning(
            "No se encontraron datos del período base (Abr 2025 – Mar 2026) "
            "en Holded. Conecta más histórico para activar el objetivo contractual."
        )
        return

    targets      = _monthly_targets(baseline_annual)
    target_by_ml = {t["month_label"]: t["target"] for t in targets}

    # ── Actual revenue by month (all time, product lines only) ────────────────
    all_monthly = _revenue_by_month(line_items)

    # Build a unified month list: baseline year + target year
    all_months = sorted(
        set(list(all_monthly.keys()) + list(target_by_ml.keys())),
        key=lambda ml: datetime.strptime(ml, "%b %Y")
    )

    chart_rows = []
    for ml in all_months:
        d      = datetime.strptime(ml, "%b %Y").date()
        actual = all_monthly.get(ml, None)
        target = target_by_ml.get(ml, None)
        period = (
            "Período base"  if BASELINE_START <= d <= BASELINE_END else
            "Año objetivo"  if TARGET_START   <= d <= TARGET_END   else
            "Otro"
        )
        chart_rows.append({
            "Mes":           ml,
            "mes_date":      d.isoformat(),
            "Ingresos (€)":  actual,
            "Objetivo (€)":  target,
            "Período":       period,
        })

    df = pd.DataFrame(chart_rows)
    df_actual = df.dropna(subset=["Ingresos (€)"])
    df_target = df.dropna(subset=["Objetivo (€)"])

    # ── KPI header metrics ─────────────────────────────────────────────────────
    today         = date.today()
    ytd_items     = _filter_by_period(line_items, TARGET_START, today)
    ytd_revenue   = sum(r["line_revenue"] for r in ytd_items if r["is_product"])

    elapsed_months = max(1, (today.year - TARGET_START.year) * 12 +
                            (today.month - TARGET_START.month) + 1)
    ytd_target_sum = sum(t["target"] for t in targets[:elapsed_months])
    pct_of_target  = (ytd_revenue / ytd_target_sum * 100) if ytd_target_sum > 0 else 0
    label, _colour = _scenario_status(ytd_revenue, ytd_target_sum, baseline_annual)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Base anual (Abr25–Mar26)", f"€{baseline_annual:,.0f}")
    m2.metric("Ingresos YTD (desde Abr26)", f"€{ytd_revenue:,.0f}")
    m3.metric("Objetivo YTD acumulado", f"€{ytd_target_sum:,.0f}",
              delta=f"{pct_of_target - 100:+.0f}%")
    m4.metric("Escenario actual", label)

    st.divider()

    # ── Alto Rendimiento progress bar ─────────────────────────────────────────
    alto_threshold_annual = baseline_annual * ALTO_THRESHOLD
    # Annualise current YTD run rate
    months_elapsed    = max(1, elapsed_months)
    annualised        = ytd_revenue / months_elapsed * 12
    pct_to_alto       = min(annualised / alto_threshold_annual * 100, 100) \
                        if alto_threshold_annual > 0 else 0

    st.caption(
        f"Umbral Alto Rendimiento (2× base): **€{alto_threshold_annual:,.0f}** — "
        f"ritmo anualizado actual: **€{annualised:,.0f}** "
        f"({pct_to_alto:.0f}% del umbral)"
    )
    st.progress(pct_to_alto / 100)

    st.divider()

    # ── Altair chart: bars (actual) + stepped line (target) ───────────────────
    if df_actual.empty:
        st.info("No hay datos de ingresos aún para este período.")
        return

    colour_scale = alt.Scale(
        domain=["Período base", "Año objetivo", "Otro"],
        range=["#9BB0C5",       "#3A7FBF",       "#CBD5E0"]
    )

    bars = (
        alt.Chart(df_actual)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("Mes:N",
                    sort=all_months,
                    axis=alt.Axis(labelAngle=-45, title=None)),
            y=alt.Y("Ingresos (€):Q",
                    axis=alt.Axis(title="€ ex-IVA", format=",.0f")),
            color=alt.Color("Período:N",
                            scale=colour_scale,
                            legend=alt.Legend(title="Período")),
            tooltip=[
                alt.Tooltip("Mes:N"),
                alt.Tooltip("Ingresos (€):Q", format=",.2f"),
                alt.Tooltip("Período:N"),
            ],
        )
    )

    target_line = (
        alt.Chart(df_target)
        .mark_line(color="#E8413C", strokeWidth=2.5, strokeDash=[6, 3],
                   interpolate="step-after", point=True)
        .encode(
            x=alt.X("Mes:N", sort=all_months),
            y=alt.Y("Objetivo (€):Q"),
            tooltip=[
                alt.Tooltip("Mes:N"),
                alt.Tooltip("Objetivo (€):Q", format=",.2f"),
            ],
        )
    )

    chart = (bars + target_line).properties(height=380).configure_axis(
        labelFontSize=11, titleFontSize=12
    )
    st.altair_chart(chart, use_container_width=True)

    st.caption(
        "📍 Línea roja discontinua = objetivo mensual. "
        "Rampa contractual: 1.2× | 1.6× | 2.0× | 2.4× de la base mensual. "
        "Ingresos ex-IVA. Líneas de entrega excluidas."
    )

    # ── Target ramp table ─────────────────────────────────────────────────────
    with st.expander("Ver desglose del objetivo mes a mes"):
        tdf = pd.DataFrame(targets)[["month_label", "multiplier", "target"]]
        tdf.columns = ["Mes", "Multiplicador", "Objetivo (€)"]
        tdf["Objetivo (€)"] = tdf["Objetivo (€)"].map(lambda x: f"€{x:,.2f}")
        tdf["Multiplicador"] = tdf["Multiplicador"].map(lambda x: f"{x}×")
        st.dataframe(tdf, hide_index=True, use_container_width=True)


def _tab_top_clients(line_items: list[dict]):
    st.markdown("### Top 5 clientes — ingresos acumulados")

    today     = date.today()
    ytd_start = TARGET_START       # Apr 2026
    ytd_end   = today

    # Same period last year
    lyr_start = date(ytd_start.year - 1, ytd_start.month, ytd_start.day)
    lyr_end   = date(ytd_end.year   - 1, ytd_end.month,   ytd_end.day)

    def client_totals(items: list[dict]) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for r in items:
            if r["is_product"]:
                totals[r["contact_name"]] += r["line_revenue"]
        return dict(totals)

    ytd_totals = client_totals(_filter_by_period(line_items, ytd_start, ytd_end))
    lyr_totals = client_totals(_filter_by_period(line_items, lyr_start, lyr_end))

    if not ytd_totals:
        st.info("Sin datos de ventas desde Abril 2026.")
        return

    top5 = sorted(ytd_totals.items(), key=lambda x: x[1], reverse=True)[:5]

    rows = []
    for client, ytd_rev in top5:
        lyr_rev = lyr_totals.get(client, 0)
        change  = ((ytd_rev - lyr_rev) / lyr_rev * 100) if lyr_rev > 0 else None
        rows.append({
            "Cliente":         client,
            "YTD 2026 (€)":    ytd_rev,
            "YTD 2025 (€)":    lyr_rev if lyr_rev > 0 else None,
            "Variación (%)":   change,
        })

    df = pd.DataFrame(rows)

    # Altair grouped bar
    df_melt = pd.melt(
        df,
        id_vars=["Cliente"],
        value_vars=["YTD 2026 (€)", "YTD 2025 (€)"],
        var_name="Período",
        value_name="Ingresos (€)",
    ).dropna(subset=["Ingresos (€)"])

    chart = (
        alt.Chart(df_melt)
        .mark_bar()
        .encode(
            x=alt.X("Cliente:N",
                    sort=[r["Cliente"] for r in rows],
                    axis=alt.Axis(labelAngle=-20, title=None,
                                  labelLimit=200)),
            y=alt.Y("Ingresos (€):Q",
                    axis=alt.Axis(title="€ ex-IVA", format=",.0f")),
            color=alt.Color("Período:N",
                            scale=alt.Scale(
                                domain=["YTD 2026 (€)", "YTD 2025 (€)"],
                                range=["#3A7FBF", "#9BB0C5"]
                            )),
            xOffset="Período:N",
            tooltip=[
                alt.Tooltip("Cliente:N"),
                alt.Tooltip("Período:N"),
                alt.Tooltip("Ingresos (€):Q", format=",.2f"),
            ],
        )
        .properties(height=320)
    )
    st.altair_chart(chart, use_container_width=True)

    st.divider()
    st.markdown("**Detalle**")

    display_df = df.copy()
    display_df["YTD 2026 (€)"]  = display_df["YTD 2026 (€)"].map(lambda x: f"€{x:,.2f}")
    display_df["YTD 2025 (€)"]  = display_df["YTD 2025 (€)"].map(
        lambda x: f"€{x:,.2f}" if x is not None else "—"
    )
    display_df["Variación (%)"] = display_df["Variación (%)"].map(
        lambda x: f"{x:+.1f}%" if x is not None else "nuevo"
    )
    st.dataframe(display_df, hide_index=True, use_container_width=True)

    st.caption(
        f"Período YTD: {ytd_start.strftime('%d %b %Y')} → hoy  ·  "
        f"Comparativa: mismo período {lyr_start.year}. Ingresos ex-IVA, excluye entregas."
    )


def _tab_new_clients(contacts: list[dict]):
    st.markdown("### Nuevos clientes por mes")

    if not contacts:
        st.info("No se han cargado contactos de Holded.")
        return

    # Consider contacts created from Apr 2025 onwards
    rows = []
    for c in contacts:
        created_ts = c.get("createdAt")
        if not created_ts:
            continue
        d = _ts_to_date(created_ts)
        if d < BASELINE_START:
            continue
        rows.append({
            "name":  c.get("name", ""),
            "month": _month_label(d),
            "date":  d,
            "type":  c.get("type", ""),
        })

    if not rows:
        st.info("No se encontraron contactos creados desde Abril 2025.")
        return

    df = pd.DataFrame(rows)

    # Count by month
    counts = (
        df.groupby("month")
        .size()
        .reset_index(name="Nuevos clientes")
    )
    # Sort chronologically
    all_months = sorted(df["month"].unique(),
                        key=lambda m: datetime.strptime(m, "%b %Y"))
    counts["month_sort"] = counts["month"].map(
        lambda m: datetime.strptime(m, "%b %Y")
    )
    counts = counts.sort_values("month_sort")

    chart = (
        alt.Chart(counts)
        .mark_bar(color="#3A7FBF",
                  cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("month:N",
                    sort=all_months,
                    axis=alt.Axis(labelAngle=-45, title=None)),
            y=alt.Y("Nuevos clientes:Q",
                    axis=alt.Axis(title="Nuevos contactos", tickMinStep=1)),
            tooltip=[
                alt.Tooltip("month:N", title="Mes"),
                alt.Tooltip("Nuevos clientes:Q"),
            ],
        )
        .properties(height=280)
    )
    st.altair_chart(chart, use_container_width=True)

    # Cumulative line
    counts["Acumulado"] = counts["Nuevos clientes"].cumsum()
    total = counts["Nuevos clientes"].sum()
    m1, m2 = st.columns(2)
    m1.metric("Total nuevos contactos (desde Abr 25)", total)
    m2.metric("Promedio mensual", f"{total / max(len(counts), 1):.1f}")

    with st.expander("Ver detalle por mes"):
        st.dataframe(
            counts[["month", "Nuevos clientes", "Acumulado"]].rename(
                columns={"month": "Mes"}
            ),
            hide_index=True,
            use_container_width=True,
        )

    st.caption("Basado en la fecha de creación del contacto en Holded.")


def _tab_ingredients(line_items: list[dict],
                     sku_map:    dict[str, str],
                     recipe_names: list[str],
                     name_id_map:  dict[str, str]):
    st.markdown("### Ingredientes — coste estimado y peso consumido")

    # Period selector
    col_p1, col_p2, _ = st.columns([1.5, 1.5, 3])
    with col_p1:
        period_start = st.date_input("Desde", value=TARGET_START,
                                     key="ing_start")
    with col_p2:
        period_end = st.date_input("Hasta", value=date.today(),
                                   key="ing_end")

    filtered = _filter_by_period(line_items, period_start, period_end)

    n_items  = st.slider("Mostrar top N ingredientes", 4, 15, 8, key="ing_top_n")

    with st.spinner("Calculando consumo de ingredientes…"):
        ing_lines           = _get_ingredient_lines()
        df, n_exact, n_fuzzy = _compute_ingredient_spend(
            filtered, sku_map, recipe_names, name_id_map, ing_lines
        )

    if df.empty:
        st.info("No hay datos suficientes para calcular el consumo de ingredientes.")
        return

    n_unmatched = sum(
        1 for r in filtered
        if r["is_product"] and _match_recipe(
            r["sku"], r["name"], sku_map, recipe_names, name_id_map
        )[1] == "none"
    )

    # ── Match quality indicator ────────────────────────────────────────────────
    ma, mb, mc = st.columns(3)
    ma.metric("Líneas con SKU exacto", n_exact)
    mb.metric("Líneas con coincidencia aproximada", n_fuzzy,
              help="Nombre de producto emparejado con receta por similitud de texto")
    mc.metric("Líneas sin identificar", n_unmatched,
              help="Excluidas del cálculo de ingredientes pero incluidas en ingresos")

    st.divider()

    top_cost   = df.head(n_items)
    top_weight = df[df["weight_kg"] > 0].nlargest(n_items, "weight_kg")

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**Por coste estimado (€)**")
        cost_chart = (
            alt.Chart(top_cost)
            .mark_bar(color="#E8413C",
                      cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
            .encode(
                x=alt.X("est_cost_eur:Q",
                         axis=alt.Axis(title="€ estimado", format=",.2f")),
                y=alt.Y("ingredient:N",
                         sort="-x",
                         axis=alt.Axis(title=None)),
                tooltip=[
                    alt.Tooltip("ingredient:N", title="Ingrediente"),
                    alt.Tooltip("est_cost_eur:Q", title="Coste est. (€)",
                                format=",.2f"),
                ],
            )
            .properties(height=max(200, n_items * 32))
        )
        st.altair_chart(cost_chart, use_container_width=True)

    with c2:
        st.markdown("**Por peso consumido (kg)**")
        if top_weight.empty:
            st.info("Sin datos de peso — asegúrate de que los ingredientes "
                    "tienen unidades g/kg en Supabase.")
        else:
            weight_chart = (
                alt.Chart(top_weight)
                .mark_bar(color="#3A7FBF",
                          cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                .encode(
                    x=alt.X("weight_kg:Q",
                             axis=alt.Axis(title="kg", format=",.2f")),
                    y=alt.Y("ingredient:N",
                             sort="-x",
                             axis=alt.Axis(title=None)),
                    tooltip=[
                        alt.Tooltip("ingredient:N", title="Ingrediente"),
                        alt.Tooltip("weight_kg:Q", title="Peso (kg)",
                                    format=",.3f"),
                    ],
                )
                .properties(height=max(200, n_items * 32))
            )
            st.altair_chart(weight_chart, use_container_width=True)

    st.divider()

    with st.expander("Ver tabla completa"):
        display = df.copy()
        display["est_cost_eur"] = display["est_cost_eur"].map(lambda x: f"€{x:,.2f}")
        display["weight_kg"]    = display["weight_kg"].map(
            lambda x: f"{x:.3f} kg" if x > 0 else "—"
        )
        display.columns = ["Ingrediente", "Coste estimado (€)", "Peso estimado (kg)"]
        st.dataframe(display, hide_index=True, use_container_width=True)

    st.caption(
        "⚠️ Cálculo aproximado: usa las cantidades de la receta de referencia "
        "sin escalar por tamaño. Útil para ranking relativo; "
        "no usar como cifra absoluta de compras."
    )


# =============================================================================
# Main screen
# =============================================================================

def screen_kpis():
    st.title("Business KPIs")
    st.caption("Datos de ventas desde Holded · Costes de recetas desde Supabase")

    # ── Sync controls ──────────────────────────────────────────────────────────
    synced = holded.last_synced()
    col_sync, col_info = st.columns([1, 4])
    with col_sync:
        force = st.button("🔄 Actualizar datos", use_container_width=True)
    with col_info:
        if synced:
            st.caption(f"Última sincronización: {synced}")
        else:
            st.caption("Datos no cargados aún — clic en Actualizar para sincronizar.")

    # ── Load data ──────────────────────────────────────────────────────────────
    try:
        invoices   = holded.get_invoices(force_refresh=force)
        contacts   = holded.get_contacts(force_refresh=force)
    except Exception as e:
        st.error(f"No se pudo conectar con Holded: {e}")
        st.info("Comprueba que HOLDED_API_KEY está configurado en st.secrets / .env")
        return

    line_items = holded.get_line_items(invoices)

    # Load Supabase data for recipe matching
    skus         = db.get_skus()
    recipes      = db.get_recipes()
    sku_map      = _build_sku_map(skus)
    recipe_names, name_id_map = _build_fuzzy_map(recipes)

    # ── Baseline revenue ───────────────────────────────────────────────────────
    baseline_items   = _filter_by_period(line_items, BASELINE_START, BASELINE_END)
    baseline_annual  = sum(
        r["line_revenue"] for r in baseline_items if r["is_product"]
    )

    # ── Tabs ───────────────────────────────────────────────────────────────────
    tabs = st.tabs([
        "📈 Ingresos vs Objetivo",
        "🏅 Top Clientes",
        "🆕 Nuevos Clientes",
        "🧂 Ingredientes",
    ])

    with tabs[0]:
        _tab_revenue(line_items, baseline_annual)

    with tabs[1]:
        _tab_top_clients(line_items)

    with tabs[2]:
        _tab_new_clients(contacts)

    with tabs[3]:
        _tab_ingredients(line_items, sku_map, recipe_names, name_id_map)
