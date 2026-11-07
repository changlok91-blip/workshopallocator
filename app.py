import streamlit as st
from workshop_input.models import STUDENTS, WORKSHOPS
from workshop_input.workbook import template_bytes, report_bytes
from workshop_input.validation import validate_workbook

st.set_page_config(page_title="Workshop Input Validator", page_icon="📋", layout="wide")
st.title("Workshop Allocation Optimiser")
st.caption("Component 1 · Prepare and validate your input workbook")
st.info("Uploads are processed in memory by this local application. No allocation is performed. No student data is sent to external services.")
st.subheader("1. Download a workbook")
st.write("Choose a blank template or a synthetic example. Keep the three worksheet names and headers unchanged; add consecutive Choice_N columns as needed.")
left, right = st.columns(2)
left.download_button("Download blank template", template_bytes(), "workshop_template.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
right.download_button("Download synthetic example", template_bytes(example=True), "synthetic_example.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
with st.expander("Input rules and privacy"):
    st.markdown("""- Enter identifiers, Day and Time_Slot as text. Matching is exact and case-sensitive.
- Preferences start at Choice_1 with no gaps. Trailing blank choices are allowed; every student must rank at least one workshop.
- Capacity and workshop requirements must be positive integers.
- The Rules sheet uses Rule / Value columns. Omitted rules use documented defaults and generate warnings. Booleans accept Excel TRUE/FALSE or literal lowercase true/false. Balanced is the only strategy currently supported.
- Day + Time_Slot is the scheduling unit. Capacity belongs to each session.
- Formulas are rejected. Input is never silently corrected.
- Keep this app bound to localhost. Uploaded data is not saved, cached globally, or logged by the application. Downloads remain on your device; protect them as student records.""")
st.subheader("2. Upload and validate")
upload = st.file_uploader("Completed Excel workbook (.xlsx, up to 10 MB)", type=["xlsx"])
if upload is not None:
    if upload.size > 10 * 1024 * 1024:
        st.error("Workbook exceeds the 10 MB limit.")
        st.stop()
    result = validate_workbook(upload.getvalue())
    if result.valid:
        st.success("Workbook passed input validation. It is ready for a future allocation engine; feasibility has not been established.")
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
            st.dataframe(frame, hide_index=True, width="stretch")
    if result.data:
        with st.expander("Effective rules (including documented defaults)"):
            st.json(result.data.rules)
