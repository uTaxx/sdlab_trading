"""구글 계정 없이도 gdrive_sync 로직(신규 파일이면 create, 있으면 update,
없으면 새 상태로 시작, 다운로드는 원자적 교체)이 맞는지 Drive API를
모킹해서 검증한다."""

from unittest.mock import MagicMock, patch

import pytest

from muwon.cloud import gdrive_sync


def make_fake_service(existing_file_id: str | None):
    service = MagicMock()
    service.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": existing_file_id, "name": "muwon.db"}] if existing_file_id else []
    }
    return service


@patch.dict("os.environ", {"GDRIVE_SA_KEY_JSON": '{"type": "service_account"}'})
@patch("muwon.cloud.gdrive_sync.service_account.Credentials.from_service_account_info")
@patch("muwon.cloud.gdrive_sync.build")
@patch("muwon.cloud.gdrive_sync.MediaFileUpload")
def test_upload_creates_new_file_when_absent(mock_media, mock_build, mock_creds, tmp_path):
    service = make_fake_service(existing_file_id=None)
    mock_build.return_value = service

    local_file = tmp_path / "muwon.db"
    local_file.write_bytes(b"fake-db-bytes")

    gdrive_sync.upload("FOLDER123", "muwon.db", str(local_file))

    service.files.return_value.create.assert_called_once()
    create_kwargs = service.files.return_value.create.call_args.kwargs
    assert create_kwargs["body"] == {"name": "muwon.db", "parents": ["FOLDER123"]}
    service.files.return_value.update.assert_not_called()


@patch.dict("os.environ", {"GDRIVE_SA_KEY_JSON": '{"type": "service_account"}'})
@patch("muwon.cloud.gdrive_sync.service_account.Credentials.from_service_account_info")
@patch("muwon.cloud.gdrive_sync.build")
@patch("muwon.cloud.gdrive_sync.MediaFileUpload")
def test_upload_updates_existing_file(mock_media, mock_build, mock_creds, tmp_path):
    service = make_fake_service(existing_file_id="EXISTING456")
    mock_build.return_value = service

    local_file = tmp_path / "muwon.db"
    local_file.write_bytes(b"fake-db-bytes")

    gdrive_sync.upload("FOLDER123", "muwon.db", str(local_file))

    service.files.return_value.update.assert_called_once()
    update_kwargs = service.files.return_value.update.call_args.kwargs
    assert update_kwargs["fileId"] == "EXISTING456"
    service.files.return_value.create.assert_not_called()


@patch.dict("os.environ", {"GDRIVE_SA_KEY_JSON": '{"type": "service_account"}'})
@patch("muwon.cloud.gdrive_sync.service_account.Credentials.from_service_account_info")
@patch("muwon.cloud.gdrive_sync.build")
def test_download_skips_when_file_missing(mock_build, mock_creds, tmp_path):
    service = make_fake_service(existing_file_id=None)
    mock_build.return_value = service

    out_path = tmp_path / "muwon.db"
    gdrive_sync.download("FOLDER123", "muwon.db", str(out_path))

    assert not out_path.exists()
    service.files.return_value.get_media.assert_not_called()


@patch.dict("os.environ", {"GDRIVE_SA_KEY_JSON": '{"type": "service_account"}'})
@patch("muwon.cloud.gdrive_sync.service_account.Credentials.from_service_account_info")
@patch("muwon.cloud.gdrive_sync.build")
@patch("muwon.cloud.gdrive_sync.MediaIoBaseDownload")
def test_download_writes_via_temp_file_then_atomic_replace(mock_downloader_cls, mock_build, mock_creds, tmp_path):
    """대시보드가 백그라운드에서 주기적으로 다시 내려받는 동안, 그 파일을
    동시에 읽는 쪽이 반쯤 쓰인 파일을 보지 않도록 임시 파일에 쓰고 나서
    교체하는지 확인한다."""
    service = make_fake_service(existing_file_id="EXISTING456")
    mock_build.return_value = service

    def fake_downloader(fileobj, request):
        fileobj.write(b"downloaded-db-bytes")
        instance = MagicMock()
        instance.next_chunk.return_value = (None, True)
        return instance

    mock_downloader_cls.side_effect = fake_downloader

    out_path = tmp_path / "muwon.db"
    gdrive_sync.download("FOLDER123", "muwon.db", str(out_path))

    assert out_path.exists()
    assert out_path.read_bytes() == b"downloaded-db-bytes"
    assert list(tmp_path.iterdir()) == [out_path]  # 교체 후 임시 파일이 남지 않아야 함


