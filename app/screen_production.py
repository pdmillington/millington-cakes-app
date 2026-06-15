# screen_production.py
# =============================================================================
# Production Log & Label Printer — Daily use screen.
#
# Tab 1 — Registro de producción
#   Records each production run: recipe, quantity, oven temp (PCC), ingredient
#   references and any incidents. Auto-generates a lote number MC-YYYYMMDD-XXX.
#   Saves to production_runs + production_ingredient_refs tables.
#   Exports a PDF record of the run (for inspector inspection).
#
# Tab 2 — Imprimir etiquetas
#   Selects a saved production run (or allows manual entry) and generates a
#   label PDF meeting EU Reg 1169/2011 B2B requirements:
#   denomination · ingredients · allergens · net weight · best-before ·
#   storage · operator name/address · lot number.
#   Labels are A6 (105×148 mm), printed 2-up on A4.
#
# PDF uses EB Garamond (data/EBGaramond-Regular.ttf + Bold) and the
# Millington logo (data/Logo.png) — same assets as screen_catalogue.py.
# =============================================================================

import io
import os
from datetime import date, timedelta, datetime

import streamlit as st
import millington_db as db

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

FORMAT_DISPLAY = {
    "standard":   "Tarta estándar",
    "individual": "Individual",
    "bocado":     "Bocado",
}

COMPANY_NAME    = "Millington Cakes, S.L."
COMPANY_CIF     = "B13998596"
COMPANY_ADDRESS = "Calle de la Granja 100, Nave 5-6, 28108 Alcobendas, Madrid"


# =============================================================================
# Main screen
# =============================================================================

def screen_production():
    st.title("Producción y etiquetas")
    st.caption(
        "Registra cada producción, obtén tu número de lote y genera las etiquetas "
        "para etiquetar cada producto antes de la entrega."
    )

    tab1, tab2, tab3 = st.tabs([
        "📦 Recepción de materias primas",
        "📋 Registro de producción",
        "🏷️ Imprimir etiquetas",
    ])

    with tab1:
        _tab_reception()

    with tab2:
        _tab_log()

    with tab3:
        _tab_labels()


# =============================================================================
# Tab 1 — Production log
# =============================================================================

def _tab_log():
    st.markdown("### Nueva producción")
    st.caption(
        "Rellena los datos de la hornada. El número de lote se genera automáticamente "
        "al guardar — cópialo en el albarán de Holded."
    )

    # ── Recipe selector ───────────────────────────────────────────────────────
    recipes = db.get_recipes()
    non_sub = [r for r in recipes if not r.get("is_sub_recipe")]
    recipe_names = sorted(r["name"] for r in non_sub)
    recipe_by_name = {r["name"]: r for r in non_sub}

    col_r, col_f = st.columns([3, 2])
    with col_r:
        recipe_name = st.selectbox(
            "Receta", ["— selecciona —"] + recipe_names, key="prod_recipe"
        )
    with col_f:
        fmt_options = ["standard", "individual", "bocado"]
        fmt_labels  = [FORMAT_DISPLAY[f] for f in fmt_options]
        fmt_idx     = st.selectbox(
            "Formato", fmt_labels, key="prod_fmt"
        )
        selected_fmt = fmt_options[fmt_labels.index(fmt_idx)]

    if recipe_name == "— selecciona —":
        st.info("Selecciona una receta para continuar.")
        st.divider()
        _show_recent_runs()
        return

    recipe = recipe_by_name[recipe_name]

    # ── Fetch recipe ingredients to pre-populate the reference table ───────────
    recipe_ings = []
    try:
        recipe_ings = db.get_key_ingredients_for_recipe(recipe["id"])
    except Exception:
        pass

    # Number of rows = max(3, number of key ingredients in recipe)
    n_rows_default = max(3, len(recipe_ings))
    # Reset row count when recipe changes
    last_recipe = st.session_state.get("prod_last_recipe")
    if last_recipe != recipe["id"]:
        st.session_state["prod_n_refs"]    = n_rows_default
        st.session_state["prod_last_recipe"] = recipe["id"]
        # clear any previous ingredient inputs
        for i in range(10):
            st.session_state.pop(f"prod_ing_{i}", None)
            st.session_state.pop(f"prod_alb_{i}", None)

    # ── Basic details ─────────────────────────────────────────────────────────
    col_d, col_q = st.columns(2)
    with col_d:
        prod_date = st.date_input(
            "Fecha de producción", value=date.today(), key="prod_date"
        )
    with col_q:
        quantity = st.number_input(
            "Unidades producidas", min_value=1, value=1, step=1, key="prod_qty"
        )

    # ── PCC steps — pre-populated from recipe ────────────────────────────────
    st.markdown("#### Control de Puntos Críticos (PCC) — registro APPCC")
    st.caption(
        "Confirma la temperatura y tiempo alcanzados en cada paso crítico. "
        "Pasos cargados desde la definición de la receta."
    )

    # Load PCC steps when recipe changes
    pcc_steps_key = f"prod_pcc_{recipe['id']}"
    if pcc_steps_key not in st.session_state:
        try:
            template_steps = db.get_pcc_steps(recipe["id"])
        except Exception:
            template_steps = []
        # Initialise with template values as defaults
        st.session_state[pcc_steps_key] = [
            {
                "step_name":             s["step_name"],
                "target_temp_c":         s.get("target_temp_c") or 0,
                "target_time_min":       s.get("target_time_min") or 0,
                "critical_limit_temp_c": s.get("critical_limit_temp_c") or 70.0,
                "temp_achieved_c":       s.get("target_temp_c") or 0,
                "time_achieved_min":     s.get("target_time_min") or 0,
            }
            for s in template_steps
        ]

    pcc_steps = st.session_state[pcc_steps_key]

    if not pcc_steps:
        st.info(
            "ℹ️ Esta receta no tiene pasos PCC definidos. "
            "Defínelos en la pantalla de Recetas para que aparezcan aquí automáticamente."
        )
        # Fallback: single manual entry
        col_t, col_m = st.columns(2)
        with col_t:
            oven_temp = st.number_input(
                "Temperatura alcanzada (°C)",
                min_value=0, max_value=300, value=180, step=5, key="prod_temp"
            )
        with col_m:
            bake_time = st.number_input(
                "Tiempo (minutos)",
                min_value=0, max_value=300, value=45, step=5, key="prod_time"
            )
        pcc_steps = [{
            "step_name":             "Horneado",
            "temp_achieved_c":       oven_temp,
            "time_achieved_min":     bake_time,
            "critical_limit_temp_c": 70.0,
        }]
        st.session_state[pcc_steps_key] = pcc_steps
    else:
        # Header
        ph1, ph2, ph3, ph4, ph5 = st.columns([2.5, 1.2, 1, 1, 1.2])
        ph1.markdown("**Elaboración**")
        ph2.markdown("**Temp. alcanzada (°C)**")
        ph3.markdown("**Tiempo (min)**")
        ph4.markdown("**Límite crítico**")
        ph5.markdown("**¿OK?**")

        for idx, step in enumerate(pcc_steps):
            pc1, pc2, pc3, pc4, pc5 = st.columns([2.5, 1.2, 1, 1, 1.2])
            pc1.markdown(f"**{step['step_name']}**")
            with pc2:
                temp_achieved = st.number_input(
                    "temp", key=f"prod_pcc_temp_{recipe['id']}_{idx}",
                    value=float(step.get("temp_achieved_c") or step.get("target_temp_c") or 0),
                    min_value=0.0, max_value=300.0, step=1.0,
                    label_visibility="collapsed"
                )
            with pc3:
                time_achieved = st.number_input(
                    "time", key=f"prod_pcc_time_{recipe['id']}_{idx}",
                    value=int(step.get("time_achieved_min") or step.get("target_time_min") or 0),
                    min_value=0, max_value=300, step=1,
                    label_visibility="collapsed"
                )
            limit = step.get("critical_limit_temp_c") or 70.0
            pc4.markdown(f"≥ {limit:.0f} °C")
            ok = temp_achieved >= limit
            pc5.markdown("✅" if ok else "❌ **¡Revisar!**")

            st.session_state[pcc_steps_key][idx] = {
                **step,
                "temp_achieved_c":   temp_achieved,
                "time_achieved_min": time_achieved,
                "critical_limit_met": ok,
            }

    # Build pcc_log for saving
    pcc_log = [
        {
            "step_name":          s["step_name"],
            "temp_achieved_c":    s.get("temp_achieved_c"),
            "time_min":           s.get("time_achieved_min"),
            "critical_limit_met": s.get("critical_limit_met", True),
        }
        for s in st.session_state.get(pcc_steps_key, [])
    ]

    # ── Ingredient references ─────────────────────────────────────────────────
    st.markdown("#### Referencias de ingredientes principales")
    if recipe_ings:
        allergen_names = [i["name"] for i in recipe_ings if i.get("is_allergen_bearing")]
        criteria_parts = ["≥5% del peso total"]
        if allergen_names:
            criteria_parts.append(f"alérgenos ({', '.join(allergen_names)})")
        st.caption(
            f"Ingredientes clave según criterio APPCC: {' + '.join(criteria_parts)}. "
            f"Indica el número de albarán del proveedor para cada uno."
        )
    else:
        st.caption(
            "Indica el número de albarán o lote del proveedor para los ingredientes "
            "clave (≥5% del peso o alérgenos)."
        )

    n_refs = st.session_state.get("prod_n_refs", n_rows_default)

    # Last-used albarán refs per ingredient name (from most recent run with same recipe)
    last_alb_by_ing: dict[str, str] = {}
    try:
        prev_runs = db.get_production_runs_for_recipe(recipe["id"], limit=1)
        if prev_runs:
            for ref in prev_runs[0].get("ingredient_refs", []):
                if ref.get("albaran_ref"):
                    last_alb_by_ing[ref["ingredient_name"].strip().lower()] = ref["albaran_ref"]
    except Exception:
        pass

    # Pre-populate ingredient names from recipe (if not already set by user)
    for i, ing in enumerate(recipe_ings[:n_refs]):
        key = f"prod_ing_{i}"
        if key not in st.session_state:
            pct_tag = f" ({ing['pct']}%)" if ing.get("pct") else ""
            allergen_tag = " ⚠️" if ing.get("is_allergen_bearing") else ""
            st.session_state[key] = ing["name"] + pct_tag + allergen_tag

    # Pre-populate last-used albarán refs
    for i in range(n_refs):
        ing_key = f"prod_ing_{i}"
        alb_key = f"prod_alb_{i}"
        if alb_key not in st.session_state:
            ing_name_val = st.session_state.get(ing_key, "")
            if ing_name_val:
                last_ref = last_alb_by_ing.get(ing_name_val.strip().lower(), "")
                if last_ref:
                    st.session_state[alb_key] = last_ref

    ing_refs = []
    for i in range(n_refs):
        c1, c2, c3 = st.columns([2, 2, 0.5])
        with c1:
            ing_name = st.text_input(
                "Ingrediente", key=f"prod_ing_{i}",
                placeholder="e.g. Harina de trigo",
                label_visibility="visible" if i == 0 else "collapsed"
            )
        with c2:
            alb_ref = st.text_input(
                "Ref. albarán proveedor", key=f"prod_alb_{i}",
                placeholder="e.g. ALB-2025-0451",
                label_visibility="visible" if i == 0 else "collapsed"
            )
        with c3:
            if i == n_refs - 1:
                st.write("")
                if i == 0:
                    st.write("")  # align with label
                if st.button("＋", key=f"prod_add_{i}", help="Añadir fila"):
                    st.session_state["prod_n_refs"] = n_refs + 1
                    st.rerun()

        if ing_name.strip():
            ing_refs.append({
                "ingredient_name": ing_name.strip(),
                "albaran_ref":     alb_ref.strip() or None,
            })

    # ── Notes ─────────────────────────────────────────────────────────────────
    st.markdown("#### Incidencias / notas")
    notes = st.text_area(
        "Notas",
        placeholder="Ninguna incidencia — o describe cualquier desviación del proceso normal.",
        key="prod_notes",
        height=80,
        label_visibility="collapsed"
    )

    st.divider()

    # ── Save ──────────────────────────────────────────────────────────────────
    if st.button("💾 Guardar registro y obtener número de lote", type="primary"):
        try:
            run = db.save_production_run(
                recipe_id    = recipe["id"],
                recipe_name  = recipe["name"],
                fmt          = selected_fmt,
                prod_date    = prod_date,
                quantity     = int(quantity),
                oven_temp_c  = None,
                bake_time_min= None,
                notes        = notes.strip() or None,
                ing_refs     = ing_refs,
                pcc_log      = pcc_log,
            )
            st.session_state["last_saved_run"] = run
            st.session_state["prod_n_refs"]    = 3   # reset rows
            st.rerun()
        except Exception as e:
            st.error(f"Error al guardar: {e}")

    # ── Show last saved result ─────────────────────────────────────────────────
    last = st.session_state.get("last_saved_run")
    if last:
        lote = last["lote_number"]
        st.success(f"✅ Registro guardado")
        st.markdown(
            f"<div style='background:#F2EEE8;border-radius:8px;padding:16px 20px;"
            f"margin:8px 0;'>"
            f"<span style='font-size:13px;color:#6b7280;'>Número de lote</span><br>"
            f"<span style='font-size:26px;font-weight:700;letter-spacing:2px;"
            f"color:#1a1a1a;'>{lote}</span><br>"
            f"<span style='font-size:12px;color:#9ca3af;'>Copia este número en el "
            f"albarán de Holded</span></div>",
            unsafe_allow_html=True
        )

        col_pdf, col_clear = st.columns([2, 1])
        with col_pdf:
            try:
                pdf_bytes = _generate_log_pdf(last)
                st.download_button(
                    "📄 Descargar registro PDF",
                    data=pdf_bytes,
                    file_name=f"registro_{lote}.pdf",
                    mime="application/pdf",
                )
            except Exception as e:
                st.warning(f"No se pudo generar el PDF: {e}")
        with col_clear:
            if st.button("Nuevo registro", key="prod_clear"):
                st.session_state.pop("last_saved_run", None)
                st.rerun()

    st.divider()
    _show_recent_runs()


