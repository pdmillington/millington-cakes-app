# main.py — Millington Cakes Pricing Manager
# =============================================================================
# Entry point. Run with: streamlit run app/main.py
#
# Screen files (separate modules):
#   screen_recipes.py, screen_ingredients.py, screen_calculator.py,
#   screen_analysis.py, screen_variants.py, screen_packaging.py,
#   screen_settings.py
#
# Inline screen: screen_consumables (kept here — smaller, no separate file)
# =============================================================================

import streamlit as st
import millington_db as db

from screen_recipes     import screen_recipes
from screen_ingredients import screen_ingredients
from screen_calculator  import screen_calculator
from screen_analysis    import screen_analysis
from screen_variants    import screen_variants
from screen_packaging   import screen_packaging
from screen_settings    import screen_settings
from screen_repricing   import screen_repricing
from screen_catalogue   import screen_catalogue
from screen_prices      import screen_prices
from screen_kpis        import screen_kpis

# =============================================================================
# Page config
# =============================================================================

st.set_page_config(
    page_title="Millington Cakes",
    page_icon="🎂",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =============================================================================
# Password protection
# =============================================================================

def check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True

    st.markdown("### 🎂 Millington Cakes")
    st.markdown("Pricing Manager — please log in")
    st.divider()

    password = st.text_input("Password", type="password", key="password_input")

    if st.button("Log in", type="primary"):
        if password == st.secrets.get("APP_PASSWORD", ""):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    return False


# =============================================================================
# Sidebar
# =============================================================================

def sidebar() -> str:
    with st.sidebar:
        st.markdown("### 🎂 Millington Cakes")
        st.markdown("Pricing Manager")
        st.divider()

        st.markdown("**Daily use**")
        if st.button("Cost calculator", use_container_width=True):
            st.session_state.screen = "calculator"
        if st.button("Recipe analysis", use_container_width=True):
            st.session_state.screen = "analysis"
        if st.button("Repricing analysis", use_container_width=True):
             st.session_state.screen = "repricing"
        if st.button("Business KPIs", use_container_width=True):
             st.session_state.screen = "kpis"

        st.divider()
        st.markdown("**Manage**")

        if st.button("Recipes", use_container_width=True):
            st.session_state.screen = "recipes"
        if st.button("Ingredients", use_container_width=True):
            st.session_state.screen = "ingredients"
        if st.button("Consumables", use_container_width=True):
            st.session_state.screen = "consumables"
        if st.button("Prices", use_container_width=True):
            st.session_state.screen = "prices"
        if st.button("Wholesale catalogue", use_container_width=True):
            st.session_state.screen = "catalogue"

        st.divider()
        st.markdown("**Config**")

        if st.button("Product variants", use_container_width=True):
            st.session_state.screen = "variants"
        if st.button("Packaging presets", use_container_width=True):
            st.session_state.screen = "packaging"
        if st.button("Settings", use_container_width=True):
            st.session_state.screen = "settings"

    return st.session_state.get("screen", "calculator")


# =============================================================================
# Inline screen: Consumables
# =============================================================================

def screen_consumables():
    st.title("Consumables")
    st.caption("Packaging materials and other consumables used in cost calculations")

    consumables = db.get_consumables()
    total       = len(consumables)
    no_price    = sum(1 for c in consumables if not c.get("cost_per_unit"))

    col1, col2 = st.columns(2)
    col1.metric("Total consumables", total)
    col2.metric("Missing cost", no_price)

    if no_price:
        st.warning(
            f"{no_price} consumable(s) have no cost set — "
            "packaging cost will be understated in the calculator."
        )

    st.divider()

    search = st.text_input(
        "Search", placeholder="Filter by name…",
        label_visibility="collapsed"
    )
    filtered = [
        c for c in consumables
        if search.lower() in c["name"].lower()
    ] if search else consumables

    st.caption(f"Showing {len(filtered)} of {total}")
    st.divider()

    h1, h2, h3, h4, h5, h6 = st.columns([3, 2, 1.2, 1, 1.5, 0.5])
    h1.markdown("**Name**"); h2.markdown("**Supplier**")
    h3.markdown("**Qty**");  h4.markdown("**Unit**")
    h5.markdown("**Cost/unit**"); h6.markdown("")

    for con in filtered:
        _consumable_row(con)

    st.divider()
    with st.expander("➕ Add new consumable"):
        _add_consumable_form()


def _consumable_row(con: dict):
    cid = f"con_{con['id']}"
    c1, c2, c3, c4, c5, c6 = st.columns([3, 2, 1.2, 1, 1.5, 0.5])

    with c1:
        name = st.text_input("Name", value=con.get("name", ""),
                             key=f"{cid}_name", label_visibility="collapsed")
    with c2:
        supplier = st.text_input("Supplier", value=con.get("supplier") or "",
                                 key=f"{cid}_supplier",
                                 label_visibility="collapsed")
    with c3:
        quantity = st.number_input("Qty", value=float(con.get("pack_quantity") or 0),
                                   min_value=0.0, key=f"{cid}_qty",
                                   label_visibility="collapsed")
    with c4:
        unit_opts = ["units", "g", "kg", "ml", "l"]
        cur_unit  = con.get("pack_unit") or "units"
        if cur_unit not in unit_opts:
            cur_unit = "units"
        unit = st.selectbox("Unit", unit_opts, index=unit_opts.index(cur_unit),
                            key=f"{cid}_unit", label_visibility="collapsed")
    with c5:
        price = st.number_input("Price", value=float(con.get("pack_price_ex_vat") or 0),
                                min_value=0.0, format="%.4f",
                                key=f"{cid}_price", label_visibility="collapsed")
        cost = round(price / quantity, 6) if quantity > 0 and price > 0 else None
        if cost:
            st.caption(f"€ {cost:.5f}/unit")
    with c6:
        if st.button("💾", key=f"{cid}_save", help="Save"):
            db.save_consumable({
                "id": con["id"], "name": name,
                "supplier": supplier or None,
                "pack_quantity": quantity or None,
                "pack_unit": unit,
                "pack_price_ex_vat": price or None,
            })
            st.success(f"Saved: {name}", icon="✅")
            st.rerun()


def _add_consumable_form():
    c1, c2, c3, c4, c5 = st.columns([3, 2, 1.2, 1, 1.5])
    with c1:
        name = st.text_input("Name", key="new_con_name",
                             placeholder="e.g. Caja tarta 22cm")
    with c2:
        supplier = st.text_input("Supplier", key="new_con_supplier")
    with c3:
        quantity = st.number_input("Qty", min_value=0.0, key="new_con_qty")
    with c4:
        unit = st.selectbox("Unit", ["units", "g", "kg", "ml", "l"],
                            key="new_con_unit")
    with c5:
        price = st.number_input("Price ex VAT (€)", min_value=0.0,
                                format="%.4f", key="new_con_price")

    confirmed = True
    if name:
        existing = [c["name"] for c in db.get_consumables()]
        similar  = db.find_similar_names(name, existing)
        if similar:
            st.warning("⚠️ Similar consumable names already exist:")
            for match, score in similar:
                st.markdown(f"&nbsp;&nbsp;&nbsp;`{match}` ({score}% similar)")
            confirmed = st.checkbox(
                "This is a different consumable — save anyway",
                key="new_con_confirmed"
            )
    else:
        confirmed = False

    if st.button("Add consumable", type="primary"):
        if not name:
            st.error("Name is required.")
        elif not confirmed:
            st.error("Please confirm this differs from similar items.")
        else:
            db.save_consumable({
                "name": name, "supplier": supplier or None,
                "pack_quantity": quantity or None, "pack_unit": unit,
                "pack_price_ex_vat": price or None,
            })
            st.success(f"Added: {name}", icon="✅")
            st.rerun()


# =============================================================================
# Router
# =============================================================================

SCREENS = {
    "calculator":  screen_calculator,
    "analysis":    screen_analysis,
    "recipes":     screen_recipes,
    "ingredients": screen_ingredients,
    "consumables": screen_consumables,
    "variants":    screen_variants,
    "packaging":   screen_packaging,
    "settings":    screen_settings,
    "repricing":   screen_repricing,
    "catalogue":   screen_catalogue,
    "prices":      screen_prices,
    "kpis":        screen_kpis, 
}


# =============================================================================
# Entry point
# =============================================================================

def main():
    if not check_password():
        return
    screen    = sidebar()
    screen_fn = SCREENS.get(screen, screen_calculator)
    screen_fn()


if __name__ == "__main__":
    main()
