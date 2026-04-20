# main.py — Millington Cakes Pricing Manager
# =============================================================================
# Entry point for the Streamlit app. Run with:
#   streamlit run app/main.py
#
# This file handles:
#   - Page configuration (title, icon, layout)
#   - Sidebar navigation
#   - Routing to the correct screen
#
# Each screen lives in its own function in this file for now.
# As the app grows, screens can be split into separate files under app/screens/
# =============================================================================

import streamlit as st
import millington_db as db
from screen_ingredients import screen_ingredients
from screen_recipes import screen_recipes
from screen_calculator import screen_calculator
from screen_settings import screen_settings
from screen_packaging import screen_packaging
from screen_analysis import screen_analysis
from screen_variants import screen_variants

# -----------------------------------------------------------------------------
# Page config — must be the first Streamlit call in the file
# -----------------------------------------------------------------------------

st.set_page_config(
    page_title="Millington Cakes",
    page_icon="🎂",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Password checker
def check_password():
    """Returns True if the user has entered the correct password."""
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

# -----------------------------------------------------------------------------
# Sidebar navigation
# -----------------------------------------------------------------------------

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

        st.divider()
        st.markdown("**Manage**")

        if st.button("Recipes", use_container_width=True):
            st.session_state.screen = "recipes"

        if st.button("Ingredients", use_container_width=True):
            st.session_state.screen = "ingredients"

        if st.button("Consumables", use_container_width=True):
            st.session_state.screen = "consumables"

        if st.button("Packaging presets", use_container_width=True):
            st.session_state.screen = "packaging"
            
        if st.button("Variantes de producto", use_container_width=True):
            st.session_state.screen = "variants"

        st.divider()
        st.markdown("**System**")

        if st.button("Settings", use_container_width=True):
            st.session_state.screen = "settings"

    return st.session_state.get("screen", "calculator")


# -----------------------------------------------------------------------------
# Screen: Consumables (placeholder — same pattern as ingredients)
# -----------------------------------------------------------------------------

def screen_consumables():
    st.title("Consumables")
    st.caption("Packaging, piping bags, paper — non-food costs")

    consumables = db.get_consumables()

    total      = len(consumables)
    no_price   = sum(1 for c in consumables if not c.get("pack_price_ex_vat"))
    incomplete = sum(1 for c in consumables if not c.get("cost_per_unit"))

    col1, col2, col3 = st.columns(3)
    col1.metric("Total consumables", total)
    col2.metric("Missing price", no_price)
    col3.metric("Incomplete cost", incomplete)

    st.divider()

    # ── Search ────────────────────────────────────────────────────────────────
    search = st.text_input("Search", placeholder="Type to filter by name…",
                           label_visibility="collapsed")

    filtered = [
        c for c in consumables
        if (search.lower() in c["name"].lower() if search else True)
    ]

    st.caption(f"Showing {len(filtered)} of {total} consumables")
    st.divider()

    # ── Column headers ────────────────────────────────────────────────────────
    h1, h2, h3, h4, h5, h6 = st.columns([3, 2, 1.2, 1.2, 1, 1.5])
    h1.markdown("**Name**")
    h2.markdown("**Supplier**")
    h3.markdown("**Pack quantity**")
    h4.markdown("**Price ex VAT (€)**")
    h5.markdown("**VAT**")
    h6.markdown("**Cost / unit**")

    for con in filtered:
        _consumable_row(con)

    st.divider()

    with st.expander("➕ Add new consumable"):
        _add_consumable_form()


def _consumable_row(con: dict):
    col_id = f"con_{con['id']}"

    c1, c2, c3, c4, c5, c6, c7 = st.columns([3, 2, 1.2, 1.2, 1, 1.5, 0.5])

    with c1:
        name = st.text_input("Name", value=con.get("name", ""),
                             key=f"{col_id}_name",
                             label_visibility="collapsed")
    with c2:
        supplier = st.text_input("Supplier", value=con.get("supplier") or "",
                                 key=f"{col_id}_supplier",
                                 label_visibility="collapsed")
    with c3:
        pack_qty = st.number_input("Pack qty",
                                   value=float(con.get("pack_quantity") or 0),
                                   min_value=0.0, key=f"{col_id}_qty",
                                   label_visibility="collapsed")
    with c4:
        price = st.number_input("Price", 
                                value=float(con.get("pack_price_ex_vat") or 0),
                                min_value=0.0, format="%.4f",
                                key=f"{col_id}_price",
                                label_visibility="collapsed")
    with c5:
        vat = st.selectbox("VAT", [0.0, 0.04, 0.10, 0.21],
                           index=[0.0, 0.04, 0.10, 0.21].index(
                               float(con.get("vat_rate") or 0.21)),
                           format_func=lambda x: f"{int(x*100)}%",
                           key=f"{col_id}_vat",
                           label_visibility="collapsed")
    with c6:
        cost = round(price / pack_qty, 6) if pack_qty > 0 and price > 0 else None
        if cost:
            st.markdown(f"`€ {cost:.5f} / unit`")
        else:
            st.markdown("—")
    with c7:
        if st.button("💾", key=f"{col_id}_save", help="Save changes"):
            if name != con["name"]:
                existing_names = [
                    c["name"] for c in db.get_consumables()
                    if c["id"] != con["id"]
                ]
                similar = db.find_similar_names(name, existing_names)
                if similar:
                    matches = ", ".join(f"'{m}'" for m, _ in similar)
                    st.warning(
                        f"⚠️ Edited name is similar to: {matches} — "
                        "save cancelled. Check for duplicates first."
                    )
                    st.stop()
            db.save_consumable({
                "id":                con["id"],
                "name":              name,
                "supplier":          supplier or None,
                "pack_quantity":     pack_qty or None,
                "pack_price_ex_vat": price or None,
                "vat_rate":          vat,
            })
            st.success(f"Saved: {name}", icon="✅")
            st.rerun()


def _add_consumable_form():
    c1, c2, c3, c4, c5 = st.columns([3, 2, 1.2, 1.2, 1])

    with c1:
        name = st.text_input("Name", key="new_con_name",
                             placeholder="e.g. Caja tarta estándar")
    with c2:
        supplier = st.text_input("Supplier", key="new_con_supplier",
                                 placeholder="e.g. Makro")
    with c3:
        pack_qty = st.number_input("Pack quantity", min_value=0.0,
                                   key="new_con_qty")
    with c4:
        price = st.number_input("Price ex VAT (€)", min_value=0.0,
                                format="%.4f", key="new_con_price")
    with c5:
        vat = st.selectbox("VAT", [0.0, 0.04, 0.10, 0.21],
                           index=3,  # default 21%
                           format_func=lambda x: f"{int(x*100)}%",
                           key="new_con_vat")

    if name:
        existing_names = [c["name"] for c in db.get_consumables()]
        similar = db.find_similar_names(name, existing_names)
        if similar:
            st.warning(
                "⚠️ Similar consumable name(s) already exist — "
                "check this is not a duplicate:"
            )
            for match, score in similar:
                st.markdown(f"&nbsp;&nbsp;&nbsp;`{match}` &nbsp;({score}% similar)")
            confirmed = st.checkbox(
                "This is a different consumable — save anyway",
                key="new_con_confirmed"
            )
        else:
            confirmed = True
    else:
        confirmed = False

    if st.button("Add consumable", type="primary"):
        if not name:
            st.error("Name is required.")
        elif not confirmed:
            st.error("Please confirm this is different from the similar items above.")
        else:
            db.save_consumable({
                "name":              name,
                "supplier":          supplier or None,
                "pack_quantity":     pack_qty or None,
                "pack_price_ex_vat": price or None,
                "vat_rate":          vat,
            })
            st.success(f"Added: {name}")
            st.rerun()

# -----------------------------------------------------------------------------
# Router — maps screen name to function
# -----------------------------------------------------------------------------

SCREENS = {
    "calculator": screen_calculator,
    "analysis": screen_analysis,
    "ingredients": screen_ingredients,
    "consumables": screen_consumables,
    "recipes": screen_recipes,
    "packaging": screen_packaging,
    "settings": screen_settings,
    "variants": screen_variants,
}


# -----------------------------------------------------------------------------
# App entry point
# -----------------------------------------------------------------------------

def main():
    if not check_password():
        return
    screen = sidebar()
    screen_fn = SCREENS.get(screen, screen_calculator)
    screen_fn()


if __name__ == "__main__":
    main()
