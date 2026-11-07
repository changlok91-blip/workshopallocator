"""Strict validation; no trimming, case folding, rank filling, or input repair."""
import re
from numbers import Real
import pandas as pd
from .models import (STUDENTS, WORKSHOPS, RULES, DEFAULTS, Issue,
                     ValidationResult, ValidatedData)
from .workbook import read_workbook


def positive_int(value):
    return isinstance(value, Real) and not isinstance(value, bool) and value > 0 and float(value).is_integer()


def validate_workbook(content):
    result = ValidationResult()
    def error(sheet, row, column, message):
        result.issues.append(Issue("error", sheet, row, column, message))
    try:
        book = read_workbook(content)
    except Exception:
        error("Workbook", None, "", "Cannot read this file as an .xlsx workbook within the 10 MB upload / 50 MB expanded limit.")
        return result
    try:
        expected = {STUDENTS, WORKSHOPS, RULES}
        for name in sorted(expected - set(book.sheetnames)):
            error(name, None, "", "Required worksheet is missing (names must match exactly).")
        for name in sorted(set(book.sheetnames) - expected):
            error(name, None, "", "Unexpected worksheet; exactly three named worksheets are required.")
        headers_by_sheet = {}
        rows_by_sheet = {}
        for name in expected & set(book.sheetnames):
            sheet = book[name]
            if (sheet.max_row or 0) > 100_000 or (sheet.max_column or 0) > 2_000:
                error(name, None, "", "Worksheet exceeds 100,000 rows or 2,000 columns.")
                return result
            values = list(sheet.iter_rows())
            headers = [c.value for c in values[0]] if values else []
            headers_by_sheet[name] = headers
            if any(not isinstance(h, str) or not h or h != h.strip() for h in headers) or len(set(headers)) != len(headers):
                error(name, 1, "", "Headers must be unique, nonblank text without surrounding whitespace.")
            rows = []
            for row_num, cells in enumerate(values[1:], 2):
                vals = [c.value for c in cells]
                if all(v is None for v in vals):
                    continue
                rows.append((row_num, dict(zip(headers, vals))))
                for cell in cells:
                    if cell.data_type == "f":
                        error(name, row_num, str(headers[cell.column - 1]), "Formulas are not accepted; enter a literal value.")
            rows_by_sheet[name] = rows
            if len(set(headers)) == len(headers) and all(isinstance(h, str) for h in headers):
                result.frames[name] = pd.DataFrame([r for _, r in rows], columns=headers)
        base = {STUDENTS: ["Student_ID", "Class"], WORKSHOPS: ["Workshop_ID", "Workshop_Name", "Session_ID", "Day", "Time_Slot", "Capacity"], RULES: ["Rule", "Value"]}
        choices = []
        for name, headers in headers_by_sheet.items():
            for h in base[name]:
                if h not in headers:
                    error(name, 1, h, "Required column is missing.")
            extra = [h for h in headers if h not in base[name]]
            if name == RULES:
                # Optional human-readable template guidance is not engine input.
                extra = [h for h in extra if h not in ("Description", "Accepted_Values")]
            if name == STUDENTS:
                choices = [h for h in extra if isinstance(h, str) and re.fullmatch(r"Choice_[1-9][0-9]*", h)]
                choices.sort(key=lambda h: int(h.split("_")[1]))
                if not choices or choices != [f"Choice_{i}" for i in range(1, len(choices) + 1)]:
                    error(name, 1, "Choice_N", "Require consecutive Choice_1 through Choice_N columns with no missing ranks.")
                extra = [h for h in extra if h not in choices]
            for h in extra:
                error(name, 1, str(h), "Unexpected column.")
        if any(i.row == 1 or i.row is None for i in result.issues):
            return result
        rules = dict(DEFAULTS)
        seen_rules = set()
        for row, record in rows_by_sheet[RULES]:
            key, value = record["Rule"], record["Value"]
            if key not in DEFAULTS:
                error(RULES, row, "Rule", "Unknown rule name.")
                continue
            if key in seen_rules:
                error(RULES, row, "Rule", "Duplicate rule.")
            seen_rules.add(key)
            valid = True
            if key in ("Workshops_Required_Per_Student", "Fairness_Top_K"):
                valid = positive_int(value)
            elif key == "Lottery_Seed":
                valid = isinstance(value, Real) and not isinstance(value, bool) and value >= 0 and float(value).is_integer()
            elif key.startswith("Allow_"):
                valid = type(value) is bool or value in ("true", "false")
                if valid:
                    value = value is True or value == "true"
            elif key == "Allocation_Strategy":
                valid = value == "Balanced"
            if not valid:
                error(RULES, row, "Value", "Invalid rule value; use positive integers, true/false, Balanced, or a nonnegative integer seed as appropriate.")
            else:
                if key in ("Workshops_Required_Per_Student", "Fairness_Top_K", "Lottery_Seed"):
                    value = int(value)
                rules[key] = value
        for key in DEFAULTS.keys() - seen_rules:
            result.issues.append(Issue("warning", RULES, None, key, f"Rule omitted; documented default {DEFAULTS[key]} applies."))
        def required_text(name, row, record, fields):
            for field in fields:
                value = record[field]
                if not isinstance(value, str) or not value or value != value.strip():
                    error(name, row, field, "Required text; use a literal text value without surrounding whitespace.")
        for name, id_field in [(STUDENTS, "Student_ID"), (WORKSHOPS, "Session_ID")]:
            seen = {}
            for row, record in rows_by_sheet[name]:
                value = record[id_field]
                if value in seen:
                    error(name, row, id_field, f"Duplicate {id_field}; first appears on row {seen[value]}.")
                else:
                    seen[value] = row
            if not rows_by_sheet[name]:
                error(name, 2, "", "At least one data row is required.")
        names = {}
        workshop_ids = set()
        total_capacity = 0
        capacity_valid = True
        for row, record in rows_by_sheet[WORKSHOPS]:
            required_text(WORKSHOPS, row, record, ["Workshop_ID", "Workshop_Name", "Session_ID", "Day", "Time_Slot"])
            wid = record["Workshop_ID"]
            if wid:
                workshop_ids.add(wid)
            if wid in names and names[wid] != record["Workshop_Name"]:
                error(WORKSHOPS, row, "Workshop_Name", "Workshop_Name must be consistent for the same Workshop_ID.")
            names[wid] = record["Workshop_Name"]
            if not positive_int(record["Capacity"]):
                error(WORKSHOPS, row, "Capacity", "Capacity must be a positive integer.")
                capacity_valid = False
            else:
                total_capacity += int(record["Capacity"])
        student_policy = []
        for row, record in rows_by_sheet[STUDENTS]:
            required_text(STUDENTS, row, record, ["Student_ID", "Class"])
            seen = set()
            blank_choices = []
            for choice in choices:
                value = record[choice]
                if value is None:
                    blank_choices.append(choice)
                    continue
                if value in seen:
                    error(STUDENTS, row, choice, "Duplicate preference choice within this student.")
                seen.add(value)
                if value not in workshop_ids:
                    error(STUDENTS, row, choice, "Unknown Workshop_ID; match the schedule exactly.")
            student_policy.append({
                "Student_ID": record["Student_ID"],
                "allocation_phase": "after_ranked_students" if not seen else "ranked_students",
                "blank_choice_columns": blank_choices,
                "allow_random_unranked": bool(blank_choices) or rules["Allow_Unranked_Allocation"],
            })
            if blank_choices:
                message = (
                    "No choices submitted: defer this student until students with preferences "
                    "have been considered, then randomly allocate from remaining eligible sessions."
                    if not seen else
                    "Incomplete preferences: preserve submitted ranks; blank choices permit random "
                    "unranked fallback after submitted preferences have been considered."
                )
                result.issues.append(Issue("warning", STUDENTS, row, "Choice_N", message +
                    " This blank-choice permission applies even when Allow_Unranked_Allocation is false. "
                    "Capacity, timetable and repeat-workshop rules still apply. No allocation is performed in Component 1."))
            elif len(seen & workshop_ids) < rules["Workshops_Required_Per_Student"] and not rules["Allow_Unranked_Allocation"] and not rules["Allow_Repeat_Workshop"]:
                result.issues.append(Issue("warning", STUDENTS, row, "Choice_N", "Fewer distinct ranked workshops than required allocations; review preferences or rules."))
        required = len(rows_by_sheet[STUDENTS]) * rules["Workshops_Required_Per_Student"]
        result.summary = {"student_count": len(rows_by_sheet[STUDENTS]), "distinct_workshop_count": len(workshop_ids), "session_count": len(rows_by_sheet[WORKSHOPS]), "total_session_capacity": total_capacity if capacity_valid else None, "required_allocations": required}
        if capacity_valid and total_capacity < required:
            result.issues.append(Issue("warning", WORKSHOPS, None, "Capacity", "Aggregate capacity is below total required allocations."))
        result.issues.append(Issue("warning", "Workbook", None, "", "Aggregate capacity does not prove feasibility. Scheduling conflicts, preferences and rules are not solved in Component 1."))
        if result.valid:
            result.data = ValidatedData(result.frames[STUDENTS].copy(), result.frames[WORKSHOPS].copy(), rules, choices, student_policy)
        return result
    except (ValueError, TypeError, KeyError, OSError, IndexError) as exc:
        error("Workbook", None, "", "Workbook contents are malformed or unsupported; export a fresh .xlsx using the template.")
        result.data = None
        return result
    finally:
        book.close()
