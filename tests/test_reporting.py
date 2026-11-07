"""Synthetic, hand-counted dashboard, locks, comparison and export tests."""
from io import BytesIO
from copy import deepcopy
import hashlib
import pandas as pd
import pytest
from openpyxl import load_workbook
from workshop_allocation import allocate
from workshop_allocation.locks import LockConflict, validate_locks
from workshop_reporting.analytics import (build_dashboard, comparison_table, filter_students,
    filter_sessions, session_roster, student_preferences, student_timetable)
from workshop_reporting.workspace import AllocationWorkspace, ensure_current
from workshop_reporting.export import export_results, export_session, SHEETS
from workshop_input.models import STRATEGIES, STUDENTS, WORKSHOPS
from workshop_input.validation import validate_workbook
from workshop_input.workbook import template_bytes
from test_allocation import workbook, sessions_for, assert_hard_constraints


def workspace():
    state = AllocationWorkspace()
    state.set_input(hashlib.sha256(b"synthetic input").hexdigest())
    return state


def example(**rules):
    return workbook([("S1", ["A", "B"]), ("S2", ["B", "A"])], sessions_for("AB", capacity=2), **rules)


def test_dashboard_student_and_assignment_denominators_and_actual_k():
    v = workbook([("S1", ["A"]), ("S2", [])], [
        ("A1", "A", "Monday", "09", 1), ("A2", "A", "Monday", "10", 1)],
        choices=3, Fairness_Top_K=1, Allow_Repeat_Workshop=True)
    result = allocate(v)
    # Metrics must be derived from actual records, even if cached counts differ.
    result.summary["first_choice_coverage"] = 999
    d = build_dashboard(v.data, result)
    assert d.metrics["first_choice_students"] == 1
    assert d.metrics["first_choice_students_pct"] == 50
    assert d.metrics["first_choice_assignments"] == 2
    assert d.metrics["fully_allocated_pct"] == 50
    assert d.metrics["top_k"] == 1
    assert d.metrics["top_k_students"] == 1
    assert d.metrics["utilisation_pct"] == 100
    assert d.ranks.Assignments.sum() == len(result.assignments) == 2
    assert d.ranks.iloc[0].Assignments == 2
    assert d.students.Student_ID.nunique() == len(d.students) == 2


def test_rosters_utilisation_and_student_timetables_match_assignments():
    v = example()
    result = allocate(v)
    d = build_dashboard(v.data, result)
    rosters = []
    for row in d.sessions.to_dict("records"):
        roster = session_roster(result, row["Session_ID"])
        assert len(roster) == row["Allocated"]
        assert row["Remaining"] == row["Capacity"] - len(roster)
        assert row["Utilisation (%)"] == len(roster) / row["Capacity"] * 100
        rosters.extend(roster[["Student_ID", "Session_ID"]].itertuples(index=False, name=None))
    assert sorted(rosters) == sorted(result.assignments[["Student_ID", "Session_ID"]].itertuples(index=False, name=None))
    for sid in v.data.students.Student_ID:
        timetable = student_timetable(result, sid)
        assert not timetable.Time_Slot.duplicated().any()
    result.assignments.loc[1, ["Student_ID", "Day", "Time_Slot"]] = result.assignments.loc[0, ["Student_ID", "Day", "Time_Slot"]].values
    with pytest.raises(ValueError, match="timetable conflict"):
        student_timetable(result, result.assignments.iloc[0].Student_ID)


def test_search_filters_and_original_preferences():
    v = example(Fairness_Top_K=1)
    result = allocate(v)
    d = build_dashboard(v.data, result)
    assert filter_students(d.students, search="s1", classes=["Synthetic Class"], statuses=["Fully allocated"], first=True, top_k=True, unranked=False).Student_ID.tolist() == ["S1"]
    assert filter_students(d.students, search="[").empty  # literal, not a regex
    assert len(filter_sessions(d.sessions, "MONDAY")) == 2
    assert filter_sessions(d.sessions, "session-A").Session_ID.tolist() == ["session-A"]
    assert student_preferences(v.data, "S2").Workshop_ID.tolist() == ["B", "A"]


