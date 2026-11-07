"""Capacity-safe global allocation, with one proven optimum per priority stage.

Input is the Component 1 validation result. Allocation never modifies source
frames, persists student data, or uses the network. Without locks, the all-zero
assignment respects the hard constraints. Valid locks form a feasible baseline.
"""
from collections import defaultdict
from io import BytesIO
import math
from numbers import Integral, Real
import random
import time
from typing import Callable

import pandas as pd
from openpyxl import Workbook
from ortools.sat.python import cp_model

from workshop_input.models import DEFAULTS, RULES, STUDENTS, WORKSHOPS, ValidatedData, ValidationResult
from workshop_input.validation import validate_workbook
from .locks import validate_locks
from .models import ASSIGNMENT_COLUMNS, STUDENT_COLUMNS, AllocationResult, StageResult


_ORDERS = {
    "Balanced": ("fully_allocated", "total_assigned_places", "top_k_coverage", "preference_satisfaction_score", "seeded_tie_break"),
    "First-Choice Priority": ("fully_allocated", "total_assigned_places", "first_choice_coverage", "preference_satisfaction_score", "seeded_tie_break"),
    "Overall Satisfaction": ("fully_allocated", "total_assigned_places", "preference_satisfaction_score", "top_k_coverage", "seeded_tie_break"),
    "Maximum Participation": ("fully_allocated", "total_assigned_places", "participation", "preference_satisfaction_score", "seeded_tie_break"),
}


def objective_order(strategy: str) -> tuple[str, ...]:
    """Return objective names in strict priority order; reject unknown strategies."""
    if strategy not in _ORDERS:
        raise ValueError(f"Unknown allocation strategy: {strategy!r}")
    return _ORDERS[strategy]


def default_scoring(rank: int, maximum_choices: int) -> int:
    """Linear points on the workbook-wide scale: N, N-1, ..., 1; unranked = 0."""
    return maximum_choices - rank + 1


def _validated_copy(validation: ValidationResult) -> ValidatedData:
    """Reuse Component 1 even if a caller mutated its public result DataFrames.

    Revalidating a memory-only workbook avoids trusting a manually constructed
    success flag or mutated inputs. No file is written and no data is repaired.
    """
    if not isinstance(validation, ValidationResult) or not validation.valid or not isinstance(validation.data, ValidatedData):
        raise ValueError("Allocation requires a successful Component 1 ValidationResult with validated data.")
    source = validation.data
    if set(source.rules) != set(DEFAULTS):
        raise ValueError("Validated rules are incomplete or contain unsupported rule names.")
    try:
        workbook = Workbook()
        workbook.remove(workbook.active)
        for title, frame in [(STUDENTS, source.students), (WORKSHOPS, source.sessions)]:
            sheet = workbook.create_sheet(title)
            sheet.append(list(frame.columns))
            for row_number, record in enumerate(frame.itertuples(index=False, name=None), start=2):
                for column_number, value in enumerate(record, start=1):
                    value = None if pd.isna(value) else value
                    cell = sheet.cell(row_number, column_number, value=value)
                    # Preserve validated literal identifiers, including '=…'.
                    if isinstance(value, str):
                        cell.data_type = "s"
        sheet = workbook.create_sheet(RULES)
        sheet.append(["Rule", "Value"])
        for key, value in source.rules.items():
            sheet.append([key, value])
        stream = BytesIO()
        workbook.save(stream)
        checked = validate_workbook(stream.getvalue())
    except Exception as exc:
        raise ValueError("Validated input is malformed; run workbook validation again.") from exc
    if not checked.valid or checked.data is None:
        raise ValueError("Input no longer passes Component 1 validation; correct the workbook and validate again.")
    if checked.data.choice_columns != source.choice_columns:
        raise ValueError("Choice-column metadata does not match validated student data.")
    return checked.data


def _any_indicator(model, terms, name):
    flag = model.new_bool_var(name)
    if terms:
        model.add(sum(terms) >= flag)
        model.add(sum(terms) <= len(terms) * flag)
    else:
        model.add(flag == 0)
    return flag


