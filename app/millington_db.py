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

def get_current_prices(cake_code: str) -> list[dict]:
    """
    Return all current_prices rows for a given cake code prefix.
    e.g. cake_code='LP' returns all LP-* SKUs across all channels.
    """
    sb = get_client()
    result = (
        sb.table("current_prices")
        .select("*")
        .ilike("sku_code", f"{cake_code}-%")
        .order("sku_code")
        .execute()
    )
    return result.data or []

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

def get_ingredient_categories() -> list[dict]:
    sb = get_client()
    result = sb.table("ingredient_categories").select("*").order("label_name_es").execute()
    return result.data or []


def save_ingredient(record: dict) -> dict:
    """Insert or update an ingredient. Computes cost_per_unit before saving."""
    sb = get_client()
    record["name"] = _normalise_name(record.get("name", ""))
    record["updated_at"] = "now()"          
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

def save_ingredient_allergens(record: dict) -> None:
    """Save allergen and ficha fields for an ingredient."""
    sb = get_client()
    allowed = {
        k: v for k, v in record.items()
        if k.startswith("allergen_")
        or k in ("id", "category_id", "allergen_override", "is_sub_recipe")
    }
    allowed["updated_at"] = "now()"
    sb.table("ingredients").update(allowed).eq("id", allowed["id"]).execute()

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

# =============================================================================
# ALLERGEN DECLARATION GENERATOR
# Add these functions to millington_db.py
# =============================================================================
#
# These functions build the legal allergen declaration for a recipe ficha.
#
# EU 1169/2011 / RD 126/2015 terminology — Spanish legal text.
#
# Logic:
#   1. Fetch ingredient lines for the recipe
#   2. For each line, resolve effective allergen values:
#        - If ingredient.allergen_override = TRUE → use ingredient allergen fields
#        - Otherwise → use ingredient.category allergen fields
#   3. If ingredient.is_sub_recipe = TRUE → recurse into matching recipe
#   4. Union all allergen values (max wins: 2 > 1 > 0)
#   5. Add kitchen_may_contain from the recipe
#   6. Return structured Contiene / Puede contener lists
#
# Also generates a draft ingredient label text ordered by weight descending.
# =============================================================================


# ── Allergen field names and Spanish legal display text ───────────────────────
ALLERGEN_FIELDS = [
    "allergen_gluten",
    "allergen_crustacean",
    "allergen_egg",
    "allergen_fish",
    "allergen_peanut",
    "allergen_soy",
    "allergen_milk",
    "allergen_nuts",
    "allergen_celery",
    "allergen_mustard",
    "allergen_sesame",
    "allergen_sulphites",
    "allergen_lupin",
    "allergen_mollusc",
]

ALLERGEN_DISPLAY_ES = {
    "allergen_gluten":     "cereales con gluten y sus derivados",
    "allergen_crustacean": "crustáceos y productos a base de crustáceos",
    "allergen_egg":        "huevo y productos a base de huevo",
    "allergen_fish":       "pescado y productos a base de pescado",
    "allergen_peanut":     "cacahuetes y productos a base de cacahuetes",
    "allergen_soy":        "soja y productos a base de soja",
    "allergen_milk":       "leche y sus derivados (incluida la lactosa)",
    "allergen_nuts":       "frutos de cáscara y productos derivados",
    "allergen_celery":     "apio y productos derivados",
    "allergen_mustard":    "mostaza y productos derivados",
    "allergen_sesame":     "granos de sésamo y productos a base de granos de sésamo",
    "allergen_sulphites":  "dióxido de azufre y sulfitos",
    "allergen_lupin":      "altramuces y productos a base de altramuces",
    "allergen_mollusc":    "moluscos y productos a base de moluscos",
}


def _get_recipe_lines_with_allergens(recipe_id: str) -> list[dict]:
    """
    Fetch ingredient lines for a recipe with full ingredient data:
    allergen fields, category allergen fields, is_sub_recipe, label_name_es.
    """
    sb = get_client()

    # Build allergen field selectors for both ingredient and category
    ing_allergen_fields = ", ".join(
        f"ingredients.{f}" for f in ALLERGEN_FIELDS
    )
    cat_allergen_fields = ", ".join(
        f"ingredient_categories.{f}" for f in ALLERGEN_FIELDS
    )

    result = (
        sb.table("recipe_ingredient_lines")
        .select(
            "amount, sort_order, "
            "ingredients!inner("
            "  id, name, is_sub_recipe, allergen_override, allergen_notes, "
            + ", ".join(ALLERGEN_FIELDS) + ", "
            "  ingredient_categories(id, label_name_es, " +
            ", ".join(ALLERGEN_FIELDS) + ")"
            ")"
        )
        .eq("recipe_id", recipe_id)
        .order("sort_order")
        .execute()
    )

    lines = []
    for row in result.data or []:
        ing = row.pop("ingredients", None) or {}
        cat = ing.pop("ingredient_categories", None) or {}

        entry = {
            "amount":            float(row.get("amount") or 0),
            "sort_order":        row.get("sort_order", 0),
            "ingredient_id":     ing.get("id"),
            "ingredient_name":   ing.get("name", ""),
            "is_sub_recipe":     bool(ing.get("is_sub_recipe")),
            "allergen_override": bool(ing.get("allergen_override")),
            "allergen_notes":    ing.get("allergen_notes"),
            "category_label":    cat.get("label_name_es", ""),
            "category":          cat,
            "ingredient":        ing,
        }
        lines.append(entry)

    return lines


