# db.py — database connection and all query functions
# =============================================================================
# All Supabase calls live here. No other file talks to the database directly.
# This means if the database schema changes, there is only one file to update.
#
# Connection:
#   - Locally: reads SUPABASE_URL and SUPABASE_KEY from .env
#   - On Streamlit Cloud: reads from st.secrets
#
# Every function returns plain Python dicts or lists — never raw Supabase
# response objects. This keeps the rest of the app simple.
# =============================================================================

import os
import streamlit as st
from supabase import create_client, Client
from dotenv import load_dotenv
from rapidfuzz import process, fuzz

load_dotenv()

# Helpers

def _normalise_name(name: str) -> str:
    """Strip leading/trailing whitespace and collapse internal spaces."""
    import re
    return re.sub(r'\s+', ' ', name).strip()

def find_similar_names(name: str, existing_names: list[str], 
                        threshold: int = 85) -> list[tuple[str, int]]:
    """
    Return existing names that are suspiciously similar to the proposed name.
    Uses token sort ratio which handles word order differences, e.g.
    'Chocolate Negro 70%' vs '70% Chocolate Negro' would still match.
    Returns a list of (name, score) tuples above the threshold,
    sorted by score descending.
    """
    name_normalised = _normalise_name(name)
    if not name_normalised:
        return []
    results = process.extract(
        name_normalised,
        existing_names,
        scorer=fuzz.token_sort_ratio,
        limit=3,
    )
    # Filter out exact matches (the name itself if editing) and low scores
    return [
        (match, score)
        for match, score, _ in results
        if score >= threshold and match.lower() != name_normalised.lower()
    ]

# =============================================================================
# Recipe weight estimation
# =============================================================================

# Known weights for unit-based ingredients (grams per unit)
_UNIT_WEIGHTS_G = {
    "huevos":    50.0,   # medium egg, net edible weight
    "manzanas":  150.0,  # medium apple
}

# Unit ingredients to silently ignore (only part used, weight negligible,
# or weight not meaningful for costing)
_UNIT_IGNORE = {
    "limones",
    "limas",
    "naranja",
    "vainilla rama",
    "canela en rama",
}


def estimate_recipe_weight(lines: list[dict]) -> dict:
    """
    Estimate the finished weight of a recipe in grams by summing
    ingredient amounts.

    Recipe amounts are ALWAYS in grams or units — pack_unit on the
    ingredient record describes the purchase pack and is irrelevant here.

    Rules:
      - All numeric amounts: add directly as grams
      - Unit ingredients with known weight (eggs, apples): multiply
      - Unit ingredients in _UNIT_IGNORE: skip silently
      - All other unit ingredients: exclude and flag
    """
    total_g  = 0.0
    excluded = []
    notes    = []

    for line in lines:
        ing_name = (line.get("ingredient_name") or "").strip()
        amount   = float(line.get("amount") or 0)

        if not ing_name or amount <= 0:
            continue

        name_lower = ing_name.lower()

        # Check if this is a known unit ingredient
        matched_weight = next(
            (w for key, w in _UNIT_WEIGHTS_G.items()
             if key in name_lower),
            None
        )

        if matched_weight is not None:
            # Unit ingredient with known weight — e.g. eggs
            grams = amount * matched_weight
            total_g += grams
            notes.append(
                f"{ing_name}: {amount:.0f} × "
                f"{matched_weight:.0f}g = {grams:.0f}g"
            )
        elif any(key in name_lower for key in _UNIT_IGNORE):
            # Known unit ingredients to ignore (lemons, limes etc.)
            pass
        elif amount < 20:
            # Small amounts likely to be unit-based (e.g. 1 vanilla pod,
            # 4 gelatine sheets) — flag rather than add raw
            excluded.append(f"{ing_name} ({amount:.0f})")
        else:
            # Treat as grams directly
            total_g += amount

    return {
        "weight_g": round(total_g, 1),
        "excluded": excluded,
        "notes":    notes,
    }

# -----------------------------------------------------------------------------
# Connection
# -----------------------------------------------------------------------------

@st.cache_resource
def get_client() -> Client:
    """
    Create and cache a single Supabase client for the app's lifetime.
    st.cache_resource means this runs once and reuses the connection.
    """
    # Try Streamlit secrets first (production), fall back to .env (local dev)
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except (KeyError, FileNotFoundError):
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        st.error(
            "Database credentials not found. "
            "Add SUPABASE_URL and SUPABASE_KEY to your .env file."
        )
        st.stop()

    return create_client(url, key)


# -----------------------------------------------------------------------------
# Ingredients
# -----------------------------------------------------------------------------

def get_ingredients() -> list[dict]:
    sb = get_client()
    result = sb.table("ingredients").select("*").order("name").execute()
    return result.data or []


