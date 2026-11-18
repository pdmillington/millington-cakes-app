"""The Registros sheet carries the PCC and ingredient summary on the same row."""
import io

import openpyxl

import screen_production as sp


def _runs():
    base = {"production_date": "2026-11-18", "recipe_name": "Lemon Pie", "format": "individual",
            "quantity": 20, "quantity_unit": "units", "created_at": "2026-11-18T09:00:00+00:00"}
    return [
        {**base, "id": "a", "lote_number": "L1", "elaborated_by": "Blanca Sánchez",
         "ingredient_refs": [{"ingredient_name": "Mantequilla", "albaran_ref": "ALB2"},
                             {"ingredient_name": "Harina", "albaran_ref": None}],
         "pcc_log": '[{"step_name": "Horneado", "temp_achieved_c": 180, "time_min": 45, '
                    '"critical_limit_met": true}]'},                      # older layout
        {**base, "id": "b", "lote_number": "L2", "ingredient_refs": [],
         "pcc_log": '[{"step_name": "Baño maría", "temp_achieved_c": 75, "time_achieved_min": 10, '
                    '"critical_limit_temp_c": 70, "ok": false}]'},        # newer layout, failed
        {**base, "id": "c", "lote_number": "L3", "ingredient_refs": [], "pcc_log": None},
    ]


def _sheet(runs, name="Registros"):
    wb = openpyxl.load_workbook(io.BytesIO(sp._build_production_export_xlsx(runs, [])))
    ws = wb[name]
    head = [c.value for c in ws[1]]
    return [dict(zip(head, [c.value for c in row])) for row in ws.iter_rows(min_row=2)]


def test_registros_has_pcc_and_ingredients_on_the_same_row():
    a, b, c = _sheet(_runs())
    assert a["Pasos PCC"] == "Horneado: 180°C 45 min ✓" and a["PCC conformes"] == "Sí"
    assert a["Ingredientes y lotes"] == "Harina; Mantequilla — ALB2"
    assert b["Pasos PCC"] == "Baño maría: 75°C 10 min ✗" and b["PCC conformes"] == "No"
    assert c["Pasos PCC"] is None and c["PCC conformes"] is None


def test_detail_sheets_still_have_one_row_per_step_and_reference():
    assert len(_sheet(_runs(), "PCC")) == 2
    assert len(_sheet(_runs(), "Ingredientes y albaranes")) == 2
