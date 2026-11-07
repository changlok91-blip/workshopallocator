"""Deployment compatibility must never silently discard mandatory locks."""
import importlib
import pytest
import workshop_allocation
import workshop_allocation.engine as engine
import workshop_reporting.workspace as workspace
from test_allocation import workbook, sessions_for


def test_workspace_ignores_stale_package_reexport(monkeypatch):
    def legacy_allocator(validation, *, strategy=None, time_limit_seconds=30):
        raise AssertionError("Stale package alias must not be called")
    monkeypatch.setattr(workshop_allocation, "allocate", legacy_allocator)
    reloaded = importlib.reload(workspace)
    assert reloaded.allocate is engine.allocate
    v = workbook([("S1", ["A"])], sessions_for("A"), Workshops_Required_Per_Student=1)
    state = reloaded.AllocationWorkspace()
    state.set_input("synthetic fingerprint")
    record = state.run(v, "Balanced", 5)
    assert len(record.allocation.assignments) == 1


def test_stale_engine_gives_actionable_error_and_preserves_locks(monkeypatch):
    v = workbook([("S1", ["A"])], sessions_for("A"), Workshops_Required_Per_Student=1)
    state = workspace.AllocationWorkspace()
    state.set_input("synthetic fingerprint")
    state.locks = (("S1", "session-A"),)
    previous = state.run(v, "Balanced", 5)
    def legacy_allocator(validation, *, strategy=None, time_limit_seconds=30):
        raise AssertionError("Incompatible allocator must not be invoked")
    monkeypatch.setattr(workspace, "allocate", legacy_allocator)
    with pytest.raises(ValueError, match="Reboot app"):
        state.run(v, "Balanced", 5)
    assert state.locks == (("S1", "session-A"),)
    assert state.records["Balanced"] is previous
