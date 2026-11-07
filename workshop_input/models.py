from dataclasses import dataclass, field
from typing import Any
import pandas as pd

STUDENTS = "Students & Preferences"
WORKSHOPS = "Workshops & Schedule"
RULES = "Rules"
DEFAULTS = {
    "Workshops_Required_Per_Student": 2,
    "Allow_Unranked_Allocation": False,
    "Allow_Repeat_Workshop": False,
    "Allocation_Strategy": "Balanced",
    "Fairness_Top_K": 3,
    "Lottery_Seed": 42,
}

@dataclass(frozen=True)
class Issue:
    severity: str
    sheet: str
    row: int | None
    column: str
    message: str

@dataclass
class ValidatedData:
    students: pd.DataFrame
    sessions: pd.DataFrame
    rules: dict[str, Any]
    choice_columns: list[str]

@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    data: ValidatedData | None = None

    @property
    def valid(self):
        return not any(i.severity == "error" for i in self.issues)

    def issue_frame(self):
        return pd.DataFrame([vars(i) for i in self.issues],
                            columns=["severity", "sheet", "row", "column", "message"])
