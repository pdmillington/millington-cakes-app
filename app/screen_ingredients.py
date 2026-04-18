# screen_ingredients.py
# =============================================================================
# Ingredients screen — price editor plus allergen declaration editor.
# Allergens follow EU 1169/2011 / RD 126/2015 (14 mandatory allergens).
# Three states per allergen: 0=No, 1=Contiene, 2=Puede contener (supplier)
# Plus kitchen_may_contain on the recipe for cross-contamination risks.
# =============================================================================

import streamlit as st
import millington_db as db

# ── Allergen definitions ──────────────────────────────────────────────────────
# (field_name, display_name_es, group)
ALLERGENS = [
    ("allergen_gluten",     "Cereales con gluten",          "Básicos"),
    ("allergen_egg",        "Huevo",                         "Básicos"),
    ("allergen_milk",       "Leche",                         "Básicos"),
    ("allergen_nuts",       "Frutos de cáscara",             "Básicos"),
    ("allergen_peanut",     "Cacahuetes",                    "Otros"),
    ("allergen_soy",        "Soja",                          "Otros"),
    ("allergen_mustard",    "Mostaza",                       "Otros"),
    ("allergen_sesame",     "Sésamo",                        "Otros"),
    ("allergen_sulphites",  "Dióxido de azufre y sulfitos",  "Otros"),
    ("allergen_celery",     "Apio",                          "Raros"),
    ("allergen_lupin",      "Altramuces",                    "Raros"),
    ("allergen_fish",       "Pescado",                       "Raros"),
    ("allergen_crustacean", "Crustáceos",                    "Raros"),
    ("allergen_mollusc",    "Moluscos",                      "Raros"),
]

ALLERGEN_OPTIONS  = ["No", "Contiene", "Puede contener"]
ALLERGEN_COLOURS  = {
    1: "🔴",  # Contiene
    2: "🟡",  # Puede contener
    0: "",    # No
}

# Unit conversion for display
_UNIT_TO_BASE = {"g": 1.0, "kg": 1000.0, "ml": 1.0, "l": 1000.0, "units": 1.0}


