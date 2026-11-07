from pathlib import Path
from streamlit.testing.v1 import AppTest


def test_app_starts_and_offers_downloads():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    assert not app.exception
    assert len(app.get("download_button")) == 2
    assert len(app.get("file_uploader")) == 1
