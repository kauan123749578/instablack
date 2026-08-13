"""
HTTP transport layer with Android Chrome TLS impersonation and HTTP/2.

Replaces Python's `requests.Session` with `curl_cffi` to match
the real Instagram Android app's network fingerprint:

- TLS/JA3: Chrome 131 on Android (BoringSSL, same as OkHttp3)
- HTTP/2: Full multiplexing + HPACK header compression
- Accept-Encoding: zstd, br, gzip, deflate (matches real app)

Usage::

    from phantom.transport import create_session

    session = create_session()
    response = session.get("https://i.instagram.com/api/v1/feed/timeline/")
"""

import logging
import time
from typing import Any, Optional, Union
from urllib.parse import urlencode
from http.cookies import SimpleCookie

from curl_cffi.requests import Session as CurlSession
from requests.cookies import RequestsCookieJar

logger = logging.getLogger("phantom.transport")

# Chrome on Android impersonation profile for TLS fingerprinting.
# "chrome131_android" produces a JA3/JA4 that matches Chrome 131 on Android,
# which uses the same BoringSSL TLS library as Instagram's OkHttp3.
# This is the closest match to the real Instagram Android app's fingerprint.
IMPERSONATE_BROWSER = "chrome131_android"


class PhantomSession:
    """
    A drop-in replacement for requests.Session that uses curl_cffi
    with Chrome impersonation for TLS fingerprint spoofing.

    This wraps curl_cffi's Session to provide a requests-like API
    while maintaining the correct TLS/JA3 fingerprint and HTTP/2 support.
    """

    def __init__(self, impersonate: str = IMPERSONATE_BROWSER) -> None:
        self._session = CurlSession(impersonate=impersonate)
        self._impersonate = impersonate
        self._retry_strategy = None

        # requests-compatible attributes
        self.headers: dict[str, str] = {}
        self.cookies = RequestsCookieJar()
        self.proxies: dict = {}
        self.verify: bool = True
        self.timeout: Optional[int] = None

        logger.info(
            "PhantomSession created (impersonate=%s, http2=True)",
            impersonate,
        )

    def _request_with_retry(self, method: str, url: str, **kwargs) -> "PhantomResponse":
        """Make a request with optional retry logic.

        Retries on transient failures (timeout, connection error, 5xx)
        using the strategy configured via mount().
        """
        import random

        max_retries = 0
        backoff = 0
        status_forcelist = {502, 503, 504, 429}

        if self._retry_strategy:
            max_retries = getattr(self._retry_strategy, "total", 0)
            backoff = getattr(self._retry_strategy, "backoff_factor", 0)

        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                if method == "GET":
                    resp = self._session.get(url, **kwargs)
                else:
                    resp = self._session.post(url, **kwargs)
                # Retry on server errors
                if resp.status_code in status_forcelist and attempt < max_retries:
                    delay = backoff * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.debug("Retry %d after %d (%.1fs)", attempt + 1, resp.status_code, delay)
                    time.sleep(delay)
                    continue
                break
            except Exception as e:
                last_exc = e
                if attempt < max_retries:
                    delay = backoff * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.debug("Retry %d after error %s (%.1fs)", attempt + 1, e, delay)
                    time.sleep(delay)
                    continue
                raise

        # Sync cookies from curl_cffi response
        self._update_cookies(resp)
        return PhantomResponse(resp, method=method)

    def get(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        **kwargs,
    ) -> "PhantomResponse":
        """Send a GET request."""
        merged_headers = {**self.headers, **(headers or {})}
        merged_headers.pop("Content-Type", None)
        proxies = kwargs.pop("proxies", self.proxies)
        return self._request_with_retry(
            "GET",
            url,
            params=params,
            headers=merged_headers,
            proxies=proxies,
            verify=self.verify,
            timeout=kwargs.get("timeout", self.timeout),
            cookies=self.cookies.get_dict(),
            **{k: v for k, v in kwargs.items() if k not in ("timeout",)},
        )

    def post(
        self,
        url: str,
        data: Optional[Union[dict, str]] = None,
        headers: Optional[dict] = None,
        **kwargs,
    ) -> "PhantomResponse":
        """Send a POST request."""
        merged_headers = {**self.headers, **(headers or {})}
        proxies = kwargs.pop("proxies", self.proxies)
        return self._request_with_retry(
            "POST",
            url,
            data=data,
            headers=merged_headers,
            proxies=proxies,
            verify=self.verify,
            timeout=kwargs.get("timeout", self.timeout),
            cookies=self.cookies.get_dict(),
            **{k: v for k, v in kwargs.items() if k not in ("timeout",)},
        )

    def _update_cookies(self, resp) -> None:
        """Extract Set-Cookie headers from a curl_cffi response into our cookie jar."""
        # curl_cffi Headers has get_list() not getlist()
        values = []
        if hasattr(resp.headers, "get_list"):
            values = resp.headers.get_list("Set-Cookie")
        elif hasattr(resp.headers, "getlist"):
            values = resp.headers.getlist("Set-Cookie")
        elif isinstance(resp.headers, dict):
            raw = resp.headers.get("Set-Cookie") or resp.headers.get("set-cookie") or ""
            values = [raw] if raw else []
        for header_value in values:
            try:
                cookie = SimpleCookie(header_value)
                for name, morsel in cookie.items():
                    self.cookies.set(name, morsel.value)
            except Exception:
                pass

    def mount(self, prefix: str, adapter: Any) -> None:
        """Store retry strategy from instagrapi's HTTPAdapter configuration.

        instagrapi calls mount() during _configure_private_session_retry to
        attach an HTTPAdapter with Retry. We extract the retry strategy and
        use it to retry on transient failures.
        """
        if hasattr(adapter, "max_retries") and adapter.max_retries:
            self._retry_strategy = adapter.max_retries
            logger.info(
                "Mount retry on %s: total=%d, backoff=%.1f",
                prefix,
                getattr(adapter.max_retries, "total", 0),
                getattr(adapter.max_retries, "backoff_factor", 0),
            )

    def close(self) -> None:
        """Close the underlying session."""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _PhantomRequest:
    """Minimal request object for compatibility with instagrapi's response.request access."""

    def __init__(self, method: str, url: str) -> None:
        self.method = method
        self.url = url
        self.headers: dict = {}


