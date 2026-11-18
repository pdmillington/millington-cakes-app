"""
Tests for the audit trail (responsible person, void instead of delete,
revision history) against an in-memory stand-in for Supabase — nothing here
touches the real database.
"""
import itertools
import uuid

import pytest

import millington_db as db


# =============================================================================
# Minimal in-memory Supabase stand-in
# =============================================================================

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, fake, name):
        self.fake, self.name = fake, name
        self._op, self._payload = "select", None
        self._filters, self._orders, self._limit, self._range = [], [], None, None

    # builders
    def select(self, *_a, **_k):   self._op = "select"; return self
    def insert(self, row):         self._op, self._payload = "insert", row; return self
    def upsert(self, row, **_k):   self._op, self._payload = "upsert", row; return self
    def update(self, values):      self._op, self._payload = "update", values; return self
    def delete(self):              self._op = "delete"; return self
    def eq(self, c, v):            self._filters.append(lambda r: r.get(c) == v); return self
    def in_(self, c, vs):          self._filters.append(lambda r: r.get(c) in vs); return self
    def limit(self, n):            self._limit = n; return self
    def range(self, a, b):         self._range = (a, b); return self
    def order(self, c, desc=False): self._orders.append((c, desc)); return self

    def execute(self):
        fake, rows = self.fake, self.fake.tables.setdefault(self.name, [])
        fake.log.append((self._op, self.name))
        if (self._op, self.name) in fake.fail_on:
            raise RuntimeError(f"injected failure: {self._op} {self.name}")
        match = [r for r in rows if all(f(r) for f in self._filters)]
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payload:
                row = {"id": str(uuid.uuid4()), **p}
                if self.name == "record_revisions":
                    key = (row["record_table"], row["record_id"], row["revision_no"])
                    assert key not in {(r["record_table"], r["record_id"], r["revision_no"])
                                       for r in rows}, "duplicate revision number"
                rows.append(row); out.append(dict(row))
            return _Result(out)
        if self._op == "upsert":
            p = self._payload
            for r in rows:
                if r.get("name") == p.get("name"):
                    r.update(p); return _Result([dict(r)])
            rows.append({"id": str(uuid.uuid4()), **p}); return _Result([dict(rows[-1])])
        if self._op == "update":
            for r in match: r.update(self._payload)
            return _Result([dict(r) for r in match])
        if self._op == "delete":
            fake.tables[self.name] = [r for r in rows if r not in match]
            return _Result([dict(r) for r in match])
        for col, desc in reversed(self._orders):
            match.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._range:
            match = match[self._range[0]: self._range[1] + 1]
        if self._limit is not None:
            match = match[: self._limit]
        return _Result([dict(r) for r in match])


class FakeSupabase:
    def __init__(self):
        self.tables, self.log, self.fail_on = {}, [], set()

    def table(self, name):
        return _Query(self, name)

    def rows(self, name):
        return self.tables.get(name, [])


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase()
    monkeypatch.setattr(db, "get_client", lambda: f)
    return f


@pytest.fixture
def run(fake):
    """A production run with two ingredient refs."""
    rid = "run-1"
    fake.tables["production_runs"] = [{
        "id": rid, "lote_number": "MC-261118-001", "recipe_name": "Lemon Pie",
        "format": "individual", "production_date": "2026-11-18", "quantity": 20,
        "notes": None, "elaborated_by": "Blanca Sánchez", "voided_at": None,
        "created_at": "2026-11-18T09:00:00+00:00",
    }]
    fake.tables["production_ingredient_refs"] = [
        {"id": "r1", "production_run_id": rid, "ingredient_name": "Harina", "albaran_ref": "ALB1"},
        {"id": "r2", "production_run_id": rid, "ingredient_name": "Mantequilla", "albaran_ref": "ALB2"},
    ]
    return rid


@pytest.fixture
def receipt(fake):
    rid = "rec-1"
    fake.tables["goods_receipts"] = [{
        "id": rid, "receipt_date": "2026-11-18", "supplier": "GyG", "albaran_ref": "A-9",
        "received_by": "Blanca Sánchez", "notes": None, "voided_at": None,
    }]
    fake.tables["goods_receipt_items"] = [{
        "id": "i1", "receipt_id": rid, "product_name": "Mantequilla", "supplier_lot": "L1",
        "quantity": 10, "quantity_unit": "kg", "temp_type": "refrigerated",
        "temp_measured_c": 4.6, "temp_limit_c": 4.0, "packaging_ok": True,
        "labelling_ok": True, "accepted": True, "rejection_reason": None,
    }]
    return rid


