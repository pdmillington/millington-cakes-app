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

FORMAT_DISPLAY = {
    "standard":   "Tarta estándar",
    "individual": "Individual",
    "bocado":     "Bocado",
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
        include_fichas = st.checkbox(
            "Incluir fichas técnicas", value=True,
            key="cat_fichas",
            help="Si no se incluyen, se añade una nota indicando que "
                 "las fichas están disponibles bajo petición."
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

    # ── Ficha warnings ───────────────────────────────────────────────────────────
    # Check which selected products have approved label text
    unapproved = []
    for row in resolved_rows:
        vid = row.get("variant_id")
        if vid:
            v = var_lookup.get(row["recipe_id"], {}).get(row["fmt_key"], {})
            if not v.get("label_approved"):
                unapproved.append(f"{row['name']} ({FORMAT_DISPLAY.get(row['fmt_key'], row['fmt_key'])})")

    if unapproved:
        st.warning(
            f"⚠️ {len(unapproved)} ficha(s) no aprobada(s) — "
            f"se generarán con marca de borrador: "
            f"{', '.join(unapproved)}"
        )

    # ── Generate PDF ──────────────────────────────────────────────────────────
    if st.button("📄 Generar catálogo + fichas", type="primary"):
        with st.spinner("Generando catálogo y fichas…"):
            try:
                # Fetch full variant data for ficha generation
                all_v = db.get_all_variants_full()
                full_var_lookup: dict[str, dict[str, dict]] = {}
                for v in all_v:
                    full_var_lookup.setdefault(v["recipe_id"], {})[v["format"]] = v

                pdf_bytes = _generate_pdf(
                    rows           = resolved_rows,
                    settings       = settings,
                    title          = catalogue_title,
                    cat_date       = catalogue_date,
                    client_name    = client_name.strip() or None,
                    include_cond   = include_conditions,
                    include_fichas = include_fichas,
                    cond_allergen  = custom_allergen,
                    cond_avail     = custom_availability,
                    cond_returns   = custom_returns,
                    var_lookup    = full_var_lookup,
                )
                fname = (
                    f"millington_catalogo"
                    + (f"_{client_name.strip().replace(' ','_')}"
                       if client_name.strip() else "")
                    + f"_{date.today().isoformat()}.pdf"
                )
                st.download_button(
                    "⬇️ Descargar catálogo PDF",
                    data=pdf_bytes,
                    file_name=fname,
                    mime="application/pdf",
                    type="primary"
                )
                n_fichas = len(resolved_rows)
                st.success(
                    f"PDF generado — tabla de precios + {n_fichas} ficha(s)",
                    icon="✅"
                )
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
    include_fichas: bool = True,
    var_lookup: dict = None,
) -> bytes:
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, Image, HRFlowable, PageBreak
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
        topMargin=3.5*cm,  bottomMargin=2.2*cm,
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
    logo_path = os.path.join(DATA_DIR, "Logo.png")

    # ══════════════════════════════════════════════════════════════════════════
    # COVER PAGE
    # ══════════════════════════════════════════════════════════════════════════
    # Push the block roughly to vertical centre of the page
    story.append(Spacer(1, 6*cm))

    if os.path.exists(logo_path):
        cover_logo = Image(logo_path, width=10*cm, height=4*cm, kind='proportional')
        cover_logo.hAlign = 'CENTER'
        story.append(cover_logo)
        story.append(Spacer(1, 1.2*cm))

    story.append(Paragraph(
        title,
        ps("T", font=bold_font, size=22, leading=28, align=1, sb=6, sa=6)
    ))
    story.append(Paragraph(
        "Millington Cakes",
        ps("S", size=13, align=1, color=colors.HexColor("#6b7280"), sa=4)
    ))
    if client_name:
        story.append(Paragraph(
            f"Preparado para: {client_name}",
            ps("C", size=11, align=1, color=colors.HexColor("#6b7280"), sa=4)
        ))
    story.append(Spacer(1, 0.4*cm))
    story.append(HRFlowable(
        width="40%", thickness=0.5,
        color=colors.HexColor("#d1c9be"), hAlign='CENTER'
    ))
    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph(
        cat_date,
        ps("D", size=11, align=1, color=colors.HexColor("#9ca3af"), sa=0)
    ))

    # End of cover — subsequent content starts on page 2
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 2+  —  price table
    # ══════════════════════════════════════════════════════════════════════════

    # ── Price table ────────────────────────────────────────────────────────────
    groups_order = ["Tarta", "Tarta Individual", "Bocados", "Otros"]
    by_group: dict[str, list] = {}
    for r in rows:
        by_group.setdefault(r["group"], []).append(r)

    col_widths = [3.5*cm, 6.5*cm, 4*cm, 2.5*cm]

    th_style = ParagraphStyle(
        "th", fontName=bold_font, fontSize=9, textColor=colors.white
    )
    th_r = ParagraphStyle(
        "thr", fontName=bold_font, fontSize=9,
        textColor=colors.white, alignment=2
    )

    table_data = [[
        Paragraph("<b>Tamaño</b>",    th_style),
        Paragraph("<b>Producto</b>",  th_style),
        Paragraph("<b>Medida</b>",    th_style),
        Paragraph("<b>Precio (€)</b>", th_r),
    ]]

    cell_style = ps("cell", size=9, leading=12)
    size_style = ps("sz", size=8, leading=12,
                    color=colors.HexColor("#6b7280"))

    extra_styles = []
    data_idx = 1

    for group in groups_order:
        group_rows = by_group.get(group, [])
        if not group_rows:
            continue

        for i, row in enumerate(sorted(group_rows, key=lambda x: x["name"])):
            group_label = group if i == 0 else ""
            price_val   = row.get("ws_price")
            price_str   = f"{price_val:.2f}" if price_val else "—"
            overridden  = row.get("_overridden", False)

            price_color = override_col if overridden else dark_color
            price_ps    = ps(f"p{data_idx}", font=bold_font, size=9,
                             leading=12, align=2, color=price_color)

            table_data.append([
                Paragraph(group_label, ps(f"g{data_idx}",
                                          font=bold_font if i == 0 else body_font,
                                          size=9, leading=12)),
                Paragraph(row["name"], cell_style),
                Paragraph(row["size"] or "—", size_style),
                Paragraph(price_str, price_ps),
            ])

            if data_idx % 2 == 0:
                extra_styles.append(
                    ("BACKGROUND", (0, data_idx), (-1, data_idx), row_alt)
                )
            data_idx += 1

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), header_color),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("GRID",          (0, 0), (-1, -1), 0.25,
         colors.HexColor("#d1c9be")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1),
         [colors.white, row_alt]),
        ("ALIGN",         (-1, 0), (-1, -1), "RIGHT"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        *extra_styles,
    ]))
    story.append(table)

    # ── Client override note ───────────────────────────────────────────────────
    if client_name and any(r.get("_overridden") for r in rows):
        story.append(Spacer(1, 0.2*cm))
        story.append(Paragraph(
            "* Precios en azul son específicos para este cliente.",
            ps("note", size=7, color=override_col, sa=0)
        ))

    # ── Conditions ─────────────────────────────────────────────────────────────
    if include_cond:
        story.append(Spacer(1, 0.6*cm))
        story.append(HRFlowable(
            width="100%", thickness=0.5,
            color=colors.HexColor("#d1c9be")
        ))
        story.append(Paragraph(
            "Condiciones de Pedido",
            ps("sec", font=bold_font, size=11, leading=14, sb=10, sa=4)
        ))

        s       = settings
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

        cond_ps = ps("cond", size=8, leading=12,
                     color=colors.HexColor("#374151"), sa=3)

        conditions = [
            ("Pedido mínimo",
             f"El pedido mínimo es de {min_u} unidades o un valor total "
             f"de {min_v:.0f} euros."),
            ("Entrega",
             f"Se realizará un cargo adicional de {del_c:.0f} euros para "
             f"entregas si el valor total del pedido es inferior a "
             f"{del_t:.0f} euros."),
            ("Entrega refrigerada",
             "Todos los productos serán entregados en vehículos refrigerados "
             "para garantizar la frescura y calidad."),
            ("Plazos",
             f"Para asegurar la mejor calidad y servicio, les pedimos que "
             f"realicen sus pedidos con un mínimo de {lead} días de "
             f"antelación a la fecha de entrega prevista."),
            ("Facturación",
             f"La factura será emitida en el momento de la entrega y el "
             f"pago deberá realizarse mediante transferencia bancaria en "
             f"un plazo de {pay} días."),
            ("Política de cancelación",
             f"Las cancelaciones deberán ser notificadas con al menos "
             f"{cancel} horas de antelación. En caso contrario, se podrá "
             f"aplicar un cargo por cancelación."),
            ("Modificación de pedidos",
             f"Las modificaciones deberán realizarse con un mínimo de "
             f"{cancel} horas de antelación a la fecha de entrega."),
            ("Revisión de precios",
             f"Los precios estarán sujetos a revisión cada {review} meses "
             f"bajo condiciones normales de mercado."),
            ("Protección de precios",
             f"En caso de variación en el coste de materias primas superior "
             f"al {var_pct:.0f}%, Millington Cakes se reserva el derecho a "
             f"ajustar los precios con un preaviso de {notice} días."),
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

    # ── Fichas ─────────────────────────────────────────────────────────────────
    if not include_fichas:
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph(
            "<i>Las fichas técnicas de todos los productos están disponibles "
            "bajo petición.</i>",
            ps("fon", size=8, leading=11,
               color=colors.HexColor("#6b7280"), sa=0)
        ))
    elif var_lookup and rows:
        for row in rows:
            rid     = row["recipe_id"]
            fmt_key = row["fmt_key"]
            variant = (var_lookup.get(rid) or {}).get(fmt_key, {})
            if not variant:
                continue

            # Build ficha title with size and format
            fmt_label = {"standard": "Tarta estándar",
                         "individual": "Individual",
                         "bocado": "Bocado"}.get(fmt_key, "")
            size_desc = variant.get("size_description") or ""
            if fmt_key == "standard" and size_desc:
                ficha_title = f"{row['name']} — {size_desc}"
            elif fmt_key != "standard":
                ficha_title = f"{row['name']} — {fmt_label}"
                if size_desc:
                    ficha_title += f" ({size_desc})"
            else:
                ficha_title = row["name"]

            story.append(PageBreak())
            _add_ficha_page(
                story        = story,
                recipe_name  = ficha_title,
                variant      = variant,
                ps           = ps,
                body_font    = body_font,
                bold_font    = bold_font,
                rule         = colors.HexColor("#d1c9be"),
                grey         = colors.HexColor("#6b7280"),
                border_col   = colors.HexColor("#9ca3af"),
            )

    # ── Page callbacks ─────────────────────────────────────────────────────────
    address_line = (
        "Calle de la Granja 100, Nave 5-6, 28108 Alcobendas, Madrid  ·  "
        "637 773 669  ·  www.millingtons.es"
    )

    def on_cover(canvas, doc):
        """First page: background only — story supplies all cover content."""
        canvas.saveState()
        canvas.setFillColor(bg_color)
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        canvas.restoreState()

    def on_later_pages(canvas, doc):
        """Page 2+: background + logo header + address footer."""
        canvas.saveState()

        # Background
        canvas.setFillColor(bg_color)
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)

        # ── Header: logo ───────────────────────────────────────────────────
        if os.path.exists(logo_path):
            logo_h = 1.4 * cm
            logo_w = 5 * cm          # max width; aspect ratio preserved
            canvas.drawImage(
                logo_path,
                (A4[0] - logo_w) / 2,  # centred horizontally
                A4[1] - 2.4 * cm,      # top of logo
                width=logo_w,
                height=logo_h,
                preserveAspectRatio=True,
                mask='auto',
            )

        # Thin rule under header
        rule_y = A4[1] - 2.7 * cm
        canvas.setStrokeColor(colors.HexColor("#d1c9be"))
        canvas.setLineWidth(0.5)
        canvas.line(2.5 * cm, rule_y, A4[0] - 2.5 * cm, rule_y)

        # ── Footer: address ────────────────────────────────────────────────
        footer_y = 1.3 * cm
        canvas.setStrokeColor(colors.HexColor("#d1c9be"))
        canvas.line(2.5 * cm, footer_y + 0.45 * cm,
                    A4[0] - 2.5 * cm, footer_y + 0.45 * cm)
        canvas.setFont(body_font, 7.5)
        canvas.setFillColor(colors.HexColor("#9ca3af"))
        canvas.drawCentredString(A4[0] / 2, footer_y, address_line)

        canvas.restoreState()

    doc.build(story, onFirstPage=on_cover, onLaterPages=on_later_pages)
    return buffer.getvalue()


