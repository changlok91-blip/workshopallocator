from io import BytesIO
import json
import pytest
from openpyxl import load_workbook
from workshop_input.models import STUDENTS, WORKSHOPS, RULES
from workshop_input.validation import validate_workbook
from workshop_input.workbook import template_bytes, report_bytes


def edited(change):
    book = load_workbook(BytesIO(template_bytes(example=True)))
    change(book)
    out = BytesIO()
    book.save(out)
    return validate_workbook(out.getvalue())


def messages(result):
    return "\n".join(i.message for i in result.issues if i.severity == "error")


def test_valid_example_and_report():
    result = validate_workbook(template_bytes(example=True))
    assert result.valid and result.data is not None
    assert result.summary == dict(student_count=2, distinct_workshop_count=3, session_count=4, total_session_capacity=40, required_allocations=4)
    assert result.data.rules["Lottery_Seed"] == 42
    assert json.loads(report_bytes(result))["valid"]


@pytest.mark.parametrize("sheet,cell,value,expected", [
    (STUDENTS, "A3", "S001", "Duplicate Student_ID"),
    (WORKSHOPS, "C3", "R1", "Duplicate Session_ID"),
    (STUDENTS, "D2", "W01", "Duplicate preference"),
    (STUDENTS, "C2", "UNKNOWN", "Unknown Workshop_ID"),
    (WORKSHOPS, "B3", "Other name", "consistent"),
    (WORKSHOPS, "F2", 0, "positive integer"),
    (WORKSHOPS, "F2", 1.5, "positive integer"),
    (WORKSHOPS, "F2", True, "positive integer"),
    (WORKSHOPS, "F2", "10", "positive integer"),
    (RULES, "B2", -1, "Invalid rule"),
    (RULES, "B3", "yes", "Invalid rule"),
    (RULES, "B5", "Other", "Invalid rule"),
    (RULES, "B6", 0, "Invalid rule"),
    (RULES, "B7", -1, "Invalid rule"),
    (STUDENTS, "C2", None, "no blank gaps"),
    (STUDENTS, "C1", "Choice_0", "consecutive"),
    (STUDENTS, "A1", "student_id", "Required column"),
    (WORKSHOPS, "F2", "=1+1", "Formulas"),
    (STUDENTS, "A2", " S001", "surrounding whitespace"),
])
def test_invalid_cells(sheet, cell, value, expected):
    result = edited(lambda b: setattr(b[sheet][cell], "value", value))
    assert not result.valid and result.data is None
    assert expected in messages(result)
    assert any(i.sheet == sheet and i.row is not None for i in result.issues if i.severity == "error")


def test_empty_ranks():
    def change(b):
        for cell in ("C2", "D2", "E2"):
            b[STUDENTS][cell] = None
    assert "at least one" in messages(edited(change))


def test_dynamic_choices_and_trailing_blanks():
    def change(b):
        b[STUDENTS]["F1"] = "Choice_4"
        b[STUDENTS]["E2"] = None
    r = edited(change)
    assert r.valid and len(r.data.choice_columns) == 4


@pytest.mark.parametrize("change", [lambda b: b.remove(b[RULES]), lambda b: b.create_sheet("Extra"), lambda b: setattr(b[STUDENTS], "title", "Students")])
def test_sheet_contract(change):
    assert not edited(change).valid


def test_duplicate_headers():
    assert not edited(lambda b: setattr(b[STUDENTS]["D1"], "value", "Choice_1")).valid


def test_capacity_warning_and_no_feasibility_claim():
    def change(b):
        for row in range(2, 6):
            b[WORKSHOPS].cell(row, 6, 1)
        b[RULES]["B2"] = 3
    r = edited(change)
    assert r.valid
    assert any("below" in i.message for i in r.issues)
    assert any("does not prove feasibility" in i.message for i in r.issues)


def test_rule_defaults_are_explicit():
    r = edited(lambda b: b[RULES].delete_rows(2, 6))
    assert r.valid and r.data.rules["Workshops_Required_Per_Student"] == 2
    assert sum("default" in i.message for i in r.issues) == 6


def test_duplicate_rule():
    assert not edited(lambda b: b[RULES].append(["Lottery_Seed", 4])).valid


def test_unreadable_file():
    assert not validate_workbook(b"not an excel workbook").valid


def test_blank_template_requires_data():
    assert not validate_workbook(template_bytes()).valid


def test_does_not_change_input():
    raw = template_bytes(example=True)
    validate_workbook(raw)
    assert load_workbook(BytesIO(raw))[STUDENTS]["A2"].value == "S001"
