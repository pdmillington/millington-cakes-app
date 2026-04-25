# screen_prices.py
# =============================================================================
# Price matrix — batch edit and approve wholesale and retail prices.
#
# Working prices can be edited freely without commitment.
# Approved prices are stamped with a date and used in catalogues and fichas.
#
# Workflow:
#   1. Edit working prices in the matrix (st.data_editor)
#   2. Save draft — writes to ws_price_ex_vat / rt_price_inc_vat
#   3. Review impact in Repricing analysis
#   4. Approve — copies working prices to approved fields with timestamp
#
# Client-specific prices override standard approved prices in catalogue PDFs.
# =============================================================================

import streamlit as st
import millington_db as db
import pandas as pd
from datetime import date

FORMAT_DISPLAY = {
    "standard":   "Estándar",
    "individual": "Individual",
    "bocado":     "Bocado",
}


def screen_prices():
    st.title("Prices")
    st.caption(
        "Edit working prices freely, then approve when ready. "
        "Approved prices are used in catalogue PDFs and fichas. "
        "Working prices drive the repricing analysis."
    )

    tab_matrix, tab_clients = st.tabs(
        ["📊 Price matrix", "👥 Client prices"]
    )

    with tab_matrix:
        _price_matrix()

    with tab_clients:
        _client_prices()


# =============================================================================
# Tab 1 — Price matrix
# =============================================================================

def _price_matrix():
    recipes      = db.get_recipes()
    all_variants = db.get_all_variants_full_with_approval()

    recipe_by_id = {r["id"]: r for r in recipes}

    # ── Build flat dataframe ──────────────────────────────────────────────────
    rows = []
    for v in all_variants:
        rid    = v["recipe_id"]
        recipe = recipe_by_id.get(rid, {})
        fmt    = v.get("format", "standard")

        ws_working  = _f(v.get("ws_price_ex_vat"))
        ws_approved = _f(v.get("ws_price_approved"))
        rt_working  = _f(v.get("rt_price_inc_vat"))
        rt_approved = _f(v.get("rt_price_approved"))

        ws_date = _fmt_date(v.get("ws_price_approved_at"))
        rt_date = _fmt_date(v.get("rt_price_approved_at"))

        # Flag if working differs from approved
        ws_changed = (ws_working != ws_approved) if (ws_working and ws_approved) else bool(ws_working and not ws_approved)
        rt_changed = (rt_working != rt_approved) if (rt_working and rt_approved) else bool(rt_working and not rt_approved)

        # Include size in recipe label for multi-size standard variants
        variant_d  = _f(v.get("ref_diameter_cm"))
        size_label = (
            v.get("size_description") or
            (f"{variant_d:.0f}cm" if variant_d else "")
        )
        recipe_label = (
            f"{recipe.get('name', '')} ({size_label})"
            if size_label and fmt == "standard"
            else recipe.get("name", "")
        )

        rows.append({
            "_variant_id":      v.get("id"),
            "_ws_changed":      ws_changed,
            "_rt_changed":      rt_changed,
            "Recipe":           recipe_label,
            "Format":           FORMAT_DISPLAY.get(fmt, fmt),
            "_fmt":             fmt,
            "WS working (€)":   ws_working or 0.0,
            "WS approved (€)":  ws_approved or 0.0,
            "WS approved date": ws_date,
            "RT working (€)":   rt_working or 0.0,
            "RT approved (€)":  rt_approved or 0.0,
            "RT approved date": rt_date,
        })

    if not rows:
        st.info("No variants found.")
        return

    df = pd.DataFrame(rows)

    # ── Summary ───────────────────────────────────────────────────────────────
    n_ws_changed = df["_ws_changed"].sum()
    n_rt_changed = df["_rt_changed"].sum()
    n_no_price   = (df["WS working (€)"] == 0).sum()

    m1, m2, m3 = st.columns(3)
    m1.metric("WS price changes pending", int(n_ws_changed))
    m2.metric("RT price changes pending", int(n_rt_changed))
    m3.metric("No WS price set",          int(n_no_price))

    if n_ws_changed > 0 or n_rt_changed > 0:
        st.warning(
            f"{int(n_ws_changed + n_rt_changed)} price change(s) not yet approved. "
            "Review and click Approve below when ready."
        )

    st.divider()

    # ── Filter ────────────────────────────────────────────────────────────────
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        show_format = st.multiselect(
            "Format",
            ["Estándar", "Individual", "Bocado"],
            default=["Estándar", "Individual", "Bocado"]
        )
    with col_f2:
        show_changes_only = st.checkbox(
            "Show unapproved changes only", value=False
        )

    display_df = df[df["Format"].isin(show_format)].copy()
    if show_changes_only:
        display_df = display_df[
            display_df["_ws_changed"] | display_df["_rt_changed"]
        ]

    # Columns for editor — exclude internal fields
    edit_cols = [
        "Recipe", "Format",
        "WS working (€)", "WS approved (€)", "WS approved date",
        "RT working (€)", "RT approved (€)", "RT approved date",
    ]

    # ── Editable matrix ───────────────────────────────────────────────────────
    st.caption(
        "Edit **WS working** and **RT working** columns directly. "
        "Approved columns are read-only — use the Approve button below."
    )

    edited = st.data_editor(
        display_df[edit_cols].reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
        disabled=[
            "Recipe", "Format",
            "WS approved (€)", "WS approved date",
            "RT approved (€)", "RT approved date",
        ],
        column_config={
            "WS working (€)":   st.column_config.NumberColumn(
                "WS working (€)", format="€ %.2f", min_value=0.0
            ),
            "WS approved (€)":  st.column_config.NumberColumn(
                "WS approved (€)", format="€ %.2f"
            ),
            "RT working (€)":   st.column_config.NumberColumn(
                "RT working (€)", format="€ %.2f", min_value=0.0
            ),
            "RT approved (€)":  st.column_config.NumberColumn(
                "RT approved (€)", format="€ %.2f"
            ),
        },
        key="price_matrix_editor"
    )

    # ── Save draft ────────────────────────────────────────────────────────────
    col_save, col_approve, col_approve_all = st.columns([1, 1, 1])

    with col_save:
        if st.button("💾 Save draft prices", use_container_width=True):
            _save_draft_prices(edited, display_df)
            st.success("Draft prices saved", icon="✅")
            st.rerun()

    # ── Approve ───────────────────────────────────────────────────────────────
    with col_approve:
        approve_selection = st.button(
            "✅ Approve shown prices",
            use_container_width=True,
            help="Approves prices for all rows currently shown in the table"
        )

    with col_approve_all:
        approve_all = st.button(
            "✅ Approve ALL prices",
            use_container_width=True,
            type="primary",
            help="Approves all working prices across all variants"
        )

    if approve_selection:
        _approve_prices(display_df)
        st.success(
            f"Approved {len(display_df)} price(s) — "
            f"stamped {date.today().strftime('%d %B %Y')}",
            icon="✅"
        )
        st.rerun()

    if approve_all:
        _approve_prices(df)
        st.success(
            f"Approved all {len(df)} price(s) — "
            f"stamped {date.today().strftime('%d %B %Y')}",
            icon="✅"
        )
        st.rerun()

    st.caption(
        "WS prices are ex-VAT · RT prices are inc-VAT (10%) · "
        "Approved prices are used in catalogue PDFs."
    )


