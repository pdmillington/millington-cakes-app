# screen_orders.py
# =============================================================================
# Pedidos — combined Shopify (retail) + Holded presupuestos (wholesale).
#
# Layout:
#   1. Production matrix  — products × days, with prep hours as final row
#   2. Orders by day      — individual order detail grouped by delivery date
# =============================================================================

from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import millington_db as db
from shopify_api        import get_open_orders, last_synced as shopify_synced
from holded_api         import get_estimates,   estimates_last_synced
from core.settings      import load_settings
from core.pricing_engine import calc_labour_cost

_LOCALE_DAYS = {
    0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves",
    4: "Viernes", 5: "Sábado", 6: "Domingo",
}
_LOCALE_MONTHS = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def _fmt_date(d: date) -> str:
    return f"{_LOCALE_DAYS[d.weekday()]} {d.day} {_LOCALE_MONTHS[d.month]}"


def _source_badge(source: str) -> str:
    return "🛒" if source == "Shopify" else "📋"


def _match_recipe(product: str, recipe_map: dict) -> tuple | None:
    """Return (recipe, format_key) or None."""
    clean      = product.lower().split("(")[0].strip()
    name_lower = product.lower()
    recipe     = next(
        (r for name, r in recipe_map.items() if clean in name or name in clean),
        None,
    )
    if not recipe:
        return None
    if "bocado" in name_lower or "bite" in name_lower:
        fmt = "bocado"
    elif "individual" in name_lower or "tartaleta" in name_lower:
        fmt = "individual"
    else:
        fmt = "standard"
    return recipe, fmt


