-- =============================================================================
-- MILLINGTON CAKES — Supabase Schema
-- Run this in the Supabase SQL editor (Dashboard → SQL Editor → New query)
-- Tables are created in dependency order to satisfy foreign key constraints.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- REFERENCE DATA TABLES
-- These hold the lookup values that make up the SKU segments.
-- -----------------------------------------------------------------------------

CREATE TABLE cake_codes (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code        TEXT        NOT NULL UNIQUE,   -- e.g. 'CC', 'LP', 'FR'
    name        TEXT        NOT NULL,           -- e.g. 'Chocolate Crocanti'
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE size_tiers (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code            TEXT        NOT NULL UNIQUE, -- e.g. 'LA', 'XL', '25', '30'
    label           TEXT        NOT NULL,         -- e.g. 'Large', '25cm'
    is_numeric      BOOLEAN     NOT NULL DEFAULT FALSE,
    -- For named tiers: the size range they cover and what unit they use
    size_type       TEXT        CHECK (size_type IN ('diameter', 'weight', 'portions')),
    min_value       NUMERIC,    -- e.g. 20 for LA (diameter cm), 1 for LA (weight kg)
    max_value       NUMERIC,    -- e.g. 22 for LA (diameter cm)
    unit            TEXT,       -- 'cm', 'kg', 'portions'
    -- For numeric tiers: the single exact value (always cm diameter)
    numeric_value   NUMERIC,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE price_channels (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code        TEXT        NOT NULL UNIQUE,   -- 'GW', 'WS', 'MD'
    label       TEXT        NOT NULL,           -- 'General web', 'Wholesale', 'Mentidero'
    created_at  TIMESTAMPTZ DEFAULT NOW()
);


-- -----------------------------------------------------------------------------
-- INGREDIENTS
-- Raw baking materials. pack_price_ex_vat is always ex-VAT (Spanish wholesale
-- pricing standard). cost_per_unit is computed by the application on save.
-- -----------------------------------------------------------------------------

CREATE TABLE ingredients (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT        NOT NULL UNIQUE,
    supplier            TEXT,
    pack_size           NUMERIC,    -- quantity in the pack (e.g. 1000 for 1kg bag)
    pack_unit           TEXT        CHECK (pack_unit IN ('g', 'kg', 'ml', 'l', 'units')),
    pack_price_ex_vat   NUMERIC,    -- price you pay, before VAT
    vat_rate            NUMERIC     NOT NULL DEFAULT 0.10, -- 0.0, 0.04, 0.10, 0.21
    cost_per_unit       NUMERIC,    -- pack_price_ex_vat / pack_size — set on save
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);


-- -----------------------------------------------------------------------------
-- CONSUMABLES
-- Non-food materials: boxes, acetate, piping bags, labels etc.
-- Structurally identical to ingredients but kept separate for clarity.
-- Default VAT is 0.21 (standard rate applies to all packaging materials).
-- -----------------------------------------------------------------------------

CREATE TABLE consumables (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT        NOT NULL UNIQUE,
    supplier            TEXT,
    pack_quantity       NUMERIC,    -- number of units in the pack (e.g. 50 boxes)
    pack_price_ex_vat   NUMERIC,    -- price you pay, before VAT
    vat_rate            NUMERIC     NOT NULL DEFAULT 0.21,
    cost_per_unit       NUMERIC,    -- pack_price_ex_vat / pack_quantity — set on save
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);


-- -----------------------------------------------------------------------------
-- PACKAGING PRESETS
-- Saved combinations of consumables for a given cake type.
-- e.g. "Standard tart 22-26cm" = 1 box + 1 base + 1 label.
-- Selecting a preset in the calculator populates all consumable fields at once.
-- -----------------------------------------------------------------------------

CREATE TABLE packaging_presets (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT    NOT NULL UNIQUE,  -- e.g. 'Standard tart 22-26cm'
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE packaging_preset_lines (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    preset_id       UUID        NOT NULL REFERENCES packaging_presets(id) ON DELETE CASCADE,
    consumable_id   UUID        NOT NULL REFERENCES consumables(id),
    quantity        NUMERIC     NOT NULL DEFAULT 1
);


-- -----------------------------------------------------------------------------
-- RECIPES
-- The reference formulation of a product at a specific base size.
-- One recipe per CAKE_CODE + VERSION combination.
-- ref_height_cm is required for volume-based diameter scaling.
-- If NULL, the calculator will warn that scaling may be inaccurate.
-- -----------------------------------------------------------------------------

CREATE TABLE recipes (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    cake_code_id    UUID        REFERENCES cake_codes(id),
    version         TEXT        NOT NULL DEFAULT '01',   -- '01', '02' etc.
    name            TEXT        NOT NULL,
    size_type       TEXT        NOT NULL CHECK (size_type IN ('diameter', 'weight', 'portions')),
    -- Reference dimensions — only the fields relevant to size_type are populated
    ref_diameter_cm NUMERIC,    -- used when size_type = 'diameter'
    ref_height_cm   NUMERIC,    -- used when size_type = 'diameter' — critical for scaling
    ref_weight_kg   NUMERIC,    -- used when size_type = 'weight'
    ref_portions    INTEGER,    -- used when size_type = 'portions'
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Ensure version is unique within a cake code
CREATE UNIQUE INDEX recipes_cake_code_version_idx ON recipes(cake_code_id, version)
    WHERE cake_code_id IS NOT NULL;


-- -----------------------------------------------------------------------------
-- RECIPE INGREDIENT LINES
-- Each row is one ingredient (or sub-recipe) in a recipe.
-- Exactly one of ingredient_id or sub_recipe_id must be set per row.
-- amount is always in the pack_unit of the ingredient (grams, units, etc.)
-- or in the size_type unit of the sub-recipe.
-- -----------------------------------------------------------------------------

CREATE TABLE recipe_ingredient_lines (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    recipe_id       UUID        NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    ingredient_id   UUID        REFERENCES ingredients(id),
    sub_recipe_id   UUID        REFERENCES recipes(id),
    amount          NUMERIC     NOT NULL,
    sort_order      INTEGER     DEFAULT 0,
    -- Enforce that each line references exactly one thing
    CONSTRAINT one_ref_only CHECK (
        (ingredient_id IS NOT NULL AND sub_recipe_id IS NULL) OR
        (ingredient_id IS NULL  AND sub_recipe_id IS NOT NULL)
    )
);


-- -----------------------------------------------------------------------------
-- SKUS
-- A SKU is a specific sellable product:
--   recipe × size_tier × price_channel → sku_code (e.g. 'CC-01-LA-GW')
--
-- locked = TRUE for any SKU already existing in Shopify or other systems.
-- Locked SKUs cannot have their sku_code edited, only their cost recalculated
-- and packaging preset updated.
--
-- target_* fields are populated when size_tier is numeric (bespoke sizes).
-- For named tiers the calculator uses the tier's own min/max values.
-- -----------------------------------------------------------------------------

CREATE TABLE skus (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    sku_code                TEXT        NOT NULL UNIQUE,  -- 'CC-01-LA-GW'
    recipe_id               UUID        REFERENCES recipes(id),
    size_tier_id            UUID        REFERENCES size_tiers(id),
    price_channel_id        UUID        REFERENCES price_channels(id),
    packaging_preset_id     UUID        REFERENCES packaging_presets(id),
    -- Exact target dimensions (for numeric size tiers or bespoke orders)
    target_diameter_cm      NUMERIC,
    target_height_cm        NUMERIC,
    target_weight_kg        NUMERIC,
    target_portions         INTEGER,
    -- Cost stored whenever the calculator runs — historical record
    last_calculated_cost    NUMERIC,
    last_calculated_at      TIMESTAMPTZ,
    locked                  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);


-- -----------------------------------------------------------------------------
-- SETTINGS
-- Single-row table for app-wide defaults.
-- The application upserts into this table on first run if it is empty.
-- -----------------------------------------------------------------------------

CREATE TABLE settings (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    default_labour_rate NUMERIC     NOT NULL DEFAULT 30.0,   -- EUR per hour
    default_oven_rate   NUMERIC     NOT NULL DEFAULT 2.0,    -- EUR per hour
    default_margin      NUMERIC     NOT NULL DEFAULT 3.0,    -- multiplier on cost
    currency            TEXT        NOT NULL DEFAULT 'EUR',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);


-- =============================================================================
-- REFERENCE DATA — insert the known lookup values
-- =============================================================================

-- Cake codes (from SKUnumbering_system.xlsx)
INSERT INTO cake_codes (code, name) VALUES
    ('LP', 'Lemon Pie'),
    ('CK', 'Carrot Cake'),
    ('CE', 'Chocolate Extravaganza'),
    ('FP', 'Frutas del Bosque'),
    ('PC', 'Pistacho Chocolate'),
    ('FR', 'Fraisier'),
    ('BR', 'Brioche'),
    ('BO', 'Brownie'),
    ('SV', 'Caja San Valentín'),
    ('CB', 'Choco Bites'),
    ('CC', 'Chocolate Crocanti'),
    ('CO', 'Cookie Box'),
    ('GB', 'Gift Box'),
    ('LS', 'Lemon Sponge'),
    ('RV', 'Red Velvet'),
    ('SC', 'Salted Caramel'),
    ('BA', 'Banoffee'),
    ('PA', 'Pavlova'),
    ('DL', 'Dulce de Leche'),
    ('LO', 'Lotus'),
    ('DI', 'Diamantes'),
    ('CW', 'Coffee and Walnut'),
    ('LI', 'Lime Pie');

-- Named size tiers
INSERT INTO size_tiers (code, label, is_numeric, size_type, min_value, max_value, unit) VALUES
    ('BO', 'Bocado ×20',       FALSE, 'portions',  20,   20,   'portions'),
    ('IN', 'Individual ×4',    FALSE, 'portions',  4,    4,    'portions'),
    ('LA', 'Large',            FALSE, 'diameter',  20,   22,   'cm'),
    ('XL', 'XLarge',           FALSE, 'diameter',  24,   26,   'cm'),
    ('XX', 'XXLarge',          FALSE, 'diameter',  28,   30,   'cm'),
    ('DC', 'Desayuno / Caja',  FALSE, 'portions',  NULL, NULL, NULL),
    ('MI', 'Bocado Individual', FALSE, 'portions', 1,    1,    'portions'),
    ('TI', 'Individual',       FALSE, 'portions',  1,    1,    'portions');

-- Price channels
INSERT INTO price_channels (code, label) VALUES
    ('GW', 'General web'),
    ('WS', 'Wholesale'),
    ('MD', 'Mentidero');

-- Default settings row
INSERT INTO settings (default_labour_rate, default_oven_rate, default_margin, currency)
VALUES (30.0, 2.0, 3.0, 'EUR');


-- =============================================================================
-- ROW LEVEL SECURITY
-- Enable RLS on all tables. The Streamlit app connects via the service role
-- key (server-side only, never exposed to the browser), so it bypasses RLS.
-- This protects the data if the anon key is ever accidentally used.
-- =============================================================================

ALTER TABLE cake_codes              ENABLE ROW LEVEL SECURITY;
ALTER TABLE size_tiers              ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_channels          ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingredients             ENABLE ROW LEVEL SECURITY;
ALTER TABLE consumables             ENABLE ROW LEVEL SECURITY;
ALTER TABLE packaging_presets       ENABLE ROW LEVEL SECURITY;
ALTER TABLE packaging_preset_lines  ENABLE ROW LEVEL SECURITY;
ALTER TABLE recipes                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE recipe_ingredient_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE skus                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings                ENABLE ROW LEVEL SECURITY;
