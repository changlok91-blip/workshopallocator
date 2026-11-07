"""Workshop allocation application; solver, analytics and exports stay modular."""
import hashlib
import streamlit as st
from workshop_input.models import STUDENTS, RULES, STRATEGIES
from workshop_input.workbook import template_bytes, report_bytes
from workshop_input.validation import validate_workbook
from workshop_reporting.workspace import AllocationWorkspace, context_key
from workshop_reporting.analytics import comparison_table
from workshop_reporting.export import export_results
from workshop_reporting.ui import render_dashboard, render_students, render_workshops, render_manual, MIME

st.set_page_config(page_title="Workshop Allocation Optimiser", page_icon="📋", layout="wide")
st.title("Workshop Allocation Optimiser")
st.caption("Validate · Allocate · Compare · Review · Export")
st.info("Student data stays in this application's server memory. No student data is sent to external services. "
        "Use synthetic data on public deployments; keep real records in an appropriately secured environment.")
sections = st.tabs(["1. Upload & Validate", "2. Run Allocation", "3. Compare Strategies",
                    "4. Student Allocations", "5. Workshop Rosters", "6. Manual Adjustments", "7. Export Results"])
if "workspace" not in st.session_state:
    st.session_state["workspace"] = AllocationWorkspace()
workspace = st.session_state["workspace"]
with sections[0]:
    st.subheader("Upload & Validate")
    st.write("Download a template, complete its three worksheets, and upload it here.")
    left, right = st.columns(2)
    left.download_button("Download blank template", template_bytes(), "workshop_template.xlsx", MIME)
    right.download_button("Download synthetic example", template_bytes(example=True), "synthetic_example.xlsx", MIME)
    st.caption("Edit the yellow Value cells in Rules. Workshop IDs ignore trailing spaces with warnings. "
               "The original workbook is never modified.")
    upload = st.file_uploader("Completed Excel workbook (.xlsx, up to 10 MB)", type=["xlsx"])
    fingerprint = hashlib.sha256(upload.getvalue()).hexdigest() if upload is not None else None
    if workspace.set_input(fingerprint):
        st.warning("The uploaded input changed. Previous results are outdated and were cleared; manual locks were cleared for the new input.")
    result = None
    if upload is not None:
        if upload.size > 10 * 1024 * 1024:
            st.error("Workbook exceeds the 10 MB limit.")
        else:
            cached = st.session_state.get("validated_input")
            if cached is None or cached[0] != fingerprint:
                cached = (fingerprint, validate_workbook(upload.getvalue()))
                st.session_state["validated_input"] = cached
            result = cached[1]
            if result.valid:
                st.success("Workbook passed input validation. Run allocation to evaluate its allocation outcomes.")
            else:
                st.error("Workbook has validation errors. Correct the indicated cells and upload it again.")
            for severity, label in [("error", "Errors"), ("warning", "Warnings")]:
                issues = result.issue_frame()
                filtered = issues[issues.severity == severity]
                if not filtered.empty:
                    st.subheader(f"{label} ({len(filtered)})")
                    st.dataframe(filtered.drop(columns="severity"), hide_index=True, width="stretch")
            st.download_button("Download validation report (JSON)", report_bytes(result), "validation_report.json", "application/json")
            for name, frame in result.frames.items():
                with st.expander(name):
                    preview = frame.astype({"Value": "string"}) if name == RULES and "Value" in frame else frame
                    st.dataframe(preview, hide_index=True, width="stretch")
    else:
        st.session_state.pop("validated_input", None)
    with st.expander("Input rules and privacy"):
        st.write("Keep the three-sheet format and consecutive Choice_N headers. Missing preferences are preserved. "
                 "Allow_Unranked_Allocation controls eligibility for every student, including blank-choice students; "
                 "all students are optimised together. Capacity, exact Day/Time_Slot clashes and repeat-workshop rules remain hard constraints. "
                 "Descriptions in older templates referring to a future engine or blank-choice overrides are historical; "
                 "the current workbook rule values and explicit app overrides below are applied. "
                 "Uploads, locks and audit records are session-local and are not persisted to a database.")