@st.dialog("✏️ Editar registro de producción")
def _dialog_edit_run(run: dict):
    fmt_opts   = ["standard", "individual", "bocado"]
    fmt_labels = [FORMAT_DISPLAY[f] for f in fmt_opts]
    cur_fmt    = run.get("format", "standard")
    cur_idx    = fmt_opts.index(cur_fmt) if cur_fmt in fmt_opts else 0

    st.markdown(f"Lote: `{run['lote_number']}`")
    ec1, ec2, ec3 = st.columns(3)
    with ec1:
        raw      = str(run.get("production_date", ""))[:10]
        new_date = st.date_input("Fecha", value=date.fromisoformat(raw) if raw else date.today())
    with ec2:
        new_qty  = st.number_input("Unidades", min_value=1, value=int(run.get("quantity") or 1), step=1)
    with ec3:
        new_fmt  = fmt_opts[fmt_labels.index(
            st.selectbox("Formato", fmt_labels, index=cur_idx)
        )]

    new_notes = st.text_area("Notas / incidencias", value=run.get("notes") or "", height=70)

    st.markdown("**Referencias de ingredientes**")
    existing = run.get("ingredient_refs", [])
    n_rows   = max(3, len(existing))
    new_refs = []
    for i in range(n_rows):
        r = existing[i] if i < len(existing) else {}
        rc1, rc2 = st.columns(2)
        with rc1:
            ing = st.text_input(
                "Ingrediente" if i == 0 else "​",
                value=r.get("ingredient_name", ""),
                key=f"dedit_ing_{run['id']}_{i}",
                label_visibility="visible" if i == 0 else "collapsed",
            )
        with rc2:
            alb = st.text_input(
                "Ref. albarán" if i == 0 else "​",
                value=r.get("albaran_ref", "") or "",
                key=f"dedit_alb_{run['id']}_{i}",
                label_visibility="visible" if i == 0 else "collapsed",
            )
        if ing.strip():
            new_refs.append({"ingredient_name": ing.strip(), "albaran_ref": alb.strip() or None})

    st.divider()
    sb1, sb2 = st.columns(2)
    with sb1:
        if st.button("💾 Guardar cambios", type="primary", use_container_width=True):
            db.update_production_run(run["id"], {
                "production_date": new_date.isoformat(),
                "quantity":        new_qty,
                "format":          new_fmt,
                "notes":           new_notes.strip() or None,
            })
            db.replace_production_ingredient_refs(run["id"], new_refs)
            st.rerun()
    with sb2:
        if st.button("Cancelar", use_container_width=True):
            st.rerun()


@st.dialog("🗑️ Confirmar eliminación")
def _dialog_delete_run(run: dict):
    st.write(f"¿Eliminar el registro **{run['lote_number']}** ({run['recipe_name']})?")
    st.caption("Esta acción no se puede deshacer.")
    d1, d2 = st.columns(2)
    with d1:
        if st.button("✅ Eliminar", type="primary", use_container_width=True):
            db.delete_production_run(run["id"])
            st.rerun()
    with d2:
        if st.button("Cancelar", use_container_width=True):
            st.rerun()


def _show_recent_runs():
    st.markdown("### Registros recientes")

    show_all = st.checkbox("Mostrar todos los registros", value=False, key="prod_show_all")
    try:
        runs = db.get_production_runs(limit=500 if show_all else 30)
    except Exception:
        st.caption("Sin registros todavía.")
        return

    if not runs:
        st.caption("Sin registros todavía.")
        return

    h1, h2, h3, h4, h5, h6, h7, h8 = st.columns([2, 1.5, 1, 0.7, 1, 1, 0.5, 0.5])
    h1.markdown("**Lote**"); h2.markdown("**Producto**"); h3.markdown("**Formato**")
    h4.markdown("**Uds**");  h5.markdown("**Fecha**");   h6.markdown("**PDF**")

    for run in runs:
        c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([2, 1.5, 1, 0.7, 1, 1, 0.5, 0.5])
        c1.code(run["lote_number"], language=None)
        c2.write(run["recipe_name"])
        c3.write(FORMAT_DISPLAY.get(run.get("format", ""), "—"))
        c4.write(str(run["quantity"]))
        c5.write(str(run["production_date"])[:10])
        with c6:
            try:
                pdf_bytes = _generate_log_pdf(run)
                st.download_button(
                    "📄", data=pdf_bytes,
                    file_name=f"registro_{run['lote_number']}.pdf",
                    mime="application/pdf",
                    key=f"dl_{run['id']}",
                )
            except Exception:
                st.write("—")
        with c7:
            if st.button("✏️", key=f"edit_{run['id']}", help="Editar"):
                _dialog_edit_run(run)
        with c8:
            if st.button("🗑️", key=f"del_{run['id']}", help="Eliminar"):
                _dialog_delete_run(run)



# =============================================================================
# Tab 2 — Label printer
# =============================================================================

