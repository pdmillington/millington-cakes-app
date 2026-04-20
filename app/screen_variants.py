# screen_variants.py
# =============================================================================
# Product variants editor — one variant per recipe + format + channel.
# Each variant holds all the data needed to generate a ficha técnica:
#   description, weight, packaging, storage, shelf life, allergen declaration,
#   ingredient label text, and current prices.
#
# Variants can be standard (both channels), wholesale-only, or retail-only.
# Wholesale variants can specify a units_per_pack for box configurations.
# =============================================================================

import streamlit as st
import millington_db as db


# Format display names
FORMAT_LABELS = {
    "standard":   "Tarta estándar",
    "individual": "Individual",
    "bocado":     "Bocado",
}

CHANNEL_LABELS = {
    "both": "Mayorista y minorista",
    "WS":   "Solo mayorista",
    "GW":   "Solo minorista",
}


def screen_variants():
    st.title("Variantes de producto")
    st.caption(
        "Cada variante define una combinación de receta + formato + canal. "
        "Los datos aquí son la fuente de verdad para la generación del ficha técnica."
    )

    # ── Load data ─────────────────────────────────────────────────────────────
    recipes  = db.get_recipes()
    presets  = db.get_packaging_presets()

    recipe_map   = {r["name"]: r for r in recipes}
    recipe_names = sorted([r["name"] for r in recipes])
    preset_map   = {p["id"]: p for p in presets}
    preset_names = {p["name"]: p["id"] for p in presets}

    col_list, col_detail = st.columns([1, 2.5])

    # ── Recipe selector ───────────────────────────────────────────────────────
    with col_list:
        st.markdown("**Seleccionar receta**")

        search = st.text_input(
            "Buscar", placeholder="Filtrar recetas…",
            label_visibility="collapsed"
        )

        filtered_names = [
            n for n in recipe_names
            if search.lower() in n.lower()
        ] if search else recipe_names

        selected_name = st.session_state.get("var_selected_recipe")

        for name in filtered_names:
            r = recipe_map[name]
            # Count existing variants
            variants = db.get_variants_for_recipe(r["id"])
            approved = sum(1 for v in variants if v.get("label_approved"))
            label    = (
                f"{name}  ✅ {approved}/{len(variants)}"
                if variants else f"{name}  —"
            )
            if st.button(
                label, key=f"var_btn_{r['id']}",
                use_container_width=True,
                type="primary" if selected_name == r["id"] else "secondary"
            ):
                st.session_state.var_selected_recipe = r["id"]
                st.session_state.pop("var_selected_variant", None)
                st.rerun()

    # ── Variant list and editor ───────────────────────────────────────────────
    with col_detail:
        recipe_id = st.session_state.get("var_selected_recipe")

        if not recipe_id:
            st.info("Selecciona una receta de la lista.")
            return

        recipe   = db.get_recipe(recipe_id)
        variants = db.get_variants_for_recipe(recipe_id)

        st.markdown(f"### {recipe['name']}")
        st.caption(
            f"Tipo: {recipe.get('size_type','diameter')} · "
            f"Referencia: {_ref_size_desc(recipe)}"
        )

        # ── Existing variants ─────────────────────────────────────────────────
        if variants:
            st.markdown("**Variantes existentes**")
            for v in variants:
                _variant_card(v, recipe, preset_map, preset_names, presets)

        st.divider()

        # ── Add new variant ───────────────────────────────────────────────────
        with st.expander("➕ Añadir nueva variante"):
            _add_variant_form(recipe_id, presets, preset_names, variants)


def _ref_size_desc(recipe: dict) -> str:
    st = recipe.get("size_type", "diameter")
    if st == "diameter":
        d = recipe.get("ref_diameter_cm", "")
        h = recipe.get("ref_height_cm", "")
        return f"{d}cm diámetro" + (f" × {h}cm alto" if h else "")
    elif st == "weight":
        return f"{recipe.get('ref_weight_kg', '')}kg"
    else:
        return f"{recipe.get('ref_portions', '')} porciones"


def _variant_card(
    v: dict, recipe: dict,
    preset_map: dict, preset_names: dict,
    presets: list
):
    """Render one variant as an expandable card."""
    fmt     = v.get("format", "standard")
    channel = v.get("channel", "both")
    units   = int(v.get("units_per_pack") or 1)
    approved = bool(v.get("label_approved"))

    # Card label
    status  = "✅" if approved else "⚠️ Pendiente"
    label   = (
        f"{status}  {FORMAT_LABELS.get(fmt, fmt)}  ·  "
        f"{CHANNEL_LABELS.get(channel, channel)}"
        + (f"  ·  ×{units}" if units > 1 else "")
    )

    with st.expander(label):
        _variant_editor(v, recipe, preset_map, preset_names, presets)


