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
import re
from datetime import date
import streamlit as st
from supabase import create_client, Client
from dotenv import load_dotenv
from rapidfuzz import process, fuzz

load_dotenv()

# SKU pattern embedded in Holded product names e.g. "Cookie Box - CO-03-DC-GW"
_SKU_RE = re.compile(r'\b([A-Z]{2}-\d{2}-[A-Z]{2}-[A-Z]{2,4}(?:-[A-Z]{2})?)\b')
 
# Spanish month name → month number
_MONTHS_ES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
}

# Helpers

def _normalise_name(name: str) -> str:
    """Strip leading/trailing whitespace and collapse internal spaces."""
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
        or k in ("id", "category_id", "allergen_override", "is_sub_recipe", "label_name_es")
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

def get_recipes(include_sub_recipes: bool = False) -> list[dict]:
    """Return recipes. Sub-recipes (intermediate components) are excluded
    by default so they do not appear in pricing, catalogue or calculator."""
    sb = get_client()
    q = sb.table("recipes").select("*").order("name")
    if not include_sub_recipes:
        q = q.eq("is_sub_recipe", False)
    return q.execute().data or []


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
        
def get_all_variants() -> list[dict]:
    """Fetch all product variants in one query — used for sidebar counts."""
    sb = get_client()
    result = (
        sb.table("product_variants")
        .select("id, recipe_id, format, label_approved, channel")
        .execute()
    )
    return result.data or []

def get_variants_for_recipe(recipe_id: str) -> list[dict]:
    sb = get_client()
    result = (
        sb.table("product_variants")
        .select("*")
        .eq("recipe_id", recipe_id)
        .order("format")
        .execute()
    )
    return result.data or []


def save_variant(record: dict) -> dict:
    sb = get_client()
    record["updated_at"] = "now()"
    record.pop("sku_code", None)
    # Timestamp price fields when they are updated
    if "ws_price_ex_vat" in record:
        record["ws_price_updated_at"] = "now()"
    if "rt_price_inc_vat" in record:
        record["rt_price_updated_at"] = "now()"
    if record.get("id"):
        sb.table("product_variants").update(record).eq(
            "id", record["id"]
        ).execute()
        result = sb.table("product_variants").select("*").eq(
            "id", record["id"]
        ).execute()
    else:
        result = sb.table("product_variants").insert(record).execute()
    return result.data[0] if result.data else {}


def delete_variant(variant_id: str) -> None:
    sb = get_client()
    sb.table("product_variants").delete().eq("id", variant_id).execute()
    
def get_all_variants_full() -> list[dict]:
    """Fetch all variants with working, approved price fields and size info."""
    sb = get_client()
    result = (
        sb.table("product_variants")
        .select(
            "id, recipe_id, format, channel, size_description, "
            "ref_diameter_cm, ref_height_cm, "
            "ws_price_ex_vat, ws_price_approved, ws_price_approved_at, "
            "rt_price_inc_vat, rt_price_approved, rt_price_approved_at"
        )
        .execute()
    )
    return result.data or []

