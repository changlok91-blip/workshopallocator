"""Hand-counted results: student proportions differ from seat proportions."""
import pandas as pd
import pytest
from workshop_input.models import ValidatedData
from workshop_allocation.models import AllocationResult, ASSIGNMENT_COLUMNS
from workshop_allocation.distribution import build_distributions


def example():
    students = pd.DataFrame({"Student_ID": ["S1", "S2", "S3", "S4"]})
    sessions = pd.DataFrame([
        ("A", "Art", "A1", 3), ("A", "Art", "A2", 3),
        ("B", "Building", "B1", 2), ("B", "Building", "B2", 2),
        ("C", "Cooking", "C1", 2), ("D", "Drama", "D1", 5),
    ], columns=["Workshop_ID", "Workshop_Name", "Session_ID", "Capacity"])
    assignments = pd.DataFrame([
        ("S1", "A", "A1", 1, False), ("S1", "B", "B1", 2, False),
        ("S2", "B", "B1", 2, False), ("S2", "B", "B2", 2, False),
        ("S3", "C", "C1", None, True),
    ], columns=["Student_ID", "Workshop_ID", "Session_ID", "Preference_Rank", "Is_Unranked_Allocation"])
    data = ValidatedData(students, sessions, {}, ["Choice_1", "Choice_2", "Choice_3"])
    allocation = AllocationResult(assignments, students.copy(), {})
    return data, allocation


def test_workshop_distribution_counts_places_and_distinct_students():
    data, allocation = example()
    result = build_distributions(data, allocation).workshops.set_index("Workshop_ID")
    assert result.loc["B", "Assigned_Places"] == 3
    assert result.loc["B", "Students"] == 2
    assert result.loc["B", "Sessions"] == 2
    assert result.loc["B", "Capacity"] == 4
    assert result.loc["B", "Available_Places"] == 1
    assert result.loc["B", "Filled (%)"] == 75
    assert result.loc["D", "Assigned_Places"] == 0
    assert result.loc["D", "Available_Places"] == 5
    assert result.Assigned_Places.sum() == 5


def test_each_choice_uses_all_students_and_counts_repeats_once():
    data, allocation = example()
    result = build_distributions(data, allocation).each_choice.set_index("Choice")
    assert result.loc["Choice 1", "Students (%)"] == 25
    assert result.loc["Choice 2", "Students (%)"] == 50
    assert result.loc["Choice 2", "Students"] == 2
    assert result.loc["Choice 3", "Students"] == 0
    assert result.loc["Unranked", "Students (%)"] == 25
    assert result.loc["No allocation", "Students (%)"] == 25
    assert result["Students (%)"].sum() == 125


def test_best_choice_categories_are_mutually_exclusive():
    data, allocation = example()
    result = build_distributions(data, allocation).best_choice.set_index("Choice")
    assert result.Students.to_dict() == {
        "Choice 1": 1, "Choice 2": 1, "Choice 3": 0, "Unranked only": 1, "No allocation": 1,
    }
    assert result["Students (%)"].sum() == pytest.approx(100)


def test_zero_allocations_keep_all_workshops_and_count_all_students():
    data, allocation = example()
    allocation.assignments = pd.DataFrame(columns=ASSIGNMENT_COLUMNS)
    result = build_distributions(data, allocation)
    assert len(result.workshops) == 4
    assert result.workshops.Assigned_Places.sum() == 0
    assert result.workshops["Filled (%)"].sum() == 0
    for frame in (result.each_choice, result.best_choice):
        assert frame.iloc[-1].Choice == "No allocation"
        assert frame.iloc[-1]["Students (%)"] == 100
        assert frame.Students.sum() == 4


def test_distribution_does_not_modify_inputs_and_supports_variable_ranks():
    data, allocation = example()
    before_sessions = data.sessions.copy(deep=True)
    data.choice_columns.extend([f"Choice_{rank}" for rank in range(4, 9)])
    allocation.assignments.loc[0, "Preference_Rank"] = 8
    expected = allocation.assignments.copy(deep=True)
    result = build_distributions(data, allocation)
    assert "Choice 8" in result.each_choice.Choice.tolist()
    assert result.each_choice.set_index("Choice").loc["Choice 8", "Students"] == 1
    pd.testing.assert_frame_equal(allocation.assignments, expected)
    pd.testing.assert_frame_equal(data.sessions, before_sessions)
