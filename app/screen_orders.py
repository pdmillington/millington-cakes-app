# screen_orders.py
# =============================================================================
# Pedidos y compras — two tabs:
#   Tab 1: Pedidos   — production matrix + orders by day
#   Tab 2: Compras   — ingredient requirements vs current stock
#
# Both tabs share the same order data loaded once at the top.
# =============================================================================

from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import millington_db as db
from millington_db       import _UNIT_PURCHASE
from shopify_api         import get_open_orders, last_synced as shopify_synced
from holded_api          import get_estimates, estimates_last_synced
from core.settings       import load_settings
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


def _dataframe(df: pd.DataFrame) -> None:
    st.dataframe(df, width="stretch")


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


def _date_key(order: dict) -> date:
    return order.get("due_date") or order.get("order_date") or date.max


# =============================================================================
# Tab 1 — Pedidos
# =============================================================================

def _tab_pedidos(
    all_orders: list[dict],
    holded_products: list[dict],
    show_all: bool,
) -> None:
    today     = date.today()
    next_week = today + timedelta(days=7)

    def _in_range(order: dict) -> bool:
        d = order.get("due_date") or order.get("order_date")
        return (today <= d <= next_week) if d else show_all

    visible = [o for o in all_orders if show_all or _in_range(o)]

    if not visible:
        st.info(
            "No hay pedidos abiertos para los próximos 7 días. "
            "Activa 'Ver todos' para ver todos los pedidos abiertos."
        )
        return

    # ── Build product × day quantities ────────────────────────────────────────
    sku_units_per_pack: dict[str, int] = {
        p["sku"]: int(p.get("units_per_pack") or 1)
        for p in holded_products if p.get("sku")
    }

    product_by_day: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    product_sku:    dict[str, str] = {}
    for order in visible:
        d = _date_key(order)
        for line in order["lines"]:
            name = line["name"]
            if line["variant"]:
                name = f"{name} ({line['variant']})"
            if name:
                product_by_day[name][d] += line["quantity"]
                if line.get("sku") and name not in product_sku:
                    product_sku[name] = line["sku"]

    days       = sorted({d for dm in product_by_day.values() for d in dm})
    day_labels = [(_fmt_date(d) if d != date.max else "Sin fecha") for d in days]

    # ── Prep hours ────────────────────────────────────────────────────────────
    recipes    = db.get_recipes()
    s          = load_settings()
    recipe_map = {r["name"].lower(): r for r in recipes}

    prep_by_day: dict[date, float] = defaultdict(float)
    unmatched:   list[str]         = []
    calc_debug:  list[dict]        = []

    for product, day_qtys in product_by_day.items():
        match = _match_recipe(product, recipe_map)
        if not match:
            unmatched.append(product)
            continue
        recipe, fmt = match

        if fmt == "bocado":
            bocado_specific = bool(recipe.get("bocado_batch_prep_hours"))
            prep_h    = recipe.get("bocado_batch_prep_hours") or recipe.get("ref_prep_hours") or 0
            ref_batch = s.ws_batch_bocado
        elif fmt == "individual":
            bocado_specific = False
            prep_h    = recipe.get("small_batch_prep_hours") or recipe.get("ref_prep_hours") or 0
            ref_batch = s.ws_batch_individual
        else:
            bocado_specific = False
            prep_h    = recipe.get("ref_prep_hours") or 0
            ref_batch = float(recipe.get("ref_batch_size") or 20)

        sku            = product_sku.get(product, "")
        units_per_pack = sku_units_per_pack.get(sku, 1)

        for d, qty in day_qtys.items():
            individual_units = qty * units_per_pack
            labour = calc_labour_cost(individual_units, ref_batch, prep_h, 0, s)
            prep_by_day[d] += labour.prep_per_unit * individual_units

        total_qty    = sum(day_qtys.values())
        total_units  = total_qty * units_per_pack
        labour_total = calc_labour_cost(total_units, ref_batch, prep_h, 0, s)
        calc_debug.append({
            "Producto":   product,
            "SKU":        sku or "—",
            "Formato":    fmt,
            "prep_h":     prep_h,
            "ref_batch":  ref_batch,
            "uds/caja":   units_per_pack,
            "qty (cajas)":int(total_qty),
            "uds totales":int(total_units),
            "prep/ud (h)":round(labour_total.prep_per_unit, 4),
            "total (h)":  round(labour_total.prep_per_unit * total_units, 2),
            "bocado_batch_prep_hours": (
                "✅" if fmt == "bocado" and bocado_specific
                else ("⚠️ usando ref_prep_hours" if fmt == "bocado" else "—")
            ),
        })

    # ── Production matrix ─────────────────────────────────────────────────────
    st.markdown("### Resumen de producción")

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

    prep_row   = []
    total_prep = 0.0
    for d in days:
        h = prep_by_day.get(d, 0.0)
        total_prep += h
        prep_row.append(f"{h:.1f}h" if h else "")
    prep_row.append(f"{total_prep:.1f}h")
    rows["⏱ Prep (h)"] = prep_row

    df = pd.DataFrame(rows, index=day_labels + ["Total"]).T
    _dataframe(df)

    if unmatched:
        st.caption(
            "⚠️ Sin receta coincidente (excluidos del cálculo de prep): "
            + ", ".join(sorted(unmatched))
        )

    if calc_debug:
        with st.expander("🔍 Detalle del cálculo de prep"):
            _dataframe(pd.DataFrame(calc_debug))

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
            badge   = _source_badge(order["source"])
            c1, c2  = st.columns([1, 4])
            with c1:
                st.markdown(f"{badge} **{order['ref']}**")
                st.caption(order["client"])
            with c2:
                for line in order["lines"]:
                    qty = int(line["quantity"]) if line["quantity"] == int(line["quantity"]) else line["quantity"]
                    var = f" — {line['variant']}" if line["variant"] else ""
                    sku = f" `{line['sku']}`"     if line["sku"]     else ""
                    st.markdown(f"× {qty}  {line['name']}{var}{sku}")
                if order.get("note"):
                    st.caption(f"📝 {order['note']}")

        st.divider()


