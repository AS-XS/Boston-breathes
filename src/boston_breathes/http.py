"""HTTP downloads with retries.

Public data servers occasionally drop a connection or return a temporary
error (429 or 5xx); every download retries up to five times with growing
waits (2, 4, 8, 16 seconds) before failing.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RETRY = Retry(
    total=5,
    backoff_factor=2,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET",),
    raise_on_status=False,
)

_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=RETRY))
_session.mount("http://", HTTPAdapter(max_retries=RETRY))


def get(url: str, **kwargs) -> requests.Response:
    """requests.get with retries on dropped connections and temporary errors."""
    return _session.get(url, **kwargs)
