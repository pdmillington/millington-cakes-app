# screen_catalogue.py
# =============================================================================
# Wholesale catalogue generator.
#
# Select which products to include, preview the price table,
# then generate a PDF matching the format of the original LaTeX catalogue.
#
# PDF uses EB Garamond font (data/EBGaramond-Regular.ttf and Bold)
# and the Millington logo (data/Logo.png).
# Background colour: #F2EEE8 (warm off-white).
# =============================================================================

import streamlit as st
import millington_db as db
import os
import io
from datetime import date


# ── Format groupings for catalogue ───────────────────────────────────────────
FORMAT_GROUPS = [
    ("Tarta",             "standard"),
    ("Tarta Individual",  "individual"),
    ("Bocados",           "bocado"),
]

# Products that belong to "Otros" in the catalogue
OTROS_RECIPES = {
    "Cookies", "Brownie", "Blondie", "Brioche - canela",
    "Brioche - chocolate", "Scones", "Scone con pasas",
    "Trufas chocolate negro",
}

BG_COLOUR = "#F2EEE8"
DARK      = "#1a1a1a"
GREY      = "#6b7280"
HEADER_BG = "#a0a0a0"

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def screen_catalogue():
    st.title("Catálogo mayorista")
    st.caption(
        "Selecciona los productos a incluir y genera el PDF del catálogo. "
        "Los precios se toman directamente de las variantes de producto."
    )

    # ── Load data ─────────────────────────────────────────────────────────────
    recipes      = db.get_recipes()
    all_variants = db.get_all_variants_full()
    settings     = db.get_settings()

    recipe_by_id  = {r["id"]: r for r in recipes}
    var_lookup: dict[str, dict[str, dict]] = {}
    for v in all_variants:
        var_lookup.setdefault(v["recipe_id"], {})[v["format"]] = v

    # ── Build product list grouped for display ────────────────────────────────
    # Each entry: {recipe_name, format, size_description, ws_price_ex_vat, recipe_id}
    catalogue_rows: dict[str, list[dict]] = {
        "Tarta": [], "Tarta Individual": [],
        "Bocados": [], "Otros": []
    }

    for recipe in sorted(recipes, key=lambda r: r["name"]):
        rid  = recipe["id"]
        name = recipe["name"]
        is_otros = name in OTROS_RECIPES

        for fmt_label, fmt_key in FORMAT_GROUPS:
            if fmt_key == "standard":
                active = True
            elif fmt_key == "individual":
                active = bool(recipe.get("has_individual"))
            else:
                active = bool(recipe.get("has_bocado"))

            if not active:
                continue

            variant = var_lookup.get(rid, {}).get(fmt_key, {})
            ws_price = float(variant.get("ws_price_ex_vat") or 0) or None

            size_desc = variant.get("size_description") or ""

            group = "Otros" if is_otros and fmt_key == "standard" \
                else fmt_label

            catalogue_rows[group].append({
                "recipe_id":    rid,
                "fmt_key":      fmt_key,
                "name":         name,
                "size":         size_desc,
                "ws_price":     ws_price,
                "group":        group,
            })

    # ── Product selector ──────────────────────────────────────────────────────
    st.markdown("### Selección de productos")
    st.caption("Marca los productos a incluir en el catálogo.")

    selected_rows: list[dict] = []

    for group_name, group_rows in catalogue_rows.items():
        if not group_rows:
            continue
        st.markdown(f"**{group_name}**")
        cols = st.columns(2)
        for i, row in enumerate(group_rows):
            price_str = f"€ {row['ws_price']:.2f}" if row["ws_price"] else "Sin precio"
            label = f"{row['name']}  ·  {row['size']}  ·  {price_str}"
            checked = cols[i % 2].checkbox(
                label,
                value=bool(row["ws_price"]),  # default: tick if has price
                key=f"cat_sel_{row['recipe_id']}_{row['fmt_key']}"
            )
            if checked:
                selected_rows.append(row)

    st.divider()

    if not selected_rows:
        st.info("Selecciona al menos un producto para continuar.")
        return

    # ── Preview ───────────────────────────────────────────────────────────────
    st.markdown("### Vista previa")

    with st.expander("Ver tabla de precios", expanded=True):
        _render_preview_table(selected_rows)

    # ── Catalogue header options ───────────────────────────────────────────────
    st.markdown("### Opciones del catálogo")

    col_a, col_b = st.columns(2)
    with col_a:
        catalogue_title = st.text_input(
            "Título del catálogo",
            value="Catálogo para Catering y Hostelería",
            key="cat_title"
        )
        catalogue_date = st.text_input(
            "Fecha (aparece en el documento)",
            value=date.today().strftime("%B %Y").capitalize(),
            key="cat_date"
        )
    with col_b:
        client_name = st.text_input(
            "Cliente (opcional — para catálogo personalizado)",
            placeholder="e.g. Restaurante La Paloma",
            key="cat_client"
        )
        include_conditions = st.checkbox(
            "Incluir condiciones de pedido", value=True,
            key="cat_conditions"
        )

    # Override conditions text for this client
    if include_conditions:
        with st.expander("✏️ Editar condiciones para este catálogo"):
            st.caption(
                "Los valores por defecto vienen de Settings. "
                "Edita aquí solo si este cliente tiene condiciones especiales."
            )
            custom_allergen = st.text_area(
                "Nota alérgenos",
                value=settings.get("cond_allergen_notice") or "",
                key="cat_cond_allergen", height=60
            )
            custom_availability = st.text_area(
                "Disponibilidad",
                value=settings.get("cond_availability_notice") or "",
                key="cat_cond_availability", height=60
            )
            custom_returns = st.text_area(
                "Devoluciones / calidad",
                value=settings.get("cond_returns_policy") or "",
                key="cat_cond_returns", height=60
            )
    else:
        custom_allergen     = settings.get("cond_allergen_notice") or ""
        custom_availability = settings.get("cond_availability_notice") or ""
        custom_returns      = settings.get("cond_returns_policy") or ""

    st.divider()

    # ── Generate PDF ──────────────────────────────────────────────────────────
    if st.button("📄 Generar PDF", type="primary"):
        with st.spinner("Generando catálogo…"):
            try:
                pdf_bytes = _generate_pdf(
                    rows             = selected_rows,
                    settings         = settings,
                    title            = catalogue_title,
                    cat_date         = catalogue_date,
                    client_name      = client_name or None,
                    include_cond     = include_conditions,
                    cond_allergen    = custom_allergen,
                    cond_avail       = custom_availability,
                    cond_returns     = custom_returns,
                )
                fname = f"millington_catalogo_{date.today().isoformat()}.pdf"
                st.download_button(
                    "⬇️ Descargar catálogo PDF",
                    data=pdf_bytes,
                    file_name=fname,
                    mime="application/pdf",
                    type="primary"
                )
                st.success("PDF generado correctamente", icon="✅")
            except Exception as e:
                st.error(f"Error al generar el PDF: {e}")
                st.exception(e)


