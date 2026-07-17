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
#   One label per PDF page, sized for the Munbyn thermal label printer
#   (default 10×15 cm / 4×6", other roll sizes selectable). All mandatory
#   particulars are set at >=8pt — a safe margin above the EU FIC minimum
#   x-height (Reg. 1169/2011 Art.13 / Annex IV: x-height >= 1.2mm, which
#   for a serif font like EB Garamond corresponds to roughly 8pt).
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

# Thermal label roll sizes (width_mm, height_mm) — Munbyn label stock.
# 4×6" is the roll currently in use; the others are here so you can
# experiment without touching code.
LABEL_SIZE_PRESETS = {
    "10 × 15 cm  (4 × 6\")": (101.6, 152.4),
    "10 × 12,7 cm  (4 × 5\")": (101.6, 127.0),
    "10 × 10 cm  (4 × 4\")": (101.6, 101.6),
    "10 × 20 cm  (4 × 8\")": (101.6, 203.2),
}
DEFAULT_LABEL_SIZE = "10 × 15 cm  (4 × 6\")"


def _label_size_picker(key: str):
    """Render a label-size selectbox and return (width_mm, height_mm)."""
    names = list(LABEL_SIZE_PRESETS.keys())
    sel = st.selectbox(
        "Tamaño de etiqueta", names,
        index=names.index(DEFAULT_LABEL_SIZE),
        key=key,
        help="Tamaño del rollo de etiquetas térmicas cargado en la impresora Munbyn."
    )
    return LABEL_SIZE_PRESETS[sel]


# =============================================================================
# Main screen
# =============================================================================

def screen_production():
    st.title("Producción y etiquetas")
    st.caption(
        "Registra cada producción, obtén tu número de lote y genera las etiquetas "
        "para etiquetar cada producto antes de la entrega."
    )

    tab1, tab2, tab3, tab4 = st.tabs([
        "📦 Recepción de materias primas",
        "🔧 Elaboración de componentes",
        "📋 Registro de producción final",
        "🏷️ Imprimir etiquetas",
    ])

    with tab1:
        _tab_reception()

    with tab2:
        _tab_component_log()

    with tab3:
        _tab_log()

    with tab4:
        _tab_labels()


# =============================================================================
# Tab 2 — Component production log
# =============================================================================