def save_ingredient(record: dict) -> dict:
    """Insert or update an ingredient. Computes cost_per_unit before saving."""
    sb = get_client()
    record["name"] = _normalise_name(record.get("name", ""))
    record = _compute_ingredient_cost(record)
    if record.get("id"):
        sb.table("ingredients").update(record).eq("id", record["id"]).execute()
        result = sb.table("ingredients").select("*").eq("id", record["id"]).execute()
    else:
        result = sb.table("ingredients").insert(record).execute()
    return result.data[0] if result.data else {}


def delete_ingredient(ingredient_id: str) -> None:
    sb = get_client()
    sb.table("ingredients").delete().eq("id", ingredient_id).execute()


# Conversion factors to base units (g for weight, ml for volume)
_UNIT_TO_BASE = {
    "g":     1.0,
    "kg":    1000.0,
    "ml":    1.0,
    "l":     1000.0,
    "units": 1.0,   # units stay as units — recipe amounts are also in units
}

def _compute_ingredient_cost(record: dict) -> dict:
    """
    Compute cost_per_unit from pack_price_ex_vat and pack_size.
    Always normalises to cost per base unit:
      - weight ingredients → cost per gram
      - volume ingredients → cost per ml
      - unit ingredients   → cost per unit
    This ensures recipe amounts (always in grams, ml or units)
    multiply correctly regardless of how the pack size was entered.
    """
    try:
        price     = float(record.get("pack_price_ex_vat") or 0)
        size      = float(record.get("pack_size") or 0)
        unit      = record.get("pack_unit") or "g"
        # Convert pack size to base units before dividing
        factor    = _UNIT_TO_BASE.get(unit, 1.0)
        base_size = size * factor
        record["cost_per_unit"] = round(price / base_size, 6) if base_size > 0 else None
    except (TypeError, ValueError):
        record["cost_per_unit"] = None
    return record



# -----------------------------------------------------------------------------
# Consumables
# -----------------------------------------------------------------------------

def get_consumables() -> list[dict]:
    sb = get_client()
    result = sb.table("consumables").select("*").order("name").execute()
    return result.data or []


def save_consumable(record: dict) -> dict:
    """Insert or update a consumable. Computes cost_per_unit before saving."""
    sb = get_client()
    record["name"] = _normalise_name(record.get("name", ""))
    record = _compute_consumable_cost(record)
    if record.get("id"):
        sb.table("consumables").update(record).eq("id", record["id"]).execute()
        result = sb.table("consumables").select("*").eq("id", record["id"]).execute()
    else:
        result = sb.table("consumables").insert(record).execute()
    return result.data[0] if result.data else {}


def delete_consumable(consumable_id: str) -> None:
    sb = get_client()
    sb.table("consumables").delete().eq("id", consumable_id).execute()


def _compute_consumable_cost(record: dict) -> dict:
    """Compute cost_per_unit from pack_price_ex_vat and pack_quantity."""
    try:
        price = float(record.get("pack_price_ex_vat") or 0)
        qty   = float(record.get("pack_quantity") or 0)
        record["cost_per_unit"] = round(price / qty, 6) if qty > 0 else None
    except (TypeError, ValueError):
        record["cost_per_unit"] = None
    return record


# -----------------------------------------------------------------------------
# Recipes
# -----------------------------------------------------------------------------

def get_recipes() -> list[dict]:
    sb = get_client()
    result = sb.table("recipes").select("*").order("name").execute()
    return result.data or []


def get_recipe(recipe_id: str) -> dict:
    sb = get_client()
    result = sb.table("recipes").select("*").eq("id", recipe_id).execute()
    return result.data[0] if result.data else {}


def save_recipe(record: dict) -> dict:
    sb = get_client()
    if record.get("id"):
        sb.table("recipes").update(record).eq("id", record["id"]).execute()
        result = sb.table("recipes").select("*").eq("id", record["id"]).execute()
    else:
        result = sb.table("recipes").insert(record).execute()
    return result.data[0] if result.data else {}


def delete_recipe(recipe_id: str) -> None:
    sb = get_client()
    # ingredient lines cascade-delete automatically (ON DELETE CASCADE)
    sb.table("recipes").delete().eq("id", recipe_id).execute()


# -----------------------------------------------------------------------------
# Recipe ingredient lines
# -----------------------------------------------------------------------------

def get_recipe_lines(recipe_id: str) -> list[dict]:
    """
    Return ingredient lines for a recipe, joined with ingredient names
    and costs so the UI does not need to do extra lookups.
    """
    sb = get_client()
    result = (
        sb.table("recipe_ingredient_lines")
        .select("*, ingredients(name, cost_per_unit, pack_unit)")
        .eq("recipe_id", recipe_id)
        .order("sort_order")
        .execute()
    )
    # Flatten the joined ingredient data for easier use in the UI
    lines = []
    for row in result.data or []:
        ing = row.pop("ingredients", None) or {}
        row["ingredient_name"]     = ing.get("name", "")
        row["ingredient_cost_per_unit"] = ing.get("cost_per_unit")
        row["ingredient_unit"]     = ing.get("pack_unit", "g")
        lines.append(row)
    return lines


