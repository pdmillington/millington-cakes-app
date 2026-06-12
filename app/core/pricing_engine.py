# core/pricing_engine.py
# =============================================================================
# Pure calculation functions — no Streamlit imports, no DB calls.
#
# These replace duplicated logic previously spread across:
#   screen_calculator.py, screen_analysis.py, screen_repricing.py
#
# Usage:
#   from core.pricing_engine import calc_ingredient_cost, calc_labour_cost
#   from core.pricing_engine import AnchorPoint, calc_interpolated_cost
# =============================================================================

from __future__ import annotations
import math as _math
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


# =============================================================================
# Intermediate size interpolation
# =============================================================================

@dataclass
class AnchorPoint:
    """
    A known weight anchor for power-law interpolation.
    All cost values are per unit at the given weight.
    """
    label:            str
    weight_g:         float
    labour_cost:      float
    oven_cost:        float
    ingredient_cost:  float
    approved_price:   float | None   # ws or rt ex-VAT; None = no approved price


@dataclass
class InterpolatedCostResult:
    """Result of calc_interpolated_cost()."""
    ingredient_cost:  float
    labour_cost:      float
    oven_cost:        float
    total_cost:       float
    implied_margin:   float | None   # interpolated M = approved_price / anchor_cost
    suggested_price:  float | None   # total_cost × implied_margin, or None
    margin_source:    str            # human-readable description of how margin was derived
    lower_anchor:     AnchorPoint
    upper_anchor:     AnchorPoint
    warnings:         list[str] = field(default_factory=list)


def _power_law_interp(
    w:    float,
    w_lo: float, y_lo: float,
    w_hi: float, y_hi: float,
) -> float:
    """
    Power-law interpolation between anchor points (w_lo, y_lo) and (w_hi, y_hi).

    Fits y = y_lo × (w / w_lo)^α where α = log(y_hi/y_lo) / log(w_hi/w_lo).
    Falls back to linear interpolation when any value is non-positive.
    """
    if w_lo <= 0 or w_hi <= 0 or w_lo == w_hi:
        t = (w - w_lo) / (w_hi - w_lo) if w_hi != w_lo else 0.5
        return y_lo + t * (y_hi - y_lo)
    if y_lo <= 0 or y_hi <= 0:
        # One anchor is zero — degenerate power law; use linear
        t = _math.log(w / w_lo) / _math.log(w_hi / w_lo) if w != w_lo else 0.0
        return y_lo + t * (y_hi - y_lo)
    alpha = _math.log(y_hi / y_lo) / _math.log(w_hi / w_lo)
    return y_lo * (w / w_lo) ** alpha


def calc_interpolated_cost(
    target_weight_g:       float,
    anchors:               list[AnchorPoint],
    ingredient_cost_per_g: float,
) -> InterpolatedCostResult:
    """
    Estimate per-unit cost for a non-standard weight by power-law interpolation
    between the two bracketing anchor points.

    Ingredient cost scales linearly with weight. Labour and oven costs are
    interpolated using a power law fitted to the bracketing anchors. Suggested
    price is derived by power-law interpolating the implied margin
    (approved_price / anchor_total_cost) at anchors that have an approved price.

    Parameters
    ----------
    target_weight_g:
        Target cake weight in grams.
    anchors:
        List of AnchorPoint objects (e.g. bocado, individual, standard).
        Must have at least two entries. Need not be pre-sorted.
    ingredient_cost_per_g:
        Reference ingredient cost per gram (recipe ingredient cost / ref weight).
        Used for linear ingredient scaling independent of labour interpolation.

    Returns
    -------
    InterpolatedCostResult.
    """
    if len(anchors) < 2:
        raise ValueError("calc_interpolated_cost: need at least two anchor points.")

    sorted_anchors = sorted(anchors, key=lambda a: a.weight_g)
    warnings: list[str] = []

    # Find the two bracketing anchors; fall back to outermost pair for extrapolation
    lo = sorted_anchors[0]
    hi = sorted_anchors[-1]
    for i in range(len(sorted_anchors) - 1):
        a_lo, a_hi = sorted_anchors[i], sorted_anchors[i + 1]
        if a_lo.weight_g <= target_weight_g <= a_hi.weight_g:
            lo, hi = a_lo, a_hi
            break

    if target_weight_g < sorted_anchors[0].weight_g:
        warnings.append(
            f"Weight {target_weight_g:.0f}g is below '{sorted_anchors[0].label}' "
            f"anchor ({sorted_anchors[0].weight_g:.0f}g) — extrapolating."
        )
    elif target_weight_g > sorted_anchors[-1].weight_g:
        warnings.append(
            f"Weight {target_weight_g:.0f}g is above '{sorted_anchors[-1].label}' "
            f"anchor ({sorted_anchors[-1].weight_g:.0f}g) — extrapolating."
        )

    # Ingredient cost: strictly linear with weight
    ingredient_cost = ingredient_cost_per_g * target_weight_g

    # Labour and oven: power-law between the two bracketing anchors
    labour_cost = _power_law_interp(
        target_weight_g,
        lo.weight_g, lo.labour_cost,
        hi.weight_g, hi.labour_cost,
    )
    oven_cost = _power_law_interp(
        target_weight_g,
        lo.weight_g, lo.oven_cost,
        hi.weight_g, hi.oven_cost,
    )

    total_cost = ingredient_cost + labour_cost + oven_cost

    # Implied margins at anchors (M = approved_price / anchor_total_cost)
    lo_total  = lo.ingredient_cost + lo.labour_cost + lo.oven_cost
    hi_total  = hi.ingredient_cost + hi.labour_cost + hi.oven_cost
    lo_margin = (lo.approved_price / lo_total) if (lo.approved_price and lo_total > 0) else None
    hi_margin = (hi.approved_price / hi_total) if (hi.approved_price and hi_total > 0) else None

    if lo_margin is not None and hi_margin is not None:
        implied_margin = _power_law_interp(
            target_weight_g,
            lo.weight_g, lo_margin,
            hi.weight_g, hi_margin,
        )
        suggested_price = total_cost * implied_margin
        margin_source   = (
            f"interpolated — {lo.label} {lo_margin:.2f}× → "
            f"{hi.label} {hi_margin:.2f}×"
        )
    elif lo_margin is not None:
        implied_margin  = lo_margin
        suggested_price = total_cost * implied_margin
        margin_source   = (
            f"{lo.label} anchor only ({lo_margin:.2f}×) — no approved price at {hi.label}"
        )
        warnings.append(f"No approved price at '{hi.label}'; using '{lo.label}' margin.")
    elif hi_margin is not None:
        implied_margin  = hi_margin
        suggested_price = total_cost * implied_margin
        margin_source   = (
            f"{hi.label} anchor only ({hi_margin:.2f}×) — no approved price at {lo.label}"
        )
        warnings.append(f"No approved price at '{lo.label}'; using '{hi.label}' margin.")
    else:
        implied_margin  = None
        suggested_price = None
        margin_source   = "no approved prices at anchors"
        warnings.append(
            "Neither anchor has an approved price — apply settings margin manually."
        )

    return InterpolatedCostResult(
        ingredient_cost = ingredient_cost,
        labour_cost     = labour_cost,
        oven_cost       = oven_cost,
        total_cost      = total_cost,
        implied_margin  = implied_margin,
        suggested_price = suggested_price,
        margin_source   = margin_source,
        lower_anchor    = lo,
        upper_anchor    = hi,
        warnings        = warnings,
    )
