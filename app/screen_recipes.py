# screen_recipes.py
import streamlit as st
import millington_db as db
from screen_analysis import screen_analysis


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

    recipes           = db.get_recipes(include_sub_recipes=True, include_deprecated=True)
    cake_codes        = db.get_cake_codes()
    ingredients       = db.get_ingredients()
    component_recipes = db.get_component_recipes()
    settings          = db.get_settings()

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

        # Split into sellable recipes and sub-recipes
        sellable   = [r for r in filtered if not r.get("is_sub_recipe")]
        sub_recipes = [r for r in filtered if r.get("is_sub_recipe")]

        assigned   = [r for r in sellable if r.get("cake_code_id")]
        unassigned = [r for r in sellable if not r.get("cake_code_id")]

        if assigned:
            st.caption("Assigned")
            for r in assigned:
                code  = code_by_id.get(r["cake_code_id"], "")
                dep   = r.get("deprecated", False)
                label = f"{'🚫 ' if dep else ''}{code}-{r['version']}  {r['name']}"
                if st.button(
                    label, key=f"btn_{r['id']}",
                    use_container_width=True,
                    type="primary" if selected_id == r["id"] else "secondary"
                ):
                    _load_recipe(r["id"], code_options)

        if unassigned:
            st.caption("No cake code yet")
            for r in unassigned:
                dep   = r.get("deprecated", False)
                label = f"{'🚫 ' if dep else ''}{r['name']}"
                if st.button(
                    label, key=f"btn_{r['id']}",
                    use_container_width=True,
                    type="primary" if selected_id == r["id"] else "secondary"
                ):
                    _load_recipe(r["id"], code_options)

        # Sub-recipes hidden by default
        if sub_recipes:
            show_subs = st.toggle(
                f"🔧 Components ({len(sub_recipes)})",
                value=False, key="show_sub_recipes"
            )
            if show_subs:
                for r in sub_recipes:
                    if st.button(
                        f"🔧 {r['name']}", key=f"btn_{r['id']}",
                        use_container_width=True,
                        type="primary" if selected_id == r["id"] else "secondary"
                    ):
                        _load_recipe(r["id"], code_options)

        # ── New cake code ─────────────────────────────────────────────────────
        with st.expander("➕ New cake code"):
            nc1, nc2 = st.columns(2)
            with nc1:
                new_code = st.text_input(
                    "Code (2 letters)",
                    max_chars=2,
                    placeholder="e.g. RV",
                    key="new_cake_code_code",
                ).upper().strip()
            with nc2:
                new_name = st.text_input(
                    "Name",
                    placeholder="e.g. Red Velvet",
                    key="new_cake_code_name",
                ).strip()
 
            if st.button("Save cake code", key="save_cake_code",
                         type="primary",
                         disabled=len(new_code) != 2 or not new_name):
                try:
                    db.save_cake_code(new_code, new_name)
                    db.get_cake_codes.clear()
                    st.toast(f"✓ {new_code} — {new_name} saved.")
                    st.rerun()
                except Exception as e:
                    if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                        st.error(f"Code '{new_code}' already exists.")
                    else:
                        st.error(f"Error: {e}")


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

        # ── Tabs: Edit / Analysis (analysis only for saved recipes) ──────────
        if is_new:
            # New recipe — no analysis tab yet
            _recipe_editor(p, selected_id, recipe, lines, code_options,
                           ing_options, recipes, settings,
                           ws_batch_ind, ws_batch_boc)
        else:
            tab_edit, tab_analysis = st.tabs([
                "✏️ Editar receta",
                "📊 Análisis",
            ])
            with tab_edit:
                _recipe_editor(p, selected_id, recipe, lines, code_options,
                               ing_options, recipes, settings,
                               ws_batch_ind, ws_batch_boc)
            with tab_analysis:
                screen_analysis(recipe_id=selected_id)




