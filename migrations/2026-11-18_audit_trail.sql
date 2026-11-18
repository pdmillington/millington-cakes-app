-- =============================================================================
-- Audit trail + responsible person for production and goods-receipt records
-- =============================================================================
-- Run ONCE in the Supabase SQL editor (Dashboard → SQL editor → New query),
-- BEFORE deploying the matching app version. Safe to run again.
--
-- What it does
--   1. staff_members      list of people who can be named as responsible
--   2. production_runs    + elaborated_by (who produced it)
--   3. production_runs,
--      goods_receipts     + voided_at / voided_by / void_reason
--                         ("Anular" replaces "Eliminar": the record stays)
--   4. record_revisions   every correction or voiding, with who, when, why,
--                         and the full before/after state
--   5. triggers           records cannot be deleted; revisions cannot be
--                         changed or deleted
--
-- Existing records are untouched: elaborated_by stays empty for them and they
-- have no revision history (history starts the day this migration is applied).
--
-- To remove test data deliberately (admin only), switch the guard off, delete,
-- and switch it back on:
--     ALTER TABLE production_runs DISABLE TRIGGER production_runs_no_delete;
--     -- DELETE ...;
--     ALTER TABLE production_runs ENABLE  TRIGGER production_runs_no_delete;
-- =============================================================================


-- 1. Staff ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staff_members (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL UNIQUE,
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO staff_members (name) VALUES
    ('Christine Millington'),
    ('Blanca Sánchez')
ON CONFLICT (name) DO NOTHING;

-- No policies on purpose: only the app's service key (server side) can read it.
ALTER TABLE staff_members ENABLE ROW LEVEL SECURITY;


-- 2. Responsible person on production runs --------------------------------------
ALTER TABLE production_runs
    ADD COLUMN IF NOT EXISTS elaborated_by TEXT;


-- 3. Void instead of delete -----------------------------------------------------
ALTER TABLE production_runs
    ADD COLUMN IF NOT EXISTS voided_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS voided_by   TEXT,
    ADD COLUMN IF NOT EXISTS void_reason TEXT;

ALTER TABLE goods_receipts
    ADD COLUMN IF NOT EXISTS voided_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS voided_by   TEXT,
    ADD COLUMN IF NOT EXISTS void_reason TEXT;


-- 4. Revision history -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS record_revisions (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    record_table  TEXT        NOT NULL
                  CHECK (record_table IN ('production_runs', 'goods_receipts')),
    record_id     TEXT        NOT NULL,
    revision_no   INTEGER     NOT NULL,
    action        TEXT        NOT NULL
                  CHECK (action IN ('edit', 'void', 'restore')),
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by    TEXT        NOT NULL CHECK (btrim(changed_by) <> ''),
    reason        TEXT        NOT NULL CHECK (btrim(reason) <> ''),
    changes       JSONB       NOT NULL,   -- {field: [old, new]}
    before_state  JSONB       NOT NULL,   -- header + child rows before the change
    after_state   JSONB       NOT NULL,   -- header + child rows after the change
    UNIQUE (record_table, record_id, revision_no)
);

CREATE INDEX IF NOT EXISTS record_revisions_record_idx
    ON record_revisions (record_table, record_id);
CREATE INDEX IF NOT EXISTS record_revisions_changed_at_idx
    ON record_revisions (changed_at);

ALTER TABLE record_revisions ENABLE ROW LEVEL SECURITY;


-- 5. Guards ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION forbid_record_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'Los registros no se pueden eliminar (%). Use "Anular" para conservar el historial.',
        TG_TABLE_NAME;
END;
$$;

CREATE OR REPLACE FUNCTION forbid_revision_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'El historial de cambios es de solo lectura (record_revisions).';
END;
$$;

DROP TRIGGER IF EXISTS production_runs_no_delete ON production_runs;
CREATE TRIGGER production_runs_no_delete
    BEFORE DELETE ON production_runs
    FOR EACH ROW EXECUTE FUNCTION forbid_record_delete();

DROP TRIGGER IF EXISTS goods_receipts_no_delete ON goods_receipts;
CREATE TRIGGER goods_receipts_no_delete
    BEFORE DELETE ON goods_receipts
    FOR EACH ROW EXECUTE FUNCTION forbid_record_delete();

DROP TRIGGER IF EXISTS record_revisions_append_only ON record_revisions;
CREATE TRIGGER record_revisions_append_only
    BEFORE UPDATE OR DELETE ON record_revisions
    FOR EACH ROW EXECUTE FUNCTION forbid_revision_change();
