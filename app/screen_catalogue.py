# screen_catalogue.py
# =============================================================================
# Wholesale catalogue generator.
#
# Uses APPROVED prices only (ws_price_approved).
# Client-specific prices override standard approved prices per client.
#
# PDF layout: Option B — shaded group header rows, three product columns.
# Font: EB Garamond (data/EBGaramond-Regular.ttf + Bold).
# Logo: data/Logo.png
# Background: #F2EEE8
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
        "Para actualizar, aprueba los precios primero en la pantalla Prices."
    )

    recipes      = db.get_recipes()
    all_variants = db.get_all_variants_full()
    settings     = db.get_settings()

    recipe_by_id = {r["id"]: r for r in recipes}
    var_lookup: dict[str, dict[str, dict]] = {}
    for v in all_variants:
        var_lookup.setdefault(v["recipe_id"], {})[v["format"]] = v

    # ── Build product list ────────────────────────────────────────────────────
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

            # Use APPROVED price
            ws_price   = (
                float(variant.get("ws_price_approved") or 0) or None
            )
            size_desc  = variant.get("size_description") or ""
            group      = (
                "Otros" if is_otros and fmt_key == "standard"
                else fmt_label
            )

            catalogue_rows[group].append({
                "variant_id": variant_id,
                "recipe_id":  rid,
                "fmt_key":    fmt_key,
                "name":       name,
                "size":       size_desc,
                "ws_price":   ws_price,
                "group":      group,
            })

    # ── Product selector ──────────────────────────────────────────────────────
    st.markdown("### Selección de productos")
    st.caption(
        "Precios aprobados. ⚠️ Sin precio aprobado = no aparecerá correctamente en el PDF."
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
                if row["ws_price"] else "⚠️ Sin precio"
            )
            label = f"{row['name']}  ·  {row['size']}  ·  {price_str}"
            if cols[i % 2].checkbox(
                label,
                value=bool(row["ws_price"]),
                key=f"cat_sel_{row['recipe_id']}_{row['fmt_key']}"
            ):
                selected_rows.append(row)

    st.divider()

    if not selected_rows:
        st.info("Selecciona al menos un producto para continuar.")
        return

    # ── Catalogue options ─────────────────────────────────────────────────────
    st.markdown("### Opciones del catálogo")

    col_a, col_b = st.columns(2)
    with col_a:
        catalogue_title = st.text_input(
            "Título", value="Catálogo para Catering y Hostelería",
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
            help="Si este cliente tiene precios específicos, se aplicarán automáticamente."
        )
        include_conditions = st.checkbox(
            "Incluir condiciones de pedido", value=True,
            key="cat_conditions"
        )

    # ── Resolve client overrides ──────────────────────────────────────────────
    client_overrides = {}
    if client_name.strip():
        client_overrides = db.get_client_prices_for_catalogue(
            client_name.strip()
        )
        if client_overrides:
            st.info(
                f"Se aplicarán {len(client_overrides)} precio(s) específico(s) "
                f"para '{client_name}'."
            )

    resolved_rows = []
    for row in selected_rows:
        r = dict(row)
        override = client_overrides.get(row.get("variant_id"), {})
        if override.get("ws_price_ex_vat"):
            r["ws_price"]    = float(override["ws_price_ex_vat"])
            r["_overridden"] = True
        else:
            r["_overridden"] = False
        resolved_rows.append(r)

    # ── Conditions ────────────────────────────────────────────────────────────
    if include_conditions:
        with st.expander("✏️ Editar condiciones para este catálogo"):
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
    with st.expander("Ver tabla", expanded=True):
        _render_preview(resolved_rows)

    st.divider()

    # ── Generate ──────────────────────────────────────────────────────────────
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
                    + (f"_{client_name.strip().replace(' ','_')}"
                       if client_name.strip() else "")
                    + f"_{date.today().isoformat()}.pdf"
                )
                st.download_button(
                    "⬇️ Descargar catálogo PDF",
                    data=pdf_bytes, file_name=fname,
                    mime="application/pdf", type="primary"
                )
                st.success("PDF generado correctamente", icon="✅")
            except Exception as e:
                st.error(f"Error: {e}")
                st.exception(e)


# =============================================================================
# Preview
# =============================================================================

