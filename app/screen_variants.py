# screen_variants.py
# =============================================================================
# Product variants editor.
#
# Slots are auto-derived from recipe flags (has_individual, has_bocado).
# Widget values come directly from the variant dict via value= parameters —
# no session state pre-loading, which avoids the Streamlit instantiation error.
#
# Sidebar uses a single aggregated DB call to avoid per-recipe queries.
# Allergen declaration is deferred behind a button.
# Ingredient label regeneration is a setup operation — button kept minimal.
# =============================================================================

import streamlit as st
import millington_db as db
from core.constants import FORMAT_DISPLAY

PACKAGING_DEFAULTS = {
    "standard":   "Caja de cartón y base de cartón",
    "individual": "Caja de cartón",
    "bocado":     "Caja de cartón",
}

STORAGE_DEFAULT = "Refrigerada entre 0 - 5°C"
SHELF_DEFAULT   = 24


# =============================================================================
# Main screen
# =============================================================================

def screen_variants():
    st.title("Variantes de producto")
    st.caption(
        "Ficha técnica por receta y formato. "
        "Los formatos disponibles se derivan automáticamente de la receta."
    )

    recipes  = db.get_recipes()
    presets  = db.get_packaging_presets()

    recipe_by_id   = {r["id"]: r for r in recipes}
    recipe_names   = sorted([r["name"] for r in recipes], key=str.lower)
    preset_by_id   = {p["id"]: p for p in presets}

    # Single query — all variants keyed by recipe_id
    all_variants   = db.get_all_variants()
    variants_by_rid = {}
    for v in all_variants:
        variants_by_rid.setdefault(v["recipe_id"], []).append(v)

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
            r       = next(x for x in recipes if x["name"] == name)
            rid     = r["id"]
            slots   = _active_slots(r)
            v_list  = variants_by_rid.get(rid, [])
            v_by_fmt = {v["format"]: v for v in v_list}
            approved = sum(
                1 for fmt in slots
                if v_by_fmt.get(fmt, {}).get("label_approved")
            )
            badge = f" {approved}/{len(slots)}"

            if st.button(
                name + badge,
                key=f"var_rbtn_{rid}",
                use_container_width=True,
                type="primary" if selected_rid == rid else "secondary"
            ):
                st.session_state.var_recipe_id = rid
                st.rerun()

    # ── Detail panel ──────────────────────────────────────────────────────────
    with col_detail:
        rid = st.session_state.get("var_recipe_id")
        if not rid:
            st.info("Selecciona una receta de la lista.")
            return

        recipe   = recipe_by_id.get(rid, {})
        v_list   = variants_by_rid.get(rid, [])
        var_by_fmt = {v["format"]: v for v in v_list}
        slots    = _active_slots(recipe)

        st.markdown(f"### {recipe.get('name', '')}")
        st.caption(_ref_size_desc(recipe))

        if not slots:
            st.warning("Esta receta no tiene formatos activos.")
            return

        tab_labels = [
            f"{FORMAT_DISPLAY[fmt]}" +
            (" ✅" if var_by_fmt.get(fmt, {}).get("label_approved") else "")
            for fmt in slots
        ]
        tabs = st.tabs(tab_labels)

        for tab, fmt in zip(tabs, slots):
            with tab:
                _slot_editor(
                    fmt=fmt,
                    variant=var_by_fmt.get(fmt),
                    rid=rid,
                    recipe=recipe,
                    var_by_fmt=var_by_fmt,
                )


# =============================================================================
# Slot editor — values come directly from variant dict via value= parameters
# =============================================================================

