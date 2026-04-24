# screen_catalogue.py
# =============================================================================
# Wholesale catalogue generator.
#
# Prices shown and printed are APPROVED prices (ws_price_approved).
# Working/draft prices are NOT used here — only approved prices go into
# the catalogue. This ensures the catalogue always reflects a deliberate
# pricing decision.
#
# If a client name is entered and that client has specific prices in the
# client_prices table, those override the standard approved prices for
# that client's catalogue only.
#
# PDF uses EB Garamond (data/EBGaramond-Regular.ttf + Bold) and
# the Millington logo (data/Logo.png).
# Background: #F2EEE8 (warm off-white).
# =============================================================================

import streamlit as st
import millington_db as db
import os
import io
from datetime import date


FORMAT_GROUPS = [
    ("Tarta",            "standard"),
    ("Tarta Individual", "individual"),
    ("Bocados",          "bocado"),
]

OTROS_RECIPES = {
    "Cookies", "Brownie", "Blondie", "Brioche - canela",
    "Brioche - chocolate", "Scones", "Scone con pasas",
    "Trufas chocolate negro",
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def screen_catalogue():
    st.title("Catálogo mayorista")
    st.caption(
        "Los precios del catálogo son los precios aprobados. "
        "Para actualizar precios en el catálogo, apruébalos primero en la pantalla Prices."
    )

    # ── Load data ─────────────────────────────────────────────────────────────
    recipes      = db.get_recipes()
    all_variants = db.get_all_variants_full()
    settings     = db.get_settings()

    recipe_by_id = {r["id"]: r for r in recipes}

    # Build variant lookup: {recipe_id: {format: variant}}
    var_lookup: dict[str, dict[str, dict]] = {}
    for v in all_variants:
        var_lookup.setdefault(v["recipe_id"], {})[v["format"]] = v

    # ── Build product list using APPROVED prices ───────────────────────────────
    # Each row stores the variant_id so we can apply client overrides later.
    catalogue_rows: dict[str, list[dict]] = {
        "Tarta": [], "Tarta Individual": [],
        "Bocados": [], "Otros": [],
    }

    for recipe in sorted(recipes, key=lambda r: r["name"]):
        rid      = recipe["id"]
        name     = recipe["name"]
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

            variant    = var_lookup.get(rid, {}).get(fmt_key, {})
            variant_id = variant.get("id")

            # Use APPROVED price — not working price
            approved_price = (
                float(variant.get("ws_price_approved") or 0) or None
            )
            approved_date = (
                str(variant.get("ws_price_approved_at") or "")[:10] or None
            )
            size_desc  = variant.get("size_description") or ""
            group      = "Otros" if is_otros and fmt_key == "standard" \
                else fmt_label

            catalogue_rows[group].append({
                "variant_id":     variant_id,
                "recipe_id":      rid,
                "fmt_key":        fmt_key,
                "name":           name,
                "size":           size_desc,
                "ws_price":       approved_price,   # approved price
                "approved_date":  approved_date,
                "group":          group,
            })

    # ── Product selector ──────────────────────────────────────────────────────
    st.markdown("### Selección de productos")
    st.caption(
        "Sólo se muestran precios aprobados. "
        "Los productos sin precio aprobado aparecen como 'Sin precio'."
    )

    selected_rows: list[dict] = []

    for group_name, group_rows in catalogue_rows.items():
        if not group_rows:
            continue
        st.markdown(f"**{group_name}**")
        cols = st.columns(2)
        for i, row in enumerate(group_rows):
            price_str = (
                f"€ {row['ws_price']:.2f}"
                if row["ws_price"]
                else "⚠️ Sin precio aprobado"
            )
            label = f"{row['name']}  ·  {row['size']}  ·  {price_str}"
            checked = cols[i % 2].checkbox(
                label,
                value=bool(row["ws_price"]),
                key=f"cat_sel_{row['recipe_id']}_{row['fmt_key']}"
            )
            if checked:
                selected_rows.append(row)

    st.divider()

    if not selected_rows:
        st.info("Selecciona al menos un producto para continuar.")
        return

    # ── Catalogue options ──────────────────────────────────────────────────────
    st.markdown("### Opciones del catálogo")

    col_a, col_b = st.columns(2)
    with col_a:
        catalogue_title = st.text_input(
            "Título del catálogo",
            value="Catálogo para Catering y Hostelería",
            key="cat_title"
        )
        catalogue_date = st.text_input(
            "Fecha",
            value=date.today().strftime("%B %Y").capitalize(),
            key="cat_date"
        )
    with col_b:
        client_name = st.text_input(
            "Cliente (opcional)",
            placeholder="e.g. Restaurante La Paloma",
            key="cat_client",
            help="If this client has specific prices in the Prices screen, "
                 "those will override the standard approved prices."
        )
        include_conditions = st.checkbox(
            "Incluir condiciones de pedido", value=True,
            key="cat_conditions"
        )

    # ── Resolve final prices ───────────────────────────────────────────────────
    # If client_name is set, fetch their overrides and apply.
    # This happens AFTER the product selection so the client name is known.
    if client_name.strip():
        client_overrides = db.get_client_prices_for_catalogue(
            client_name.strip()
        )
        if client_overrides:
            st.info(
                f"Found {len(client_overrides)} client-specific price(s) "
                f"for '{client_name}' — these will override standard prices."
            )
            # Apply overrides to selected rows (mutate a copy)
            resolved_rows = []
            for row in selected_rows:
                row_copy = dict(row)
                override = client_overrides.get(row["variant_id"], {})
                if override.get("ws_price_ex_vat"):
                    row_copy["ws_price"] = float(override["ws_price_ex_vat"])
                    row_copy["_overridden"] = True
                else:
                    row_copy["_overridden"] = False
                resolved_rows.append(row_copy)
        else:
            resolved_rows = selected_rows
            if client_name.strip():
                st.caption(
                    f"No client-specific prices found for '{client_name}' — "
                    "using standard approved prices."
                )
    else:
        resolved_rows = selected_rows

    # ── Conditions ────────────────────────────────────────────────────────────
    if include_conditions:
        with st.expander("✏️ Editar condiciones para este catálogo"):
            st.caption(
                "Los valores por defecto vienen de Settings. "
                "Edita aquí para condiciones especiales de este cliente."
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

    # ── Preview ───────────────────────────────────────────────────────────────
    st.markdown("### Vista previa")
    with st.expander("Ver tabla de precios", expanded=True):
        _render_preview_table(resolved_rows, client_name.strip())

    st.divider()

    # ── Generate PDF ──────────────────────────────────────────────────────────
    if st.button("📄 Generar PDF", type="primary"):
        with st.spinner("Generando catálogo…"):
            try:
                pdf_bytes = _generate_pdf(
                    rows          = resolved_rows,
                    settings      = settings,
                    title         = catalogue_title,
                    cat_date      = catalogue_date,
                    client_name   = client_name.strip() or None,
                    include_cond  = include_conditions,
                    cond_allergen = custom_allergen,
                    cond_avail    = custom_availability,
                    cond_returns  = custom_returns,
                )
                fname = (
                    f"millington_catalogo"
                    f"{'_' + client_name.strip().replace(' ','_') if client_name.strip() else ''}"
                    f"_{date.today().isoformat()}.pdf"
                )
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
# Preview
# =============================================================================

def _render_preview_table(rows: list[dict], client_name: str = ""):
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

            if row.get("ws_price"):
                price_text = f"€ {row['ws_price']:.2f}"
                if row.get("_overridden"):
                    price_text += " ★"  # flag client override
            else:
                price_text = "—"
            c4.write(price_text)
            first = False

    if client_name:
        st.caption("★ = client-specific price override")


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
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, Image, HRFlowable
    )
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # ── Fonts ──────────────────────────────────────────────────────────────────
    font_regular = os.path.join(DATA_DIR, "EBGaramond-Regular.ttf")
    font_bold    = os.path.join(DATA_DIR, "EBGaramond-Bold.ttf")

    if os.path.exists(font_regular) and os.path.exists(font_bold):
        pdfmetrics.registerFont(TTFont("Garamond",      font_regular))
        pdfmetrics.registerFont(TTFont("Garamond-Bold", font_bold))
        body_font = "Garamond"
        bold_font = "Garamond-Bold"
    else:
        body_font = "Times-Roman"
        bold_font = "Times-Bold"

    # ── Colours ────────────────────────────────────────────────────────────────
    bg_color     = colors.HexColor("#F2EEE8")
    dark_color   = colors.HexColor("#1a1a1a")
    header_color = colors.HexColor("#9ca3af")
    row_alt      = colors.HexColor("#ebe6de")
    override_col = colors.HexColor("#1d4ed8")  # blue for client overrides

    # ── Document ───────────────────────────────────────────────────────────────
    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2*cm,    bottomMargin=2.5*cm,
    )

    def ps(name, font=None, size=10, leading=14, align=0,
           color=None, sb=0, sa=4):
        return ParagraphStyle(
            name,
            fontName=font or body_font,
            fontSize=size, leading=leading,
            alignment=align,
            textColor=color or dark_color,
            spaceBefore=sb, spaceAfter=sa,
        )

    story = []

    # ── Logo ───────────────────────────────────────────────────────────────────
    logo_path = os.path.join(DATA_DIR, "Logo.png")
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=8*cm, height=3*cm, kind='proportional')
        logo.hAlign = 'CENTER'
        story.append(logo)
        story.append(Spacer(1, 0.3*cm))

    # ── Title block ────────────────────────────────────────────────────────────
    story.append(Paragraph(
        title,
        ps("T", font=bold_font, size=18, leading=22, align=1, sb=6, sa=4)
    ))
    story.append(Paragraph(
        "Millington Cakes",
        ps("S", size=11, align=1, color=colors.HexColor("#6b7280"), sa=2)
    ))
    if client_name:
        story.append(Paragraph(
            f"Preparado para: {client_name}",
            ps("C", size=10, align=1, color=colors.HexColor("#6b7280"), sa=2)
        ))
    story.append(Paragraph(
        cat_date,
        ps("D", size=9, align=1, color=colors.HexColor("#6b7280"), sa=12)
    ))
    story.append(HRFlowable(
        width="100%", thickness=0.5,
        color=colors.HexColor("#d1c9be")
    ))
    story.append(Spacer(1, 0.4*cm))

    # ── Price table — one sub-table per group ────────────────────────────────────
    # Each group is a separate Table so rows stay together and the group header
    # with "Precio (€)" on the right is clearly associated with its products.
    # Conditions are pushed to a new page via PageBreak.

    from reportlab.platypus import PageBreak

    groups_order = ["Tarta", "Tarta Individual", "Bocados", "Otros"]
    by_group: dict[str, list] = {}
    for r in rows:
        by_group.setdefault(r["group"], []).append(r)

    col_w = [9.5*cm, 4*cm, 3*cm]   # Producto, Medida, Precio

    # Paragraph styles
    grp_bg   = colors.HexColor("#6b7280")
    row_alt  = colors.HexColor("#ebe6de")
    rule_col = colors.HexColor("#d1c9be")
    grey     = colors.HexColor("#6b7280")
    ovr_col  = colors.HexColor("#1d4ed8")

    grp_l_ps = ParagraphStyle("gl", fontName=bold_font, fontSize=9,
                               leading=11, textColor=colors.white)
    grp_r_ps = ParagraphStyle("gr", fontName=bold_font, fontSize=8,
                               leading=11, textColor=colors.white, alignment=2)
    prod_ps  = ParagraphStyle("pr", fontName=body_font, fontSize=9, leading=11)
    size_ps  = ParagraphStyle("sz", fontName=body_font, fontSize=8,
                               leading=11, textColor=grey)

    has_overrides = any(r.get("_overridden") for r in rows)

    for group in groups_order:
        group_rows = by_group.get(group, [])
        if not group_rows:
            continue

        tdata  = []
        tstyle = [
            # No outer border
            ("GRID",          (0, 0), (-1, -1), 0,    colors.white),
            ("LINEBELOW",     (0, 0), (-1, -1), 0.25, rule_col),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",         (2, 0), (2, -1),  "RIGHT"),
        ]

        # Group header row: group name left, "Precio (€)" right
        tdata.append([
            Paragraph(group.upper(), grp_l_ps),
            Paragraph("", grp_l_ps),
            Paragraph("Precio (€)", grp_r_ps),
        ])
        tstyle += [
            ("BACKGROUND",    (0, 0), (-1, 0), grp_bg),
            ("TOPPADDING",    (0, 0), (-1, 0), 3),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ]

        # Product rows
        for i, row in enumerate(sorted(group_rows, key=lambda x: x["name"])):
            price_val  = row.get("ws_price")
            overridden = row.get("_overridden", False)
            price_str  = f"{price_val:.2f}" if price_val else "—"
            price_col  = ovr_col if overridden else dark_color

            price_ps = ParagraphStyle(
                f"p{i}", fontName=bold_font, fontSize=9,
                leading=11, alignment=2, textColor=price_col
            )

            tdata.append([
                Paragraph(row["name"], prod_ps),
                Paragraph(row["size"] or "—", size_ps),
                Paragraph(price_str, price_ps),
            ])

            # Alternate shading (skip header row = index 0)
            if i % 2 == 1:
                tstyle.append(
                    ("BACKGROUND", (0, i+1), (-1, i+1), row_alt)
                )

        t = Table(tdata, colWidths=col_w)
        t.setStyle(TableStyle(tstyle))
        story.append(t)
        story.append(Spacer(1, 0.2*cm))

    # Client override footnote
    if has_overrides and client_name:
        story.append(Paragraph(
            "★ Precio específico para este cliente.",
            ps("fn", color=ovr_col, size=7, sa=0)
        ))

    # ── Page break before conditions ───────────────────────────────────────────
    if include_cond:
        story.append(PageBreak())

        story.append(Paragraph(
            "Condiciones de Pedido",
            ps("sec", font=bold_font, size=11, leading=14, sb=8, sa=4)
        ))

        s       = settings
        min_u   = int(s.get("cond_min_order_units")     or 50)
        min_v   = float(s.get("cond_min_order_value")   or 150)
        del_c   = float(s.get("cond_delivery_charge")   or 25)
        del_t   = float(s.get("cond_delivery_threshold")or 400)
        lead    = int(s.get("cond_lead_time_days")       or 3)
        pay     = int(s.get("cond_payment_days")         or 15)
        cancel  = int(s.get("cond_cancellation_hours")   or 48)
        review  = int(s.get("cond_price_review_months")  or 6)
        var_pct = float(s.get("cond_price_variation_pct")or 5)
        notice  = int(s.get("cond_price_notice_days")    or 30)

        cond_ps = ps("cp", size=8, leading=12,
                     color=colors.HexColor("#374151"), sa=2)

        conditions = [
            ("Pedido mínimo",
             f"El pedido mínimo es de {min_u} unidades o un valor total de {min_v:.0f} euros."),
            ("Entrega",
             f"Se realizará un cargo adicional de {del_c:.0f} euros para entregas "
             f"si el valor total del pedido es inferior a {del_t:.0f} euros."),
            ("Entrega refrigerada",
             "Todos los productos serán entregados en vehículos refrigerados "
             "para garantizar la frescura y calidad."),
            ("Plazos",
             f"Para asegurar la mejor calidad y servicio, les pedimos que realicen "
             f"sus pedidos con un mínimo de {lead} días de antelación a la fecha "
             f"de entrega prevista."),
            ("Facturación",
             f"La factura será emitida en el momento de la entrega y el pago deberá "
             f"realizarse mediante transferencia bancaria en un plazo de {pay} días."),
            ("Política de cancelación",
             f"Las cancelaciones deberán ser notificadas con al menos {cancel} horas "
             f"de antelación. En caso contrario, se podrá aplicar un cargo por cancelación."),
            ("Modificación de pedidos",
             f"Las modificaciones deberán realizarse con un mínimo de {cancel} horas "
             f"de antelación a la fecha de entrega."),
            ("Revisión de precios",
             f"Los precios estarán sujetos a revisión cada {review} meses "
             f"bajo condiciones normales de mercado."),
            ("Protección de precios",
             f"En caso de variación en el coste de materias primas superior al "
             f"{var_pct:.0f}%, Millington Cakes se reserva el derecho a ajustar "
             f"los precios con un preaviso de {notice} días."),
        ]
        if cond_allergen:
            conditions.append(("Información sobre alérgenos", cond_allergen))
        if cond_avail:
            conditions.append(("Disponibilidad de productos", cond_avail))
        if cond_returns:
            conditions.append(("Política de devoluciones", cond_returns))

        for heading, text in conditions:
            story.append(Paragraph(
                f"<b>{heading}:</b> {text}", cond_ps
            ))

        # Footer on conditions page
        story.append(Spacer(1, 0.4*cm))
        story.append(HRFlowable(width="100%", thickness=0.5,
                                color=colors.HexColor("#d1c9be")))
        story.append(Paragraph(
            "Calle de la Granja 100, Nave 5-6, 28108 Alcobendas, Madrid  ·  "
            "637 773 669  ·  www.millingtons.es",
            ps("ft", size=8, leading=10, align=1,
               color=colors.HexColor("#9ca3af"), sa=0)
        ))

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(bg_color)
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()