def _save_draft_prices(edited: pd.DataFrame, original: pd.DataFrame):
    """Save edited working prices back to product_variants."""
    sb_variants = []
    for idx, row in edited.iterrows():
        orig_row = original.iloc[idx]
        vid      = orig_row["_variant_id"]
        if not vid:
            continue

        new_ws = float(row["WS working (€)"] or 0) or None
        new_rt = float(row["RT working (€)"] or 0) or None

        # Only save if changed
        old_ws = float(orig_row["WS working (€)"] or 0) or None
        old_rt = float(orig_row["RT working (€)"] or 0) or None

        if new_ws != old_ws or new_rt != old_rt:
            sb_variants.append({
                "id":              vid,
                "ws_price_ex_vat": new_ws,
                "rt_price_inc_vat": new_rt,
            })

    for record in sb_variants:
        db.save_variant(record)


def _approve_prices(df: pd.DataFrame):
    """Copy working prices to approved fields with timestamp."""
    for _, row in df.iterrows():
        vid = row.get("_variant_id")
        if not vid:
            continue
        ws = float(row["WS working (€)"] or 0) or None
        rt = float(row["RT working (€)"] or 0) or None
        if ws or rt:
            db.approve_variant_prices(vid, ws, rt)


# =============================================================================
# Tab 2 — Client prices
# =============================================================================