def get_ingredient_lines_all() -> list[dict]:
    """
    Return every recipe_ingredient_lines row joined with its ingredient's
    name, cost_per_unit and pack_unit.
 
    Used by screen_kpis.py to compute estimated ingredient spend from
    Holded sales data without N+1 queries.
 
    Returns a list of dicts with keys:
      recipe_id, amount, ingredient_id, ingredient_name,
      cost_per_unit, pack_unit
    """
    sb     = get_client()
    result = (
        sb.table("recipe_ingredient_lines")
        .select(
            "recipe_id, amount, "
            "ingredients(id, name, cost_per_unit, pack_unit)"
        )
        .execute()
    )
    rows = []
    for row in result.data or []:
        ing = row.pop("ingredients", None) or {}
        row["ingredient_id"]   = ing.get("id",             "")
        row["ingredient_name"] = ing.get("name",           "Unknown")
        row["cost_per_unit"]   = ing.get("cost_per_unit")
        row["pack_unit"]       = ing.get("pack_unit",      "")
        rows.append(row)
    return rows

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
    "allergen_crustacean": "crustáceos y sus derivados",
    "allergen_egg":        "huevo y derivados",
    "allergen_fish":       "pescado y sus derivados",
    "allergen_peanut":     "cacahuetes y sus derivados",
    "allergen_soy":        "soja y sus derivados",
    "allergen_milk":       "leche y derivados lácteos",
    "allergen_nuts":       "frutos de cáscara y sus derivados",
    "allergen_celery":     "apio y sus derivados",
    "allergen_mustard":    "mostaza y sus derivados",
    "allergen_sesame":     "granos de sésamo y sus derivados",
    "allergen_sulphites":  "dióxido de azufre y sulfitos",
    "allergen_lupin":      "altramuces y sus derivados",
    "allergen_mollusc":    "moluscos y sus derivados",
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
            "  id, name, label_name_es, is_sub_recipe, allergen_override, allergen_notes, "
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

        ing_label = (
            ing.get("label_name_es")
            or cat.get("label_name_es")
            or ing.get("name", "")
        )

        entry = {
            "amount":            float(row.get("amount") or 0),
            "sort_order":        row.get("sort_order", 0),
            "ingredient_id":     ing.get("id"),
            "ingredient_name":   ing.get("name", ""),
            "label_name_es":     ing_label,
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
                line.get("label_name_es")
                or line.get("category_label")
                or name
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


# =============================================================================
# Price approval and client pricing
# =============================================================================

def get_all_variants_full_with_approval() -> list[dict]:
    """Fetch all variants with working and approved price fields."""
    sb = get_client()
    result = (
        sb.table("product_variants")
        .select(
            "id, recipe_id, format, channel, "
            "ws_price_ex_vat, ws_price_approved, ws_price_approved_at, "
            "rt_price_inc_vat, rt_price_approved, rt_price_approved_at, "
            "ws_price_updated_at, rt_price_updated_at, "
            "size_description"
        )
        .execute()
    )
    return result.data or []


def approve_variant_prices(
    variant_id: str,
    ws_price: float | None,
    rt_price: float | None
) -> None:
    """Copy working prices to approved fields with current timestamp."""
    sb     = get_client()
    record = {"id": variant_id, "updated_at": "now()"}
    if ws_price is not None:
        record["ws_price_approved"]    = ws_price
        record["ws_price_approved_at"] = "now()"
    if rt_price is not None:
        record["rt_price_approved"]    = rt_price
        record["rt_price_approved_at"] = "now()"
    sb.table("product_variants").update(record).eq(
        "id", variant_id
    ).execute()


def get_client_prices() -> list[dict]:
    """Fetch all client-specific prices with variant and recipe info."""
    sb = get_client()
    result = (
        sb.table("client_prices")
        .select(
            "*, "
            "product_variants(format, size_description, "
            "recipes(name))"
        )
        .order("client_name")
        .execute()
    )
    rows = []
    for row in result.data or []:
        variant = row.pop("product_variants", None) or {}
        recipe  = variant.pop("recipes", None) or {}
        fmt     = variant.get("format", "")
        fmt_label = {"standard": "Estándar", "individual": "Individual",
                     "bocado": "Bocado"}.get(fmt, fmt)
        row["variant_label"] = (
            f"{recipe.get('name', '')} — {fmt_label}"
        )
        rows.append(row)
    return rows


def save_client_price(record: dict) -> dict:
    """Save a client-specific price (upsert on client_name + variant_id)."""
    sb = get_client()
    # Check if exists
    existing = (
        sb.table("client_prices")
        .select("id")
        .eq("client_name", record["client_name"])
        .eq("variant_id",  record["variant_id"])
        .execute()
    )
    if existing.data:
        record["id"] = existing.data[0]["id"]
        sb.table("client_prices").update(record).eq(
            "id", record["id"]
        ).execute()
        result = sb.table("client_prices").select("*").eq(
            "id", record["id"]
        ).execute()
    else:
        result = sb.table("client_prices").insert(record).execute()
    return result.data[0] if result.data else {}


def delete_client_price(price_id: str) -> None:
    sb = get_client()
    sb.table("client_prices").delete().eq("id", price_id).execute()


def get_client_prices_for_catalogue(client_name: str) -> dict[str, dict]:
    """
    Return client-specific prices for a named client, keyed by variant_id.
    Used in catalogue generation to override standard approved prices.
    """
    sb = get_client()
    today = str(__import__("datetime").date.today())
    result = (
        sb.table("client_prices")
        .select("variant_id, ws_price_ex_vat, rt_price_inc_vat")
        .eq("client_name", client_name)
        .lte("valid_from", today)
        .or_(f"valid_until.is.null,valid_until.gte.{today}")
        .execute()
    )
    return {r["variant_id"]: r for r in (result.data or [])}

# -----------------------------------------------------------------------------
# Holded year cache
# Persistent cache for Holded invoice data. One row per calendar year.
# Historical years are written once and never updated automatically.
# -----------------------------------------------------------------------------

def get_holded_cache_index() -> list[dict]:
    """
    Return one summary row per cached year:
      { year, invoice_count, synced_at }
    Used by holded_api.py to know which years are already stored.
    """
    sb     = get_client()
    result = (
        sb.table("holded_year_cache")
        .select("year, synced_at")
        .order("year")
        .execute()
    )
    return result.data or []


def get_holded_year_cache(year: int) -> list[dict]:
    """
    Return the cached list of invoice dicts for a given year.
    Returns [] if the year is not cached.
    """
    sb     = get_client()
    result = (
        sb.table("holded_year_cache")
        .select("invoices")
        .eq("year", year)
        .limit(1)
        .execute()
    )
    if not result.data:
        return []
    return result.data[0].get("invoices") or []


def save_holded_year_cache(year: int, invoices: list[dict],
                           cache_version: int = 1) -> None:
    """
    Upsert invoice data for a given year into the Supabase cache.
    Called once per historical year; never called for the current year.
    """
    sb = get_client()
    sb.table("holded_year_cache").upsert({
        "year":      year,
        "invoices":  invoices,
        "synced_at": "now()",
    }).execute()

# =============================================================================
# Excel parsing helpers
# =============================================================================
 
def _parse_month_header(cell_value: str) -> tuple[int, int] | None:
    """
    Parse a Holded month column header like 'Enero 26' or 'Febrero 2026'
    into (month_number, year). Returns None if not parseable.
    """
    if not cell_value:
        return None
    parts = str(cell_value).lower().split()
    if len(parts) < 2:
        return None
    month_name = parts[0]
    month = _MONTHS_ES.get(month_name)
    if not month:
        return None
    try:
        year_str = parts[1]
        year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
        return month, year
    except (ValueError, IndexError):
        return None
 
 
def _extract_sku(product_name: str) -> tuple[str, str | None]:
    """
    If the product name contains an embedded SKU (e.g. 'Cookie Box - CO-03-DC-GW'),
    return (clean_name, sku). Otherwise return (product_name, None).
    """
    m = _SKU_RE.search(product_name)
    if not m:
        return product_name.strip(), None
    sku = m.group(1)
    # Remove the SKU and any trailing separator from the name
    clean = re.sub(r'\s*[-–]\s*' + re.escape(sku) + r'\s*$', '', product_name).strip()
    return clean, sku
 
 
def parse_ventas_excel(file_bytes: bytes) -> list[dict]:
    """
    Parse a Holded 'Ventas' Excel export (monthly revenue totals).
 
    Returns a list of dicts, one per month with data:
      { year, month, ventas_ex_vat, tax, total_inc_vat, units }
 
    Months with ventas_ex_vat == 0 are skipped (future months in the export).
    """
    import openpyxl
    from io import BytesIO
 
    wb = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
 
    # Build column index → (month, year) map from the header row
    col_map: dict[int, tuple[int, int]] = {}
    header_row_idx = None
 
    rows = list(ws.iter_rows(values_only=True))
    for row_idx, row in enumerate(rows):
        for col_idx, cell in enumerate(row):
            parsed = _parse_month_header(str(cell) if cell else "")
            if parsed:
                col_map[col_idx] = parsed
                header_row_idx = row_idx
        if col_map:
            break
 
    if not col_map:
        raise ValueError("No se encontró la fila de cabecera de meses en el fichero de Ventas.")
 
    # Extract metric rows
    metric_map = {
        'ventas':    'ventas_ex_vat',
        'impuestos': 'tax',
        'total':     'total_inc_vat',
        'unidades':  'units',
    }
 
    # {(year, month): {metric: value}}
    data: dict[tuple, dict] = {}
 
    for row in rows[header_row_idx + 1:]:
        if not row or not row[0]:
            continue
        label = str(row[0]).strip().lower()
        field = metric_map.get(label)
        if not field:
            continue
        for col_idx, (month, year) in col_map.items():
            if col_idx >= len(row):
                continue
            val = row[col_idx]
            try:
                val = float(val or 0)
            except (TypeError, ValueError):
                val = 0.0
            key = (year, month)
            if key not in data:
                data[key] = {'year': year, 'month': month,
                             'ventas_ex_vat': 0.0, 'tax': 0.0,
                             'total_inc_vat': 0.0, 'units': 0.0}
            data[key][field] = val
 
    # Only return months that have actual revenue (skip future zero months)
    today = date.today()
    return [
        v for v in data.values()
        if v['ventas_ex_vat'] != 0 or v['total_inc_vat'] != 0
        if not (v['year'] == today.year and v['month'] == today.month)
    ]
 
def parse_productos_excel(file_bytes: bytes) -> list[dict]:
    """
    Parse a Holded 'Ventas por producto' Excel export (units per product per month).
 
    Returns a list of dicts:
      { year, month, product_name, sku (or None), units }
 
    Rows with zero units across all months are skipped.
    'Total' row is skipped.
    """
    import openpyxl
    from io import BytesIO
 
    wb = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
 
    rows = list(ws.iter_rows(values_only=True))
 
    # Find header row
    col_map: dict[int, tuple[int, int]] = {}
    header_row_idx = None
 
    for row_idx, row in enumerate(rows):
        for col_idx, cell in enumerate(row):
            parsed = _parse_month_header(str(cell) if cell else "")
            if parsed:
                col_map[col_idx] = parsed
                header_row_idx = row_idx
        if col_map:
            break
 
    if not col_map:
        raise ValueError("No se encontró la fila de cabecera de meses en el fichero de Productos.")
 
    results = []
    skip_labels = {'total', 'informe creado'}
 
    for row in rows[header_row_idx + 1:]:
        if not row or not row[0]:
            continue
        raw_name = str(row[0]).strip()
        if not raw_name:
            continue
        if any(raw_name.lower().startswith(s) for s in skip_labels):
            continue
 
        clean_name, sku = _extract_sku(raw_name)
 
        for col_idx, (month, year) in col_map.items():
            if col_idx >= len(row):
                continue
            val = row[col_idx]
            try:
                units = float(val or 0)
            except (TypeError, ValueError):
                units = 0.0
            if units == 0:
                continue
            results.append({
                'year':         year,
                'month':        month,
                'product_name': clean_name,
                'sku':          sku,
                'units':        units,
            })
 
    today = date.today()
    
    # Deduplicate by (year, month, product_name) — sum units if duplicate
    deduped: dict[tuple, dict] = {}
    for r in results:
        if r['year'] == today.year and r['month'] == today.month:
            continue
        key = (r['year'], r['month'], r['product_name'])
        if key in deduped:
            deduped[key]['units'] += r['units']
        else:
            deduped[key] = r
    return list(deduped.values())

 
# =============================================================================
# Supabase read/write — monthly revenue
# =============================================================================
 
def upsert_monthly_revenue(rows: list[dict]) -> int:
    """
    Upsert monthly revenue rows into holded_monthly_revenue.
    Returns number of rows upserted.
    """
    if not rows:
        return 0
    sb = get_client()
    payload = [
        {
            'year':          r['year'],
            'month':         r['month'],
            'ventas_ex_vat': r['ventas_ex_vat'],
            'tax':           r['tax'],
            'total_inc_vat': r['total_inc_vat'],
            'units':         r['units'],
            'uploaded_at':   'now()',
        }
        for r in rows
    ]
    sb.table('holded_monthly_revenue').upsert(payload).execute()
    return len(payload)
 
 
def get_monthly_revenue(year: int | None = None) -> list[dict]:
    """
    Return all monthly revenue rows, optionally filtered by year.
    Sorted by year, month ascending.
    """
    sb = get_client()
    q  = sb.table('holded_monthly_revenue').select('*').order('year').order('month')
    if year is not None:
        q = q.eq('year', year)
    return q.execute().data or []
 
 
def upsert_monthly_products(rows: list[dict]) -> int:
    """
    Upsert monthly product rows into holded_monthly_products.
    Returns number of rows upserted.
    """
    if not rows:
        return 0
    sb = get_client()
    payload = [
        {
            'year':         r['year'],
            'month':        r['month'],
            'product_name': r['product_name'],
            'sku':          r.get('sku'),
            'units':        r['units'],
            'uploaded_at':  'now()',
        }
        for r in rows
    ]
    sb.table('holded_monthly_products').upsert(payload).execute()
    return len(payload)
 
 
def get_monthly_products(year: int | None = None,
                         month: int | None = None) -> list[dict]:
    """
    Return product rows, optionally filtered by year and/or month.
    """
    sb = get_client()
    q  = (sb.table('holded_monthly_products')
            .select('*')
            .order('year').order('month').order('units', desc=True))
    if year  is not None: q = q.eq('year',  year)
    if month is not None: q = q.eq('month', month)
    return q.execute().data or []
 
 
def get_upload_status() -> dict:
    """
    Return a summary of what data has been uploaded:
      {
        'months':      [(year, month), ...],   # all uploaded months
        'latest_year': int | None,
        'latest_month': int | None,
      }
    """
    sb   = get_client()
    rows = (sb.table('holded_monthly_revenue')
              .select('year, month, uploaded_at')
              .order('year', desc=True).order('month', desc=True)
              .execute().data or [])
    months = [(r['year'], r['month']) for r in rows]
    return {
        'months':       months,
        'latest_year':  rows[0]['year']  if rows else None,
        'latest_month': rows[0]['month'] if rows else None,
        'latest_upload': rows[0]['uploaded_at'] if rows else None,
    }
 
    
# =============================================================================
# holded_products — inventory / product catalogue
# =============================================================================
 
def parse_inventory_excel(file_bytes: bytes) -> list[dict]:
    import openpyxl
    from io import BytesIO

    wb   = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(values_only=True))

    header_idx = None
    for i, row in enumerate(rows):
        if row and str(row[0]).strip().upper() == 'SKU':
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("No se encontró la fila de cabecera 'SKU' en el fichero.")

    st.write(f"Header found at row {header_idx}")
    st.write(f"First 3 data rows: {rows[header_idx+1:header_idx+4]}")

    SKU_RE = re.compile(
        r'^[A-Z]{2}-?\d{2}-?[A-Z]{2}-?[A-Z]{2,4}(?:-[A-Z]{2,4})?$'
    )

    def _normalise_sku(raw: str) -> str | None:
        if not raw:
            return None
        s = raw.strip().upper()
        if SKU_RE.match(s):
            return s
        m = re.match(r'^([A-Z]{2})(\d{2})([A-Z]{2})([A-Z]{2,4})(?:([A-Z]{2,4}))?$', s)
        if m:
            parts = [p for p in m.groups() if p]
            return '-'.join(parts)
        return None

    seen: dict[tuple, dict] = {}
    skip_names = {'informe creado', 'none', ''}

    for row in rows[header_idx + 1:]:
        if not row or not row[0]:
            continue
        raw_sku  = str(row[0]).strip() if row[0] else ''
        raw_name = str(row[1]).strip() if row[1] else ''

        if not raw_name or any(raw_name.lower().startswith(s) for s in skip_names):
            continue

        sku = _normalise_sku(raw_sku)
        if not sku:
            continue

        try:
            price = float(row[4]) if row[4] and str(row[4]) not in ('-', 'None') else None
        except (TypeError, ValueError):
            price = None

        key = (sku, raw_name)
        if key not in seen or (price and (seen[key]['price_ex_vat'] or 0) < price):
            seen[key] = {
                'sku':          sku,
                'name':         raw_name,
                'price_ex_vat': price,
            }

    print(f"Parsed {len(seen)} products, first few: {list(seen.values())[:3]}")
    result = list(seen.values())
    st.write(f"DEBUG: Parsed {len(result)} products")
    if result:
        st.write(f"First product: {result[0]}")
    return result
 
 
