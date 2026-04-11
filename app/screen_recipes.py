# screen_recipes.py
import streamlit as st
import millington_db as db


def screen_recipes():
    st.title("Recipes")
    st.caption("Manage reference recipes, ingredients and cake code assignments")

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

    col_list, col_detail = st.columns([1, 2.5])

    recipes     = db.get_recipes()
    cake_codes  = db.get_cake_codes()
    ingredients = db.get_ingredients()
    settings    = db.get_settings()

    code_options = {f"{cc['code']} — {cc['name']}": cc['id'] for cc in cake_codes}
    code_by_id   = {cc['id']: cc['code'] for cc in cake_codes}
    ing_options  = {i['name']: i['id'] for i in ingredients}

    ws_batch_ind = int(settings.get("ws_batch_individual") or 100)
    ws_batch_boc = int(settings.get("ws_batch_bocado") or 250)

    # ── Recipe list ───────────────────────────────────────────────────────────
    with col_list:
        st.markdown("**All recipes**")

        search = st.text_input("Search recipes", placeholder="Filter…",
                               label_visibility="collapsed")

        filtered = [
            r for r in recipes
            if (search.lower() in r["name"].lower() if search else True)
        ]

        assigned   = [r for r in filtered if r.get("cake_code_id")]
        unassigned = [r for r in filtered if not r.get("cake_code_id")]

        selected_id = st.session_state.get("selected_recipe_id")

        if assigned:
            st.caption("Assigned")
            for r in assigned:
                code  = code_by_id.get(r["cake_code_id"], "")
                label = f"{code}-{r['version']}  {r['name']}"
                if st.button(
                    label, key=f"btn_{r['id']}",
                    use_container_width=True,
                    type="primary" if selected_id == r["id"] else "secondary"
                ):
                    _load_recipe(r["id"], code_options)

        if unassigned:
            st.caption("No cake code yet")
            for r in unassigned:
                if st.button(
                    r["name"], key=f"btn_{r['id']}",
                    use_container_width=True,
                    type="primary" if selected_id == r["id"] else "secondary"
                ):
                    _load_recipe(r["id"], code_options)

        st.divider()
        if st.button("➕ New recipe", use_container_width=True):
            _load_recipe("new", code_options)

    # ── Recipe detail ─────────────────────────────────────────────────────────
    with col_detail:
        selected_id = st.session_state.get("selected_recipe_id")

        if not selected_id:
            st.info("Select a recipe from the list to view or edit it.")
            return

        is_new = selected_id == "new"

        if is_new:
            recipe = {}
            lines  = []
        else:
            recipe = db.get_recipe(selected_id)
            lines  = db.get_recipe_lines(selected_id)

        if not recipe and not is_new:
            st.error("Recipe not found.")
            return

        # Height warning
        if not is_new and recipe.get("size_type") == "diameter" \
                and not recipe.get("ref_height_cm"):
            st.warning(
                "⚠️ Reference height is not set. The cost calculator will "
                "not be able to scale this recipe accurately by volume. "
                "Please add the height below."
            )

        p = selected_id  # key prefix

        # ── Recipe details ────────────────────────────────────────────────────
        st.markdown("#### Recipe details")

        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Recipe name", key=f"field_name_{p}")
        with c2:
            code_labels = ["— no code assigned —"] + list(code_options.keys())
            selected_code_label = st.selectbox(
                "Cake code", code_labels, key=f"field_code_{p}"
            )
            selected_code_id = code_options.get(selected_code_label)

        c3, c4 = st.columns(2)
        with c3:
            version = st.text_input(
                "Version", key=f"field_version_{p}",
                help="Two digits: 01, 02 etc. Increment only when the "
                     "recipe formulation meaningfully changes."
            )
        with c4:
            size_type = st.selectbox(
                "Size type", ["diameter", "weight", "portions"],
                key=f"field_size_type_{p}"
            )

        st.markdown("**Reference dimensions**")
        if size_type == "diameter":
            d1, d2 = st.columns(2)
            with d1:
                ref_diameter = st.number_input(
                    "Diameter (cm)", min_value=0.0,
                    key=f"field_diameter_{p}"
                )
            with d2:
                ref_height = st.number_input(
                    "Height (cm) ★", min_value=0.0,
                    key=f"field_height_{p}",
                    help="Required for accurate volume-based scaling."
                )
            ref_weight = ref_portions = None

        elif size_type == "weight":
            ref_weight = st.number_input(
                "Weight (kg)", min_value=0.0,
                key=f"field_weight_{p}"
            )
            ref_diameter = ref_height = ref_portions = None

        else:
            ref_portions = st.number_input(
                "Portions", min_value=0,
                key=f"field_portions_{p}"
            )
            ref_diameter = ref_height = ref_weight = None

        notes = st.text_area(
            "Notes", key=f"field_notes_{p}", height=60,
            placeholder="Optional — storage instructions, allergen notes, etc."
        )

        # ── Formats & labour ──────────────────────────────────────────────────
        with st.expander("📦 Formats & labour times"):
            st.caption(
                "Enable smaller formats and set production batch times. "
                "The calculator uses these to derive per-unit labour costs."
            )

            # ── Format availability ───────────────────────────────────────────
            has_individual = st.checkbox(
                "Available as Individual",
                key=f"field_has_individual_{p}"
            )
            if has_individual:
                individual_weight = st.number_input(
                    "Individual weight (g)", min_value=1.0,
                    key=f"field_individual_weight_{p}",
                    help="Typical weight per individual portion"
                )
            else:
                individual_weight = None

            has_bocado = st.checkbox(
                "Available as Bocado",
                key=f"field_has_bocado_{p}"
            )
            if has_bocado:
                bocado_weight = st.number_input(
                    "Bocado weight (g)", min_value=1.0,
                    key=f"field_bocado_weight_{p}",
                    help="Typical weight per bocado piece"
                )
            else:
                bocado_weight = None

            # ── Labour table ──────────────────────────────────────────────────
            st.markdown("**Labour reference times**")

            # Header
            lh0, lh1, lh2, lh3 = st.columns([1.2, 0.8, 1, 1])
            lh0.markdown("**Format**")
            lh1.markdown("**Batch**")
            lh2.markdown("**Prep hrs**")
            lh3.markdown("**Oven hrs**")

            # Standard row — always shown
            ls0, ls1, ls2, ls3 = st.columns([1.2, 0.8, 1, 1])
            ls0.markdown("Standard")
            with ls1:
                ref_batch_size = st.number_input(
                    "batch_std", min_value=0,
                    label_visibility="collapsed",
                    key=f"field_batch_size_{p}"
                )
            with ls2:
                ref_prep_hours = st.number_input(
                    "prep_std", min_value=0.0, step=0.25,
                    label_visibility="collapsed",
                    key=f"field_prep_hours_{p}"
                )
            with ls3:
                ref_oven_hours = st.number_input(
                    "oven_std", min_value=0.0, step=0.25,
                    label_visibility="collapsed",
                    key=f"field_oven_hours_{p}"
                )

            # Individual row — only if has_individual ticked
            if has_individual:
                li0, li1, li2, li3 = st.columns([1.2, 0.8, 1, 1])
                li0.markdown("Individual")
                li1.markdown(f"`{ws_batch_ind}`")
                with li2:
                    small_prep_hours = st.number_input(
                        "prep_ind", min_value=0.0, step=0.25,
                        label_visibility="collapsed",
                        key=f"field_small_prep_{p}"
                    )
                with li3:
                    small_oven_hours = st.number_input(
                        "oven_ind", min_value=0.0, step=0.25,
                        label_visibility="collapsed",
                        key=f"field_small_oven_{p}"
                    )
            else:
                small_prep_hours = 0.0
                small_oven_hours = 0.0

            # Bocado row — only if has_bocado ticked
            if has_bocado:
                lb0, lb1, lb2, lb3 = st.columns([1.2, 0.8, 1, 1])
                lb0.markdown("Bocado")
                lb1.markdown(f"`{ws_batch_boc}`")
                with lb2:
                    bocado_prep_hours = st.number_input(
                        "prep_boc", min_value=0.0, step=0.25,
                        label_visibility="collapsed",
                        key=f"field_bocado_prep_{p}"
                    )
                with lb3:
                    bocado_oven_hours = st.number_input(
                        "oven_boc", min_value=0.0, step=0.25,
                        label_visibility="collapsed",
                        key=f"field_bocado_oven_{p}"
                    )
            else:
                bocado_prep_hours = 0.0
                bocado_oven_hours = 0.0

        # ── Ingredient lines ──────────────────────────────────────────────────
        st.markdown("#### Ingredients")
        st.caption(
            "Select from the ingredient list — type to search. "
            "Cost updates automatically when ingredient prices are set."
        )

        lines_key = f"lines_{selected_id}"
        if lines_key not in st.session_state:
            st.session_state[lines_key] = [
                {
                    "ingredient_id":   l.get("ingredient_id"),
                    "ingredient_name": l.get("ingredient_name", ""),
                    "amount":          float(l.get("amount") or 0),
                    "cost_per_unit":   l.get("ingredient_cost_per_unit"),
                }
                for l in lines
            ]
            st.session_state[lines_key].append(_empty_line())

        working_lines = st.session_state[lines_key]

        h1, h2, h3, h4 = st.columns([3, 1.5, 1.5, 0.5])
        h1.markdown("**Ingredient**")
        h2.markdown("**Amount**")
        h3.markdown("**Line cost**")
        h4.markdown("")

        total_cost = 0.0
        remove_idx = None

        for idx, line in enumerate(working_lines):
            c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 0.5])

            with c1:
                ing_labels  = ["— select ingredient —"] + list(ing_options.keys())
                current_ing = line.get("ingredient_name", "")
                ing_idx     = ing_labels.index(current_ing) \
                    if current_ing in ing_labels else 0
                selected_ing = st.selectbox(
                    "Ingredient", ing_labels, index=ing_idx,
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
                ing_id        = ing_options.get(selected_ing)
                cost_per_unit = None
                if ing_id:
                    ing_data      = next(
                        (i for i in ingredients if i["id"] == ing_id), {}
                    )
                    cost_per_unit = ing_data.get("cost_per_unit")
                if cost_per_unit and amount:
                    line_cost   = cost_per_unit * amount
                    total_cost += line_cost
                    st.markdown(f"`€ {line_cost:.4f}`")
                else:
                    st.markdown("—")

            with c4:
                if selected_ing != "— select ingredient —":
                    if st.button("✕", key=f"line_del_{selected_id}_{idx}",
                                 help="Remove this ingredient"):
                        remove_idx = idx

            st.session_state[lines_key][idx] = {
                "ingredient_id":   ing_options.get(selected_ing),
                "ingredient_name": selected_ing
                    if selected_ing != "— select ingredient —" else "",
                "amount":          amount,
                "cost_per_unit":   cost_per_unit,
            }

        if remove_idx is not None:
            del st.session_state[lines_key][remove_idx]
            st.rerun()

        last = working_lines[-1] if working_lines else {}
        if last.get("ingredient_name") and \
                last["ingredient_name"] != "— select ingredient —":
            st.session_state[lines_key].append(_empty_line())
            st.rerun()

        st.divider()
        if total_cost > 0:
            st.markdown(f"**Reference recipe cost: € {total_cost:.4f}**")
            st.caption("Cost of ingredients only at reference size. "
                       "Labour, packaging and scaling applied in the calculator.")
        else:
            st.caption("Ingredient costs will appear here once prices "
                       "are set in the Ingredients screen.")

        # ── Save / Cancel ─────────────────────────────────────────────────────
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
                        "id":                     None if is_new else selected_id,
                        "name":                   name,
                        "cake_code_id":           selected_code_id,
                        "version":                version.strip().zfill(2),
                        "size_type":              size_type,
                        "ref_diameter_cm":        ref_diameter,
                        "ref_height_cm":          ref_height,
                        "ref_weight_kg":          ref_weight,
                        "ref_portions":           ref_portions,
                        "notes":                  notes or None,
                        "ref_batch_size":         ref_batch_size or None,
                        "ref_prep_hours":         ref_prep_hours or None,
                        "ref_oven_hours":         ref_oven_hours or None,
                        "has_individual":         has_individual,
                        "has_bocado":             has_bocado,
                        "individual_weight_g":    individual_weight,
                        "bocado_weight_g":        bocado_weight,
                        "small_batch_prep_hours": small_prep_hours or None,
                        "small_batch_oven_hours": small_oven_hours or None,
                        "bocado_batch_prep_hours": bocado_prep_hours or None,
                        "bocado_batch_oven_hours": bocado_oven_hours or None,
                    })
                    clean_lines = [
                        {"ingredient_id": l["ingredient_id"],
                         "amount": l["amount"]}
                        for l in st.session_state[lines_key]
                        if l.get("ingredient_id") and l.get("amount", 0) > 0
                    ]
                    db.replace_recipe_lines(saved["id"], clean_lines)
                    st.success(f"Saved: {name}", icon="✅")
                    _load_recipe(saved["id"], code_options)

        with col_cancel:
            if not is_new and st.button("Cancel changes",
                                        use_container_width=True):
                _load_recipe(selected_id, code_options)