def test_all_strategies_share_input_settings_and_locks_and_reproduce():
    v = example(Lottery_Seed=19)
    state = workspace()
    state.locks = (("S1", "session-A"),)
    records = state.compare(v, 10)
    assert set(records) == set(STRATEGIES)
    assert len({r.context for r in records.values()}) == 1
    assert all(r.fingerprint == state.fingerprint and r.locks == state.locks for r in records.values())
    for record in records.values():
        assert record.rules["Lottery_Seed"] == 19
        assert ("S1", "session-A") in set(record.allocation.assignments[["Student_ID", "Session_ID"]].itertuples(index=False, name=None))
        assert_hard_constraints(record.allocation, v)
    compared = comparison_table(v.data, records)
    assert len(compared) == 4
    assert "First-choice assignments" in compared
    again = state.compare(v, 10)
    for strategy in STRATEGIES:
        pd.testing.assert_frame_equal(records[strategy].allocation.assignments, again[strategy].allocation.assignments)


def test_valid_locks_and_unlock_survive_real_reoptimisation():
    v = example()
    state = workspace()
    state.run(v, "Balanced", 10)
    locked = (("S1", "session-A"), ("S1", "session-B"))
    record = state.apply_locks(v, locked, "Balanced", 10, "Lock sessions")
    assert state.locks == locked
    assert session_roster(record.allocation, "session-A", locked).Manually_Locked.sum() == 1
    rerun = state.run(v, "First-Choice Priority", 10)
    pairs = set(rerun.allocation.assignments[["Student_ID", "Session_ID"]].itertuples(index=False, name=None))
    assert set(locked) <= pairs
    assert_hard_constraints(rerun.allocation, v)
    state.apply_locks(v, locked[:1], "Balanced", 10, "Unlock session")
    assert state.locks == locked[:1]
    assert any(event["Action"] == "Unlock session" for event in state.audit)


@pytest.mark.parametrize("pairs,reason", [
    ((("unknown", "session-A"),), "Unknown Student_ID"),
    ((("S1", "unknown"),), "Unknown Session_ID"),
    ((("S1", "session-A"), ("S2", "session-A")), "capacity"),
])
def test_rejected_manual_lock_preserves_previous_locks_and_result(pairs, reason):
    v = workbook([("S1", ["A", "B"]), ("S2", ["A", "B"])], sessions_for("AB"))
    state = workspace()
    before = state.run(v, "Balanced", 10)
    previous_locks = state.locks
    with pytest.raises(LockConflict, match=reason):
        state.apply_locks(v, pairs, "Balanced", 10, "Invalid manual request")
    assert state.records["Balanced"] is before
    assert state.locks == previous_locks
    assert state.audit[-1]["Action"] == "Rejected manual change"


@pytest.mark.parametrize("rule,pairs,reason", [
    ({}, (("S1", "A1"), ("S1", "B1")), "clash"),
    ({}, (("S1", "A1"), ("S1", "A2")), "repeat Workshop_ID"),
    ({"Workshops_Required_Per_Student": 1}, (("S1", "A1"), ("S1", "A2")), "exceed workshops required"),
])
def test_conflicting_lock_combinations_identify_constraints(rule, pairs, reason):
    v = workbook([("S1", ["A", "B"])], [
        ("A1", "A", "Monday", "09", 2), ("B1", "B", "Monday", "09", 2),
        ("A2", "A", "Monday", "10", 2)], **rule)
    with pytest.raises(LockConflict, match=reason):
        allocate(v, locks=pairs)


def test_unranked_lock_is_only_allowed_with_permission():
    for allowed in [False, True]:
        v = workbook([("S1", ["A"])], sessions_for("AB"), Allow_Unranked_Allocation=allowed)
        if allowed:
            result = allocate(v, locks=[("S1", "session-B")])
            assert result.summary["unranked_assignments"] == 1
            assert_hard_constraints(result, v)
        else:
            with pytest.raises(LockConflict, match="outside submitted preferences"):
                allocate(v, locks=[("S1", "session-B")])


