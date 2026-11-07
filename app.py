import hashlib
from dataclasses import asdict
import streamlit as st
from workshop_input.models import STUDENTS, RULES, STRATEGIES
from workshop_allocation import allocate
from workshop_allocation.distribution import build_distributions
from workshop_input.workbook import template_bytes, report_bytes
from workshop_input.validation import validate_workbook

st.set_page_config(page_title="Workshop Input Validator", page_icon="📋", layout="wide")
st.title("Workshop Allocation Optimiser")
st.caption("Components 1 & 2 · Validate inputs and allocate workshops")
st.info("Uploads are processed in memory by this local application. Allocation runs locally with OR-Tools. No student data is sent to external services.")
st.subheader("1. Download a workbook")
st.write("Choose a blank template or a synthetic example. Keep the three worksheet names and headers unchanged; add consecutive Choice_N columns as needed.")
st.caption("In the Rules sheet, edit the yellow Value cells. Each rule has a plain-language description and accepted values beside it. These settings apply to every student.")
left, right = st.columns(2)
left.download_button("Download blank template", template_bytes(), "workshop_template.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
right.download_button("Download synthetic example", template_bytes(example=True), "synthetic_example.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
with st.expander("Input rules and privacy"):
    st.markdown("""- Enter identifiers, Day and Time_Slot as text. Workshop IDs ignore trailing spaces; matching otherwise remains exact and case-sensitive.
- Blank preferences are allowed and submitted ranks are preserved. Allow_Unranked_Allocation controls eligibility for every student, including blank or partial rows. All students are optimised together; there is no sequential allocation.
- Capacity and workshop requirements must be positive integers.
- The Rules sheet uses Rule / Value columns, with optional Description / Accepted_Values guidance. Edit Value cells; keep Rule names unchanged. Omitted rules use documented defaults and generate warnings. Booleans accept Excel TRUE/FALSE or literal lowercase true/false. The engine supports Balanced, First-Choice Priority, Overall Satisfaction and Maximum Participation.
- Day + Time_Slot is the scheduling unit. Capacity belongs to each session.
- Formulas are rejected. Ignored trailing spaces in workshop IDs are reported as warnings; previews show the IDs used for validation. Other input is not silently corrected.
- Keep this app bound to localhost. Uploaded data is not saved, cached globally, or logged by the application. Downloads remain on your device; protect them as student records.""")
st.subheader("2. Upload and validate")
upload = st.file_uploader("Completed Excel workbook (.xlsx, up to 10 MB)", type=["xlsx"])
result = None
if upload is not None:
    if upload.size > 10 * 1024 * 1024:
        st.error("Workbook exceeds the 10 MB limit.")
        st.stop()
    result = validate_workbook(upload.getvalue())
    if result.valid:
        st.success("Workbook passed input validation. Run allocation below to find a feasible allocation; validation alone does not prove feasibility.")
    else:
        st.error("Workbook has validation errors. Correct the indicated Excel rows and upload it again.")
    if result.summary:
        columns = st.columns(4)
        for col, label, key in zip(columns, ["Students", "Distinct workshops", "Sessions", "Total capacity"], ["student_count", "distinct_workshop_count", "session_count", "total_session_capacity"]):
            col.metric(label, result.summary[key] if result.summary[key] is not None else "Invalid")
        st.caption(f"Required allocations: {result.summary['required_allocations']}")
    for severity, label in [("error", "Errors"), ("warning", "Warnings")]:
        issues = result.issue_frame()
        filtered = issues[issues.severity == severity]
        if not filtered.empty:
            st.subheader(f"{label} ({len(filtered)})")
            st.dataframe(filtered.drop(columns="severity"), hide_index=True, width="stretch")
    st.download_button("Download validation report (JSON)", report_bytes(result), "validation_report.json", "application/json")
    st.subheader("Validated data" if result.valid else "Input preview · contains errors")
    for name, frame in result.frames.items():
        with st.expander(name, expanded=result.valid and name == STUDENTS):
            # Rule values mix booleans, numbers and text; render a text-only preview
            # without changing the typed input used by validation or the engine.
            preview = frame.astype({"Value": "string"}) if name == RULES and "Value" in frame else frame
            st.dataframe(preview, hide_index=True, width="stretch")
    if result.data:
        with st.expander("Preference eligibility policy"):
            st.dataframe(result.data.student_allocation_policy, hide_index=True, width="stretch")
        with st.expander("Effective rules (including documented defaults)"):
            st.json(result.data.rules)


st.subheader("3. Allocate workshops")
valid_input = result is not None and result.valid and result.data is not None
workbook_strategy = result.data.rules["Allocation_Strategy"] if valid_input else "Balanced"
strategy = st.selectbox(
    "Allocation strategy", STRATEGIES, index=STRATEGIES.index(workbook_strategy),
    key=f"allocation_strategy_{workbook_strategy}",
    help="Overrides the workbook strategy for this run; the workbook is not modified.",
)
st.caption("Every strategy first maximises fully allocated students, then total assigned places. "
           "Balanced next favours top-K coverage; First-Choice Priority favours first choices; "
           "Overall Satisfaction favours preference points; Maximum Participation favours students with at least one place.")
if valid_input:
    st.write("Workbook rule settings")
    st.json({**result.data.rules, "Allocation_Strategy": strategy})
    st.info("Component 2 uses strict global eligibility: unranked workshops require "
            "Allow_Unranked_Allocation=true, including blank-choice students. This supersedes "
            "the earlier blank-choice exception. Any Component 1 template notes referring to "
            "a future engine or blank-choice overrides are superseded by these settings.")
seconds = st.number_input("Solver time limit (seconds)", min_value=1, max_value=120, value=30)
run_key = (hashlib.sha256(upload.getvalue()).hexdigest(), strategy, seconds) if valid_input else None
if st.session_state.get("allocation_key") != run_key:
    st.session_state.pop("allocation_result", None)
    st.session_state.pop("allocation_key", None)
if st.button("Run Allocation", disabled=not valid_input, type="primary"):
    try:
        with st.spinner("Optimising all students and sessions together…"):
            allocation = allocate(result, strategy=strategy, time_limit_seconds=float(seconds))
        st.session_state["allocation_key"] = run_key
        st.session_state["allocation_result"] = allocation
    except ValueError as exc:
        st.error(f"Cannot allocate this input: {exc}")
allocation = st.session_state.get("allocation_result")
if allocation is not None:
    if allocation.optimal:
        st.success("All objective stages, including seeded tie-breaking, are proven optimal. "
                   "Optimality does not guarantee that every student can be fully allocated.")
    else:
        st.warning("Best available feasible allocation; the complete objective sequence is not proven optimal.")
    st.caption(f"Strategy: {allocation.strategy} · Solver status: {allocation.status} · "
               f"Elapsed: {allocation.elapsed_seconds:.2f} seconds")
    cols = st.columns(4)
    for col, label, key in zip(cols,
            ["Fully allocated", "Partially allocated", "No allocation", "Assigned places"],
            ["students_fully_allocated", "students_partially_allocated", "students_with_no_allocation", "total_assigned_places"]):
        col.metric(label, allocation.summary[key])
    st.write(f"First-choice coverage: {allocation.summary['first_choice_coverage']} / {allocation.summary['student_count']} students · "
             f"Top-K coverage: {allocation.summary['top_k_coverage']} / {allocation.summary['student_count']} students · "
             f"Preference points: {allocation.summary['preference_satisfaction_score']}")
    for warning in allocation.warnings:
        st.warning(warning)
    distribution = build_distributions(result.data, allocation)
    st.subheader("Workshop allocation distribution")
    st.caption("Assigned places count session seats; students count distinct students in each workshop. "
               "Repeat sessions can therefore use more places than the number of students. "
               "Capacity is summed across the workshop's sessions; unused workshops are included.")
    workshop_chart = distribution.workshops.copy()
    workshop_chart["Workshop"] = workshop_chart["Workshop_ID"] + " · " + workshop_chart["Workshop_Name"]
    st.bar_chart(workshop_chart, x="Workshop", y="Assigned_Places", sort=False)
    st.dataframe(distribution.workshops, hide_index=True, width="stretch",
                 column_config={"Filled (%)": st.column_config.NumberColumn(format="%.1f%%")})
    st.subheader("Preference choice distribution")
    st.caption(f"Percentages use all {allocation.summary['student_count']} students, including those with no allocation. "
               "Choice 1 means first choice, Choice 2 means second choice, and so on.")
    each_tab, best_tab = st.tabs(["Each choice received", "Best choice per student"])
    with each_tab:
        st.write("Students receiving each choice")
        st.caption("A student is counted once per choice, even with repeated sessions. Students may receive "
                   "several choices, so these percentages can total more than 100%. Unranked means at least one unranked assignment.")
        st.bar_chart(distribution.each_choice, x="Choice", y="Students (%)", sort=False)
        st.dataframe(distribution.each_choice, hide_index=True, width="stretch",
                     column_config={"Students (%)": st.column_config.NumberColumn(format="%.1f%%")})
    with best_tab:
        st.write("Best ranked choice received by each student")
        st.caption("Each student appears in exactly one category. Percentages total 100% before rounding. "
                   "Unranked only means the student received places but no ranked choice.")
        st.bar_chart(distribution.best_choice, x="Choice", y="Students (%)", sort=False)
        st.dataframe(distribution.best_choice, hide_index=True, width="stretch",
                     column_config={"Students (%)": st.column_config.NumberColumn(format="%.1f%%")})
    st.subheader("Student allocation preview")
    st.dataframe(allocation.assignments, hide_index=True, width="stretch")
    st.subheader("Per-student allocation summary")
    st.dataframe(allocation.student_summary, hide_index=True, width="stretch")
    incomplete = allocation.student_summary[~allocation.student_summary["Fully_Allocated"]]
    if not incomplete.empty:
        st.write("Students with incomplete allocations")
        st.dataframe(incomplete, hide_index=True, width="stretch")
    with st.expander("Solver stages and summary"):
        st.dataframe([asdict(stage) for stage in allocation.stages], hide_index=True, width="stretch")
        st.json(allocation.summary)
