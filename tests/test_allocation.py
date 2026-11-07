"""Hand-verifiable synthetic allocation scenarios and independent constraint checks."""
from io import BytesIO
import random

import pandas as pd
import pytest
from openpyxl import load_workbook
from ortools.sat.python import cp_model

from workshop_input.models import STUDENTS, WORKSHOPS, RULES, ValidationResult
from workshop_input.validation import validate_workbook
from workshop_input.workbook import template_bytes
from workshop_allocation import allocate, objective_order


STRATEGIES = (
    "Balanced", "First-Choice Priority", "Overall Satisfaction", "Maximum Participation",
)


def workbook(students, sessions, *, choices=None, **rules):
    """Each student is (ID, [ranked IDs]); session = (ID, workshop, day, slot, cap)."""
    n = choices or max(1, max((len(p) for _, p in students), default=1))
    book = load_workbook(BytesIO(template_bytes(choice_count=n)))
    for sid, prefs in students:
        book[STUDENTS].append([sid, "Synthetic Class", *prefs])
    for session_id, wid, day, slot, cap in sessions:
        book[WORKSHOPS].append([wid, f"Workshop {wid}", session_id, day, slot, cap])
    for row in book[RULES].iter_rows(min_row=2):
        if row[0].value in rules:
            row[1].value = rules[row[0].value]
    output = BytesIO()
    book.save(output)
    result = validate_workbook(output.getvalue())
    assert result.valid, result.issue_frame().to_dict("records")
    return result


def sessions_for(workshops, *, capacity=1, simultaneous=False):
    return [(f"session-{wid}", wid, "Monday", "09:00" if simultaneous else str(i), capacity)
            for i, wid in enumerate(workshops)]


def assert_hard_constraints(result, validation):
    """Check output independently against the validated source, not solver helpers."""
    data = validation.data
    assigned = result.assignments
    expected_columns = {
        "Student_ID", "Class", "Workshop_ID", "Workshop_Name", "Session_ID", "Day",
        "Time_Slot", "Preference_Rank", "Is_Unranked_Allocation",
    }
    assert expected_columns <= set(assigned.columns)
    sessions = data.sessions.set_index("Session_ID")
    students = data.students.set_index("Student_ID")
    assert not assigned.duplicated(["Student_ID", "Session_ID"]).any()
    for session_id, count in assigned.groupby("Session_ID").size().items():
        assert count <= sessions.loc[session_id, "Capacity"]
    for sid, group in assigned.groupby("Student_ID"):
        assert sid in students.index
        assert len(group) <= data.rules["Workshops_Required_Per_Student"]
        assert not group.duplicated(["Day", "Time_Slot"]).any()
        if not data.rules["Allow_Repeat_Workshop"]:
            assert not group.Workshop_ID.duplicated().any()
        prefs = {students.loc[sid, c]: int(c.split("_")[1]) for c in data.choice_columns
                 if pd.notna(students.loc[sid, c])}
        for row in group.to_dict("records"):
            source = sessions.loc[row["Session_ID"]]
            for column in ("Workshop_ID", "Workshop_Name", "Day", "Time_Slot"):
                assert row[column] == source[column]
            assert row["Class"] == students.loc[sid, "Class"]
            if row["Workshop_ID"] in prefs:
                assert row["Preference_Rank"] == prefs[row["Workshop_ID"]]
                assert not row["Is_Unranked_Allocation"]
            else:
                assert data.rules["Allow_Unranked_Allocation"]
                assert pd.isna(row["Preference_Rank"])
                assert row["Is_Unranked_Allocation"]
    assert result.summary["total_assigned_places"] == len(assigned)
    assert result.summary["ranked_assignments"] + result.summary["unranked_assignments"] == len(assigned)
    assert len(result.student_summary) == len(data.students)
    assert result.summary["students_fully_allocated"] == int(result.student_summary.Fully_Allocated.sum())
    assert result.summary["first_choice_coverage"] == int(result.student_summary.Has_First_Choice.sum())
    assert result.summary["top_k_coverage"] == int(result.student_summary.Has_Top_K_Choice.sum())


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_feasible_global_allocation_satisfies_all_hard_constraints(strategy):
    v = workbook([("S2", ["A", "B", "C"]), ("S1", ["B", "C", "A"])],
                 sessions_for("ABC", capacity=2), Allocation_Strategy=strategy)
    r = allocate(v)
    assert r.optimal
    assert r.summary["students_fully_allocated"] == 2
    assert r.summary["total_assigned_places"] == 4
    assert_hard_constraints(r, v)