def allocate(
    validation: ValidationResult,
    *,
    strategy: str | None = None,
    time_limit_seconds: float = 30.0,
    scoring: Callable[[int, int], int] | None = None,
    locks=(),
) -> AllocationResult:
    """Allocate valid input without exceeding capacities or sacrificing proven priorities.

    ``time_limit_seconds`` is a global budget for the optimisation stages, not a
    separate budget for each stage. It includes model construction and validation.
    A zero budget returns the validated locks (or empty allocation), uncertified.
    ``scoring(rank, maximum_choice_columns)`` must return strictly descending,
    positive integer scores. Repeat sessions earn a workshop's score only once.
    Strict Allow_Unranked_Allocation applies also to students with blank choices.
    """
    started = time.monotonic()
    if isinstance(time_limit_seconds, bool) or not isinstance(time_limit_seconds, Real) or not math.isfinite(time_limit_seconds) or time_limit_seconds < 0:
        raise ValueError("time_limit_seconds must be a finite nonnegative number.")
    data = _validated_copy(validation)
    locked_pairs = validate_locks(data, locks)
    selected_strategy = data.rules["Allocation_Strategy"] if strategy is None else strategy
    order = objective_order(selected_strategy)
    rules = data.rules
    required = rules["Workshops_Required_Per_Student"]
    students = data.students.sort_values("Student_ID").to_dict("records")
    sessions = data.sessions.sort_values("Session_ID").to_dict("records")
    maximum_choices = len(data.choice_columns)
    score_fn = default_scoring if scoring is None else scoring
    scores = {}
    for rank in range(1, maximum_choices + 1):
        value = score_fn(rank, maximum_choices)
        if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
            raise ValueError("Scoring must return positive integer points for every ranked choice.")
        scores[rank] = int(value)
        if rank > 1 and scores[rank] >= scores[rank - 1]:
            raise ValueError("Scoring must give strictly higher points to higher-ranked choices.")
    # Keep every possible objective safely within CP-SAT's signed integer domain.
    if scores[1] * len(students) * maximum_choices >= 2**60:
        raise ValueError("Preference scores are too large for safe integer optimisation.")
    ranks = []
    top_sets = []
    for student in students:
        choices = {student[col]: int(col.split("_")[1]) for col in data.choice_columns if pd.notna(student[col])}
        ranks.append(choices)
        ranked_workshops = sorted(choices, key=choices.get)
        top_sets.append(set(ranked_workshops[:min(rules["Fairness_Top_K"], len(choices))]))

    model = cp_model.CpModel()
    x = {}
    per_student = defaultdict(list)
    per_session = defaultdict(list)
    per_slot = defaultdict(list)
    per_workshop = defaultdict(list)
    for i, student in enumerate(students):
        for j, session in enumerate(sessions):
            workshop = session["Workshop_ID"]
            if not rules["Allow_Unranked_Allocation"] and workshop not in ranks[i]:
                continue
            var = model.new_bool_var(f"x_{i}_{j}")
            x[i, j] = var
            per_student[i].append(var)
            per_session[j].append(var)
            per_slot[i, session["Day"], session["Time_Slot"]].append(var)
            per_workshop[i, workshop].append(var)
    student_indices = {row["Student_ID"]: i for i, row in enumerate(students)}
    session_indices = {row["Session_ID"]: j for j, row in enumerate(sessions)}
    locked_edges = {(student_indices[sid], session_indices[session_id]) for sid, session_id in locked_pairs}
    for edge in sorted(locked_edges):
        model.add(x[edge] == 1)
    for j, session in enumerate(sessions):
        model.add(sum(per_session[j]) <= min(int(session["Capacity"]), len(students)))
    for variables in per_slot.values():
        model.add(sum(variables) <= 1)
    if not rules["Allow_Repeat_Workshop"]:
        for variables in per_workshop.values():
            model.add(sum(variables) <= 1)

    full = []
    participating = []
    first = []
    top = []
    satisfaction = []
    for i, student in enumerate(students):
        terms = per_student[i]
        count = sum(terms)
        model.add(count <= min(required, len(terms)))
        flag = model.new_bool_var(f"full_{i}")
        if required > len(terms):
            model.add(flag == 0)
        else:
            model.add(count >= required).only_enforce_if(flag)
            model.add(count <= required - 1).only_enforce_if(flag.Not())
        full.append(flag)
        participating.append(_any_indicator(model, terms, f"participating_{i}"))
        first_terms = []
        top_terms = []
        for workshop in sorted(ranks[i]):
            workshop_terms = per_workshop[i, workshop]
            has_workshop = _any_indicator(model, workshop_terms, f"workshop_{i}_{workshop}")
            satisfaction.append(scores[ranks[i][workshop]] * has_workshop)
            if ranks[i][workshop] == 1:
                first_terms.append(has_workshop)
            if workshop in top_sets[i]:
                top_terms.append(has_workshop)
        first.append(_any_indicator(model, first_terms, f"first_{i}"))
        top.append(_any_indicator(model, top_terms, f"top_{i}"))

    # Random permutation assigns distinct, positive seeded edge priorities. Sorted
    # identifiers plus one deterministic worker also resolve any equal sums.
    tie_weights = list(range(1, len(x) + 1))
    random.Random(rules["Lottery_Seed"]).shuffle(tie_weights)
    objectives = {
        "fully_allocated": sum(full),
        "total_assigned_places": sum(x.values()),
        "top_k_coverage": sum(top),
        "first_choice_coverage": sum(first),
        "participation": sum(participating),
        "preference_satisfaction_score": sum(satisfaction),
        "seeded_tie_break": sum(weight * var for weight, var in zip(tie_weights, x.values())),
    }
    stages = []
    best = set(locked_edges)  # Validated locks alone are a feasible baseline.
    status_name = "UNKNOWN"
    has_solver_solution = False
    warnings = []
    validation_error = model.validate()
    if validation_error:
        raise ValueError(f"Allocation model could not be constructed safely: {validation_error}")
    previous_solver = None
    for name in order:
        remaining = float(time_limit_seconds) - (time.monotonic() - started)
        if remaining <= 0:
            stages.append(StageResult(name, "UNKNOWN", None, False))
            warnings.append("The solve time limit was reached. Returned allocation is feasible, but optimality was not proven for all priorities.")
            status_name = "FEASIBLE" if has_solver_solution else "UNKNOWN"
            break
        objective = objectives[name]
        if previous_solver is not None:
            # Preserve the current objective value already achieved by a known
            # feasible incumbent. This is a lower bound, not an optimality claim
            # or an equality fixing an unproven objective.
            model.add(objective >= int(previous_solver.value(objective)))
            model.clear_hints()
            for index in range(len(model.proto.variables)):
                variable = model.get_int_var_from_proto_index(index)
                model.add_hint(variable, int(previous_solver.value(variable)))
        model.maximize(objective)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = int(rules["Lottery_Seed"] % (2**31 - 1))
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.randomize_search = False
        status = solver.solve(model)
        status_name = solver.status_name(status)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            has_solver_solution = True
            previous_solver = solver
            best = {key for key, variable in x.items() if solver.value(variable)}
            value = int(solver.value(objective))
            stages.append(StageResult(name, status_name, value, status == cp_model.OPTIMAL))
            if status == cp_model.OPTIMAL:
                model.add(objective == value)
            else:
                warnings.append(f"Priority '{name}' returned a feasible solution without proof of optimality. Lower priorities were not run.")
                break
        else:
            stages.append(StageResult(name, status_name, None, False))
            if status == cp_model.MODEL_INVALID:
                raise ValueError("CP-SAT rejected the allocation model.")
            warnings.append(f"Priority '{name}' ended with {status_name}. The last known feasible allocation is returned without a complete optimality certificate.")
            status_name = "FEASIBLE" if has_solver_solution else "UNKNOWN"
            break
    optimal = len(stages) == len(order) and all(stage.proven_optimal for stage in stages)
    if optimal:
        status_name = "OPTIMAL"
    assignments = []
    by_student = defaultdict(list)
    for i, j in sorted(best):
        student, session = students[i], sessions[j]
        rank = ranks[i].get(session["Workshop_ID"])
        assignments.append({
            "Student_ID": student["Student_ID"], "Class": student["Class"],
            **{key: session[key] for key in ["Workshop_ID", "Workshop_Name", "Session_ID", "Day", "Time_Slot"]},
            "Preference_Rank": rank, "Is_Unranked_Allocation": rank is None,
        })
        by_student[i].append(j)
    student_summary = []
    satisfaction_total = 0
    for i, student in enumerate(students):
        allocated = by_student[i]
        awarded_workshops = {sessions[j]["Workshop_ID"] for j in allocated}
        satisfaction_total += sum(scores[rank] for workshop, rank in ranks[i].items() if workshop in awarded_workshops)
        student_summary.append({
            "Student_ID": student["Student_ID"], "Class": student["Class"],
            "Workshops_Required": required, "Workshops_Allocated": len(allocated),
            "Fully_Allocated": len(allocated) == required,
            "Has_Top_K_Choice": bool(awarded_workshops & top_sets[i]),
            "Has_First_Choice": any(ranks[i].get(workshop) == 1 for workshop in awarded_workshops),
        })
    assignment_frame = pd.DataFrame(assignments, columns=ASSIGNMENT_COLUMNS)
    assignment_frame["Preference_Rank"] = pd.array(assignment_frame["Preference_Rank"], dtype="Int64")
    students_frame = pd.DataFrame(student_summary, columns=STUDENT_COLUMNS)
    full_count = sum(row["Fully_Allocated"] for row in student_summary)
    no_count = sum(row["Workshops_Allocated"] == 0 for row in student_summary)
    elapsed = time.monotonic() - started
    summary = {
        "student_count": len(students), "session_count": len(sessions),
        "total_session_capacity": sum(int(session["Capacity"]) for session in sessions),
        "students_fully_allocated": full_count,
        "students_partially_allocated": len(students) - full_count - no_count,
        "students_with_no_allocation": no_count,
        "total_assigned_places": len(assignments),
        "first_choice_coverage": sum(row["Has_First_Choice"] for row in student_summary),
        "top_k_coverage": sum(row["Has_Top_K_Choice"] for row in student_summary),
        "ranked_assignments": sum(not row["Is_Unranked_Allocation"] for row in assignments),
        "unranked_assignments": sum(row["Is_Unranked_Allocation"] for row in assignments),
        "preference_satisfaction_score": satisfaction_total,
        "solver_status": status_name, "elapsed_seconds": elapsed, "optimal": optimal,
    }
    if full_count < len(students):
        warnings.append("Some students are incompletely allocated in this result. Capacity, eligibility, scheduling and workshop rules interact; this result alone does not identify a specific cause. No hard constraints were relaxed.")
    return AllocationResult(
        assignments=assignment_frame, student_summary=students_frame, summary=summary,
        warnings=warnings, stages=stages, status=status_name, optimal=optimal,
        elapsed_seconds=elapsed, strategy=selected_strategy,
    )
