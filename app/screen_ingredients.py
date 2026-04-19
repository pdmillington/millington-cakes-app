# screen_ingredients.py
# =============================================================================
# Ingredients screen — two tabs:
#   Tab 1 (Precios)  — pricing, pack sizes, cost per unit
#   Tab 2 (Fichas)   — category, allergens, label name
#
# Allergens follow EU 1169/2011 / RD 126/2015 (14 mandatory allergens).
# Three states: 0=No, 1=Contiene, 2=Puede contener (supplier declared).
# Allergens are inherited from the ingredient category by default.
# Set allergen_override=TRUE on an ingredient to override category values.
# =============================================================================

import streamlit as st
import millington_db as db

# ── Allergen definitions ──────────────────────────────────────────────────────
ALLERGENS = [
    ("allergen_gluten",     "Cereales con gluten",          "Básicos"),
    ("allergen_egg",        "Huevo",                        "Básicos"),
    ("allergen_milk",       "Leche",                        "Básicos"),
    ("allergen_nuts",       "Frutos de cáscara",            "Básicos"),
    ("allergen_peanut",     "Cacahuetes",                   "Otros"),
    ("allergen_soy",        "Soja",                         "Otros"),
    ("allergen_mustard",    "Mostaza",                      "Otros"),
    ("allergen_sesame",     "Sésamo",                       "Otros"),
    ("allergen_sulphites",  "Dióxido de azufre y sulfitos", "Otros"),
    ("allergen_celery",     "Apio",                         "Raros"),
    ("allergen_lupin",      "Altramuces",                   "Raros"),
    ("allergen_fish",       "Pescado",                      "Raros"),
    ("allergen_crustacean", "Crustáceos",                   "Raros"),
    ("allergen_mollusc",    "Moluscos",                     "Raros"),
]

ALLERGEN_OPTIONS = ["No", "Contiene", "Puede contener"]

_UNIT_TO_BASE = {
    "g": 1.0, "kg": 1000.0,
    "ml": 1.0, "l": 1000.0,
    "units": 1.0
}


# =============================================================================
# Main screen
# =============================================================================

