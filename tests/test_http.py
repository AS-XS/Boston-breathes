from boston_breathes import http


def test_downloads_retry_dropped_connections_and_temporary_errors():
    adapter = http._session.get_adapter("https://example.org")
    retry = adapter.max_retries
    assert retry.total == 5
    assert retry.backoff_factor == 2
    assert {429, 503} <= set(retry.status_forcelist)
