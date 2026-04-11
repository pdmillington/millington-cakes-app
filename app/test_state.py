# test_state.py — run with: streamlit run app/test_state.py
# Tests whether explicitly setting session state before widget render
# correctly updates the displayed value when switching between items.

import streamlit as st

ITEMS = {
    "Item A": {"name": "Alpha", "version": "01", "size": 22.0},
    "Item B": {"name": "Beta",  "version": "02", "size": 26.0},
    "Item C": {"name": "Gamma", "version": "03", "size": 18.0},
}

col_list, col_detail = st.columns([1, 2])

with col_list:
    st.markdown("**Items**")
    for label in ITEMS:
        if st.button(label, key=f"btn_{label}", use_container_width=True):
            # Clear old field state
            for k in [k for k in st.session_state if k.startswith("field_")]:
                del st.session_state[k]
            # Write new values directly into session state
            data = ITEMS[label]
            st.session_state["field_name"]    = data["name"]
            st.session_state["field_version"] = data["version"]
            st.session_state["field_size"]    = data["size"]
            st.session_state["selected"]      = label
            st.rerun()

with col_detail:
    selected = st.session_state.get("selected")
    if not selected:
        st.info("Select an item")
    else:
        st.markdown(f"**Editing: {selected}**")
        st.markdown(f"Session state before widgets: name={st.session_state.get('field_name')}, "
                    f"version={st.session_state.get('field_version')}, "
                    f"size={st.session_state.get('field_size')}")
        name    = st.text_input("Name",    key="field_name")
        version = st.text_input("Version", key="field_version")
        size    = st.number_input("Size",  key="field_size", min_value=0.0)
        st.markdown(f"Widget values: name={name}, version={version}, size={size}")