def screen_orders():
    st.title("Pedidos")
    st.caption("Shopify (retail) + Holded presupuestos (mayorista) — próximos 7 días")

    # ── Controls ──────────────────────────────────────────────────────────────
    col_a, col_b, col_c = st.columns([1, 1, 4])
    with col_a:
        refresh  = st.button("🔄 Actualizar", use_container_width=True)
    with col_b:
        show_all = st.toggle("Ver todos", value=False, key="orders_show_all")

    # ── Load data ─────────────────────────────────────────────────────────────
    shopify_orders = get_open_orders(force_refresh=refresh)
    holded_orders  = get_estimates(force_refresh=refresh)

    shopify_err = st.session_state.get("_shopify_error")
    holded_err  = st.session_state.get("_holded_estimates_error")
    if shopify_err:
        st.warning(f"⚠️ Shopify: {shopify_err}")
    if holded_err:
        st.warning(f"⚠️ Holded: {holded_err}")

    ss = shopify_synced()
    hs = estimates_last_synced()
    st.caption(
        f"🛒 Shopify: {ss or 'not loaded'}  ·  "
        f"📋 Holded: {hs or 'not loaded'}  ·  "
        f"{len(shopify_orders)} retail + {len(holded_orders)} mayorista"
    )

    st.divider()

    # ── Date filtering ────────────────────────────────────────────────────────
    today      = date.today()
    next_week  = today + timedelta(days=7)
    all_orders = shopify_orders + holded_orders

    def _in_range(order: dict) -> bool:
        d = order.get("due_date") or order.get("order_date")
        return (today <= d <= next_week) if d else show_all

    def _date_key(order: dict) -> date:
        return order.get("due_date") or order.get("order_date") or date.max

    visible = [o for o in all_orders if show_all or _in_range(o)]

    if not visible:
        st.info(
            "No hay pedidos abiertos para los próximos 7 días. "
            "Activa 'Ver todos' para ver todos los pedidos abiertos."
        )
        return

    # ── Build product × day quantities ────────────────────────────────────────
    product_by_day: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for order in visible:
        d = _date_key(order)
        for line in order["lines"]:
            name = line["name"]
            if line["variant"]:
                name = f"{name} ({line['variant']})"
            if name:
                product_by_day[name][d] += line["quantity"]

    days       = sorted({d for dm in product_by_day.values() for d in dm})
    day_labels = [(_fmt_date(d) if d != date.max else "Sin fecha") for d in days]

    # ── Prep hours per product per day ────────────────────────────────────────
    recipes    = db.get_recipes()
    s          = load_settings()
    recipe_map = {r["name"].lower(): r for r in recipes}

    prep_by_day: dict[date, float] = defaultdict(float)
    unmatched: list[str] = []

    for product, day_qtys in product_by_day.items():
        match = _match_recipe(product, recipe_map)
        if not match:
            unmatched.append(product)
            continue
        recipe, fmt = match

        if fmt == "bocado":
            prep_h    = recipe.get("bocado_batch_prep_hours") or recipe.get("ref_prep_hours") or 0
            ref_batch = s.ws_batch_bocado
            batch_sz  = s.ws_batch_bocado
        elif fmt == "individual":
            prep_h    = recipe.get("small_batch_prep_hours") or recipe.get("ref_prep_hours") or 0
            ref_batch = s.ws_batch_individual
            batch_sz  = s.ws_batch_individual
        else:
            prep_h    = recipe.get("ref_prep_hours") or 0
            ref_batch = float(recipe.get("ref_batch_size") or 20)
            batch_sz  = s.ws_batch_large

        labour = calc_labour_cost(batch_sz, ref_batch, prep_h, 0, s)
        # bocado qty from orders is in boxes (e.g. 1 box = rt_batch_bocado units).
        # prep_per_unit is per individual bocado, so scale up accordingly.
        units_per_order_qty = s.rt_batch_bocado if fmt == "bocado" else 1
        for d, qty in day_qtys.items():
            prep_by_day[d] += labour.prep_per_unit * qty * units_per_order_qty

    # ── Production matrix ─────────────────────────────────────────────────────
    st.markdown("### Resumen de producción")

    # All cells must be the same type (str) to avoid Arrow serialisation errors.
    rows: dict[str, list] = {}
    for product in sorted(product_by_day.keys()):
        row   = []
        total = 0.0
        for d in days:
            qty = product_by_day[product].get(d, 0)
            total += qty
            row.append(str(int(qty)) if qty else "")
        row.append(str(int(total)))
        rows[product] = row

    # Prep row
    prep_row   = []
    total_prep = 0.0
    for d in days:
        h = prep_by_day.get(d, 0.0)
        total_prep += h
        prep_row.append(f"{h:.1f}h" if h else "")
    prep_row.append(f"{total_prep:.1f}h")
    rows["⏱ Prep (h)"] = prep_row

    df = pd.DataFrame(rows, index=day_labels + ["Total"]).T
    st.dataframe(df, width="stretch")

    if unmatched:
        st.caption(
            "⚠️ Sin receta coincidente (excluidos del cálculo de prep): "
            + ", ".join(sorted(unmatched))
        )

    st.divider()

    # ── Orders by day ─────────────────────────────────────────────────────────
    by_date: dict[date, list[dict]] = defaultdict(list)
    for order in visible:
        by_date[_date_key(order)].append(order)

    for day in sorted(by_date.keys()):
        label   = _fmt_date(day) if day != date.max else "Sin fecha"
        is_past = day < today
        prefix  = "⚠️ " if is_past else ""
        st.markdown(f"#### {prefix}{label}")

        for order in sorted(by_date[day], key=lambda o: o["source"]):
            badge  = _source_badge(order["source"])
            c1, c2 = st.columns([1, 4])
            with c1:
                st.markdown(f"{badge} **{order['ref']}**")
                st.caption(order["client"])
            with c2:
                for line in order["lines"]:
                    qty  = int(line["quantity"]) if line["quantity"] == int(line["quantity"]) else line["quantity"]
                    var  = f" — {line['variant']}" if line["variant"] else ""
                    sku  = f" `{line['sku']}`"     if line["sku"]     else ""
                    st.markdown(f"× {qty}  {line['name']}{var}{sku}")
                if order.get("note"):
                    st.caption(f"📝 {order['note']}")

        st.divider()
