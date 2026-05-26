# screen_todos.py
# =============================================================================
# Team to-do list.
# Tasks have: description, priority, responsible, due date, status, notes.
# Filter by person, sort by priority then due date.
# Completed tasks collapsible at the bottom.
# =============================================================================

from datetime import date, timedelta
import streamlit as st
import millington_db as db

TEAM       = ["Christine", "Peter", "Blanca"]
PRIORITIES = ["Alta", "Media", "Baja"]
STATUSES   = ["Por hacer", "En curso", "Hecho"]

PRIORITY_COLOUR = {"Alta": "🔴", "Media": "🟡", "Baja": "🟢"}
STATUS_COLOUR   = {"Por hacer": "⬜", "En curso": "🔵", "Hecho": "✅"}

PRIORITY_ORDER  = {"Alta": 0, "Media": 1, "Baja": 2}


# =============================================================================
# Main screen
# =============================================================================

def screen_todos():
    st.title("📋 Tareas")

    # ── Filter bar ────────────────────────────────────────────────────────────
    col_who, col_status, col_spacer = st.columns([2, 2, 3])
    with col_who:
        filter_who = st.selectbox(
            "Filtrar por persona",
            ["Todas"] + TEAM,
            key="todo_filter_who",
            label_visibility="collapsed",
        )
    with col_status:
        filter_status = st.selectbox(
            "Filtrar por estado",
            ["Activas", "Todas", "Hecho"],
            key="todo_filter_status",
            label_visibility="collapsed",
        )

    st.divider()

    # ── Load tasks ────────────────────────────────────────────────────────────
    try:
        all_tasks = db.get_todos()
    except Exception as e:
        st.error(f"Error cargando tareas: {e}")
        all_tasks = []

    # Apply filters
    tasks = all_tasks
    if filter_who != "Todas":
        tasks = [t for t in tasks if t.get("responsible") == filter_who]
    if filter_status == "Activas":
        tasks = [t for t in tasks if t.get("status") != "Hecho"]
    elif filter_status == "Hecho":
        tasks = [t for t in tasks if t.get("status") == "Hecho"]

    # Sort: priority then due date
    def sort_key(t):
        p = PRIORITY_ORDER.get(t.get("priority", "Baja"), 2)
        d = t.get("due_date") or "9999-12-31"
        return (p, str(d))

    open_tasks = sorted(
        [t for t in tasks if t.get("status") != "Hecho"],
        key=sort_key
    )
    done_tasks = sorted(
        [t for t in tasks if t.get("status") == "Hecho"],
        key=sort_key
    )

    # ── Open tasks ────────────────────────────────────────────────────────────
    if open_tasks:
        for task in open_tasks:
            _task_card(task)
    else:
        st.caption("No hay tareas activas." if filter_status == "Activas"
                   else "No hay tareas.")

    # ── Completed tasks (collapsible) ─────────────────────────────────────────
    if done_tasks and filter_status != "Activas":
        st.divider()
        with st.expander(f"✅ Tareas completadas ({len(done_tasks)})", expanded=False):
            for task in done_tasks:
                _task_card(task, compact=True)

    # ── Add new task ──────────────────────────────────────────────────────────
    st.divider()
    _add_task_form()


# =============================================================================
# Task card
# =============================================================================