def _tab_labels():
    st.markdown("### Generar etiquetas")
    st.caption(
        "Selecciona una producción guardada para imprimir sus etiquetas. "
        "Las etiquetas cumplen el Reglamento (UE) 1169/2011 para venta B2B."
    )

    source = st.radio(
        "Fuente de datos",
        ["Desde registro de producción", "Entrada manual"],
        horizontal=True,
        key="label_source",
    )

    st.divider()

    if source == "Desde registro de producción":
        _label_from_run()
    else:
        _label_manual()


def _label_from_run():
    try:
        runs = db.get_production_runs(limit=30)
    except Exception:
        st.warning("No se pudieron cargar los registros.")
        return

    if not runs:
        st.info("No hay registros de producción guardados todavía.")
        return

    run_options = {
        f"{r['lote_number']}  —  {r['recipe_name']} ({str(r['production_date'])[:10]})": r
        for r in runs
    }
    selected_label = st.selectbox(
        "Selecciona producción", list(run_options.keys()), key="label_run_sel"
    )
    run = run_options[selected_label]

    # Pull variant data for the ingredient list
    variant = None
    try:
        variants = db.get_variants_for_recipe(run["recipe_id"])
        fmt = run.get("format", "standard")
        variant = next((v for v in variants if v.get("format") == fmt), None)
    except Exception:
        pass

    shelf_hours = int((variant or {}).get("shelf_life_hours") or 48)
    fresh_storage = (variant or {}).get("storage_instructions") or "Refrigerada entre 0 y 5°C"
    raw_date = run["production_date"]
    if isinstance(raw_date, str):
        raw_date = date.fromisoformat(raw_date[:10])

    # ── Variant approval + staleness warning ─────────────────────────────────
    if variant is None:
        st.warning(
            "⚠️ No se encontró una variante aprobada para este producto y formato. "
            "Los ingredientes y alérgenos no aparecerán en la etiqueta. "
            "Aprueba la variante en la pantalla de Variantes antes de imprimir."
        )
    elif not variant.get("label_approved"):
        st.warning(
            "⚠️ La variante de este producto no está aprobada todavía. "
            "La etiqueta se generará sin lista de ingredientes ni declaración de alérgenos. "
            "Ve a Variantes → aprueba la ficha antes de usar esta etiqueta para entregas."
        )
    elif variant.get("ingredient_label_es") and run.get("recipe_id"):
        # Staleness check — compare stored approved label against live generated text
        try:
            _live = db.get_ingredient_label_text(run["recipe_id"]).get("label_text", "").strip()
            _stored = variant["ingredient_label_es"].strip()
            if _live and _live != _stored:
                st.warning(
                    "⚠️ **La receta ha cambiado desde la última aprobación de la etiqueta.** "
                    "La lista de ingredientes puede estar desactualizada. "
                    "Ve a Variantes, regenera el borrador y vuelve a aprobar antes de imprimir."
                )
        except Exception:
            pass

    # ── Delivery mode ─────────────────────────────────────────────────────────
    delivery_mode = st.radio(
        "Modo de entrega", ["🌿 Fresco", "❄️ Congelado"],
        index=1, horizontal=True, key="label_run_mode"
    )
    frozen = delivery_mode.startswith("❄️")

    col_nlab, col_upb, col_fdays = st.columns(3)
    with col_upb:
        units_per_box = st.number_input(
            "Unidades por caja", min_value=1, value=1, step=1,
            key="label_run_upb",
            help="Número de piezas individuales que contiene cada caja"
        )
    import math as _math
    _default_n = min(_math.ceil(run["quantity"] / max(1, int(units_per_box))), 9)
    with col_nlab:
        n_labels = st.number_input(
            "Nº etiquetas", min_value=1,
            value=_default_n, step=1, key="label_run_qty",
            help="Por defecto: unidades ÷ uds por caja (máx. 9 = una página completa)"
        )
    with col_fdays:
        frozen_days = st.number_input(
            "Vida útil congelado (días)", min_value=1, value=90, step=1,
            key="label_run_fdays",
            disabled=not frozen,
            help="Días desde elaboración hasta fecha de consumo preferente"
        )

    if frozen:
        best_before  = raw_date + timedelta(days=int(frozen_days))
        storage_text = (
            "Conservar congelado a -18°C o inferior. "
            "Descongelar en frigorífico entre 0 y 4°C, "
            "y consumir en un plazo de 24 horas. No volver a congelar."
        )
    else:
        best_before  = raw_date + timedelta(hours=shelf_hours)
        storage_text = fresh_storage

    best_before_d = best_before if isinstance(best_before, date) and not isinstance(best_before, datetime) else best_before.date()

    with st.expander("Vista previa", expanded=True):
        c1, c2 = st.columns(2)
        c1.markdown(f"**Producto:** {run['recipe_name']}")
        c1.markdown(f"**Nº Lote:** `{run['lote_number']}`")
        c2.markdown(f"**Elaborado:** {raw_date.strftime('%d/%m/%Y')}")
        c2.markdown(f"**Consumir antes de:** {best_before_d.strftime('%d/%m/%Y')}")
        c2.markdown(f"**Conservación:** {storage_text[:60]}{'…' if len(storage_text)>60 else ''}")

    if st.button("🏷️ Generar etiquetas PDF", type="primary", key="label_run_gen"):
        try:
            pdf_bytes = _generate_labels_pdf(
                recipe_name   = run["recipe_name"],
                fmt           = run.get("format", "standard"),
                lote          = run["lote_number"],
                prod_date     = raw_date,
                best_before   = best_before_d,
                storage_text  = storage_text,
                variant       = variant,
                n_labels         = int(n_labels),
                units_per_box    = int(units_per_box),
                last_label_units = int(run["quantity"]) % int(units_per_box),
            )
            st.download_button(
                "⬇️ Descargar etiquetas",
                data=pdf_bytes,
                file_name=f"etiquetas_{run['lote_number']}.pdf",
                mime="application/pdf",
            )
        except Exception as e:
            st.error(f"Error al generar etiquetas: {e}")


def _label_manual():
    recipes      = db.get_recipes()
    non_sub      = [r for r in recipes if not r.get("is_sub_recipe")]
    recipe_names = sorted(r["name"] for r in non_sub)
    recipe_by_name = {r["name"]: r for r in non_sub}

    col_r, col_f = st.columns([3, 2])
    with col_r:
        recipe_name = st.selectbox(
            "Receta", ["— selecciona —"] + recipe_names, key="lm_recipe"
        )
    with col_f:
        fmt_options = ["standard", "individual", "bocado"]
        fmt_labels  = [FORMAT_DISPLAY[f] for f in fmt_options]
        fmt_label   = st.selectbox("Formato", fmt_labels, key="lm_fmt")
        selected_fmt = fmt_options[fmt_labels.index(fmt_label)]

    if recipe_name == "— selecciona —":
        return

    recipe  = recipe_by_name[recipe_name]
    variant = None
    try:
        variants = db.get_variants_for_recipe(recipe["id"])
        variant  = next((v for v in variants if v.get("format") == selected_fmt), None)
    except Exception:
        pass

    if variant is None:
        st.warning(
            "⚠️ No se encontró una variante para este producto y formato. "
            "La etiqueta se generará sin ingredientes ni alérgenos."
        )
    elif not variant.get("label_approved"):
        st.warning(
            "⚠️ La variante no está aprobada. La etiqueta se generará sin lista "
            "de ingredientes ni alérgenos. Aprueba la ficha en la pantalla de Variantes."
        )

    shelf_hours   = int((variant or {}).get("shelf_life_hours") or 48)
    fresh_storage = (variant or {}).get("storage_instructions") or "Refrigerada entre 0 y 5°C"

    delivery_mode = st.radio(
        "Modo de entrega", ["🌿 Fresco", "❄️ Congelado"],
        index=1, horizontal=True, key="lm_mode"
    )
    frozen = delivery_mode.startswith("❄️")

    col_l, col_d, col_q, col_upb, col_fdays = st.columns(5)
    with col_l:
        lote = st.text_input(
            "Nº de lote", placeholder="MC-20250522-001", key="lm_lote"
        )
    with col_d:
        prod_date = st.date_input(
            "Fecha elaboración", value=date.today(), key="lm_date"
        )
    with col_q:
        n_labels = st.number_input(
            "Nº etiquetas", min_value=1, value=9, step=1, key="lm_qty",
            help="9 = una página completa"
        )
    with col_upb:
        units_per_box = st.number_input(
            "Uds por caja", min_value=1, value=1, step=1, key="lm_upb",
            help="Número de piezas individuales que contiene cada caja"
        )
    with col_fdays:
        frozen_days = st.number_input(
            "Vida útil congelado (días)", min_value=1, value=90, step=1,
            key="lm_fdays", disabled=not frozen
        )

    if frozen:
        best_before  = prod_date + timedelta(days=int(frozen_days))
        storage_text = (
            "Conservar congelado a -18°C o inferior. "
            "Descongelar en frigorífico entre 0 y 4°C, "
            "y consumir en un plazo de 24 horas. No volver a congelar."
        )
    else:
        best_before  = prod_date + timedelta(hours=shelf_hours)
        storage_text = fresh_storage

    st.caption(
        f"Consumir antes de: **{best_before.strftime('%d/%m/%Y')}**  ·  "
        f"Conservación: {storage_text[:55]}{'…' if len(storage_text)>55 else ''}"
    )

    if st.button("🏷️ Generar etiquetas PDF", type="primary", key="lm_gen"):
        if not lote.strip():
            st.error("Introduce el número de lote.")
            return
        try:
            pdf_bytes = _generate_labels_pdf(
                recipe_name   = recipe_name,
                fmt           = selected_fmt,
                lote          = lote.strip(),
                prod_date     = prod_date,
                best_before   = best_before if isinstance(best_before, date) else best_before.date(),
                storage_text  = storage_text,
                variant       = variant,
                n_labels         = int(n_labels),
                units_per_box    = int(units_per_box),
                last_label_units = 0,  # no quantity known in manual mode
            )
            st.download_button(
                "⬇️ Descargar etiquetas",
                data=pdf_bytes,
                file_name=f"etiquetas_{lote.strip()}.pdf",
                mime="application/pdf",
            )
        except Exception as e:
            st.error(f"Error al generar etiquetas: {e}")


