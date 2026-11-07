from pathlib import Path
from streamlit.testing.v1 import AppTest


def test_app_starts_and_offers_downloads():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    assert not app.exception
    assert len(app.get("download_button")) == 2
    assert len(app.get("file_uploader")) == 1


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def uploaded(content):
    """Small stand-in for Streamlit's in-memory uploaded-file boundary."""
    from io import BytesIO
    result = BytesIO(content)
    result.size = len(content)
    return result


def allocation_button(app):
    return next(button for button in app.button if button.label == "Run Allocation")


def has_allocation_preview(app):
    return any(heading.value == "Student allocation preview" for heading in app.subheader)


def test_allocation_button_disabled_without_upload():
    app = AppTest.from_file(APP_PATH).run()
    assert not app.exception
    assert allocation_button(app).disabled
    assert not has_allocation_preview(app)


def test_invalid_upload_cannot_run_allocation():
    from unittest.mock import patch
    with patch("streamlit.file_uploader", return_value=uploaded(b"not excel")):
        app = AppTest.from_file(APP_PATH).run()
        assert not app.exception
        assert allocation_button(app).disabled
        assert any("validation errors" in error.value for error in app.error)
        assert not has_allocation_preview(app)


def test_valid_upload_runs_and_displays_allocation_metrics():
    from unittest.mock import patch
    from workshop_input.models import STRATEGIES
    from workshop_input.workbook import template_bytes
    with patch("streamlit.file_uploader", return_value=uploaded(template_bytes(example=True))):
        app = AppTest.from_file(APP_PATH).run()
        assert not app.exception
        assert not allocation_button(app).disabled
        assert tuple(app.selectbox[0].options) == STRATEGIES
        allocation_button(app).click().run()
        assert not app.exception
        assert has_allocation_preview(app)
        headings = [heading.value for heading in app.subheader]
        assert "Workshop allocation distribution" in headings
        assert "Preference choice distribution" in headings
        assert "3. Compare Strategies" in [tab.label for tab in app.tabs]
        assert "Each choice received" in [tab.label for tab in app.tabs]
        metrics = {metric.label: metric.value for metric in app.metric}
        assert metrics["Fully allocated"] == "2"
        assert metrics["Assigned places"] == "4"
        assert app.session_state["allocation_result"].optimal


def test_changing_strategy_clears_stale_allocation():
    from unittest.mock import patch
    from workshop_input.workbook import template_bytes
    with patch("streamlit.file_uploader", return_value=uploaded(template_bytes(example=True))):
        app = AppTest.from_file(APP_PATH).run()
        allocation_button(app).click().run()
        assert has_allocation_preview(app)
        app.selectbox[0].select("First-Choice Priority").run()
        assert not app.exception
        assert not has_allocation_preview(app)
        allocation_button(app).click().run()
        assert not app.exception
        assert app.session_state["allocation_result"].strategy == "First-Choice Priority"
        assert has_allocation_preview(app)


def test_changing_workbook_or_removing_upload_clears_stale_allocation():
    from unittest.mock import patch
    from workshop_input.workbook import template_bytes
    with patch("streamlit.file_uploader", return_value=uploaded(template_bytes(example=True))) as uploader:
        app = AppTest.from_file(APP_PATH).run()
        allocation_button(app).click().run()
        assert has_allocation_preview(app)
        uploader.return_value = uploaded(b"invalid replacement upload")
        app.run()
        assert not app.exception
        assert allocation_button(app).disabled
        assert not has_allocation_preview(app)
        uploader.return_value = None
        app.run()
        assert not app.exception
        assert allocation_button(app).disabled
        assert not has_allocation_preview(app)


def button_by_label(app, label):
    return next(button for button in app.button if button.label == label)


def test_comparison_selection_uses_cached_results_without_resolving():
    from unittest.mock import patch
    from workshop_input.workbook import template_bytes
    from workshop_allocation import allocate
    with patch("streamlit.file_uploader", return_value=uploaded(template_bytes(example=True))):
        with patch("workshop_reporting.workspace.allocate", wraps=allocate) as solver:
            app = AppTest.from_file(APP_PATH).run()
            button_by_label(app, "Compare all four strategies").click().run(timeout=20)
            assert not app.exception
            assert solver.call_count == 4
            selector = next(box for box in app.selectbox if box.label == "View compared strategy")
            selector.select("First-Choice Priority").run()
            button_by_label(app, "View selected strategy details").click().run()
            assert not app.exception
            assert app.session_state["allocation_result"].strategy == "First-Choice Priority"
            assert solver.call_count == 4
            next(field for field in app.text_input if field.label == "Search Student ID").set_value("S001").run()
            assert solver.call_count == 4
            assert any(button.label == "Download allocation workbook" for button in app.get("download_button"))


def test_changed_time_prevents_stale_export_and_requests_rerun():
    from unittest.mock import patch
    from workshop_input.workbook import template_bytes
    with patch("streamlit.file_uploader", return_value=uploaded(template_bytes(example=True))):
        app = AppTest.from_file(APP_PATH).run()
        allocation_button(app).click().run()
        assert any(button.label == "Download allocation workbook" for button in app.get("download_button"))
        app.number_input[0].set_value(31).run()
        assert not app.exception
        assert any("outdated" in warning.value for warning in app.warning)
        assert not any(button.label == "Download allocation workbook" for button in app.get("download_button"))


def test_manual_lock_reruns_and_retains_lock_in_active_result():
    from unittest.mock import patch
    from workshop_input.workbook import template_bytes
    with patch("streamlit.file_uploader", return_value=uploaded(template_bytes(example=True))):
        app = AppTest.from_file(APP_PATH).run()
        allocation_button(app).click().run()
        button_by_label(app, "Lock existing assignment and rerun").click().run(timeout=10)
        assert not app.exception
        state = app.session_state["workspace"]
        assert len(state.locks) == 1
        pair = state.locks[0]
        result = app.session_state["allocation_result"]
        assert pair in set(result.assignments[["Student_ID", "Session_ID"]].itertuples(index=False, name=None))
        button_by_label(app, "Unlock and rerun").click().run(timeout=10)
        assert not app.exception
        assert not app.session_state["workspace"].locks
