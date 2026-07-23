# screen_variants.py
# =============================================================================
# Product variants editor — tier-based layout.
#
# Each recipe has up to three format tiers:
#   standard  → "Tarta" (diameter cakes) or "Caja/Porción" (DC box products)
#   individual → individual-serve formats (TI, IN, CA, LI)
#   bocado     → bite formats (MI, BO)
#
# Within each tier, multiple commercial sizes can exist (one row each),
# keyed by size_code.  Shared fields (label, packaging, storage) are
# edited once per tier and propagated to all sizes in that tier on save.
#
# Size code conventions:
#   Existing cakes:  LA (Large), XL (XLarge), XX (XXLarge)
#   New cakes:       numeric diameter, e.g. "22", "26", "30"
#   Box products:    DC (standard box); DC2, DC3 for additional sizes
#   Individual:      TI, IN, CA, LI
#   Bocado:          MI, BO
# =============================================================================

import streamlit as st
import millington_db as db


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

PACKAGING_DEFAULTS = {
    "standard":   "Caja de cartón y base de cartón",
    "individual": "Caja de cartón",
    "bocado":     "Caja de cartón",
}
STORAGE_DEFAULT = "Refrigerada entre 0 - 5°C"
SHELF_DEFAULT   = 24

TIER_LABELS = {
    "individual": "Individual",
    "bocado":     "Bocado",
}


# -----------------------------------------------------------------------------
# Size code helpers — loaded from DB, cached for the session
# -----------------------------------------------------------------------------

@st.cache_data(ttl=300)
def _load_size_code_defs() -> list[dict]:
    return db.get_size_code_definitions()


def _codes_for_tier(tier: str) -> list[dict]:
    """Return size code defs for a given tier, ordered by sort_order."""
    return [d for d in _load_size_code_defs() if d["tier"] == tier]


def _size_label(code: str) -> str:
    """Human-readable label: DB lookup first, then numeric-diameter fallback."""
    for d in _load_size_code_defs():
        if d["code"] == code:
            return d["label_es"]
    try:
        return f"Tarta {int(float(code))}cm"
    except (ValueError, TypeError):
        return code or "—"


# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------

def _standard_tier_label(recipe: dict) -> str:
    size_type = recipe.get("size_type", "diameter")
    return "Caja / Porción" if size_type in ("weight", "portions") else "Tarta"


def _is_dc_recipe(recipe: dict) -> bool:
    return recipe.get("size_type", "diameter") in ("weight", "portions")


def _ref_size_desc(recipe: dict) -> str:
    size_type = recipe.get("size_type", "diameter")
    if size_type == "diameter":
        d = recipe.get("ref_diameter_cm", "")
        h = recipe.get("ref_height_cm", "")
        return f"Referencia: {d}cm diámetro" + (f" × {h}cm" if h else "")
    elif size_type == "weight":
        return f"Referencia: {recipe.get('ref_weight_kg', '')} kg"
    return f"Referencia: {recipe.get('ref_portions', '')} porciones"


# -----------------------------------------------------------------------------
# Main screen
# -----------------------------------------------------------------------------

