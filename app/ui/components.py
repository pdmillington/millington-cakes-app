# ui/components.py
# =============================================================================
# Shared Streamlit UI components.
#
# These are small, pure-UI functions with no business logic.
# Each replaces a pattern that was copy-pasted across multiple screens.
#
# Usage:
#   from ui.components import missing_prices_warning, cost_breakdown_metrics
#                             weight_estimate_expander
# =============================================================================

import streamlit as st


def missing_prices_warning(missing_prices: list[str]) -> None:
    """
    Show a warning banner if any ingredient prices are absent.

    Parameters
    ----------
    missing_prices:
        List of ingredient names with no cost set, as returned by
        IngredientCostResult.missing_prices from calc_ingredient_cost().
        Does nothing if the list is empty.
    """
    if missing_prices:
        st.warning(
            f"⚠️ Missing prices for: {', '.join(missing_prices)}. "
            "Ingredient cost is understated."
        )


def cost_breakdown_metrics(
    ingredient_cost: float,
    labour_cost:     float,
    oven_cost:       float,
    packaging_cost:  float,
) -> None:
    """
    Render a 4-column metrics row showing the per-unit cost breakdown.

    Displays values to 4 decimal places so small differences between
    ingredients remain visible.
    """
    st.markdown("**Cost breakdown**")
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Ingredients", f"€ {ingredient_cost:.4f}")
    col_b.metric("Labour",      f"€ {labour_cost:.4f}")
    col_c.metric("Oven",        f"€ {oven_cost:.4f}")
    col_d.metric("Packaging",   f"€ {packaging_cost:.4f}")


def weight_estimate_expander(
    ref_weight_g: float,
    notes:        list[str],
    excluded:     list[str],
) -> None:
    """
    Render a collapsible expander showing how the reference weight was estimated.

    Only renders if there is something to show (notes or excluded ingredients).
    Used in the calculator for Individual and Bocado formats where ingredient
    scaling depends on an estimated total recipe weight.

    Parameters
    ----------
    ref_weight_g:
        Estimated total recipe weight in grams.
    notes:
        List of informational notes from db.estimate_recipe_weight().
    excluded:
        List of ingredient names that could not be converted to grams.
    """
    if not notes and not excluded:
        return

    with st.expander("Weight estimate detail"):
        st.caption(f"Estimated recipe weight: {ref_weight_g:.0f}g")
        for note in notes:
            st.caption(f"  {note}")
        if excluded:
            st.warning(
                f"Excluded (unknown unit weight): {', '.join(excluded)}"
            )
