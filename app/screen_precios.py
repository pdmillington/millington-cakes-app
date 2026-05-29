# screen_precios.py
# =============================================================================
# Pantalla de Precios — dos pestañas:
#
#   Pestaña 1: Gestión de precios   (screen_prices.py — matrix + client prices)
#   Pestaña 2: Análisis de repricing (screen_repricing.py — cost vs price)
#
# Both underlying screens are unchanged — this module simply wraps them
# inside a tab container so they appear as one coherent Prices section
# accessible from the Gestionar nav item.
# =============================================================================

import streamlit as st
from screen_prices    import _price_matrix, _client_prices
from screen_repricing import screen_repricing


def screen_precios():
    st.title("Precios")
    st.caption(
        "Gestiona los precios de venta y revisa márgenes frente a costes calculados."
    )

    tab_matrix, tab_clients, tab_repricing = st.tabs([
        "📊 Matriz de precios",
        "👥 Precios por cliente",
        "📈 Análisis de repricing",
    ])

    with tab_matrix:
        _price_matrix()

    with tab_clients:
        _client_prices()

    with tab_repricing:
        # Remove the title/caption since screen_precios already has a title
        screen_repricing(embedded=True)
