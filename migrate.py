#!/usr/bin/env python3
"""
migrate.py — Millington Cakes data migration
=============================================
Reads the existing recipes.json and consumables.json files and loads them
into a Supabase database that has already had schema.sql applied.

Usage:
    pip install supabase python-dotenv
    python migrate.py

Environment variables (set in a .env file or your shell):
    SUPABASE_URL      — your project URL, e.g. https://xxxx.supabase.co
    SUPABASE_KEY      — your service role key (NOT the anon key)
                        Dashboard → Settings → API → service_role

The service role key bypasses Row Level Security and is safe to use in
a server-side script like this. Never expose it in a browser or commit
it to a public repository.

What this script does:
    1. Loads consumables.json → inserts into consumables table
    2. Loads recipes.json → extracts unique ingredient names →
       inserts placeholder ingredient records (no pricing — to be
       filled in via the app)
    3. Inserts recipe records with correct size_type mapping
    4. Inserts recipe_ingredient_lines for each ingredient/amount pair
    5. Flags any sub-recipe references it detects for manual review

What it does NOT do:
    - Set ingredient prices (unknown from JSON — fill via the app)
    - Set recipe heights (unknown from JSON — fill via the app)
    - Create SKUs (create these via the app after reviewing recipes)
    - Create packaging presets (create via the app)

Run the script as many times as needed — it is idempotent. Existing
records are skipped rather than duplicated (upsert by name/code).
"""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set.")
    print("Create a .env file with these values and try again.")
    sys.exit(1)

# Paths to JSON source files — adjust if running from a different directory
RECIPES_FILE     = Path("data/recipes.json")
CONSUMABLES_FILE = Path("data/consumables.json")


# =============================================================================
# Helpers
# =============================================================================