# =============================================================================
# Pure helpers
# =============================================================================

def test_format_reason():
    assert db.format_reason("Otro", "cantidad mal anotada") == "Otro: cantidad mal anotada"
    assert db.format_reason("Dato omitido", "") == "Dato omitido"
    assert db.format_reason(None, " x ") == "x"


def test_diff_ignores_noise():
    before = {"header": {"id": 1, "notes": "", "quantity": 5, "updated_at": "a", "name": " x "}}
    after  = {"header": {"id": 1, "notes": None, "quantity": 5.0, "updated_at": "b", "name": "x"}}
    assert db.diff_states(before, after) == {}


def test_diff_and_describe():
    before = {"header": {"quantity": 20, "notes": None},
              "ingredient_refs": [{"ingredient_name": "Harina", "albaran_ref": "ALB1"}]}
    after  = {"header": {"quantity": 24, "notes": "ok"},
              "ingredient_refs": [{"ingredient_name": "Harina", "albaran_ref": "ALB9"}]}
    changes = db.diff_states(before, after)
    assert changes["quantity"] == [20, 24]
    lines = db.describe_changes(changes)
    assert "Unidades: 20 → 24" in lines
    assert "Notas: — → ok" in lines
    assert any("− Harina — ALB1" in l and "+ Harina — ALB9" in l for l in lines)


# =============================================================================
# Production runs
# =============================================================================

def test_amend_records_revision(fake, run):
    rev = db.amend_production_run(
        run, {"quantity": 24}, [{"ingredient_name": "Harina", "albaran_ref": "ALB9"}],
        changed_by="Christine Millington", reason="Error de transcripción: eran 24")
    assert rev["revision_no"] == 1 and rev["action"] == "edit"
    assert rev["changed_by"] == "Christine Millington"
    assert rev["changes"]["quantity"] == [20, 24]
    assert "ingredient_refs" in rev["changes"]
    assert rev["before_state"]["header"]["quantity"] == 20      # original preserved
    assert rev["after_state"]["header"]["quantity"] == 24
    assert fake.rows("production_runs")[0]["quantity"] == 24
    assert [r["ingredient_name"] for r in fake.rows("production_ingredient_refs")] == ["Harina"]

    rev2 = db.amend_production_run(run, {"quantity": 25}, None, "Blanca Sánchez", "Dato incorrecto")
    assert rev2["revision_no"] == 2
    assert [r["revision_no"] for r in db.get_revisions("production_runs", run)] == [1, 2]


def test_amend_with_no_real_change_writes_nothing(fake, run):
    same_refs = [{"ingredient_name": "Harina", "albaran_ref": "ALB1"},
                 {"ingredient_name": "Mantequilla", "albaran_ref": "ALB2"}]
    assert db.amend_production_run(run, {"quantity": 20, "notes": ""}, same_refs,
                                   "Blanca Sánchez", "Otro: nada") is None
    assert fake.rows("record_revisions") == []


@pytest.mark.parametrize("who,why", [("", "motivo"), ("  ", "motivo"), ("Blanca", ""), ("Blanca", "  ")])
def test_who_and_reason_are_required(fake, run, who, why):
    with pytest.raises(ValueError):
        db.amend_production_run(run, {"quantity": 99}, None, who, why)
    assert fake.rows("production_runs")[0]["quantity"] == 20      # untouched
    assert fake.rows("record_revisions") == []


def test_void_keeps_record_blocks_edit_and_can_be_restored(fake, run):
    rev = db.void_record("production_runs", run, "Christine Millington", "Registro duplicado")
    row = fake.rows("production_runs")[0]
    assert row["voided_at"] and row["voided_by"] == "Christine Millington"
    assert row["void_reason"] == "Registro duplicado"
    assert rev["action"] == "void"

    with pytest.raises(ValueError):                                 # voided = frozen
        db.amend_production_run(run, {"quantity": 1}, None, "Blanca Sánchez", "Otro: x")
    with pytest.raises(ValueError):                                 # can't void twice
        db.void_record("production_runs", run, "Blanca Sánchez", "Creado por error")

    rev2 = db.restore_record("production_runs", run, "Blanca Sánchez", "Anulado por error")
    assert rev2["action"] == "restore" and fake.rows("production_runs")[0]["voided_at"] is None
    assert [r["action"] for r in db.get_revisions("production_runs", run)] == ["void", "restore"]
    with pytest.raises(ValueError):
        db.restore_record("production_runs", run, "Blanca Sánchez", "otra vez")