def _variant_editor(
    v: dict, recipe: dict,
    preset_map: dict, preset_names: dict,
    presets: list
):
    """Full editor for an existing variant."""
    vid = v["id"]
    p   = f"ve_{vid}"

    # ── Basic info ────────────────────────────────────────────────────────────
    st.markdown("#### Información básica")

    b1, b2, b3 = st.columns(3)
    with b1:
        fmt = st.selectbox(
            "Formato",
            options=list(FORMAT_LABELS.keys()),
            format_func=lambda x: FORMAT_LABELS[x],
            index=list(FORMAT_LABELS.keys()).index(
                v.get("format", "standard")
            ),
            key=f"{p}_format"
        )
    with b2:
        channel = st.selectbox(
            "Canal",
            options=list(CHANNEL_LABELS.keys()),
            format_func=lambda x: CHANNEL_LABELS[x],
            index=list(CHANNEL_LABELS.keys()).index(
                v.get("channel", "both")
            ),
            key=f"{p}_channel"
        )
    with b3:
        units_per_pack = st.number_input(
            "Unidades por caja",
            min_value=1,
            value=int(v.get("units_per_pack") or 1),
            key=f"{p}_units",
            help="Para variantes mayoristas con caja de 12, 24 uds. etc."
        )

    c1, c2 = st.columns(2)
    with c1:
        sku_code = st.text_input(
            "Código SKU",
            value=v.get("sku_code") or "",
            key=f"{p}_sku",
            placeholder="e.g. LP-01-TI-WS"
        )
    with c2:
        size_description = st.text_input(
            "Descripción de tamaño",
            value=v.get("size_description") or "",
            key=f"{p}_size",
            placeholder="e.g. 8 cm diámetro"
        )

    ref_weight_g = st.number_input(
        "Peso aproximado (g)",
        min_value=0.0,
        value=float(v.get("ref_weight_g") or 0),
        key=f"{p}_weight",
        help="Peso del producto terminado — aparece en el ficha"
    )

    # ── Description ───────────────────────────────────────────────────────────
    st.markdown("#### Descripción (español)")
    description_es = st.text_area(
        "Descripción",
        value=v.get("description_es") or "",
        key=f"{p}_desc",
        height=100,
        label_visibility="collapsed",
        placeholder="Descripción del producto para el ficha técnico..."
    )

    # ── Packaging & storage ───────────────────────────────────────────────────
    st.markdown("#### Embalaje y conservación")

    pk1, pk2 = st.columns(2)
    with pk1:
        packaging_desc = st.text_input(
            "Embalaje",
            value=v.get("packaging_desc") or "",
            key=f"{p}_pack",
            placeholder="e.g. Caja de cartón y base de cartón"
        )
    with pk2:
        # Packaging preset
        preset_options = ["— ninguno —"] + [p2["name"] for p2 in presets]
        current_preset_id = v.get("packaging_preset_id")
        current_preset    = preset_map.get(current_preset_id, {})
        current_preset_name = current_preset.get("name", "— ninguno —")
        if current_preset_name not in preset_options:
            current_preset_name = "— ninguno —"

        selected_preset_name = st.selectbox(
            "Preset de embalaje",
            options=preset_options,
            index=preset_options.index(current_preset_name),
            key=f"{p}_preset"
        )
        selected_preset_id = preset_names.get(selected_preset_name)

    s1, s2 = st.columns(2)
    with s1:
        storage_instructions = st.text_input(
            "Conservación",
            value=v.get("storage_instructions") or "Refrigerada entre 0 - 5°C",
            key=f"{p}_storage"
        )
    with s2:
        shelf_life_hours = st.number_input(
            "Vida útil (horas)",
            min_value=1,
            value=int(v.get("shelf_life_hours") or 24),
            key=f"{p}_shelf"
        )

    # ── Prices ────────────────────────────────────────────────────────────────
    st.markdown("#### Precios actuales")
    pr1, pr2 = st.columns(2)
    with pr1:
        ws_price = st.number_input(
            "Precio mayorista ex-IVA (€)",
            min_value=0.0, format="%.4f",
            value=float(v.get("ws_price_ex_vat") or 0),
            key=f"{p}_ws"
        )
    with pr2:
        rt_price = st.number_input(
            "Precio minorista inc-IVA (€)",
            min_value=0.0, format="%.4f",
            value=float(v.get("rt_price_inc_vat") or 0),
            key=f"{p}_rt"
        )

    # ── Allergen declaration preview ──────────────────────────────────────────
    st.markdown("#### Declaración de alérgenos")
    st.caption(
        "Calculado automáticamente — revisa antes de aprobar."
    )

    with st.spinner("Calculando alérgenos..."):
        declaration = db.get_allergen_declaration(recipe["id"])

    if declaration["warnings"]:
        for w in declaration["warnings"]:
            st.warning(w)

    al1, al2 = st.columns(2)
    with al1:
        st.markdown("**Contiene:**")
        if declaration["contiene"]:
            for item in declaration["contiene"]:
                st.markdown(f"- {item.capitalize()}")
        else:
            st.caption("Ninguno detectado")
    with al2:
        st.markdown("**Puede contener:**")
        if declaration["puede_contener"]:
            for item in declaration["puede_contener"]:
                st.markdown(f"- {item.capitalize()}")
        else:
            st.caption("Ninguno")

    # ── Ingredient label text ─────────────────────────────────────────────────
    st.markdown("#### Lista de ingredientes (etiqueta)")

    label_data    = db.get_ingredient_label_text(recipe["id"])
    auto_text     = label_data.get("label_text", "")
    current_label = v.get("ingredient_label_es") or auto_text

    if auto_text and not v.get("ingredient_label_es"):
        st.caption(
            "Borrador generado automáticamente — edita si es necesario "
            "y marca como aprobado al guardar."
        )

    ingredient_label_es = st.text_area(
        "Lista de ingredientes",
        value=current_label,
        key=f"{p}_label",
        height=120,
        label_visibility="collapsed",
        help="Ordenado por peso descendente. Los alérgenos aparecerán "
             "en negrita en el ficha generado."
    )

    if auto_text:
        if st.button(
            "↺ Regenerar desde receta",
            key=f"{p}_regen",
            help="Sobreescribe el texto actual con el borrador generado"
        ):
            st.session_state[f"{p}_label"] = auto_text
            st.rerun()

    label_approved = st.checkbox(
        "✅ Lista de ingredientes aprobada",
        value=bool(v.get("label_approved")),
        key=f"{p}_approved",
        help="Marca como aprobada cuando el texto sea correcto. "
             "Solo las variantes aprobadas pueden generar fichas."
    )

    # ── Save / Delete ─────────────────────────────────────────────────────────
    st.divider()
    col_save, col_del = st.columns([2, 1])

    with col_save:
        if st.button(
            "💾 Guardar variante", type="primary",
            use_container_width=True,
            key=f"{p}_save"
        ):
            db.save_variant({
                "id":                    vid,
                "format":                fmt,
                "channel":               channel,
                "units_per_pack":        units_per_pack,
                "sku_code":              sku_code or None,
                "size_description":      size_description or None,
                "ref_weight_g":          ref_weight_g or None,
                "description_es":        description_es or None,
                "packaging_desc":        packaging_desc or None,
                "packaging_preset_id":   selected_preset_id,
                "storage_instructions":  storage_instructions or None,
                "shelf_life_hours":      shelf_life_hours,
                "ws_price_ex_vat":       ws_price or None,
                "rt_price_inc_vat":      rt_price or None,
                "ingredient_label_es":   ingredient_label_es or None,
                "label_approved":        label_approved,
            })
            st.success("Variante guardada", icon="✅")
            st.rerun()

    with col_del:
        if st.button(
            "🗑 Eliminar variante",
            use_container_width=True,
            key=f"{p}_del"
        ):
            db.delete_variant(vid)
            st.rerun()