def test_identical_day_slot_clashes_but_other_days_and_slots_do_not():
    v = workbook([("S", ["A", "B", "C", "D"])], [
        ("A1", "A", "Monday", "09:00", 1),
        ("B1", "B", "Monday", "09:00", 1),
        ("C1", "C", "Tuesday", "09:00", 1),
        ("D1", "D", "Tuesday", "10:00", 1),
    ], Workshops_Required_Per_Student=4)
    r = allocate(v)
    assert r.summary["total_assigned_places"] == 3
    assert r.summary["students_partially_allocated"] == 1
    assert_hard_constraints(r, v)


@pytest.mark.parametrize("repeat,allocated", [(False, 1), (True, 2)])
def test_repeat_rule_and_no_duplicate_preference_bonus(repeat, allocated):
    v = workbook([("S", ["A"])], [
        ("A1", "A", "Monday", "09:00", 1),
        ("A2", "A", "Monday", "10:00", 1),
    ], choices=4, Allow_Repeat_Workshop=repeat)
    r = allocate(v)
    assert len(r.assignments) == allocated
    assert r.summary["preference_satisfaction_score"] == 4
    assert r.summary["first_choice_coverage"] == 1
    assert r.summary["top_k_coverage"] == 1
    assert_hard_constraints(r, v)


@pytest.mark.parametrize("unranked,allocated,unranked_count", [(False, 1, 0), (True, 2, 1)])
def test_unranked_rule_is_strict_even_for_incomplete_preferences(unranked, allocated, unranked_count):
    v = workbook([("S", ["A"])], sessions_for("AB"), choices=3,
                 Allow_Unranked_Allocation=unranked)
    r = allocate(v)
    assert len(r.assignments) == allocated
    assert r.summary["unranked_assignments"] == unranked_count
    assert r.summary["preference_satisfaction_score"] == 3
    assert_hard_constraints(r, v)


@pytest.mark.parametrize("choices", [1, 2, 5, 8])
def test_variable_workshop_and_preference_counts(choices):
    ids = [f"W{i}" for i in range(choices)]
    v = workbook([("S", ids)], sessions_for(ids),
                 Workshops_Required_Per_Student=choices)
    r = allocate(v)
    assert len(r.assignments) == choices
    assert r.summary["preference_satisfaction_score"] == choices * (choices + 1) // 2
    assert_hard_constraints(r, v)


def test_over_demand_preserves_capacity_and_quantity():
    v = workbook([(f"S{i}", ["A", "B"]) for i in range(5)], sessions_for("AB"))
    r = allocate(v)
    assert r.summary["students_fully_allocated"] == 1
    assert r.summary["total_assigned_places"] == 2
    assert r.summary["students_with_no_allocation"] == 4
    assert r.warnings
    assert_hard_constraints(r, v)


def test_fully_allocated_priority_precedes_participation():
    # S0 can take both seats; S1/S2 can only take one each. Exactly one full
    # student outranks distributing one seat to each incomplete student.
    v = workbook([("S0", ["A", "B"]), ("S1", ["A"]), ("S2", ["B"])],
                 sessions_for("AB"), Allocation_Strategy="Maximum Participation")
    r = allocate(v)
    assert set(r.assignments.Student_ID) == {"S0"}
    assert r.summary["students_fully_allocated"] == 1
    assert_hard_constraints(r, v)


