# monthly_report.py
# =============================================================================
# Monthly APPCC evidence report (for Sanidad, Madrid).
#
# One download per month: a zip with
#   • informe_appcc_YYYY-MM.pdf   the report to sign and keep
#   • registros_YYYY-MM.xlsx      the same data as sheets (for filtering/checks)
#
# collect()      pure: picks the month's records and works out deviations,
#                traceability and completeness checks
# build_pdf()    the report itself
# build_xlsx()   the workbook
# build_zip()    both, zipped
# render_tab()   the Streamlit tab (lives in Producción)
# =============================================================================

import io
import json
import os
import zipfile
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

import millington_db as db

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Senior signature on the report (the business has two employees).
SENIOR_SIGNATORY       = "Christine Millington"
SENIOR_SIGNATORY_ROLE  = "Dirección"

TEMP_TYPE_TEXT = {"refrigerated": "Refrigerado", "frozen": "Congelado", "ambient": "Ambiente"}
TEMP_TOLERANCE = 2.0          # °C at unloading (APPCC Prerrequisitos p.29)
MAX_EXAMPLES   = 8            # records named per completeness issue


# =============================================================================
# Data
# =============================================================================

def month_label(year: int, month: int) -> str:
    return f"{MESES[month - 1]} de {year}"


def _day(value) -> str:
    return str(value or "")[:10]


def _in_month(value, year: int, month: int) -> bool:
    return _day(value)[:7] == f"{year:04d}-{month:02d}"


def _local(ts) -> str:
    import screen_production as sp
    return sp._local_time(ts)


def _norm(value) -> str:
    return " ".join(str(value or "").lower().split())


def pcc_steps(run: dict) -> list[dict]:
    """The run's PCC log as [{step, temp_c, minutes, met}] — handles both stored layouts."""
    raw = run.get("pcc_log")
    try:
        steps = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        steps = []
    out = []
    for s in steps if isinstance(steps, list) else []:
        out.append({
            "step":    s.get("step_name") or "?",
            "temp_c":  s.get("temp_achieved_c"),
            "minutes": s.get("time_min", s.get("time_achieved_min")),
            "met":     s.get("critical_limit_met", s.get("ok")),
        })
    return out


def pcc_text(steps: list[dict]) -> str:
    parts = []
    for s in steps:
        bits = [f"{s['step']}:"]
        if s["temp_c"] is not None:
            bits.append(f"{s['temp_c']:g} °C")
        if s["minutes"]:
            bits.append(f"{s['minutes']:g} min")
        bits.append("conforme" if s["met"] is True else "NO conforme" if s["met"] is False else "")
        parts.append(" ".join(b for b in bits if b))
    return "; ".join(parts)


def run_title(run: dict) -> str:
    return f"{run.get('lote_number') or '?'} · {run.get('recipe_name') or '?'}"


def receipt_title(r: dict) -> str:
    alb = r.get("albaran_ref")
    return f"{r.get('supplier') or '?'} · " + (f"albarán {alb}" if alb else "sin albarán")