# =============================================================================
# PDF — Production log record (A4)
# =============================================================================

def _generate_log_pdf(run: dict) -> bytes:
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    body_font, bold_font = _load_fonts()
    dark  = colors.HexColor("#1a1a1a")
    mid   = colors.HexColor("#6b7280")
    bg    = colors.HexColor("#F2EEE8")
    light = colors.HexColor("#ebe6de")

    def ps(name, font=None, size=10, leading=14, align=0, color=None, sb=0, sa=4):
        return ParagraphStyle(
            name, fontName=font or body_font,
            fontSize=size, leading=leading, alignment=align,
            textColor=color or dark, spaceBefore=sb, spaceAfter=sa,
        )

    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2.5*cm, bottomMargin=2.5*cm,
    )
    story = []

    # Header
    story.append(Paragraph("REGISTRO DE PRODUCCIÓN", ps("H", font=bold_font, size=16, sa=2)))
    story.append(Paragraph("Sistema APPCC — Control de Puntos Críticos", ps("Hs", size=10, color=mid, sa=6)))
    story.append(Paragraph(f"Millington Cakes, S.L. · CIF {COMPANY_CIF}", ps("Co", size=9, color=mid, sa=2)))
    story.append(Paragraph(COMPANY_ADDRESS, ps("Ca", size=9, color=mid, sa=0)))
    story.append(HRFlowable(width="100%", thickness=1, color=light, spaceAfter=12))

    # Lote callout
    story.append(Paragraph("Número de lote", ps("Lh", size=9, color=mid, sa=0)))
    story.append(Paragraph(run["lote_number"], ps("Ln", font=bold_font, size=20, sa=8)))

    # Main details table
    prod_date = str(run.get("production_date", ""))[:10]
    details = [
        ["Receta",              run.get("recipe_name", "—")],
        ["Formato",             FORMAT_DISPLAY.get(run.get("format", ""), "—")],
        ["Fecha elaboración",   prod_date],
        ["Unidades producidas", str(run.get("quantity", "—"))],
    ]
    tbl = Table(details, colWidths=[5.5*cm, 11*cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (0, -1), light),
        ("FONTNAME",    (0, 0), (0, -1), bold_font),
        ("FONTNAME",    (1, 0), (1, -1), body_font),
        ("FONTSIZE",    (0, 0), (-1, -1), 10),
        ("LEADING",     (0, 0), (-1, -1), 14),
        ("TOPPADDING",  (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0,0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, colors.HexColor("#faf9f7")]),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#d1cdc7")),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 0.5*cm))

    # PCC log
    import json as _json
    pcc_log = run.get("pcc_log")
    if isinstance(pcc_log, str):
        try:
            pcc_log = _json.loads(pcc_log)
        except Exception:
            pcc_log = []
    if pcc_log:
        story.append(Paragraph("Registro de Puntos de Control Crítico (PCC)",
                               ps("PH", font=bold_font, size=11, sa=4)))
        pcc_data = [["Elaboración", "Temp. alcanzada", "Tiempo", "Límite crítico", "Resultado"]]
        for s in pcc_log:
            ok    = s.get("critical_limit_met", True)
            pcc_data.append([
                s.get("step_name", "—"),
                f"{s.get('temp_achieved_c', '—')} °C",
                f"{s.get('time_min', '—')} min",
                f"≥ {s.get('critical_limit_temp_c', 70):.0f} °C"
                    if s.get('critical_limit_temp_c') else "≥ 70 °C",
                "✓ Correcto" if ok else "✗ REVISAR",
            ])
        pcc_tbl = Table(pcc_data, colWidths=[5.5*cm, 2.5*cm, 2*cm, 2.5*cm, 2.5*cm])
        pcc_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#9ca3af")),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), bold_font),
            ("FONTNAME",      (0, 1), (-1, -1), body_font),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("LEADING",       (0, 0), (-1, -1), 13),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.white, colors.HexColor("#faf9f7")]),
            ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#d1cdc7")),
            ("TEXTCOLOR",     (4, 1), (4, -1), colors.HexColor("#16a34a")),
        ]))
        story.append(pcc_tbl)
        story.append(Spacer(1, 0.5*cm))

    # Ingredient references
    ing_refs = run.get("ingredient_refs", [])
    if ing_refs:
        story.append(Paragraph("Referencias de ingredientes", ps("IH", font=bold_font, size=11, sa=4)))
        ing_data = [["Ingrediente", "Referencia albarán proveedor"]] + [
            [r.get("ingredient_name", ""), r.get("albaran_ref", "—")]
            for r in ing_refs
        ]
        ing_tbl = Table(ing_data, colWidths=[8*cm, 8.5*cm])
        ing_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#9ca3af")),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), bold_font),
            ("FONTNAME",      (0, 1), (-1, -1), body_font),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("LEADING",       (0, 0), (-1, -1), 13),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.white, colors.HexColor("#faf9f7")]),
            ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#d1cdc7")),
        ]))
        story.append(ing_tbl)
        story.append(Spacer(1, 0.4*cm))

    # Notes
    notes = run.get("notes")
    story.append(Paragraph("Incidencias / notas", ps("NH", font=bold_font, size=11, sa=4)))
    story.append(Paragraph(
        notes if notes else "Sin incidencias registradas.",
        ps("NB", size=10, color=dark if notes else mid)
    ))

    story.append(Spacer(1, 1*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=light))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        f"Generado el {date.today().strftime('%d/%m/%Y')} · "
        f"Conservar este registro al menos 5 años (Reg. CE 178/2002)",
        ps("Ft", size=8, color=mid, align=1)
    ))

    doc.build(story)
    return buffer.getvalue()


# =============================================================================
# PDF — Product labels  (9-up, 3×3 grid on A4)
# =============================================================================

