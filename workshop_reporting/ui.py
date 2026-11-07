"""Streamlit presentation; solving and exports live in other modules."""
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st
from workshop_allocation.distribution import build_distributions
from .analytics import (build_dashboard, filter_students, filter_sessions,
                        student_preferences, student_timetable, session_roster)
from .export import export_session

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def render_dashboard(data, record):
    allocation = record.allocation
    dashboard = build_dashboard(data, allocation, record.locks)
    m = dashboard.metrics
    st.subheader("Allocation dashboard")
    if allocation.optimal:
        st.success("All objective stages, including seeded tie-breaking, are proven optimal. "
                   "Optimality does not guarantee that every student can be fully allocated.")
    else:
        st.warning("Best available feasible allocation; the complete objective sequence is not proven optimal. "
                   "Timed-out results should be compared with care.")
    local_run_time = datetime.fromisoformat(record.created_at).astimezone(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d %H:%M:%S %Z")
    st.caption(f"Strategy: {allocation.strategy} · Solver status: {allocation.status} · "
               f"Elapsed: {allocation.elapsed_seconds:.2f} seconds · Run time: {local_run_time}")
    for warning in allocation.warnings:
        st.warning(warning)
    columns = st.columns(4)
    for col, label, value in zip(columns, ["Total students", "Distinct workshops", "Workshop sessions", "Available places"],
                                 [m["total_students"], m["distinct_workshops"], m["total_sessions"], m["available_places"]]):
        col.metric(label, value)
    st.caption("Available places is total input capacity; remaining vacancies are shown in the session utilisation table.")
    columns = st.columns(4)
    for col, label, key in zip(columns, ["Fully allocated", "Partially allocated", "No allocation", "Assigned places"],
                               ["fully_allocated", "partially_allocated", "unallocated", "assignments"]):
        col.metric(label, m[key])
        if key + "_pct" in m:
            col.caption(f"{m[key + '_pct']:.1f}% of all students")
    columns = st.columns(4)
    for col, label, value in zip(columns, ["First-choice students", f"Top-{m['top_k']} students", "Unranked assignments", "Capacity used"],
                                 [m["first_choice_students"], m["top_k_students"], m["unranked_assignments"], f"{m['utilisation_pct']:.1f}%"]):
        col.metric(label, value)
    st.caption(f"First-choice coverage: {m['first_choice_students_pct']:.1f}% of all students. "
               f"Top-{m['top_k']} coverage: {m['top_k_students_pct']:.1f}% of all students; K is capped at each student's submitted choice count. "
               f"Preference points: {m['preference_score']}. Student coverage counts each student once; assignments count session seats.")
    st.subheader("Assignments by preference rank")
    st.bar_chart(dashboard.ranks, x="Preference", y="Assignments", sort=False)
    st.dataframe(dashboard.ranks, hide_index=True, width="stretch",
                 column_config={"Assignments (%)": st.column_config.NumberColumn(format="%.1f%%")})
    st.caption("Assignment percentages use assigned places as the denominator. Repeat sessions count separately here.")
    st.subheader("Workshop capacity utilisation")
    chart = dashboard.sessions.copy()
    chart["Session"] = chart.Workshop_ID + " · " + chart.Session_ID
    st.bar_chart(chart, x="Session", y="Utilisation (%)", sort=False)
    st.dataframe(dashboard.sessions, hide_index=True, width="stretch")
    distribution = build_distributions(data, allocation)
    st.subheader("Workshop allocation distribution")
    st.caption("Places count session seats; students count distinct participants per workshop. Unused workshops are included.")
    chart = distribution.workshops.copy()
    chart["Workshop"] = chart.Workshop_ID + " · " + chart.Workshop_Name
    st.bar_chart(chart, x="Workshop", y="Assigned_Places", sort=False)
    st.dataframe(distribution.workshops, hide_index=True, width="stretch")
    st.subheader("Preference choice distribution")
    st.caption(f"Student percentages use all {m['total_students']} students, including those without allocations.")
    each, best = st.tabs(["Each choice received", "Best choice per student"])
    with each:
        st.write("Students receiving each choice")
        st.caption("Students can appear at several ranks, so percentages may exceed 100% in total. Repeat sessions count once per student/rank.")
        st.bar_chart(distribution.each_choice, x="Choice", y="Students (%)", sort=False)
        st.dataframe(distribution.each_choice, hide_index=True, width="stretch")
    with best:
        st.write("Best ranked choice received by each student")
        st.caption("Each student appears once. Percentages total 100% before rounding; unranked only means no ranked choice was received.")
        st.bar_chart(distribution.best_choice, x="Choice", y="Students (%)", sort=False)
        st.dataframe(distribution.best_choice, hide_index=True, width="stretch")
    with st.expander("Solver stages and summary"):
        from dataclasses import asdict
        st.dataframe([asdict(stage) for stage in allocation.stages], hide_index=True, width="stretch")
        st.json(allocation.summary)
    return dashboard


def render_students(data, record):
    dashboard = build_dashboard(data, record.allocation, record.locks)
    st.subheader("Student allocations")
    search = st.text_input("Search Student ID")
    classes = st.multiselect("Class", sorted(data.students.Class.unique()), default=sorted(data.students.Class.unique()))
    statuses = st.multiselect("Allocation status", ["Fully allocated", "Partially allocated", "Unallocated"],
                             default=["Fully allocated", "Partially allocated", "Unallocated"])
    def flag(label):
        value = st.selectbox(label, ["Any", "Yes", "No"])
        return None if value == "Any" else value == "Yes"
    columns = st.columns(3)
    with columns[0]: first = flag("Received a first choice")
    with columns[1]: top = flag(f"Received a top-{data.rules['Fairness_Top_K']} choice")
    with columns[2]: unranked = flag("Received an unranked workshop")
    filtered = filter_students(dashboard.students, search=search, classes=classes, statuses=statuses,
                               first=first, top_k=top, unranked=unranked)
    st.dataframe(filtered, hide_index=True, width="stretch")
    if filtered.empty:
        st.info("No students match these filters.")
        return
    student_id = st.selectbox("View student", filtered.Student_ID.tolist())
    status = dashboard.students.set_index("Student_ID").loc[student_id]
    st.write(f"Required: {status.Workshops_Required} · Allocated: {status.Workshops_Allocated} · {status.Status}")
    st.write("Submitted preferences")
    st.dataframe(student_preferences(data, student_id), hide_index=True, width="stretch")
    st.subheader("Student allocation preview")
    selected = record.allocation.assignments[record.allocation.assignments.Student_ID == student_id]
    st.dataframe(selected, hide_index=True, width="stretch")
    st.write("Student timetable")
    if selected.empty:
        st.info("This student has no allocated sessions.")
    else:
        st.dataframe(student_timetable(record.allocation, student_id), hide_index=True, width="stretch")
    st.write("Manual locks", [session for sid, session in record.locks if sid == student_id])


def render_workshops(data, record, workspace, seconds):
    dashboard = build_dashboard(data, record.allocation, record.locks)
    st.subheader("Workshop rosters")
    search = st.text_input("Search workshop name, Workshop ID, Session ID, day or time slot")
    filtered = filter_sessions(dashboard.sessions, search)
    st.dataframe(filtered, hide_index=True, width="stretch")
    if filtered.empty:
        st.info("No sessions match this search.")
        return
    session_id = st.selectbox("View session", filtered.Session_ID.tolist())
    session = filtered.set_index("Session_ID").loc[session_id]
    st.text(f"{session.Workshop_ID} · {session.Workshop_Name} · {session.Day} / {session.Time_Slot}")
    st.write(f"Capacity: {session.Capacity} · Allocated: {session.Allocated} · Remaining: {session.Remaining} · Used: {session['Utilisation (%)']:.1f}%")
    st.dataframe(session_roster(record.allocation, session_id, record.locks), hide_index=True, width="stretch")
    st.download_button("Download session participant list", export_session(data, record, session_id,
        fingerprint=workspace.fingerprint, seconds=seconds, locks=workspace.locks),
        file_name="session_participants.xlsx", mime=MIME)


def render_manual(validation, record, workspace, strategy, seconds):
    data = validation.data
    st.subheader("Manual adjustments")
    st.caption("Locks require a Student_ID / Session_ID pair. Every change is validated and rerun through the optimiser before it is applied. "
               "To replace a locked session, unlock it first, then request the new one. Other unlocked assignments may change.")
    st.dataframe([{"Student_ID": sid, "Session_ID": session} for sid, session in workspace.locks], hide_index=True)
    def apply(proposed, action):
        try:
            with st.spinner("Checking locks and re-optimising…"):
                workspace.apply_locks(validation, proposed, strategy, seconds, action)
            st.session_state["manual_notice"] = "Manual change applied; locked allocations were preserved by the optimiser."
            st.rerun()
        except ValueError as exc:
            st.error("Manual change rejected. Previous locks and valid results have been preserved.")
            st.text(str(exc))
    if record is not None and not record.allocation.assignments.empty:
        existing = list(record.allocation.assignments[["Student_ID", "Session_ID"]].itertuples(index=False, name=None))
        pair = st.selectbox("Existing assignment to lock", existing, format_func=lambda pair: f"{pair[0]} → {pair[1]}")
        if st.button("Lock existing assignment and rerun"):
            apply((*workspace.locks, pair), "Lock existing assignment")
    student = st.selectbox("Student for requested session", sorted(data.students.Student_ID.tolist()))
    session_ids = sorted(data.sessions.Session_ID.tolist())
    labels = data.sessions.set_index("Session_ID")
    session = st.selectbox("Requested session", session_ids,
        format_func=lambda sid: f"{sid} · {labels.loc[sid, 'Workshop_ID']} · {labels.loc[sid, 'Day']} / {labels.loc[sid, 'Time_Slot']}")
    if st.button("Request session, lock and rerun"):
        apply((*workspace.locks, (student, session)), "Request and lock session")
    if workspace.locks:
        pair = st.selectbox("Lock to remove", workspace.locks, format_func=lambda pair: f"{pair[0]} → {pair[1]}")
        if st.button("Unlock and rerun"):
            apply(tuple(lock for lock in workspace.locks if lock != pair), "Unlock assignment")
    with st.expander("Manual change and run audit"):
        # JSON preserves nested lock details without mixed Arrow column types.
        st.json([event for event in workspace.audit if event["Input_Fingerprint"] == workspace.fingerprint])