def screen_variants():
    st.title("Variantes de producto")
    st.caption(
        "Define los tamaños comerciales por receta y nivel de formato. "
        "Ingredientes escalan por peso; coste de mano de obra fijado en la "
        "receta por nivel."
    )

    with st.expander("🔍 Buscar un código de tamaño (diagnóstico)"):
        st.caption(
            "Si al añadir un tamaño te dice que un código 'ya existe' pero no "
            "lo ves en la lista de la receta, es probable que esté en una "
            "receta antigua o deprecada, invisible en los selectores normales. "
            "Búscalo aquí — mira en todas las recetas, incluidas las deprecadas."
        )
        find_code = st.text_input(
            "Código de tamaño", placeholder="ej. LI", key="var_find_code"
        ).strip().upper()
        if find_code:
            try:
                found = db.find_variants_by_size_code(find_code)
            except Exception as e:
                found = None
                st.error(f"Error al buscar: {e}")
            if found is not None:
                if not found:
                    st.success(f"Ningún variante usa el código `{find_code}` — está libre.")
                else:
                    st.warning(f"{len(found)} variante(s) usan `{find_code}`:")
                    for f in found:
                        r = f.get("recipes") or {}
                        dep = "🚫 deprecada" if r.get("deprecated") else "activa"
                        sub = " · sub-receta" if r.get("is_sub_recipe") else ""
                        code = (r.get("cake_codes") or {}).get("code", "?")
                        st.markdown(
                            f"- **{r.get('name', '?')}** ({code}-{r.get('version','?')}, {dep}{sub}) "
                            f"— formato `{f.get('format')}` — "
                            f"SKU WS `{f.get('sku_ws') or '—'}` · SKU GW `{f.get('sku_gw') or '—'}` "
                            f"· peso {f.get('ref_weight_g') or '—'}g"
                        )

        st.divider()
        st.caption(
            "Busca por texto dentro del SKU — útil para revisar variantes de "
            "cliente específico (ej. 'MD' para Mentidero) y comprobar que el "
            "número de versión en el SKU coincide con la versión real de la "
            "receta (el autocompletado antiguo siempre ponía '-01-', diera "
            "igual la versión real)."
        )
        find_sku_text = st.text_input(
            "Texto en el SKU", placeholder="ej. MD", key="var_find_sku_text"
        ).strip()
        if find_sku_text:
            try:
                sku_found = db.find_variants_by_sku_text(find_sku_text)
            except Exception as e:
                sku_found = None
                st.error(f"Error al buscar: {e}")
            if sku_found is not None:
                if not sku_found:
                    st.info(f"Ningún SKU contiene `{find_sku_text}`.")
                else:
                    st.warning(f"{len(sku_found)} variante(s) encontradas:")
                    for f in sku_found:
                        r = f.get("recipes") or {}
                        dep = "🚫 deprecada" if r.get("deprecated") else "activa"
                        recipe_version = (r.get("version") or "01").strip().zfill(2)
                        mismatches = []
                        for field in ("sku_ws", "sku_gw"):
                            sku_val = f.get(field)
                            if sku_val:
                                parts = sku_val.split("-")
                                if len(parts) >= 2 and parts[1] != recipe_version:
                                    mismatches.append(f"{field}=`{sku_val}` (dice v{parts[1]})")
                        flag = (
                            f"  ⚠️ **versión no coincide con la receta (v{recipe_version})**: "
                            + ", ".join(mismatches)
                            if mismatches else ""
                        )
                        st.markdown(
                            f"- **{r.get('name', '?')}** (v{recipe_version}, {dep}) — "
                            f"formato `{f.get('format')}`, código `{f.get('size_code') or '—'}` — "
                            f"SKU WS `{f.get('sku_ws') or '—'}` · SKU GW `{f.get('sku_gw') or '—'}`"
                            f"{flag}"
                        )

    recipes      = db.get_recipes()
    recipe_by_id = {r["id"]: r for r in recipes}
    recipe_names = sorted([r["name"] for r in recipes], key=str.lower)

    # Lightweight count query for sidebar badges
    all_variants: list[dict] = db.get_all_variants()
    count_by_rid: dict[str, int] = {}
    for v in all_variants:
        count_by_rid[v["recipe_id"]] = count_by_rid.get(v["recipe_id"], 0) + 1

    col_list, col_detail = st.columns([1, 2.5])

    # ── Recipe list ───────────────────────────────────────────────────────────
    with col_list:
        st.markdown("**Receta**")
        search = st.text_input(
            "Buscar", placeholder="Filtrar…",
            label_visibility="collapsed", key="var_search",
        )
        displayed = (
            [n for n in recipe_names if search.lower() in n.lower()]
            if search else recipe_names
        )
        selected_rid = st.session_state.get("var_recipe_id")

        for name in displayed:
            r   = next(x for x in recipes if x["name"] == name)
            rid = r["id"]
            cnt = count_by_rid.get(rid, 0)
            badge = f" {cnt}" if cnt else ""
            if st.button(
                name + badge,
                key=f"var_rbtn_{rid}",
                use_container_width=True,
                type="primary" if selected_rid == rid else "secondary",
            ):
                st.session_state.var_recipe_id = rid
                st.rerun()

    # ── Detail panel ──────────────────────────────────────────────────────────
    with col_detail:
        rid = st.session_state.get("var_recipe_id")
        if not rid:
            st.info("Selecciona una receta de la lista.")
            return

        recipe = recipe_by_id.get(rid, {})

        # Full variant data for selected recipe only (fixes the save/reload bug)
        full_variants = db.get_variants_for_recipe(rid)
        by_fmt: dict[str, list[dict]] = {}
        for v in full_variants:
            by_fmt.setdefault(v["format"], []).append(v)

        st.markdown(f"### {recipe.get('name', '')}")
        st.caption(_ref_size_desc(recipe))

        # ── Migrate variants ───────────────────────────────────────────────────
        n_variants = sum(len(v) for v in by_fmt.values())
        if n_variants > 0:
            with st.expander("🔀 Migrar variantes a otra receta"):
                st.caption(
                    "Reasigna todos los SKUs de esta receta a una nueva receta. "
                    "Se resetea el campo 'aprobado' en todos los variantes migrados."
                )
                # All non-deprecated, non-sub-recipe options excluding current
                all_recipes = db.get_recipes(include_deprecated=False)
                migrate_options = {
                    r["name"]: r["id"]
                    for r in all_recipes
                    if r["id"] != rid
                }
                target_name = st.selectbox(
                    "Receta destino",
                    ["— selecciona —"] + sorted(migrate_options.keys()),
                    key=f"migrate_target_{rid}",
                )
                if target_name != "— selecciona —":
                    st.warning(
                        f"Se moverán **{n_variants}** variantes a '{target_name}'. "
                        "El campo 'lista de ingredientes aprobada' se reseteará en todos. "
                        "Esta acción no se puede deshacer."
                    )
                    if st.button("Confirmar migración", type="primary",
                                 key=f"migrate_confirm_{rid}"):
                        try:
                            moved = db.reassign_variants(rid, migrate_options[target_name])
                            st.success(f"✅ {moved} variante(s) migrada(s) a '{target_name}'.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")

        # Determine tiers
        is_dc = _is_dc_recipe(recipe)
        tiers = ["standard"] if is_dc else ["standard", "individual", "bocado"]

        tab_labels = []
        for fmt in tiers:
            lbl = _standard_tier_label(recipe) if fmt == "standard" else TIER_LABELS[fmt]
            cnt = len(by_fmt.get(fmt, []))
            tab_labels.append(f"{lbl} ({cnt})" if cnt else lbl)

        tabs = st.tabs(tab_labels)
        for tab, fmt in zip(tabs, tiers):
            with tab:
                _tier_panel(fmt, recipe, by_fmt.get(fmt, []), rid)