def test_history_failure_rolls_the_change_back(fake, run):
    fake.fail_on.add(("insert", "record_revisions"))
    with pytest.raises(RuntimeError, match="revertido"):
        db.amend_production_run(run, {"quantity": 77},
                                [{"ingredient_name": "Azúcar", "albaran_ref": None}],
                                "Blanca Sánchez", "Dato incorrecto")
    assert fake.rows("production_runs")[0]["quantity"] == 20
    assert sorted(r["ingredient_name"] for r in fake.rows("production_ingredient_refs")) \
        == ["Harina", "Mantequilla"]


def test_failed_write_midway_is_undone(fake, run):
    # header update succeeds, then re-inserting the refs fails
    fake.fail_on.add(("insert", "production_ingredient_refs"))
    with pytest.raises(RuntimeError):
        db.amend_production_run(run, {"quantity": 55},
                                [{"ingredient_name": "Azúcar", "albaran_ref": "X"}],
                                "Blanca Sánchez", "Dato incorrecto")
    fake.fail_on.clear()
    assert fake.rows("production_runs")[0]["quantity"] == 20
    assert fake.rows("record_revisions") == []


def test_save_records_responsible_person(fake, monkeypatch):
    monkeypatch.setattr(db, "_next_lote_number", lambda sb, d: "MC-261118-001")
    from datetime import date
    saved = db.save_production_run("rid", "Lemon Pie", "individual", date(2026, 11, 18), 20,
                                   elaborated_by="  Blanca Sánchez ")
    assert saved["elaborated_by"] == "Blanca Sánchez"
    assert fake.rows("production_runs")[0]["elaborated_by"] == "Blanca Sánchez"


def test_voided_runs_are_hidden_by_default(fake, run):
    fake.tables["production_runs"].append({
        "id": "run-2", "lote_number": "MC-261118-002", "production_date": "2026-11-18",
        "voided_at": "2026-11-18T10:00:00+00:00"})
    assert [r["id"] for r in db.get_production_runs()] == [run]
    assert {r["id"] for r in db.get_production_runs(include_voided=True)} == {run, "run-2"}


# =============================================================================
# Goods receipts
# =============================================================================

def test_amend_receipt_tracks_items(fake, receipt):
    items = [{**fake.rows("goods_receipt_items")[0], "accepted": False,
              "rejection_reason": "Temperatura"}]
    rev = db.amend_goods_receipt(receipt, {"notes": "Devuelta"}, items,
                                 "Christine Millington", "Dato incorrecto: se rechazó")
    lines = db.describe_changes(rev["changes"])
    assert any(l.startswith("Notas:") for l in lines)
    assert any("RECHAZADO (Temperatura)" in l for l in lines)
    assert fake.rows("goods_receipt_items")[0]["accepted"] is False


def test_receipts_void_and_listing(fake, receipt):
    db.void_record("goods_receipts", receipt, "Blanca Sánchez", "Creado por error")
    assert db.get_goods_receipts() == []
    assert len(db.get_goods_receipts(include_voided=True)) == 1
    assert db.get_albaran_by_lote() == {}


# =============================================================================
# Staff
# =============================================================================

def test_staff_falls_back_to_defaults_before_migration(fake):
    fake.fail_on.add(("select", "staff_members"))        # table doesn't exist yet
    assert db.get_staff_names() == db.DEFAULT_STAFF


def test_staff_add_deactivate_and_reactivate(fake):
    fake.tables["staff_members"] = [{"id": f"s{i}", "name": n, "active": True}
                                    for i, n in enumerate(db.DEFAULT_STAFF)]
    db.add_staff_member("Peter Millington")
    assert "Peter Millington" in db.get_staff_names()

    pid = next(r["id"] for r in db.get_staff() if r["name"] == "Peter Millington")
    db.set_staff_active(pid, False)
    assert "Peter Millington" not in db.get_staff_names()            # not offered any more…
    assert "Peter Millington" in [r["name"] for r in db.get_staff()]  # …but still on record

    db.add_staff_member("Peter Millington")                           # re-adding reactivates
    assert "Peter Millington" in db.get_staff_names()
    assert sum(r["name"] == "Peter Millington" for r in db.get_staff()) == 1
    with pytest.raises(ValueError):
        db.add_staff_member("  ")