def screen_ingredients():
    st.title("Ingredients")

    ingredients = db.get_ingredients()
    categories  = db.get_ingredient_categories()
    cat_map     = {c["id"]: c for c in categories}

    # ── Summary metrics ───────────────────────────────────────────────────────
    total       = len(ingredients)
    no_price    = sum(1 for i in ingredients if not i.get("pack_price_ex_vat"))
    no_size     = sum(1 for i in ingredients if not i.get("pack_size"))
    no_category = sum(1 for i in ingredients
                      if not i.get("category_id")
                      and not i.get("is_sub_recipe"))
    needs_check = sum(
        1 for i in ingredients
        if "verificar" in (i.get("allergen_notes") or "").lower()
        or "needs" in (i.get("allergen_notes") or "").lower()
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total", total)
    c2.metric("Sin precio", no_price,
              help="Sin precio de compra")
    c3.metric("Sin tamaño", no_size,
              help="Sin tamaño de envase")
    c4.metric("Sin categoría", no_category,
              help="Ingredientes sin categoría asignada")
    c5.metric("Verificar alérgenos", needs_check,
              help="Marcados para verificar con etiqueta del proveedor")

    st.divider()

    # ── Search / filter ───────────────────────────────────────────────────────
    col_s, col_sup, col_f = st.columns([3, 1, 1])
    with col_s:
        search = st.text_input(
            "Search", placeholder="Buscar por nombre…",
            label_visibility="collapsed"
        )
    with col_sup:
        suppliers = sorted(set(
            i["supplier"] for i in ingredients if i.get("supplier")
        ))
        supplier_filter = st.selectbox(
            "Proveedor", ["Todos"] + suppliers,
            label_visibility="collapsed"
        )
    with col_f:
        active_filter = st.selectbox(
            "Filtro",
            ["Todos", "Sin categoría", "Verificar alérgenos", "Sin precio"],
            label_visibility="collapsed"
        )

    filtered = [
        i for i in ingredients
        if (search.lower() in i["name"].lower() if search else True)
        and (i.get("supplier") == supplier_filter
             if supplier_filter != "Todos" else True)
        and _filter_match(i, active_filter)
    ]

    st.caption(f"Mostrando {len(filtered)} de {total} ingredientes")
    st.divider()

    # ── Two tabs ──────────────────────────────────────────────────────────────
    tab_pricing, tab_fichas = st.tabs(["💶 Precios", "📋 Fichas y alérgenos"])

    with tab_pricing:
        _pricing_tab(filtered)

    with tab_fichas:
        _fichas_tab(filtered, categories, cat_map)


# =============================================================================
# Tab 1 — Pricing
# =============================================================================

def _pricing_tab(filtered: list):
    h1, h2, h3, h4, h5, h6, h7, h8 = st.columns(
        [3, 2, 1.2, 1, 1.2, 1, 1.5, 0.5]
    )
    h1.markdown("**Nombre**")
    h2.markdown("**Proveedor**")
    h3.markdown("**Envase**")
    h4.markdown("**Ud.**")
    h5.markdown("**Precio s/IVA (€)**")
    h6.markdown("**IVA**")
    h7.markdown("**Coste/ud.**")
    h8.markdown("")

    for ing in filtered:
        if ing.get("is_sub_recipe"):
            continue
        _pricing_row(ing)

    st.divider()
    with st.expander("➕ Añadir nuevo ingrediente"):
        _add_ingredient_form()


def _pricing_row(ing: dict):
    col_id = f"ing_{ing['id']}"

    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(
        [3, 2, 1.2, 1, 1.2, 1, 1.5, 0.5]
    )

    with c1:
        name = st.text_input(
            "Nombre", value=ing.get("name", ""),
            key=f"{col_id}_name", label_visibility="collapsed"
        )
    with c2:
        supplier = st.text_input(
            "Proveedor", value=ing.get("supplier") or "",
            key=f"{col_id}_supplier", label_visibility="collapsed"
        )
    with c3:
        pack_size = st.number_input(
            "Envase", value=float(ing.get("pack_size") or 0),
            min_value=0.0, key=f"{col_id}_size",
            label_visibility="collapsed"
        )
    with c4:
        unit_opts = ["g", "kg", "ml", "l", "units"]
        current_unit = ing.get("pack_unit") or "g"
        if current_unit not in unit_opts:
            current_unit = "g"
        unit = st.selectbox(
            "Ud.", unit_opts,
            index=unit_opts.index(current_unit),
            key=f"{col_id}_unit", label_visibility="collapsed"
        )
    with c5:
        price = st.number_input(
            "Precio", value=float(ing.get("pack_price_ex_vat") or 0),
            min_value=0.0, format="%.4f",
            key=f"{col_id}_price", label_visibility="collapsed"
        )
    with c6:
        vat_opts = [0.0, 0.04, 0.10, 0.21]
        current_vat = float(ing.get("vat_rate") or 0.10)
        if current_vat not in vat_opts:
            current_vat = 0.10
        vat = st.selectbox(
            "IVA", vat_opts,
            index=vat_opts.index(current_vat),
            format_func=lambda x: f"{int(x*100)}%",
            key=f"{col_id}_vat", label_visibility="collapsed"
        )
    with c7:
        factor    = _UNIT_TO_BASE.get(unit, 1.0)
        base_size = pack_size * factor
        cost      = round(price / base_size, 6) \
            if base_size > 0 and price > 0 else None
        base_unit = ("g"    if unit in ("g", "kg")
                     else "ml" if unit in ("ml", "l")
                     else "ud.")
        if cost:
            st.markdown(f"`€ {cost:.5f}/{base_unit}`")
        else:
            st.markdown("—")
    with c8:
        if st.button("💾", key=f"{col_id}_save", help="Guardar cambios"):
            # Flag allergen review if supplier changed
            old_supplier = ing.get("supplier") or ""
            if supplier and supplier != old_supplier:
                current_notes = ing.get("allergen_notes") or ""
                if "verificar" not in current_notes.lower():
                    new_notes = (
                        current_notes +
                        " | Proveedor cambiado — verificar alérgenos"
                        if current_notes
                        else "Proveedor cambiado — verificar alérgenos"
                    )
                    db.save_ingredient_allergens({
                        "id": ing["id"],
                        "allergen_notes": new_notes,
                    })

            if name != ing["name"]:
                existing_names = [
                    i["name"] for i in db.get_ingredients()
                    if i["id"] != ing["id"]
                ]
                similar = db.find_similar_names(name, existing_names)
                if similar:
                    matches = ", ".join(f"'{m}'" for m, _ in similar)
                    st.warning(
                        f"⚠️ Nombre similar a: {matches} — guardado cancelado."
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
            st.success(f"Guardado: {name}", icon="✅")
            st.rerun()


# =============================================================================
# Tab 2 — Fichas y alérgenos
# =============================================================================

def _fichas_tab(filtered: list, categories: list, cat_map: dict):
    st.caption(
        "Selecciona una categoría para heredar el nombre de etiqueta y "
        "alérgenos por defecto. Activa la anulación solo si este ingrediente "
        "específico difiere de su categoría."
    )

    # Category options for selectbox
    cat_options = {"— sin categoría —": None}
    for c in sorted(categories, key=lambda x: x["label_name_es"]):
        cat_options[c["label_name_es"]] = c["id"]

    for ing in filtered:
        _ficha_row(ing, cat_options, cat_map)


def _ficha_row(ing: dict, cat_options: dict, cat_map: dict):
    col_id   = f"fi_{ing['id']}"
    cat_id   = ing.get("category_id")
    cat      = cat_map.get(cat_id, {}) if cat_id else {}
    cat_label = cat.get("label_name_es", "—")
    override  = bool(ing.get("allergen_override"))
    is_sub    = bool(ing.get("is_sub_recipe"))
    notes     = ing.get("allergen_notes") or ""
    needs_check = (
        "verificar" in notes.lower() or "needs" in notes.lower()
    )

    # Allergen summary for the expander label
    summary = _allergen_summary(ing, cat, override)
    if is_sub:
        label = f"🔧 {ing['name']}  —  sub-receta"
    elif needs_check:
        label = f"⚠️ {ing['name']}  ·  {cat_label}  ·  {summary}"
    else:
        label = f"{ing['name']}  ·  {cat_label}  ·  {summary}"

    with st.expander(label):
        if is_sub:
            st.caption(
                "Sub-receta interna — alérgenos calculados automáticamente "
                "a partir de sus ingredientes durante la generación del ficha."
            )
            return

        # ── Category selector ─────────────────────────────────────────────────
        col_a, col_b = st.columns([3, 1])
        with col_a:
            current_label = cat_label if cat_label in cat_options \
                else "— sin categoría —"
            selected_label = st.selectbox(
                "Categoría (nombre de etiqueta)",
                options=list(cat_options.keys()),
                index=list(cat_options.keys()).index(current_label),
                key=f"{col_id}_cat",
                help="Nombre legal que aparece en el listado de ingredientes "
                     "del ficha (EU 1169/2011)."
            )
            selected_cat_id = cat_options[selected_label]
            selected_cat    = cat_map.get(selected_cat_id, {}) \
                if selected_cat_id else {}

        with col_b:
            override_new = st.toggle(
                "Anular categoría",
                value=override,
                key=f"{col_id}_override",
                help="Activa solo si este proveedor declara alérgenos "
                     "distintos a los de la categoría."
            )

        # ── Allergen profile ──────────────────────────────────────────────────
        allergen_vals = {}

        if not override_new and selected_cat:
            st.caption(
                f"Alérgenos heredados de **{selected_label}** — "
                "activa la anulación para editar."
            )
            _allergen_display(selected_cat)

        elif override_new:
            st.caption("🔓 Anulación activa — editando alérgenos individuales.")
            if selected_cat and not override:
                st.info(
                    "Valores iniciales de la categoría. "
                    "Modifica solo los que difieran."
                )
            allergen_vals = _allergen_editor_grid(ing, selected_cat, col_id)

        else:
            st.warning(
                "Sin categoría — selecciona una categoría o "
                "introduce alérgenos manualmente."
            )
            allergen_vals = _allergen_editor_grid(ing, {}, col_id)

        # ── Notes ─────────────────────────────────────────────────────────────
        new_notes = st.text_input(
            "Notas de verificación",
            value=notes,
            key=f"{col_id}_notes",
            placeholder="e.g. Verificar etiqueta Valrhona · dejar vacío si OK"
        )

        # ── Save ──────────────────────────────────────────────────────────────
        if st.button(
            "💾 Guardar datos ficha",
            key=f"{col_id}_save_fi",
            type="primary"
        ):
            record = {
                "id":                ing["id"],
                "category_id":       selected_cat_id,
                "allergen_override": override_new,
                "allergen_notes":    new_notes or None,
            }
            if override_new and allergen_vals:
                record.update(allergen_vals)
            elif not override_new:
                # Reset ingredient allergen fields to 0 —
                # effective allergens come from the category
                for field, _, _ in ALLERGENS:
                    record[field] = 0

            db.save_ingredient_allergens(record)
            st.success("Datos ficha guardados", icon="✅")
            st.rerun()


def _allergen_display(cat: dict):
    """Read-only compact allergen summary from a category."""
    ICONS = {0: "✅ No", 1: "🔴 Contiene", 2: "🟡 Puede"}
    groups = {}
    for field, name, group in ALLERGENS:
        groups.setdefault(group, []).append((field, name))

    for group_name, items in groups.items():
        st.markdown(f"**{group_name}**")
        cols = st.columns(len(items))
        for col, (field, display) in zip(cols, items):
            val = int(cat.get(field) or 0)
            if val > 0:
                with col:
                    st.caption(display)
                    st.markdown(ICONS[val])


def _allergen_editor_grid(ing: dict, cat: dict, col_id: str) -> dict:
    """Editable allergen grid seeded from ingredient (if override) or category."""
    groups = {}
    for field, name, group in ALLERGENS:
        groups.setdefault(group, []).append((field, name))

    allergen_vals = {}

    for group_name, items in groups.items():
        st.markdown(f"**{group_name}**")
        for i in range(0, len(items), 2):
            pair = items[i:i+2]
            cols = st.columns(2)
            for col, (field, display) in zip(cols, pair):
                with col:
                    current = (
                        int(ing.get(field) or 0)
                        if ing.get("allergen_override")
                        else int(cat.get(field) or 0)
                    )
                    allergen_vals[field] = st.radio(
                        display,
                        options=[0, 1, 2],
                        format_func=lambda x: ALLERGEN_OPTIONS[x],
                        index=current,
                        key=f"{col_id}_{field}",
                        horizontal=True
                    )

    return allergen_vals


def _allergen_summary(ing: dict, cat: dict, override: bool) -> str:
    """Compact summary — uses ingredient values if override, else category."""
    source   = ing if override else cat
    contiene = []
    puede    = []
    for field, name, _ in ALLERGENS:
        val   = int(source.get(field) or 0)
        short = name.split()[0]
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


# =============================================================================
# Filter helper
# =============================================================================

def _filter_match(ing: dict, filter_val: str) -> bool:
    if filter_val == "Todos":
        return True
    if filter_val == "Sin categoría":
        return not ing.get("category_id") and not ing.get("is_sub_recipe")
    if filter_val == "Verificar alérgenos":
        notes = (ing.get("allergen_notes") or "").lower()
        return "verificar" in notes or "needs" in notes
    if filter_val == "Sin precio":
        return not ing.get("pack_price_ex_vat")
    return True


# =============================================================================
# Add new ingredient form
# =============================================================================

def _add_ingredient_form():
    st.caption(
        "Añade los datos de compra aquí. Asigna la categoría en la pestaña "
        "Fichas para heredar nombre de etiqueta y alérgenos."
    )

    c1, c2, c3, c4, c5, c6 = st.columns([3, 2, 1.2, 1, 1.2, 1])

    with c1:
        name = st.text_input(
            "Nombre", key="new_ing_name",
            placeholder="e.g. Chocolate Negro 72% Valrhona"
        )
    with c2:
        supplier = st.text_input(
            "Proveedor", key="new_ing_supplier",
            placeholder="e.g. Valrhona"
        )
    with c3:
        pack_size = st.number_input(
            "Envase", min_value=0.0, key="new_ing_size"
        )
    with c4:
        unit = st.selectbox(
            "Ud.", ["g", "kg", "ml", "l", "units"],
            key="new_ing_unit"
        )
    with c5:
        price = st.number_input(
            "Precio s/IVA (€)", min_value=0.0,
            format="%.4f", key="new_ing_price"
        )
    with c6:
        vat = st.selectbox(
            "IVA", [0.0, 0.04, 0.10, 0.21],
            index=2,
            format_func=lambda x: f"{int(x*100)}%",
            key="new_ing_vat"
        )

    confirmed = True
    if name:
        existing_names = [i["name"] for i in db.get_ingredients()]
        similar = db.find_similar_names(name, existing_names)
        if similar:
            st.warning("⚠️ Nombres similares ya existen:")
            for match, score in similar:
                st.markdown(f"&nbsp;&nbsp;&nbsp;`{match}` ({score}% similar)")
            confirmed = st.checkbox(
                "Es un ingrediente diferente — guardar de todas formas",
                key="new_ing_confirmed"
            )
    else:
        confirmed = False

    if st.button("Añadir ingrediente", type="primary"):
        if not name:
            st.error("El nombre es obligatorio.")
        elif not confirmed:
            st.error(
                "Confirma que es diferente de los ingredientes similares."
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
            st.success(
                f"Añadido: {name} — asigna la categoría en Fichas",
                icon="✅"
            )
            st.rerun()