def save_recipe_line(record: dict) -> dict:
    sb = get_client()
    if record.get("id"):
        sb.table("recipe_ingredient_lines").update(record).eq("id", record["id"]).execute()
        result = sb.table("recipe_ingredient_lines").select("*").eq("id", record["id"]).execute()
    else:
        result = sb.table("recipe_ingredient_lines").insert(record).execute()
    return result.data[0] if result.data else {}


def delete_recipe_line(line_id: str) -> None:
    sb = get_client()
    sb.table("recipe_ingredient_lines").delete().eq("id", line_id).execute()


def replace_recipe_lines(recipe_id: str, lines: list[dict]) -> None:
    """
    Replace all ingredient lines for a recipe in a single operation.
    Used when saving an edited recipe to avoid partial updates.
    """
    sb = get_client()
    sb.table("recipe_ingredient_lines").delete().eq("recipe_id", recipe_id).execute()
    if lines:
        for i, line in enumerate(lines):
            line["recipe_id"]  = recipe_id
            line["sort_order"] = i
        sb.table("recipe_ingredient_lines").insert(lines).execute()


# -----------------------------------------------------------------------------
# Reference data (cake codes, size tiers, price channels)
# These rarely change so we cache them for the session.
# -----------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def get_cake_codes() -> list[dict]:
    sb = get_client()
    result = sb.table("cake_codes").select("*").order("code").execute()
    return result.data or []


@st.cache_data(ttl=3600)
def get_size_tiers() -> list[dict]:
    sb = get_client()
    result = sb.table("size_tiers").select("*").order("code").execute()
    return result.data or []


@st.cache_data(ttl=3600)
def get_price_channels() -> list[dict]:
    sb = get_client()
    result = sb.table("price_channels").select("*").order("code").execute()
    return result.data or []


# -----------------------------------------------------------------------------
# SKUs
# -----------------------------------------------------------------------------

def get_skus() -> list[dict]:
    sb = get_client()
    result = (
        sb.table("skus")
        .select("*, recipes(name), size_tiers(code, label), price_channels(code, label)")
        .order("sku_code")
        .execute()
    )
    return result.data or []


def save_sku(record: dict) -> dict:
    sb = get_client()
    if record.get("id"):
        sb.table("skus").update(record).eq("id", record["id"]).execute()
        result = sb.table("skus").select("*").eq("id", record["id"]).execute()
    else:
        result = sb.table("skus").insert(record).execute()
    return result.data[0] if result.data else {}


# -----------------------------------------------------------------------------
# Packaging presets
# -----------------------------------------------------------------------------

def get_packaging_presets() -> list[dict]:
    sb = get_client()
    result = sb.table("packaging_presets").select("*").order("name").execute()
    return result.data or []


def get_preset_lines(preset_id: str) -> list[dict]:
    sb = get_client()
    result = (
        sb.table("packaging_preset_lines")
        .select("*, consumables(name, cost_per_unit)")
        .eq("preset_id", preset_id)
        .execute()
    )
    lines = []
    for row in result.data or []:
        con = row.pop("consumables", None) or {}
        row["consumable_name"]      = con.get("name", "")
        row["consumable_cost_per_unit"] = con.get("cost_per_unit")
        lines.append(row)
    return lines


def save_preset(name: str, lines: list[dict]) -> None:
    """Create or replace a packaging preset and its lines."""
    sb = get_client()
    result = sb.table("packaging_presets").insert({"name": name}).execute()
    if not result.data:
        return
    preset_id = result.data[0]["id"]
    for line in lines:
        line["preset_id"] = preset_id
    if lines:
        sb.table("packaging_preset_lines").insert(lines).execute()

# Packaging presets

def update_preset(preset_id: str, name: str, lines: list[dict]) -> None:
    """Update an existing packaging preset name and replace its lines."""
    sb = get_client()
    sb.table("packaging_presets").update({"name": name}).eq("id", preset_id).execute()
    # Replace all lines
    sb.table("packaging_preset_lines").delete().eq("preset_id", preset_id).execute()
    if lines:
        for line in lines:
            line["preset_id"] = preset_id
        sb.table("packaging_preset_lines").insert(lines).execute()


def delete_preset(preset_id: str) -> None:
    """Delete a packaging preset and its lines (cascade handles lines)."""
    sb = get_client()
    sb.table("packaging_presets").delete().eq("id", preset_id).execute()

# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------

def get_settings() -> dict:
    sb = get_client()
    result = sb.table("settings").select("*").limit(1).execute()
    return result.data[0] if result.data else {}


def save_settings(record: dict) -> dict:
    sb = get_client()
    if record.get("id"):
        sb.table("settings").update(record).eq("id", record["id"]).execute()
        result = sb.table("settings").select("*").eq("id", record["id"]).execute()
    else:
        result = sb.table("settings").insert(record).execute()
    return result.data[0] if result.data else {}
