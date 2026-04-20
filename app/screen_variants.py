# screen_variants.py
# =============================================================================
# Product variants editor.
# One variant per recipe + format + channel combination.
# Each variant holds all data needed to generate a ficha técnica.
#
# Pre-population pattern: values written to session state before rerun,
# so widgets render correctly on the next pass (same as recipe editor).
# Allergen declaration is deferred behind a button to avoid slow renders.
# =============================================================================

import streamlit as st
import millington_db as db

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


# =============================================================================
# Main screen
# =============================================================================

def screen_variants():
    st.title("Variantes de producto")
    st.caption(
        "Cada variante define una combinación de receta + formato + canal. "
        "Aprueba la lista de ingredientes antes de generar el ficha."
    )

    recipes  = db.get_recipes()
    presets  = db.get_packaging_presets()

    recipe_map   = {r["id"]: r for r in recipes}
    recipe_names = sorted([r["name"] for r in recipes], key=str.lower)
    preset_list  = [{"id": None, "name": "— ninguno —"}] + presets
    preset_by_id = {p["id"]: p for p in presets}
    preset_by_name = {p["name"]: p["id"] for p in presets}

    col_list, col_detail = st.columns([1, 2.5])

    # ── Recipe list ───────────────────────────────────────────────────────────
    with col_list:
        st.markdown("**Receta**")

        search = st.text_input(
            "Buscar", placeholder="Filtrar…",
            label_visibility="collapsed",
            key="var_search"
        )

        displayed = [
            n for n in recipe_names
            if search.lower() in n.lower()
        ] if search else recipe_names

        selected_rid = st.session_state.get("var_recipe_id")

        for name in displayed:
            r        = next(x for x in recipes if x["name"] == name)
            variants = db.get_variants_for_recipe(r["id"])
            approved = sum(1 for v in variants if v.get("label_approved"))
            badge    = f" ✅{approved}/{len(variants)}" if variants else " —"

            if st.button(
                name + badge,
                key=f"var_rbtn_{r['id']}",
                use_container_width=True,
                type="primary" if selected_rid == r["id"] else "secondary"
            ):
                st.session_state.var_recipe_id  = r["id"]
                st.session_state.var_variant_id = None
                st.rerun()

    # ── Detail panel ──────────────────────────────────────────────────────────
    with col_detail:
        rid = st.session_state.get("var_recipe_id")
        if not rid:
            st.info("Selecciona una receta de la lista.")
            return

        recipe   = recipe_map.get(rid, {})
        variants = db.get_variants_for_recipe(rid)

        st.markdown(f"### {recipe.get('name','')}")
        st.caption(_ref_size_desc(recipe))

        # Variant selector tabs when there are multiple variants
        if variants:
            tab_labels = [_variant_tab_label(v) for v in variants]
            tab_labels.append("➕ Nueva")
            tabs = st.tabs(tab_labels)

            for tab, v in zip(tabs[:-1], variants):
                with tab:
                    _variant_form(
                        v, rid, recipe, preset_list,
                        preset_by_id, preset_by_name
                    )

            with tabs[-1]:
                _new_variant_form(rid, presets, preset_by_name, variants)
        else:
            st.info("No hay variantes — añade la primera abajo.")
            _new_variant_form(rid, presets, preset_by_name, variants)


# =============================================================================
# Variant form
# =============================================================================

