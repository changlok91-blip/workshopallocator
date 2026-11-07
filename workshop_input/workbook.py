"""In-memory Excel I/O. Never writes uploaded student data to disk."""
from io import BytesIO
from zipfile import ZipFile
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.comments import Comment
from .models import STUDENTS, WORKSHOPS, RULES, DEFAULTS


RULE_GUIDANCE = {
    "Workshops_Required_Per_Student": (
        "How many workshop sessions each student should attend. Example: 2 means "
        "100 students need 200 allocations. Applies to every student. Component 1 "
        "checks this value and capacity totals; it does not allocate students.",
        "Positive whole number, e.g. 2 or 3.",
    ),
    "Allow_Unranked_Allocation": (
        "Whether students may receive workshops they did not rank. FALSE means "
        "ranked choices only; TRUE permits other workshops. This permission is "
        "for the future allocation engine; no allocation is performed now.",
        "Excel TRUE or FALSE, or lowercase text true or false.",
    ),
    "Allow_Repeat_Workshop": (
        "Whether a student may attend the same Workshop_ID in different sessions. "
        "FALSE means different workshops; TRUE permits repeats. This permission "
        "is for the future allocation engine; no allocation is performed now.",
        "Excel TRUE or FALSE, or lowercase text true or false.",
    ),
    "Allocation_Strategy": (
        "Reserved for the future allocation engine. Keep Balanced for now: it is "
        "the only accepted setting. Its allocation behaviour is not implemented "
        "in Component 1.",
        "Balanced (exact spelling).",
    ),
    "Fairness_Top_K": (
        "Preference cutoff reserved for future fairness assessment. Example: 3 "
        "refers to each student's first three choices. It does not guarantee a "
        "top-three allocation. Component 1 validates the number only.",
        "Positive whole number, e.g. 3.",
    ),
    "Lottery_Seed": (
        "Number reserved for reproducible random decisions in the future engine. "
        "Keep 42 unless you intend to change its random sequence. It is not a "
        "student priority score and has no allocation effect in Component 1.",
        "Whole number of 0 or more, e.g. 42.",
    ),
}


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
    rules.append(["Rule", "Value", "Description", "Accepted_Values"])
    for key, value in DEFAULTS.items():
        rules.append([key, value, *RULE_GUIDANCE[key]])
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
    rules["A1"].comment = Comment(
        "These settings apply to every student. Keep rule names unchanged. "
        "Component 1 validates inputs only; allocation is not implemented.",
        "Workshop Input Validator",
    )
    rules["B1"].comment = Comment(
        "Edit values in this column. Starting values are the defaults. "
        "Description and Accepted_Values are guidance, not allocation inputs.",
        "Workshop Input Validator",
    )
    for letter, width in {"A": 42, "B": 16, "C": 85, "D": 44}.items():
        rules.column_dimensions[letter].width = width
    for row in rules.iter_rows(min_row=2):
        rules.row_dimensions[row[0].row].height = 90
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        row[1].fill = PatternFill("solid", fgColor="FFF2CC")
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
