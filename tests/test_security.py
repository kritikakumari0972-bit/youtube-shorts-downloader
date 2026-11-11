import pytest

from server.security import UnsafeURLError, safe_filename, validate_external_url


def test_rejects_non_http_schemes():
    with pytest.raises(UnsafeURLError):
        validate_external_url("file:///etc/passwd")


def test_rejects_localhost():
    with pytest.raises(UnsafeURLError):
        validate_external_url("http://127.0.0.1:8080/admin")


def test_rejects_private_ip():
    with pytest.raises(UnsafeURLError):
        validate_external_url("http://192.168.1.10/video.mp4")


def test_allows_configured_trusted_host():
    assert validate_external_url("http://cobalt:9000/file", {"cobalt"}) == "http://cobalt:9000/file"


def test_safe_filename_removes_path_chars():
    assert "/" not in safe_filename("../../my:video?.mp4")