class PhantomResponse:
    """
    A requests-compatible response wrapper around curl_cffi's response.
    """

    def __init__(self, curl_response, method: str = "GET") -> None:
        self._resp = curl_response
        self.request = _PhantomRequest(method, curl_response.url if hasattr(curl_response, 'url') else "")

    @property
    def status_code(self) -> int:
        return self._resp.status_code

    @property
    def text(self) -> str:
        return self._resp.text

    @property
    def content(self) -> bytes:
        return self._resp.content

    @property
    def url(self) -> str:
        return self._resp.url

    @property
    def headers(self) -> dict:
        return dict(self._resp.headers)

    @property
    def ok(self) -> bool:
        return self._resp.ok

    @property
    def http_version(self) -> str:
        """Return the HTTP version used (e.g. 'HTTP/2')."""
        return getattr(self._resp, "http_version", "HTTP/1.1")

    def json(self, **kwargs):
        return self._resp.json(**kwargs)

    def raise_for_status(self) -> None:
        """Raise an HTTPError for bad responses (4xx, 5xx)."""
        if self.status_code >= 400:
            from requests.exceptions import HTTPError
            raise HTTPError(
                f"{self.status_code} Server Error",
                response=self,
            )

    def __repr__(self) -> str:
        return f"<PhantomResponse [{self.status_code}]>"


def create_session(
    impersonate: str = IMPERSONATE_BROWSER,
    proxy: Optional[str] = None,
) -> PhantomSession:
    """
    Create a PhantomSession with Chrome impersonation and HTTP/2.

    Parameters
    ----------
    impersonate : str
        Browser to impersonate (default: chrome131).
    proxy : str, optional
        Proxy DSN (e.g. "http://user:pass@host:port").

    Returns
    -------
    PhantomSession
        Configured session ready for requests.
    """
    session = PhantomSession(impersonate=impersonate)
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def create_curl_session(
    impersonate: str = IMPERSONATE_BROWSER,
    proxy: Optional[str] = None,
) -> CurlSession:
    """
    Create a raw curl_cffi Session (for advanced use).

    Parameters
    ----------
    impersonate : str
        Browser to impersonate.
    proxy : str, optional
        Proxy DSN.

    Returns
    -------
    CurlSession
        Raw curl_cffi session.
    """
    session = CurlSession(impersonate=impersonate)
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session