# =============================================================================
# Helpers
# =============================================================================

def _load_recipe(recipe_id: str, code_options: dict):
    """
    Load a recipe and write all field values into session state before
    rerunning. Values are set here, in the button handler, so widgets
    render correctly on the next pass.
    """
    keys_to_clear = [
        k for k in st.session_state
        if k.startswith("field_")
        or k.startswith("lines_")
        or k.startswith("line_ing_")
        or k.startswith("line_amt_")
        or k.startswith("line_del_")
    ]
    for k in keys_to_clear:
        del st.session_state[k]

    st.session_state.selected_recipe_id = recipe_id
    p = recipe_id

    if recipe_id == "new":
        st.session_state[f"field_name_{p}"]               = ""
        st.session_state[f"field_code_{p}"]               = "— no code assigned —"
        st.session_state[f"field_version_{p}"]            = "01"
        st.session_state[f"field_size_type_{p}"]          = "diameter"
        st.session_state[f"field_diameter_{p}"]           = 0.0
        st.session_state[f"field_height_{p}"]             = 0.0
        st.session_state[f"field_weight_{p}"]             = 0.0
        st.session_state[f"field_portions_{p}"]           = 0
        st.session_state[f"field_notes_{p}"]              = ""
        st.session_state[f"field_batch_size_{p}"]         = 20
        st.session_state[f"field_prep_hours_{p}"]         = 1.0
        st.session_state[f"field_oven_hours_{p}"]         = 1.0
        st.session_state[f"field_has_individual_{p}"]     = False
        st.session_state[f"field_individual_weight_{p}"]  = 100.0
        st.session_state[f"field_small_prep_{p}"]         = 0.0
        st.session_state[f"field_small_oven_{p}"]         = 0.0
        st.session_state[f"field_has_bocado_{p}"]         = False
        st.session_state[f"field_bocado_weight_{p}"]      = 30.0
        st.session_state[f"field_bocado_prep_{p}"]        = 0.0
        st.session_state[f"field_bocado_oven_{p}"]        = 0.0
    else:
        recipe = db.get_recipe(recipe_id)

        st.session_state[f"field_name_{p}"]    = recipe.get("name", "")
        st.session_state[f"field_version_{p}"] = recipe.get("version", "01")
        st.session_state[f"field_notes_{p}"]   = recipe.get("notes") or ""

        code_by_id         = {v: k for k, v in code_options.items()}
        current_code_label = code_by_id.get(
            recipe.get("cake_code_id"), "— no code assigned —"
        )
        st.session_state[f"field_code_{p}"] = current_code_label

        size_type = recipe.get("size_type", "diameter")
        st.session_state[f"field_size_type_{p}"] = size_type
        st.session_state[f"field_diameter_{p}"]  = float(recipe.get("ref_diameter_cm") or 0)
        st.session_state[f"field_height_{p}"]    = float(recipe.get("ref_height_cm") or 0)
        st.session_state[f"field_weight_{p}"]    = float(recipe.get("ref_weight_kg") or 0)
        st.session_state[f"field_portions_{p}"]  = int(recipe.get("ref_portions") or 0)

        st.session_state[f"field_batch_size_{p}"]        = int(recipe.get("ref_batch_size") or 20)
        st.session_state[f"field_prep_hours_{p}"]        = float(recipe.get("ref_prep_hours") or 1.0)
        st.session_state[f"field_oven_hours_{p}"]        = float(recipe.get("ref_oven_hours") or 1.0)
        st.session_state[f"field_has_individual_{p}"]    = bool(recipe.get("has_individual"))
        st.session_state[f"field_individual_weight_{p}"] = float(recipe.get("individual_weight_g") or 100)
        st.session_state[f"field_small_prep_{p}"]        = float(recipe.get("small_batch_prep_hours") or 0.0)
        st.session_state[f"field_small_oven_{p}"]        = float(recipe.get("small_batch_oven_hours") or 0.0)
        st.session_state[f"field_has_bocado_{p}"]        = bool(recipe.get("has_bocado"))
        st.session_state[f"field_bocado_weight_{p}"]     = float(recipe.get("bocado_weight_g") or 30)
        st.session_state[f"field_bocado_prep_{p}"]       = float(recipe.get("bocado_batch_prep_hours") or 0.0)
        st.session_state[f"field_bocado_oven_{p}"]       = float(recipe.get("bocado_batch_oven_hours") or 0.0)

    st.rerun()


def _empty_line() -> dict:
    return {
        "ingredient_id":   None,
        "ingredient_name": "",
        "amount":          0.0,
        "cost_per_unit":   None,
    }


def _validate_recipe(name, code_id, version, is_new, current_id, all_recipes):
    if not name:
        return "Recipe name is required."
    if not version.strip():
        return "Version is required (e.g. 01)."
    if code_id:
        conflict = next(
            (r for r in all_recipes
             if r.get("cake_code_id") == code_id
             and r.get("version") == version.strip().zfill(2)
             and r["id"] != current_id),
            None
        )
        if conflict:
            return (
                f"Version {version} is already used by '{conflict['name']}' "
                f"for this cake code. Choose a different version number."
            )
    return None