def collect(year: int, month: int, runs: list[dict], receipts: list[dict],
            revisions: list[dict], links: list[dict] | None = None) -> dict:
    """Everything the report shows, for one month.

    runs / receipts / revisions are the full tables (voided records included):
    older records are needed to resolve albaranes and component links.
    """
    links = links or []
    runs_by_id     = {str(r["id"]): r for r in runs}
    receipts_by_id = {str(r["id"]): r for r in receipts}

    runs_m = sorted((r for r in runs if _in_month(r.get("production_date"), year, month)),
                    key=lambda r: (_day(r.get("production_date")), r.get("lote_number") or ""))
    rec_m = sorted((r for r in receipts if _in_month(r.get("receipt_date"), year, month)),
                   key=lambda r: (_day(r.get("receipt_date")), str(r.get("created_at") or "")))
    live_runs = [r for r in runs_m if not r.get("voided_at")]
    live_rec  = [r for r in rec_m if not r.get("voided_at")]

    # ---- deviations ------------------------------------------------------
    deviations = []

    def dev(date_, record, kind, detail, action):
        deviations.append({"date": _day(date_), "record": record, "kind": kind,
                           "detail": detail, "action": action})

    for r in live_rec:
        for it in r.get("items") or []:
            name = it.get("product_name") or "?"
            if it.get("accepted", True) is False:
                dev(r.get("receipt_date"), receipt_title(r), "Producto rechazado",
                    f"{name}" + (f" — {it['rejection_reason']}" if it.get("rejection_reason") else ""),
                    "Producto rechazado")
                continue
            problems = []
            if it.get("packaging_ok") is False:
                problems.append("envase no conforme")
            if it.get("labelling_ok") is False:
                problems.append("etiquetado no conforme")
            t, lim = it.get("temp_measured_c"), it.get("temp_limit_c")
            if t is not None and lim is not None and t > lim:
                where = "dentro de tolerancia" if t <= lim + TEMP_TOLERANCE else "fuera de tolerancia"
                problems.append(f"temperatura {t:g} °C sobre el límite de {lim:g} °C ({where})")
            if problems:
                dev(r.get("receipt_date"), receipt_title(r), "Recepción con incidencia",
                    f"{name}: " + "; ".join(problems), "Aceptado")
        if r.get("notes"):
            dev(r.get("receipt_date"), receipt_title(r), "Observación en recepción",
                r["notes"], "—")
    for r in live_runs:
        for s in pcc_steps(r):
            if s["met"] is False:
                dev(r.get("production_date"), run_title(r), "PCC no conforme",
                    pcc_text([s]), "—")
        if r.get("notes"):
            dev(r.get("production_date"), run_title(r), "Nota en producción", r["notes"], "—")
    deviations.sort(key=lambda d: d["date"])

    # ---- traceability ----------------------------------------------------
    albaranes: dict[str, list[dict]] = {}
    for r in receipts:
        if not r.get("voided_at") and r.get("albaran_ref"):
            albaranes.setdefault(_norm(r["albaran_ref"]), []).append(r)

    link_by_run: dict[str, list[str]] = {}
    for l in links:
        link_by_run.setdefault(str(l["production_run_id"]), []).append(str(l["component_run_id"]))

    trace = []
    for r in live_runs:
        base = {"lote": r.get("lote_number"), "date": _day(r.get("production_date")),
                "product": r.get("recipe_name")}
        for cid in link_by_run.get(str(r["id"]), []):
            c = runs_by_id.get(cid) or {}
            trace.append({**base, "input": f"Componente: {c.get('recipe_name') or '?'}",
                          "albaran": f"lote {c.get('lote_number') or '?'}",
                          "supplier": "(elaboración propia)", "received": _day(c.get("production_date"))})
        for ref in sorted(r.get("ingredient_refs") or [], key=lambda x: x.get("ingredient_name") or ""):
            alb = ref.get("albaran_ref")
            matches = albaranes.get(_norm(alb), []) if alb else []
            trace.append({
                **base, "input": ref.get("ingredient_name"), "albaran": alb or "sin albarán",
                "supplier": ", ".join(sorted({m.get("supplier") or "?" for m in matches}))
                            or ("albarán no encontrado en recepciones" if alb else "—"),
                "received": ", ".join(sorted({_day(m.get("receipt_date")) for m in matches})) or "—",
            })

    # ---- corrections and voids ------------------------------------------
    def record_date(rev):
        rec = (runs_by_id if rev.get("record_table") == "production_runs"
               else receipts_by_id).get(str(rev.get("record_id"))) or {}
        return rec.get("production_date") or rec.get("receipt_date")

    revs_m = []
    for rev in sorted(revisions, key=lambda x: str(x.get("changed_at"))):
        if _in_month(record_date(rev), year, month) or _in_month(_local(rev.get("changed_at")), year, month):
            head = (rev.get("before_state") or {}).get("header") or {}
            if rev.get("record_table") == "goods_receipts":
                kind, ident = "Recepción", (f"{head.get('supplier') or '?'} · "
                                            f"{head.get('albaran_ref') or 'sin albarán'}")
            else:
                kind, ident = "Producción", head.get("lote_number") or str(rev.get("record_id"))
            revs_m.append({
                "when": _local(rev.get("changed_at")), "kind": kind, "ident": ident,
                "record_date": _day(record_date(rev)), "action": rev.get("action"),
                "by": rev.get("changed_by"), "reason": rev.get("reason"),
                "changes": db.describe_changes(rev.get("changes") or {}),
                "raw": rev,
            })

    # ---- completeness ----------------------------------------------------
    issues: dict[str, list[str]] = {}

    def flag(issue, record):
        issues.setdefault(issue, []).append(record)

    for r in live_runs:
        t = run_title(r)
        if not r.get("elaborated_by"):
            flag("Producción sin responsable («Elaborado por»)", t)
        if not pcc_steps(r) and r.get("oven_temp_c") is None:
            flag("Producción sin registro PCC", t)
        if not r.get("is_component_run") and not r.get("ingredient_refs"):
            flag("Producción sin ingredientes/albaranes asociados", t)
        missing = [x.get("ingredient_name") for x in r.get("ingredient_refs") or []
                   if not x.get("albaran_ref")]
        if missing:
            flag("Ingrediente sin albarán en producción", f"{t} ({', '.join(map(str, missing))})")
    for r in live_rec:
        t = f"{_day(r.get('receipt_date'))} {receipt_title(r)}"
        if not r.get("albaran_ref"):
            flag("Recepción sin nº de albarán", t)
        if not r.get("received_by"):
            flag("Recepción sin «Recibido por»", t)
        for it in r.get("items") or []:
            if it.get("temp_type") in ("refrigerated", "frozen") and it.get("temp_measured_c") is None:
                flag("Producto refrigerado/congelado sin temperatura medida",
                     f"{t} ({it.get('product_name')})")

    completeness = [{"issue": k, "count": len(v), "records": v} for k, v in issues.items()]
    flagged_records = len({x for v in issues.values() for x in v})

    steps_all = [s for r in live_runs for s in pcc_steps(r)]
    items_all = [it for r in live_rec for it in r.get("items") or []]
    summary = {
        "receipts":          len(live_rec),
        "receipts_voided":   len(rec_m) - len(live_rec),
        "items":             len(items_all),
        "items_rejected":    sum(1 for it in items_all if it.get("accepted", True) is False),
        "runs_final":        sum(1 for r in live_runs if not r.get("is_component_run")),
        "runs_component":    sum(1 for r in live_runs if r.get("is_component_run")),
        "runs_voided":       len(runs_m) - len(live_runs),
        "pcc_steps":         len(steps_all),
        "pcc_not_met":       sum(1 for s in steps_all if s["met"] is False),
        "deviations":        len(deviations),
        "corrections":       sum(1 for r in revs_m if r["action"] == "edit"),
        "voids":             sum(1 for r in revs_m if r["action"] == "void"),
        "restores":          sum(1 for r in revs_m if r["action"] == "restore"),
        "completeness":      len(completeness),
        "flagged_records":   flagged_records,
        "no_activity":       not live_rec and not live_runs,
    }
    return {"year": year, "month": month, "runs": runs_m, "receipts": rec_m,
            "deviations": deviations, "trace": trace, "revisions": revs_m,
            "completeness": completeness, "summary": summary}


