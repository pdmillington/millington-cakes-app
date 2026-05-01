# core/pricing_engine.py
# =============================================================================
# Pure calculation functions — no Streamlit imports, no DB calls.
#
# These replace duplicated logic previously spread across:
#   screen_calculator.py, screen_analysis.py, screen_repricing.py
#
# Usage:
#   from core.pricing_engine import calc_ingredient_cost, calc_labour_cost
# =============================================================================

from __future__ import annotations
from dataclasses import dataclass, field
from .constants import UNIT_TO_G
from .settings import AppSettings


# =============================================================================
# Result dataclasses
# =============================================================================

@dataclass
class IngredientCostResult:
    """
    Result of calc_ingredient_cost().

    `total` is the RAW cost at reference scale (scale factor NOT applied).
    Callers multiply by their own scale:
        ingredient_cost = result.total * scale
    """
    total:          float
    breakdown:      list[dict]   # one dict per ingredient line, for tables/charts
    missing_prices: list[str]    # ingredient names with no cost set


@dataclass
class LabourCostResult:
    """
    Result of calc_labour_cost().

    Intermediate values (qty_factor, prep_per_unit, oven_per_unit) are exposed
    so the calculator's detail expander can display the full working.
    """
    labour_cost:   float
    oven_cost:     float
    qty_factor:    float
    prep_per_unit: float
    oven_per_unit: float


# =============================================================================
# Ingredient cost
# =============================================================================

def calc_ingredient_cost(
    lines:   list[dict],
    ing_map: dict,
) -> IngredientCostResult:
    """
    Calculate raw ingredient cost at reference scale (scale = 1.0).

    Parameters
    ----------
    lines:
        Recipe ingredient lines as returned by db.get_recipe_lines().
    ing_map:
        Dict mapping ingredient name → ingredient record, e.g.:
        {i["name"]: i for i in db.get_ingredients()}

    Returns
    -------
    IngredientCostResult with total, breakdown, and missing_prices.
    The caller is responsible for multiplying total by the appropriate
    size/format scale factor before adding to the unit cost.
    """
    total:          float      = 0.0
    breakdown:      list[dict] = []
    missing_prices: list[str]  = []

    for line in lines:
        ing_name  = line.get("ingredient_name", "")
        amount    = float(line.get("amount") or 0)
        ing       = ing_map.get(ing_name, {})
        cpu       = ing.get("cost_per_unit")
        pack_unit = (ing.get("pack_unit") or "g").lower()

        if cpu:
            eff_amount = amount

            # If the ingredient is bought by weight (g/kg) but the recipe
            # records a unit count (e.g. "3 limones"), convert to grams.
            # The < 20 guard prevents e.g. "150g of lemon juice" being
            # misread as 150 lemons.
            if pack_unit in ("kg", "g"):
                name_lower  = ing_name.lower()
                unit_weight = next(
                    (w for key, w in UNIT_TO_G.items() if key in name_lower),
                    None,
                )
                if unit_weight and amount < 20:
                    eff_amount = amount * unit_weight

            line_cost = cpu * eff_amount
            total    += line_cost
            breakdown.append({
                "name":      ing_name,
                "amount":    amount,
                "unit":      pack_unit,
                "cpu":       cpu,
                "line_cost": line_cost,
            })

        elif ing_name:
            missing_prices.append(ing_name)

    return IngredientCostResult(
        total=total,
        breakdown=breakdown,
        missing_prices=missing_prices,
    )


# =============================================================================
# Labour cost
# =============================================================================

def calc_labour_cost(
    batch_size:         int,
    ref_batch_size:     float,
    prep_hours:         float,
    oven_hours:         float,
    s:                  AppSettings,
    size_labour_factor: float = 1.0,
) -> LabourCostResult:
    """
    Calculate labour and oven cost per unit using power-law batch scaling.

    The scaling formula is:
        qty_factor = (batch_size / ref_batch_size) ^ labour_power / batch_size

    A larger batch reduces cost per unit; the power exponent controls
    how steeply. size_labour_factor adjusts prep time for non-reference
    cake sizes (used in the calculator for diameter scaling; pass 1.0
    for individual/bocado formats and for the analysis screen).

    Parameters
    ----------
    batch_size:
        The production run size to cost against (e.g. ws_batch_large).
    ref_batch_size:
        The batch size at which prep/oven hours were measured.
    prep_hours:
        Reference prep hours for ref_batch_size units.
    oven_hours:
        Reference oven hours for ref_batch_size units.
    s:
        AppSettings instance (supplies labour_power, default_labour_rate,
        default_oven_rate).
    size_labour_factor:
        Ratio of target size to reference size for diameter-scaled cakes.
        Defaults to 1.0 (no size adjustment).

    Returns
    -------
    LabourCostResult with labour_cost, oven_cost, and intermediate values.
    """
    if ref_batch_size > 0:
        qty_factor = (
            (batch_size / ref_batch_size) ** s.labour_power
        ) / batch_size
    else:
        qty_factor = 1.0 / max(batch_size, 1)

    prep_per_unit = prep_hours * qty_factor * size_labour_factor
    oven_per_unit = oven_hours * qty_factor

    return LabourCostResult(
        labour_cost   = prep_per_unit * s.default_labour_rate,
        oven_cost     = oven_per_unit * s.default_oven_rate,
        qty_factor    = qty_factor,
        prep_per_unit = prep_per_unit,
        oven_per_unit = oven_per_unit,
    )