def _task_card(task: dict, compact: bool = False):
    tid       = task["id"]
    desc      = task.get("description", "")
    notes     = task.get("notes") or ""
    priority  = task.get("priority", "Media")
    resp      = task.get("responsible", "—")
    status    = task.get("status", "Por hacer")
    due_raw   = task.get("due_date")
    due_str   = str(due_raw)[:10] if due_raw else "—"

    # Overdue flag
    overdue = False
    if due_raw and status != "Hecho":
        try:
            due_date = date.fromisoformat(str(due_raw)[:10])
            overdue  = due_date < date.today()
        except Exception:
            pass

    prio_icon   = PRIORITY_COLOUR.get(priority, "🟡")
    status_icon = STATUS_COLOUR.get(status, "⬜")
    due_display = f"⚠️ {due_str}" if overdue else due_str

    # Card container
    with st.container():
        if compact:
            # Compact row for completed tasks
            c1, c2, c3, c4 = st.columns([0.3, 4, 1.5, 1.5])
            c1.markdown(status_icon)
            c2.markdown(f"~~{desc[:80]}~~" if status == "Hecho" else desc[:80])
            c3.markdown(f"<small>{resp}</small>", unsafe_allow_html=True)
            c4.markdown(f"<small>{due_str}</small>", unsafe_allow_html=True)
            return

        # Full card
        col_icon, col_main, col_meta = st.columns([0.3, 5, 2])

        with col_icon:
            st.markdown(f"{prio_icon}")

        with col_main:
            st.markdown(f"**{desc}**")
            if notes:
                st.caption(notes)

        with col_meta:
            st.markdown(
                f"<div style='font-size:12px;color:#6b7280;line-height:1.8'>"
                f"{resp}<br>"
                f"{'<span style=\"color:#ef4444\">' + due_display + '</span>' if overdue else due_display}<br>"
                f"{status_icon} {status}"
                f"</div>",
                unsafe_allow_html=True
            )

        # Edit controls in expander
        with st.expander("Editar", expanded=False):
            ec1, ec2, ec3 = st.columns(3)
            with ec1:
                new_status = st.selectbox(
                    "Estado", STATUSES,
                    index=STATUSES.index(status) if status in STATUSES else 0,
                    key=f"todo_status_{tid}"
                )
            with ec2:
                new_priority = st.selectbox(
                    "Prioridad", PRIORITIES,
                    index=PRIORITIES.index(priority) if priority in PRIORITIES else 1,
                    key=f"todo_prio_{tid}"
                )
            with ec3:
                new_resp = st.selectbox(
                    "Responsable", TEAM,
                    index=TEAM.index(resp) if resp in TEAM else 0,
                    key=f"todo_resp_{tid}"
                )

            new_due = st.date_input(
                "Fecha límite",
                value=date.fromisoformat(str(due_raw)[:10]) if due_raw else date.today(),
                key=f"todo_due_{tid}"
            )
            new_desc = st.text_area(
                "Descripción", value=desc,
                key=f"todo_desc_{tid}", height=80
            )
            new_notes = st.text_area(
                "Notas / contexto", value=notes,
                key=f"todo_notes_{tid}", height=60,
                placeholder="Contexto adicional, links, instrucciones..."
            )

            bc1, bc2 = st.columns([1, 4])
            with bc1:
                if st.button("💾 Guardar", key=f"todo_save_{tid}", type="primary"):
                    try:
                        db.save_todo({
                            "id":          tid,
                            "description": new_desc.strip(),
                            "notes":       new_notes.strip() or None,
                            "priority":    new_priority,
                            "responsible": new_resp,
                            "due_date":    new_due.isoformat(),
                            "status":      new_status,
                        })
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
            with bc2:
                if st.button("🗑️ Eliminar tarea", key=f"todo_del_{tid}"):
                    try:
                        db.delete_todo(tid)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

        st.markdown("---")


# =============================================================================
# Add task form
# =============================================================================

def _add_task_form():
    with st.expander("➕ Nueva tarea", expanded=False):
        new_desc = st.text_area(
            "Descripción *",
            key="todo_new_desc",
            height=80,
            placeholder="¿Qué hay que hacer?"
        )
        new_notes = st.text_area(
            "Notas / contexto",
            key="todo_new_notes",
            height=60,
            placeholder="Contexto adicional, instrucciones, links..."
        )
        nc1, nc2, nc3 = st.columns(3)
        with nc1:
            new_resp = st.selectbox("Responsable", TEAM, key="todo_new_resp")
        with nc2:
            new_prio = st.selectbox("Prioridad", PRIORITIES,
                                    index=1, key="todo_new_prio")
        with nc3:
            new_due = st.date_input(
                "Fecha límite",
                value=date.today() + timedelta(days=7),
                key="todo_new_due"
            )

        if st.button("Añadir tarea", type="primary", key="todo_new_save"):
            if not new_desc.strip():
                st.error("La descripción es obligatoria.")
            else:
                try:
                    db.save_todo({
                        "description": new_desc.strip(),
                        "notes":       new_notes.strip() or None,
                        "priority":    new_prio,
                        "responsible": new_resp,
                        "due_date":    new_due.isoformat(),
                        "status":      "Por hacer",
                    })
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al guardar: {e}")