# =============================================================================
# PDF
# =============================================================================

def _fonts():
    import screen_production as sp
    return sp._load_fonts()


def build_pdf(data: dict, prepared_by: str, generated_at: datetime | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)
    import screen_production as sp

    body, bold = _fonts()
    dark, mid = colors.HexColor("#1a1a1a"), colors.HexColor("#6b7280")
    band, line = colors.HexColor("#F2EEE8"), colors.HexColor("#d1cdc7")
    head_bg, red = colors.HexColor("#9ca3af"), colors.HexColor("#b91c1c")
    year, month, S = data["year"], data["month"], data["summary"]
    generated_at = generated_at or datetime.now()
    label = month_label(year, month)

    def ps(name, font=None, size=9, color=dark, sa=4, sb=0, align=0):
        return ParagraphStyle(name, fontName=font or body, fontSize=size, leading=size + 3.5,
                              textColor=color, spaceAfter=sa, spaceBefore=sb, alignment=align)

    def P(text, **kw):
        return Paragraph(escape(str(text if text not in (None, "") else "—")).replace("\n", "<br/>"),
                         ps("c", **{"size": 8, "sa": 0, **kw}))

    def table(header, rows, widths, voided_rows=()):
        t = Table([[P(h, font=bold, color=colors.white) for h in header]] + rows,
                  colWidths=[w * cm for w in widths], repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), head_bg), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.4, line),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#faf9f7")]),
        ]
        for i in voided_rows:
            style.append(("BACKGROUND", (0, i + 1), (-1, i + 1), colors.HexColor("#f3e8e8")))
        t.setStyle(TableStyle(style))
        return t

    def heading(text):
        style = ps("h", font=bold, size=12, sa=4, sb=12)
        style.keepWithNext = 1
        return Paragraph(escape(text), style)

    def none_line(text):
        return Paragraph(escape(text), ps("n", color=mid, size=9, sa=4))

    story = [
        Paragraph(escape(sp.COMPANY_NAME), ps("co", font=bold, size=10, color=mid, sa=2)),
        Paragraph(escape(f"CIF {sp.COMPANY_CIF} · {sp.COMPANY_ADDRESS}"), ps("ca", color=mid, sa=6)),
        HRFlowable(width="100%", thickness=1, color=band, spaceAfter=10),
        Paragraph("INFORME MENSUAL APPCC", ps("t", font=bold, size=18, sa=2)),
        Paragraph(escape(f"Periodo: {label} · Registros de recepción, elaboración y trazabilidad"),
                  ps("st", size=11, color=mid, sa=10)),
    ]

    # ---- 1. summary ------------------------------------------------------
    story.append(heading("1. Resumen del mes"))
    if S["no_activity"]:
        story.append(Paragraph(escape("SIN ACTIVIDAD: no constan recepciones ni elaboraciones "
                                      f"en {label}."), ps("na", font=bold, size=11, sa=6)))
    rows = [
        ["Recepciones de materias primas", f"{S['receipts']} ({S['items']} productos; "
                                           f"rechazados: {S['items_rejected']})"],
        ["Elaboraciones", f"producto final: {S['runs_final']}; componentes: {S['runs_component']}"],
        ["Pasos PCC registrados", f"{S['pcc_steps']} (no conformes: {S['pcc_not_met']})"],
        ["Desviaciones registradas", str(S["deviations"])],
        ["Correcciones / anulaciones / restauraciones",
         f"{S['corrections']} / {S['voids']} / {S['restores']}"],
        ["Registros anulados en el mes",
         f"recepciones: {S['receipts_voided']}; elaboraciones: {S['runs_voided']}"],
        ["Control de completitud", "sin incidencias" if not S["completeness"]
         else f"{S['completeness']} tipos de incidencia en {S['flagged_records']} registros"],
        ["Preparado por", prepared_by or "—"],
        ["Generado el", generated_at.strftime("%d/%m/%Y %H:%M")],
    ]
    t = Table([[P(a, font=bold), P(b)] for a, b in rows], colWidths=[8*cm, 18*cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), band), ("GRID", (0, 0), (-1, -1), 0.4, line),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(t)

    # ---- 2. deviations ---------------------------------------------------
    story.append(heading("2. Desviaciones y acciones correctoras"))
    if data["deviations"]:
        story.append(table(
            ["Fecha", "Registro", "Tipo", "Detalle", "Acción registrada"],
            [[P(d["date"]), P(d["record"]), P(d["kind"]), P(d["detail"]), P(d["action"])]
             for d in data["deviations"]], [2.2, 6.5, 3.8, 10.2, 4.0]))
        story.append(Paragraph(escape(
            "«—» indica que el sistema no tiene un campo de acción correctora; las acciones tomadas "
            "se anotan en las observaciones del registro."), ps("nt", size=8, color=mid, sb=3)))
    else:
        story.append(none_line("No se registraron desviaciones en el mes."))

    # ---- 3. receptions ---------------------------------------------------
    story.append(heading("3. Recepción de materias primas (APPCC ELD R7-01)"))
    rec_rows, voided = [], []
    for r in data["receipts"]:
        items = r.get("items") or [{}]
        for it in items:
            if r.get("voided_at"):
                voided.append(len(rec_rows))
            t_meas = it.get("temp_measured_c")
            qty = it.get("quantity")
            rec_rows.append([
                P(_day(r.get("receipt_date"))), P(r.get("supplier")), P(r.get("albaran_ref")),
                P(it.get("product_name")), P(it.get("supplier_lot")),
                P(f"{qty:g} {it.get('quantity_unit') or ''}".strip() if qty is not None else None),
                P(TEMP_TYPE_TEXT.get(it.get("temp_type"), it.get("temp_type"))),
                P(f"{t_meas:.1f} °C" if t_meas is not None else None),
                P("Sí" if it.get("packaging_ok", True) else "No"),
                P("Sí" if it.get("labelling_ok", True) else "No"),
                P("ANULADA" if r.get("voided_at") else
                  ("Aceptado" if it.get("accepted", True) else "RECHAZADO"),
                  color=red if (r.get("voided_at") or it.get("accepted", True) is False) else dark),
                P(r.get("received_by")),
            ])
    if rec_rows:
        story.append(table(
            ["Fecha", "Proveedor", "Albarán", "Producto", "Lote prov.", "Cantidad", "Tipo",
             "T.ª medida", "Envase OK", "Etiq. OK", "Resultado", "Recibido por"],
            rec_rows, [1.9, 2.7, 2.8, 3.8, 2.1, 1.7, 1.8, 1.6, 1.3, 1.3, 2.4, 3.2], voided))
    else:
        story.append(none_line("Sin recepciones en el mes."))

    # ---- 4. production ---------------------------------------------------
    story.append(heading("4. Elaboración y puntos de control crítico (PCC)"))
    run_rows, voided = [], []
    for r in data["runs"]:
        if r.get("voided_at"):
            voided.append(len(run_rows))
        qty = r.get("quantity")
        unit = r.get("quantity_unit") or ""
        pcc = pcc_text(pcc_steps(r))
        if not pcc and r.get("oven_temp_c") is not None:
            pcc = f"Horno: {r['oven_temp_c']:g} °C" + (
                f" {r['bake_time_min']:g} min" if r.get("bake_time_min") else "")
        run_rows.append([
            P(r.get("lote_number")), P(_day(r.get("production_date"))), P(r.get("recipe_name")),
            P("Componente" if r.get("is_component_run")
              else sp.FORMAT_DISPLAY.get(r.get("format"), r.get("format"))),
            P(f"{qty:g} {unit}".strip() if qty is not None else None),
            P(r.get("elaborated_by")), P("ANULADO" if r.get("voided_at") else pcc,
                                         color=red if r.get("voided_at") else dark),
        ])
    if run_rows:
        story.append(table(["Lote", "Fecha", "Producto", "Formato", "Cantidad", "Elaborado por",
                            "Temperatura y tiempo (PCC)"],
                           run_rows, [3.0, 1.9, 4.8, 2.4, 2.0, 3.3, 9.3], voided))
    else:
        story.append(none_line("Sin elaboraciones en el mes."))

    # ---- 5. traceability -------------------------------------------------
    story.append(heading("5. Trazabilidad: de cada lote a sus ingredientes y proveedores"))
    if data["trace"]:
        story.append(table(
            ["Lote", "Fecha", "Producto", "Ingrediente / componente", "Albarán", "Proveedor", "Recibido"],
            [[P(x["lote"]), P(x["date"]), P(x["product"]), P(x["input"]), P(x["albaran"]),
              P(x["supplier"]), P(x["received"])] for x in data["trace"]],
            [3.0, 1.9, 4.2, 5.0, 4.5, 4.8, 3.3]))
    else:
        story.append(none_line("Sin elaboraciones con ingredientes registrados."))
    story.append(Paragraph(escape(
        "Trazabilidad hacia adelante: el número de lote figura en las etiquetas de las cajas "
        "(«Imprimir etiquetas»). El cliente destinatario no se registra en el sistema."),
        ps("fw", size=8, color=mid, sb=3)))

    # ---- 6. corrections --------------------------------------------------
    story.append(heading("6. Correcciones y anulaciones de registros"))
    if data["revisions"]:
        names = {"edit": "Corrección", "void": "Anulación", "restore": "Restauración"}
        story.append(table(
            ["Fecha y hora", "Registro", "Identificador", "Acción", "Por", "Motivo", "Cambios"],
            [[P(x["when"]), P(x["kind"]), P(f"{x['ident']} ({x['record_date']})"),
              P(names.get(x["action"], x["action"])), P(x["by"]), P(x["reason"]),
              P("\n".join(x["changes"]) or None)] for x in data["revisions"]],
            [2.6, 2.0, 5.2, 2.2, 3.0, 4.8, 6.9]))
    else:
        story.append(none_line("Sin correcciones ni anulaciones en el mes."))

    # ---- 7. completeness -------------------------------------------------
    story.append(heading("7. Control de completitud de los datos"))
    if data["completeness"]:
        rows = []
        for c in data["completeness"]:
            ex = c["records"][:MAX_EXAMPLES]
            more = f" … y {len(c['records']) - MAX_EXAMPLES} más" if len(c["records"]) > MAX_EXAMPLES else ""
            rows.append([P(c["issue"]), P(c["count"]), P("; ".join(ex) + more)])
        story.append(table(["Incidencia", "Nº", "Registros"], rows, [8.0, 1.5, 17.2]))
    else:
        story.append(none_line("Todos los registros del mes están completos."))

    # ---- 8. notes + sign-off --------------------------------------------
    story.append(heading("8. Alcance y limitaciones de este informe"))
    for note in (
        "Los datos proceden del sistema de registro de la empresa. Los registros no se eliminan: "
        "se anulan o se corrigen dejando constancia de quién, cuándo y por qué.",
        "El responsable de cada recepción o elaboración es la persona seleccionada en la aplicación "
        "(se utiliza un acceso compartido). Los registros anteriores a la introducción del campo "
        "«Elaborado por» no lo incluyen.",
        "El historial de cambios existe desde la puesta en marcha del control de cambios; "
        "las modificaciones directas en la base de datos no quedan registradas.",
    ):
        story.append(Paragraph("• " + escape(note), ps("lim", size=8.5, sa=2)))

    def sign(role, name):
        box = Table([[P(role, font=bold)], [Paragraph(" ", ps("sp"))], [P(f"Nombre: {name}" if name else "Nombre:")],
                     [P("Firma y fecha:")]], colWidths=[12.5 * cm], rowHeights=[None, 0.9 * cm, None, 1.4 * cm])
        box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, line),
                                 ("BACKGROUND", (0, 0), (-1, 0), band)]))
        return box

    sig = Table([[sign("Preparado por", prepared_by),
                  sign(f"Aprobado — {SENIOR_SIGNATORY_ROLE}", SENIOR_SIGNATORY)]],
                colWidths=[13.2 * cm, 13.2 * cm])
    story.append(KeepTogether([heading("9. Firmas"), sig]))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(body, 8)
        canvas.setFillColor(mid)
        canvas.drawString(1.5 * cm, 0.9 * cm,
                          f"{sp.COMPANY_NAME} · Informe APPCC · {label}")
        canvas.drawRightString(landscape(A4)[0] - 1.5 * cm, 0.9 * cm, f"Página {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.6 * cm,
                            title=f"Informe APPCC {label}", author=prepared_by or sp.COMPANY_NAME)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# =============================================================================
