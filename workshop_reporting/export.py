"""Formatted in-memory XLSX output. All text is explicitly stored as text."""
from dataclasses import asdict
from io import BytesIO
import json
import pandas as pd
import ortools
import platform
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from .analytics import assignments_with_locks, build_dashboard, comparison_table, session_roster
from .workspace import ensure_current


SHEETS = ["Summary", "Student Allocations", "Student Status", "Workshop Rosters",
          "Workshop Utilisation", "Exceptions", "Strategy Comparison", "Settings & Audit"]


def _write_sheet(book, name, frame):
    sheet = book.create_sheet(name)
    sheet.append([str(column) for column in frame.columns])
    for record in frame.itertuples(index=False, name=None):
        values = []
        for value in record:
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, default=str)
            elif pd.isna(value):
                value = None
            elif hasattr(value, "item"):
                value = value.item()
            values.append(value)
        sheet.append(values)
    for row in sheet:
        for cell in row:
            # XLSX inline strings cannot execute formulas, including text starting
            # with =, +, -, @, tabs or whitespace. Preserve the literal user value.
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="245C64")
    if name == "Summary":
        for row in sheet.iter_rows(min_row=2):
            if str(row[0].value).endswith("(%)"):
                row[1].number_format = '0.0"%"'
            elif row[0].value == "Elapsed seconds":
                row[1].number_format = "0.00"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        width = min(60, max(14, max(len(str(cell.value or "")) for cell in column) + 2))
        sheet.column_dimensions[column[0].column_letter].width = width
        header = str(column[0].value)
        if "%" in header:
            for cell in column[1:]:
                cell.number_format = '0.0"%"'
        elif "seconds" in header.lower():
            for cell in column[1:]:
                cell.number_format = "0.00"
    return sheet


def _bytes(book):
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def export_session(data, record, session_id, *, fingerprint, seconds, locks):
    ensure_current(record, fingerprint, data, seconds, locks)
    if session_id not in set(data.sessions.Session_ID):
        raise ValueError("Unknown workshop session.")
    book = Workbook()
    book.remove(book.active)
    _write_sheet(book, "Participants", session_roster(record.allocation, session_id, record.locks))
    return _bytes(book)


def export_results(data, record, *, fingerprint, seconds, locks, comparisons=None, audit=()):
    ensure_current(record, fingerprint, data, seconds, locks)
    comparisons = comparisons or {}
    for other in comparisons.values():
        ensure_current(other, fingerprint, data, seconds, locks)
    allocation = record.allocation
    dashboard = build_dashboard(data, allocation, record.locks)
    summary = [{"Setting / Metric": key, "Value": value} for key, value in {
        "Selected strategy": allocation.strategy, "Input fingerprint (SHA-256)": fingerprint,
        **record.rules, **{key.replace("_pct", " (%)").replace("_", " ").capitalize(): value
                           for key, value in dashboard.metrics.items()}, "Solver status": allocation.status,
        "Optimality proven": allocation.optimal, "Elapsed seconds": allocation.elapsed_seconds,
    }.items()]
    summary.extend({"Setting / Metric": "Warning", "Value": warning} for warning in (*record.validation_warnings, *allocation.warnings))
    assigned = assignments_with_locks(allocation, record.locks)
    exceptions = []
    for row in dashboard.students[dashboard.students.Status != "Fully allocated"].to_dict("records"):
        exceptions.append({"Type": row["Status"], "Student_ID": row["Student_ID"], "Session_ID": None,
                           "Detail": f"{row['Workshops_Allocated']} of {row['Workshops_Required']} workshops allocated; no specific cause established."})
    for row in assigned[assigned.Is_Unranked_Allocation.astype(bool)].to_dict("records"):
        exceptions.append({"Type": "Unranked assignment", "Student_ID": row["Student_ID"], "Session_ID": row["Session_ID"], "Detail": row["Workshop_ID"]})
    exceptions.extend({"Type": "Solver warning", "Student_ID": None, "Session_ID": None, "Detail": w} for w in allocation.warnings)
    exceptions.extend({"Type": "Input warning", "Student_ID": None, "Session_ID": None, "Detail": w} for w in record.validation_warnings)
    active_audit = [event for event in audit if event.get("Input_Fingerprint") == fingerprint]
    exceptions.extend({"Type": "Rejected manual change (not applied)", "Student_ID": None, "Session_ID": None, "Detail": event["Detail"]}
                      for event in active_audit if event["Action"] == "Rejected manual change")
    settings = [{"Category": "Run", "Key": key, "Value": value} for key, value in {
        "Run_ID": record.run_id, "Run_Time_UTC": record.created_at, "Input_Fingerprint": fingerprint,
        "Selected_Strategy": allocation.strategy, "Time_Limit_Seconds": record.seconds,
        "Settings_Fingerprint": record.context, "OR_Tools_Version": ortools.__version__, "Python_Version": platform.python_version(),
        "Solver_Status": allocation.status, "Optimality_Proven": allocation.optimal,
        "Elapsed_Seconds": allocation.elapsed_seconds,
        "Formula_Safety": "All exported text is stored as literal XLSX strings, never formulas.",
    }.items()]
    settings.extend({"Category": "Applied rule", "Key": key, "Value": value} for key, value in record.rules.items())
    settings.extend({"Category": "Excel rule", "Key": key, "Value": value} for key, value in data.rules.items())
    settings.extend({"Category": "Manual lock", "Key": sid, "Value": session_id} for sid, session_id in record.locks)
    settings.extend({"Category": "Solver stage", "Key": stage.name, "Value": asdict(stage)} for stage in allocation.stages)
    settings.extend({"Category": "Audit", "Key": event["Time_UTC"], "Value": event} for event in active_audit)
    frames = [
        pd.DataFrame(summary), assigned, dashboard.students,
        assigned.sort_values(["Session_ID", "Class", "Student_ID"]), dashboard.sessions,
        pd.DataFrame(exceptions, columns=["Type", "Student_ID", "Session_ID", "Detail"]),
        comparison_table(data, comparisons) if comparisons else pd.DataFrame({"Status": ["No strategy comparison available for the active settings."]}),
        pd.DataFrame(settings),
    ]
    book = Workbook()
    book.remove(book.active)
    for name, frame in zip(SHEETS, frames):
        _write_sheet(book, name, frame)
    return _bytes(book)
