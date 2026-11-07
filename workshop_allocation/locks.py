"""Validate mandatory student/session pairs against the same hard constraints."""
from collections import Counter, defaultdict
import pandas as pd
from workshop_input.models import ValidatedData


class LockConflict(ValueError):
    def __init__(self, conflicts):
        self.conflicts = list(conflicts)
        super().__init__("; ".join(self.conflicts))


def validate_locks(data: ValidatedData, locks=()):
    pairs = []
    errors = []
    try:
        for pair in locks:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not all(isinstance(v, str) for v in pair):
                raise ValueError
            pairs.append(tuple(pair))
    except (TypeError, ValueError):
        raise LockConflict(["Locks must be (Student_ID, Session_ID) text pairs."])
    pairs = tuple(sorted(set(pairs)))
    students = data.students.set_index("Student_ID")
    sessions = data.sessions.set_index("Session_ID")
    by_student = defaultdict(list)
    capacities = Counter()
    for sid, session_id in pairs:
        if sid not in students.index:
            errors.append(f"Unknown Student_ID {sid!r}.")
            continue
        if session_id not in sessions.index:
            errors.append(f"Unknown Session_ID {session_id!r}.")
            continue
        session = sessions.loc[session_id]
        choices = {students.loc[sid, c] for c in data.choice_columns if pd.notna(students.loc[sid, c])}
        if not data.rules["Allow_Unranked_Allocation"] and session.Workshop_ID not in choices:
            errors.append(f"Student {sid}: session {session_id} is outside submitted preferences and unranked allocation is prohibited.")
        by_student[sid].append(session)
        capacities[session_id] += 1
    for session_id, count in capacities.items():
        if count > sessions.loc[session_id, "Capacity"]:
            errors.append(f"Session {session_id}: {count} locked places exceed capacity {sessions.loc[session_id, 'Capacity']}.")
    for sid, selected in by_student.items():
        if len(selected) > data.rules["Workshops_Required_Per_Student"]:
            errors.append(f"Student {sid}: locked sessions exceed workshops required.")
        slots = defaultdict(list)
        workshops = defaultdict(list)
        for session in selected:
            slots[session.Day, session.Time_Slot].append(session.name)
            workshops[session.Workshop_ID].append(session.name)
        for slot, ids in slots.items():
            if len(ids) > 1:
                errors.append(f"Student {sid}: locked sessions {', '.join(ids)} clash at {slot[0]} / {slot[1]}.")
        if not data.rules["Allow_Repeat_Workshop"]:
            for wid, ids in workshops.items():
                if len(ids) > 1:
                    errors.append(f"Student {sid}: locked sessions {', '.join(ids)} repeat Workshop_ID {wid}.")
    if errors:
        raise LockConflict(errors)
    return pairs