def test_participation_objective_distributes_remaining_places():
    v = workbook([(f"S{i}", ["A", "B", "C"]) for i in range(4)], [
        ("A1", "A", "Monday", "09:00", 2),
        ("B1", "B", "Monday", "10:00", 2),
        ("C1", "C", "Monday", "11:00", 1),
    ], Workshops_Required_Per_Student=3, Allocation_Strategy="Maximum Participation")
    r = allocate(v)
    assert r.summary["students_fully_allocated"] == 1
    assert len(r.assignments) == 5
    assert r.assignments.Student_ID.nunique() == 3
    assert_hard_constraints(r, v)


def test_balanced_improves_top_k_coverage_before_satisfaction():
    # Four one-seat workshops, all simultaneous. Enumerating 4! assignments
    # shows top-2 coverage 4 has best score 13, versus score 14 with coverage 3.
    prefs = [["D", "C", "B", "A"], ["A", "D", "B", "C"],
             ["B", "A", "C", "D"], ["B", "A", "C", "D"]]
    v = workbook([(str(i), p) for i, p in enumerate(prefs)],
                 sessions_for("ABCD", simultaneous=True),
                 Workshops_Required_Per_Student=1, Fairness_Top_K=2)
    balanced = allocate(v, strategy="Balanced")
    satisfaction = allocate(v, strategy="Overall Satisfaction")
    assert balanced.summary["top_k_coverage"] == 4
    assert balanced.summary["preference_satisfaction_score"] == 13
    assert satisfaction.summary["top_k_coverage"] == 3
    assert satisfaction.summary["preference_satisfaction_score"] == 14
    assert balanced.summary["students_fully_allocated"] == satisfaction.summary["students_fully_allocated"] == 4
    assert_hard_constraints(balanced, v)
    assert_hard_constraints(satisfaction, v)


def test_first_choice_priority_maximises_first_choice_coverage():
    prefs = [["D", "A", "C", "B"], ["A", "B", "D", "C"],
             ["D", "C", "A", "B"], ["C", "D", "A", "B"]]
    v = workbook([(str(i), p) for i, p in enumerate(prefs)],
                 sessions_for("ABCD", simultaneous=True), Workshops_Required_Per_Student=1)
    first = allocate(v, strategy="First-Choice Priority")
    satisfaction = allocate(v, strategy="Overall Satisfaction")
    assert first.summary["first_choice_coverage"] == 3
    assert first.summary["preference_satisfaction_score"] == 13
    assert satisfaction.summary["first_choice_coverage"] == 2
    assert satisfaction.summary["preference_satisfaction_score"] == 14
    assert_hard_constraints(first, v)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_reproducible_seed_and_excel_row_order(strategy):
    students = [(f"S{i}", ["A", "B", "C"]) for i in range(5)]
    sessions = sessions_for("ABC", capacity=2)
    v = workbook(students, sessions, Lottery_Seed=987654321)
    first = allocate(v, strategy=strategy)
    again = allocate(v, strategy=strategy)
    random.Random(12).shuffle(students)
    random.Random(99).shuffle(sessions)
    shuffled = allocate(workbook(students, sessions, Lottery_Seed=987654321), strategy=strategy)
    pd.testing.assert_frame_equal(first.assignments, again.assignments)
    pd.testing.assert_frame_equal(first.assignments, shuffled.assignments)
    pd.testing.assert_frame_equal(first.student_summary, shuffled.student_summary)


@pytest.mark.parametrize("unranked,allocated", [(False, 0), (True, 1)])
def test_no_choices_zero_or_unranked_allocation_is_safe(unranked, allocated):
    v = workbook([("S", [])], sessions_for("A"), Allow_Unranked_Allocation=unranked)
    r = allocate(v)
    assert r.optimal
    assert len(r.assignments) == allocated
    assert r.summary["students_fully_allocated"] == 0
    assert r.summary["students_with_no_allocation"] == (1 if not unranked else 0)
    assert_hard_constraints(r, v)


def test_invalid_workbooks_and_missing_validated_data_are_rejected():
    for invalid in [validate_workbook(b"invalid"), ValidationResult(), None]:
        with pytest.raises((ValueError, TypeError)):
            allocate(invalid)


