# screen_recipes.py
# =============================================================================
# Recipe management screen.
# Paste the contents of this file into main.py, replacing the existing
# screen_recipes() function and any recipe helper functions.
# =============================================================================

import streamlit as st
import db


def screen_recipes():
    st.title("Recipes")
    st.caption("Manage reference recipes, ingredients and cake code assignments")

    # ── SKU logic reference ───────────────────────────────────────────────────
    with st.expander("ⓘ SKU naming convention — click to expand"):
        st.markdown("""
**SKU structure: `CAKE-VERSION-SIZE-PRICE`**

Every sellable product has a SKU built from four segments. Example: `CC-01-LA-GW`
means Chocolate Crocanti, first formulation, Large size, General web price.

---

**Segment 1 — Cake code**
A two-letter code identifying the product. Assigned once per product and never
changed. Examples: `CC` Chocolate Crocanti · `FR` Fraisier · `LP` Lemon Pie ·
`SC` Salted Caramel Cheesecake · `BR` Brioche · `BO` Brownie.

---

**Segment 2 — Version**
A two-digit number identifying the recipe formulation. Starts at `01`.
Increment only when the recipe itself meaningfully changes — different
ratios, substituted ingredients. Changing size or price does not change
the version. Two variants of the same product (e.g. Brioche canela vs
chocolate) are `BR-01` and `BR-02`.

---

**Segment 3 — Size**
Either a named tier or an integer diameter in cm.

| Code | Meaning |
|------|---------|
| `BO` | Bocado ×20 portions |
| `IN` | Individual ×4 portions |
| `LA` | Large — 20 to 22 cm diameter |
| `XL` | XLarge — 24 to 26 cm diameter |
| `XX` | XXLarge — 28 to 30 cm diameter |
| `DC` | Desayuno / Caja |
| `MI` | Bocado individual |
| `TI` | Individual tartaleta |
| `25`, `30`… | Bespoke integer diameter in cm (round cakes only) |

Weight-based products (Brownie, Brioche) are always sold as whole unit
multiples — no numeric size codes needed.

---

**Segment 4 — Price channel**

| Code | Meaning |
|------|---------|
| `GW` | General web price |
| `WS` | Wholesale |
| `MD` | Mentidero client |
        """)

    st.divider()

    # ── Layout: list left, detail right ──────────────────────────────────────
    col_list, col_detail = st.columns([1, 2.5])

    recipes     = db.get_recipes()
    cake_codes  = db.get_cake_codes()
    ingredients = db.get_ingredients()

    # Build lookup dicts for dropdowns
    code_options  = {f"{cc['code']} — {cc['name']}": cc['id'] for cc in cake_codes}
    code_by_id    = {cc['id']: cc['code'] for cc in cake_codes}
    ing_options   = {i['name']: i['id'] for i in ingredients}

    # ── Recipe list ───────────────────────────────────────────────────────────
    with col_list:
        st.markdown("**All recipes**")

        search = st.text_input("Search recipes", placeholder="Filter…",
                               label_visibility="collapsed")

        filtered = [
            r for r in recipes
            if (search.lower() in r["name"].lower() if search else True)
        ]

        # Group into assigned and unassigned
        assigned   = [r for r in filtered if r.get("cake_code_id")]
        unassigned = [r for r in filtered if not r.get("cake_code_id")]

        selected_id = st.session_state.get("selected_recipe_id")

        if assigned:
            st.caption("Assigned")
            for r in assigned:
                code  = code_by_id.get(r["cake_code_id"], "")
                label = f"{code}-{r['version']}  {r['name']}"
                active = selected_id == r["id"]
                if st.button(
                    label,
                    key=f"rec_{r['id']}",
                    use_container_width=True,
                    type="primary" if active else "secondary"
                ):
                    st.session_state.selected_recipe_id = r["id"]
                    st.rerun()

        if unassigned:
            st.caption("No cake code yet")
            for r in unassigned:
                label  = r["name"]
                active = selected_id == r["id"]
                if st.button(
                    label,
                    key=f"rec_{r['id']}",
                    use_container_width=True,
                    type="primary" if active else "secondary"
                ):
                    st.session_state.selected_recipe_id = r["id"]
                    st.rerun()

        st.divider()
        if st.button("➕ New recipe", use_container_width=True):
            st.session_state.selected_recipe_id = "new"
            st.rerun()

    # ── Recipe detail / editor ────────────────────────────────────────────────
    with col_detail:
        selected_id = st.session_state.get("selected_recipe_id")

        if not selected_id:
            st.info("Select a recipe from the list to view or edit it.")
            return

        if selected_id == "new":
            recipe = {}
            lines  = []
        else:
            recipe = db.get_recipe(selected_id)
            lines  = db.get_recipe_lines(selected_id)

        if not recipe and selected_id != "new":
            st.error("Recipe not found.")
            return

        is_new = selected_id == "new"

        # ── Missing height warning ────────────────────────────────────────────
        if not is_new and recipe.get("size_type") == "diameter" \
                and not recipe.get("ref_height_cm"):
            st.warning(
                "⚠️ Reference height is not set. The cost calculator will "
                "not be able to scale this recipe accurately by volume. "
                "Please add the height below."
            )

        # ── Recipe details form ───────────────────────────────────────────────
        st.markdown("#### Recipe details")

        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(
                "Recipe name",
                value=recipe.get("name", ""),
                key="rec_name"
            )
        with c2:
            # Cake code selector — version conflict check built in
            current_code_id = recipe.get("cake_code_id")
            current_code_label = next(
                (k for k, v in code_options.items() if v == current_code_id),
                "— no code assigned —"
            )
            code_labels = ["— no code assigned —"] + list(code_options.keys())
            code_idx    = code_labels.index(current_code_label) \
                if current_code_label in code_labels else 0
            selected_code_label = st.selectbox(
                "Cake code", code_labels, index=code_idx, key="rec_code"
            )
            selected_code_id = code_options.get(selected_code_label)

        c3, c4 = st.columns(2)
        with c3:
            version = st.text_input(
                "Version",
                value=recipe.get("version", "01"),
                key="rec_version",
                help="Two digits: 01, 02 etc. Increment only when the "
                     "recipe formulation meaningfully changes."
            )
        with c4:
            size_types  = ["diameter", "weight", "portions"]
            size_idx    = size_types.index(recipe["size_type"]) \
                if recipe.get("size_type") in size_types else 0
            size_type   = st.selectbox(
                "Size type", size_types, index=size_idx, key="rec_size_type"
            )

        # Reference dimensions — show only fields relevant to size type
        st.markdown("**Reference dimensions**")
        if size_type == "diameter":
            d1, d2 = st.columns(2)
            with d1:
                ref_diameter = st.number_input(
                    "Diameter (cm)",
                    value=float(recipe.get("ref_diameter_cm") or 0),
                    min_value=0.0, key="rec_diameter"
                )
            with d2:
                ref_height = st.number_input(
                    "Height (cm) ★",
                    value=float(recipe.get("ref_height_cm") or 0),
                    min_value=0.0, key="rec_height",
                    help="Required for accurate volume-based scaling. "
                         "Measure the finished cake height."
                )
            ref_weight   = None
            ref_portions = None

        elif size_type == "weight":
            ref_weight = st.number_input(
                "Weight (kg)",
                value=float(recipe.get("ref_weight_kg") or 0),
                min_value=0.0, key="rec_weight"
            )
            ref_diameter = ref_height = ref_portions = None

        else:  # portions
            ref_portions = st.number_input(
                "Portions",
                value=int(recipe.get("ref_portions") or 0),
                min_value=0, key="rec_portions"
            )
            ref_diameter = ref_height = ref_weight = None

        notes = st.text_area(
            "Notes",
            value=recipe.get("notes") or "",
            key="rec_notes",
            height=60,
            placeholder="Optional — storage instructions, allergen notes, etc."
        )

        # ── Ingredient lines ──────────────────────────────────────────────────
        st.markdown("#### Ingredients")
        st.caption(
            "Select from the ingredient list — type to search. "
            "Cost updates automatically when ingredient prices are set."
        )

        # Use session state to hold the working copy of lines
        state_key = f"lines_{selected_id}"
        if state_key not in st.session_state:
            st.session_state[state_key] = [
                {
                    "ingredient_id":   l.get("ingredient_id"),
                    "ingredient_name": l.get("ingredient_name", ""),
                    "amount":          float(l.get("amount") or 0),
                    "cost_per_unit":   l.get("ingredient_cost_per_unit"),
                    "unit":            l.get("ingredient_unit", "g"),
                }
                for l in lines
            ]
            # Always have one empty row at the bottom
            st.session_state[state_key].append(_empty_line())

        working_lines = st.session_state[state_key]

        # Column headers
        h1, h2, h3, h4 = st.columns([3, 1.5, 1.5, 0.5])
        h1.markdown("**Ingredient**")
        h2.markdown("**Amount**")
        h3.markdown("**Line cost**")
        h4.markdown("")

        total_cost   = 0.0
        lines_to_keep = []
        remove_idx   = None

        for idx, line in enumerate(working_lines):
            c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 0.5])

            with c1:
                ing_labels = ["— select ingredient —"] + list(ing_options.keys())
                current_ing = line.get("ingredient_name", "")
                ing_idx     = ing_labels.index(current_ing) \
                    if current_ing in ing_labels else 0
                selected_ing = st.selectbox(
                    "Ingredient", ing_labels,
                    index=ing_idx,
                    key=f"line_ing_{selected_id}_{idx}",
                    label_visibility="collapsed"
                )

            with c2:
                amount = st.number_input(
                    "Amount",
                    value=float(line.get("amount") or 0),
                    min_value=0.0,
                    key=f"line_amt_{selected_id}_{idx}",
                    label_visibility="collapsed"
                )

            with c3:
                # Compute line cost live
                ing_id       = ing_options.get(selected_ing)
                cost_per_unit = None
                if ing_id:
                    ing_data     = next(
                        (i for i in ingredients if i["id"] == ing_id), {}
                    )
                    cost_per_unit = ing_data.get("cost_per_unit")

                if cost_per_unit and amount:
                    line_cost  = cost_per_unit * amount
                    total_cost += line_cost
                    unit        = ing_data.get("pack_unit", "g")
                    st.markdown(f"`€ {line_cost:.4f}`")
                else:
                    st.markdown("—")

            with c4:
                # Only show remove button on non-empty lines
                if selected_ing != "— select ingredient —":
                    if st.button("✕", key=f"line_del_{selected_id}_{idx}",
                                 help="Remove this ingredient"):
                        remove_idx = idx

            # Update working line state
            lines_to_keep.append({
                "ingredient_id":   ing_options.get(selected_ing),
                "ingredient_name": selected_ing
                    if selected_ing != "— select ingredient —" else "",
                "amount":          amount,
                "cost_per_unit":   cost_per_unit,
            })

        # Handle row removal
        if remove_idx is not None:
            del st.session_state[state_key][remove_idx]
            st.rerun()

        # Add a new empty row when the last row has been filled
        last = working_lines[-1] if working_lines else {}
        if last.get("ingredient_name") and last.get("ingredient_name") \
                != "— select ingredient —":
            st.session_state[state_key].append(_empty_line())
            st.rerun()

        # Running total
        st.divider()
        if total_cost > 0:
            st.markdown(f"**Reference recipe cost: € {total_cost:.4f}**")
            st.caption(
                "Cost of ingredients only at reference size. "
                "Labour, packaging and scaling applied in the calculator."
            )
        else:
            st.caption(
                "Ingredient costs will appear here once prices "
                "are set in the Ingredients screen."
            )

        # ── Save ──────────────────────────────────────────────────────────────
        st.divider()
        col_save, col_cancel = st.columns([1, 3])

        with col_save:
            if st.button("💾 Save recipe", type="primary",
                         use_container_width=True):
                error = _validate_recipe(
                    name, selected_code_id, version,
                    is_new, selected_id, recipes
                )
                if error:
                    st.error(error)
                else:
                    saved = db.save_recipe({
                        "id":             None if is_new else selected_id,
                        "name":           name,
                        "cake_code_id":   selected_code_id,
                        "version":        version.strip().zfill(2),
                        "size_type":      size_type,
                        "ref_diameter_cm": ref_diameter,
                        "ref_height_cm":  ref_height,
                        "ref_weight_kg":  ref_weight,
                        "ref_portions":   ref_portions,
                        "notes":          notes or None,
                    })

                    # Save ingredient lines — exclude empty rows
                    clean_lines = [
                        {
                            "ingredient_id": l["ingredient_id"],
                            "amount":        l["amount"],
                        }
                        for l in st.session_state[state_key]
                        if l.get("ingredient_id") and l.get("amount", 0) > 0
                    ]
                    db.replace_recipe_lines(saved["id"], clean_lines)

                    # Clear working state
                    if state_key in st.session_state:
                        del st.session_state[state_key]

                    st.session_state.selected_recipe_id = saved["id"]
                    st.success(f"Saved: {name}", icon="✅")
                    st.rerun()

        with col_cancel:
            if not is_new and st.button("Cancel changes",
                                        use_container_width=True):
                if state_key in st.session_state:
                    del st.session_state[state_key]
                st.rerun()


# =============================================================================
# Helpers
# =============================================================================

def _empty_line() -> dict:
    return {
        "ingredient_id":   None,
        "ingredient_name": "",
        "amount":          0.0,
        "cost_per_unit":   None,
    }


def _validate_recipe(
    name: str,
    code_id: str | None,
    version: str,
    is_new: bool,
    current_id: str,
    all_recipes: list[dict]
) -> str | None:
    """
    Validate recipe before saving. Returns an error message string if
    validation fails, or None if everything is fine.
    """
    if not name:
        return "Recipe name is required."

    if not version.strip():
        return "Version is required (e.g. 01)."

    # If a cake code is assigned, check the code+version combination
    # is not already used by a different recipe
    if code_id:
        conflict = next(
            (
                r for r in all_recipes
                if r.get("cake_code_id") == code_id
                and r.get("version") == version.strip().zfill(2)
                and r["id"] != current_id
            ),
            None
        )
        if conflict:
            return (
                f"Version {version} is already used by '{conflict['name']}' "
                f"for this cake code. Choose a different version number."
            )

    return None