def upsert_holded_products(rows: list[dict]) -> int:
    """Upsert product catalogue rows. Returns count upserted."""
    if not rows:
        return 0
    sb = get_client()
    print(f"upserting {len(rows)} rows, first: {rows[0]}")
    payload = [
        {
            'sku':          r['sku'],
            'name':         r['name'],
            'price_ex_vat': r.get('price_ex_vat'),
            'uploaded_at':  'now()',
        }
        for r in rows
    ]
    result = sb.table('holded_products').upsert(payload).execute()
    print(f"Result: {result}")
    return len(payload)
 
 
def get_holded_products() -> list[dict]:
    """Return all active products: [{sku, name, price_ex_vat}]."""
    sb = get_client()
    return (
        sb.table('holded_products')
        .select('sku, name, price_ex_vat')
        .eq('active', True)
        .order('name')
        .execute()
        .data or []
    )
 
 
def get_name_to_sku_map() -> dict[str, str]:
    """
    Return {product_name: sku} for all active products.
 
    When multiple SKUs share the same name (e.g. Roscón large with different
    fillings), the first SKU alphabetically is used and the ambiguous names
    are stored in st.session_state['_holded_ambiguous_names'] so the UI
    can surface a warning.
    """
 
    products   = get_holded_products()
    result:    dict[str, str]       = {}
    ambiguous: dict[str, list[str]] = {}
 
    for p in sorted(products, key=lambda x: x['sku']):
        name = p['name']
        if name in result:
            ambiguous.setdefault(name, [result[name]]).append(p['sku'])
        else:
            result[name] = p['sku']
 
    st.session_state['_holded_ambiguous_names'] = ambiguous
    return result