def test_mutated_capacity_is_not_trusted():
    v = workbook([("S", ["A"])], sessions_for("A"))
    v.data.sessions.loc[0, "Capacity"] = -1
    with pytest.raises((ValueError, TypeError)):
        allocate(v)


@pytest.mark.parametrize("strategy,third,fourth", [
    ("Balanced", "top_k_coverage", "preference_satisfaction_score"),
    ("First-Choice Priority", "first_choice_coverage", "preference_satisfaction_score"),
    ("Overall Satisfaction", "preference_satisfaction_score", "top_k_coverage"),
    ("Maximum Participation", "participation", "preference_satisfaction_score"),
])
def test_each_strategy_has_exact_lexicographic_order(strategy, third, fourth):
    expected = ("fully_allocated", "total_assigned_places", third, fourth, "seeded_tie_break")
    assert tuple(objective_order(strategy)) == expected
    v = workbook([("S", ["A"])], sessions_for("A"), Workshops_Required_Per_Student=1)
    r = allocate(v, strategy=strategy)
    assert tuple(stage.name for stage in r.stages) == expected
    assert all(stage.proven_optimal for stage in r.stages)


def test_invalid_strategy_and_scoring_rejected():
    v = workbook([("S", ["A"])], sessions_for("A"))
    with pytest.raises(ValueError):
        allocate(v, strategy="Unknown")
    with pytest.raises(ValueError):
        allocate(v, scoring=lambda rank, n: 0.5)


def test_configurable_scoring_uses_common_workbook_scale():
    v = workbook([("S1", ["A"]), ("S2", ["B"])], sessions_for("AB", capacity=2),
                 choices=5, Workshops_Required_Per_Student=1)
    called = []
    def score(rank, max_columns):
        called.append((rank, max_columns))
        return (max_columns - rank + 1) * 10
    r = allocate(v, scoring=score)
    assert called and all(n == 5 for _, n in called)
    assert r.summary["preference_satisfaction_score"] == 100
    assert_hard_constraints(r, v)


def test_top_k_uses_submitted_choices_when_rank_cells_have_gaps():
    v = workbook([("S", [None, None, "A"])], sessions_for("A"),
                 Workshops_Required_Per_Student=1, Fairness_Top_K=1)
    r = allocate(v)
    assert r.summary["top_k_coverage"] == 1
    assert r.summary["first_choice_coverage"] == 0
    assert r.assignments.iloc[0].Preference_Rank == 3
    assert_hard_constraints(r, v)


def test_zero_time_budget_never_claims_optimality():
    v = workbook([("S", ["A"])], sessions_for("A"))
    r = allocate(v, time_limit_seconds=0)
    assert not r.optimal
    assert r.status != "OPTIMAL"
    assert r.warnings
    assert not any(stage.proven_optimal for stage in r.stages)
    assert_hard_constraints(r, v)


@pytest.mark.parametrize("unproven_status", [cp_model.FEASIBLE, cp_model.UNKNOWN])
def test_unproven_stage_stops_lexicographic_optimisation(monkeypatch, unproven_status):
    original = cp_model.CpSolver.solve
    calls = []
    def solve_feasible_on_third(self, model, *args, **kwargs):
        status = original(self, model, *args, **kwargs)
        calls.append(status)
        if len(calls) == 3 and status == cp_model.OPTIMAL:
            return unproven_status
        return status
    monkeypatch.setattr(cp_model.CpSolver, "solve", solve_feasible_on_third)
    v = workbook([("S", ["A", "B"])], sessions_for("AB"))
    r = allocate(v)
    assert len(calls) == 3
    assert len(r.stages) == 3
    assert r.stages[0].proven_optimal and r.stages[1].proven_optimal
    assert not r.stages[2].proven_optimal
    assert not r.optimal
    assert r.status != "OPTIMAL"
    assert len(r.assignments) == 2
    assert_hard_constraints(r, v)
