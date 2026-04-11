# screen_settings.py
import streamlit as st
import database as db


def screen_settings():
    st.title("Settings")
    st.caption("Business-wide defaults used in all calculations")

    settings = db.get_settings()

    # ── Labour & oven defaults ────────────────────────────────────────────────
    st.markdown("### Labour & oven")

    c1, c2 = st.columns(2)
    with c1:
        labour_rate = st.number_input(
            "Default labour rate (€/hr)",
            min_value=0.0,
            value=float(settings.get("default_labour_rate") or 30.0),
            key="set_labour"
        )
    with c2:
        oven_rate = st.number_input(
            "Default oven rate (€/hr)",
            min_value=0.0,
            value=float(settings.get("default_oven_rate") or 2.0),
            key="set_oven"
        )

    labour_power = st.number_input(
        "Labour scaling power",
        min_value=0.1, max_value=1.0,
        value=float(settings.get("labour_power") or 0.7),
        step=0.05,
        key="set_power",
        help="Power law exponent for batch labour scaling. "
             "0.7 = standard economies of scale. "
             "Lower = more savings at scale, higher = more linear."
    )

    st.divider()

    # ── Margins ───────────────────────────────────────────────────────────────
    st.markdown("### Margins")
    st.caption("Applied to total cost to produce suggested prices.")

    mc1, mc2 = st.columns(2)
    with mc1:
        st.markdown("**Wholesale (all formats)**")
        ws_margin = st.number_input(
            "Wholesale margin ×",
            min_value=1.0, value=float(settings.get("ws_margin") or 2.0),
            step=0.1, key="set_ws_margin"
        )
    with mc2:
        st.markdown("**Retail**")
        rt_margin_large = st.number_input(
            "Large cake ×",
            min_value=1.0,
            value=float(settings.get("rt_margin_large") or 3.0),
            step=0.1, key="set_rt_large"
        )
        rt_margin_ind = st.number_input(
            "Individual ×",
            min_value=1.0,
            value=float(settings.get("rt_margin_individual") or 3.5),
            step=0.1, key="set_rt_ind"
        )
        rt_margin_boc = st.number_input(
            "Bocado ×",
            min_value=1.0,
            value=float(settings.get("rt_margin_bocado") or 4.0),
            step=0.1, key="set_rt_boc"
        )

    st.divider()

    # ── Batch sizes ───────────────────────────────────────────────────────────
    st.markdown("### Batch size assumptions")
    st.caption(
        "Labour cost is calculated assuming these production run sizes. "
        "Wholesale assumes large batches, retail assumes small runs."
    )

    st.markdown("**Wholesale**")
    wb1, wb2, wb3 = st.columns(3)
    with wb1:
        ws_batch_large = st.number_input(
            "Large cakes",
            min_value=1,
            value=int(settings.get("ws_batch_large") or 20),
            key="set_ws_batch_large"
        )
    with wb2:
        ws_batch_ind = st.number_input(
            "Individual",
            min_value=1,
            value=int(settings.get("ws_batch_individual") or 100),
            key="set_ws_batch_ind"
        )
    with wb3:
        ws_batch_boc = st.number_input(
            "Bocado",
            min_value=1,
            value=int(settings.get("ws_batch_bocado") or 250),
            key="set_ws_batch_boc"
        )

    st.markdown("**Retail**")
    rb1, rb2, rb3 = st.columns(3)
    with rb1:
        rt_batch_large = st.number_input(
            "Large cakes",
            min_value=1,
            value=int(settings.get("rt_batch_large") or 1),
            key="set_rt_batch_large"
        )
    with rb2:
        rt_batch_ind = st.number_input(
            "Individual",
            min_value=1,
            value=int(settings.get("rt_batch_individual") or 4),
            key="set_rt_batch_ind"
        )
    with rb3:
        rt_batch_boc = st.number_input(
            "Bocado",
            min_value=1,
            value=int(settings.get("rt_batch_bocado") or 10),
            key="set_rt_batch_boc"
        )

    st.divider()

    # ── Format weights ────────────────────────────────────────────────────────
    st.markdown("### Format reference weights")
    st.caption(
        "Default weights used for Individual and Bocado scaling "
        "when not set on the individual recipe."
    )

    fw1, fw2 = st.columns(2)
    with fw1:
        ind_weight = st.number_input(
            "Individual weight (g)",
            min_value=1.0,
            value=float(settings.get("individual_weight_g") or 100),
            key="set_ind_weight"
        )
    with fw2:
        boc_weight = st.number_input(
            "Bocado weight (g)",
            min_value=1.0,
            value=float(settings.get("bocado_weight_g") or 30),
            key="set_boc_weight"
        )

    st.divider()

    # ── Save ──────────────────────────────────────────────────────────────────
    if st.button("💾 Save settings", type="primary", use_container_width=True):
        db.save_settings({
            "id":                    settings.get("id"),
            "default_labour_rate":   labour_rate,
            "default_oven_rate":     oven_rate,
            "labour_power":          labour_power,
            "ws_margin":             ws_margin,
            "rt_margin_large":       rt_margin_large,
            "rt_margin_individual":  rt_margin_ind,
            "rt_margin_bocado":      rt_margin_boc,
            "ws_batch_large":        ws_batch_large,
            "ws_batch_individual":   ws_batch_ind,
            "ws_batch_bocado":       ws_batch_boc,
            "rt_batch_large":        rt_batch_large,
            "rt_batch_individual":   rt_batch_ind,
            "rt_batch_bocado":       rt_batch_boc,
            "individual_weight_g":   ind_weight,
            "bocado_weight_g":       boc_weight,
        })
        st.success("Settings saved", icon="✅")
        st.rerun()

    # ── Data section ──────────────────────────────────────────────────────────
    st.divider()
    st.markdown("### Data")

    col_exp, col_db = st.columns(2)
    with col_db:
        st.markdown("**Database**")
        st.caption("Connected to Supabase")
        st.markdown(
            f"Recipes: **{len(db.get_recipes())}** · "
            f"Ingredients: **{len(db.get_ingredients())}** · "
            f"Consumables: **{len(db.get_consumables())}**"
        )