def _generate_labels_pdf(
    recipe_name: str,
    fmt: str,
    lote: str,
    prod_date,
    best_before,
    variant: dict | None,
    n_labels: int,
    units_per_box: int = 1,
    last_label_units: int = 0,
    storage_text: str | None = None,
) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas as pdfcanvas
    from reportlab.lib.utils import ImageReader
    import io as _io

    body_font, bold_font = _load_fonts()

    v           = variant or {}
    storage     = storage_text or v.get("storage_instructions") or "Refrigerada entre 0 y 5°C"
    fmt_display = FORMAT_DISPLAY.get(fmt, fmt)

    # Ingredient label text
    label_text      = v.get("ingredient_label_es") or ""
    allergen_labels = {}
    if not label_text and variant and variant.get("recipe_id"):
        try:
            label_data      = db.get_ingredient_label_text(variant["recipe_id"])
            label_text      = label_data.get("label_text") or ""
            allergen_labels = label_data.get("allergen_fields") or {}
        except Exception:
            pass
    elif variant and variant.get("recipe_id"):
        try:
            label_data      = db.get_ingredient_label_text(variant["recipe_id"])
            allergen_labels = label_data.get("allergen_fields") or {}
        except Exception:
            pass

    if label_text and allergen_labels:
        label_text = db.apply_allergen_bold(label_text, allergen_labels)

    # Weight
    weight_g = v.get("ref_weight_g")

    # Allergens
    allergen_contiene, allergen_puede = [], []
    if variant and variant.get("recipe_id"):
        try:
            decl = db.get_allergen_declaration(variant["recipe_id"])
            allergen_contiene = decl.get("contiene", [])
            allergen_puede    = decl.get("puede_contener", [])
        except Exception:
            pass

    def _allergen_text(items):
        if not items:
            return ""
        t = ", ".join(a.lower() for a in items)
        return t[0].upper() + t[1:] if t else t

    allergen_contiene_str = _allergen_text(allergen_contiene)
    allergen_puede_str    = _allergen_text(allergen_puede)
    ing_display = _bold_allergens(label_text, allergen_contiene)

    prod_str = prod_date.strftime("%d/%m/%Y") if hasattr(prod_date, "strftime") else str(prod_date)
    bb_str   = best_before.strftime("%d/%m/%Y") if hasattr(best_before, "strftime") else str(best_before)

    # ── Grid geometry ─────────────────────────────────────────────────────────
    PAGE_W, PAGE_H = A4          # 595.28 × 841.89 pts
    MARGIN  = 8  * mm
    GAP     = 3  * mm
    COLS    = 3
    ROWS    = 3

    LABEL_W = (PAGE_W - 2 * MARGIN - (COLS - 1) * GAP) / COLS   # ≈ 177 pts / 62.5 mm
    LABEL_H = (PAGE_H - 2 * MARGIN - (ROWS - 1) * GAP) / ROWS   # ≈ 259 pts / 91.5 mm
    PAD     = 2.5 * mm
    CONTENT_W = LABEL_W - 2 * PAD

    # ── Colours ───────────────────────────────────────────────────────────────
    dark      = colors.HexColor("#1a1a1a")
    mid       = colors.HexColor("#4b5563")
    light     = colors.HexColor("#9ca3af")
    bg        = colors.HexColor("#F2EEE8")
    border    = colors.HexColor("#c4bdb4")
    header_bg = colors.HexColor("#ebe6de")

    logo_path = os.path.join(DATA_DIR, "Logo.png")
    has_logo  = os.path.exists(logo_path)

    buffer = _io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=A4)

    # ── Font sizes for small label ────────────────────────────────────────────
    FS_NAME    = 7.0    # product name
    FS_SUB     = 5.0    # format / subtitle
    FS_SECTION = 5.5    # section headers (INGREDIENTES, CONSERVACIÓN…)
    FS_BODY    = 5.0    # body text
    FS_FOOTER  = 4.0    # company footer

    # ── Line heights (pts) ────────────────────────────────────────────────────
    LH_BODY    = 1.9 * mm
    LH_SECTION = 2.0 * mm

    def draw_label(c, x0: float, y0: float, label_upb: int = units_per_box):
        """Draw one small label with bottom-left at (x0, y0)."""

        # Background + border
        c.setFillColor(bg)
        c.roundRect(x0, y0, LABEL_W, LABEL_H, 1.5 * mm, fill=1, stroke=0)
        c.setStrokeColor(border)
        c.setLineWidth(0.4)
        c.roundRect(x0, y0, LABEL_W, LABEL_H, 1.5 * mm, fill=0, stroke=1)

        # Header band
        header_h = 11 * mm
        c.setFillColor(header_bg)
        c.roundRect(x0, y0 + LABEL_H - header_h, LABEL_W, header_h,
                    1.5 * mm, fill=1, stroke=0)
        c.rect(x0, y0 + LABEL_H - header_h, LABEL_W, header_h / 2,
               fill=1, stroke=0)
        c.setStrokeColor(border)
        c.setLineWidth(0.5)
        c.line(x0, y0 + LABEL_H - header_h,
               x0 + LABEL_W, y0 + LABEL_H - header_h)

        # Logo or text in header
        if has_logo:
            try:
                reader = ImageReader(logo_path)
                iw, ih = reader.getSize()
                aspect = iw / ih if ih else 2
                logo_h = 7 * mm
                logo_w = min(logo_h * aspect, LABEL_W - 2 * PAD)
                logo_x = x0 + (LABEL_W - logo_w) / 2
                logo_y = y0 + LABEL_H - header_h + (header_h - logo_h) / 2
                c.drawImage(logo_path, logo_x, logo_y, width=logo_w,
                            height=logo_h, preserveAspectRatio=True, mask="auto")
            except Exception:
                _draw_centred(c, "Millington Cakes", x0,
                              y0 + LABEL_H - 5 * mm, LABEL_W, bold_font, 6.5, dark)
        else:
            _draw_centred(c, "Millington Cakes", x0,
                          y0 + LABEL_H - 5 * mm, LABEL_W, bold_font, 6.5, dark)

        # Weight string — depends on this label's unit count
        if weight_g and label_upb > 1:
            weight_str = f"{int(weight_g * label_upb)} g  ({label_upb} × {int(weight_g)} g)"
        elif weight_g:
            weight_str = f"{int(weight_g)} g"
        else:
            weight_str = None

        # ── Product name + subtitle ───────────────────────────────────────────
        y = y0 + LABEL_H - header_h - 3.5 * mm
        _draw_text(c, recipe_name, x0 + PAD, y, bold_font, FS_NAME, dark)
        y -= LH_SECTION
        sub = fmt_display
        if label_upb > 1:
            sub += f"  ·  {label_upb} uds/caja"
        _draw_text(c, sub, x0 + PAD, y, body_font, FS_SUB, mid)
        y -= 3 * mm

        # Divider
        c.setStrokeColor(border)
        c.setLineWidth(0.3)
        c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
        y -= 2.5 * mm

        # ── Key info rows ─────────────────────────────────────────────────────
        def _kv(label, value):
            nonlocal y
            c.setFont(bold_font, FS_BODY)
            c.setFillColor(mid)
            c.drawString(x0 + PAD, y, label)
            lw = c.stringWidth(label, bold_font, FS_BODY) + 1.5 * mm
            c.setFont(body_font, FS_BODY)
            c.setFillColor(dark)
            c.drawString(x0 + PAD + lw, y, value)
            y -= LH_BODY

        _kv("Lote:", lote)
        _kv("Elaborado:", prod_str)
        _kv("Consumir antes:", bb_str)
        if weight_str:
            _kv("Peso neto:", weight_str)

        y -= 1.5 * mm
        c.setStrokeColor(border)
        c.setLineWidth(0.3)
        c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
        y -= 2.5 * mm

        # ── Ingredients ───────────────────────────────────────────────────────
        _draw_text(c, "INGREDIENTES:", x0 + PAD, y, bold_font, FS_SECTION, dark)
        y -= LH_SECTION

        if ing_display:
            lines = _wrap_text(c, ing_display, body_font, bold_font, FS_BODY, CONTENT_W)
            for line_parts in lines[:4]:   # cap at 4 lines on a small label
                _draw_rich_line(c, x0 + PAD, y, line_parts,
                                body_font, bold_font, FS_BODY, dark)
                y -= LH_BODY
        else:
            _draw_text(c, "Ver ficha técnica.", x0 + PAD, y, body_font, FS_BODY, mid)
            y -= LH_BODY

        y -= 1.5 * mm

        # ── Allergens ─────────────────────────────────────────────────────────
        if allergen_contiene_str or allergen_puede_str:
            c.setStrokeColor(border)
            c.setLineWidth(0.3)
            c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
            y -= 2.5 * mm
            if allergen_contiene_str:
                _draw_text(c, "CONTIENE:", x0 + PAD, y, bold_font, FS_SECTION, dark)
                y -= LH_SECTION
                for line in _simple_wrap(c, allergen_contiene_str,
                                         bold_font, FS_BODY, CONTENT_W)[:2]:
                    _draw_text(c, line, x0 + PAD, y, bold_font, FS_BODY, dark)
                    y -= LH_BODY
            if allergen_puede_str:
                txt = "Trazas: " + allergen_puede_str
                for line in _simple_wrap(c, txt, body_font, FS_BODY, CONTENT_W)[:2]:
                    _draw_text(c, line, x0 + PAD, y, body_font, FS_BODY, mid)
                    y -= LH_BODY
            y -= 1 * mm

        # ── Storage ───────────────────────────────────────────────────────────
        c.setStrokeColor(border)
        c.setLineWidth(0.3)
        c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
        y -= 2.5 * mm
        _draw_text(c, "CONSERVACIÓN:", x0 + PAD, y, bold_font, FS_SECTION, dark)
        y -= LH_SECTION
        for line in _simple_wrap(c, storage, body_font, FS_BODY, CONTENT_W)[:3]:
            _draw_text(c, line, x0 + PAD, y, body_font, FS_BODY, dark)
            y -= LH_BODY

        # ── Footer (pinned to bottom of label) ────────────────────────────────
        footer_y = y0 + PAD
        c.setStrokeColor(border)
        c.setLineWidth(0.3)
        c.line(x0 + PAD, footer_y + 5 * mm, x0 + LABEL_W - PAD, footer_y + 5 * mm)
        _draw_text(c, COMPANY_NAME, x0 + PAD, footer_y + 3.5 * mm,
                   bold_font, FS_FOOTER, mid)
        _draw_text(c, f"CIF: {COMPANY_CIF}", x0 + PAD, footer_y + 2 * mm,
                   body_font, FS_FOOTER, light)
        addr_lines = _simple_wrap(c, COMPANY_ADDRESS, body_font, FS_FOOTER - 0.5, CONTENT_W)
        _draw_text(c, addr_lines[0] if addr_lines else COMPANY_ADDRESS,
                   x0 + PAD, footer_y + 0.5 * mm, body_font, FS_FOOTER - 0.5, light)

    # ── Lay out 9 labels per page in a 3×3 grid ───────────────────────────────
    labels_drawn = 0
    while labels_drawn < n_labels:
        for row in range(ROWS - 1, -1, -1):          # top row first (PDF y increases upward)
            for col in range(COLS):
                if labels_drawn >= n_labels:
                    break
                x0 = MARGIN + col * (LABEL_W + GAP)
                y0 = MARGIN + row * (LABEL_H + GAP)
                is_last = (labels_drawn == n_labels - 1)
                upb_this = (last_label_units if (is_last and last_label_units > 0)
                            else units_per_box)
                draw_label(c, x0, y0, label_upb=upb_this)
                labels_drawn += 1
            if labels_drawn >= n_labels:
                break
        if labels_drawn < n_labels:
            c.showPage()

    c.save()
    return buffer.getvalue()

# =============================================================================
# PDF helpers
# =============================================================================

def _load_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    fr = os.path.join(DATA_DIR, "EBGaramond-Regular.ttf")
    fb = os.path.join(DATA_DIR, "EBGaramond-Bold.ttf")
    if os.path.exists(fr) and os.path.exists(fb):
        try:
            pdfmetrics.registerFont(TTFont("Garamond",      fr))
            pdfmetrics.registerFont(TTFont("Garamond-Bold", fb))
            return "Garamond", "Garamond-Bold"
        except Exception:
            pass
    return "Helvetica", "Helvetica-Bold"


def _draw_text(c, text: str, x: float, y: float, font: str, size: float, color):
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawString(x, y, text)


def _draw_centred(c, text: str, x0: float, y: float, width: float,
                  font: str, size: float, color):
    """Draw text horizontally centred within a band starting at x0 of given width."""
    c.setFont(font, size)
    c.setFillColor(color)
    text_w = c.stringWidth(text, font, size)
    c.drawString(x0 + (width - text_w) / 2, y, text)