valid_input = result is not None and result.valid and result.data is not None
with sections[1]:
    st.subheader("Run Allocation")
    workbook_strategy = result.data.rules["Allocation_Strategy"] if valid_input else "Balanced"
    strategy_key = f"allocation_strategy_{workbook_strategy}"
    strategy = st.selectbox("Allocation strategy", STRATEGIES, index=STRATEGIES.index(workbook_strategy), key=strategy_key,
                           help="Temporary setting for this run. Selecting a previously compared strategy displays its current result without solving again.")
    st.caption("All strategies first maximise fully allocated students, then assigned places. "
               "Balanced next favours top-K coverage; First-Choice Priority favours first choices; "
               "Overall Satisfaction favours preference points; Maximum Participation favours students with at least one place.")
    seconds = st.number_input("Solver time limit (seconds)", min_value=1, max_value=120, value=30)
    if valid_input:
        with st.expander("Settings from Excel and temporary app settings"):
            st.write("Excel rules (not modified)")
            st.json(result.data.rules)
            st.write("Temporary run settings", {"Allocation_Strategy": strategy, "Time_Limit_Seconds": seconds})
            st.caption(f"The same {len(workspace.locks)} manual locks apply to every strategy.")
    if st.button("Run Allocation", disabled=not valid_input, type="primary"):
        try:
            with st.spinner("Optimising all students and sessions together…"):
                workspace.run(result, strategy, seconds)
        except ValueError as exc:
            st.error("Cannot allocate this input. Previous valid results were preserved.")
            st.text(str(exc))

with sections[2]:
    st.subheader("Compare Strategies")
    st.write("Run all four strategies with the same workbook, hard constraints, lottery seed, manual locks and per-strategy time limit. "
             "No strategy is universally best; they prioritise different outcomes.")
    if st.button("Compare all four strategies", disabled=not valid_input):
        with st.spinner("Running four independent strategy solves…"):
            workspace.compare(result, seconds)
    current = workspace.current_records(result, seconds)
    comparison_current = valid_input and workspace.comparison_context == context_key(fingerprint, result.data, seconds, workspace.locks)
    comparisons = current if comparison_current else {}
    if comparison_current:
        st.dataframe(comparison_table(result.data, comparisons), hide_index=True, width="stretch")
        for name, message in workspace.comparison_errors.items():
            st.error(f"{name}: comparison failed.")
            st.text(message)
        for name, run in comparisons.items():
            if not run.allocation.optimal:
                st.warning(f"{name}: {run.allocation.status}; optimality was not proven. Compare this result with care.")
        if comparisons:
            viewed = st.selectbox("View compared strategy", list(comparisons), key="compared_strategy")
            def activate_comparison():
                st.session_state[strategy_key] = st.session_state["compared_strategy"]
            st.button("View selected strategy details", on_click=activate_comparison)
    else:
        st.info("No comparison has been run for the current workbook, settings and locks.")

current = workspace.current_records(result, seconds)
record = current.get(strategy)
if record is None:
    st.session_state.pop("allocation_result", None)
else:
    st.session_state["allocation_result"] = record.allocation
with sections[1]:
    if record is not None:
        render_dashboard(result.data, record)
    elif workspace.records:
        st.warning("Previous allocations are outdated for the selected strategy, settings or locks. Rerun allocation or comparison before reviewing or exporting.")
    else:
        st.info("Upload a valid workbook and run allocation to see the dashboard.")
with sections[3]:
    if record is not None:
        render_students(result.data, record)
    else:
        st.info("Run allocation for the current settings to view students.")
with sections[4]:
    if record is not None:
        render_workshops(result.data, record, workspace, seconds)
    else:
        st.info("Run allocation for the current settings to view workshop rosters.")
with sections[5]:
    notice = st.session_state.pop("manual_notice", None)
    if notice:
        st.success(notice)
    if valid_input:
        render_manual(result, record, workspace, strategy, seconds)
    else:
        st.info("Upload a valid workbook to review manual adjustments.")
with sections[6]:
    st.subheader("Export Results")
    if record is None:
        st.info("There is no current allocation to export. Run the selected strategy with the current settings first.")
    else:
        st.write(f"Exporting {record.allocation.strategy} · run {record.run_id[:8]} · "
                 f"{len(record.locks)} locks · optimality {'proven' if record.allocation.optimal else 'not proven'}.")
        st.caption("Includes eight sheets: summary, student allocations/status, rosters, utilisation, exceptions, "
                   "strategy comparison (if run), and settings/audit. User text is stored as literal text, never spreadsheet formulas.")
        try:
            export_key = (record.run_id, tuple(run.run_id for run in comparisons.values()), len(workspace.audit))
            cached = st.session_state.get("excel_export")
            if cached is None or cached[0] != export_key:
                content = export_results(result.data, record, fingerprint=fingerprint, seconds=seconds,
                    locks=workspace.locks, comparisons=comparisons, audit=workspace.audit)
                cached = (export_key, content)
                st.session_state["excel_export"] = cached
            st.download_button("Download allocation workbook", cached[1], "workshop_allocations.xlsx", MIME)
        except ValueError as exc:
            st.error("Export could not be generated for these settings.")
            st.text(str(exc))
