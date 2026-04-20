# screen_variants.py
# =============================================================================
# Product variants editor — simplified.
#
# Variant slots are auto-derived from recipe flags:
#   Standard  — always shown
#   Individual — shown if recipe.has_individual = TRUE
#   Bocado     — shown if recipe.has_bocado = TRUE
#
# Each slot pre-fills sensible defaults:
#   - Description: standard text for individual, blank prompt for bocado
#   - Packaging: format default unless variant has a packaging_desc override
#   - Ingredient label: copied from standard for individual,
#                       flagged for review for bocado
#
# Allergen declaration is calculated on demand (button) to avoid slow renders.
# =============================================================================

import streamlit as st
import millington_db as db


# ── Packaging defaults per format ─────────────────────────────────────────────
PACKAGING_DEFAULTS = {
    "standard":   "Caja de cartón y base de cartón",
    "individual": "Caja de cartón",
    "bocado":     "Caja de cartón",
}

FORMAT_DISPLAY = {
    "standard":   "Tarta estándar",
    "individual": "Individual",
    "bocado":     "Bocado",
}

STORAGE_DEFAULT  = "Refrigerada entre 0 - 5°C"
SHELF_DEFAULT    = 24


# =============================================================================
# Main screen
# =============================================================================

def screen_variants():
    st.title("Variantes de producto")
    st.caption(
        "Los slots de variante se generan automáticamente según los formatos "
        "activos en cada receta. Rellena los datos del ficha y aprueba la "
        "lista de ingredientes antes de generar el PDF."
    )

    recipes      = db.get_recipes()
    presets      = db.get_packaging_presets()
    preset_by_id = {p["id"]: p for p in presets}

    recipe_names = sorted([r["name"] for r in recipes], key=str.lower)
    recipe_by_id = {r["id"]: r for r in recipes}

    col_list, col_detail = st.columns([1, 2.5])

    # ── Recipe list ───────────────────────────────────────────────────────────
    with col_list:
        st.markdown("**Receta**")

        search = st.text_input(
            "Buscar", placeholder="Filtrar…",
            label_visibility="collapsed", key="var_search"
        )

        displayed = [
            n for n in recipe_names
            if search.lower() in n.lower()
        ] if search else recipe_names

        selected_rid = st.session_state.get("var_recipe_id")

        for name in displayed:
            r        = next(x for x in recipes if x["name"] == name)
            variants = db.get_variants_for_recipe(r["id"])
            n_slots  = _count_slots(r)
            approved = sum(1 for v in variants if v.get("label_approved"))
            badge    = f" ✅{approved}/{n_slots}"

            if st.button(
                name + badge,
                key=f"var_rbtn_{r['id']}",
                use_container_width=True,
                type="primary" if selected_rid == r["id"] else "secondary"
            ):
                st.session_state.var_recipe_id = r["id"]
                # Clear all loaded flags for this recipe's variants
                _clear_variant_state(r["id"])
                st.rerun()

    # ── Detail panel ──────────────────────────────────────────────────────────
    with col_detail:
        rid = st.session_state.get("var_recipe_id")
        if not rid:
            st.info("Selecciona una receta de la lista.")
            return

        recipe   = recipe_by_id.get(rid, {})
        variants = db.get_variants_for_recipe(rid)
        var_by_fmt = {v["format"]: v for v in variants}

        st.markdown(f"### {recipe.get('name', '')}")
        st.caption(_ref_size_desc(recipe))

        # Determine active slots from recipe flags
        slots = _active_slots(recipe)

        if not slots:
            st.warning("Esta receta no tiene formatos activos.")
            return

        # ── Tabs — one per slot ───────────────────────────────────────────────
        tab_labels = [_slot_tab_label(fmt, var_by_fmt) for fmt in slots]
        tabs = st.tabs(tab_labels)

        for tab, fmt in zip(tabs, slots):
            with tab:
                variant = var_by_fmt.get(fmt)
                _slot_editor(
                    fmt, variant, rid, recipe,
                    var_by_fmt, presets, preset_by_id
                )


# =============================================================================
# Slot editor
# =============================================================================