def _recipe_editor(p, selected_id, recipe, lines, code_options,
                   ing_options, recipes, settings,
                   ws_batch_ind=100, ws_batch_boc=250):
    """All the recipe edit widgets — called from inside a tab."""
    from core.settings import load_settings
    is_new            = selected_id == "new"
    ingredients       = db.get_ingredients()
    component_recipes = db.get_component_recipes()
    s                 = load_settings()
    comp_options      = {f"🔧 {c['name']}": c['id'] for c in component_recipes}
    all_line_options  = {**ing_options, **comp_options}
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

    c3, c4, c5 = st.columns([1, 1, 1])
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
    with c5:
        is_sub_recipe = st.checkbox(
            "🔧 Component recipe",
            key=f"field_is_sub_recipe_{p}",
            help="Tick for intermediate components (pastry dough, "
                 "mousse, etc.) used as ingredients in other recipes. "
                 "These are excluded from pricing analysis and catalogue."
        )

    if is_sub_recipe:
        deprecated = False  # component recipes are never deprecated independently
        st.info(
            "🔧 Component recipe — this will not appear in the pricing "
            "analysis, calculator or catalogue. It will only be accessible "
            "from the recipe list under Components."
        )
        labour_per_kg = st.number_input(
            "Labour time (hours/kg output)",
            min_value=0.0, step=0.25,
            key=f"field_labour_per_kg_{p}",
            help="Labour hours per kg of component produced at typical batch size. "
                 "Used to cost component contributions to final recipe labour."
        )
    else:
        labour_per_kg = None  # only used for component recipes
        deprecated = st.checkbox(
            "🚫 Deprecated — replaced by new recipe",
            key=f"field_deprecated_{p}",
            help="Hides this recipe from active screens. Variants must be migrated "
                 "first. Deprecated recipes remain visible in the pricing analysis "
                 "tab for cost comparison."
        )
        if deprecated:
            st.warning(
                "This recipe is deprecated. Make sure variants have been migrated "
                "to the new recipe before deprecating."
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

    if not is_sub_recipe:
        catalogue_section = st.selectbox(
            "Catalogue section",
            options=["tartas", "otros"],
            format_func=lambda x: "Tartas" if x == "tartas" else "Otros",
            key=f"field_catalogue_section_{p}",
            help="Controls which section of the price catalogue this product appears in",
        )

    # ── Formats & labour ──────────────────────────────────────────────────
    if not is_sub_recipe:
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
        init_lines = []
        for l in lines:
            if l.get("is_component_line"):
                init_lines.append({
                    "ingredient_id":       None,
                    "component_recipe_id": l.get("component_recipe_id"),
                    "ingredient_name":     f"🔧 {l.get('ingredient_name', '')}",
                    "amount":              float(l.get("amount") or 0),
                    "cost_per_unit":       None,
                    "is_component_line":   True,
                })
            else:
                init_lines.append({
                    "ingredient_id":       l.get("ingredient_id"),
                    "component_recipe_id": None,
                    "ingredient_name":     l.get("ingredient_name", ""),
                    "amount":              float(l.get("amount") or 0),
                    "cost_per_unit":       l.get("ingredient_cost_per_unit"),
                    "is_component_line":   False,
                })
        st.session_state[lines_key] = init_lines
        st.session_state[lines_key].append(_empty_line())

    working_lines = st.session_state[lines_key]

    # For sub-recipes, only raw ingredients; for final recipes, both
    picker_options = ing_options if is_sub_recipe else all_line_options
    picker_labels  = ["— select ingredient —"] + list(picker_options.keys())

    h1, h2, h3, h4 = st.columns([3, 1.5, 1.5, 0.5])
    h1.markdown("**Ingredient**")
    h2.markdown("**Amount (g)**")
    h3.markdown("**Line cost**")
    h4.markdown("")

    total_cost = 0.0
    remove_idx = None

    for idx, line in enumerate(working_lines):
        c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 0.5])

        with c1:
            current_ing = line.get("ingredient_name", "")
            ing_idx     = picker_labels.index(current_ing) \
                if current_ing in picker_labels else 0
            selected_ing = st.selectbox(
                "Ingredient", picker_labels, index=ing_idx,
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
            is_comp      = selected_ing.startswith("🔧 ")
            cost_per_unit = None
            line_cost_val = None

            if is_comp:
                comp_id = comp_options.get(selected_ing)
                if comp_id and amount:
                    st.markdown("*(component)*")
            else:
                ing_id = ing_options.get(selected_ing)
                if ing_id:
                    ing_data      = next((i for i in ingredients if i["id"] == ing_id), {})
                    cost_per_unit = ing_data.get("cost_per_unit")
                if cost_per_unit and amount:
                    line_cost_val = cost_per_unit * amount
                    total_cost   += line_cost_val
                    st.markdown(f"`€ {line_cost_val:.4f}`")
                else:
                    st.markdown("—")

        with c4:
            if selected_ing != "— select ingredient —":
                if st.button("✕", key=f"line_del_{selected_id}_{idx}",
                             help="Remove this line"):
                    remove_idx = idx

        # Persist line state
        if is_comp:
            comp_id = comp_options.get(selected_ing)
            st.session_state[lines_key][idx] = {
                "ingredient_id":       None,
                "component_recipe_id": comp_id,
                "ingredient_name":     selected_ing,
                "amount":              amount,
                "cost_per_unit":       None,
                "is_component_line":   True,
            }
        else:
            st.session_state[lines_key][idx] = {
                "ingredient_id":       ing_options.get(selected_ing),
                "component_recipe_id": None,
                "ingredient_name":     selected_ing
                    if selected_ing != "— select ingredient —" else "",
                "amount":              amount,
                "cost_per_unit":       cost_per_unit,
                "is_component_line":   False,
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

    # ── PCC Steps ─────────────────────────────────────────────────────────
    st.divider()
    st.markdown("#### 🌡️ Puntos de Control Crítico (PCC)")
    st.caption(
        "Define los pasos de elaboración que requieren control de temperatura. "
        "Estos pasos se mostrarán en el registro de producción para su confirmación. "
        "Solo incluye pasos con aplicación de calor (horneado, cocción, pasteurización). "
        "No incluyas pasos en frío — se controlan por prerrequisitos."
    )

    pcc_key = f"pcc_steps_{selected_id}"
    if pcc_key not in st.session_state:
        # Load existing PCC steps from DB
        existing_pcc = []
        if selected_id != "new":
            try:
                existing_pcc = db.get_pcc_steps(selected_id)
            except Exception:
                pass
        st.session_state[pcc_key] = existing_pcc or []
        st.session_state[pcc_key].append(_empty_pcc_step())

    working_pcc = st.session_state[pcc_key]

    # Header
    ph1, ph2, ph3, ph4, ph5 = st.columns([2.5, 1, 1, 1, 0.5])
    ph1.markdown("**Elaboración**")
    ph2.markdown("**Temp. objetivo (°C)**")
    ph3.markdown("**Tiempo (min)**")
    ph4.markdown("**Límite crítico (°C)**")
    ph5.markdown("")

    pcc_remove_idx = None
    for idx, step in enumerate(working_pcc):
        pc1, pc2, pc3, pc4, pc5 = st.columns([2.5, 1, 1, 1, 0.5])
        with pc1:
            step_name = st.text_input(
                "Elaboración", key=f"pcc_name_{selected_id}_{idx}",
                value=step.get("step_name", ""),
                placeholder="e.g. Horneado bizcocho",
                label_visibility="visible" if idx == 0 else "collapsed"
            )
        with pc2:
            target_temp = st.number_input(
                "Temp. objetivo", key=f"pcc_temp_{selected_id}_{idx}",
                value=float(step.get("target_temp_c") or 0),
                min_value=0.0, max_value=300.0, step=5.0,
                label_visibility="visible" if idx == 0 else "collapsed"
            )
        with pc3:
            target_time = st.number_input(
                "Tiempo", key=f"pcc_time_{selected_id}_{idx}",
                value=int(step.get("target_time_min") or 0),
                min_value=0, max_value=300, step=5,
                label_visibility="visible" if idx == 0 else "collapsed"
            )
        with pc4:
            critical_limit = st.number_input(
                "Límite crítico", key=f"pcc_limit_{selected_id}_{idx}",
                value=float(step.get("critical_limit_temp_c") or 70.0),
                min_value=0.0, max_value=300.0, step=1.0,
                label_visibility="visible" if idx == 0 else "collapsed",
                help="Temperatura mínima que debe alcanzarse para destruir patógenos. "
                     "Normalmente 70°C/2min o 75°C instantáneo."
            )
        with pc5:
            if step_name.strip() and st.button(
                "✕", key=f"pcc_del_{selected_id}_{idx}",
                help="Eliminar este paso"
            ):
                pcc_remove_idx = idx

        st.session_state[pcc_key][idx] = {
            "id":                  step.get("id"),
            "step_name":           step_name.strip(),
            "target_temp_c":       target_temp if target_temp > 0 else None,
            "target_time_min":     target_time if target_time > 0 else None,
            "critical_limit_temp_c": critical_limit,
            "sort_order":          idx,
        }

    if pcc_remove_idx is not None:
        del st.session_state[pcc_key][pcc_remove_idx]
        st.rerun()

    # Auto-add new row when last row has a name
    last_pcc = working_pcc[-1] if working_pcc else {}
    if last_pcc.get("step_name", "").strip():
        st.session_state[pcc_key].append(_empty_pcc_step())
        st.rerun()

    # ── Save / Cancel ─────────────────────────────────────────────────────────
    st.divider()
    col_save, col_cancel, col_dup = st.columns([1, 1.5, 1.5])

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
                    "is_sub_recipe":          is_sub_recipe,
                    "labour_per_kg":          labour_per_kg or None,
                    "deprecated":             deprecated,
                    "catalogue_section":      catalogue_section if not is_sub_recipe else "tartas",
                    "has_individual":         has_individual if not is_sub_recipe else False,
                    "has_bocado":             has_bocado if not is_sub_recipe else False,
                    "individual_weight_g":    individual_weight if not is_sub_recipe else None,
                    "bocado_weight_g":        bocado_weight if not is_sub_recipe else None,
                    "small_batch_prep_hours": small_prep_hours or None if not is_sub_recipe else None,
                    "small_batch_oven_hours": small_oven_hours or None if not is_sub_recipe else None,
                    "bocado_batch_prep_hours": bocado_prep_hours or None if not is_sub_recipe else None,
                    "bocado_batch_oven_hours": bocado_oven_hours or None if not is_sub_recipe else None,
                })
                clean_lines = []
                for l in st.session_state[lines_key]:
                    if l.get("is_component_line") and l.get("component_recipe_id") and l.get("amount", 0) > 0:
                        clean_lines.append({
                            "component_recipe_id": l["component_recipe_id"],
                            "ingredient_id":       None,
                            "amount":              l["amount"],
                        })
                    elif l.get("ingredient_id") and l.get("amount", 0) > 0:
                        clean_lines.append({
                            "ingredient_id":       l["ingredient_id"],
                            "component_recipe_id": None,
                            "amount":              l["amount"],
                        })
                db.replace_recipe_lines(saved["id"], clean_lines)

                # Save PCC steps
                clean_pcc = [
                    {
                        "id":                    s.get("id"),
                        "step_name":             s["step_name"],
                        "target_temp_c":         s.get("target_temp_c"),
                        "target_time_min":       s.get("target_time_min"),
                        "critical_limit_temp_c": s.get("critical_limit_temp_c") or 70.0,
                        "sort_order":            s.get("sort_order", i),
                    }
                    for i, s in enumerate(st.session_state.get(f"pcc_steps_{selected_id}", []))
                    if s.get("step_name", "").strip()
                ]
                db.replace_pcc_steps(saved["id"], clean_pcc)

                st.success(f"Saved: {name}", icon="✅")
                _load_recipe(saved["id"], code_options)

    with col_cancel:
        if not is_new and st.button("Cancel changes",
                                    use_container_width=True):
            _load_recipe(selected_id, code_options)

    with col_dup:
        if not is_new and st.button("📋 Duplicate recipe",
                                    use_container_width=True,
                                    help="Creates a copy with no cake code assigned. "
                                         "Useful for creating a legacy baseline before "
                                         "refactoring to components."):
            try:
                copy = db.duplicate_recipe(selected_id)
                st.toast(f"✓ Duplicated as '{copy['name']}'")
                _load_recipe(copy["id"], code_options)
            except Exception as e:
                st.error(f"Error duplicating: {e}")

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
        or k.startswith("pcc_")
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
        st.session_state[f"field_is_sub_recipe_{p}"]      = False
        st.session_state[f"field_labour_per_kg_{p}"]     = 0.0
        st.session_state[f"field_deprecated_{p}"]        = False
        st.session_state[f"field_catalogue_section_{p}"] = "tartas"
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
        st.session_state[f"field_is_sub_recipe_{p}"]     = bool(recipe.get("is_sub_recipe"))
        st.session_state[f"field_labour_per_kg_{p}"]    = float(recipe.get("labour_per_kg") or 0.0)
        st.session_state[f"field_deprecated_{p}"]       = bool(recipe.get("deprecated"))
        st.session_state[f"field_catalogue_section_{p}"] = recipe.get("catalogue_section") or "tartas"

    st.rerun()


def _empty_line() -> dict:
    return {
        "ingredient_id":       None,
        "component_recipe_id": None,
        "ingredient_name":     "",
        "amount":              0.0,
        "cost_per_unit":       None,
        "is_component_line":   False,
    }


def _empty_pcc_step() -> dict:
    return {
        "id":                    None,
        "step_name":             "",
        "target_temp_c":         None,
        "target_time_min":       None,
        "critical_limit_temp_c": 70.0,
        "sort_order":            0,
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