# =============================================================================
# Ficha page generator
# =============================================================================

def _add_ficha_page(
    story: list,
    recipe_name: str,
    variant: dict,
    ps,
    body_font: str,
    bold_font: str,
    rule,
    grey,
    border_col,
):
    """
    Add one ficha page to the story.
    Reads all data from the variant dict.
    Fetches allergen declaration and ingredient label from DB.
    Marks as BORRADOR if label_approved is False.
    """
    from reportlab.platypus import Table, TableStyle, Spacer, HRFlowable
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    recipe_id    = variant.get("recipe_id")
    is_approved  = bool(variant.get("label_approved"))
    size_desc    = variant.get("size_description") or "—"
    weight_g = variant.get("ref_weight_g")

    # Fallback: estimate weight from recipe lines, rounded to nearest 50g
    if not weight_g and recipe_id:
        try:
            lines    = db.get_recipe_lines(recipe_id)
            result   = db.estimate_recipe_weight(lines)
            est      = result.get("weight_g") or None
            if est:
                weight_g = round(est / 50) * 50  # round to nearest 50g
        except Exception:
            weight_g = None

    weight_str = f"{int(weight_g)} g" if weight_g else "—"
    description  = variant.get("description_es") or ""
    packaging    = variant.get("packaging_desc") or "Caja de cartón"
    storage      = variant.get("storage_instructions") or "Refrigerada entre 0 - 5°C"
    shelf_life   = int(variant.get("shelf_life_hours") or 24)
    label_text   = variant.get("ingredient_label_es") or ""

    # Fetch allergen declaration
    try:
        declaration = db.get_allergen_declaration(recipe_id)
    except Exception:
        declaration = {"contiene": [], "puede_contener": [], "warnings": []}

    # If no stored label text, generate from recipe
    if not label_text and recipe_id:
        try:
            label_data = db.get_ingredient_label_text(recipe_id)
            label_text = label_data.get("label_text") or ""
        except Exception:
            label_text = ""

    contiene = (
        ", ".join(a.capitalize() for a in declaration.get("contiene", []))
        or "Ninguno detectado"
    )
    puede = (
        ", ".join(a.capitalize() for a in declaration.get("puede_contener", []))
        or "Ninguno"
    )

    white = colors.white
    box_bg = white

    # Company header box
    story.append(_ficha_box(
        title="Empresa",
        content=[
            f"<b>Millington Cakes</b>",
            "<b>CIF: B13998596</b>",
            "<i>Calle de la Granja 100, Nave 5-6, 28108 Alcobendas, Madrid</i>",
        ],
        title_bg=border_col,
        box_bg=box_bg,
        body_font=body_font,
        bold_font=bold_font,
        border_col=border_col,
        is_header=True,
    ))
    story.append(Spacer(1, 0.25*cm))

    # Draft watermark note if not approved
    draft_note = ""
    if not is_approved:
        draft_note = " ⚠️ BORRADOR — pendiente de aprobación"

    # Ficha content
    lines = [
        f"<b>Tamaño:</b> {size_desc}",
        f"<b>Peso aprox:</b> {weight_str}",
    ]
    if description:
        lines.append(f"<b>Descripción:</b> {description}")

    lines += [
        f"<b>Ingredientes:</b> {label_text}" if label_text
            else "<b>Ingredientes:</b> Ver ingredientes en base de datos.",
        "<b>Declaración de alérgenos</b>",
        f"<b>Contiene:</b> {contiene}.",
        f"<b>Puede contener:</b> {puede}.",
        f"<b>Embalaje:</b> {packaging}",
        f"<b>Conservación:</b> {storage}",
        f"<b>Vida útil:</b> {shelf_life} horas",
    ]

    story.append(_ficha_box(
        title=recipe_name + draft_note,
        content=lines,
        title_bg=border_col,
        box_bg=box_bg,
        body_font=body_font,
        bold_font=bold_font,
        border_col=border_col,
    ))

    story.append(Spacer(1, 0.5*cm))