def screen_ingredients():
    st.title("Ingredients")
    st.caption("Edit prices, pack sizes and allergen declarations")

    ingredients = db.get_ingredients()

    # ── Summary metrics ───────────────────────────────────────────────────────
    total      = len(ingredients)
    no_price   = sum(1 for i in ingredients if not i.get("pack_price_ex_vat"))
    no_size    = sum(1 for i in ingredients if not i.get("pack_size"))
    incomplete = sum(1 for i in ingredients if not i.get("cost_per_unit"))
    needs_check = sum(
        1 for i in ingredients
        if i.get("allergen_notes") and
        "verificar" in (i.get("allergen_notes") or "").lower()
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total ingredients", total)
    col2.metric("Missing price", no_price,
                help="Ingredients with no pack price set")
    col3.metric("Missing pack size", no_size)
    col4.metric("Allergens to verify", needs_check,
                help="Ingredients flagged for label verification")

    if incomplete:
        st.warning(
            f"{incomplete} ingredient(s) have no cost per unit — "
            "add pack size and price to fix."
        )

    st.divider()

    # ── Search / filter ───────────────────────────────────────────────────────
    col_search, col_supplier, col_filter = st.columns([3, 1, 1])
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
    with col_filter:
        allergen_filter = st.selectbox(
            "Allergen filter",
            ["All", "Needs verification", "No allergens set"],
            label_visibility="collapsed"
        )

    filtered = [
        i for i in ingredients
        if (search.lower() in i["name"].lower() if search else True)
        and (i.get("supplier") == supplier_filter
             if supplier_filter != "All suppliers" else True)
        and _allergen_filter_match(i, allergen_filter)
    ]

    st.caption(f"Showing {len(filtered)} of {total} ingredients")
    st.divider()

    # ── Column headers ────────────────────────────────────────────────────────
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


def _allergen_filter_match(ing: dict, filter_val: str) -> bool:
    if filter_val == "All":
        return True
    if filter_val == "Needs verification":
        notes = (ing.get("allergen_notes") or "").lower()
        return "verificar" in notes or "needs" in notes
    if filter_val == "No allergens set":
        return all(
            ing.get(field, 0) == 0
            for field, _, _ in ALLERGENS
        )
    return True


def _allergen_summary(ing: dict) -> str:
    """Compact allergen summary for display in the row."""
    contiene     = []
    puede        = []
    for field, name, _ in ALLERGENS:
        val = ing.get(field, 0)
        short = name.split()[0]  # first word only for compactness
        if val == 1:
            contiene.append(short)
        elif val == 2:
            puede.append(short)
    parts = []
    if contiene:
        parts.append(f"🔴 {', '.join(contiene)}")
    if puede:
        parts.append(f"🟡 {', '.join(puede)}")
    return "  ".join(parts) if parts else "✅ Sin alérgenos"


def _ingredient_row(ing: dict):
    """Render one editable ingredient row with allergen expander."""
    col_id = f"ing_{ing['id']}"

    # ── Price row ─────────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([3, 2, 1.2, 1, 1.2, 1, 1.5, 0.5])

    with c1:
        name = st.text_input("Name", value=ing.get("name", ""),
                             key=f"{col_id}_name",
                             label_visibility="collapsed")
    with c2:
        supplier = st.text_input("Supplier",
                                 value=ing.get("supplier") or "",
                                 key=f"{col_id}_supplier",
                                 label_visibility="collapsed")
    with c3:
        pack_size = st.number_input(
            "Pack size",
            value=float(ing.get("pack_size") or 0),
            min_value=0.0, key=f"{col_id}_size",
            label_visibility="collapsed"
        )
    with c4:
        unit_opts = ["g", "kg", "ml", "l", "units"]
        unit = st.selectbox(
            "Unit", unit_opts,
            index=unit_opts.index(ing.get("pack_unit") or "g"),
            key=f"{col_id}_unit",
            label_visibility="collapsed"
        )
    with c5:
        price = st.number_input(
            "Price", value=float(ing.get("pack_price_ex_vat") or 0),
            min_value=0.0, format="%.4f",
            key=f"{col_id}_price",
            label_visibility="collapsed"
        )
    with c6:
        vat_opts = [0.0, 0.04, 0.10, 0.21]
        vat = st.selectbox(
            "VAT", vat_opts,
            index=vat_opts.index(float(ing.get("vat_rate") or 0.10)),
            format_func=lambda x: f"{int(x*100)}%",
            key=f"{col_id}_vat",
            label_visibility="collapsed"
        )
    with c7:
        factor    = _UNIT_TO_BASE.get(unit, 1.0)
        base_size = pack_size * factor
        cost      = round(price / base_size, 6) \
            if base_size > 0 and price > 0 else None
        base_unit = "g" if unit in ("g", "kg") \
            else "ml" if unit in ("ml", "l") else "unit"
        if cost:
            st.markdown(f"`€ {cost:.5f} / {base_unit}`")
        else:
            st.markdown("—")
    with c8:
        if st.button("💾", key=f"{col_id}_save", help="Save changes"):
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
                        "save cancelled."
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

    # ── Allergen expander ─────────────────────────────────────────────────────
    summary = _allergen_summary(ing)
    notes   = ing.get("allergen_notes") or ""
    needs_check = "verificar" in notes.lower() or "needs" in notes.lower()
    label = f"⚠️ Alérgenos — {summary}" if needs_check \
        else f"Alérgenos — {summary}"

    with st.expander(label):
        _allergen_editor(ing)


def _allergen_editor(ing: dict):
    """Compact allergen grid — 3 states per allergen."""
    col_id = f"al_{ing['id']}"

    st.caption(
        "🔴 Contiene = presente en este ingrediente (etiqueta del proveedor)  "
        "🟡 Puede contener = declarado por el proveedor"
    )

    # Group allergens
    groups = {}
    for field, name, group in ALLERGENS:
        groups.setdefault(group, []).append((field, name))

    allergen_vals = {}

    for group_name, items in groups.items():
        st.markdown(f"**{group_name}**")
        # Two allergens per row for compact layout
        for i in range(0, len(items), 2):
            pair = items[i:i+2]
            cols = st.columns(len(pair))
            for col, (field, display) in zip(cols, pair):
                with col:
                    current = int(ing.get(field) or 0)
                    allergen_vals[field] = st.radio(
                        display,
                        options=[0, 1, 2],
                        format_func=lambda x: ALLERGEN_OPTIONS[x],
                        index=current,
                        key=f"{col_id}_{field}",
                        horizontal=True
                    )

    # Notes field
    allergen_notes = st.text_input(
        "Notas (verificación, aclaraciones)",
        value=ing.get("allergen_notes") or "",
        key=f"{col_id}_notes",
        placeholder="e.g. 'Verificar etiqueta Valrhona' or leave blank"
    )

    if st.button("💾 Guardar alérgenos", key=f"{col_id}_save_al"):
        record = {"id": ing["id"], "allergen_notes": allergen_notes or None}
        record.update(allergen_vals)
        db.save_ingredient_allergens(record)
        st.success("Alérgenos guardados", icon="✅")
        st.rerun()


def _add_ingredient_form():
    """Form for adding a new ingredient with fuzzy duplicate detection."""
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

    if name:
        existing_names = [i["name"] for i in db.get_ingredients()]
        similar = db.find_similar_names(name, existing_names)
        if similar:
            st.warning("⚠️ Similar ingredient name(s) already exist:")
            for match, score in similar:
                st.markdown(f"&nbsp;&nbsp;&nbsp;`{match}` ({score}% similar)")
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
            st.error("Please confirm this is different from the similar items above.")
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