def _slot_editor(
    fmt: str,
    variant: dict | None,
    rid: str,
    recipe: dict,
    var_by_fmt: dict,
):
    vid   = variant["id"] if variant else None
    p     = f"vs_{rid}_{fmt}"
    std   = var_by_fmt.get("standard", {})
    is_new = vid is None

    if is_new:
        st.info(
            f"Sin variante {FORMAT_DISPLAY[fmt]} — "
            "rellena los datos y guarda para crearla."
        )

    # ── SKUs ──────────────────────────────────────────────────────────────────
    st.markdown("#### SKUs")
 
    # Derive cake code from recipe (requires get_recipes to join cake_codes)
    cake_codes_data = recipe.get("cake_codes") or {}
    cake_code       = cake_codes_data.get("code", "")
 
    # Size options per format — controls middle segment of SKU
    SIZE_OPTIONS = {
        "standard":   {"LA": "Large (22cm)", "XL": "XLarge (24-26cm)",
                       "XX": "XXLarge (28-30cm)", "DC": "Desayuno/Caja"},
        "individual": {"TI": "Individual (1 ud)", "IN": "Individual ×4",
                       "BO": "Bocado ×25"},
        "bocado":     {"MI": "Bocado individual", "BO": "Bocado ×25"},
    }
    size_opts  = SIZE_OPTIONS.get(fmt, {"LA": "Large"})
    default_sz = _v(variant, "size_code", list(size_opts.keys())[0])
    if default_sz not in size_opts:
        default_sz = list(size_opts.keys())[0]
 
    size_code = st.selectbox(
        "Tamaño / formato SKU",
        options=list(size_opts.keys()),
        format_func=lambda x: f"{x} — {size_opts[x]}",
        index=list(size_opts.keys()).index(default_sz),
        key=f"{p}_size_code",
        help="Determina el segmento de tamaño en el SKU (p.ej. LA, TI, BO)"
    )
 
    # Auto-generate SKUs — editable, auto-filled if empty
    if cake_code:
        auto_ws = f"{cake_code}-01-{size_code}-WS"
        auto_gw = f"{cake_code}-01-{size_code}-GW"
    else:
        auto_ws = ""
        auto_gw = ""
 
    # Use stored value if it exists, otherwise auto-generate
    default_sku_ws = _v(variant, "sku_ws", auto_ws)
    default_sku_gw = _v(variant, "sku_gw", auto_gw)
 
    # If size_code just changed, regenerate (only if stored value matches
    # the old auto-pattern — don't overwrite manual edits)
    stored_size = _v(variant, "size_code", "")
    if stored_size and stored_size != size_code and cake_code:
        # Size changed — update if current value was auto-generated
        old_ws = f"{cake_code}-01-{stored_size}-WS"
        old_gw = f"{cake_code}-01-{stored_size}-GW"
        if default_sku_ws == old_ws:
            default_sku_ws = auto_ws
        if default_sku_gw == old_gw:
            default_sku_gw = auto_gw
 
    sk1, sk2 = st.columns(2)
    with sk1:
        sku_ws = st.text_input(
            "SKU Mayorista (WS)",
            value=default_sku_ws,
            key=f"{p}_sku_ws",
            placeholder="e.g. LP-01-TI-WS",
            help=f"Auto-generado: {auto_ws}" if auto_ws else "Introduce el código manualmente",
        )
    with sk2:
        sku_gw = st.text_input(
            "SKU Minorista (GW)",
            value=default_sku_gw,
            key=f"{p}_sku_gw",
            placeholder="e.g. LP-01-TI-GW",
            help=f"Auto-generado: {auto_gw}" if auto_gw else "Introduce el código manualmente",
        )
 
    if not cake_code:
        st.caption(
            "⚠️ Esta receta no tiene un código de tarta asignado — "
            "asigna uno en la pantalla de Recetas para auto-generar los SKUs."
        )
 

    # ── Size & weight ──────────────────────────────────────────────────────────
    st.markdown("#### Tamaño y peso")
    sz1, sz2 = st.columns(2)
    with sz1:
        size_description = st.text_input(
            "Descripción de tamaño",
            value=_v(variant, "size_description",
                     _default_size(fmt, recipe)),
            key=f"{p}_size",
        )
    with sz2:
        ref_weight_g = st.number_input(
            "Peso aprox. (g)",
            value=float(_v(variant, "ref_weight_g",
                           _default_weight(fmt, recipe))),
            min_value=0.0,
            key=f"{p}_weight"
        )

    # ── Description ────────────────────────────────────────────────────────────
    st.markdown("#### Descripción (español)")

    # Default description: individual copies standard, bocado blank
    if fmt == "bocado":
        default_desc = ""
        help_text = (
            "Los bocados suelen tener descripción más corta — "
            "empieza con 'Bocado de…' y omite decoración no aplicable."
        )
    elif fmt == "individual":
        default_desc = std.get("description_es") or ""
        help_text = (
            "Normalmente igual a la tarta estándar. "
            "Modifica solo si hay diferencias reales."
        )
    else:
        default_desc = ""
        help_text = ""

    description_es = st.text_area(
        "Descripción",
        value=_v(variant, "description_es", default_desc),
        key=f"{p}_desc",
        height=85,
        label_visibility="collapsed",
        placeholder="Descripción del producto en español…",
        help=help_text if help_text else None
    )

    # ── Packaging ──────────────────────────────────────────────────────────────
    st.markdown("#### Embalaje")

    # Show stored value; if it equals the default, show empty so placeholder works
    stored_pack = _v(variant, "packaging_desc", "")
    show_pack   = "" if stored_pack == PACKAGING_DEFAULTS[fmt] else stored_pack

    st.caption(
        f"Predeterminado: *{PACKAGING_DEFAULTS[fmt]}*  "
        "— deja vacío para usar el predeterminado."
    )
    packaging_input = st.text_input(
        "Embalaje (vacío = predeterminado)",
        value=show_pack,
        key=f"{p}_pack",
        placeholder=PACKAGING_DEFAULTS[fmt]
    )
    effective_packaging = (
        packaging_input.strip()
        if packaging_input.strip()
        else PACKAGING_DEFAULTS[fmt]
    )

    # ── Storage & shelf life ───────────────────────────────────────────────────
    sv1, sv2 = st.columns(2)
    with sv1:
        storage_instructions = st.text_input(
            "Conservación",
            value=_v(variant, "storage_instructions", STORAGE_DEFAULT),
            key=f"{p}_storage"
        )
    with sv2:
        shelf_life_hours = st.number_input(
            "Vida útil (horas)",
            value=int(_v(variant, "shelf_life_hours", SHELF_DEFAULT)),
            min_value=1,
            key=f"{p}_shelf"
        )


    # ── Ingredient label text ──────────────────────────────────────────────────
    st.markdown("#### Lista de ingredientes (etiqueta)")

    # Default label: individual copies standard, bocado blank
    if fmt == "individual":
        default_label = std.get("ingredient_label_es") or ""
    elif fmt == "bocado":
        default_label = ""
    else:
        default_label = ""

    stored_label = _v(variant, "ingredient_label_es", default_label)

    if fmt == "bocado" and not stored_label:
        st.caption(
            "⚠️ Verifica si los ingredientes del bocado difieren del estándar "
            "(p.ej. sin fruta fresca si no lleva decoración). "
            "Usa el botón de abajo para generar un borrador y edita si necesario."
        )

    ingredient_label_es = st.text_area(
        "Lista de ingredientes",
        value=stored_label,
        key=f"{p}_label",
        height=100,
        label_visibility="collapsed",
        help="Ordenado por peso descendente. Alérgenos en negrita en el ficha."
    )

    with st.expander("🔧 Herramientas de configuración"):
        st.caption(
            "Genera un borrador de la lista de ingredientes desde la receta. "
            "Úsalo como punto de partida — revisa y edita antes de aprobar."
        )
        if st.button("Generar borrador lista de ingredientes", key=f"{p}_regen"):
            label_data = db.get_ingredient_label_text(rid)
            new_label  = label_data.get("label_text", "")
            if new_label:
                # We cannot set session state for an already-instantiated widget.
                # Store in a staging key and show it below.
                st.session_state[f"{p}_label_draft"] = new_label
                if label_data.get("warnings"):
                    for w in label_data["warnings"]:
                        st.warning(w)
                st.info(
                    "Borrador generado — copia el texto de abajo al campo "
                    "de lista de ingredientes y guarda."
                )
            else:
                st.warning("No se pudo generar el borrador.")

        draft = st.session_state.get(f"{p}_label_draft")
        if draft:
            st.code(draft, language=None)

    label_approved = st.checkbox(
        "✅ Lista de ingredientes aprobada",
        value=bool(_v(variant, "label_approved", False)),
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
        if decl.get("warnings"):
            for w in decl["warnings"]:
                st.warning(w)
        al1, al2 = st.columns(2)
        with al1:
            st.markdown("**Contiene:**")
            if decl["contiene"]:
                for item in decl["contiene"]:
                    st.markdown(f"- {item.capitalize()}")
            else:
                st.caption("Ninguno")
        with al2:
            st.markdown("**Puede contener:**")
            if decl["puede_contener"]:
                for item in decl["puede_contener"]:
                    st.markdown(f"- {item.capitalize()}")
            else:
                st.caption("Ninguno")
    else:
        st.caption(
            "Pulsa 'Calcular alérgenos' para ver la declaración "
            "generada automáticamente."
        )

    # ── Save / Delete ──────────────────────────────────────────────────────────
    st.divider()
    col_save, col_del = st.columns([2, 1])

    with col_save:
        if st.button(
            "💾 Guardar", type="primary",
            use_container_width=True,
            key=f"{p}_save"
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
                "ingredient_label_es":  ingredient_label_es or None,
                "label_approved":       label_approved,
                "size_code":            size_code or None,
            }
            if vid:
                record["id"] = vid

            db.save_variant(record)
            # Clear draft and declaration so they reload
            st.session_state.pop(f"{p}_label_draft", None)
            st.session_state.pop(f"{p}_declaration", None)
            st.success("Guardado", icon="✅")
            st.rerun()

    with col_del:
        if vid and st.button(
            "🗑 Eliminar", use_container_width=True,
            key=f"{p}_del"
        ):
            db.delete_variant(vid)
            st.session_state.pop(f"{p}_declaration", None)
            st.rerun()


# =============================================================================
# Helpers
# =============================================================================

def _v(variant: dict | None, key: str, default):
    """Safely get a value from a variant dict, returning default if None."""
    if not variant:
        return default
    val = variant.get(key)
    if val is None:
        return default
    return val


def _active_slots(recipe: dict) -> list[str]:
    slots = ["standard"]
    if recipe.get("has_individual"):
        slots.append("individual")
    if recipe.get("has_bocado"):
        slots.append("bocado")
    return slots


def _default_size(fmt: str, recipe: dict) -> str:
    size_type = recipe.get("size_type", "diameter")
    if fmt == "standard":
        if size_type == "diameter":
            d = recipe.get("ref_diameter_cm", "")
            return f"{float(d):.0f} cm diámetro" if d else ""
        elif size_type == "weight":
            return f"{recipe.get('ref_weight_kg', '')} kg"
        else:
            return f"{recipe.get('ref_portions', '')} porciones"
    elif fmt == "individual":
        return "8 cm diámetro"
    elif fmt == "bocado":
        return "3.5 cm diámetro"
    return ""


def _default_weight(fmt: str, recipe: dict) -> float:
    if fmt == "individual":
        return float(recipe.get("individual_weight_g") or 100)
    elif fmt == "bocado":
        return float(recipe.get("bocado_weight_g") or 30)
    return 0.0


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
