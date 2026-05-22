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

    tab1, tab2 = st.tabs(["📋 Registro de producción", "🏷️ Imprimir etiquetas"])

    with tab1:
        _tab_log()

    with tab2:
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

    # ── PCC — oven control (APPCC record) ────────────────────────────────────
    st.markdown("#### Control de punto crítico (PCC) — horneado")
    st.caption(
        "Registro obligatorio APPCC. Anota la temperatura y tiempo alcanzados."
    )
    col_t, col_m = st.columns(2)
    with col_t:
        oven_temp = st.number_input(
            "Temperatura horno alcanzada (°C)",
            min_value=0, max_value=300, value=180, step=5, key="prod_temp"
        )
    with col_m:
        bake_time = st.number_input(
            "Tiempo de horneado (minutos)",
            min_value=0, max_value=300, value=45, step=5, key="prod_time"
        )

    # ── Ingredient references ─────────────────────────────────────────────────
    st.markdown("#### Referencias de ingredientes principales")
    st.caption(
        "Indica el número de albarán o lote del proveedor para los ingredientes "
        "clave. No es necesario incluir todos — solo los principales "
        "(harina, huevos, mantequilla, lácteos)."
    )

    n_refs = st.session_state.get("prod_n_refs", 3)

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
                oven_temp_c  = float(oven_temp),
                bake_time_min= int(bake_time),
                notes        = notes.strip() or None,
                ing_refs     = ing_refs,
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


