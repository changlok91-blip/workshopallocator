"""Aggregate an allocation for display, without changing inputs or solver results."""
from dataclasses import dataclass
import pandas as pd
from workshop_input.models import ValidatedData
from .models import AllocationResult


@dataclass
class AllocationDistributions:
    workshops: pd.DataFrame
    each_choice: pd.DataFrame
    best_choice: pd.DataFrame


def build_distributions(data: ValidatedData, allocation: AllocationResult) -> AllocationDistributions:
    assignments = allocation.assignments
    workshops = data.sessions.groupby(["Workshop_ID", "Workshop_Name"], as_index=False).agg(
        Sessions=("Session_ID", "size"), Capacity=("Capacity", "sum"),
    )
    counts = assignments.groupby("Workshop_ID").agg(
        Assigned_Places=("Student_ID", "size"), Students=("Student_ID", "nunique"),
    )
    workshops = workshops.merge(counts, on="Workshop_ID", how="left")
    for column in ("Assigned_Places", "Students"):
        workshops[column] = workshops[column].fillna(0).astype(int)
    workshops["Available_Places"] = workshops["Capacity"] - workshops["Assigned_Places"]
    workshops["Filled (%)"] = workshops["Assigned_Places"] / workshops["Capacity"] * 100
    workshops = workshops.sort_values("Workshop_ID").reset_index(drop=True)

    # Student-based denominators include students receiving no allocation.
    student_ids = allocation.student_summary["Student_ID"].tolist()
    total_students = len(student_ids)
    allocated_students = set(assignments["Student_ID"])
    ranked = assignments[assignments["Preference_Rank"].notna()]
    best_ranks = ranked.groupby("Student_ID")["Preference_Rank"].min().to_dict()
    unranked = assignments[assignments["Is_Unranked_Allocation"].astype(bool)]
    rank_numbers = sorted(int(column.split("_")[1]) for column in data.choice_columns)

    def row(label, count):
        return {"Choice": label, "Students": count,
                "Students (%)": count / total_students * 100 if total_students else 0.0}

    each_choice, best_choice = [], []
    for rank in rank_numbers:
        count = ranked.loc[ranked["Preference_Rank"] == rank, "Student_ID"].nunique()
        each_choice.append(row(f"Choice {rank}", int(count)))
        best_choice.append(row(f"Choice {rank}", sum(value == rank for value in best_ranks.values())))
    no_allocation = sum(sid not in allocated_students for sid in student_ids)
    each_choice.extend([
        row("Unranked", int(unranked["Student_ID"].nunique())),
        row("No allocation", no_allocation),
    ])
    best_choice.extend([
        row("Unranked only", sum(sid in allocated_students and sid not in best_ranks for sid in student_ids)),
        row("No allocation", no_allocation),
    ])
    return AllocationDistributions(workshops, pd.DataFrame(each_choice), pd.DataFrame(best_choice))
