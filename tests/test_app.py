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
