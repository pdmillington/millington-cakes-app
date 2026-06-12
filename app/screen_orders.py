# screen_orders.py
# =============================================================================
# Pedidos — combined Shopify (retail) + Holded presupuestos (wholesale).
#
# Shows all open orders grouped by delivery date for the next 7 days,
# with a production summary (total units per product) and a weekly
# labour estimate at the bottom.
# =============================================================================

from __future__ import annotations
import math
from collections import defaultdict
from datetime import date, timedelta

import streamlit as st

import millington_db as db
import shopify_api as _shopify_mod
from shopify_api  import get_open_orders, last_synced as shopify_synced
from holded_api   import get_estimates,   estimates_last_synced
from core.settings import load_settings
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


def screen_orders():
    st.title("Pedidos")
    st.caption("Shopify (retail) + Holded presupuestos (mayorista) — próximos 7 días")

    # ── Temporary debug ───────────────────────────────────────────────────────
    with st.expander("🔧 Debug Shopify (temp)", expanded=False):
        import os
        st.write("_ENV_PATH:", _shopify_mod._ENV_PATH)
        st.write("_ENV_PATH exists:", os.path.exists(_shopify_mod._ENV_PATH))
        st.write("_ENV keys:", list(_shopify_mod._ENV.keys()))
        st.write("SHOPIFY_STORE in _ENV:", "SHOPIFY_STORE" in _shopify_mod._ENV)
        st.write("_store() value:", repr(_shopify_mod._store()))
        st.write("__file__:", _shopify_mod.__file__)

    # ── Refresh controls ──────────────────────────────────────────────────────
    col_a, col_b, col_c = st.columns([1, 1, 4])
    with col_a:
        refresh = st.button("🔄 Actualizar", use_container_width=True)
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

    # ── Date range ────────────────────────────────────────────────────────────
    today     = date.today()
    next_week = today + timedelta(days=7)

    all_orders = shopify_orders + holded_orders

    def _in_range(order: dict) -> bool:
        d = order.get("due_date") or order.get("order_date")
        if d is None:
            return show_all
        return today <= d <= next_week

    def _date_key(order: dict) -> date:
        d = order.get("due_date") or order.get("order_date")
        return d or date.max

    visible = [o for o in all_orders if show_all or _in_range(o)]

    if not visible:
        st.info(
            "No hay pedidos abiertos para los próximos 7 días. "
            "Activa 'Ver todos' para ver todos los pedidos abiertos."
        )
        return

    # ── Group by delivery date ────────────────────────────────────────────────
    by_date: dict[date, list[dict]] = defaultdict(list)
    for order in visible:
        key = _date_key(order)
        by_date[key].append(order)

    # ── Orders by day ─────────────────────────────────────────────────────────
    for day in sorted(by_date.keys()):
        orders_today = by_date[day]
        label        = _fmt_date(day) if day != date.max else "Sin fecha"
        is_today     = (day == today)
        is_past      = (day < today)

        prefix = "📅 " if is_today else ("⚠️ " if is_past else "")
        st.markdown(f"#### {prefix}{label}")

        for order in sorted(orders_today, key=lambda o: o["source"]):
            badge  = _source_badge(order["source"])
            ref    = order["ref"]
            client = order["client"]
            lines  = order["lines"]

            with st.container():
                c1, c2 = st.columns([1, 4])
                with c1:
                    st.markdown(f"{badge} **{ref}**")
                    st.caption(client)
                with c2:
                    for line in lines:
                        qty  = int(line["quantity"]) if line["quantity"] == int(line["quantity"]) else line["quantity"]
                        name = line["name"]
                        var  = f" — {line['variant']}" if line["variant"] else ""
                        sku  = f" `{line['sku']}`" if line["sku"] else ""
                        st.markdown(f"× {qty}  {name}{var}{sku}")
                    if order.get("note"):
                        st.caption(f"📝 {order['note']}")

        st.divider()

    # ── Production summary ────────────────────────────────────────────────────
    st.markdown("### Resumen de producción")

    product_totals: dict[str, float] = defaultdict(float)
    for order in visible:
        for line in order["lines"]:
            name = line["name"]
            if line["variant"]:
                name = f"{name} ({line['variant']})"
            if name:
                product_totals[name] += line["quantity"]

    if product_totals:
        col_h1, col_h2 = st.columns([4, 1])
        col_h1.markdown("**Producto**")
        col_h2.markdown("**Uds**")
        for product, qty in sorted(product_totals.items(), key=lambda x: -x[1]):
            c1, c2 = st.columns([4, 1])
            c1.write(product)
            c2.write(int(qty) if qty == int(qty) else qty)

    # ── Labour estimate ───────────────────────────────────────────────────────
    st.divider()
    st.markdown("### Mano de obra estimada")

    recipes     = db.get_recipes()
    s           = load_settings()
    recipe_map  = {r["name"].lower(): r for r in recipes}

    total_labour_h = 0.0
    total_oven_h   = 0.0
    matched        = []
    unmatched      = []

    for product, qty in product_totals.items():
        # Try to match product name to a recipe (case-insensitive substring)
        clean   = product.lower().split("(")[0].strip()
        recipe  = next(
            (r for name, r in recipe_map.items() if clean in name or name in clean),
            None,
        )
        if not recipe:
            unmatched.append(product)
            continue

        # Determine format from product name
        name_lower = product.lower()
        if "bocado" in name_lower or "bite" in name_lower:
            fmt        = "bocado"
            batch_sz   = s.ws_batch_bocado
            prep_h     = recipe.get("bocado_batch_prep_hours") or recipe.get("ref_prep_hours") or 0
            oven_h     = recipe.get("bocado_batch_oven_hours") or recipe.get("ref_oven_hours")  or 0
            ref_batch  = s.ws_batch_bocado
        elif "individual" in name_lower or "tartaleta" in name_lower:
            fmt        = "individual"
            batch_sz   = s.ws_batch_individual
            prep_h     = recipe.get("small_batch_prep_hours") or recipe.get("ref_prep_hours") or 0
            oven_h     = recipe.get("small_batch_oven_hours") or recipe.get("ref_oven_hours")  or 0
            ref_batch  = s.ws_batch_individual
        else:
            fmt        = "standard"
            batch_sz   = s.ws_batch_large
            prep_h     = recipe.get("ref_prep_hours") or 0
            oven_h     = recipe.get("ref_oven_hours")  or 0
            ref_batch  = float(recipe.get("ref_batch_size") or 20)

        labour = calc_labour_cost(batch_sz, ref_batch, prep_h, oven_h, s)
        labour_h = (labour.prep_per_unit * qty)
        oven_h_t = (labour.oven_per_unit * qty)

        total_labour_h += labour_h
        total_oven_h   += oven_h_t
        matched.append((product, fmt, qty, labour_h, oven_h_t))

    if matched:
        col_h1, col_h2, col_h3, col_h4 = st.columns([4, 1, 1.2, 1.2])
        col_h1.markdown("**Producto**")
        col_h2.markdown("**Uds**")
        col_h3.markdown("**Prep h**")
        col_h4.markdown("**Horno h**")

        for product, fmt, qty, lh, oh in matched:
            c1, c2, c3, c4 = st.columns([4, 1, 1.2, 1.2])
            c1.write(f"{product} _{fmt}_")
            c2.write(int(qty) if qty == int(qty) else qty)
            c3.write(f"{lh:.1f}")
            c4.write(f"{oh:.1f}")

        st.divider()
        col_x, col_y = st.columns(2)
        col_x.metric("Total prep", f"{total_labour_h:.1f} h")
        col_y.metric("Total horno", f"{total_oven_h:.1f} h")
        st.metric(
            "Total mano de obra",
            f"{total_labour_h + total_oven_h:.1f} h",
        )

    if unmatched:
        st.caption(
            "⚠️ Sin receta coincidente (excluidos del cálculo): "
            + ", ".join(unmatched)
        )