def _draw_label_pair(c, x, y, width, label_str, value_str, bold_font, body_font, dark, mid):
    from reportlab.lib.units import mm
    c.setFont(bold_font, 7.5)
    c.setFillColor(mid)
    c.drawString(x, y, label_str)
    label_w = c.stringWidth(label_str, bold_font, 7.5) + 2 * mm
    c.setFont(body_font, 7.5)
    c.setFillColor(dark)
    c.drawString(x + label_w, y, value_str)


def _bold_allergens(text: str, allergens: list) -> str:
    """Wrap allergen names in the ingredient text with **markers for rendering."""
    if not text or not allergens:
        return text
    result = text
    for a in allergens:
        # Match case-insensitively
        import re
        result = re.sub(
            re.escape(a), f"**{a}**",
            result, flags=re.IGNORECASE, count=1
        )
    return result


def _wrap_text(c, text: str, body_font: str, bold_font: str, size: float, max_w: float):
    """
    Very simple word-wrapper that handles **bold** markers.
    Returns list of line parts: each line is [(text, is_bold), ...].
    """
    import re
    # Split into (text, is_bold) parts
    parts = []
    for seg in re.split(r'(\*\*[^*]+\*\*)', text):
        if seg.startswith("**") and seg.endswith("**"):
            parts.append((seg[2:-2], True))
        elif seg:
            parts.append((seg, False))

    lines      = []
    cur_line   = []
    cur_width  = 0.0

    for raw_text, is_bold in parts:
        font = bold_font if is_bold else body_font
        words = raw_text.split(" ")
        for i, word in enumerate(words):
            w_text = (word + " ") if i < len(words) - 1 else word
            w_width = c.stringWidth(w_text, font, size)
            if cur_width + w_width > max_w and cur_line:
                lines.append(cur_line)
                cur_line  = []
                cur_width = 0.0
            cur_line.append((w_text, is_bold))
            cur_width += w_width

    if cur_line:
        lines.append(cur_line)

    return lines


def _draw_rich_line(c, x: float, y: float, parts, body_font: str, bold_font: str, size: float, color):
    """Draw a line of (text, is_bold) parts at position x, y."""
    c.setFillColor(color)
    cur_x = x
    for text, is_bold in parts:
        font = bold_font if is_bold else body_font
        c.setFont(font, size)
        c.drawString(cur_x, y, text)
        cur_x += c.stringWidth(text, font, size)


def _simple_wrap(c, text: str, font: str, size: float, max_w: float) -> list:
    """Simple word wrap returning list of strings, no bold support."""
    words  = text.split()
    lines  = []
    cur    = ""
    for word in words:
        test = (cur + " " + word).strip()
        if c.stringWidth(test, font, size) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [text]


# =============================================================================
# Tab 3 — Recepción de materias primas (APPCC ELD R7-01)
# =============================================================================

# Temperature limits per product type, per Millingtons APPCC Prerrequisitos p.29
_TEMP_LIMITS = {
    "refrigerated": 4.0,   # ≤4°C (ovoproductos, lácteos, pastelería, etc.)
    "frozen":      -18.0,  # ≤ -18°C (alimentos congelados y ultracongelados)
    "ambient":      None,  # no temperature check required
}

# Tolerance allowed during unloading / reception only (APPCC p.29)
_TEMP_TOLERANCE = 2.0   # °C

_TEMP_TYPE_LABELS = {
    "refrigerated": "Refrigerado (≤4°C)",
    "frozen":       "Congelado (≤ -18°C)",
    "ambient":      "Temperatura ambiente",
}


def _tab_reception():
    st.markdown("### Nueva recepción de mercancía")
    st.caption(
        "Registro obligatorio APPCC — ELD R7-01. Registra cada albarán de proveedor "
        "con los controles de temperatura, estado y trazabilidad requeridos por Sanidad."
    )

    # ── Header ────────────────────────────────────────────────────────────────
    col_date, col_prov, col_alb = st.columns([1.2, 2, 1.8])
    with col_date:
        rec_date = st.date_input("Fecha de recepción", value=date.today(), key="rec_date")
    with col_prov:
        supplier = st.text_input(
            "Proveedor", placeholder="Nombre del proveedor", key="rec_supplier"
        )
    with col_alb:
        albaran = st.text_input(
            "Nº albarán", placeholder="Ref. albarán / factura", key="rec_albaran"
        )

    _SIGNERS = ["Christine Millington", "Blanca Sánchez"]
    received_by = st.selectbox(
        "Recibido por", _SIGNERS, key="rec_by"
    )

    st.divider()

    # ── Line items ────────────────────────────────────────────────────────────
    st.markdown("#### Productos recibidos")
    st.caption(
        "Añade una fila por cada tipo de producto del albarán. "
        "Para productos refrigerados o congelados, la temperatura es un **Punto de Control Crítico (PCC)**."
    )

    n_items = st.session_state.get("rec_n_items", 1)

    items_data = []
    any_rejected = False

    for i in range(n_items):
        with st.expander(f"Producto {i + 1}", expanded=True):
            c1, c2, c3, c4 = st.columns([2.8, 1.2, 1.2, 1.4])
            with c1:
                prod_name = st.text_input(
                    "Nombre del producto / ingrediente",
                    placeholder="Ej: Huevo líquido pasteurizado",
                    key=f"rec_prod_{i}",
                )
            with c2:
                lot_ref = st.text_input(
                    "Lote proveedor", placeholder="Nº lote", key=f"rec_lot_{i}"
                )
            with c3:
                quantity = st.number_input(
                    "Cantidad",
                    min_value=0.0,
                    value=None,
                    step=0.1,
                    format="%.2f",
                    placeholder="0.00",
                    key=f"rec_qty_{i}",
                )
            with c4:
                qty_unit = st.selectbox(
                    "Unidad",
                    ["kg", "l", "ud", "g", "ml", "caja"],
                    key=f"rec_unit_{i}",
                )

            # Temperature type
            temp_type_keys = list(_TEMP_TYPE_LABELS.keys())
            temp_type_labels = list(_TEMP_TYPE_LABELS.values())
            temp_type_idx = st.radio(
                "Tipo de conservación",
                options=range(len(temp_type_keys)),
                format_func=lambda x: temp_type_labels[x],
                horizontal=True,
                key=f"rec_ttype_{i}",
            )
            temp_type = temp_type_keys[temp_type_idx]
            limit_c   = _TEMP_LIMITS[temp_type]

            # Temperature measurement — only for refrigerated / frozen (PCC)
            temp_measured = None
            temp_ok = True
            if temp_type != "ambient":
                col_t, col_lim, col_status = st.columns([1.5, 1.5, 2])
                with col_t:
                    temp_measured = st.number_input(
                        "Temperatura medida (°C)",
                        min_value=-40.0,
                        max_value=40.0,
                        value=float(limit_c),
                        step=0.1,
                        key=f"rec_temp_{i}",
                        format="%.1f",
                    )
                with col_lim:
                    st.metric("Límite crítico", f"{limit_c}°C")
                with col_status:
                    st.write("")  # spacing
                    if temp_type == "refrigerated":
                        # Limit ≤4°C, tolerance +2°C during unloading
                        if temp_measured <= limit_c:
                            st.success("✅ Temperatura conforme")
                            temp_ok = True
                        elif temp_measured <= limit_c + _TEMP_TOLERANCE:
                            st.warning(
                                f"⚠️ En tolerancia (+{_TEMP_TOLERANCE}°C). "
                                "Aceptar. Notificar al proveedor, acortar vida útil a 24h "
                                "y destinar a elaboraciones con tratamiento térmico."
                            )
                            temp_ok = True  # within tolerance — still accepted
                        else:
                            st.error("🚫 Temperatura fuera de límite — RECHAZAR")
                            temp_ok = False
                    else:  # frozen
                        # Limit ≤ -18°C (APPCC: alimentos congelados ≤ -18°C)
                        if temp_measured <= limit_c:
                            st.success("✅ Temperatura conforme")
                            temp_ok = True
                        elif temp_measured <= limit_c + _TEMP_TOLERANCE:
                            st.warning(
                                f"⚠️ En tolerancia (+{_TEMP_TOLERANCE}°C). "
                                "Aceptar con aviso al proveedor. Usar inmediatamente o "
                                "trasladar a otra cámara. No recongelar."
                            )
                            temp_ok = True
                        else:
                            st.error("🚫 Temperatura fuera de límite — RECHAZAR")
                            temp_ok = False

            # Visual / organoleptic checks
            st.markdown("**Controles visuales**")
            col_pkg, col_lbl = st.columns(2)
            with col_pkg:
                pkg_ok = st.checkbox(
                    "📦 Envase íntegro (sin roturas, golpes ni fugas)",
                    value=True,
                    key=f"rec_pkg_{i}",
                )
            with col_lbl:
                lbl_ok = st.checkbox(
                    "🏷️ Etiquetado correcto (lote y caducidad visibles)",
                    value=True,
                    key=f"rec_lbl_{i}",
                )

            # Acceptance decision (auto-suggest based on checks)
            auto_accept = temp_ok and pkg_ok and lbl_ok
            accepted = st.checkbox(
                "✅ Mercancía ACEPTADA",
                value=auto_accept,
                key=f"rec_acc_{i}",
            )

            rejection_reason = None
            if not accepted:
                any_rejected = True
                rejection_reason = st.text_area(
                    "Motivo de rechazo / acción correctora",
                    placeholder="Describe el motivo del rechazo y la acción tomada "
                                "(devolución al proveedor, apertura de incidencia, etc.)",
                    key=f"rec_rej_{i}",
                    height=70,
                )

            # Only include items with a product name filled in
            if prod_name.strip():
                items_data.append({
                    "product_name":     prod_name.strip(),
                    "supplier_lot":     lot_ref.strip() or None,
                    "quantity":         quantity,
                    "quantity_unit":    qty_unit,
                    "temp_type":        temp_type,
                    "temp_measured_c":  temp_measured,
                    "temp_limit_c":     limit_c,
                    "packaging_ok":     pkg_ok,
                    "labelling_ok":     lbl_ok,
                    "accepted":         accepted,
                    "rejection_reason": (rejection_reason or "").strip() or None,
                })

    col_add, col_rem = st.columns([1, 5])
    with col_add:
        if st.button("＋ Añadir producto", key="rec_add_item"):
            st.session_state["rec_n_items"] = n_items + 1
            st.rerun()
    if n_items > 1:
        with col_rem:
            if st.button("－ Eliminar último", key="rec_rem_item"):
                st.session_state["rec_n_items"] = n_items - 1
                st.rerun()

    # ── Notes ─────────────────────────────────────────────────────────────────
    st.markdown("#### Observaciones generales")
    notes = st.text_area(
        "Notas",
        placeholder="Ninguna incidencia — o indica cualquier observación sobre la entrega.",
        key="rec_notes",
        height=70,
        label_visibility="collapsed",
    )

    if any_rejected:
        st.warning(
            "⚠️ Hay productos rechazados. Recuerda notificar al proveedor por escrito "
            "y abrir un parte de incidencias (ELD R2-01)."
        )

    st.divider()

    # ── Validation & Save ──────────────────────────────────────────────────────
    if st.button("💾 Guardar registro de recepción", type="primary", key="rec_save"):
        errors = []
        if not supplier.strip():
            errors.append("El proveedor es obligatorio.")
        if not items_data:
            errors.append("Añade al menos un producto con nombre.")
        for it in items_data:
            if not it["accepted"] and not it.get("rejection_reason"):
                errors.append(
                    f"El producto '{it['product_name']}' está rechazado pero falta el motivo."
                )

        if errors:
            for e in errors:
                st.error(e)
        else:
            try:
                saved = db.save_goods_receipt(
                    receipt_date=rec_date,
                    supplier=supplier.strip(),
                    albaran_ref=albaran.strip() or None,
                    received_by=received_by.strip() or None,
                    items=items_data,
                    notes=notes.strip() or None,
                )
                st.session_state["last_saved_receipt"] = saved
                st.session_state["rec_n_items"] = 1
                st.rerun()
            except Exception as e:
                st.error(f"Error al guardar: {e}")

    # ── Success callout ────────────────────────────────────────────────────────
    last = st.session_state.get("last_saved_receipt")
    if last:
        n_accepted = sum(1 for it in last.get("items", []) if it.get("accepted"))
        n_rejected = len(last.get("items", [])) - n_accepted
        st.success(
            f"✅ Registro guardado — {n_accepted} producto(s) aceptado(s)"
            + (f", {n_rejected} rechazado(s)" if n_rejected else "")
        )
        col_pdf, col_clear = st.columns([2, 1])
        with col_pdf:
            try:
                pdf_bytes = _generate_receipt_pdf(last)
                fname = (
                    f"recepcion_{last['receipt_date']}_{last['supplier'][:15].replace(' ','_')}.pdf"
                )
                st.download_button(
                    "📄 Descargar registro PDF",
                    data=pdf_bytes,
                    file_name=fname,
                    mime="application/pdf",
                    key="rec_dl_pdf",
                )
            except Exception as e:
                st.warning(f"No se pudo generar el PDF: {e}")
        with col_clear:
            if st.button("Nueva recepción", key="rec_clear"):
                st.session_state.pop("last_saved_receipt", None)
                st.rerun()

    st.divider()
    _show_recent_receipts()