# Excel + zip
# =============================================================================

def build_xlsx(data: dict) -> bytes:
    """The existing production export for the month, plus receipts and traceability sheets."""
    import openpyxl
    from openpyxl.styles import Font
    import screen_production as sp

    wb = openpyxl.load_workbook(io.BytesIO(
        sp._build_production_export_xlsx(data["runs"], [x["raw"] for x in data["revisions"]])))

    ws = wb.create_sheet("Recepciones")
    ws.append(["Fecha", "Proveedor", "Albarán", "Recibido por", "Estado", "Producto", "Lote proveedor",
               "Cantidad", "Unidad", "Tipo", "T.ª medida (°C)", "Límite (°C)", "Envase OK",
               "Etiquetado OK", "Aceptado", "Motivo de rechazo", "Observaciones"])
    yn = lambda v: "Sí" if v else "No"
    for r in data["receipts"]:
        for it in r.get("items") or [{}]:
            ws.append([_day(r.get("receipt_date")), r.get("supplier"), r.get("albaran_ref"),
                       r.get("received_by"), "ANULADA" if r.get("voided_at") else "Vigente",
                       it.get("product_name"), it.get("supplier_lot"), it.get("quantity"),
                       it.get("quantity_unit"), TEMP_TYPE_TEXT.get(it.get("temp_type"), it.get("temp_type")),
                       it.get("temp_measured_c"), it.get("temp_limit_c"),
                       yn(it.get("packaging_ok", True)), yn(it.get("labelling_ok", True)),
                       yn(it.get("accepted", True)), it.get("rejection_reason"), r.get("notes")])

    wt = wb.create_sheet("Trazabilidad")
    wt.append(["Lote", "Fecha", "Producto", "Ingrediente / componente", "Albarán", "Proveedor", "Recibido"])
    for x in data["trace"]:
        wt.append([x["lote"], x["date"], x["product"], x["input"], x["albaran"], x["supplier"], x["received"]])

    for sheet in (ws, wt):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def build_zip(data: dict, prepared_by: str, generated_at: datetime | None = None) -> tuple[str, bytes]:
    tag = f"{data['year']:04d}-{data['month']:02d}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"informe_appcc_{tag}.pdf", build_pdf(data, prepared_by, generated_at))
        z.writestr(f"registros_{tag}.xlsx", build_xlsx(data))
    return f"informe_appcc_{tag}.zip", buf.getvalue()