def _slot_editor(
    fmt: str,
    variant: dict | None,
    rid: str,
    recipe: dict,
    var_by_fmt: dict,
    presets: list,
    preset_by_id: dict,
):
    """
    Editor for one format slot.
    If no variant row exists yet, show a create form with pre-filled defaults.
    If variant exists, show editable form pre-populated from the DB row.
    """
    vid  = variant["id"] if variant else None
    p    = f"vs_{rid}_{fmt}"

    # Load into session state once
    if f"{p}_loaded" not in st.session_state:
        _load_slot(p, fmt, variant, var_by_fmt, recipe)

    is_new = vid is None

    if is_new:
        st.info(
            f"No hay variante {FORMAT_DISPLAY[fmt]} aún — "
            "rellena los datos y guarda para crearla."
        )

    # ── SKUs ──────────────────────────────────────────────────────────────────
    st.markdown("#### SKUs")
    sk1, sk2 = st.columns(2)
    with sk1:
        sku_ws = st.text_input(
            "SKU Mayorista",
            key=f"{p}_sku_ws",
            placeholder="e.g. LP-01-TI-WS"
        )
    with sk2:
        sku_gw = st.text_input(
            "SKU Minorista",
            key=f"{p}_sku_gw",
            placeholder="e.g. LP-01-TI-GW"
        )

    # ── Size & weight ──────────────────────────────────────────────────────────
    st.markdown("#### Tamaño y peso")
    sz1, sz2 = st.columns(2)
    with sz1:
        size_description = st.text_input(
            "Descripción de tamaño",
            key=f"{p}_size",
            placeholder="e.g. 8 cm diámetro"
        )
    with sz2:
        ref_weight_g = st.number_input(
            "Peso aprox. (g)",
            min_value=0.0,
            key=f"{p}_weight"
        )

    # ── Description ────────────────────────────────────────────────────────────
    st.markdown("#### Descripción (español)")

    if fmt == "bocado" and not st.session_state.get(f"{p}_desc"):
        st.caption(
            "Los bocados suelen tener una descripción más corta — "
            "comienza con 'Bocado de...' y omite detalles de decoración "
            "que no apliquen."
        )
    elif fmt == "individual":
        st.caption(
            "La descripción del individual suele ser idéntica a la estándar. "
            "Modifica solo si hay diferencias reales."
        )

    description_es = st.text_area(
        "Descripción",
        key=f"{p}_desc",
        height=85,
        label_visibility="collapsed",
        placeholder="Descripción del producto en español…"
    )

    # ── Packaging ──────────────────────────────────────────────────────────────
    st.markdown("#### Embalaje")
    st.caption(
        f"Por defecto: *{PACKAGING_DEFAULTS[fmt]}* — "
        "modifica solo si este producto usa embalaje diferente."
    )
    packaging_desc = st.text_input(
        "Embalaje (dejar vacío para usar el predeterminado)",
        key=f"{p}_packdesc",
        placeholder=PACKAGING_DEFAULTS[fmt]
    )
    # Effective packaging — override if filled, otherwise default
    effective_packaging = packaging_desc.strip() if packaging_desc.strip() \
        else PACKAGING_DEFAULTS[fmt]

    # ── Storage & shelf life ───────────────────────────────────────────────────
    st.markdown("#### Conservación")
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

    # ── Prices ─────────────────────────────────────────────────────────────────
    st.markdown("#### Precios")
    pr1, pr2 = st.columns(2)
    with pr1:
        ws_price = st.number_input(
            "Mayorista ex-IVA (€)",
            min_value=0.0, format="%.4f",
            key=f"{p}_ws"
        )
    with pr2:
        rt_price = st.number_input(
            "Minorista inc-IVA (€)",
            min_value=0.0, format="%.4f",
            key=f"{p}_rt"
        )

    # ── Ingredient label text ──────────────────────────────────────────────────
    st.markdown("#### Lista de ingredientes (etiqueta)")

    if fmt == "bocado":
        st.caption(
            "⚠️ Verifica si los ingredientes del bocado difieren del estándar "
            "(p.ej. sin fruta fresca si no lleva decoración). "
            "Pulsa Regenerar como punto de partida y edita si es necesario."
        )
    elif fmt == "individual":
        st.caption(
            "Normalmente idéntica al estándar. "
            "Pulsa Regenerar si está vacía."
        )

    ingredient_label_es = st.text_area(
        "Lista de ingredientes",
        key=f"{p}_label",
        height=100,
        label_visibility="collapsed",
        help="Ordenado por peso descendente. "
             "Alérgenos en negrita en el ficha generado."
    )

    col_regen, _ = st.columns([1, 3])
    with col_regen:
        if st.button("↺ Regenerar", key=f"{p}_regen",
                     help="Genera borrador desde la receta"):
            label_data = db.get_ingredient_label_text(rid)
            st.session_state[f"{p}_label"] = \
                label_data.get("label_text", "")
            if label_data.get("warnings"):
                for w in label_data["warnings"]:
                    st.warning(w)
            st.rerun()

    label_approved = st.checkbox(
        "✅ Lista de ingredientes aprobada",
        key=f"{p}_approved",
        help="Solo las variantes aprobadas pueden generar fichas."
    )

    # ── Allergen declaration — on demand ──────────────────────────────────────
    st.markdown("#### Declaración de alérgenos")

    if st.button("Calcular alérgenos", key=f"{p}_calc"):
        with st.spinner("Calculando…"):
            declaration = db.get_allergen_declaration(rid)
        st.session_state[f"{p}_declaration"] = declaration

    decl = st.session_state.get(f"{p}_declaration")
    if decl:
        if decl["warnings"]:
            for w in decl["warnings"]:
                st.warning(w)
        al1, al2 = st.columns(2)
        with al1:
            st.markdown("**Contiene:**")
            for item in decl["contiene"]:
                st.markdown(f"- {item.capitalize()}")
            if not decl["contiene"]:
                st.caption("Ninguno")
        with al2:
            st.markdown("**Puede contener:**")
            for item in decl["puede_contener"]:
                st.markdown(f"- {item.capitalize()}")
            if not decl["puede_contener"]:
                st.caption("Ninguno")
    else:
        st.caption(
            "Pulsa 'Calcular alérgenos' para ver la declaración "
            "generada automáticamente."
        )

    # ── Save ──────────────────────────────────────────────────────────────────
    st.divider()
    col_save, col_del = st.columns([2, 1])

    with col_save:
        if st.button(
            "💾 Guardar", type="primary",
            use_container_width=True, key=f"{p}_save"
        ):
            record = {
                "recipe_id":            rid,
                "format":               fmt,
                "channel":              "both",
                "units_per_pack":       1,
                "sku_ws":               sku_ws or None,
                "sku_gw":               sku_gw or None,
                "size_description":     size_description or None,
                "ref_weight_g":         ref_weight_g or None,
                "description_es":       description_es or None,
                "packaging_desc":       effective_packaging,
                "storage_instructions": storage_instructions or None,
                "shelf_life_hours":     shelf_life_hours,
                "ws_price_ex_vat":      ws_price or None,
                "rt_price_inc_vat":     rt_price or None,
                "ingredient_label_es":  ingredient_label_es or None,
                "label_approved":       label_approved,
            }
            if vid:
                record["id"] = vid
            db.save_variant(record)

            # Clear loaded flag so form reloads fresh values
            st.session_state.pop(f"{p}_loaded", None)
            st.session_state.pop(f"{p}_declaration", None)
            st.success("Guardado", icon="✅")
            st.rerun()

    with col_del:
        if vid and st.button(
            "🗑 Eliminar", use_container_width=True,
            key=f"{p}_del"
        ):
            db.delete_variant(vid)
            st.session_state.pop(f"{p}_loaded", None)
            st.session_state.pop(f"{p}_declaration", None)
            st.rerun()