@patch.dict("os.environ", {"GDRIVE_SA_KEY_JSON": '{"type": "service_account"}'})
@patch("muwon.cloud.gdrive_sync.service_account.Credentials.from_service_account_info")
@patch("muwon.cloud.gdrive_sync.build")
@patch("muwon.cloud.gdrive_sync.MediaIoBaseDownload")
def test_download_uses_unique_temp_file_per_call(mock_downloader_cls, mock_build, mock_creds, tmp_path):
    """대시보드는 시작 시 1회 동기화와 30초 주기 동기화가 겹칠 수 있다.
    임시 파일 이름이 고정이면 먼저 끝난 쪽이 그 파일을 치워서 나중 쪽의
    os.replace가 FileNotFoundError로 죽는다(실제로 발생): 호출마다 임시
    파일 경로가 달라야 한다."""
    service = make_fake_service(existing_file_id="EXISTING456")
    mock_build.return_value = service

    used_temp_names = []

    def fake_downloader(fileobj, request):
        # 다운로드가 진행 중인 시점에 실제로 디렉터리에 존재하는 임시 파일 이름을
        # 본다 (fileobj.name은 os.fdopen으로 연 파일이라 경로가 아닌 fd 번호다).
        used_temp_names.extend(p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp"))
        fileobj.write(b"bytes")
        instance = MagicMock()
        instance.next_chunk.return_value = (None, True)
        return instance

    mock_downloader_cls.side_effect = fake_downloader

    out_path = tmp_path / "muwon.db"
    gdrive_sync.download("FOLDER123", "muwon.db", str(out_path))
    gdrive_sync.download("FOLDER123", "muwon.db", str(out_path))

    assert len(used_temp_names) == 2
    assert used_temp_names[0] != used_temp_names[1]
    assert list(tmp_path.iterdir()) == [out_path]


@patch.dict("os.environ", {"GDRIVE_SA_KEY_JSON": '{"type": "service_account"}'})
@patch("muwon.cloud.gdrive_sync.service_account.Credentials.from_service_account_info")
@patch("muwon.cloud.gdrive_sync.build")
@patch("muwon.cloud.gdrive_sync.MediaIoBaseDownload")
def test_download_failure_leaves_no_temp_file_and_keeps_existing_db(
    mock_downloader_cls, mock_build, mock_creds, tmp_path
):
    """다운로드가 중간에 끊겨도 임시 파일을 남기지 않고, 기존 muwon.db를
    망가뜨리지 않아야 한다(원자적 교체 전에 실패했으므로)."""
    service = make_fake_service(existing_file_id="EXISTING456")
    mock_build.return_value = service

    def failing_downloader(fileobj, request):
        instance = MagicMock()
        instance.next_chunk.side_effect = OSError("네트워크 끊김")
        return instance

    mock_downloader_cls.side_effect = failing_downloader

    out_path = tmp_path / "muwon.db"
    out_path.write_bytes(b"existing-db")

    with pytest.raises(OSError, match="네트워크 끊김"):
        gdrive_sync.download("FOLDER123", "muwon.db", str(out_path))

    assert out_path.read_bytes() == b"existing-db"  # 기존 파일 보존
    assert list(tmp_path.iterdir()) == [out_path]  # 임시 파일 잔재 없음


def test_missing_master_key_env_raises_system_exit():
    with patch.dict("os.environ", {}, clear=True):
        try:
            gdrive_sync._build_service()
            raise AssertionError("SystemExit이 발생해야 한다")
        except SystemExit as e:
            assert "GDRIVE_SA_KEY_JSON" in str(e)
