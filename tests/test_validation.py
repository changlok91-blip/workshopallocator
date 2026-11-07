from io import BytesIO
import json
import pytest
from openpyxl import load_workbook
from workshop_input.models import STUDENTS, WORKSHOPS, RULES, DEFAULTS
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
    result = edited(change)
    assert result.valid
    policy = result.data.student_allocation_policy[0]
    assert policy["allocation_phase"] == "after_ranked_students"
    assert policy["allow_random_unranked"]
    assert result.data.students.iloc[0]["Choice_1"] is None
    assert any("No choices submitted" in i.message for i in result.issues)


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


@pytest.mark.parametrize("example", [False, True])
def test_templates_explain_every_rule_without_extra_sheets(example):
    book = load_workbook(BytesIO(template_bytes(example=example)))
    assert book.sheetnames == [STUDENTS, WORKSHOPS, RULES]
    rules = book[RULES]
    records = list(rules.values)
    assert records[0] == ("Rule", "Value", "Description", "Accepted_Values")
    assert {row[0]: row[1] for row in records[1:]} == DEFAULTS
    for row in records[1:]:
        assert len(row[2]) > 80  # Visible explanation, not just a repeated name.
        assert row[3]
    assert "100 students need 200 allocations" in rules["C2"].value
    assert "same Workshop_ID" in rules["C4"].value
    assert "not implemented" in rules["C5"].value
    assert "does not guarantee" in rules["C6"].value
    assert rules["C2"].alignment.wrap_text


def test_legacy_two_column_rules_still_validate():
    result = edited(lambda b: b[RULES].delete_cols(3, 2))
    assert result.valid
    assert result.data.rules == DEFAULTS


def test_guidance_is_not_used_as_allocation_input():
    def change(book):
        book[RULES]["C2"] = "An organiser's note; not a rule value."
        book[RULES]["D2"] = None
        book[RULES]["B2"] = 3
    result = edited(change)
    assert result.valid
    assert result.data.rules == {**DEFAULTS, "Workshops_Required_Per_Student": 3}


def test_unknown_rules_column_is_still_rejected():
    result = edited(lambda b: setattr(b[RULES]["E1"], "value", "Other"))
    assert not result.valid
    assert "Unexpected column" in messages(result)


def test_partial_preferences_keep_ranks_and_allow_random_fallback():
    result = edited(lambda b: setattr(b[STUDENTS]["D2"], "value", None))
    assert result.valid
    assert result.data.rules["Allow_Unranked_Allocation"] is False
    policy = result.data.student_allocation_policy[0]
    assert policy["allocation_phase"] == "ranked_students"
    assert policy["blank_choice_columns"] == ["Choice_2"]
    assert policy["allow_random_unranked"]
    assert result.data.students.iloc[0]["Choice_3"] == "W03"
    assert result.data.students.iloc[0]["Choice_2"] is None
    assert result.data.student_allocation_policy[1]["allow_random_unranked"] is False


def test_workshop_references_ignore_trailing_spaces_with_warnings():
    def change(book):
        book[WORKSHOPS]["A2"] = "W01  "
        book[STUDENTS]["C2"] = "W01 "
    result = edited(change)
    assert result.valid
    assert result.data.sessions.iloc[0]["Workshop_ID"] == "W01"
    assert result.data.students.iloc[0]["Choice_1"] == "W01"
    assert sum("Trailing spaces ignored" in i.message for i in result.issues) == 2


def test_duplicate_choices_detected_after_ignoring_trailing_spaces():
    result = edited(lambda b: setattr(b[STUDENTS]["D2"], "value", "W01 "))
    assert not result.valid
    assert "Duplicate preference" in messages(result)


def test_inconsistent_workshop_names_detected_after_trimming_ids():
    def change(book):
        book[WORKSHOPS]["A3"] = "W01 "
        book[WORKSHOPS]["B3"] = "Other name"
    assert "consistent" in messages(edited(change))


def test_leading_workshop_spaces_still_rejected():
    assert not edited(lambda b: setattr(b[WORKSHOPS]["A2"], "value", " W01")).valid