# -----------------------------------------------------------------------------
# Tier panel
# -----------------------------------------------------------------------------

def _tier_panel(fmt: str, recipe: dict, variants: list[dict], rid: str):
    """One tier tab: size table + shared section."""
    existing_codes = {v.get("size_code", "") for v in variants}

    # ── Size table ─────────────────────────────────────────────────────────────
    hcol, acol = st.columns([3, 1])
    with hcol:
        tier_lbl = _standard_tier_label(recipe) if fmt == "standard" else TIER_LABELS[fmt]
        st.markdown(f"**Tamaños — {tier_lbl}**")
    with acol:
        if st.button("＋ Añadir tamaño", key=f"add_{rid}_{fmt}", use_container_width=True):
            _dialog_add_size(fmt, recipe, rid, existing_codes)

    if variants:
        h0, h1, h2, h3, h4, h5, h6 = st.columns([1, 2, 1.2, 2.2, 2.2, 1.2, 1.2])
        for cell, label in zip(
            [h0, h1, h2, h3, h4, h5, h6],
            ["Código", "Descripción", "Peso", "SKU WS", "SKU GW", "Precio WS", "Precio GW"],
        ):
            cell.caption(label)

        sorted_variants = sorted(variants, key=lambda x: x.get("size_code") or "")
        for v in sorted_variants:
            c0, c1, c2, c3, c4, c5, c6 = st.columns([1, 2, 1.2, 2.2, 2.2, 1.2, 1.2])
            sc = v.get("size_code") or "—"
            c0.code(sc)
            c1.write(_size_label(sc))
            w = v.get("ref_weight_g")
            c2.write(f"{int(w)} g" if w else "—")
            c3.write(v.get("sku_ws") or "—")
            c4.write(v.get("sku_gw") or "—")
            ws = v.get("ws_price_ex_vat")
            rt = v.get("rt_price_inc_vat")
            c5.write(f"€{ws:.2f}" if ws else "—")
            c6.write(f"€{rt:.2f}" if rt else "—")
            # Edit button in last-used column — add an extra action column
            # by re-rendering a small button row

        # Action row per variant (separate pass to keep columns aligned)
        for v in sorted_variants:
            ac0, ac1, ac2, *_ = st.columns([1, 2, 1.2, 2.2, 2.2, 1.2, 1.2])
            sc = v.get("size_code") or "—"
            with ac0:
                if st.button("✏️", key=f"edit_{v['id']}", help="Editar"):
                    _dialog_edit_size(v, recipe)
    else:
        st.caption("Sin tamaños definidos — añade uno con el botón de arriba.")

    st.divider()

    # ── Shared section ─────────────────────────────────────────────────────────
    with st.expander(
        "📋 Información compartida (etiqueta, embalaje, alérgenos)",
        expanded=bool(variants),
    ):
        _shared_section(fmt, recipe, rid, variants)