@st.dialog("✏️ Editar recepción de mercancía")
def _dialog_edit_receipt(r: dict):
    st.markdown(f"Proveedor: **{r['supplier']}** · {str(r['receipt_date'])[:10]}")

    _SIGNERS = ["Christine Millington", "Blanca Sánchez"]
    rc1, rc2 = st.columns(2)
    with rc1:
        raw = str(r.get("receipt_date", ""))[:10]
        new_date = st.date_input("Fecha", value=date.fromisoformat(raw) if raw else date.today())
        new_supplier = st.text_input("Proveedor", value=r.get("supplier", ""))
    with rc2:
        new_albaran  = st.text_input("Nº albarán", value=r.get("albaran_ref") or "")
        cur_rb       = r.get("received_by") or _SIGNERS[0]
        rb_idx       = _SIGNERS.index(cur_rb) if cur_rb in _SIGNERS else 0
        new_rb       = st.selectbox("Recibido por", _SIGNERS, index=rb_idx)
    new_notes = st.text_area("Observaciones", value=r.get("notes") or "", height=60)

    st.markdown("**Productos recibidos**")
    items      = r.get("items", [])
    new_items  = []
    for i, it in enumerate(items):
        with st.expander(it.get("product_name") or f"Producto {i+1}", expanded=False):
            ic1, ic2, ic3 = st.columns(3)
            with ic1:
                name = st.text_input("Producto", value=it.get("product_name", ""), key=f"re_name_{r['id']}_{i}")
                lot  = st.text_input("Lote prov.", value=it.get("supplier_lot") or "", key=f"re_lot_{r['id']}_{i}")
            with ic2:
                qty  = st.number_input("Cantidad", value=float(it.get("quantity") or 0),
                                       step=0.1, format="%.2f", key=f"re_qty_{r['id']}_{i}")
                unit = st.selectbox("Unidad", ["kg","l","ud","g","ml","caja"],
                                    index=["kg","l","ud","g","ml","caja"].index(it.get("quantity_unit","kg"))
                                          if it.get("quantity_unit") in ["kg","l","ud","g","ml","caja"] else 0,
                                    key=f"re_unit_{r['id']}_{i}")
            with ic3:
                temp = it.get("temp_measured_c")
                new_temp = st.number_input("Temperatura (°C)", value=float(temp) if temp is not None else 0.0,
                                           step=0.1, format="%.1f", key=f"re_temp_{r['id']}_{i}")
                acc  = st.checkbox("Aceptado", value=it.get("accepted", True), key=f"re_acc_{r['id']}_{i}")
            rej = st.text_input("Motivo rechazo", value=it.get("rejection_reason") or "",
                                key=f"re_rej_{r['id']}_{i}", disabled=acc)
            new_items.append({
                **{k: v for k, v in it.items() if k not in ("id","receipt_id")},
                "product_name":     name.strip(),
                "supplier_lot":     lot.strip() or None,
                "quantity":         qty,
                "quantity_unit":    unit,
                "temp_measured_c":  new_temp if it.get("temp_type") != "ambient" else None,
                "accepted":         acc,
                "rejection_reason": rej.strip() or None,
            })

    st.divider()
    b1, b2 = st.columns(2)
    with b1:
        if st.button("💾 Guardar cambios", type="primary", use_container_width=True):
            db.update_goods_receipt(r["id"], {
                "receipt_date": new_date.isoformat(),
                "supplier":     new_supplier.strip(),
                "albaran_ref":  new_albaran.strip() or None,
                "received_by":  new_rb,
                "notes":        new_notes.strip() or None,
            }, new_items)
            st.rerun()
    with b2:
        if st.button("Cancelar", use_container_width=True):
            st.rerun()


@st.dialog("🗑️ Confirmar eliminación")
def _dialog_delete_receipt(r: dict):
    st.write(f"¿Eliminar la recepción de **{r['supplier']}** del {str(r['receipt_date'])[:10]}?")
    st.caption("Esta acción no se puede deshacer.")
    d1, d2 = st.columns(2)
    with d1:
        if st.button("✅ Eliminar", type="primary", use_container_width=True):
            db.delete_goods_receipt(r["id"])
            st.rerun()
    with d2:
        if st.button("Cancelar", use_container_width=True):
            st.rerun()


def _show_recent_receipts():
    st.markdown("### Recepciones recientes")
    try:
        receipts = db.get_goods_receipts(limit=100)
    except Exception:
        st.caption("Sin registros todavía.")
        return

    if not receipts:
        st.caption("Sin registros todavía.")
        return

    h1, h2, h3, h4, h5, h6, h7, h8 = st.columns([1.2, 2, 1.5, 0.7, 0.7, 0.8, 0.5, 0.5])
    h1.markdown("**Fecha**"); h2.markdown("**Proveedor**"); h3.markdown("**Albarán**")
    h4.markdown("**Prods**"); h5.markdown("**Estado**");   h6.markdown("**PDF**")

    for r in receipts:
        n_items    = len(r.get("items", []))
        n_rejected = sum(1 for it in r.get("items", []) if not it.get("accepted", True))
        c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.2, 2, 1.5, 0.7, 0.7, 0.8, 0.5, 0.5])
        c1.write(str(r["receipt_date"])[:10])
        c2.write(r["supplier"])
        c3.write(r.get("albaran_ref") or "—")
        c4.write(str(n_items))
        if n_rejected:
            c5.markdown(f"❌ {n_rejected}")
        else:
            c5.markdown("✅")
        with c6:
            try:
                pdf_bytes = _generate_receipt_pdf(r)
                st.download_button(
                    "📄", data=pdf_bytes,
                    file_name=f"recepcion_{r['receipt_date']}_{r['supplier'][:10].replace(' ','_')}.pdf",
                    mime="application/pdf",
                    key=f"rec_dl_{r['id']}",
                )
            except Exception:
                st.write("—")
        with c7:
            if st.button("✏️", key=f"rec_edit_{r['id']}", help="Editar"):
                _dialog_edit_receipt(r)
        with c8:
            if st.button("🗑️", key=f"rec_del_{r['id']}", help="Eliminar"):
                _dialog_delete_receipt(r)


