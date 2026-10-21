import pytest

from boston_breathes import http


def test_downloads_retry_dropped_connections_and_temporary_errors():
    adapter = http._session.get_adapter("https://example.org")
    retry = adapter.max_retries
    assert retry.total == 5
    assert retry.backoff_factor == 2
    assert {429, 503} <= set(retry.status_forcelist)


def test_web_page_instead_of_a_zip_is_rejected():
    page = b"<!doctype html><title>Site Offline Due To Scheduled System Maintenance</title>"
    with pytest.raises(RuntimeError, match="not a zip file; the site may be down"):
        http.require_zip(page, "https://example.org/EF2024A.zip")
    assert http.require_zip(b"PK\x03\x04rest", "https://example.org/x.zip") == b"PK\x03\x04rest"