# -----------------------------------------------------------------------------
# Shared section (label / packaging / storage — per tier, all sizes)
# -----------------------------------------------------------------------------

def _shared_section(fmt: str, recipe: dict, rid: str, variants: list[dict]):
    p       = f"sh_{rid}_{fmt}"
    primary = variants[0] if variants else None
    default_pack = PACKAGING_DEFAULTS.get(fmt, "")

    col1, col2 = st.columns(2)
    with col1:
        storage = st.text_input(
            "Conservación",
            value=(primary or {}).get("storage_instructions") or STORAGE_DEFAULT,
            key=f"{p}_storage",
        )
    with col2:
        shelf = st.number_input(
            "Vida útil (horas)",
            value=int((primary or {}).get("shelf_life_hours") or SHELF_DEFAULT),
            min_value=1,
            key=f"{p}_shelf",
        )

    stored_pack = (primary or {}).get("packaging_desc") or ""
    show_pack   = "" if stored_pack == default_pack else stored_pack
    st.caption(f"Predeterminado: *{default_pack}* — deja vacío para usar el predeterminado.")
    pack_input  = st.text_input(
        "Embalaje (vacío = predeterminado)",
        value=show_pack, key=f"{p}_pack",
        placeholder=default_pack,
    )
    packaging = pack_input.strip() or default_pack

    st.markdown("**Lista de ingredientes (etiqueta)**")

    stored_label = (primary or {}).get("ingredient_label_es") or ""
    if stored_label and (primary or {}).get("label_approved"):
        try:
            live = db.get_ingredient_label_text(rid)
            live_text = (live.get("label_text") or "").strip()
            if live_text and live_text != stored_label.strip():
                st.warning(
                    "⚠️ La receta ha cambiado desde la última aprobación. "
                    "Regenera el borrador y vuelve a aprobar."
                )
                st.session_state[f"{p}_live_label"] = live_text
        except Exception:
            pass

    label_text = st.text_area(
        "Lista de ingredientes",
        value=stored_label,
        key=f"{p}_label",
        height=100,
        label_visibility="collapsed",
        placeholder="Ordenado por peso descendente. Alérgenos en negrita.",
    )

    with st.expander("🔧 Generar borrador"):
        if st.button("Generar desde receta", key=f"{p}_regen"):
            new_label = st.session_state.pop(f"{p}_live_label", None)
            if not new_label:
                try:
                    data = db.get_ingredient_label_text(rid)
                    new_label = data.get("label_text", "")
                    for w in (data.get("warnings") or []):
                        st.warning(w)
                except Exception as e:
                    st.error(str(e))
            if new_label:
                st.session_state[f"{p}_draft"] = new_label
            else:
                st.warning("No se pudo generar el borrador.")
        draft = st.session_state.get(f"{p}_draft")
        if draft:
            st.code(draft, language=None)

    label_approved = st.checkbox(
        "✅ Lista de ingredientes aprobada",
        value=bool((primary or {}).get("label_approved", False)),
        key=f"{p}_approved",
    )

    st.markdown("**Alérgenos**")
    if st.button("Calcular alérgenos", key=f"{p}_allergens"):
        with st.spinner("Calculando…"):
            try:
                decl = db.get_allergen_declaration(rid)
                st.session_state[f"{p}_decl"] = decl
            except Exception as e:
                st.error(str(e))

    decl = st.session_state.get(f"{p}_decl")
    if decl:
        if decl.get("warnings"):
            for w in decl["warnings"]:
                st.warning(w)
        al1, al2 = st.columns(2)
        with al1:
            st.markdown("**Contiene:**")
            for item in (decl.get("contiene") or []):
                st.markdown(f"- {item.capitalize()}")
        with al2:
            st.markdown("**Puede contener:**")
            for item in (decl.get("puede_contener") or []):
                st.markdown(f"- {item.capitalize()}")

    st.divider()

    n = len(variants)
    save_label = (
        f"💾 Guardar información compartida (propagará a {n} tamaños)"
        if n > 1 else "💾 Guardar información compartida"
    )

    if variants:
        if st.button(save_label, type="primary", key=f"{p}_save_shared"):
            shared = {
                "storage_instructions": storage or None,
                "shelf_life_hours":     shelf,
                "packaging_desc":       packaging,
                "ingredient_label_es":  label_text or None,
                "label_approved":       label_approved,
            }
            errors = []
            for v in variants:
                try:
                    db.save_variant({"id": v["id"], **shared})
                except Exception as e:
                    errors.append(str(e))
            if errors:
                st.error(f"Error al guardar: {errors[0]}")
            else:
                st.session_state.pop(f"{p}_draft", None)
                st.session_state.pop(f"{p}_decl", None)
                st.success(f"Guardado en {n} variante(s)", icon="✅")
                st.rerun()
    else:
        st.caption("Añade al menos un tamaño para guardar la información compartida.")