def test_zero_time_preserves_locks_without_optimality_claims():
    v = example()
    pairs = (("S1", "session-A"),)
    result = allocate(v, locks=pairs, time_limit_seconds=0)
    assert not result.optimal
    assert result.status == "UNKNOWN"
    assert set(result.assignments[["Student_ID", "Session_ID"]].itertuples(index=False, name=None)) == set(pairs)
    assert_hard_constraints(result, v)
    state = workspace()
    records = state.compare(v, 0)
    assert len(records) == 4
    assert not comparison_table(v.data, records)["Optimality proven"].any()
    assert set(comparison_table(v.data, records)["Solver status"]) == {"UNKNOWN"}


def test_stale_input_rules_time_and_locks_cannot_be_exported():
    v = example()
    state = workspace()
    record = state.run(v, "Balanced", 10)
    assert state.current_records(v, 11) == {}
    with pytest.raises(ValueError, match="outdated"):
        export_results(v.data, record, fingerprint=state.fingerprint, seconds=11, locks=())
    changed = deepcopy(v)
    changed.data.rules["Allow_Repeat_Workshop"] = True
    assert state.current_records(changed, 10) == {}
    with pytest.raises(ValueError, match="outdated"):
        ensure_current(record, state.fingerprint, changed.data, 10, ())
    with pytest.raises(ValueError, match="outdated"):
        export_results(v.data, record, fingerprint=state.fingerprint, seconds=10, locks=(("S1", "session-A"),))
    state.set_input("new fingerprint")
    assert not state.locks and not state.current_records(v, 10)
    with pytest.raises(ValueError, match="outdated"):
        export_results(v.data, record, fingerprint=state.fingerprint, seconds=10, locks=())


def test_eight_sheet_export_matches_selected_result_and_comparison():
    v = example(Fairness_Top_K=1)
    state = workspace()
    state.apply_locks(v, [("S1", "session-A")], "Balanced", 10, "Lock session")
    comparisons = state.compare(v, 10)
    record = comparisons["Overall Satisfaction"]
    content = export_results(v.data, record, fingerprint=state.fingerprint, seconds=10,
                             locks=state.locks, comparisons=comparisons, audit=state.audit)
    book = load_workbook(BytesIO(content))
    assert book.sheetnames == SHEETS
    summary_rows = {row[0].value: row[1] for row in book["Summary"].iter_rows(min_row=2)}
    assert summary_rows["Fully allocated (%)"].value == 100
    assert summary_rows["Fully allocated (%)"].number_format == '0.0"%"'
    for sheet in book:
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref
    exported = pd.read_excel(BytesIO(content), sheet_name="Student Allocations")
    assert len(exported) == len(record.allocation.assignments)
    assert exported.Manually_Locked.sum() == 1
    assert set(zip(exported.Student_ID, exported.Session_ID)) == set(record.allocation.assignments[["Student_ID", "Session_ID"]].itertuples(index=False, name=None))
    statuses = pd.read_excel(BytesIO(content), sheet_name="Student Status")
    assert statuses.Student_ID.nunique() == len(statuses) == 2
    comparison = pd.read_excel(BytesIO(content), sheet_name="Strategy Comparison")
    assert len(comparison) == 4
    audit = pd.read_excel(BytesIO(content), sheet_name="Settings & Audit")
    assert state.fingerprint in set(audit.Value.astype(str))
    assert "Manual lock" in set(audit.Category)
    roster = pd.read_excel(BytesIO(content), sheet_name="Workshop Rosters")
    assert roster.Session_ID.tolist() == sorted(roster.Session_ID.tolist())
    session_book = export_session(v.data, record, "session-A", fingerprint=state.fingerprint, seconds=10, locks=state.locks)
    participants = pd.read_excel(BytesIO(session_book))
    assert set(participants.Student_ID) == set(session_roster(record.allocation, "session-A").Student_ID)