# =============================================================================
# PDF generator for goods receipt (APPCC ELD R7-01 format)
# =============================================================================

def _generate_receipt_pdf(receipt: dict) -> bytes:
    """Generate a Sanidad-compliant goods-reception record PDF."""
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    # ── Fonts ─────────────────────────────────────────────────────────────────
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    body_font = "Helvetica"
    bold_font = "Helvetica-Bold"
    try:
        reg_path  = os.path.join(DATA_DIR, "EBGaramond-Regular.ttf")
        bold_path = os.path.join(DATA_DIR, "EBGaramond-Bold.ttf")
        if os.path.exists(reg_path) and os.path.exists(bold_path):
            pdfmetrics.registerFont(TTFont("EBGaramond", reg_path))
            pdfmetrics.registerFont(TTFont("EBGaramond-Bold", bold_path))
            body_font = "EBGaramond"
            bold_font = "EBGaramond-Bold"
    except Exception:
        pass

    # ── Colours (same palette as production PDF) ──────────────────────────────
    dark  = colors.HexColor("#1a1a1a")
    mid   = colors.HexColor("#4b5563")
    light = colors.HexColor("#f3f0eb")
    accent = colors.HexColor("#92400e")   # warm brown

    def ps(name, font=None, size=11, color=dark, sa=6, sb=0, align=TA_LEFT):
        return ParagraphStyle(
            name, fontName=font or body_font, fontSize=size,
            textColor=color, spaceAfter=sa, spaceBefore=sb, alignment=align,
        )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm,  bottomMargin=2*cm,
    )

    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph(COMPANY_NAME, ps("Co", font=bold_font, size=10, color=mid, sa=2)))
    story.append(Paragraph(f"CIF {COMPANY_CIF}  ·  {COMPANY_ADDRESS}",
                           ps("Ca", size=9, color=mid, sa=0)))
    story.append(HRFlowable(width="100%", thickness=1, color=light, spaceAfter=8))
    story.append(Paragraph(
        "REGISTRO DE RECEPCIÓN DE MATERIAS PRIMAS",
        ps("Title", font=bold_font, size=14, color=dark, sa=2, align=TA_CENTER),
    ))
    story.append(Paragraph(
        "APPCC — Plan de Proveedores  ·  ELD R7-01",
        ps("Sub", size=9, color=mid, sa=10, align=TA_CENTER),
    ))

    # ── Summary table ─────────────────────────────────────────────────────────
    receipt_date = str(receipt.get("receipt_date", ""))[:10]
    summary_data = [
        ["Fecha recepción",   receipt_date],
        ["Proveedor",         receipt.get("supplier", "—")],
        ["Nº albarán",        receipt.get("albaran_ref") or "—"],
        ["Recibido por",      receipt.get("received_by") or "—"],
    ]
    summary_tbl = Table(summary_data, colWidths=[4.5*cm, 12*cm])
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (0, -1), light),
        ("FONTNAME",       (0, 0), (0, -1), bold_font),
        ("FONTNAME",       (1, 0), (1, -1), body_font),
        ("FONTSIZE",       (0, 0), (-1, -1), 10),
        ("LEADING",        (0, 0), (-1, -1), 14),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 7),
        ("GRID",           (0, 0), (-1, -1), 0.5, colors.HexColor("#d1cdc7")),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#faf9f7")]),
    ]))
    story.append(summary_tbl)
    story.append(Spacer(1, 0.5*cm))

    # ── Products table ────────────────────────────────────────────────────────
    story.append(Paragraph("Control de productos recibidos (PCC)",
                           ps("Ph", font=bold_font, size=11, sa=4)))

    items = receipt.get("items", [])
    col_w = [3.0*cm, 1.8*cm, 1.8*cm, 2.0*cm, 1.5*cm, 1.5*cm, 1.5*cm, 2.0*cm]
    header_row = [
        Paragraph("<b>Producto</b>",   ps("h", font=bold_font, size=8, sa=0)),
        Paragraph("<b>Lote prov.</b>", ps("h", font=bold_font, size=8, sa=0)),
        Paragraph("<b>Cantidad</b>",   ps("h", font=bold_font, size=8, sa=0)),
        Paragraph("<b>Tipo temp.</b>", ps("h", font=bold_font, size=8, sa=0)),
        Paragraph("<b>T° medida</b>",  ps("h", font=bold_font, size=8, sa=0)),
        Paragraph("<b>Envase OK</b>",  ps("h", font=bold_font, size=8, sa=0)),
        Paragraph("<b>Etiq. OK</b>",   ps("h", font=bold_font, size=8, sa=0)),
        Paragraph("<b>Resultado</b>",  ps("h", font=bold_font, size=8, sa=0)),
    ]
    rows = [header_row]
    style_cmds = [
        ("BACKGROUND",   (0, 0), (-1, 0), accent),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTSIZE",     (0, 0), (-1, -1), 8),
        ("LEADING",      (0, 0), (-1, -1), 11),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("GRID",         (0, 0), (-1, -1), 0.4, colors.HexColor("#d1cdc7")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#faf9f7")]),
    ]

    for idx, it in enumerate(items):
        temp_type = it.get("temp_type", "ambient")
        temp_meas = it.get("temp_measured_c")
        temp_str  = f"{temp_meas:.1f}°C" if temp_meas is not None else "N/A"
        accepted  = it.get("accepted", True)
        result_str = "✓ Aceptado" if accepted else "✗ Rechazado"
        qty       = it.get("quantity")
        qty_unit  = it.get("quantity_unit") or ""
        qty_str   = f"{qty:g} {qty_unit}".strip() if qty is not None else "—"

        row = [
            Paragraph(it.get("product_name", ""), ps("td", size=8, sa=0)),
            Paragraph(it.get("supplier_lot") or "—", ps("td", size=8, sa=0)),
            Paragraph(qty_str, ps("td", size=8, sa=0)),
            Paragraph(_TEMP_TYPE_LABELS.get(temp_type, temp_type), ps("td", size=7, sa=0)),
            Paragraph(temp_str, ps("td", size=8, sa=0)),
            Paragraph("✓" if it.get("packaging_ok", True) else "✗", ps("td", size=9, sa=0)),
            Paragraph("✓" if it.get("labelling_ok", True) else "✗", ps("td", size=9, sa=0)),
            Paragraph(result_str, ps("td", size=8, sa=0, color=(dark if accepted else colors.red))),
        ]
        rows.append(row)
        if not accepted and it.get("rejection_reason"):
            # Span a rejection reason row across all columns
            reason_row = [
                Paragraph(
                    f"  Motivo rechazo: {it['rejection_reason']}",
                    ps("rej", size=7, sa=0, color=colors.red),
                ),
                "", "", "", "", "", "",
            ]
            rows.append(reason_row)
            style_cmds.append(
                ("SPAN", (0, len(rows) - 1), (-1, len(rows) - 1))
            )
            style_cmds.append(
                ("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), colors.HexColor("#fff1f1"))
            )

    items_tbl = Table(rows, colWidths=col_w)
    items_tbl.setStyle(TableStyle(style_cmds))
    story.append(items_tbl)
    story.append(Spacer(1, 0.4*cm))

    # ── Notes ─────────────────────────────────────────────────────────────────
    notes = receipt.get("notes")
    if notes:
        story.append(Paragraph("Observaciones / incidencias", ps("Nh", font=bold_font, size=10, sa=2)))
        story.append(Paragraph(notes, ps("Nb", size=10, sa=6)))

    # ── Temperature reference table ───────────────────────────────────────────
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        "Temperaturas de referencia (Prerrequisitos APPCC — Millington Cakes, S.L.)",
        ps("Ref", size=8, color=mid, sa=2),
    ))
    ref_data = [
        ["Producto", "Temperatura de conservación", "Tolerancia en recepción"],
        ["Congelados / ultracongelados", "< -18°C", "+2°C"],
        ["Ovoproductos (huevo líquido)", "≤ 4°C", "+2°C"],
        ["Lácteos (leche, nata, yogures)", "Según etiquetado", "+2°C"],
        ["Pastelería", "≤ 4°C", "+2°C"],
        ["Temperatura ambiente (harinas, frutos secos…)", "Según etiquetado", "—"],
    ]
    ref_tbl = Table(ref_data, colWidths=[6*cm, 5*cm, 4.5*cm])
    ref_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#e5e0d8")),
        ("FONTNAME",     (0, 0), (-1, 0), bold_font),
        ("FONTNAME",     (0, 1), (-1, -1), body_font),
        ("FONTSIZE",     (0, 0), (-1, -1), 7),
        ("LEADING",      (0, 0), (-1, -1), 10),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("GRID",         (0, 0), (-1, -1), 0.4, colors.HexColor("#d1cdc7")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#faf9f7")]),
    ]))
    story.append(ref_tbl)

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=light, spaceAfter=4))
    story.append(Paragraph(
        f"Documento generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}  ·  "
        f"APPCC Rev.02_2025  ·  {COMPANY_NAME}",
        ps("Ft", size=7, color=mid, sa=0, align=TA_CENTER),
    ))

    doc.build(story)
    return buf.getvalue()
