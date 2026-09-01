"""대시보드가 구글드라이브와 muwon.db를 주고받는 로직(순수 함수 부분)만
따로 검증한다. Streamlit 위젯 렌더링 자체는 여기서 다루지 않는다. 그건
streamlit.testing.v1.AppTest로 수동 확인했다(전체 화면이 예외 없이 그려지는지)."""

from unittest.mock import patch

from muwon.dashboard import app


def test_drive_sync_not_configured_without_env_vars(monkeypatch):
    monkeypatch.delenv("GDRIVE_SA_KEY_JSON", raising=False)
    monkeypatch.delenv("GDRIVE_FOLDER_ID", raising=False)
    assert app._drive_sync_configured() is False


def test_drive_sync_configured_when_both_env_vars_present(monkeypatch):
    monkeypatch.setenv("GDRIVE_SA_KEY_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "FOLDER123")
    assert app._drive_sync_configured() is True


def test_local_db_path_extracts_sqlite_path():
    assert app._local_db_path() == "./muwon.db"


def test_sync_from_drive_noop_when_not_configured(monkeypatch):
    monkeypatch.delenv("GDRIVE_SA_KEY_JSON", raising=False)
    monkeypatch.delenv("GDRIVE_FOLDER_ID", raising=False)
    with patch("muwon.dashboard.app.gdrive_download") as mock_download:
        app.sync_db_from_drive()
    mock_download.assert_not_called()


def test_sync_from_drive_calls_download_when_configured(monkeypatch):
    monkeypatch.setenv("GDRIVE_SA_KEY_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "FOLDER123")
    with patch("muwon.dashboard.app.gdrive_download") as mock_download:
        app.sync_db_from_drive()
    mock_download.assert_called_once_with("FOLDER123", "muwon.db", "./muwon.db")


def test_sync_to_drive_skips_when_local_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("GDRIVE_SA_KEY_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "FOLDER123")
    monkeypatch.chdir(tmp_path)  # ./muwon.db가 존재하지 않는 디렉터리
    with patch("muwon.dashboard.app.gdrive_upload") as mock_upload:
        app.sync_db_to_drive()
    mock_upload.assert_not_called()


def test_sync_to_drive_uploads_when_configured_and_file_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("GDRIVE_SA_KEY_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "FOLDER123")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "muwon.db").write_bytes(b"fake")

    with patch("muwon.dashboard.app.gdrive_upload") as mock_upload:
        app.sync_db_to_drive()
    mock_upload.assert_called_once_with("FOLDER123", "muwon.db", "./muwon.db")
