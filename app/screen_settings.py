# screen_settings.py
import streamlit as st
import millington_db as db


def screen_settings():
    st.title("Settings")
    st.caption("Business-wide defaults used in all calculations")

    settings = db.get_settings()

    # ── Labour & oven ─────────────────────────────────────────────────────────
    st.markdown("### Labour & oven")

    c1, c2 = st.columns(2)
    with c1:
        labour_rate = st.number_input(
            "Default labour rate (€/hr)", min_value=0.0,
            value=float(settings.get("default_labour_rate") or 30.0),
            key="set_labour"
        )
    with c2:
        oven_rate = st.number_input(
            "Default oven rate (€/hr)", min_value=0.0,
            value=float(settings.get("default_oven_rate") or 2.0),
            key="set_oven"
        )

    labour_power = st.number_input(
        "Labour scaling power",
        min_value=0.1, max_value=1.0,
        value=float(settings.get("labour_power") or 0.7),
        step=0.05, key="set_power",
        help="Power law exponent for batch labour scaling. 0.7 = standard economies of scale."
    )

    st.divider()

    # ── Margins ───────────────────────────────────────────────────────────────
    st.markdown("### Margins")
    st.caption(
        "Applied to total cost to produce suggested prices. "
        "Retail margins are applied to the inc-VAT price — "
        "true ex-VAT retail margin = target ÷ 1.10."
    )

    mc1, mc2 = st.columns(2)
    with mc1:
        st.markdown("**Wholesale (all formats)**")
        ws_margin = st.number_input(
            "Wholesale margin ×", min_value=1.0,
            value=float(settings.get("ws_margin") or 2.0),
            step=0.1, key="set_ws_margin"
        )
    with mc2:
        st.markdown("**Retail**")
        rt_margin_large = st.number_input(
            "Large cake ×", min_value=1.0,
            value=float(settings.get("rt_margin_large") or 3.0),
            step=0.1, key="set_rt_large"
        )
        rt_margin_ind = st.number_input(
            "Individual ×", min_value=1.0,
            value=float(settings.get("rt_margin_individual") or 3.0),
            step=0.1, key="set_rt_ind"
        )
        rt_margin_boc = st.number_input(
            "Bocado ×", min_value=1.0,
            value=float(settings.get("rt_margin_bocado") or 3.0),
            step=0.1, key="set_rt_boc"
        )

    st.divider()

    # ── Batch sizes ───────────────────────────────────────────────────────────
    st.markdown("### Batch size assumptions")
    st.caption("Labour cost is calculated assuming these production run sizes.")

    st.markdown("**Wholesale**")
    wb1, wb2, wb3 = st.columns(3)
    with wb1:
        ws_batch_large = st.number_input(
            "Large cakes", min_value=1,
            value=int(settings.get("ws_batch_large") or 20),
            key="set_ws_batch_large"
        )
    with wb2:
        ws_batch_ind = st.number_input(
            "Individual", min_value=1,
            value=int(settings.get("ws_batch_individual") or 100),
            key="set_ws_batch_ind"
        )
    with wb3:
        ws_batch_boc = st.number_input(
            "Bocado", min_value=1,
            value=int(settings.get("ws_batch_bocado") or 250),
            key="set_ws_batch_boc"
        )

    st.markdown("**Retail**")
    rb1, rb2, rb3 = st.columns(3)
    with rb1:
        rt_batch_large = st.number_input(
            "Large cakes", min_value=1,
            value=int(settings.get("rt_batch_large") or 1),
            key="set_rt_batch_large"
        )
    with rb2:
        rt_batch_ind = st.number_input(
            "Individual", min_value=1,
            value=int(settings.get("rt_batch_individual") or 4),
            key="set_rt_batch_ind"
        )
    with rb3:
        rt_batch_boc = st.number_input(
            "Bocado", min_value=1,
            value=int(settings.get("rt_batch_bocado") or 10),
            key="set_rt_batch_boc"
        )

    st.divider()

    # ── Format weights ────────────────────────────────────────────────────────
    st.markdown("### Format reference weights")
    st.caption(
        "Default weights for Individual and Bocado scaling "
        "when not set on the individual recipe."
    )

    fw1, fw2 = st.columns(2)
    with fw1:
        ind_weight = st.number_input(
            "Individual weight (g)", min_value=1.0,
            value=float(settings.get("individual_weight_g") or 100),
            key="set_ind_weight"
        )
    with fw2:
        boc_weight = st.number_input(
            "Bocado weight (g)", min_value=1.0,
            value=float(settings.get("bocado_weight_g") or 30),
            key="set_boc_weight"
        )

    st.divider()

    # ── Wholesale catalogue conditions ────────────────────────────────────────
    st.markdown("### Condiciones de pedido")
    st.caption(
        "Used in the wholesale catalogue PDF. "
        "Numeric fields can be referenced in the catalogue automatically. "
        "Text fields appear verbatim."
    )

    st.markdown("**Order & delivery**")
    nc1, nc2, nc3, nc4 = st.columns(4)
    with nc1:
        cond_min_units = st.number_input(
            "Min order (units)", min_value=0,
            value=int(settings.get("cond_min_order_units") or 50),
            key="set_cond_min_units"
        )
    with nc2:
        cond_min_value = st.number_input(
            "Min order (€)", min_value=0.0,
            value=float(settings.get("cond_min_order_value") or 150),
            key="set_cond_min_value"
        )
    with nc3:
        cond_delivery_charge = st.number_input(
            "Delivery charge (€)", min_value=0.0,
            value=float(settings.get("cond_delivery_charge") or 25),
            key="set_cond_delivery_charge"
        )
    with nc4:
        cond_delivery_threshold = st.number_input(
            "Free delivery above (€)", min_value=0.0,
            value=float(settings.get("cond_delivery_threshold") or 400),
            key="set_cond_delivery_threshold"
        )

    st.markdown("**Timing & payment**")
    nd1, nd2, nd3 = st.columns(3)
    with nd1:
        cond_lead_time = st.number_input(
            "Lead time (days)", min_value=0,
            value=int(settings.get("cond_lead_time_days") or 3),
            key="set_cond_lead_time"
        )
    with nd2:
        cond_payment_days = st.number_input(
            "Payment terms (days)", min_value=0,
            value=int(settings.get("cond_payment_days") or 15),
            key="set_cond_payment_days"
        )
    with nd3:
        cond_cancellation_hours = st.number_input(
            "Cancellation notice (hrs)", min_value=0,
            value=int(settings.get("cond_cancellation_hours") or 48),
            key="set_cond_cancellation"
        )

    st.markdown("**Price protection**")
    np1, np2, np3 = st.columns(3)
    with np1:
        cond_price_review = st.number_input(
            "Price review (months)", min_value=1,
            value=int(settings.get("cond_price_review_months") or 6),
            key="set_cond_price_review"
        )
    with np2:
        cond_price_variation = st.number_input(
            "Variation threshold (%)", min_value=0.0,
            value=float(settings.get("cond_price_variation_pct") or 5),
            key="set_cond_price_var",
            help="Trigger for unscheduled price adjustment"
        )
    with np3:
        cond_price_notice = st.number_input(
            "Change notice (days)", min_value=0,
            value=int(settings.get("cond_price_notice_days") or 30),
            key="set_cond_price_notice"
        )

    st.markdown("**Text conditions**")

    cond_allergen = st.text_area(
        "Allergen notice",
        value=settings.get("cond_allergen_notice") or
              "Todos los productos pueden contener trazas de los 14 alérgenos "
              "de declaración obligatoria según el Reglamento UE 1169/2011. "
              "Las fichas técnicas completas están disponibles bajo petición.",
        key="set_cond_allergen", height=75
    )
    cond_availability = st.text_area(
        "Availability notice",
        value=settings.get("cond_availability_notice") or
              "La disponibilidad de los productos está sujeta a la disponibilidad "
              "de materias primas y estacionalidad. Millington Cakes se reserva "
              "el derecho a sustituir o retirar productos con previo aviso.",
        key="set_cond_availability", height=75
    )
    cond_returns = st.text_area(
        "Returns / quality policy",
        value=settings.get("cond_returns_policy") or
              "En caso de incidencia en la calidad del producto, el cliente deberá "
              "notificarlo en el momento de la entrega. No se aceptarán devoluciones "
              "una vez aceptada la mercancía.",
        key="set_cond_returns", height=75
    )

    st.divider()

    # ── Save ──────────────────────────────────────────────────────────────────
    if st.button("💾 Save settings", type="primary",
                 use_container_width=True):
        db.save_settings({
            "id":                       settings.get("id"),
            "default_labour_rate":      labour_rate,
            "default_oven_rate":        oven_rate,
            "labour_power":             labour_power,
            "ws_margin":                ws_margin,
            "rt_margin_large":          rt_margin_large,
            "rt_margin_individual":     rt_margin_ind,
            "rt_margin_bocado":         rt_margin_boc,
            "ws_batch_large":           ws_batch_large,
            "ws_batch_individual":      ws_batch_ind,
            "ws_batch_bocado":          ws_batch_boc,
            "rt_batch_large":           rt_batch_large,
            "rt_batch_individual":      rt_batch_ind,
            "rt_batch_bocado":          rt_batch_boc,
            "individual_weight_g":      ind_weight,
            "bocado_weight_g":          boc_weight,
            "cond_min_order_units":     cond_min_units,
            "cond_min_order_value":     cond_min_value,
            "cond_delivery_charge":     cond_delivery_charge,
            "cond_delivery_threshold":  cond_delivery_threshold,
            "cond_lead_time_days":      cond_lead_time,
            "cond_payment_days":        cond_payment_days,
            "cond_cancellation_hours":  cond_cancellation_hours,
            "cond_price_review_months": cond_price_review,
            "cond_price_variation_pct": cond_price_variation,
            "cond_price_notice_days":   cond_price_notice,
            "cond_allergen_notice":     cond_allergen or None,
            "cond_availability_notice": cond_availability or None,
            "cond_returns_policy":      cond_returns or None,
        })
        st.success("Settings saved", icon="✅")
        st.rerun()

    st.divider()

    # ── Size code definitions ─────────────────────────────────────────────────
    st.markdown("### Códigos de tamaño")
    st.caption(
        "Códigos que aparecen en los SKUs y en el selector de variantes. "
        "El tier determina la base de coste de mano de obra para el cálculo."
    )

    defs = db.get_size_code_definitions()
    tiers = ["standard", "individual", "bocado"]
    tier_labels = {"standard": "Tarta / Caja", "individual": "Individual", "bocado": "Bocado"}

    for tier in tiers:
        tier_defs = [d for d in defs if d["tier"] == tier]
        st.markdown(f"**{tier_labels[tier]}**")

        if tier_defs:
            h0, h1, h2, h3 = st.columns([1, 3, 2, 0.5])
            h0.caption("Código"); h1.caption("Etiqueta"); h2.caption("Peso aprox. (g)"); h3.caption("")
            for d in tier_defs:
                c0, c1, c2, c3 = st.columns([1, 3, 2, 0.5])
                c0.code(d["code"])
                new_label = c1.text_input(
                    "label", value=d["label_es"], label_visibility="collapsed",
                    key=f"scd_label_{d['code']}"
                )
                new_weight = c2.number_input(
                    "weight", value=float(d["default_weight_g"] or 0),
                    min_value=0.0, label_visibility="collapsed",
                    key=f"scd_weight_{d['code']}"
                )
                with c3:
                    if st.button("💾", key=f"scd_save_{d['code']}", help="Guardar"):
                        try:
                            db.save_size_code_definition({
                                "code":              d["code"],
                                "label_es":          new_label,
                                "tier":              d["tier"],
                                "default_weight_g":  new_weight or None,
                                "sort_order":        d.get("sort_order", 100),
                            })
                            st.success(f"{d['code']} guardado", icon="✅")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))
        else:
            st.caption(f"Sin códigos definidos para {tier_labels[tier]}.")

    st.divider()
    with st.expander("➕ Añadir nuevo código de tamaño"):
        na, nb, nc, nd = st.columns([1, 3, 2, 2])
        with na:
            new_code = st.text_input("Código", placeholder="ej. CA", key="scd_new_code").upper().strip()
        with nb:
            new_label_es = st.text_input("Etiqueta", placeholder="ej. Capricho", key="scd_new_label")
        with nc:
            new_tier = st.selectbox(
                "Tier", options=tiers,
                format_func=lambda x: tier_labels[x],
                key="scd_new_tier"
            )
        with nd:
            new_def_weight = st.number_input(
                "Peso aprox. (g)", min_value=0.0, value=0.0, key="scd_new_weight"
            )
        if st.button("Añadir código", type="primary", key="scd_add"):
            if not new_code:
                st.error("El código es obligatorio.")
            elif not new_label_es:
                st.error("La etiqueta es obligatoria.")
            else:
                existing = {d["code"] for d in defs}
                if new_code in existing:
                    st.error(f"El código `{new_code}` ya existe.")
                else:
                    try:
                        db.save_size_code_definition({
                            "code":             new_code,
                            "label_es":         new_label_es,
                            "tier":             new_tier,
                            "default_weight_g": new_def_weight or None,
                            "sort_order":       100,
                        })
                        st.success(f"Código {new_code} añadido", icon="✅")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    st.divider()

    # ── Data ──────────────────────────────────────────────────────────────────
    st.markdown("### Data")
    st.caption("Connected to Supabase")
    st.markdown(
        f"Recipes: **{len(db.get_recipes())}** · "
        f"Ingredients: **{len(db.get_ingredients())}** · "
        f"Consumables: **{len(db.get_consumables())}**"
    )
