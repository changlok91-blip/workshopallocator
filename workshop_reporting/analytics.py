"""Derive metrics and viewers from assignment records, not cached summary counts."""
from dataclasses import dataclass
import pandas as pd


@dataclass
class Dashboard:
    metrics: dict
    students: pd.DataFrame
    sessions: pd.DataFrame
    ranks: pd.DataFrame


def assignments_with_locks(allocation, locks=()):
    frame = allocation.assignments.copy()
    locked = set(locks)
    frame["Manually_Locked"] = [(sid, session) in locked for sid, session in zip(frame.Student_ID, frame.Session_ID)]
    return frame


def build_dashboard(data, allocation, locks=()):
    assigned = allocation.assignments
    rows = []
    required = data.rules["Workshops_Required_Per_Student"]
    k = data.rules["Fairness_Top_K"]
    for student in data.students.sort_values("Student_ID").to_dict("records"):
        group = assigned[assigned.Student_ID == student["Student_ID"]]
        prefs = [student[col] for col in data.choice_columns if pd.notna(student[col])]
        workshops = set(group.Workshop_ID)
        count = len(group)
        rows.append({"Student_ID": student["Student_ID"], "Class": student["Class"],
                     "Workshops_Required": required, "Workshops_Allocated": count,
                     "Fully_Allocated": count == required,
                     "Status": "Fully allocated" if count == required else "Partially allocated" if count else "Unallocated",
                     "Has_First_Choice": pd.notna(student["Choice_1"]) and student["Choice_1"] in workshops,
                     "Has_Top_K_Choice": bool(workshops & set(prefs[:k])),
                     "Has_Unranked_Allocation": bool(group.Is_Unranked_Allocation.any())})
    students = pd.DataFrame(rows)
    counts = assigned.groupby("Session_ID").size().rename("Allocated")
    sessions = data.sessions.merge(counts, on="Session_ID", how="left").sort_values(["Workshop_ID", "Session_ID"]).reset_index(drop=True)
    sessions["Allocated"] = sessions.Allocated.fillna(0).astype(int)
    sessions["Remaining"] = sessions.Capacity - sessions.Allocated
    sessions["Utilisation (%)"] = sessions.Allocated / sessions.Capacity * 100
    total = len(students)
    def pct(n, denominator=total):
        return n / denominator * 100 if denominator else 0.0
    metrics = {
        "total_students": total, "distinct_workshops": data.sessions.Workshop_ID.nunique(),
        "total_sessions": len(sessions), "available_places": int(sessions.Capacity.sum()),
        "fully_allocated": int(students.Fully_Allocated.sum()),
        "partially_allocated": int((students.Status == "Partially allocated").sum()),
        "unallocated": int((students.Status == "Unallocated").sum()),
        "first_choice_students": int(students.Has_First_Choice.sum()),
        "top_k_students": int(students.Has_Top_K_Choice.sum()),
        "top_k": k, "assignments": len(assigned),
        "first_choice_assignments": int((assigned.Preference_Rank == 1).sum()),
        "unranked_assignments": int(assigned.Is_Unranked_Allocation.sum()),
        "preference_score": allocation.summary["preference_satisfaction_score"],
    }
    for key in ("fully_allocated", "partially_allocated", "unallocated", "first_choice_students", "top_k_students"):
        metrics[key + "_pct"] = pct(metrics[key])
    metrics["utilisation_pct"] = pct(len(assigned), metrics["available_places"])
    rank_rows = []
    for column in data.choice_columns:
        rank = int(column.split("_")[1])
        count = int((assigned.Preference_Rank == rank).sum())
        rank_rows.append({"Preference": f"Choice {rank}", "Assignments": count, "Assignments (%)": pct(count, len(assigned))})
    rank_rows.append({"Preference": "Unranked", "Assignments": metrics["unranked_assignments"], "Assignments (%)": pct(metrics["unranked_assignments"], len(assigned))})
    return Dashboard(metrics, students, sessions, pd.DataFrame(rank_rows))


def filter_students(frame, *, search="", classes=None, statuses=None, first=None, top_k=None, unranked=None):
    mask = frame.Student_ID.str.contains(search, case=False, regex=False)
    if classes is not None:
        mask &= frame.Class.isin(classes)
    if statuses is not None:
        mask &= frame.Status.isin(statuses)
    for key, value in [("Has_First_Choice", first), ("Has_Top_K_Choice", top_k), ("Has_Unranked_Allocation", unranked)]:
        if value is not None:
            mask &= frame[key] == value
    return frame[mask].copy()


def filter_sessions(frame, search=""):
    fields = ["Workshop_ID", "Workshop_Name", "Session_ID", "Day", "Time_Slot"]
    text = frame[fields].astype(str).agg(" | ".join, axis=1)
    return frame[text.str.contains(search, case=False, regex=False)].copy()


def student_preferences(data, student_id):
    student = data.students.set_index("Student_ID").loc[student_id]
    names = data.sessions.drop_duplicates("Workshop_ID").set_index("Workshop_ID").Workshop_Name.to_dict()
    return pd.DataFrame([{"Rank": int(col.split("_")[1]), "Workshop_ID": student[col],
                          "Workshop_Name": names.get(student[col], "")} for col in data.choice_columns])


def student_timetable(allocation, student_id):
    assigned = allocation.assignments[allocation.assignments.Student_ID == student_id].copy()
    if assigned.duplicated(["Day", "Time_Slot"]).any():
        raise ValueError("This result contains a timetable conflict and cannot be displayed as a valid timetable.")
    if assigned.empty:
        return pd.DataFrame(columns=["Time_Slot"])
    assigned["Session"] = assigned.Workshop_ID + " · " + assigned.Workshop_Name + " (" + assigned.Session_ID + ")"
    return assigned.pivot(index="Time_Slot", columns="Day", values="Session").fillna("").reset_index()


def session_roster(allocation, session_id, locks=()):
    frame = assignments_with_locks(allocation, locks)
    return frame[frame.Session_ID == session_id].sort_values(["Class", "Student_ID"]).reset_index(drop=True)


def comparison_table(data, records):
    rows = []
    for strategy, record in records.items():
        allocation = record.allocation
        m = build_dashboard(data, allocation, record.locks).metrics
        rows.append({"Strategy": strategy, "Fully allocated": m["fully_allocated"],
                     "Partially allocated": m["partially_allocated"], "Unallocated": m["unallocated"],
                     "First-choice students": m["first_choice_students"], f"Top-{m['top_k']} students": m["top_k_students"],
                     "First-choice assignments": m["first_choice_assignments"], "Unranked assignments": m["unranked_assignments"],
                     "Preference score": m["preference_score"], "Solver status": allocation.status,
                     "Optimality proven": allocation.optimal, "Elapsed seconds": allocation.elapsed_seconds})
    return pd.DataFrame(rows)
