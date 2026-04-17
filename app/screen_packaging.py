# screen_packaging.py
import streamlit as st
import millington_db as db


def screen_packaging():
    st.title("Packaging presets")
    st.caption("Saved combinations used in the cost calculator")

    consumables = db.get_consumables()
    presets     = db.get_packaging_presets()

    con_names = {c["name"]: c for c in consumables}

    col_list, col_detail = st.columns([1, 2])

    # ── Preset list ───────────────────────────────────────────────────────────
    with col_list:
        st.markdown("**All presets**")

        selected_preset_id = st.session_state.get("selected_preset_id")

        for p in presets:
            units = int(p.get("units_per_pack") or 1)
            label = f"{p['name']}" if units == 1 else f"{p['name']} (×{units})"
            if st.button(
                label,
                key=f"preset_btn_{p['id']}",
                use_container_width=True,
                type="primary" if selected_preset_id == p["id"] else "secondary"
            ):
                st.session_state.selected_preset_id = p["id"]
                st.session_state.pop("preset_lines_state", None)
                st.rerun()

        st.divider()
        if st.button("➕ New preset", use_container_width=True):
            st.session_state.selected_preset_id = "new"
            st.session_state.pop("preset_lines_state", None)
            st.rerun()

    # ── Preset detail ─────────────────────────────────────────────────────────
    with col_detail:
        selected_id = st.session_state.get("selected_preset_id")

        if not selected_id:
            st.info("Select a preset from the list or create a new one.")
            return

        is_new = selected_id == "new"

        if is_new:
            preset      = {}
            saved_lines = []
        else:
            preset      = next((p for p in presets if p["id"] == selected_id), {})
            saved_lines = db.get_preset_lines(selected_id)

        if not preset and not is_new:
            st.error("Preset not found.")
            return

        # ── Preset name and units per pack ────────────────────────────────────
        st.markdown("#### Preset details")

        c1, c2 = st.columns([3, 1])
        with c1:
            preset_name = st.text_input(
                "Preset name",
                value=preset.get("name", ""),
                key=f"preset_name_{selected_id}",
                placeholder="e.g. Individual tart retail (×4)"
            )
        with c2:
            units_per_pack = st.number_input(
                "Units per pack",
                min_value=1,
                value=int(preset.get("units_per_pack") or 1),
                key=f"preset_units_{selected_id}",
                help="How many cakes fit in this packaging. "
                     "Total cost is divided by this number."
            )

        # ── Consumable lines ──────────────────────────────────────────────────
        st.markdown("#### Consumables")
        st.caption(
            "Add the consumables that make up this preset. "
            "Cost per unit = total ÷ units per pack."
        )

        lines_key = "preset_lines_state"
        if lines_key not in st.session_state:
            st.session_state[lines_key] = [
                {
                    "id":              l.get("id"),
                    "consumable_name": l.get("consumable_name", ""),
                    "quantity":        float(l.get("quantity") or 1),
                    "cost_per_unit":   l.get("consumable_cost_per_unit") or 0,
                }
                for l in saved_lines
            ]
            st.session_state[lines_key].append(_empty_preset_line())

        working_lines = st.session_state[lines_key]

        h1, h2, h3, h4 = st.columns([3, 1, 1.5, 0.5])
        h1.markdown("**Consumable**")
        h2.markdown("**Qty**")
        h3.markdown("**Line cost**")
        h4.markdown("")

        total_pack_cost = 0.0
        remove_idx      = None
        con_label_list  = ["— select consumable —"] + list(con_names.keys())

        for idx, line in enumerate(working_lines):
            c1, c2, c3, c4 = st.columns([3, 1, 1.5, 0.5])

            with c1:
                current_con = line.get("consumable_name", "")
                con_idx     = con_label_list.index(current_con) \
                    if current_con in con_label_list else 0
                selected_con = st.selectbox(
                    "Consumable", con_label_list,
                    index=con_idx,
                    key=f"preset_con_{selected_id}_{idx}",
                    label_visibility="collapsed"
                )

            with c2:
                quantity = st.number_input(
                    "Qty",
                    value=float(line.get("quantity") or 1),
                    min_value=0.0, step=1.0,
                    key=f"preset_qty_{selected_id}_{idx}",
                    label_visibility="collapsed"
                )

            with c3:
                con_data  = con_names.get(selected_con, {})
                cpu       = con_data.get("cost_per_unit") or 0
                line_cost = cpu * quantity if cpu and quantity else 0
                if line_cost:
                    total_pack_cost += line_cost
                    st.markdown(f"`€ {line_cost:.4f}`")
                else:
                    st.markdown("—")

            with c4:
                if selected_con != "— select consumable —":
                    if st.button(
                        "✕", key=f"preset_del_{selected_id}_{idx}",
                        help="Remove this line"
                    ):
                        remove_idx = idx

            st.session_state[lines_key][idx] = {
                "id":              line.get("id"),
                "consumable_name": selected_con
                    if selected_con != "— select consumable —" else "",
                "quantity":        quantity,
                "cost_per_unit":   cpu,
            }

        if remove_idx is not None:
            del st.session_state[lines_key][remove_idx]
            st.rerun()

        last = working_lines[-1] if working_lines else {}
        if last.get("consumable_name") and \
                last["consumable_name"] != "— select consumable —":
            st.session_state[lines_key].append(_empty_preset_line())
            st.rerun()

        # ── Cost summary ──────────────────────────────────────────────────────
        st.divider()
        if total_pack_cost > 0:
            cost_per_unit = total_pack_cost / units_per_pack
            st.markdown(
                f"**Pack cost: € {total_pack_cost:.4f} "
                f"÷ {units_per_pack} unit(s) "
                f"= € {cost_per_unit:.4f} per unit**"
            )
        else:
            st.caption("Add consumables above to see the cost breakdown.")

        # ── Save / Delete ─────────────────────────────────────────────────────
        st.divider()
        col_save, col_delete = st.columns([1, 1])

        with col_save:
            if st.button("💾 Save preset", type="primary",
                         use_container_width=True):
                if not preset_name:
                    st.error("Preset name is required.")
                else:
                    clean_lines = [
                        {
                            "consumable_id": con_names[l["consumable_name"]]["id"],
                            "quantity":      l["quantity"],
                        }
                        for l in st.session_state[lines_key]
                        if l.get("consumable_name")
                        and l["consumable_name"] != "— select consumable —"
                        and l.get("quantity", 0) > 0
                    ]
                    if not clean_lines:
                        st.error("Add at least one consumable.")
                    else:
                        if is_new:
                            db.save_preset(preset_name, clean_lines,
                                           units_per_pack)
                        else:
                            db.update_preset(selected_id, preset_name,
                                             clean_lines, units_per_pack)
                        st.success(f"Saved: {preset_name}", icon="✅")
                        st.session_state.pop("preset_lines_state", None)
                        st.session_state.pop("selected_preset_id", None)
                        st.rerun()

        with col_delete:
            if not is_new:
                if st.button("🗑 Delete preset", use_container_width=True):
                    db.delete_preset(selected_id)
                    st.session_state.pop("preset_lines_state", None)
                    st.session_state.pop("selected_preset_id", None)
                    st.rerun()


# =============================================================================
# Helpers
# =============================================================================

def _empty_preset_line() -> dict:
    return {
        "id":              None,
        "consumable_name": "",
        "quantity":        1.0,
        "cost_per_unit":   0,
    }