def _render_preview(rows: list[dict]):
    groups_order = ["Tarta", "Tarta Individual", "Bocados", "Otros"]
    by_group: dict[str, list] = {}
    for r in rows:
        by_group.setdefault(r["group"], []).append(r)

    for group in groups_order:
        group_rows = by_group.get(group, [])
        if not group_rows:
            continue

        # Group header
        st.markdown(
            f"<div style='background:#9ca3af;color:white;padding:4px 8px;"
            f"font-weight:bold;margin-top:8px'>{group}</div>",
            unsafe_allow_html=True
        )

        h1, h2, h3 = st.columns([3, 2, 1])
        h1.markdown("**Producto**")
        h2.markdown("**Medida**")
        h3.markdown("**€**")

        for row in sorted(group_rows, key=lambda x: x["name"]):
            c1, c2, c3 = st.columns([3, 2, 1])
            name = row["name"]
            if row.get("_overridden"):
                name += " ★"
            c1.write(name)
            c2.write(row["size"] or "—")
            c3.write(
                f"{row['ws_price']:.2f}" if row["ws_price"] else "—"
            )


# =============================================================================
# PDF — Option B layout
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
    font_r = os.path.join(DATA_DIR, "EBGaramond-Regular.ttf")
    font_b = os.path.join(DATA_DIR, "EBGaramond-Bold.ttf")
    if os.path.exists(font_r) and os.path.exists(font_b):
        pdfmetrics.registerFont(TTFont("Gar",  font_r))
        pdfmetrics.registerFont(TTFont("GarB", font_b))
        body, bold = "Gar", "GarB"
    else:
        body, bold = "Times-Roman", "Times-Bold"

    # ── Colours ────────────────────────────────────────────────────────────────
    bg      = colors.HexColor("#F2EEE8")
    dark    = colors.HexColor("#1a1a1a")
    grey    = colors.HexColor("#6b7280")
    row_alt = colors.HexColor("#ebe6de")
    grp_bg  = colors.HexColor("#6b7280")   # group header row background
    grp_fg  = colors.white
    ovr_col = colors.HexColor("#1d4ed8")   # client override price colour
    rule    = colors.HexColor("#d1c9be")

    # ── Helpers ────────────────────────────────────────────────────────────────
    def ps(name, f=None, sz=10, ld=14, al=0, col=None, sb=0, sa=3):
        return ParagraphStyle(
            name, fontName=f or body, fontSize=sz,
            leading=ld, alignment=al,
            textColor=col or dark,
            spaceBefore=sb, spaceAfter=sa,
        )

    # ── Document ───────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2*cm, bottomMargin=2.5*cm,
    )

    story = []

    # Logo
    logo_path = os.path.join(DATA_DIR, "Logo.png")
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=8*cm, height=3*cm, kind='proportional')
        logo.hAlign = 'CENTER'
        story.append(logo)
        story.append(Spacer(1, 0.3*cm))

    # Title block
    story.append(Paragraph(
        title, ps("T", f=bold, sz=17, ld=21, al=1, sb=4, sa=3)
    ))
    story.append(Paragraph(
        "Millington Cakes", ps("S", sz=10, al=1, col=grey, sa=2)
    ))
    if client_name:
        story.append(Paragraph(
            f"Preparado para: {client_name}",
            ps("CL", sz=10, al=1, col=grey, sa=2)
        ))
    story.append(Paragraph(
        cat_date, ps("D", sz=9, al=1, col=grey, sa=10)
    ))
    story.append(HRFlowable(width="100%", thickness=0.5, color=rule))
    story.append(Spacer(1, 0.3*cm))

    # ── Price table ────────────────────────────────────────────────────────────
    # Three columns: Producto, Medida, Precio
    # Group name spans full width as a shaded header row.
    # No vertical lines — cleaner, closer to LaTeX original.

    col_w = [8.5*cm, 4.5*cm, 2.5*cm]

    groups_order = ["Tarta", "Tarta Individual", "Bocados", "Otros"]
    by_group: dict[str, list] = {}
    for r in rows:
        by_group.setdefault(r["group"], []).append(r)

    # Styles for table cells
    prod_ps = ps("pr", sz=9, ld=12)
    size_ps = ps("sz", sz=8, ld=12, col=grey)
    grp_ps  = ps("gp", f=bold, sz=9, ld=12, col=grp_fg)

    table_data   = []
    table_styles = [
        # No outer border
        ("GRID",        (0, 0), (-1, -1), 0, colors.white),
        ("LINEBELOW",   (0, 0), (-1, -1), 0.3, rule),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",(0, 0), (-1, -1), 6),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0,0), (-1, -1), 3),
        ("ALIGN",       (2, 0), (2, -1), "RIGHT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
    ]

    row_idx = 0

    for group in groups_order:
        group_rows = by_group.get(group, [])
        if not group_rows:
            continue

        # Group header row — full width, shaded
        table_data.append([
            Paragraph(group.upper(), grp_ps),
            "",
            Paragraph("Precio (€)", ps(f"gh{row_idx}", f=bold, sz=8, ld=11, al=2, col=grp_fg)),
        ])
        table_styles += [
            ("BACKGROUND", (0, row_idx), (-1, row_idx), grp_bg),
            ("SPAN",       (0, row_idx), (-1, row_idx)),
            ("TOPPADDING", (0, row_idx), (-1, row_idx), 3),
            ("BOTTOMPADDING",(0,row_idx),(-1, row_idx), 3),
        ]
        row_idx += 1

        # Product rows
        for i, row in enumerate(sorted(group_rows, key=lambda x: x["name"])):
            price_val  = row.get("ws_price")
            overridden = row.get("_overridden", False)
            price_str  = f"{price_val:.2f}" if price_val else "—"
            price_col  = ovr_col if overridden else dark

            price_ps = ps(f"p{row_idx}", f=bold, sz=9, ld=12,
                          al=2, col=price_col)

            table_data.append([
                Paragraph(row["name"], prod_ps),
                Paragraph(row["size"] or "—", size_ps),
                Paragraph(price_str, price_ps),
            ])

            # Alternate row shading
            if i % 2 == 1:
                table_styles.append(
                    ("BACKGROUND", (0, row_idx), (-1, row_idx), row_alt)
                )
            row_idx += 1

    table = Table(table_data, colWidths=col_w, repeatRows=0)
    table.setStyle(TableStyle(table_styles))
    story.append(table)

    # Client override footnote
    if client_name and any(r.get("_overridden") for r in rows):
        story.append(Spacer(1, 0.15*cm))
        story.append(Paragraph(
            "★ Precio específico para este cliente.",
            ps("fn", sz=7, col=ovr_col, sa=0)
        ))

    # ── Conditions ─────────────────────────────────────────────────────────────
    if include_cond:
        story.append(Spacer(1, 0.5*cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=rule))
        story.append(Paragraph(
            "Condiciones de Pedido",
            ps("sec", f=bold, sz=11, ld=14, sb=8, sa=4)
        ))

        s       = settings
        min_u   = int(s.get("cond_min_order_units")    or 50)
        min_v   = float(s.get("cond_min_order_value")   or 150)
        del_c   = float(s.get("cond_delivery_charge")   or 25)
        del_t   = float(s.get("cond_delivery_threshold")or 400)
        lead    = int(s.get("cond_lead_time_days")       or 3)
        pay     = int(s.get("cond_payment_days")         or 15)
        cancel  = int(s.get("cond_cancellation_hours")   or 48)
        review  = int(s.get("cond_price_review_months")  or 6)
        var_pct = float(s.get("cond_price_variation_pct")or 5)
        notice  = int(s.get("cond_price_notice_days")    or 30)

        cp = ps("cp", sz=8, ld=12, col=colors.HexColor("#374151"), sa=2)

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
             f"Para asegurar la mejor calidad y servicio, les pedimos que "
             f"realicen sus pedidos con un mínimo de {lead} días de antelación "
             f"a la fecha de entrega prevista."),
            ("Facturación",
             f"La factura será emitida en el momento de la entrega y el pago "
             f"deberá realizarse mediante transferencia bancaria en un plazo de {pay} días."),
            ("Política de cancelación",
             f"Las cancelaciones deberán ser notificadas con al menos {cancel} horas "
             f"de antelación. En caso contrario, se podrá aplicar un cargo por cancelación."),
            ("Modificación de pedidos",
             f"Las modificaciones deberán realizarse con un mínimo de {cancel} horas "
             f"de antelación a la fecha de entrega."),
            ("Revisión de precios",
             f"Los precios indicados en este documento estarán sujetos a revisión "
             f"cada {review} meses bajo condiciones normales de mercado."),
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
            story.append(Paragraph(f"<b>{heading}:</b> {text}", cp))

    # Footer
    story.append(Spacer(1, 0.4*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=rule))
    story.append(Paragraph(
        "Calle de la Granja 100, Nave 5-6, 28108 Alcobendas, Madrid  ·  "
        "637 773 669  ·  www.millingtons.es",
        ps("ft", sz=8, ld=10, al=1, col=grey, sa=0)
    ))

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(bg)
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