# =============================================================================
# Streamlit tab
# =============================================================================

def render_tab():
    import streamlit as st
    import screen_production as sp

    st.markdown("### Informe mensual APPCC")
    st.caption("Un solo archivo (.zip) con el informe en PDF para firmar y archivar y el Excel con "
               "los mismos datos. Incluye recepciones, elaboraciones y PCC, trazabilidad, "
               "correcciones y un control de datos incompletos.")

    last_month_end = date.today().replace(day=1) - timedelta(days=1)
    years = list(range(date.today().year - 4, date.today().year + 1))
    c1, c2, c3 = st.columns([1, 1.3, 2])
    year = c1.selectbox("Año", years, index=years.index(last_month_end.year), key="mr_year")
    month = c2.selectbox("Mes", list(range(1, 13)), index=last_month_end.month - 1,
                         format_func=lambda m: MESES[m - 1].capitalize(), key="mr_month")
    with c3:
        who = sp._person_select("Preparado por", "mr_by")
    if who == SENIOR_SIGNATORY:
        st.caption(f"Firma de aprobación: {SENIOR_SIGNATORY}. Conviene que el informe lo prepare "
                   "otra persona, para que la revisión sea independiente.")

    key = (year, month, who)
    if st.button("Generar informe", type="primary", disabled=not who, key="mr_go"):
        with st.spinner("Preparando informe…"):
            runs = db.get_all_production_runs()
            data = collect(year, month, runs, db.get_all_goods_receipts(), db.get_all_revisions(),
                           db.get_component_links([r["id"] for r in runs]))
            name, blob = build_zip(data, who)
        st.session_state["mr_result"] = {"key": key, "name": name, "zip": blob,
                                         "summary": data["summary"]}
    if not who:
        st.info("Selecciona quién prepara el informe para poder generarlo.")

    res = st.session_state.get("mr_result")
    if res and res["key"] == key:
        S = res["summary"]
        if S["no_activity"]:
            st.warning(f"Sin actividad en {month_label(year, month)}: el informe lo hará constar.")
        m = st.columns(4)
        m[0].metric("Recepciones", S["receipts"])
        m[1].metric("Elaboraciones", S["runs_final"] + S["runs_component"])
        m[2].metric("Desviaciones", S["deviations"])
        m[3].metric("Datos incompletos", S["flagged_records"])
        st.download_button("⬇️ Descargar informe (.zip)", data=res["zip"], file_name=res["name"],
                           mime="application/zip", key="mr_dl")
    elif res:
        st.caption("Has cambiado el mes o la persona: vuelve a pulsar «Generar informe».")
