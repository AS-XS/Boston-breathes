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


def require(content: bytes, url: str, starts_with: bytes, kind: str) -> bytes:
    """Return `content` if it is the expected kind of file; otherwise raise.

    Some servers answer "200 OK" with a web page when they are down (NCES
    shows a maintenance page for every file, even ones that do not exist),
    so a successful response is checked before it is used or cached.
    """
    if not content.startswith(starts_with):
        raise RuntimeError(f"{url} returned {len(content):,} bytes that are not a {kind}; the site may be down")
    return content


def require_zip(content: bytes, url: str) -> bytes:
    return require(content, url, b"PK\x03\x04", "zip file")