def _find_recipe_by_ingredient_name(name: str) -> dict:
    """
    Find a recipe whose name loosely matches an ingredient marked as sub_recipe.
    Used to expand sub-recipes during allergen calculation.
    """
    sb = get_client()
    result = (
        sb.table("recipes")
        .select("id, name, kitchen_may_contain")
        .ilike("name", f"%{name}%")
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def _effective_allergens(line: dict) -> dict:
    """
    Return the effective allergen values for one ingredient line.
    Uses ingredient fields if allergen_override=True,
    otherwise uses category fields.
    Returns {field: value} for all 14 allergens.
    """
    if line["allergen_override"]:
        source = line["ingredient"]
    else:
        source = line["category"]

    return {
        field: int(source.get(field) or 0)
        for field in ALLERGEN_FIELDS
    }


def _union_allergens(
    accumulated: dict[str, int],
    new_values: dict[str, int]
) -> dict[str, int]:
    """
    Merge two allergen dicts — highest value wins.
    0=no, 1=contiene, 2=puede contener.
    Note: 1 (Contiene) beats 2 (Puede contener) since definite presence
    is more serious than possible presence.
    Special merge rule:
      - If accumulated=0 and new=2 → result=2
      - If accumulated=2 and new=1 → result=1 (Contiene overrides Puede)
      - If accumulated=1 and new=2 → result=1 (keep Contiene)
      - Otherwise max wins
    """
    result = {}
    for field in ALLERGEN_FIELDS:
        a = accumulated.get(field, 0)
        n = new_values.get(field, 0)
        if a == 0:
            result[field] = n
        elif a == 1:
            result[field] = 1  # Contiene always wins
        elif a == 2:
            result[field] = 1 if n == 1 else 2
        else:
            result[field] = max(a, n)
    return result


def get_allergen_declaration(
    recipe_id: str,
    depth: int = 0,
    _visited: set | None = None
) -> dict:
    """
    Build the full allergen declaration for a recipe.

    Returns:
    {
        "contiene":          [list of Spanish legal allergen strings],
        "puede_contener":    [list of Spanish legal allergen strings],
        "accumulated":       {field: 0|1|2} raw values,
        "warnings":          [list of warning strings],
        "ingredient_names":  [list of (label_name_es, amount_g) for label text],
    }

    Args:
        recipe_id: UUID of the recipe to process
        depth:     recursion depth (max 5 to prevent infinite loops)
        _visited:  set of recipe_ids already visited (loop detection)
    """
    if _visited is None:
        _visited = set()

    if depth > 5:
        return {
            "contiene": [], "puede_contener": [], "accumulated": {},
            "warnings": ["⚠️ Profundidad máxima de recursión alcanzada"],
            "ingredient_names": [],
        }

    if recipe_id in _visited:
        return {
            "contiene": [], "puede_contener": [], "accumulated": {},
            "warnings": [f"⚠️ Referencia circular detectada en receta {recipe_id}"],
            "ingredient_names": [],
        }

    _visited.add(recipe_id)

    # Fetch recipe for kitchen_may_contain
    recipe  = get_recipe(recipe_id)
    kitchen = recipe.get("kitchen_may_contain") or ""

    # Fetch ingredient lines with allergen data
    lines    = _get_recipe_lines_with_allergens(recipe_id)
    warnings = []

    # Accumulate allergen values across all ingredients
    accumulated   = {f: 0 for f in ALLERGEN_FIELDS}
    ing_for_label = []  # [(label_name_es, amount_g), ...]

    for line in lines:
        name   = line["ingredient_name"]
        amount = line["amount"]

        if line["is_sub_recipe"]:
            # Find the matching recipe and recurse
            sub_recipe = _find_recipe_by_ingredient_name(name)
            if sub_recipe:
                sub_result = get_allergen_declaration(
                    sub_recipe["id"],
                    depth=depth + 1,
                    _visited=_visited
                )
                accumulated = _union_allergens(
                    accumulated, sub_result["accumulated"]
                )
                warnings.extend(sub_result["warnings"])
                # Sub-recipe ingredient labels come from its expansion
                for sub_ing in sub_result["ingredient_names"]:
                    # Scale amounts by the proportion used
                    ing_for_label.append((
                        sub_ing[0],
                        sub_ing[1] * amount if sub_ing[1] else amount,
                    ))
            else:
                warnings.append(
                    f"⚠️ Sub-receta '{name}' no encontrada — "
                    "alérgenos no calculados para este componente."
                )
        else:
            # Leaf ingredient — get effective allergen values
            if not line["category"] and not line["allergen_override"]:
                warnings.append(
                    f"⚠️ '{name}' sin categoría asignada — "
                    "alérgenos no incluidos."
                )
                continue

            eff = _effective_allergens(line)
            accumulated = _union_allergens(accumulated, eff)

            # Add to label ingredient list
            label_name = (
                line["category_label"]
                if line["category_label"]
                else name
            )
            if label_name:
                ing_for_label.append((label_name, amount))

            # Flag ingredients needing verification
            notes = line.get("allergen_notes") or ""
            if "verificar" in notes.lower() or "needs" in notes.lower():
                warnings.append(
                    f"⚠️ '{name}': {notes}"
                )

    # Build Contiene and Puede contener lists
    contiene       = []
    puede_contener = []

    for field in ALLERGEN_FIELDS:
        val = accumulated.get(field, 0)
        display = ALLERGEN_DISPLAY_ES[field]
        if val == 1:
            contiene.append(display)
        elif val == 2:
            puede_contener.append(display)

    # Add kitchen-level may_contain
    # Parse as comma-separated list and add any new items
    if kitchen:
        kitchen_items = [
            k.strip() for k in kitchen.split(",")
            if k.strip()
        ]
        for item in kitchen_items:
            # Only add if not already covered by ingredient-level flags
            if item.lower() not in " ".join(puede_contener).lower() \
                    and item.lower() not in " ".join(contiene).lower():
                puede_contener.append(item)

    return {
        "contiene":         contiene,
        "puede_contener":   puede_contener,
        "accumulated":      accumulated,
        "warnings":         warnings,
        "ingredient_names": ing_for_label,
    }


def get_ingredient_label_text(recipe_id: str) -> dict:
    """
    Generate a draft ingredient label text for a recipe ficha.

    Returns:
    {
        "label_text":  "Harina de trigo, azúcar, mantequilla, huevo, ...",
        "ordered":     [(label_name_es, total_amount_g), ...] sorted desc,
        "warnings":    [list of warning strings],
        "allergen_fields": {field: label_name_es} for bolding in PDF,
    }

    The label text lists ingredients ordered by weight descending (EU legal
    requirement). Allergen-containing ingredients are noted for bolding.
    """
    declaration = get_allergen_declaration(recipe_id)
    ing_names   = declaration["ingredient_names"]
    warnings    = list(declaration["warnings"])

    if not ing_names:
        return {
            "label_text":     "",
            "ordered":        [],
            "warnings":       warnings + ["Sin ingredientes encontrados."],
            "allergen_fields": {},
        }

    # Aggregate by label name — same label from different ingredients
    # (e.g. egg yolk + egg white both become "huevo")
    aggregated: dict[str, float] = {}
    for label, amount in ing_names:
        aggregated[label] = aggregated.get(label, 0) + (amount or 0)

    # Sort by amount descending
    ordered = sorted(aggregated.items(), key=lambda x: x[1], reverse=True)

    # Build the draft text — comma separated, capitalise first word
    ingredient_list = ", ".join(name for name, _ in ordered)
    if ingredient_list:
        ingredient_list = ingredient_list[0].upper() + ingredient_list[1:]

    # Identify which label names are allergens (for bolding in PDF)
    # Build reverse map: label_name_es -> allergen field(s) it triggers
    # This requires checking each ingredient's category
    sb          = get_client()
    cat_result  = (
        sb.table("ingredient_categories")
        .select("label_name_es, " + ", ".join(ALLERGEN_FIELDS))
        .execute()
    )
    allergen_labels: dict[str, list[str]] = {}
    for cat in cat_result.data or []:
        label = cat["label_name_es"]
        triggered = [
            f for f in ALLERGEN_FIELDS if int(cat.get(f) or 0) == 1
        ]
        if triggered:
            allergen_labels[label] = triggered

    return {
        "label_text":      ingredient_list,
        "ordered":         ordered,
        "warnings":        warnings,
        "allergen_fields": allergen_labels,
    }


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


def save_preset(name: str, lines: list[dict], 
                units_per_pack: int = 1) -> None:
    sb = get_client()
    result = sb.table("packaging_presets").insert({
        "name": name,
        "units_per_pack": units_per_pack
    }).execute()
    if not result.data:
        return
    preset_id = result.data[0]["id"]
    for line in lines:
        line["preset_id"] = preset_id
    if lines:
        sb.table("packaging_preset_lines").insert(lines).execute()


def update_preset(preset_id: str, name: str, lines: list[dict],
                  units_per_pack: int = 1) -> None:
    sb = get_client()
    sb.table("packaging_presets").update({
        "name": name,
        "units_per_pack": units_per_pack
    }).eq("id", preset_id).execute()
    sb.table("packaging_preset_lines").delete().eq(
        "preset_id", preset_id
    ).execute()
    if lines:
        for line in lines:
            line["preset_id"] = preset_id
        sb.table("packaging_preset_lines").insert(lines).execute()


def delete_preset(preset_id: str) -> None:
    sb = get_client()
    sb.table("packaging_presets").delete().eq("id", preset_id).execute()

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