def test_export_handles_zero_allocations_and_missing_comparison():
    v = workbook([("S1", [])], sessions_for("A"))
    state = workspace()
    record = state.run(v, "Balanced", 10)
    content = export_results(v.data, record, fingerprint=state.fingerprint, seconds=10, locks=())
    book = load_workbook(BytesIO(content))
    assert book.sheetnames == SHEETS
    assert book["Student Allocations"].max_row == 1
    exceptions = pd.read_excel(BytesIO(content), sheet_name="Exceptions")
    assert "Unallocated" in set(exceptions.Type)
    assert pd.read_excel(BytesIO(content), sheet_name="Workshop Utilisation").Allocated.sum() == 0


@pytest.mark.parametrize("dangerous", ["=1+1", "+cmd", "-1+2", "@SUM(A1)"])
def test_literal_user_text_never_exports_as_formula(dangerous):
    book = load_workbook(BytesIO(template_bytes(example=True)))
    book[STUDENTS]["A2"] = dangerous
    book[STUDENTS]["A2"].data_type = "s"
    book[WORKSHOPS]["B2"] = dangerous
    book[WORKSHOPS]["B3"] = dangerous
    for cell in (book[WORKSHOPS]["B2"], book[WORKSHOPS]["B3"]):
        cell.data_type = "s"
    out = BytesIO(); book.save(out)
    v = validate_workbook(out.getvalue())
    assert v.valid
    state = workspace()
    record = state.run(v, "Balanced", 10)
    content = export_results(v.data, record, fingerprint=state.fingerprint, seconds=10, locks=())
    result = load_workbook(BytesIO(content), data_only=False)
    matches = []
    for sheet in result:
        for row in sheet:
            for cell in row:
                assert cell.data_type != "f"
                if cell.value == dangerous:
                    matches.append(cell)
    assert matches and all(cell.data_type == "s" for cell in matches)


def test_larger_synthetic_dataset_preserves_constraints_and_reporting_counts():
    ids = [f"W{i:02}" for i in range(12)]
    students = [(f"S{i:03}", [ids[(i + j) % 12] for j in range(6)]) for i in range(60)]
    sessions = [(f"{wid}-{day}", wid, day, str(i % 4), 5)
                for i, wid in enumerate(ids) for day in ["Monday", "Tuesday"]]
    v = workbook(students, sessions)
    result = allocate(v, time_limit_seconds=3, locks=[("S000", "W00-Monday")])
    assert_hard_constraints(result, v)
    d = build_dashboard(v.data, result)
    assert d.metrics["total_students"] == 60
    assert d.metrics["distinct_workshops"] == 12
    assert d.metrics["total_sessions"] == 24
    assert d.sessions.Allocated.sum() == len(result.assignments)
    assert d.students.Workshops_Allocated.sum() == len(result.assignments)
    assert d.metrics["fully_allocated"] + d.metrics["partially_allocated"] + d.metrics["unallocated"] == 60


def test_backend_failure_after_validating_locks_is_atomic(monkeypatch):
    v = example()
    state = workspace()
    previous = state.run(v, "Balanced", 10)
    def fail(*args, **kwargs):
        raise ValueError("Solver could not apply this lock combination")
    monkeypatch.setattr(state, "_solve", fail)
    with pytest.raises(ValueError, match="lock combination"):
        state.apply_locks(v, [("S1", "session-A")], "Balanced", 10, "Request session")
    assert state.records["Balanced"] is previous
    assert state.locks == ()
    assert state.audit[-1]["Action"] == "Rejected manual change"


def test_stale_comparison_cannot_be_mixed_into_current_export():
    v = example()
    state = workspace()
    previous = state.compare(v, 10)
    current = state.run(v, "Balanced", 11)
    with pytest.raises(ValueError, match="outdated"):
        export_results(v.data, current, fingerprint=state.fingerprint, seconds=11, locks=(), comparisons=previous)


def test_invalid_input_cannot_be_used_for_manual_requests():
    state = workspace()
    with pytest.raises(ValueError, match="validation"):
        state.apply_locks(validate_workbook(b"invalid"), [("S1", "A")], "Balanced", 10, "Request session")
    assert not state.locks and not state.records