# =============================================================================
# Preview table
# =============================================================================

def _render_preview_table(rows: list[dict]):
    """Render an in-app preview of the catalogue table."""
    groups_order = ["Tarta", "Tarta Individual", "Bocados", "Otros"]
    by_group: dict[str, list] = {}
    for r in rows:
        by_group.setdefault(r["group"], []).append(r)

    for group in groups_order:
        group_rows = by_group.get(group, [])
        if not group_rows:
            continue

        h1, h2, h3, h4 = st.columns([1.2, 2.5, 1.5, 1])
        h1.markdown(f"**{group}**")
        h2.markdown("**Producto**")
        h3.markdown("**Medida**")
        h4.markdown("**Precio (€)**")

        first = True
        for row in sorted(group_rows, key=lambda x: x["name"]):
            c1, c2, c3, c4 = st.columns([1.2, 2.5, 1.5, 1])
            c1.write(group if first else "")
            c2.write(row["name"])
            c3.write(row["size"] or "—")
            c4.write(
                f"€ {row['ws_price']:.2f}" if row["ws_price"] else "—"
            )
            first = False


# =============================================================================
# PDF generation
# =============================================================================

def _generate_pdf(
    rows: list[dict],
    settings: dict,
    title: str,
    cat_date: str,
    client_name: str | None,
    include_cond: bool,
    cond_allergen: str,
    cond_avail: str,
    cond_returns: str,
) -> bytes:
    """Generate the catalogue PDF using ReportLab."""
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, Image, HRFlowable
    )
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # ── Fonts ──────────────────────────────────────────────────────────────────
    font_regular = os.path.join(DATA_DIR, "EBGaramond-Regular.ttf")
    font_bold    = os.path.join(DATA_DIR, "EBGaramond-Bold.ttf")

    if os.path.exists(font_regular) and os.path.exists(font_bold):
        pdfmetrics.registerFont(TTFont("Garamond",     font_regular))
        pdfmetrics.registerFont(TTFont("Garamond-Bold", font_bold))
        body_font  = "Garamond"
        bold_font  = "Garamond-Bold"
    else:
        body_font  = "Times-Roman"
        bold_font  = "Times-Bold"

    # ── Colours ────────────────────────────────────────────────────────────────
    bg_color     = colors.HexColor("#F2EEE8")
    dark_color   = colors.HexColor("#1a1a1a")
    header_color = colors.HexColor("#9ca3af")
    row_alt      = colors.HexColor("#ebe6de")

    # ── Document ───────────────────────────────────────────────────────────────
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2*cm, bottomMargin=2.5*cm,
    )

    styles = getSampleStyleSheet()

    def style(name, font=body_font, size=10, leading=14,
              alignment=0, color=dark_color, space_before=0, space_after=4):
        return ParagraphStyle(
            name, fontName=font, fontSize=size,
            leading=leading, alignment=alignment,
            textColor=color, spaceAfter=space_after,
            spaceBefore=space_before,
        )

    title_style    = style("Title",    font=bold_font,  size=18, leading=22, alignment=1, space_before=6, space_after=4)
    subtitle_style = style("Subtitle", font=body_font,  size=11, leading=14, alignment=1, color=colors.HexColor("#6b7280"), space_after=2)
    date_style     = style("Date",     font=body_font,  size=9,  leading=12, alignment=1, color=colors.HexColor("#6b7280"), space_after=12)
    section_style  = style("Section",  font=bold_font,  size=11, leading=14, space_before=10, space_after=4)
    body_style     = style("Body",     font=body_font,  size=9,  leading=13)
    cond_h_style   = style("CondH",    font=bold_font,  size=9,  leading=13, space_before=6)
    cond_style     = style("Cond",     font=body_font,  size=8,  leading=12, color=colors.HexColor("#374151"))
    footer_style   = style("Footer",   font=body_font,  size=8,  leading=10, alignment=1, color=colors.HexColor("#9ca3af"))

    story = []

    # ── Logo ───────────────────────────────────────────────────────────────────
    logo_path = os.path.join(DATA_DIR, "Logo.png")
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=8*cm, height=3*cm, kind='proportional')
        logo.hAlign = 'CENTER'
        story.append(logo)
        story.append(Spacer(1, 0.3*cm))

    # ── Title block ────────────────────────────────────────────────────────────
    story.append(Paragraph(title, title_style))
    story.append(Paragraph("Millington Cakes", subtitle_style))
    if client_name:
        story.append(Paragraph(f"Preparado para: {client_name}", subtitle_style))
    story.append(Paragraph(cat_date, date_style))
    story.append(HRFlowable(width="100%", thickness=0.5,
                             color=colors.HexColor("#d1c9be")))
    story.append(Spacer(1, 0.4*cm))

    # ── Price table ────────────────────────────────────────────────────────────
    groups_order = ["Tarta", "Tarta Individual", "Bocados", "Otros"]
    by_group: dict[str, list] = {}
    for r in rows:
        by_group.setdefault(r["group"], []).append(r)

    # Build table data
    col_widths = [3.5*cm, 6.5*cm, 4*cm, 2.5*cm]
    table_data = [[
        Paragraph("<b>Tamaño</b>",   ParagraphStyle("th", fontName=bold_font, fontSize=9, textColor=colors.white)),
        Paragraph("<b>Producto</b>", ParagraphStyle("th", fontName=bold_font, fontSize=9, textColor=colors.white)),
        Paragraph("<b>Medida</b>",   ParagraphStyle("th", fontName=bold_font, fontSize=9, textColor=colors.white)),
        Paragraph("<b>Precio (€)</b>",ParagraphStyle("th", fontName=bold_font, fontSize=9, textColor=colors.white, alignment=2)),
    ]]

    row_styles   = []
    data_row_idx = 1  # header is row 0
    group_start: dict[str, int] = {}

    for group in groups_order:
        group_rows = by_group.get(group, [])
        if not group_rows:
            continue

        group_start[group] = data_row_idx
        sorted_rows = sorted(group_rows, key=lambda x: x["name"])

        for i, row in enumerate(sorted_rows):
            group_label = group if i == 0 else ""
            price_str   = f"{row['ws_price']:.2f}" if row["ws_price"] else "—"

            table_data.append([
                Paragraph(group_label, ParagraphStyle("cell", fontName=bold_font if i==0 else body_font, fontSize=9, leading=12)),
                Paragraph(row["name"], ParagraphStyle("cell", fontName=body_font, fontSize=9, leading=12)),
                Paragraph(row["size"] or "—", ParagraphStyle("cell", fontName=body_font, fontSize=8, leading=12, textColor=colors.HexColor("#6b7280"))),
                Paragraph(price_str, ParagraphStyle("cell", fontName=bold_font, fontSize=9, leading=12, alignment=2)),
            ])
            # Alternate row shading
            if data_row_idx % 2 == 0:
                row_styles.append(
                    ("BACKGROUND", (0, data_row_idx), (-1, data_row_idx), row_alt)
                )
            data_row_idx += 1

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        # Header
        ("BACKGROUND",  (0, 0), (-1, 0), header_color),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), bold_font),
        ("FONTSIZE",    (0, 0), (-1, 0), 9),
        ("TOPPADDING",  (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        # Grid
        ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#d1c9be")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, row_alt]),
        # Alignment
        ("ALIGN",       (-1, 0), (-1, -1), "RIGHT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        *row_styles,
    ]))

    story.append(table)

    # ── Conditions ─────────────────────────────────────────────────────────────
    if include_cond:
        story.append(Spacer(1, 0.6*cm))
        story.append(HRFlowable(width="100%", thickness=0.5,
                                color=colors.HexColor("#d1c9be")))
        story.append(Paragraph("Condiciones de Pedido", section_style))

        s   = settings
        min_u   = int(s.get("cond_min_order_units") or 50)
        min_v   = float(s.get("cond_min_order_value") or 150)
        del_c   = float(s.get("cond_delivery_charge") or 25)
        del_t   = float(s.get("cond_delivery_threshold") or 400)
        lead    = int(s.get("cond_lead_time_days") or 3)
        pay     = int(s.get("cond_payment_days") or 15)
        cancel  = int(s.get("cond_cancellation_hours") or 48)
        review  = int(s.get("cond_price_review_months") or 6)
        var_pct = float(s.get("cond_price_variation_pct") or 5)
        notice  = int(s.get("cond_price_notice_days") or 30)

        conditions = [
            ("Pedido mínimo",
             f"El pedido mínimo es de {min_u} unidades o un valor total de {min_v:.0f} euros."),
            ("Entrega",
             f"Se realizará un cargo adicional de {del_c:.0f} euros para entregas si el valor total del pedido es inferior a {del_t:.0f} euros."),
            ("Entrega refrigerada",
             "Todos los productos serán entregados en vehículos refrigerados para garantizar la frescura y calidad."),
            ("Plazos",
             f"Para asegurar la mejor calidad y servicio, les pedimos que realicen sus pedidos con un mínimo de {lead} días de antelación a la fecha de entrega prevista."),
            ("Facturación",
             f"La factura será emitida en el momento de la entrega y el pago deberá realizarse mediante transferencia bancaria en un plazo de {pay} días."),
            ("Política de cancelación",
             f"Las cancelaciones deberán ser notificadas con al menos {cancel} horas de antelación. En caso contrario, se podrá aplicar un cargo por cancelación."),
            ("Modificación de pedidos",
             f"Las modificaciones en los pedidos deberán realizarse con un mínimo de {cancel} horas de antelación a la fecha de entrega."),
            ("Revisión de precios",
             f"Los precios indicados en este documento estarán sujetos a revisión cada {review} meses bajo condiciones normales de mercado."),
            ("Protección de precios",
             f"En caso de variación en el coste de materias primas superior al {var_pct:.0f}%, Millington Cakes se reserva el derecho a ajustar los precios con un preaviso de {notice} días."),
        ]

        if cond_allergen:
            conditions.append(("Información sobre alérgenos", cond_allergen))
        if cond_avail:
            conditions.append(("Disponibilidad de productos", cond_avail))
        if cond_returns:
            conditions.append(("Política de devoluciones", cond_returns))

        for heading, text in conditions:
            story.append(Paragraph(f"<b>{heading}:</b> {text}", cond_style))
            story.append(Spacer(1, 0.15*cm))

    # ── Footer ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width="100%", thickness=0.5,
                            color=colors.HexColor("#d1c9be")))
    story.append(Paragraph(
        "Calle de la Granja 100, Nave 5-6, 28108 Alcobendas, Madrid  ·  "
        "637 773 669  ·  www.millingtons.es",
        footer_style
    ))

    # ── Build ──────────────────────────────────────────────────────────────────
    def on_page(canvas, doc):
        """Draw background colour on every page."""
        canvas.saveState()
        canvas.setFillColor(bg_color)
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()