def _client_prices():
    st.caption(
        "Client-specific prices override standard approved prices "
        "when generating a catalogue for that client."
    )

    # ── Load ──────────────────────────────────────────────────────────────────
    client_prices = db.get_client_prices()
    recipes       = db.get_recipes()
    all_variants  = db.get_all_variants_full_with_approval()

    recipe_by_id  = {r["id"]: r for r in recipes}
    var_options   = {}
    for v in all_variants:
        rid    = v["recipe_id"]
        fmt    = FORMAT_DISPLAY.get(v["format"], v["format"])
        name   = recipe_by_id.get(rid, {}).get("name", "")
        label  = f"{name} — {fmt}"
        var_options[label] = v["id"]

    # ── Existing client prices ────────────────────────────────────────────────
    if client_prices:
        st.markdown("**Existing client prices**")

        cp_rows = []
        for cp in sorted(client_prices, key=lambda x: x["client_name"]):
            cp_rows.append({
                "id":            cp["id"],
                "Client":        cp["client_name"],
                "Product":       cp.get("variant_label", ""),
                "WS price (€)":  cp.get("ws_price_ex_vat"),
                "RT price (€)":  cp.get("rt_price_inc_vat"),
                "Valid from":    str(cp.get("valid_from", "")),
                "Valid until":   str(cp.get("valid_until") or "—"),
                "Notes":         cp.get("notes") or "",
            })

        cp_df = pd.DataFrame(cp_rows)
        st.dataframe(
            cp_df[["Client", "Product", "WS price (€)",
                   "RT price (€)", "Valid from", "Valid until", "Notes"]],
            use_container_width=True,
            hide_index=True
        )
    else:
        st.caption("No client-specific prices set yet.")

    st.divider()

    # ── Add new client price ──────────────────────────────────────────────────
    with st.expander("➕ Add client-specific price"):
        c1, c2 = st.columns(2)
        with c1:
            client_name = st.text_input(
                "Client name",
                key="cp_client",
                placeholder="e.g. Restaurante La Paloma"
            )
        with c2:
            selected_var_label = st.selectbox(
                "Product / format",
                ["— select —"] + list(var_options.keys()),
                key="cp_variant"
            )

        p1, p2 = st.columns(2)
        with p1:
            cp_ws = st.number_input(
                "WS price ex-VAT (€)", min_value=0.0,
                format="%.2f", key="cp_ws"
            )
        with p2:
            cp_rt = st.number_input(
                "RT price inc-VAT (€)", min_value=0.0,
                format="%.2f", key="cp_rt"
            )

        d1, d2 = st.columns(2)
        with d1:
            cp_valid_from = st.date_input(
                "Valid from", value=date.today(), key="cp_from"
            )
        with d2:
            cp_valid_until = st.date_input(
                "Valid until (optional)", value=None, key="cp_until"
            )

        cp_notes = st.text_input(
            "Notes", key="cp_notes",
            placeholder="e.g. Agreed at meeting 15 April 2026"
        )

        if st.button("Save client price", type="primary", key="cp_save"):
            if not client_name:
                st.error("Client name is required.")
            elif selected_var_label == "— select —":
                st.error("Select a product.")
            elif not cp_ws and not cp_rt:
                st.error("Enter at least one price.")
            else:
                vid = var_options[selected_var_label]
                db.save_client_price({
                    "client_name":     client_name,
                    "variant_id":      vid,
                    "ws_price_ex_vat": cp_ws or None,
                    "rt_price_inc_vat": cp_rt or None,
                    "valid_from":      str(cp_valid_from),
                    "valid_until":     str(cp_valid_until) if cp_valid_until else None,
                    "notes":           cp_notes or None,
                })
                st.success(
                    f"Saved: {client_name} — {selected_var_label}",
                    icon="✅"
                )
                st.rerun()

    # ── Delete ────────────────────────────────────────────────────────────────
    if client_prices:
        with st.expander("🗑 Delete client price"):
            del_options = {
                f"{cp['client_name']} — {cp.get('variant_label', '')}": cp["id"]
                for cp in client_prices
            }
            to_delete = st.selectbox(
                "Select to delete",
                ["— select —"] + list(del_options.keys()),
                key="cp_delete_sel"
            )
            if st.button("Delete", key="cp_delete_btn"):
                if to_delete != "— select —":
                    db.delete_client_price(del_options[to_delete])
                    st.success("Deleted", icon="✅")
                    st.rerun()


# =============================================================================
# Helpers
# =============================================================================

def _f(val) -> float | None:
    try:
        v = float(val)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _fmt_date(val) -> str:
    if not val:
        return "—"
    try:
        return str(val)[:10]
    except Exception:
        return "—"