def _variant_form(
    v: dict, rid: str, recipe: dict,
    preset_list: list, preset_by_id: dict, preset_by_name: dict
):
    """Render the full editor for an existing variant."""
    vid = v["id"]
    p   = f"vf_{vid}"

    # ── Load into session state on first render ────────────────────────────
    # Uses the same pattern as screen_recipes — write before render.
    if f"{p}_loaded" not in st.session_state:
        _load_variant(v, preset_by_id, p)

    # ── Section 1: Format / Channel / Pack ────────────────────────────────
    st.markdown("#### Formato y canal")
    c1, c2, c3 = st.columns(3)

    with c1:
        fmt = st.selectbox(
            "Formato",
            options=list(FORMAT_LABELS.keys()),
            format_func=lambda x: FORMAT_LABELS[x],
            key=f"{p}_format"
        )
    with c2:
        channel = st.selectbox(
            "Canal",
            options=list(CHANNEL_LABELS.keys()),
            format_func=lambda x: CHANNEL_LABELS[x],
            key=f"{p}_channel"
        )
    with c3:
        units_per_pack = st.number_input(
            "Uds. por caja",
            min_value=1,
            key=f"{p}_units",
            help="1 = unidad. 12, 24 etc. para cajas mayoristas."
        )

    # ── Section 2: SKUs (conditional on channel) ─────────────────────────
    st.markdown("#### SKUs")
    if channel in ("both", "WS"):
        sku_ws = st.text_input(
            "SKU Mayorista",
            key=f"{p}_sku_ws",
            placeholder="e.g. LP-01-TI-WS"
        )
    else:
        sku_ws = None

    if channel in ("both", "GW"):
        sku_gw = st.text_input(
            "SKU Minorista",
            key=f"{p}_sku_gw",
            placeholder="e.g. LP-01-TI-GW"
        )
    else:
        sku_gw = None

    # ── Section 3: Size & weight ──────────────────────────────────────────
    st.markdown("#### Tamaño y peso")
    s1, s2 = st.columns(2)
    with s1:
        size_description = st.text_input(
            "Descripción de tamaño",
            key=f"{p}_size",
            placeholder="e.g. 8 cm diámetro"
        )
    with s2:
        ref_weight_g = st.number_input(
            "Peso aprox. (g)",
            min_value=0.0,
            key=f"{p}_weight"
        )

    # ── Section 4: Description ────────────────────────────────────────────
    st.markdown("#### Descripción")
    description_es = st.text_area(
        "Descripción",
        key=f"{p}_desc",
        height=90,
        label_visibility="collapsed",
        placeholder="Descripción del producto en español…"
    )

    # ── Section 5: Packaging & storage ───────────────────────────────────
    st.markdown("#### Embalaje y conservación")
    pk1, pk2 = st.columns(2)
    with pk1:
        packaging_desc = st.text_input(
            "Descripción de embalaje",
            key=f"{p}_packdesc",
            placeholder="e.g. Caja de cartón y base de cartón"
        )
    with pk2:
        preset_names_list = [pl["name"] for pl in preset_list]
        selected_preset_name = st.selectbox(
            "Preset de embalaje",
            options=preset_names_list,
            key=f"{p}_preset"
        )
        selected_preset_id = preset_by_name.get(selected_preset_name)

    sv1, sv2 = st.columns(2)
    with sv1:
        storage_instructions = st.text_input(
            "Conservación",
            key=f"{p}_storage"
        )
    with sv2:
        shelf_life_hours = st.number_input(
            "Vida útil (horas)",
            min_value=1,
            key=f"{p}_shelf"
        )

    # ── Section 6: Prices ─────────────────────────────────────────────────
    st.markdown("#### Precios")
    pr1, pr2 = st.columns(2)
    with pr1:
        if channel in ("both", "WS"):
            ws_price = st.number_input(
                "Precio mayorista ex-IVA (€)",
                min_value=0.0, format="%.4f",
                key=f"{p}_ws"
            )
        else:
            ws_price = None
    with pr2:
        if channel in ("both", "GW"):
            rt_price = st.number_input(
                "Precio minorista inc-IVA (€)",
                min_value=0.0, format="%.4f",
                key=f"{p}_rt"
            )
        else:
            rt_price = None

    # ── Section 7: Ingredient label text ──────────────────────────────────
    st.markdown("#### Lista de ingredientes (etiqueta)")

    ingredient_label_es = st.text_area(
        "Lista de ingredientes",
        key=f"{p}_label",
        height=110,
        label_visibility="collapsed",
        help="Ordenado por peso descendente. Alérgenos en negrita en el ficha."
    )

    col_regen, col_blank = st.columns([1, 3])
    with col_regen:
        if st.button("↺ Regenerar", key=f"{p}_regen",
                     help="Sobreescribe con borrador generado desde la receta"):
            label_data = db.get_ingredient_label_text(rid)
            st.session_state[f"{p}_label"] = label_data.get("label_text", "")
            st.rerun()

    label_approved = st.checkbox(
        "✅ Lista de ingredientes aprobada",
        key=f"{p}_approved",
        help="Solo las variantes aprobadas pueden generar fichas."
    )

    # ── Section 8: Allergen declaration (on demand) ───────────────────────
    st.markdown("#### Declaración de alérgenos")

    if st.button("Calcular alérgenos", key=f"{p}_calc_al"):
        declaration = db.get_allergen_declaration(rid)
        st.session_state[f"{p}_declaration"] = declaration

    declaration = st.session_state.get(f"{p}_declaration")
    if declaration:
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
    else:
        st.caption(
            "Pulsa 'Calcular alérgenos' para ver la declaración generada "
            "automáticamente desde los ingredientes de la receta."
        )

    # ── Save / Delete ─────────────────────────────────────────────────────
    st.divider()
    col_save, col_del = st.columns([2, 1])

    with col_save:
        if st.button(
            "💾 Guardar variante", type="primary",
            use_container_width=True, key=f"{p}_save"
        ):
            db.save_variant({
                "id":                   vid,
                "format":               fmt,
                "channel":              channel,
                "units_per_pack":       units_per_pack,
                "sku_ws":               sku_ws or None,
                "sku_gw":               sku_gw or None,
                "size_description":     size_description or None,
                "ref_weight_g":         ref_weight_g or None,
                "description_es":       description_es or None,
                "packaging_desc":       packaging_desc or None,
                "packaging_preset_id":  selected_preset_id,
                "storage_instructions": storage_instructions or None,
                "shelf_life_hours":     shelf_life_hours,
                "ws_price_ex_vat":      ws_price or None,
                "rt_price_inc_vat":     rt_price or None,
                "ingredient_label_es":  ingredient_label_es or None,
                "label_approved":       label_approved,
            })
            # Clear loaded flag so form reloads fresh values
            st.session_state.pop(f"{p}_loaded", None)
            st.session_state.pop(f"{p}_declaration", None)
            st.success("Variante guardada", icon="✅")
            st.rerun()

    with col_del:
        if st.button(
            "🗑 Eliminar", use_container_width=True,
            key=f"{p}_del"
        ):
            db.delete_variant(vid)
            st.session_state.pop(f"{p}_loaded", None)
            st.session_state.pop(f"{p}_declaration", None)
            st.rerun()


