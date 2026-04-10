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
import db

# -----------------------------------------------------------------------------
# Page config — must be the first Streamlit call in the file
# -----------------------------------------------------------------------------

st.set_page_config(
    page_title="Millington Cakes",
    page_icon="🎂",
    layout="wide",
    initial_sidebar_state="expanded",
)


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

        st.divider()
        st.markdown("**System**")

        if st.button("Settings", use_container_width=True):
            st.session_state.screen = "settings"

    return st.session_state.get("screen", "calculator")


# -----------------------------------------------------------------------------
# Screen: Cost calculator (placeholder — built in next step)
# -----------------------------------------------------------------------------

def screen_calculator():
    st.title("Cost calculator")
    st.caption("Calculate ingredient, labour and packaging costs for any cake")
    st.info("Coming soon — ingredients screen first.")


# -----------------------------------------------------------------------------
# Screen: Ingredients
# -----------------------------------------------------------------------------

def screen_ingredients():
    st.title("Ingredients")
    st.caption("Edit prices and pack sizes — cost per unit updates automatically")

    ingredients = db.get_ingredients()

    # ── Summary metrics ──────────────────────────────────────────────────────
    total      = len(ingredients)
    no_price   = sum(1 for i in ingredients if not i.get("pack_price_ex_vat"))
    no_size    = sum(1 for i in ingredients if not i.get("pack_size"))
    incomplete = sum(1 for i in ingredients if not i.get("cost_per_unit"))

    col1, col2, col3 = st.columns(3)
    col1.metric("Total ingredients", total)
    col2.metric("Missing price", no_price, delta=None,
                help="Ingredients with no pack price set")
    col3.metric("Missing pack size", no_size,
                help="Ingredients with no pack size set")

    if incomplete:
        st.warning(
            f"{incomplete} ingredient(s) have no cost per unit — "
            "cost calculator results will be incomplete until these are filled in."
        )

    st.divider()

    # ── Search / filter ──────────────────────────────────────────────────────
    col_search, col_supplier = st.columns([3, 1])
    with col_search:
        search = st.text_input("Search", placeholder="Type to filter by name…",
                               label_visibility="collapsed")
    with col_supplier:
        suppliers = sorted(set(
            i["supplier"] for i in ingredients if i.get("supplier")
        ))
        supplier_filter = st.selectbox(
            "Supplier", ["All suppliers"] + suppliers,
            label_visibility="collapsed"
        )

    filtered = [
        i for i in ingredients
        if (search.lower() in i["name"].lower() if search else True)
        and (i.get("supplier") == supplier_filter if supplier_filter != "All suppliers" else True)
    ]

    st.caption(f"Showing {len(filtered)} of {total} ingredients")
    st.divider()

    # ── Ingredient rows ───────────────────────────────────────────────────────
    # Column headers
    h1, h2, h3, h4, h5, h6, h7, h8 = st.columns([3, 2, 1.2, 1, 1.2, 1, 1.5, 0.5])
    h1.markdown("**Name**")
    h2.markdown("**Supplier**")
    h3.markdown("**Pack size**")
    h4.markdown("**Unit**")
    h5.markdown("**Price ex VAT (€)**")
    h6.markdown("**VAT**")
    h7.markdown("**Cost / unit**")
    h8.markdown("")

    for ing in filtered:
        _ingredient_row(ing)

    st.divider()

    # ── Add new ingredient ────────────────────────────────────────────────────
    with st.expander("➕ Add new ingredient"):
        _add_ingredient_form()


def _ingredient_row(ing: dict):
    """Render one editable ingredient row."""
    col_id = f"ing_{ing['id']}"

    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([3, 2, 1.2, 1, 1.2, 1, 1.5, 0.5])

    with c1:
        name = st.text_input("Name", value=ing.get("name", ""),
                             key=f"{col_id}_name", label_visibility="collapsed")
    with c2:
        supplier = st.text_input("Supplier", value=ing.get("supplier") or "",
                                 key=f"{col_id}_supplier", label_visibility="collapsed")
    with c3:
        pack_size = st.number_input("Pack size", value=float(ing.get("pack_size") or 0),
                                    min_value=0.0, key=f"{col_id}_size",
                                    label_visibility="collapsed")
    with c4:
        unit = st.selectbox("Unit", ["g", "kg", "ml", "l", "units"],
                            index=["g", "kg", "ml", "l", "units"].index(
                                ing.get("pack_unit") or "g"),
                            key=f"{col_id}_unit", label_visibility="collapsed")
    with c5:
        price = st.number_input("Price", value=float(ing.get("pack_price_ex_vat") or 0),
                                min_value=0.0, format="%.4f",
                                key=f"{col_id}_price", label_visibility="collapsed")
    with c6:
        vat = st.selectbox("VAT", [0.0, 0.04, 0.10, 0.21],
                           index=[0.0, 0.04, 0.10, 0.21].index(
                               float(ing.get("vat_rate") or 0.10)),
                           format_func=lambda x: f"{int(x*100)}%",
                           key=f"{col_id}_vat", label_visibility="collapsed")
    with c7:
        # Compute and display cost per unit live
        cost = round(price / pack_size, 6) if pack_size > 0 and price > 0 else None
        if cost:
            st.markdown(f"`€ {cost:.5f} / {unit}`")
        else:
            st.markdown("—")
    with c8:
        if st.button("💾", key=f"{col_id}_save", help="Save changes"):
            # Check if the edited name clashes with a different existing ingredient
            if name != ing["name"]:
                existing_names = [
                    i["name"] for i in db.get_ingredients() 
                    if i["id"] != ing["id"]
                ]
                similar = db.find_similar_names(name, existing_names)
                if similar:
                    matches = ", ".join(f"'{m}'" for m, _ in similar)
                    st.warning(
                        f"⚠️ Edited name is similar to: {matches} — "
                        "save cancelled. Check for duplicates first."
                    )
                    st.stop()
            db.save_ingredient({
                "id":                ing["id"],
                "name":              name,
                "supplier":          supplier or None,
                "pack_size":         pack_size or None,
                "pack_unit":         unit,
                "pack_price_ex_vat": price or None,
                "vat_rate":          vat,
            })
            st.success(f"Saved: {name}", icon="✅")
            st.rerun()