# -----------------------------------------------------------------------------
# Dialogs
# -----------------------------------------------------------------------------

@st.dialog("Añadir tamaño")
def _dialog_add_size(fmt: str, recipe: dict, rid: str, existing_codes: set):
    cake_code = (recipe.get("cake_codes") or {}).get("code", "")
    is_dc     = _is_dc_recipe(recipe)

    # ── Size code input ────────────────────────────────────────────────────────
    if fmt == "standard":
        # Standard tier: predefined codes from DB + free-text for new diameters
        tier_defs   = _codes_for_tier("standard")
        known_codes = [d["code"] for d in tier_defs if d["code"] not in existing_codes]
        options     = known_codes + ["Otro (diámetro)"]
        choice = st.selectbox(
            "Código de tamaño",
            options=options,
            format_func=lambda x: f"{x} — {_size_label(x)}" if x != "Otro (diámetro)" else x,
        )
        if choice == "Otro (diámetro)":
            size_code = st.text_input(
                "Diámetro en cm",
                placeholder="ej. 22, 26, 30",
                help="Se usará como código de tamaño en el SKU (ej. LP-01-22-WS).",
            ).strip()
        else:
            size_code = choice
    else:
        tier_defs = _codes_for_tier(fmt)
        available = [d for d in tier_defs if d["code"] not in existing_codes]
        if not available:
            st.info("Ya están definidos todos los tamaños disponibles para este nivel.")
            st.caption("Puedes añadir nuevos códigos en Ajustes → Códigos de tamaño.")
            return
        size_code = st.selectbox(
            "Código de tamaño",
            options=[d["code"] for d in available],
            format_func=lambda x: f"{x} — {_size_label(x)}",
        )

    # ── Weight ─────────────────────────────────────────────────────────────────
    if fmt == "individual":
        default_w = float(recipe.get("individual_weight_g") or 100)
    elif fmt == "bocado":
        default_w = float(recipe.get("bocado_weight_g") or 30)
    else:
        kg = recipe.get("ref_weight_kg") or 0
        default_w = float(kg) * 1000 if kg else 0.0

    weight = st.number_input("Peso aprox. (g)", value=default_w, min_value=0.0)

    # ── SKUs ──────────────────────────────────────────────────────────────────
    # Version comes from the recipe itself (segment 2 identifies the actual
    # formulation — LP-02 is a genuinely different recipe from LP-01, not
    # just "a newer Lemon Pie"), so it must never be hardcoded here.
    recipe_version = (recipe.get("version") or "01").strip().zfill(2)
    auto_ws = f"{cake_code}-{recipe_version}-{size_code}-WS" if cake_code and size_code else ""
    auto_gw = f"{cake_code}-{recipe_version}-{size_code}-GW" if cake_code and size_code else ""

    sk1, sk2 = st.columns(2)
    with sk1:
        sku_ws = st.text_input("SKU Mayorista", value=auto_ws, placeholder="CC-01-XX-WS")
    with sk2:
        sku_gw = st.text_input("SKU Minorista", value=auto_gw, placeholder="CC-01-XX-GW")

    size_desc = st.text_input(
        "Descripción del tamaño",
        value=_size_label(size_code),
        placeholder="Ej. Tarta 22cm",
    )

    st.divider()
    if st.button("Añadir", type="primary", use_container_width=True):
        if not size_code:
            st.error("El código de tamaño es obligatorio.")
            return
        try:
            db.save_variant({
                "recipe_id":            rid,
                "format":               fmt,
                "channel":              "both",
                "units_per_pack":       25 if size_code == "BO" else 1,
                "size_code":            size_code,
                "ref_weight_g":         weight or None,
                "sku_ws":               sku_ws or None,
                "sku_gw":               sku_gw or None,
                "size_description":     size_desc or None,
                "packaging_desc":       PACKAGING_DEFAULTS.get(fmt, ""),
                "storage_instructions": STORAGE_DEFAULT,
                "shelf_life_hours":     SHELF_DEFAULT,
                "label_approved":       False,
            })
            st.success(f"Tamaño {size_code} añadido", icon="✅")
            st.rerun()
        except Exception as e:
            err = str(e)
            if "unique" in err.lower() or "duplicate" in err.lower():
                st.error(
                    f"El código `{size_code}` ya existe para esta receta y nivel. "
                    "Usa un código diferente."
                )
                # Show the raw DB error too — the generic message above is a
                # guess at *which* uniqueness rule fired. If it isn't really
                # about size_code (e.g. the real constraint is on something
                # else, like the channel field, which every variant here sets
                # to the same "both" value), the raw text below will name the
                # actual constraint.
                with st.expander("Detalle técnico del error"):
                    st.code(err)
            else:
                st.error(f"Error al añadir: {err}")