# =============================================================================
# Session state helpers
# =============================================================================

def _load_slot(
    p: str, fmt: str,
    variant: dict | None,
    var_by_fmt: dict,
    recipe: dict,
):
    """
    Write slot values into session state before first render.
    Pre-fills defaults intelligently:
      - Individual inherits description from standard if blank
      - All formats use packaging default if variant has no override
    """
    std = var_by_fmt.get("standard", {})

    if variant:
        # Existing variant — load from DB
        st.session_state[f"{p}_sku_ws"]   = variant.get("sku_ws") or ""
        st.session_state[f"{p}_sku_gw"]   = variant.get("sku_gw") or ""
        st.session_state[f"{p}_size"]     = variant.get("size_description") or ""
        st.session_state[f"{p}_weight"]   = float(variant.get("ref_weight_g") or 0)
        st.session_state[f"{p}_desc"]     = variant.get("description_es") or ""
        # packaging_desc: show the override (may be empty, default shown as placeholder)
        stored_pack = variant.get("packaging_desc") or ""
        is_default  = stored_pack == PACKAGING_DEFAULTS.get(fmt, "")
        st.session_state[f"{p}_packdesc"] = "" if is_default else stored_pack
        st.session_state[f"{p}_storage"]  = (
            variant.get("storage_instructions") or STORAGE_DEFAULT
        )
        st.session_state[f"{p}_shelf"]    = int(
            variant.get("shelf_life_hours") or SHELF_DEFAULT
        )
        st.session_state[f"{p}_ws"]       = float(variant.get("ws_price_ex_vat") or 0)
        st.session_state[f"{p}_rt"]       = float(variant.get("rt_price_inc_vat") or 0)
        st.session_state[f"{p}_label"]    = variant.get("ingredient_label_es") or ""
        st.session_state[f"{p}_approved"] = bool(variant.get("label_approved"))

    else:
        # New variant — pre-fill defaults
        st.session_state[f"{p}_sku_ws"]   = ""
        st.session_state[f"{p}_sku_gw"]   = ""
        st.session_state[f"{p}_size"]     = _default_size(fmt, recipe)
        st.session_state[f"{p}_weight"]   = _default_weight(fmt, recipe)
        st.session_state[f"{p}_packdesc"] = ""  # empty = use format default

        # Description: individual copies standard, bocado starts blank
        if fmt == "individual":
            st.session_state[f"{p}_desc"] = std.get("description_es") or ""
        else:
            st.session_state[f"{p}_desc"] = ""

        st.session_state[f"{p}_storage"]  = STORAGE_DEFAULT
        st.session_state[f"{p}_shelf"]    = SHELF_DEFAULT
        st.session_state[f"{p}_ws"]       = 0.0
        st.session_state[f"{p}_rt"]       = 0.0

        # Label: individual copies standard, bocado starts blank
        if fmt == "individual":
            st.session_state[f"{p}_label"] = std.get("ingredient_label_es") or ""
        else:
            st.session_state[f"{p}_label"] = ""

        st.session_state[f"{p}_approved"] = False

    st.session_state[f"{p}_loaded"] = True