# =============================================================================
# New variant form
# =============================================================================

def _new_variant_form(
    rid: str, presets: list,
    preset_by_name: dict, existing_variants: list
):
    st.caption(
        "Añade un nuevo formato, canal o configuración de caja. "
        "Puedes tener varias variantes del mismo formato con "
        "diferentes tamaños de caja mayorista."
    )

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
            key="nv_format"
        )
    with a2:
        new_channel = st.selectbox(
            "Canal",
            options=list(CHANNEL_LABELS.keys()),
            format_func=lambda x: CHANNEL_LABELS[x],
            key="nv_channel"
        )
    with a3:
        new_units = st.number_input(
            "Uds. por caja", min_value=1, value=1,
            key="nv_units"
        )

    if (new_fmt, new_channel) in existing and new_units == 1:
        st.warning(
            f"Ya existe {FORMAT_LABELS[new_fmt]} / "
            f"{CHANNEL_LABELS[new_channel]}. Añade con "
            "número de unidades distinto para cajas específicas."
        )

    b1, b2 = st.columns(2)
    with b1:
        new_size = st.text_input(
            "Tamaño", key="nv_size",
            placeholder="e.g. 8 cm diámetro"
        )
    with b2:
        new_weight = st.number_input(
            "Peso aprox. (g)", min_value=0.0, key="nv_weight"
        )

    c1, c2 = st.columns(2)
    with c1:
        new_storage = st.text_input(
            "Conservación", key="nv_storage",
            value="Refrigerada entre 0 - 5°C"
        )
    with c2:
        new_shelf = st.number_input(
            "Vida útil (h)", min_value=1, value=24, key="nv_shelf"
        )

    if st.button("Añadir variante", type="primary", key="nv_add"):
        db.save_variant({
            "recipe_id":            rid,
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


# =============================================================================
# Helpers
# =============================================================================

def _load_variant(v: dict, preset_by_id: dict, p: str):
    """Write variant values into session state before render."""
    st.session_state[f"{p}_format"]   = v.get("format", "standard")
    st.session_state[f"{p}_channel"]  = v.get("channel", "both")
    st.session_state[f"{p}_units"]    = int(v.get("units_per_pack") or 1)
    st.session_state[f"{p}_sku_ws"]   = v.get("sku_ws") or ""
    st.session_state[f"{p}_sku_gw"]   = v.get("sku_gw") or ""
    st.session_state[f"{p}_size"]     = v.get("size_description") or ""
    st.session_state[f"{p}_weight"]   = float(v.get("ref_weight_g") or 0)
    st.session_state[f"{p}_desc"]     = v.get("description_es") or ""
    st.session_state[f"{p}_packdesc"] = v.get("packaging_desc") or ""
    st.session_state[f"{p}_storage"]  = (
        v.get("storage_instructions") or "Refrigerada entre 0 - 5°C"
    )
    st.session_state[f"{p}_shelf"]    = int(v.get("shelf_life_hours") or 24)
    st.session_state[f"{p}_ws"]       = float(v.get("ws_price_ex_vat") or 0)
    st.session_state[f"{p}_rt"]       = float(v.get("rt_price_inc_vat") or 0)
    st.session_state[f"{p}_label"]    = v.get("ingredient_label_es") or ""
    st.session_state[f"{p}_approved"] = bool(v.get("label_approved"))

    # Preset name
    preset_id   = v.get("packaging_preset_id")
    preset      = preset_by_id.get(preset_id, {})
    preset_name = preset.get("name", "— ninguno —")
    st.session_state[f"{p}_preset"] = preset_name

    # Mark as loaded so we don't overwrite user edits on rerun
    st.session_state[f"{p}_loaded"] = True


def _variant_tab_label(v: dict) -> str:
    fmt     = FORMAT_LABELS.get(v.get("format", "standard"), "?")
    channel = v.get("channel", "both")
    units   = int(v.get("units_per_pack") or 1)
    approved = "✅" if v.get("label_approved") else "⚠️"
    ch_short = {"both": "↕", "WS": "WS", "GW": "GW"}.get(channel, "?")
    label    = f"{approved} {fmt} {ch_short}"
    if units > 1:
        label += f" ×{units}"
    return label


def _ref_size_desc(recipe: dict) -> str:
    st_type = recipe.get("size_type", "diameter")
    if st_type == "diameter":
        d = recipe.get("ref_diameter_cm", "")
        h = recipe.get("ref_height_cm", "")
        return f"Referencia: {d}cm diámetro" + (f" × {h}cm alto" if h else "")
    elif st_type == "weight":
        return f"Referencia: {recipe.get('ref_weight_kg', '')}kg"
    else:
        return f"Referencia: {recipe.get('ref_portions', '')} porciones"
