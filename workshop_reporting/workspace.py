"""Session-local run records, atomic lock edits, and stale-result protection."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4
from workshop_input.models import STRATEGIES, ValidationResult
from workshop_allocation import allocate
from workshop_allocation.locks import validate_locks


def context_key(fingerprint, data, seconds, locks):
    rules = {key: value for key, value in data.rules.items() if key != "Allocation_Strategy"}
    content = {"fingerprint": fingerprint, "rules": rules, "seconds": float(seconds),
               "locks": sorted(set(tuple(pair) for pair in locks)),
               "students": data.students.to_json(orient="split"),
               "sessions": data.sessions.to_json(orient="split"), "choices": data.choice_columns}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RunRecord:
    allocation: object
    context: str
    fingerprint: str
    rules: dict
    locks: tuple
    seconds: float
    created_at: str = field(default_factory=now)
    run_id: str = field(default_factory=lambda: uuid4().hex)
    validation_warnings: tuple = ()


def ensure_current(record, fingerprint, data, seconds, locks):
    if record.fingerprint != fingerprint or record.context != context_key(fingerprint, data, seconds, locks):
        raise ValueError("These allocations are outdated for the current workbook, settings or locks. Rerun allocation before exporting.")


@dataclass
class AllocationWorkspace:
    fingerprint: str | None = None
    locks: tuple = ()
    records: dict = field(default_factory=dict)
    audit: list = field(default_factory=list)
    comparison_errors: dict = field(default_factory=dict)
    comparison_context: str | None = None

    def set_input(self, fingerprint):
        changed = self.fingerprint != fingerprint
        had_state = bool(self.records or self.locks)
        if changed:
            old_locks = self.locks
            self.fingerprint = fingerprint
            self.locks = ()
            self.records.clear()
            self.comparison_errors.clear()
            self.comparison_context = None
            if had_state:
                self.log("Input changed", "Previous results invalidated; locks cleared for the new input.", Cleared_Lock_Count=len(old_locks))
        return changed and had_state

    def log(self, action, detail, **extra):
        self.audit.append({"Time_UTC": now(), "Input_Fingerprint": self.fingerprint,
                           "Action": action, "Detail": detail, **extra})

    def current_records(self, validation, seconds):
        if not self.fingerprint or not validation or not validation.valid or validation.data is None:
            return {}
        key = context_key(self.fingerprint, validation.data, seconds, self.locks)
        return {strategy: record for strategy, record in self.records.items() if record.context == key}

    def _solve(self, validation, strategy, seconds, locks):
        if not self.fingerprint:
            raise ValueError("Upload and validate a workbook first.")
        allocation = allocate(validation, strategy=strategy, time_limit_seconds=seconds, locks=locks)
        return RunRecord(allocation, context_key(self.fingerprint, validation.data, seconds, locks),
                         self.fingerprint, {**validation.data.rules, "Allocation_Strategy": strategy},
                         tuple(locks), float(seconds), validation_warnings=tuple(
                             f"{issue.sheet}, row {issue.row}, {issue.column}: {issue.message}"
                             for issue in validation.issues if issue.severity == "warning"
                         ))

    def run(self, validation, strategy, seconds):
        record = self._solve(validation, strategy, seconds, self.locks)
        self.records[strategy] = record
        self.log("Allocation run", f"{strategy}: {record.allocation.status}", Run_ID=record.run_id)
        return record

    def compare(self, validation, seconds):
        # Every solve gets identical data, locks, seed, time budget and hard rules.
        self.comparison_errors = {}
        self.comparison_context = context_key(self.fingerprint, validation.data, seconds, self.locks)
        results = {}
        for strategy in STRATEGIES:
            try:
                results[strategy] = self.run(validation, strategy, seconds)
            except ValueError as exc:
                self.comparison_errors[strategy] = str(exc)
                # Never present an older run as this failed comparison attempt.
                self.records.pop(strategy, None)
        return results

    def apply_locks(self, validation, proposed, strategy, seconds, action):
        """Validate and solve before committing any lock or result changes."""
        previous = self.locks
        try:
            if not isinstance(validation, ValidationResult) or not validation.valid or validation.data is None:
                raise ValueError("Manual changes require a successful workbook validation.")
            checked = validate_locks(validation.data, proposed)
            record = self._solve(validation, strategy, seconds, checked)
        except ValueError as exc:
            self.log("Rejected manual change", str(exc), Requested_Action=action)
            raise
        self.locks = checked
        self.records[strategy] = record
        self.log(action, "Locks applied by the optimiser; no output rows were edited.",
                 Previous_Locks=previous, Locks=checked, Run_ID=record.run_id)
        return record