@st.dialog("Editar variante")
def _dialog_edit_size(variant: dict, recipe: dict):
    cake_code    = (recipe.get("cake_codes") or {}).get("code", "")
    fmt          = variant.get("format", "standard")
    current_code = variant.get("size_code") or ""

    # ── Size code ──────────────────────────────────────────────────────────────
    if fmt == "standard":
        tier_defs   = _codes_for_tier("standard")
        known_codes = [d["code"] for d in tier_defs]
        # Include current code even if not in DB (e.g. a legacy diameter)
        if current_code and current_code not in known_codes:
            known_codes = [current_code] + known_codes
        options = known_codes + ["Otro (diámetro)"]
        current_choice = current_code if current_code in options else "Otro (diámetro)"
        choice = st.selectbox(
            "Código de tamaño",
            options=options,
            index=options.index(current_choice),
            format_func=lambda x: f"{x} — {_size_label(x)}" if x != "Otro (diámetro)" else x,
        )
        if choice == "Otro (diámetro)":
            size_code = st.text_input(
                "Diámetro en cm",
                value=current_code if current_code not in known_codes else "",
                placeholder="ej. 22, 26, 30",
            ).strip()
        else:
            size_code = choice
    else:
        tier_defs = _codes_for_tier(fmt)
        all_codes = [d["code"] for d in tier_defs]
        if current_code and current_code not in all_codes:
            all_codes = [current_code] + all_codes
        size_code = st.selectbox(
            "Código de tamaño",
            options=all_codes,
            index=all_codes.index(current_code) if current_code in all_codes else 0,
            format_func=lambda x: f"{x} — {_size_label(x)}",
        )

    weight = st.number_input(
        "Peso aprox. (g)",
        value=float(variant.get("ref_weight_g") or 0),
        min_value=0.0,
    )

    # Auto-generate SKUs from (possibly updated) size_code.
    # Version comes from the recipe itself (LP-02 is a different recipe from
    # LP-01, not just "a newer Lemon Pie") — never hardcode it here.
    recipe_version = (recipe.get("version") or "01").strip().zfill(2)
    auto_ws = f"{cake_code}-{recipe_version}-{size_code}-WS" if cake_code and size_code else ""
    auto_gw = f"{cake_code}-{recipe_version}-{size_code}-GW" if cake_code and size_code else ""

    sk1, sk2 = st.columns(2)
    with sk1:
        sku_ws = st.text_input(
            "SKU Mayorista",
            value=variant.get("sku_ws") or auto_ws,
        )
    with sk2:
        sku_gw = st.text_input(
            "SKU Minorista",
            value=variant.get("sku_gw") or auto_gw,
        )

    size_desc = st.text_input(
        "Descripción del tamaño",
        value=variant.get("size_description") or _size_label(size_code),
    )

    description_es = st.text_area(
        "Descripción del producto (ficha técnica)",
        value=variant.get("description_es") or "",
        height=80,
        placeholder="Descripción en español — aparece en la ficha técnica de este tamaño.",
    )

    pr1, pr2 = st.columns(2)
    with pr1:
        ws_price = st.number_input(
            "Precio mayorista (€ s/IVA)",
            value=float(variant.get("ws_price_ex_vat") or 0),
            min_value=0.0, format="%.2f",
        )
    with pr2:
        rt_price = st.number_input(
            "Precio minorista (€ c/IVA)",
            value=float(variant.get("rt_price_inc_vat") or 0),
            min_value=0.0, format="%.2f",
        )

    st.divider()
    col_save, col_del = st.columns([2, 1])
    with col_save:
        if st.button("💾 Guardar", type="primary", use_container_width=True):
            try:
                db.save_variant({
                    "id":               variant["id"],
                    "size_code":        size_code or None,
                    "ref_weight_g":     weight or None,
                    "sku_ws":           sku_ws or None,
                    "sku_gw":           sku_gw or None,
                    "size_description": size_desc or None,
                    "description_es":   description_es or None,
                    "ws_price_ex_vat":  ws_price or None,
                    "rt_price_inc_vat": rt_price or None,
                })
                st.success("Guardado", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
    with col_del:
        if st.button("🗑 Eliminar", use_container_width=True, key=f"del_{variant['id']}"):
            try:
                db.delete_variant(variant["id"])
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