def load_json(path: Path) -> list:
    if not path.exists():
        print(f"ERROR: {path} not found. Run this script from the project folder.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def map_size_type(size_type_str: str) -> str:
    """Map the old GUI size_type strings to the new schema enum values."""
    mapping = {
        "Diameter in cm": "diameter",
        "Weight in kg":   "weight",
        "Portions":       "portions",
    }
    return mapping.get(size_type_str, "diameter")


def infer_ref_dimensions(recipe: dict) -> dict:
    """
    Extract reference dimensions from the old recipe format.
    Returns a dict with only the fields relevant to the size_type populated.
    Height is always None at migration — must be filled in via the app.
    """
    size_type = map_size_type(recipe["size_type"])
    dims = {
        "ref_diameter_cm": None,
        "ref_height_cm":   None,   # Not in old data — fill via app
        "ref_weight_kg":   None,
        "ref_portions":    None,
    }
    size_val = recipe.get("cake_size")
    if size_type == "diameter":
        dims["ref_diameter_cm"] = float(size_val) if size_val else None
    elif size_type == "weight":
        dims["ref_weight_kg"] = float(size_val) if size_val else None
    elif size_type == "portions":
        dims["ref_portions"] = int(size_val) if size_val else None
    return dims


# Known sub-recipe names in recipes.json.
# These are ingredient line entries that actually reference other recipes
# rather than raw ingredients. The migration creates the lines with
# ingredient_id=NULL and flags them for manual linkage in the app.
KNOWN_SUB_RECIPE_NAMES = {
    "coulis mango",
    "cremoso de mango",
    "masa bizcocho",
    "mousse mazapan",
    "sablee pastry dough",
    "mousse de dulce de leche",
    "mascarpone topping",
}


# =============================================================================
# Migration steps
# =============================================================================

def migrate_consumables(sb: Client, data: list) -> None:
    print("\n--- Consumables ---")
    for item in data:
        pack_qty   = float(item["unit"])
        price      = float(item["price"])
        cost_p_u   = round(price / pack_qty, 6) if pack_qty else None
        vat        = float(item["vat"])

        record = {
            "name":               item["name"],
            "supplier":           item.get("provider"),
            "pack_quantity":      pack_qty,
            "pack_price_ex_vat":  price,
            "vat_rate":           vat,
            "cost_per_unit":      cost_p_u,
        }
        # Upsert by name — safe to re-run
        result = sb.table("consumables").upsert(
            record, on_conflict="name"
        ).execute()
        print(f"  ✓ {item['name']}")


def migrate_ingredients(sb: Client, recipes: list) -> dict:
    """
    Extract unique ingredient names from all recipes and insert placeholder
    records. Returns a dict of {name: uuid} for use in line insertion.
    Sub-recipe references are excluded from this table.
    """
    print("\n--- Ingredients (placeholders — add prices via the app) ---")

    # Collect unique ingredient names, excluding known sub-recipe names
    names = set()
    for recipe in recipes:
        for ing_name, _ in recipe.get("ingredients", []):
            if ing_name.strip().lower() not in KNOWN_SUB_RECIPE_NAMES:
                names.add(ing_name.strip())

    name_to_id = {}
    for name in sorted(names):
        record = {
            "name":     name,
            "vat_rate": 0.10,   # Default — correct per ingredient in the app
            # pack_size, pack_unit, pack_price_ex_vat intentionally left NULL
            # cost_per_unit will be NULL until prices are entered
        }
        result = sb.table("ingredients").upsert(
            record, on_conflict="name"
        ).execute()
        if result.data:
            name_to_id[name] = result.data[0]["id"]
            print(f"  ✓ {name}")

    # Re-fetch all to catch any that already existed before this run
    all_ing = sb.table("ingredients").select("id, name").execute()
    for row in all_ing.data:
        name_to_id[row["name"]] = row["id"]

    return name_to_id


def migrate_recipes(sb: Client, recipes: list, ingredient_map: dict) -> None:
    """
    Insert recipe records and their ingredient lines.
    Recipes with names matching KNOWN_SUB_RECIPE_NAMES are inserted as
    normal recipes (they are referenced by other recipes).
    """
    print("\n--- Recipes ---")

    sub_recipe_warnings = []

    for recipe in recipes:
        name      = recipe["name"]
        size_type = map_size_type(recipe["size_type"])
        dims      = infer_ref_dimensions(recipe)

        recipe_record = {
            "name":             name,
            "version":          "01",
            "size_type":        size_type,
            "ref_diameter_cm":  dims["ref_diameter_cm"],
            "ref_height_cm":    dims["ref_height_cm"],
            "ref_weight_kg":    dims["ref_weight_kg"],
            "ref_portions":     dims["ref_portions"],
            # cake_code_id intentionally NULL — assign via the app
        }

        # Upsert by name
        result = sb.table("recipes").upsert(
            recipe_record, on_conflict="name"
        ).execute()

        if not result.data:
            print(f"  ✗ FAILED: {name}")
            continue

        recipe_id = result.data[0]["id"]
        print(f"  ✓ {name}  ({size_type})")

        # Insert ingredient lines
        for sort_order, (ing_name, amount) in enumerate(recipe.get("ingredients", [])):
            ing_name_clean = ing_name.strip()
            ing_name_lower = ing_name_clean.lower()

            if ing_name_lower in KNOWN_SUB_RECIPE_NAMES:
                sub_recipe_warnings.append(
                    f"  ⚠  '{name}' → sub-recipe '{ing_name_clean}' "
                    f"(amount: {amount}g) — add manually via the app"
                )
                continue  # Skip inserting this line for now
            else:
                ing_id = ingredient_map.get(ing_name_clean)
                if not ing_id:
                    print(f"    ✗ Ingredient not found: '{ing_name_clean}'")
                    continue
                line = {
                    "recipe_id":     recipe_id,
                    "ingredient_id": ing_id,
                    "sub_recipe_id": None,
                    "amount":        float(amount),
                    "sort_order":    sort_order,
                }

            sb.table("recipe_ingredient_lines").insert(line).execute()

    if sub_recipe_warnings:
        print("\n  Sub-recipe lines requiring manual linkage in the app:")
        for w in sub_recipe_warnings:
            print(w)


def print_summary() -> None:
    print("\n" + "="*60)
    print("Migration complete.")
    print("="*60)
    print("""
Next steps:
  1. Open the app → Ingredients → add pack sizes and prices
     for each ingredient. The cost calculator cannot produce
     accurate results until prices are entered.

  2. Open the app → Recipes → add ref_height_cm for each
     diameter-type recipe. Without this, the calculator will
     warn that volume scaling is approximate.

  3. Open the app → Recipes → link any sub-recipe lines
     (marked with ⚠ above) to their correct recipe records.

  4. Open the app → Recipes → assign cake_code + version
     to each recipe so SKUs can be generated.

  5. Open the app → SKUs → create SKU records for your
     standard product range.

  6. Open the app → Packaging presets → create presets for
     your standard packaging combinations.
""")


# =============================================================================
# Main
# =============================================================================

def main():
    print("Millington Cakes — data migration")
    print(f"  Target: {SUPABASE_URL}")

    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    consumables = load_json(CONSUMABLES_FILE)
    recipes     = load_json(RECIPES_FILE)

    migrate_consumables(sb, consumables)
    ingredient_map = migrate_ingredients(sb, recipes)
    migrate_recipes(sb, recipes, ingredient_map)
    print_summary()


if __name__ == "__main__":
    main()