# =============================================================================
# Tab 2 — Lista de la compra
# =============================================================================

def _tab_compras(
    all_orders: list[dict],
    holded_products: list[dict],
) -> None:
    today = date.today()

    col_a, col_b = st.columns([1, 5])
    with col_a:
        days = st.selectbox("Horizonte", [7, 14], index=0, label_visibility="collapsed",
                            key="shopping_days")
    horizon = today + timedelta(days=days)

    upcoming = [o for o in all_orders if today <= _date_key(o) <= horizon]
    st.caption(f"{len(upcoming)} pedidos en los próximos {days} días")

    # ── Load recipe + ingredient data ─────────────────────────────────────────
    recipes   = db.get_recipes()
    recipe_map     = {r["name"].lower(): r for r in recipes}
    recipe_id_map  = {r["name"].lower(): r["id"] for r in recipes}

    sku_units_per_pack: dict[str, int] = {
        p["sku"]: int(p.get("units_per_pack") or 1)
        for p in holded_products if p.get("sku")
    }

    all_lines = db.get_recipe_ingredients_for_shopping()
    lines_by_recipe: dict[str, list[dict]] = defaultdict(list)
    for line in all_lines:
        lines_by_recipe[line["recipe_id"]].append(line)

    # ── Calculate requirements ────────────────────────────────────────────────
    required: dict[str, dict] = {}
    unmatched: list[str] = []
    product_sku: dict[str, str] = {}

    for order in upcoming:
        for line in order["lines"]:
            name = line["name"]
            if line["variant"]:
                name = f"{name} ({line['variant']})"
            if line.get("sku") and name not in product_sku:
                product_sku[name] = line["sku"]

            recipe_key = next(
                (k for k in recipe_map if name.lower().split("(")[0].strip() in k
                 or k in name.lower().split("(")[0].strip()),
                None,
            )
            if not recipe_key:
                if name not in unmatched:
                    unmatched.append(name)
                continue

            recipe         = recipe_map[recipe_key]
            recipe_id      = recipe_id_map[recipe_key]
            ref_batch_size = float(recipe.get("ref_batch_size") or 1)
            name_lower     = name.lower()
            fmt            = (
                "bocado" if ("bocado" in name_lower or "bite" in name_lower)
                else "individual" if ("individual" in name_lower or "tartaleta" in name_lower)
                else "standard"
            )

            sku            = product_sku.get(name, "")
            units_per_pack = sku_units_per_pack.get(sku, 1) if fmt == "bocado" else 1
            individual_units = float(line["quantity"]) * units_per_pack

            if ref_batch_size <= 0:
                continue
            # For bocados/individuals, ingredient amounts are per-batch → divide by ref_batch_size.
            # For standard products, amounts are per-unit (matches KPI screen approach) → no division.
            if fmt in ("bocado", "individual"):
                scale = individual_units / ref_batch_size
            else:
                scale = individual_units

            for ing_line in lines_by_recipe.get(recipe_id, []):
                ing_id = ing_line["ingredient_id"]
                if not ing_id:
                    continue
                amt = ing_line["amount"] * scale
                if ing_id not in required:
                    required[ing_id] = {
                        "name":          ing_line["ingredient_name"],
                        "unit":          ing_line["pack_unit"],
                        "unit_weight_g": ing_line.get("unit_weight_g"),
                        "amount":        0.0,
                    }
                required[ing_id]["amount"] += amt

    # ── Load stock ────────────────────────────────────────────────────────────
    stock = db.get_ingredient_stock()

    # ── Format helper ─────────────────────────────────────────────────────────
    def _fmt_amt(v: float, u: str) -> str:
        if u == "docenas":
            return f"{v:.2f} doc"
        if u == "g" and v >= 1000:
            return f"{v / 1000:,.2f} kg"
        if u == "ml" and v >= 1000:
            return f"{v / 1000:,.2f} L"
        if u in ("g", "ml"):
            return f"{v:,.0f} {u}"
        if u == "kg":
            return f"{v:,.3f} kg"
        return f"{v:.2f} {u}"

    # ── Shopping list ─────────────────────────────────────────────────────────
    st.divider()
    st.markdown("### Lista de compra")

    if not required:
        st.info("No hay pedidos próximos en este horizonte.")
        return

    rows = []
    for ing_id, info in sorted(required.items(), key=lambda x: x[1]["name"]):
        needed   = info["amount"]
        in_stock = stock.get(ing_id, 0.0)
        to_buy   = max(0.0, needed - in_stock)
        unit     = info["unit"]
        rows.append({
            "Ingrediente": info["name"],
            "Unidad":      unit,
            "Necesario":   _fmt_amt(needed,   unit),
            "En stock":    _fmt_amt(in_stock, unit),
            "🛒 Comprar":  _fmt_amt(to_buy,   unit) if to_buy > 0 else "✅",
            "_to_buy":     to_buy,
            "_ing_id":     ing_id,
            "_unit":       unit,
        })

    df_all  = pd.DataFrame(rows)
    needs   = df_all[df_all["_to_buy"] > 0]
    covered = df_all[df_all["_to_buy"] == 0]

    display_cols = ["Ingrediente", "Necesario", "En stock", "🛒 Comprar"]

    if not needs.empty:
        st.markdown("**Comprar:**")
        st.dataframe(needs[display_cols], width="stretch", hide_index=True)
    else:
        st.success("✅ Todo cubierto con el stock actual.")

    if not covered.empty:
        with st.expander(f"✅ Cubierto por stock ({len(covered)} ingredientes)"):
            st.dataframe(covered[display_cols], width="stretch", hide_index=True)

    if unmatched:
        st.caption(
            "⚠️ Productos sin receta asociada (excluidos del cálculo): "
            + ", ".join(sorted(set(unmatched)))
        )

    # ── Registrar compra ──────────────────────────────────────────────────────
    st.divider()
    with st.expander("🧾 Registrar compra (añadir al stock)", expanded=False):
        st.caption(
            "Introduce lo que has comprado. Se añadirá al stock actual. "
            "No es necesario rellenar todo — deja en 0 lo que no hayas comprado."
        )
        with st.form("purchase_form"):
            purchased: dict[str, float] = {}
            items = sorted(rows, key=lambda r: r["Ingrediente"])
            cols  = st.columns(3)
            for i, item in enumerate(items):
                with cols[i % 3]:
                    unit = item["_unit"]
                    min_buy = item["_to_buy"]
                    label   = f"{item['Ingrediente']} ({unit})"
                    hint    = f"Mín: {_fmt_amt(min_buy, unit)}" if min_buy > 0 else ""
                    step    = 0.1 if unit in ("kg", "docenas") else 10.0
                    val = st.number_input(
                        label,
                        min_value=0.0,
                        value=0.0,
                        step=step,
                        help=hint or None,
                        key=f"buy_{item['_ing_id']}",
                    )
                    purchased[item["_ing_id"]] = val

            if st.form_submit_button("✅ Añadir al stock", type="primary"):
                for ing_id, bought in purchased.items():
                    if bought > 0:
                        current = stock.get(ing_id, 0.0)
                        db.upsert_ingredient_stock(ing_id, current + bought)
                st.success("Stock actualizado.")
                st.rerun()

    # ── Ajustar stock (full correction) ───────────────────────────────────────
    with st.expander("⚙️ Ajustar stock manualmente", expanded=False):
        st.caption("Establece el stock actual exacto para cada ingrediente (sobrescribe los valores guardados).")
        all_ingredients = [
            i for i in db.get_ingredients()
            if i.get("cost_per_unit") and i.get("name")
        ]
        if all_ingredients:
            _unit_norm = {"kg": "g", "l": "ml", "litre": "ml", "litro": "ml"}
            # For known unit-purchase ingredients, get their purchase unit
            def _purchase_unit(ing: dict) -> str:
                name_lower = (ing.get("name") or "").lower()
                for key, (p_unit, _) in _UNIT_PURCHASE.items():
                    if key in name_lower:
                        return p_unit
                raw = (ing.get("pack_unit") or "g").lower().strip()
                return _unit_norm.get(raw, raw)

            with st.form("stock_adjust_form"):
                new_stock: dict[str, float] = {}
                sorted_ings = sorted(all_ingredients, key=lambda x: x["name"])
                cols = st.columns(3)
                for i, ing in enumerate(sorted_ings):
                    ing_id = ing["id"]
                    unit   = _purchase_unit(ing)
                    step   = 0.1 if unit in ("kg", "docenas") else 10.0
                    with cols[i % 3]:
                        val = st.number_input(
                            f"{ing['name']} ({unit})",
                            min_value=0.0,
                            value=stock.get(ing_id, 0.0),
                            step=step,
                            key=f"adj_{ing_id}",
                        )
                        new_stock[ing_id] = val

                if st.form_submit_button("💾 Guardar stock", type="primary"):
                    for ing_id, amount in new_stock.items():
                        db.upsert_ingredient_stock(ing_id, amount)
                    st.success("Stock guardado.")
                    st.rerun()


# =============================================================================
# Main entry point
# =============================================================================

def screen_orders():
    st.title("Pedidos y compras")
    st.caption("Shopify (retail) + Holded presupuestos (mayorista)")

    # ── Shared controls ───────────────────────────────────────────────────────
    col_a, col_b, col_c = st.columns([1, 1, 4])
    with col_a:
        refresh  = st.button("🔄 Actualizar", use_container_width=True)
    with col_b:
        show_all = st.toggle("Ver todos", value=False, key="orders_show_all")

    # ── Load orders (shared by both tabs) ─────────────────────────────────────
    shopify_orders = get_open_orders(force_refresh=refresh)
    holded_orders  = get_estimates(force_refresh=refresh)
    all_orders     = shopify_orders + holded_orders

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

    holded_products = db.get_holded_products()

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab1, tab2 = st.tabs(["📦 Pedidos", "🛒 Lista de la compra"])

    with tab1:
        _tab_pedidos(all_orders, holded_products, show_all)

    with tab2:
        _tab_compras(all_orders, holded_products)
