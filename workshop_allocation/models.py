"""Public results for the independently usable allocation engine."""
from dataclasses import dataclass, field
from typing import Any
import pandas as pd

ASSIGNMENT_COLUMNS = [
    "Student_ID", "Class", "Workshop_ID", "Workshop_Name", "Session_ID", "Day",
    "Time_Slot", "Preference_Rank", "Is_Unranked_Allocation",
]
STUDENT_COLUMNS = [
    "Student_ID", "Class", "Workshops_Required", "Workshops_Allocated",
    "Fully_Allocated", "Has_Top_K_Choice", "Has_First_Choice",
]


@dataclass(frozen=True)
class StageResult:
    name: str
    status: str
    value: int | None
    proven_optimal: bool


@dataclass
class AllocationResult:
    assignments: pd.DataFrame
    student_summary: pd.DataFrame
    summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    stages: list[StageResult] = field(default_factory=list)
    status: str = "UNKNOWN"
    optimal: bool = False
    elapsed_seconds: float = 0.0
    strategy: str = "Balanced"