def _tab_component_log():
    st.markdown("### Registro de elaboración de componentes")
    st.caption(
        "Registra la elaboración de componentes (cremas, bases, salsas…). "
        "El número de lote generado sirve para vincularlo al registro de producción final."
    )

    # ── Component recipe selector ─────────────────────────────────────────────
    components = db.get_component_recipes()
    if not components:
        st.info("No hay recetas de componentes definidas. Crea una receta marcada como '🔧 Component recipe'.")
        return

    comp_by_name = {c["name"]: c for c in components}
    comp_name    = st.selectbox(
        "Componente", ["— selecciona —"] + sorted(comp_by_name.keys()),
        key="comp_log_recipe"
    )
    if comp_name == "— selecciona —":
        st.info("Selecciona un componente para continuar.")
        return

    component = comp_by_name[comp_name]

    col_d, col_w = st.columns(2)
    with col_d:
        prod_date = st.date_input("Fecha", value=date.today(), key="comp_log_date")
    with col_w:
        amount_kg = st.number_input(
            "Cantidad elaborada (kg)", min_value=0.0, step=0.1,
            key="comp_log_amount_kg",
            help="Peso real de la elaboración terminada, no de las materias primas."
        )
    amount_g = amount_kg * 1000

    if component.get("labour_per_kg"):
        st.caption(
            f"Tiempo de mano de obra estimado: "
            f"**{component['labour_per_kg'] * amount_kg:.2f} h** "
            f"({component['labour_per_kg']:.2f} h/kg × {amount_kg:.2f} kg)"
        )

    # ── PCC steps ─────────────────────────────────────────────────────────────
    st.markdown("#### Control de Puntos Críticos (PCC)")
    comp_pcc_key = f"comp_pcc_{component['id']}"
    if comp_pcc_key not in st.session_state:
        try:
            template_steps = db.get_pcc_steps(component["id"])
        except Exception:
            template_steps = []
        st.session_state[comp_pcc_key] = [
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

    comp_pcc_steps = st.session_state[comp_pcc_key]
    if not comp_pcc_steps:
        st.info("ℹ️ Sin pasos PCC definidos para este componente.")
        pcc_log = []
    else:
        ph1, ph2, ph3, ph4, ph5 = st.columns([2.5, 1.2, 1, 1, 1.2])
        ph1.markdown("**Elaboración**")
        ph2.markdown("**Temp. alcanzada (°C)**")
        ph3.markdown("**Tiempo (min)**")
        ph4.markdown("**Límite crítico**")
        ph5.markdown("**¿OK?**")

        for idx, step in enumerate(comp_pcc_steps):
            pc1, pc2, pc3, pc4, pc5 = st.columns([2.5, 1.2, 1, 1, 1.2])
            pc1.markdown(f"**{step['step_name']}**")
            with pc2:
                temp = st.number_input(
                    "temp", min_value=0, max_value=300,
                    value=int(step.get("temp_achieved_c") or 0),
                    key=f"comp_pcc_temp_{component['id']}_{idx}",
                    label_visibility="collapsed"
                )
            with pc3:
                mins = st.number_input(
                    "mins", min_value=0, max_value=300,
                    value=int(step.get("time_achieved_min") or 0),
                    key=f"comp_pcc_mins_{component['id']}_{idx}",
                    label_visibility="collapsed"
                )
            limit = step.get("critical_limit_temp_c") or 70.0
            pc4.markdown(f"{limit:.0f}°C")
            ok = temp >= limit
            pc5.markdown("✅" if ok else "⚠️ Revisar")
            comp_pcc_steps[idx]["temp_achieved_c"]   = temp
            comp_pcc_steps[idx]["time_achieved_min"] = mins

        pcc_log = [
            {
                "step_name":             s["step_name"],
                "temp_achieved_c":       s["temp_achieved_c"],
                "time_achieved_min":     s["time_achieved_min"],
                "critical_limit_temp_c": s.get("critical_limit_temp_c") or 70.0,
                "ok":                    s["temp_achieved_c"] >= (s.get("critical_limit_temp_c") or 70.0),
            }
            for s in comp_pcc_steps
        ]

    # ── Ingredient refs ───────────────────────────────────────────────────────
    st.markdown("#### Materias primas utilizadas")
    st.caption("Ingrediente y número de lote del proveedor.")
    _lote_to_albaran = {}
    try:
        _lote_to_albaran = db.get_albaran_by_lote()
    except Exception:
        pass

    # Pre-populate ingredient names from the component recipe's key ingredients
    _comp_ings = []
    try:
        _comp_ings = db.get_key_ingredients_for_recipe(component["id"])
    except Exception:
        pass

    import hashlib as _hashlib, json as _json
    _comp_sig = _hashlib.md5(
        _json.dumps([(i["name"], i.get("pct")) for i in _comp_ings]).encode()
    ).hexdigest()
    _last_comp   = st.session_state.get("comp_log_last_recipe")
    _last_comp_sig = st.session_state.get("comp_log_last_sig")
    if _last_comp != component["id"] or _last_comp_sig != _comp_sig:
        st.session_state["comp_log_last_recipe"] = component["id"]
        st.session_state["comp_log_last_sig"]    = _comp_sig
        st.session_state["comp_log_n_refs"]      = max(3, len(_comp_ings))
        for i in range(10):
            st.session_state.pop(f"comp_ing_{i}", None)
            st.session_state.pop(f"comp_alb_{i}", None)

    # Pre-populate names if not already set
    for i, ing in enumerate(_comp_ings[:st.session_state.get("comp_log_n_refs", 3)]):
        key = f"comp_ing_{i}"
        if key not in st.session_state:
            pct_tag     = f" ({ing['pct']}%)" if ing.get("pct") else ""
            allergen_tag = " ⚠️" if ing.get("is_allergen_bearing") else ""
            st.session_state[key] = ing["name"] + pct_tag + allergen_tag

    n_comp_refs = st.session_state.get("comp_log_n_refs", 3)
    comp_ing_refs = []
    for i in range(n_comp_refs):
        ci1, ci2, ci3 = st.columns([2, 2, 0.5])
        with ci1:
            ing_name = st.text_input(
                "Ingrediente", key=f"comp_ing_{i}",
                placeholder="e.g. Mantequilla",
                label_visibility="visible" if i == 0 else "collapsed"
            )
        with ci2:
            alb_ref = st.text_input(
                "Ref. Lote", key=f"comp_alb_{i}",
                placeholder="e.g. L-2025-0451",
                label_visibility="visible" if i == 0 else "collapsed"
            )
            _match = _lote_to_albaran.get(alb_ref.strip().lower())
            if _match:
                st.caption(f"↳ Albarán: `{_match}`")
        with ci3:
            if i == n_comp_refs - 1:
                st.write("")
                if i == 0:
                    st.write("")
                if st.button("＋", key=f"comp_add_{i}"):
                    st.session_state["comp_log_n_refs"] = n_comp_refs + 1
                    st.rerun()
        if ing_name.strip():
            comp_ing_refs.append({
                "ingredient_name": ing_name.strip(),
                "albaran_ref":     alb_ref.strip() or None,
            })

    notes = st.text_area(
        "Notas / incidencias", key="comp_log_notes", height=60,
        label_visibility="collapsed",
        placeholder="Ninguna incidencia — o describe cualquier desviación."
    )

    st.divider()

    if st.button("💾 Guardar elaboración de componente", type="primary",
                 disabled=(amount_g <= 0)):
        try:
            run = db.save_component_production_run(
                recipe_id         = component["id"],
                recipe_name       = component["name"],
                prod_date         = prod_date,
                amount_produced_g = amount_g,
                notes             = notes.strip() or None,
                ing_refs          = comp_ing_refs,
                pcc_log           = pcc_log,
            )
            st.session_state["last_comp_run"] = run
            st.session_state["comp_log_n_refs"] = 3
            st.rerun()
        except Exception as e:
            st.error(f"Error al guardar: {e}")

    last = st.session_state.get("last_comp_run")
    if last:
        lote = last["lote_number"]
        st.success("✅ Elaboración de componente guardada")
        st.markdown(
            f"<div style='background:#F2EEE8;border-radius:8px;padding:16px 20px;"
            f"margin:8px 0;'>"
            f"<span style='font-size:13px;color:#6b7280;'>Número de lote</span><br>"
            f"<span style='font-size:26px;font-weight:700;letter-spacing:2px;"
            f"color:#1a1a1a;'>{lote}</span><br>"
            f"<span style='font-size:12px;color:#9ca3af;'>Vincula este lote al registro "
            f"de producción final</span></div>",
            unsafe_allow_html=True
        )
        if st.button("Nueva elaboración", key="comp_clear"):
            st.session_state.pop("last_comp_run", None)
            st.rerun()

    # ── Recent component runs ─────────────────────────────────────────────────
    st.divider()
    st.markdown("#### Elaboraciones recientes")
    recent = db.get_component_production_runs(limit=20)
    if not recent:
        st.caption("Sin elaboraciones registradas.")
    else:
        for run in recent:
            kg = (run.get("amount_produced_g") or 0) / 1000
            st.markdown(
                f"`{run['lote_number']}` — **{run['recipe_name']}** — "
                f"{run['production_date']} — {kg:.2f} kg"
            )


# =============================================================================
# Tab 3 — Final recipe production log
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
    # For component-based recipes, show component names (one level only) so the
    # operator can record which batch of each component was used.
    # For direct-ingredient recipes, recurse to key leaf ingredients as before.
    # Note: label/allergen resolution always recurses to leaf level — unchanged.
    recipe_ings = []
    try:
        lines = db.get_recipe_lines(recipe["id"])
        component_lines = [l for l in lines if l.get("is_component_line")]
        if component_lines:
            # Use component names directly — one level, no recursion
            recipe_ings = [
                {
                    "name":                l["ingredient_name"],
                    "pct":                 None,
                    "is_allergen_bearing": False,
                    "component_recipe_id": l.get("component_recipe_id"),
                }
                for l in component_lines
            ]
        else:
            recipe_ings = db.get_key_ingredients_for_recipe(recipe["id"])
    except Exception:
        pass

    # Number of rows = max(3, number of key ingredients in recipe)
    n_rows_default = max(3, len(recipe_ings))
    # Reset row count when recipe changes OR when its ingredient list changes
    # (catches edits to an existing recipe without an ID change)
    import hashlib as _hashlib, json as _json
    _ings_sig = _hashlib.md5(
        _json.dumps([(i["name"], i.get("pct")) for i in recipe_ings]).encode()
    ).hexdigest()
    last_recipe = st.session_state.get("prod_last_recipe")
    last_sig    = st.session_state.get("prod_last_ings_sig")
    if last_recipe != recipe["id"] or last_sig != _ings_sig:
        st.session_state["prod_n_refs"]       = n_rows_default
        st.session_state["prod_last_recipe"]  = recipe["id"]
        st.session_state["prod_last_ings_sig"] = _ings_sig
        # clear any previous ingredient inputs
        for i in range(10):
            st.session_state.pop(f"prod_ing_{i}", None)
            st.session_state.pop(f"prod_alb_{i}", None)
            st.session_state.pop(f"prod_alb_sel_{i}", None)
            st.session_state.pop(f"prod_alb_txt_{i}", None)

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

    # ── Per-component recent runs (for albaran dropdowns) ─────────────────────
    # Build {component_recipe_id: [run, ...]} and {lote_number: run_id} maps
    comp_runs_by_recipe: dict = {}
    comp_run_id_by_lote: dict = {}
    for _ing in recipe_ings:
        _crid = _ing.get("component_recipe_id")
        if _crid and _crid not in comp_runs_by_recipe:
            try:
                _runs = db.get_production_runs_for_recipe(_crid, limit=10)
                comp_runs_by_recipe[_crid] = _runs
                for _r in _runs:
                    comp_run_id_by_lote[_r["lote_number"]] = _r["id"]
            except Exception:
                comp_runs_by_recipe[_crid] = []

    has_component_ings = any(i.get("component_recipe_id") for i in recipe_ings)

    # ── Ingredient references ─────────────────────────────────────────────────
    st.markdown("#### Referencias de ingredientes principales")
    if has_component_ings:
        st.caption(
            "Selecciona el lote elaborado de cada componente, o introduce la "
            "referencia manualmente si no aparece en la lista."
        )
    elif recipe_ings:
        allergen_names = [i["name"] for i in recipe_ings if i.get("is_allergen_bearing")]
        criteria_parts = ["≥5% del peso total"]
        if allergen_names:
            criteria_parts.append(f"alérgenos ({', '.join(allergen_names)})")
        st.caption(
            f"Ingredientes clave según criterio APPCC: {' + '.join(criteria_parts)}. "
            f"Indica el número de lote del proveedor para cada uno."
        )
    else:
        st.caption(
            "Indica el número de lote del proveedor para los ingredientes "
            "clave (≥5% del peso o alérgenos)."
        )

    _lote_to_albaran = {}
    try:
        _lote_to_albaran = db.get_albaran_by_lote()
    except Exception:
        pass

    n_refs = st.session_state.get("prod_n_refs", n_rows_default)

    # Last-used lote refs per ingredient name (from most recent run with same recipe)
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
    selected_comp_run_ids = []
    _MANUAL = "— entrada manual —"
    _NONE   = "— selecciona —"

    for i in range(n_refs):
        ing_meta = recipe_ings[i] if i < len(recipe_ings) else {}
        comp_rid = ing_meta.get("component_recipe_id")
        comp_runs = comp_runs_by_recipe.get(comp_rid, []) if comp_rid else []

        c1, c2, c3 = st.columns([2, 2, 0.5])
        with c1:
            ing_name = st.text_input(
                "Ingrediente", key=f"prod_ing_{i}",
                placeholder="e.g. Harina de trigo",
                label_visibility="visible" if i == 0 else "collapsed"
            )
        with c2:
            if comp_rid and comp_runs:
                # Component with logged runs — show dropdown
                run_options = (
                    [_NONE]
                    + [f"{r['lote_number']}  ({r['production_date']})" for r in comp_runs]
                    + [_MANUAL]
                )
                sel = st.selectbox(
                    "Lote componente", run_options,
                    key=f"prod_alb_sel_{i}",
                    label_visibility="visible" if i == 0 else "collapsed"
                )
                if sel == _MANUAL:
                    alb_ref = st.text_input(
                        "Ref. manual", key=f"prod_alb_txt_{i}",
                        placeholder="Introduce lote o ref.",
                        label_visibility="collapsed"
                    )
                elif sel == _NONE:
                    alb_ref = ""
                else:
                    alb_ref = sel.split("  ")[0].strip()
                    # Track the run ID for linking
                    run_id = comp_run_id_by_lote.get(alb_ref)
                    if run_id:
                        selected_comp_run_ids.append(run_id)
            else:
                # No logged runs yet (or direct ingredient) — plain text input
                alb_ref = st.text_input(
                    "Ref. Lote", key=f"prod_alb_{i}",
                    placeholder="e.g. L-2025-0451" if not comp_rid else "MC-XXXXXX",
                    label_visibility="visible" if i == 0 else "collapsed"
                )
                _match = _lote_to_albaran.get(alb_ref.strip().lower())
                if _match:
                    st.caption(f"↳ Albarán: `{_match}`")
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
            if selected_comp_run_ids:
                db.link_component_runs(run["id"], selected_comp_run_ids)
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
                "Ref. Lote" if i == 0 else "​",
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
    _default_n = _math.ceil(run["quantity"] / max(1, int(units_per_box)))
    with col_nlab:
        n_labels = st.number_input(
            "Nº etiquetas", min_value=1,
            value=_default_n, step=1, key="label_run_qty",
            help="Por defecto: unidades ÷ uds por caja. Cada etiqueta se imprime en su propia página."
        )
    with col_fdays:
        frozen_days = st.number_input(
            "Vida útil congelado (días)", min_value=1, value=90, step=1,
            key="label_run_fdays",
            disabled=not frozen,
            help="Días desde elaboración hasta fecha de consumo preferente"
        )

    label_w_mm, label_h_mm = _label_size_picker("label_run_size")

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
                label_width_mm   = label_w_mm,
                label_height_mm  = label_h_mm,
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
            "Nº etiquetas", min_value=1, value=1, step=1, key="lm_qty",
            help="Cada etiqueta se imprime en su propia página."
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

    label_w_mm, label_h_mm = _label_size_picker("lm_size")

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
                label_width_mm   = label_w_mm,
                label_height_mm  = label_h_mm,
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
        ing_data = [["Ingrediente", "Ref. Lote proveedor"]] + [
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
# PDF — Product labels  (one label per page, sized for the thermal printer)
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
    label_width_mm: float = 101.6,
    label_height_mm: float = 152.4,
) -> bytes:
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

    # ── Page / label geometry ─────────────────────────────────────────────────
    # One label = one PDF page, sized exactly to the thermal roll so it
    # prints at 100% scale with no cropping.
    LABEL_W = label_width_mm  * mm
    LABEL_H = label_height_mm * mm
    PAGE_W, PAGE_H = LABEL_W, LABEL_H
    x0, y0  = 0, 0

    PAD       = 4 * mm
    CONTENT_W = LABEL_W - 2 * PAD

    # ── Colours ───────────────────────────────────────────────────────────────
    # Pure black on white — the off-white/grey palette used elsewhere in the
    # app doesn't reproduce well on the thermal printer (greys dither into
    # faint, patchy dots instead of a clean solid), so this label uses solid
    # black ink on a plain white background throughout, with bold/size for
    # hierarchy instead of colour.
    dark      = colors.HexColor("#000000")
    mid       = colors.HexColor("#000000")
    light     = colors.HexColor("#000000")
    bg        = colors.HexColor("#FFFFFF")
    border    = colors.HexColor("#000000")
    header_bg = colors.HexColor("#FFFFFF")

    logo_path = os.path.join(DATA_DIR, "Logo.png")
    has_logo  = os.path.exists(logo_path)

    buffer = _io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=(PAGE_W, PAGE_H))

    # ── Font sizes ────────────────────────────────────────────────────────────
    # EU FIC (Reg. 1169/2011, Art. 13 / Annex IV) requires an x-height of at
    # least 1.2mm for mandatory particulars. For a serif font like EB Garamond
    # (lower x-height ratio than a sans-serif) that works out to roughly 8pt,
    # so every mandatory field below (name, ingredients, allergens, net weight,
    # dates, storage, operator name/address, lot) is set at >=8pt.
    FS_NAME    = 15.0   # product name
    FS_SUB     = 9.0    # format / subtitle
    FS_SECTION = 9.5    # section headers (INGREDIENTES, CONSERVACIÓN…)
    FS_BODY    = 8.5    # body text
    FS_FOOTER  = 8.0    # company footer (name, CIF, address)

    # ── Line heights ──────────────────────────────────────────────────────────
    LH_BODY    = 4.0 * mm
    LH_SECTION = 4.5 * mm
    LH_NAME    = 6.0 * mm

    # Header band scales down on shorter labels so the mandatory sections
    # below (ingredients, allergens, storage, footer) keep enough room.
    header_h = max(12 * mm, min(20 * mm, LABEL_H * 0.13))

    def _draw_bg_and_header():
        """Draw the background, border and header band (logo) for one page."""
        c.setFillColor(bg)
        c.roundRect(x0, y0, LABEL_W, LABEL_H, 2.5 * mm, fill=1, stroke=0)
        c.setStrokeColor(border)
        c.setLineWidth(0.6)
        c.roundRect(x0, y0, LABEL_W, LABEL_H, 2.5 * mm, fill=0, stroke=1)

        c.setStrokeColor(border)
        c.setLineWidth(0.7)
        c.line(x0, y0 + LABEL_H - header_h, x0 + LABEL_W, y0 + LABEL_H - header_h)

        if has_logo:
            try:
                reader = ImageReader(logo_path)
                iw, ih = reader.getSize()
                aspect = iw / ih if ih else 2
                logo_h = 12 * mm
                logo_w = min(logo_h * aspect, LABEL_W - 2 * PAD)
                logo_x = x0 + (LABEL_W - logo_w) / 2
                logo_y = y0 + LABEL_H - header_h + (header_h - logo_h) / 2
                c.drawImage(logo_path, logo_x, logo_y, width=logo_w,
                            height=logo_h, preserveAspectRatio=True, mask="auto")
            except Exception:
                _draw_centred(c, "Millington Cakes", x0,
                              y0 + LABEL_H - 10 * mm, LABEL_W, bold_font, 13, dark)
        else:
            _draw_centred(c, "Millington Cakes", x0,
                          y0 + LABEL_H - 10 * mm, LABEL_W, bold_font, 13, dark)

    def draw_label(label_upb: int = units_per_box):
        """Draw the label content (below the header, which is static)."""

        # Weight string — depends on this label's unit count
        if weight_g and label_upb > 1:
            weight_str = f"{int(weight_g * label_upb)} g  ({label_upb} × {int(weight_g)} g)"
        elif weight_g:
            weight_str = f"{int(weight_g)} g"
        else:
            weight_str = None

        # ── Product name + subtitle ───────────────────────────────────────────
        y = y0 + LABEL_H - header_h - 5 * mm
        for name_line in _simple_wrap(c, recipe_name, bold_font, FS_NAME, CONTENT_W)[:2]:
            _draw_text(c, name_line, x0 + PAD, y, bold_font, FS_NAME, dark)
            y -= LH_NAME
        sub = fmt_display
        if label_upb > 1:
            sub += f"  ·  {label_upb} uds/caja"
        _draw_text(c, sub, x0 + PAD, y, body_font, FS_SUB, mid)
        y -= 4 * mm

        # Divider
        c.setStrokeColor(border)
        c.setLineWidth(0.4)
        c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
        y -= 3.5 * mm

        # ── Key info rows — two per line (Lote/Elaborado, Consumir antes/
        #    Peso neto) so this block only costs 2 lines instead of 4. ────────
        col2_x = x0 + PAD + CONTENT_W / 2

        def _kv(label, value, at_x):
            c.setFont(bold_font, FS_BODY)
            c.setFillColor(mid)
            c.drawString(at_x, y, label)
            lw = c.stringWidth(label, bold_font, FS_BODY) + 2 * mm
            c.setFont(body_font, FS_BODY)
            c.setFillColor(dark)
            c.drawString(at_x + lw, y, value)

        _kv("Lote:", lote, x0 + PAD)
        _kv("Elaborado:", prod_str, col2_x)
        y -= LH_BODY
        _kv("Consumir antes:", bb_str, x0 + PAD)
        if weight_str:
            _kv("Peso neto:", weight_str, col2_x)
        y -= LH_BODY

        y -= 1.5 * mm
        c.setStrokeColor(border)
        c.setLineWidth(0.4)
        c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
        y -= 3.5 * mm

        # ── Ingredients / allergens / storage share whatever vertical room
        #    is left before the footer. Rather than guess at fixed budgets
        #    (fragile — easy for the guess to drift from what's actually
        #    drawn), we measure the real layout with a dry run and shrink
        #    the least-important content first until it fits:
        #      1. ingredients can always shrink to 1 line (never disappears)
        #      2. "may contain traces" is advisory, not a legal requirement
        #         — first to go if space is critically tight
        #      3. storage instructions shrink toward 1 line
        #      4. "CONTIENE" (mandatory allergens) shrinks toward 1 line
        #         only as an absolute last resort
        # ──────────────────────────────────────────────────────────────────
        footer_top = (y0 + PAD) + 10 * mm + 2 * mm  # footer divider + safety buffer
        allergens_present = bool(allergen_contiene_str or allergen_puede_str)

        contiene_lines_full = (_simple_wrap(c, allergen_contiene_str, bold_font, FS_BODY, CONTENT_W)[:2]
                                if allergen_contiene_str else [])
        puede_lines_full    = (_simple_wrap(c, "Trazas: " + allergen_puede_str, body_font, FS_BODY, CONTENT_W)[:2]
                                if allergen_puede_str else [])
        storage_lines_full  = _simple_wrap(c, storage, body_font, FS_BODY, CONTENT_W)[:4]
        ing_lines_full      = (_wrap_text(c, ing_display, body_font, bold_font, FS_BODY, CONTENT_W)
                                if ing_display else [])

        def _measure(y_top, n_ing, n_contiene, n_puede, n_storage):
            """Replay the section layout arithmetic without drawing anything;
            return the y position after the storage section."""
            yy = y_top - LH_SECTION                        # "INGREDIENTES:" header
            yy -= max(n_ing, 1) * LH_BODY if ing_lines_full else LH_BODY
            yy -= 2 * mm
            if allergens_present:
                yy -= 4 * mm                                # divider gap
                if n_contiene:
                    yy -= LH_SECTION + n_contiene * LH_BODY
                yy -= n_puede * LH_BODY
                yy -= 2 * mm
            yy -= 4 * mm                                    # divider gap before storage
            yy -= LH_SECTION + max(n_storage, 1) * LH_BODY
            return yy

        n_ing      = len(ing_lines_full)
        n_contiene = len(contiene_lines_full)
        n_puede    = len(puede_lines_full)
        n_storage  = len(storage_lines_full)

        # Shrink ingredients down to 1 line first (cheapest to give up —
        # the full list is always in the recipe's approved spec sheet too).
        while n_ing > 1 and _measure(y, n_ing, n_contiene, n_puede, n_storage) < footer_top:
            n_ing -= 1
        # Then drop "may contain traces" (advisory, not a FIC requirement).
        while n_puede > 0 and _measure(y, n_ing, n_contiene, n_puede, n_storage) < footer_top:
            n_puede -= 1
        # Then trim storage instructions toward 1 line.
        while n_storage > 1 and _measure(y, n_ing, n_contiene, n_puede, n_storage) < footer_top:
            n_storage -= 1
        # Last resort: trim "CONTIENE" toward 1 line.
        while n_contiene > 1 and _measure(y, n_ing, n_contiene, n_puede, n_storage) < footer_top:
            n_contiene -= 1

        max_ing_lines   = max(n_ing, 1)
        contiene_lines  = contiene_lines_full[:n_contiene]
        puede_lines     = puede_lines_full[:n_puede]
        storage_lines   = storage_lines_full[:n_storage]

        # ── Ingredients ───────────────────────────────────────────────────────
        _draw_text(c, "INGREDIENTES:", x0 + PAD, y, bold_font, FS_SECTION, dark)
        y -= LH_SECTION

        if ing_lines_full:
            for line_parts in ing_lines_full[:max_ing_lines]:
                _draw_rich_line(c, x0 + PAD, y, line_parts,
                                body_font, bold_font, FS_BODY, dark)
                y -= LH_BODY
        else:
            _draw_text(c, "Ver ficha técnica.", x0 + PAD, y, body_font, FS_BODY, mid)
            y -= LH_BODY

        y -= 2 * mm

        # ── Allergens ─────────────────────────────────────────────────────────
        if allergens_present:
            c.setStrokeColor(border)
            c.setLineWidth(0.4)
            c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
            y -= 4 * mm
            if contiene_lines:
                _draw_text(c, "CONTIENE:", x0 + PAD, y, bold_font, FS_SECTION, dark)
                y -= LH_SECTION
                for line in contiene_lines:
                    _draw_text(c, line, x0 + PAD, y, bold_font, FS_BODY, dark)
                    y -= LH_BODY
            for line in puede_lines:
                _draw_text(c, line, x0 + PAD, y, body_font, FS_BODY, mid)
                y -= LH_BODY
            y -= 2 * mm

        # ── Storage ───────────────────────────────────────────────────────────
        c.setStrokeColor(border)
        c.setLineWidth(0.4)
        c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
        y -= 4 * mm
        _draw_text(c, "CONSERVACIÓN:", x0 + PAD, y, bold_font, FS_SECTION, dark)
        y -= LH_SECTION
        for line in storage_lines:
            _draw_text(c, line, x0 + PAD, y, body_font, FS_BODY, dark)
            y -= LH_BODY

        # ── Footer (pinned to bottom of label) ────────────────────────────────
        footer_y = y0 + PAD
        c.setStrokeColor(border)
        c.setLineWidth(0.4)
        c.line(x0 + PAD, footer_y + 10 * mm, x0 + LABEL_W - PAD, footer_y + 10 * mm)
        _draw_text(c, COMPANY_NAME, x0 + PAD, footer_y + 7 * mm,
                   bold_font, FS_FOOTER, mid)
        _draw_text(c, f"CIF: {COMPANY_CIF}", x0 + PAD, footer_y + 4 * mm,
                   body_font, FS_FOOTER, light)
        addr_lines = _simple_wrap(c, COMPANY_ADDRESS, body_font, FS_FOOTER, CONTENT_W)
        _draw_text(c, addr_lines[0] if addr_lines else COMPANY_ADDRESS,
                   x0 + PAD, footer_y + 1 * mm, body_font, FS_FOOTER, light)

    # ── One label per page ────────────────────────────────────────────────────
    for i in range(n_labels):
        is_last  = (i == n_labels - 1)
        upb_this = (last_label_units if (is_last and last_label_units > 0)
                    else units_per_box)
        _draw_bg_and_header()
        draw_label(label_upb=upb_this)
        if not is_last:
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
