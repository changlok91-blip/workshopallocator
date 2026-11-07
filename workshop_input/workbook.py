"""In-memory Excel I/O. Never writes uploaded student data to disk."""
from io import BytesIO
from zipfile import ZipFile
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from .models import STUDENTS, WORKSHOPS, RULES, DEFAULTS


def template_bytes(example=False, choice_count=3):
    if type(choice_count) is not int or choice_count < 1:
        raise ValueError("choice_count must be a positive integer")
    book = Workbook()
    book.remove(book.active)
    students = book.create_sheet(STUDENTS)
    students.append(["Student_ID", "Class"] + [f"Choice_{n}" for n in range(1, choice_count + 1)])
    sessions = book.create_sheet(WORKSHOPS)
    sessions.append(["Workshop_ID", "Workshop_Name", "Session_ID", "Day", "Time_Slot", "Capacity"])
    rules = book.create_sheet(RULES)
    rules.append(["Rule", "Value"])
    for key, value in DEFAULTS.items():
        rules.append([key, value])
    if example:
        for sid, choices in [("S001", ["W01", "W02", "W03"]), ("S002", ["W02", "W03", "W01"])]:
            students.append([sid, "Example Class"] + choices[:choice_count])
        for row in [("W01", "Robotics", "R1", "Monday", "09:00", 10),
                    ("W01", "Robotics", "R2", "Monday", "10:00", 10),
                    ("W02", "Art", "A1", "Monday", "10:00", 10),
                    ("W03", "Drama", "D1", "Monday", "09:00", 10)]:
            sessions.append(row)
    for sheet in book:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="245C64")
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(44, max(18, len(str(column[0].value)) + 3))
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def read_workbook(content):
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("Upload too large")
    with ZipFile(BytesIO(content)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
            raise ValueError("Expanded workbook too large")
    return load_workbook(BytesIO(content), data_only=False, read_only=True)


def report_bytes(result):
    # JSON preserves user text literally and cannot trigger spreadsheet formulas.
    import json
    return json.dumps({"valid": result.valid, "summary": result.summary,
                       "issues": [vars(i) for i in result.issues]}, indent=2).encode()