def _add_ingredient_form():
    """Form for adding a brand new ingredient with fuzzy duplicate detection."""
    c1, c2, c3, c4, c5, c6 = st.columns([3, 2, 1.2, 1, 1.2, 1])

    with c1:
        name = st.text_input("Name", key="new_ing_name",
                             placeholder="e.g. Chocolate Negro 70%")
    with c2:
        supplier = st.text_input("Supplier", key="new_ing_supplier",
                                 placeholder="e.g. Valrhona")
    with c3:
        pack_size = st.number_input("Pack size", min_value=0.0,
                                    key="new_ing_size")
    with c4:
        unit = st.selectbox("Unit", ["g", "kg", "ml", "l", "units"],
                            key="new_ing_unit")
    with c5:
        price = st.number_input("Price ex VAT (€)", min_value=0.0,
                                format="%.4f", key="new_ing_price")
    with c6:
        vat = st.selectbox("VAT", [0.0, 0.04, 0.10, 0.21],
                           index=2,
                           format_func=lambda x: f"{int(x*100)}%",
                           key="new_ing_vat")

    # Fuzzy match check — runs as soon as a name is typed
    if name:
        existing_names = [i["name"] for i in db.get_ingredients()]
        similar = db.find_similar_names(name, existing_names)
        if similar:
            st.warning(
                "⚠️ Similar ingredient name(s) already exist — "
                "check this is not a duplicate or spelling mistake:"
            )
            for match, score in similar:
                st.markdown(f"&nbsp;&nbsp;&nbsp;`{match}` &nbsp;({score}% similar)")
            # Require explicit confirmation before allowing save
            confirmed = st.checkbox(
                "This is genuinely a different ingredient — save anyway",
                key="new_ing_confirmed"
            )
        else:
            confirmed = True
    else:
        confirmed = False

    if st.button("Add ingredient", type="primary"):
        if not name:
            st.error("Name is required.")
        elif not confirmed:
            st.error(
                "Please confirm this is a different ingredient "
                "from the similar ones listed above."
            )
        else:
            db.save_ingredient({
                "name":              name,
                "supplier":          supplier or None,
                "pack_size":         pack_size or None,
                "pack_unit":         unit,
                "pack_price_ex_vat": price or None,
                "vat_rate":          vat,
            })
            st.success(f"Added: {name}")
            st.rerun()


# -----------------------------------------------------------------------------
# Screen: Consumables (placeholder — same pattern as ingredients)
# -----------------------------------------------------------------------------

def screen_consumables():
    st.title("Consumables")
    st.caption("Packaging, piping bags, paper — non-food costs")
    st.info("Coming in the next step — same pattern as ingredients.")


# -----------------------------------------------------------------------------
# Screen: Recipes (placeholder)
# -----------------------------------------------------------------------------

def screen_recipes():
    st.title("Recipes")
    st.caption("Manage reference recipes and their ingredients")
    st.info("Coming soon.")


# -----------------------------------------------------------------------------
# Screen: Packaging presets (placeholder)
# -----------------------------------------------------------------------------

def screen_packaging():
    st.title("Packaging presets")
    st.caption("Saved combinations used in the cost calculator")
    st.info("Coming soon.")


# -----------------------------------------------------------------------------
# Screen: Settings (placeholder)
# -----------------------------------------------------------------------------

def screen_settings():
    st.title("Settings")
    st.caption("Business-wide defaults used in calculations")
    st.info("Coming soon.")


# -----------------------------------------------------------------------------
# Router — maps screen name to function
# -----------------------------------------------------------------------------

SCREENS = {
    "calculator": screen_calculator,
    "ingredients": screen_ingredients,
    "consumables": screen_consumables,
    "recipes": screen_recipes,
    "packaging": screen_packaging,
    "settings": screen_settings,
}


# -----------------------------------------------------------------------------
# App entry point
# -----------------------------------------------------------------------------

def main():
    screen = sidebar()
    screen_fn = SCREENS.get(screen, screen_calculator)
    screen_fn()


if __name__ == "__main__":
    main()