def _add_variant_form(
    recipe_id: str,
    presets: list,
    preset_names: dict,
    existing_variants: list
):
    """Form to add a new variant to a recipe."""
    st.caption(
        "Crea una nueva variante para este receta. "
        "Una variante puede ser un formato diferente, canal diferente, "
        "o una configuración de caja específica (e.g. caja mayorista de 12)."
    )

    # Suggest formats not yet covered
    existing = {
        (v.get("format"), v.get("channel", "both"))
        for v in existing_variants
    }

    a1, a2, a3 = st.columns(3)
    with a1:
        new_fmt = st.selectbox(
            "Formato",
            options=list(FORMAT_LABELS.keys()),
            format_func=lambda x: FORMAT_LABELS[x],
            key="new_var_format"
        )
    with a2:
        new_channel = st.selectbox(
            "Canal",
            options=list(CHANNEL_LABELS.keys()),
            format_func=lambda x: CHANNEL_LABELS[x],
            key="new_var_channel"
        )
    with a3:
        new_units = st.number_input(
            "Unidades por caja",
            min_value=1, value=1,
            key="new_var_units",
            help="1 = unidad individual. 12, 24 etc. para cajas mayoristas."
        )

    # Warn if this format+channel already exists
    if (new_fmt, new_channel) in existing and new_units == 1:
        st.warning(
            f"Ya existe una variante {FORMAT_LABELS[new_fmt]} / "
            f"{CHANNEL_LABELS[new_channel]}. Puedes añadir otra con "
            "un número de unidades diferente."
        )

    new_size = st.text_input(
        "Descripción de tamaño",
        key="new_var_size",
        placeholder="e.g. 8 cm diámetro  ·  6 cm diámetro  ·  3.5 cm diámetro"
    )
    new_weight = st.number_input(
        "Peso aproximado (g)",
        min_value=0.0, key="new_var_weight"
    )
    new_storage = st.text_input(
        "Conservación",
        value="Refrigerada entre 0 - 5°C",
        key="new_var_storage"
    )
    new_shelf = st.number_input(
        "Vida útil (horas)",
        min_value=1, value=24,
        key="new_var_shelf"
    )

    if st.button("Añadir variante", type="primary"):
        db.save_variant({
            "recipe_id":            recipe_id,
            "format":               new_fmt,
            "channel":              new_channel,
            "units_per_pack":       new_units,
            "size_description":     new_size or None,
            "ref_weight_g":         new_weight or None,
            "storage_instructions": new_storage or None,
            "shelf_life_hours":     new_shelf,
            "label_approved":       False,
        })
        st.success("Variante añadida", icon="✅")
        st.rerun()