def _show_recent_runs():
    st.markdown("### Registros recientes")
    try:
        runs = db.get_production_runs(limit=15)
    except Exception:
        st.caption("Sin registros todavía.")
        return

    if not runs:
        st.caption("Sin registros todavía.")
        return

    h1, h2, h3, h4, h5 = st.columns([2, 1.5, 1, 1, 1.2])
    h1.markdown("**Lote**")
    h2.markdown("**Producto**")
    h3.markdown("**Formato**")
    h4.markdown("**Uds**")
    h5.markdown("**Fecha**")

    for run in runs:
        c1, c2, c3, c4, c5 = st.columns([2, 1.5, 1, 1, 1.2])
        c1.code(run["lote_number"], language=None)
        c2.write(run["recipe_name"])
        c3.write(FORMAT_DISPLAY.get(run.get("format", ""), run.get("format", "—")))
        c4.write(str(run["quantity"]))
        c5.write(str(run["production_date"])[:10])


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
    best_before = run["production_date"] + timedelta(hours=shelf_hours)

    # Preview
    with st.expander("Vista previa de datos de etiqueta", expanded=True):
        c1, c2 = st.columns(2)
        c1.markdown(f"**Producto:** {run['recipe_name']}")
        c1.markdown(f"**Formato:** {FORMAT_DISPLAY.get(run.get('format',''), '—')}")
        c1.markdown(f"**Nº Lote:** `{run['lote_number']}`")
        c2.markdown(f"**Fecha elaboración:** {str(run['production_date'])[:10]}")
        c2.markdown(f"**Consumir antes de:** {best_before.strftime('%d/%m/%Y')}")
        if variant:
            c2.markdown(f"**Conservación:** {variant.get('storage_instructions','Refrigerada 0–5°C')}")

    n_labels = st.number_input(
        "Número de etiquetas a imprimir", min_value=1,
        value=run["quantity"], step=1, key="label_run_qty"
    )

    if st.button("🏷️ Generar etiquetas PDF", type="primary", key="label_run_gen"):
        try:
            pdf_bytes = _generate_labels_pdf(
                recipe_name  = run["recipe_name"],
                fmt          = run.get("format", "standard"),
                lote         = run["lote_number"],
                prod_date    = run["production_date"],
                best_before  = best_before.date() if hasattr(best_before, "date") else best_before,
                variant      = variant,
                n_labels     = int(n_labels),
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

    shelf_hours = int((variant or {}).get("shelf_life_hours") or 48)

    col_l, col_d, col_q = st.columns(3)
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
            "Nº etiquetas", min_value=1, value=1, step=1, key="lm_qty"
        )

    best_before = prod_date + timedelta(hours=shelf_hours)
    st.caption(
        f"Consumir antes de: **{best_before.strftime('%d/%m/%Y')}** "
        f"(vida útil: {shelf_hours}h desde elaboración)"
    )

    if st.button("🏷️ Generar etiquetas PDF", type="primary", key="lm_gen"):
        if not lote.strip():
            st.error("Introduce el número de lote.")
            return
        try:
            pdf_bytes = _generate_labels_pdf(
                recipe_name  = recipe_name,
                fmt          = selected_fmt,
                lote         = lote.strip(),
                prod_date    = prod_date,
                best_before  = best_before if isinstance(best_before, date) else best_before.date(),
                variant      = variant,
                n_labels     = int(n_labels),
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
        ["Receta",           run.get("recipe_name", "—")],
        ["Formato",          FORMAT_DISPLAY.get(run.get("format", ""), "—")],
        ["Fecha elaboración",prod_date],
        ["Unidades producidas", str(run.get("quantity", "—"))],
        ["Temperatura horno",f"{run.get('oven_temp_c', '—')} °C"],
        ["Tiempo horneado",  f"{run.get('bake_time_min', '—')} min"],
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
# PDF — Product labels (A6 × 2-up on A4)
# =============================================================================

def _generate_labels_pdf(
    recipe_name: str,
    fmt: str,
    lote: str,
    prod_date,
    best_before,
    variant: dict | None,
    n_labels: int,
) -> bytes:
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm, mm
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Drawing, Rect
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import math

    body_font, bold_font = _load_fonts()

    # Fetch variant data
    v           = variant or {}
    storage     = v.get("storage_instructions") or "Refrigerada entre 0 y 5°C"
    label_text  = v.get("ingredient_label_es") or ""
    weight_g    = v.get("ref_weight_g")
    fmt_display = FORMAT_DISPLAY.get(fmt, fmt)

    # Allergen declaration
    allergen_contiene  = []
    allergen_puede     = []
    if variant and variant.get("recipe_id"):
        try:
            decl = db.get_allergen_declaration(variant["recipe_id"])
            allergen_contiene = decl.get("contiene", [])
            allergen_puede    = decl.get("puede_contener", [])
        except Exception:
            pass

    # Build ingredient text with allergens in bold
    # The label_text already has allergens embedded; we wrap the allergen
    # names in <b> tags by checking against the contiene list.
    ing_display = _bold_allergens(label_text, allergen_contiene)

    # Date strings
    prod_str = prod_date.strftime("%d/%m/%Y") if hasattr(prod_date, "strftime") else str(prod_date)
    bb_str   = best_before.strftime("%d/%m/%Y") if hasattr(best_before, "strftime") else str(best_before)

    # Weight string
    weight_str = f"{int(weight_g)} g" if weight_g else None

    # ── Document ──────────────────────────────────────────────────────────────
    # Labels are A6 (105×148mm). We lay them 2 per A4 page side by side.
    PAGE_W, PAGE_H = A4  # 595.28 × 841.89 pts
    LABEL_W = 105 * mm
    LABEL_H = 148 * mm
    MARGIN  =  5 * mm

    buffer = io.BytesIO()

    # We'll use canvas directly for precise label placement
    from reportlab.pdfgen import canvas as pdfcanvas
    from reportlab.lib.utils import ImageReader

    c = pdfcanvas.Canvas(buffer, pagesize=A4)

    # Colours
    dark    = colors.HexColor("#1a1a1a")
    mid     = colors.HexColor("#4b5563")
    light   = colors.HexColor("#9ca3af")
    bg      = colors.HexColor("#F2EEE8")
    border  = colors.HexColor("#c4bdb4")

    logo_path = os.path.join(DATA_DIR, "Logo.png")
    has_logo  = os.path.exists(logo_path)

    def draw_label(c, x0: float, y0: float):
        """Draw one label with bottom-left corner at (x0, y0)."""
        PAD = 5 * mm

        # Background
        c.setFillColor(bg)
        c.roundRect(x0, y0, LABEL_W, LABEL_H, 3*mm, fill=1, stroke=0)

        # Border
        c.setStrokeColor(border)
        c.setLineWidth(0.5)
        c.roundRect(x0, y0, LABEL_W, LABEL_H, 3*mm, fill=0, stroke=1)

        # ── Header band ───────────────────────────────────────────────────────
        header_h = 22 * mm
        c.setFillColor(dark)
        c.roundRect(x0, y0 + LABEL_H - header_h, LABEL_W, header_h, 3*mm, fill=1, stroke=0)
        # Square off bottom of header band
        c.rect(x0, y0 + LABEL_H - header_h, LABEL_W, header_h / 2, fill=1, stroke=0)

        # Logo or company name in header
        if has_logo:
            try:
                img_h = 10 * mm
                img_w = 40 * mm
                c.drawImage(
                    logo_path,
                    x0 + PAD,
                    y0 + LABEL_H - header_h + 5 * mm,
                    width=img_w, height=img_h,
                    preserveAspectRatio=True, mask="auto",
                )
            except Exception:
                _draw_text(c, "Millington Cakes", x0 + PAD,
                           y0 + LABEL_H - 10*mm, bold_font, 11, colors.white)
        else:
            _draw_text(c, "Millington Cakes", x0 + PAD,
                       y0 + LABEL_H - 10*mm, bold_font, 11, colors.white)

        # ── Product name ──────────────────────────────────────────────────────
        y = y0 + LABEL_H - header_h - 7 * mm
        _draw_text(c, recipe_name, x0 + PAD, y, bold_font, 12, dark)
        y -= 5 * mm
        _draw_text(c, fmt_display, x0 + PAD, y, body_font, 9, mid)
        y -= 6 * mm

        # Divider
        c.setStrokeColor(border)
        c.setLineWidth(0.4)
        c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
        y -= 4 * mm

        # ── Lote + dates ──────────────────────────────────────────────────────
        _draw_label_pair(c, x0 + PAD, y, LABEL_W - 2*PAD,
                         "Nº Lote:", lote, bold_font, body_font, dark, mid)
        y -= 4.5 * mm
        _draw_label_pair(c, x0 + PAD, y, LABEL_W - 2*PAD,
                         "Elaborado:", prod_str, bold_font, body_font, dark, mid)
        y -= 4.5 * mm
        _draw_label_pair(c, x0 + PAD, y, LABEL_W - 2*PAD,
                         "Consumir antes de:", bb_str, bold_font, body_font, dark, mid)
        y -= 4.5 * mm

        if weight_str:
            _draw_label_pair(c, x0 + PAD, y, LABEL_W - 2*PAD,
                             "Peso neto aprox.:", weight_str, bold_font, body_font, dark, mid)
            y -= 4.5 * mm

        # Divider
        c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
        y -= 4 * mm

        # ── Ingredients ───────────────────────────────────────────────────────
        _draw_text(c, "INGREDIENTES:", x0 + PAD, y, bold_font, 7.5, dark)
        y -= 3.5 * mm

        if ing_display:
            # Word-wrap ingredient text
            max_w  = LABEL_W - 2 * PAD
            lines  = _wrap_text(c, ing_display, body_font, bold_font, 7, max_w)
            for line_parts in lines:
                _draw_rich_line(c, x0 + PAD, y, line_parts, body_font, bold_font, 7, dark)
                y -= 3.2 * mm
        else:
            _draw_text(c, "Ver ficha técnica.", x0 + PAD, y, body_font, 7, mid)
            y -= 3.5 * mm

        y -= 2 * mm

        # ── Allergens ─────────────────────────────────────────────────────────
        if allergen_contiene or allergen_puede:
            c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
            y -= 3.5 * mm
            if allergen_contiene:
                _draw_text(c, "ALÉRGENOS — CONTIENE:", x0 + PAD, y, bold_font, 7.5, dark)
                y -= 3.5 * mm
                _draw_text(c, ", ".join(a.capitalize() for a in allergen_contiene),
                           x0 + PAD, y, body_font, 7, dark)
                y -= 3.5 * mm
            if allergen_puede:
                _draw_text(c, "Puede contener: " + ", ".join(a.capitalize() for a in allergen_puede),
                           x0 + PAD, y, body_font, 7, mid)
                y -= 3.5 * mm

        # ── Storage ───────────────────────────────────────────────────────────
        c.line(x0 + PAD, y, x0 + LABEL_W - PAD, y)
        y -= 3.5 * mm
        _draw_text(c, "CONSERVACIÓN:", x0 + PAD, y, bold_font, 7.5, dark)
        y -= 3.5 * mm
        for line in _simple_wrap(c, storage, body_font, 7, LABEL_W - 2 * PAD):
            _draw_text(c, line, x0 + PAD, y, body_font, 7, dark)
            y -= 3.2 * mm

        # ── Footer ────────────────────────────────────────────────────────────
        footer_y = y0 + PAD
        c.line(x0 + PAD, footer_y + 9*mm, x0 + LABEL_W - PAD, footer_y + 9*mm)
        _draw_text(c, COMPANY_NAME, x0 + PAD, footer_y + 6*mm, bold_font, 6.5, mid)
        _draw_text(c, f"CIF: {COMPANY_CIF}", x0 + PAD, footer_y + 3*mm, body_font, 6.5, mid)
        _draw_text(c, COMPANY_ADDRESS, x0 + PAD, footer_y, body_font, 5.5, light)

    # ── Lay out labels 2-up ───────────────────────────────────────────────────
    # Left label origin:  x = (PAGE_W/2 - LABEL_W) / 2, y = (PAGE_H - LABEL_H) / 2
    # Right label origin: x = PAGE_W/2 + (PAGE_W/2 - LABEL_W) / 2
    gap     = 5 * mm
    total_w = 2 * LABEL_W + gap
    x_left  = (PAGE_W - total_w) / 2
    x_right = x_left + LABEL_W + gap
    y_base  = (PAGE_H - LABEL_H) / 2

    n_pages = math.ceil(n_labels / 2)
    labels_drawn = 0

    for page in range(n_pages):
        # Left label
        if labels_drawn < n_labels:
            draw_label(c, x_left, y_base)
            labels_drawn += 1

        # Right label
        if labels_drawn < n_labels:
            draw_label(c, x_right, y_base)
            labels_drawn += 1

        if page < n_pages - 1:
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
