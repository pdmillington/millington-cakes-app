# core/constants.py
# =============================================================================
# Shared constants used across multiple screens.
# Import from here rather than redefining locally.
# =============================================================================


# -----------------------------------------------------------------------------
# VAT
# -----------------------------------------------------------------------------

VAT_RATE = 0.10          # Spanish food VAT (10%)
VAT_MULTIPLIER = 1 + VAT_RATE   # convenience: price_inc = price_ex * VAT_MULTIPLIER


# -----------------------------------------------------------------------------
# Fruit / unit-to-gram conversions
#
# Used when an ingredient is recorded in whole-fruit units (e.g. "3 limones")
# but priced per gram. Any ingredient whose name contains one of these keys
# and whose amount is < 20 is assumed to be a unit count and is converted.
#
# Add new fruits here — this is the single source of truth.
# -----------------------------------------------------------------------------

UNIT_TO_G: dict[str, float] = {
    "limones":  100.0,
    "limas":     67.0,
    "naranja":  180.0,
    "manzanas": 182.0,
}


# -----------------------------------------------------------------------------
# Format display labels
#
# Canonical mapping from internal format keys → human-readable Spanish labels.
# Used in repricing table, catalogue, and any other screen that lists formats.
# -----------------------------------------------------------------------------

FORMAT_DISPLAY: dict[str, str] = {
    "standard":   "Estándar",
    "individual": "Individual",
    "bocado":     "Bocado",
}


# -----------------------------------------------------------------------------
# SKU format tier codes
#
# Maps the product format to the size-segment codes used in SKU strings.
# Used when matching live prices from product_variants to a given format.
# -----------------------------------------------------------------------------

FORMAT_TIER_CODES: dict[str, list[str]] = {
    "Standard":   ["LA", "XL", "XX", "DC"],
    "Individual": ["TI", "IN"],
    "Bocado":     ["MI", "BO"],
}