def _clear_variant_state(rid: str):
    """Clear all session state keys for a recipe's variant slots."""
    keys_to_clear = [
        k for k in st.session_state
        if k.startswith(f"vs_{rid}_")
    ]
    for k in keys_to_clear:
        del st.session_state[k]


# =============================================================================
# Default value helpers
# =============================================================================

def _default_size(fmt: str, recipe: dict) -> str:
    """Suggest a size description for a new variant slot."""
    size_type = recipe.get("size_type", "diameter")
    if fmt == "standard":
        if size_type == "diameter":
            d = recipe.get("ref_diameter_cm", "")
            return f"{d:.0f} cm diámetro" if d else ""
        elif size_type == "weight":
            return f"{recipe.get('ref_weight_kg', '')} kg"
        else:
            return f"{recipe.get('ref_portions', '')} porciones"
    elif fmt == "individual":
        return "8 cm diámetro"   # most common — user adjusts
    elif fmt == "bocado":
        return "3.5 cm diámetro"
    return ""


def _default_weight(fmt: str, recipe: dict) -> float:
    """Suggest a weight in grams for a new variant slot."""
    if fmt == "individual":
        return float(recipe.get("individual_weight_g") or 100)
    elif fmt == "bocado":
        return float(recipe.get("bocado_weight_g") or 30)
    return 0.0


# =============================================================================
# Display helpers
# =============================================================================

def _active_slots(recipe: dict) -> list[str]:
    """Return list of active format slots for this recipe."""
    slots = ["standard"]
    if recipe.get("has_individual"):
        slots.append("individual")
    if recipe.get("has_bocado"):
        slots.append("bocado")
    return slots


def _count_slots(recipe: dict) -> int:
    return len(_active_slots(recipe))


def _slot_tab_label(fmt: str, var_by_fmt: dict) -> str:
    v        = var_by_fmt.get(fmt)
    approved = v and v.get("label_approved")
    icon     = "✅" if approved else "⚠️"
    return f"{icon} {FORMAT_DISPLAY[fmt]}"


def _ref_size_desc(recipe: dict) -> str:
    size_type = recipe.get("size_type", "diameter")
    if size_type == "diameter":
        d = recipe.get("ref_diameter_cm", "")
        h = recipe.get("ref_height_cm", "")
        return f"Referencia: {d}cm diámetro" + (f" × {h}cm" if h else "")
    elif size_type == "weight":
        return f"Referencia: {recipe.get('ref_weight_kg', '')} kg"
    else:
        return f"Referencia: {recipe.get('ref_portions', '')} porciones"