def _ficha_box(
    title: str,
    content: list,
    title_bg,
    box_bg,
    body_font: str,
    bold_font: str,
    border_col,
    is_header: bool = False,
) -> object:
    """
    Render a tcolorbox-style bordered box matching the LaTeX ficha layout.
    content is a list of HTML strings rendered as Paragraphs.
    """
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    white = colors.white

    title_ps = ParagraphStyle(
        f"bt_{title[:8]}", fontName=bold_font, fontSize=10,
        leading=13, textColor=white
    )
    body_ps = ParagraphStyle(
        f"bc_{title[:8]}", fontName=body_font, fontSize=9,
        leading=13, textColor=colors.HexColor("#1a1a1a"),
        spaceAfter=2
    )
    body_bold_ps = ParagraphStyle(
        f"bb_{title[:8]}", fontName=bold_font, fontSize=9,
        leading=13, textColor=colors.HexColor("#1a1a1a"),
        spaceAfter=6
    )

    # Title row
    title_data  = [[Paragraph(title, title_ps)]]
    title_table = Table(title_data, colWidths=[15.5*cm])
    title_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), title_bg),
        ("LEFTPADDING",   (0, 0), (-1, -1), 7),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    # Content rows
    content_rows = []
    for line in content:
        content_rows.append(
            [Paragraph(line, body_ps)]
        )

    if content_rows:
        content_table = Table(content_rows, colWidths=[15.5*cm])
        content_table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), box_bg),
            ("LEFTPADDING",   (0, 0), (-1, -1), 7),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        outer_data = [[title_table], [content_table]]
    else:
        outer_data = [[title_table]]

    outer = Table(outer_data, colWidths=[15.5*cm])
    outer.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 0.75, border_col),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return outer
