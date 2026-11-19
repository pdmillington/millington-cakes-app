"""Monthly APPCC report: month selection, deviations, traceability, PDF and zip."""
import io
import zipfile

import openpyxl
import pdfplumber

import monthly_report as mr


def _run(id_, lote, day, **kw):
    return {"id": id_, "lote_number": lote, "production_date": f"2026-10-{day:02d}",
            "recipe_name": "Lemon Pie", "format": "individual", "quantity": 20,
            "quantity_unit": "units", "created_at": f"2026-10-{day:02d}T09:00:00+00:00",
            "elaborated_by": "Blanca Sánchez", "ingredient_refs": [], "pcc_log": None, **kw}


def _item(name, **kw):
    return {"product_name": name, "quantity": 5, "quantity_unit": "kg", "temp_type": "ambient",
            "accepted": True, "packaging_ok": True, "labelling_ok": True, **kw}


def _receipt(id_, supplier, alb, day, items, **kw):
    return {"id": id_, "supplier": supplier, "albaran_ref": alb, "receipt_date": f"2026-10-{day:02d}",
            "received_by": "Christine Millington", "items": items,
            "created_at": f"2026-10-{day:02d}T08:00:00+00:00", **kw}


def _data():
    receipts = [
        _receipt("r1", "Makro", "ALB1", 3, [
            _item("Mantequilla", temp_type="refrigerated", temp_measured_c=5.0, temp_limit_c=4.0),
            _item("Nata", temp_type="refrigerated", temp_measured_c=9.0, temp_limit_c=4.0,
                  accepted=False, rejection_reason="Temperatura"),
        ]),
        _receipt("r2", "Gyg", None, 10, [_item("Harina")], received_by=None),
        _receipt("r3", "Prueba", "X", 12, [_item("Sal")], voided_at="2026-10-13T10:00:00+00:00"),
        _receipt("r4", "Makro", "SEP", 28, [_item("Azúcar")]),
    ]
    receipts[3]["receipt_date"] = "2026-09-28"                       # other month
    runs = [
        _run("a", "MC-261005-001", 5, elaborated_by=None, notes="Se quemó el borde",
             ingredient_refs=[{"ingredient_name": "Mantequilla", "albaran_ref": "alb1"},
                              {"ingredient_name": "Harina", "albaran_ref": None}],
             pcc_log='[{"step_name": "Horneado", "temp_achieved_c": 180, "time_min": 45, '
                     '"critical_limit_met": true}]'),
        _run("b", "MC-261006-001", 6,
             pcc_log='[{"step_name": "Baño maría", "temp_achieved_c": 60, "time_achieved_min": 10, '
                     '"critical_limit_temp_c": 70, "ok": false}]'),
        _run("c", "MC-261007-001", 7, is_component_run=True, recipe_name="Masa Sablé"),
        _run("d", "MC-261008-001", 8, voided_at="2026-10-09T10:00:00+00:00"),
    ]
    revisions = [{
        "record_table": "production_runs", "record_id": "a", "revision_no": 1, "action": "edit",
        "changed_at": "2026-10-20T10:00:00+00:00", "changed_by": "Peter Millington",
        "reason": "Error de cantidad", "changes": {"quantity": [200, 20]},
        "before_state": {"header": {"lote_number": "MC-261005-001"}}, "after_state": {},
    }]
    return mr.collect(2026, 10, runs, receipts, revisions, [{"production_run_id": "a", "component_run_id": "c"}])


def test_collect_selects_the_month_and_counts():
    S = _data()["summary"]
    assert S["receipts"] == 2 and S["receipts_voided"] == 1 and S["items"] == 3
    assert S["items_rejected"] == 1
    assert S["runs_final"] == 2 and S["runs_component"] == 1 and S["runs_voided"] == 1
    assert S["pcc_steps"] == 2 and S["pcc_not_met"] == 1
    assert S["corrections"] == 1 and not S["no_activity"]


def test_deviations_cover_rejections_tolerance_pcc_and_notes():
    kinds = {d["kind"] for d in _data()["deviations"]}
    assert {"Producto rechazado", "Recepción con incidencia", "PCC no conforme",
            "Nota en producción"} <= kinds
    tol = next(d for d in _data()["deviations"] if d["kind"] == "Recepción con incidencia")
    assert "dentro de tolerancia" in tol["detail"]


def test_traceability_resolves_albaran_supplier_and_components():
    rows = _data()["trace"]
    mant = next(x for x in rows if x["input"] == "Mantequilla")
    assert mant["supplier"] == "Makro" and mant["received"] == "2026-10-03"   # case-insensitive match
    assert any(x["input"] == "Componente: Masa Sablé" and x["albaran"] == "lote MC-261007-001"
               for x in rows)
    assert next(x for x in rows if x["input"] == "Harina")["albaran"] == "sin albarán"


def test_completeness_flags_missing_fields():
    issues = {c["issue"] for c in _data()["completeness"]}
    assert "Producción sin responsable («Elaborado por»)" in issues
    assert "Recepción sin nº de albarán" in issues and "Recepción sin «Recibido por»" in issues
    assert "Ingrediente sin albarán en producción" in issues
    assert "Producción sin registro PCC" in issues


def test_empty_month_says_no_activity():
    data = mr.collect(2026, 1, [], [], [])
    assert data["summary"]["no_activity"]
    text = _pdf_text(mr.build_pdf(data, "Peter Millington"))
    assert "SIN ACTIVIDAD" in text and "Sin recepciones en el mes" in text


def _pdf_text(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as f:
        return "\n".join(p.extract_text() or "" for p in f.pages)


def test_pdf_contains_all_sections_and_signatures():
    pdf = mr.build_pdf(_data(), "Peter Millington")
    assert pdf[:4] == b"%PDF"
    text = _pdf_text(pdf)
    for needle in ("octubre de 2026", "Desviaciones y acciones correctoras", "ELD R7-01",
                   "Trazabilidad", "Error de cantidad", "Control de completitud",
                   "Christine Millington", "Peter Millington", "RECHAZADO", "ANULADA"):
        assert needle in text, needle


def test_zip_has_pdf_and_workbook_with_extra_sheets():
    name, blob = mr.build_zip(_data(), "Peter Millington")
    assert name == "informe_appcc_2026-10.zip"
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert sorted(z.namelist()) == ["informe_appcc_2026-10.pdf", "registros_2026-10.xlsx"]
    wb = openpyxl.load_workbook(io.BytesIO(z.read("registros_2026-10.xlsx")))
    assert {"Registros", "Recepciones", "Trazabilidad", "Historial de cambios"} <= set(wb.sheetnames)
    assert wb["Registros"].max_row == 1 + 4                      # voided run is listed too
