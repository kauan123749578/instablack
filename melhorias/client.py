"""
Enhanced instagrapi Client with realistic request headers.

This module wraps instagrapi.Client to inject the headers that the
real Instagram Android app sends, making API requests look like they
come from an actual device rather than a library.

Usage::

    from phantom import EnhancedClient

    client = EnhancedClient()
    client.load_settings("session.json")

    # Use exactly like instagrapi.Client
    media = client.media_info(3921072280461367322)

    # Or enable debug mode to see header differences
    client = EnhancedClient(debug=True)
"""

import json
import logging
import random
import time
import uuid
from typing import Optional

import instagrapi
from instagrapi.exceptions import ClientError, ClientJSONDecodeError

from .endpoints import get_endpoint_meta
from .headers import HeaderBuilder
from .login import LoginFlow
from .navigation import NavigationTracker
from .transport import PhantomSession, create_session, IMPERSONATE_BROWSER

logger = logging.getLogger("phantom")

# Headers that instagrapi adds but the real Instagram app does NOT send.
# We strip these to look more realistic.
INSTAGRAPI_EXPERIMENTAL_HEADERS = {
    "X-Zero-Balance",
    "X-Zero-Eh",
    "X-Zero-State",
    "Zero-HTTP-Network-Interface",
    "X-IG-App-Startup-Country",
    "X-IG-Bandwidth-Speed-KBPS",
    "X-IG-Bandwidth-TotalBytes-B",
    "X-IG-Bandwidth-TotalTime-MS",
    "X-Bloks-Is-Panorama-Enabled",
    "Connection",
    "Host",
}

# Headers we want to keep from instagrapi (don't override these)
PRESERVE_HEADERS = {
    "Authorization",
    "X-IG-App-ID",
    "X-IG-Capabilities",
    "X-IG-Device-ID",
    "X-IG-Family-Device-ID",
    "X-IG-Android-ID",
    "X-IG-Timezone-Offset",
    "X-Bloks-Version-Id",
    "X-IG-App-Locale",
    "X-IG-Device-Locale",
    "X-IG-Mapped-Locale",
    "X-IG-Device-locale",
    "User-Agent",
    "IG-U-DS-USER-ID",
    "IG-U-RUR",
    "IG-U-IG-DIRECT-REGION-HINT",
    "IG-U-SHBID",
    "IG-U-SHBTS",
    "X-MID",
}


class EnhancedClient(instagrapi.Client):
    """
    An instagrapi Client enhanced with realistic Instagram headers.

    This class inherits from instagrapi.Client and intercepts the request
    pipeline to add the headers the real Android app sends. All instagrapi
    methods work exactly as before.

    Parameters
    ----------
    debug : bool
        If True, log header differences for each request.
    auto_track_nav : bool
        If True, automatically update navigation chain on each request.
    """

    def __init__(
        self,
        debug: bool = False,
        auto_track_nav: bool = True,
        impersonate: str = IMPERSONATE_BROWSER,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._phantom_debug = debug
        self._auto_track_nav = auto_track_nav
        self._nav_tracker = NavigationTracker()
        self._header_builder: Optional[HeaderBuilder] = None
        self._request_count = 0

        # ── Replace HTTP transport ─────────────────────────────────────
        # Swap the private requests.Session with a PhantomSession
        # that uses curl_cffi for Chrome TLS fingerprint + HTTP/2.
        self._impersonate = impersonate
        phantom_session = create_session(impersonate=impersonate)

        # Preserve any proxy that was set during super().__init__
        if hasattr(self, "private") and self.private.proxies:
            phantom_session.proxies = dict(self.private.proxies)

        # Preserve TLS verify setting
        if hasattr(self, "private") and not self.private.verify:
            phantom_session.verify = False

        # Replace the session — instagrapi uses self.private for all API calls
        self.private = phantom_session

        # Set initial navigation
        self._nav_tracker.set_initial()

        logger.info(
            "PhantomEnhancedClient initialized (debug=%s, impersonate=%s)",
            debug,
            impersonate,
        )

    @property
    def nav_tracker(self) -> NavigationTracker:
        """Access the navigation tracker to manually control nav state."""
        return self._nav_tracker

    @property
    def phantom_headers_enabled(self) -> bool:
        """Check if phantom headers are active."""
        return self._header_builder is not None

    def _init_header_builder(self) -> None:
        """Initialize the HeaderBuilder with current session state."""
        self._header_builder = HeaderBuilder(
            user_id=self.user_id,
            device_id=self.uuid,
            family_device_id=self.phone_id,
            android_id=self.android_device_id,
            mid=self.mid,
            session_id=self.client_session_id,
            username=getattr(self, "username", None),
        )

    def _ensure_header_builder(self) -> HeaderBuilder:
        """Ensure the header builder is initialized."""
        if self._header_builder is None:
            self._init_header_builder()
        return self._header_builder

    # ── Override base_headers ──────────────────────────────────────────

    @property
    def base_headers(self) -> dict:
        """
        Override instagrapi's base_headers to:

        1. Remove experimental headers (X-Zero-*, X-IG-Bandwidth-*, etc.)
        2. Remove Accept-Encoding (handled by curl_cffi at transport level)
        3. Keep all other instagrapi headers intact
        """
        headers = super().base_headers

        # Remove experimental/unnecessary headers
        for key in INSTAGRAPI_EXPERIMENTAL_HEADERS:
            headers.pop(key, None)

        # Remove Accept-Encoding — curl_cffi + Chrome impersonation
        # already sends the correct "gzip, deflate, br, zstd" at transport level
        headers.pop("Accept-Encoding", None)

        return headers

    # ── Override private_request ───────────────────────────────────────

    def private_request(
        self,
        endpoint: str,
        data=None,
        params=None,
        login=False,
        with_signature=True,
        headers=None,
        extra_sig=None,
        domain=None,
    ):
        """
        Override private_request to inject phantom headers.

        This intercepts every authenticated API call and adds the
        missing headers before forwarding to instagrapi's implementation.
        """
        self._request_count += 1

        # Ensure header builder is ready
        builder = self._ensure_header_builder()

        # Look up endpoint metadata
        meta = get_endpoint_meta(endpoint)

        # Update navigation tracker
        if self._auto_track_nav:
            nav_section = meta.get("nav_section", "unknown")
            # Map common patterns to navigation triggers
            trigger = "button"
            if self._request_count == 1:
                trigger = "cold_start"
            elif "feed" in nav_section:
                trigger = "pull_to_refresh"
            elif "like" in nav_section or "save" in nav_section:
                trigger = "tap"

            self._nav_tracker.push(
                fragment=meta.get("client_endpoint", "UnknownFragment:unknown:0").split(":")[0],
                screen=nav_section,
                trigger=trigger,
            )

        # Build per-request phantom headers
        phantom_headers = builder.build_for_endpoint(
            endpoint=endpoint,
            nav_chain=self._nav_tracker.get_nav_chain(),
            client_endpoint=meta.get("client_endpoint"),
            friendly_name=meta.get("friendly_name"),
        )

        # Fix x-ig-www-claim: generate HMAC if instagrapi has default "0"
        if self.ig_www_claim and self.ig_www_claim != "0":
            phantom_headers["x-ig-www-claim"] = self.ig_www_claim
        elif self.user_id:
            # Generate a plausible HMAC claim
            phantom_headers["x-ig-www-claim"] = builder.build_www_claim()

        # Merge with caller-provided headers (caller takes priority)
        if headers:
            phantom_headers.update(headers)

        if self._phantom_debug:
            self._log_header_differences(phantom_headers)

        # Forward to instagrapi's private_request with HTML detection
        try:
            return super().private_request(
                endpoint=endpoint,
                data=data,
                params=params,
                login=login,
                with_signature=with_signature,
                headers=phantom_headers,
                extra_sig=extra_sig,
                domain=domain,
            )
        except ClientJSONDecodeError as e:
            response = getattr(e, "response", None)
            body = response.text.strip() if response is not None else ""
            if body.lower().startswith(("<html", "<!doctype")):
                snippet = body[:120].replace("\n", " ")
                raise ClientError(
                    f"Instagram returned an HTML page instead of JSON for "
                    f"'{endpoint}'. The request was intercepted by a login "
                    f"wall, challenge, or rate-limit page. Check your session "
                    f"validity and try again. Response preview: {snippet}",
                    response=response,
                ) from e
            raise

    # ── Debug / logging ────────────────────────────────────────────────

    def _log_header_differences(self, phantom_headers: dict) -> None:
        """Log what phantom headers are being added."""
        logger.debug(
            "[Phantom] Request #%d — adding %d headers",
            self._request_count,
            len(phantom_headers),
        )
        for key, value in phantom_headers.items():
            # Truncate long values for readability
            display = value[:80] + "..." if len(str(value)) > 80 else value
            logger.debug("  + %s: %s", key, display)

    # ── Session state persistence ──────────────────────────────────────

    def get_settings(self) -> dict:
        """
        Extend get_settings to include phantom state.
        """
        settings = super().get_settings()
        # Add phantom-specific state if needed in the future
        return settings

    def set_settings(self, settings: dict) -> None:
        """
        Extend set_settings to restore phantom state.
        """
        super().set_settings(settings)
        # Re-initialize header builder with restored session state
        self._header_builder = None  # Will be lazily re-initialized

    # ── Bloks GraphQL ──────────────────────────────────────────────────

    def _graphql_request(
        self,
        friendly_name: str,
        client_doc_id: str,
        variables: dict,
        bloks_version_id: str = "882d784bf1ed38e7879e6d2641257ec2fa0dcb50b92ed33c4b8c87a291e4d2f8",
        product_id: str = "567067343352427",
    ) -> dict:
        """
        Make a Bloks GraphQL request to the graphql_www endpoint.

        Parameters
        ----------
        friendly_name : str
            Facebook-friendly name for the query.
        client_doc_id : str
            Client doc ID for the Bloks app.
        variables : dict
            Variables to pass to the GraphQL query.
        bloks_version_id : str
            Bloks versioning ID.
        product_id : str
            The IG App ID for the Bloks product.

        Returns
        -------
        dict
            Parsed GraphQL response.
        """
        builder = self._ensure_header_builder()
        headers = builder.build_graphql_headers(
            friendly_name=friendly_name,
            client_doc_id=client_doc_id,
            bloks_version_id=bloks_version_id,
            product_id=product_id,
            nav_chain=self._nav_tracker.get_nav_chain(),
        )

        payload = {
            "method": "post",
            "format": "json",
            "server_timestamps": "true",
            "locale": "user",
            "purpose": "fetch",
            "fb_api_req_friendly_name": friendly_name,
            "client_doc_id": client_doc_id,
            "enable_canonical_naming": "true",
            "enable_canonical_variable_overrides": "true",
            "enable_canonical_naming_ambiguous_type_prefixing": "true",
            "variables": json.dumps(variables, separators=(",", ":")),
        }

        url = "https://i.instagram.com/graphql_www"
        response = self.private.get(url, params=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def about_this_account(self, target_user_id: str) -> dict:
        """
        Fetch 'About This Account' info for a target user via Bloks GraphQL.

        Returns country, date joined, profile info that Instagram shows
        on the "About This Account" screen.

        Parameters
        ----------
        target_user_id : str
            Instagram user PK of the target account.

        Returns
        -------
        dict
            Parsed 'About This Account' data with keys:
              - country (str or None)
              - country_visible (bool)
              - date_joined (str or None)
              - username (str or None)
              - profile_pic_url (str or None)
        """
        device_id = getattr(self, "uuid", str(uuid.uuid4()))
        variables = {
            "params": {
                "params": json.dumps(
                    {"referer_type": "ProfileMore", "target_user_id": str(target_user_id)},
                    separators=(",", ":"),
                ),
                "bloks_versioning_id": "882d784bf1ed38e7879e6d2641257ec2fa0dcb50b92ed33c4b8c87a291e4d2f8",
                "infra_params": {"device_id": device_id},
                "app_id": "com.bloks.www.ig.about_this_account",
            },
            "bk_context": {
                "is_flipper_enabled": False,
                "theme_params": [
                    {"value": ["three_neutral_gray"], "design_system_name": "XMDS"}
                ],
                "debug_tooling_metadata_token": None,
            },
        }

        result = self._graphql_request(
            friendly_name="IGBloksAppRootQuery-com.bloks.www.ig.about_this_account",
            client_doc_id="253360298312778871684788706414",
            variables=variables,
        )

        # When the API is restricted / blocked, the response may be empty or null.
        if not result:
            return None

        # Parse the Bloks bundle tree to extract country and date info
        data = result.get("data") or {}
        bloks_result = (data.get(
            "1$bloks_app(bk_context:$bk_context,params:$params)"
        ) or {}) if isinstance(data, dict) else {}
        screen_content = bloks_result.get("screen_content") or {}
        component = screen_content.get("component") or {}
        bundle = component.get("bundle") or {}
        bloks_bundle_str = bundle.get("bloks_bundle_tree", "{}")

        try:
            bloks_bundle = json.loads(bloks_bundle_str)
        except (json.JSONDecodeError, TypeError):
            bloks_bundle = {}

        # Extract payload data from the Bloks bundle
        payload = bloks_bundle.get("layout") or {}
        if isinstance(payload, dict):
            payload = payload.get("bloks_payload") or {}
        else:
            payload = {}
        data_list = payload.get("data", []) if isinstance(payload, dict) else []

        country = None
        country_visible = True
        date_joined = None
        username = None
        profile_pic_url = None

        for item in data_list:
            item_data = item.get("data", {})
            key = item_data.get("key", "")
            mode = item_data.get("mode", "")
            value = item_data.get("initial", "")

            if key == "IG_ABOUT_THIS_ACCOUNT:about_this_account_country":
                if mode == "p":
                    country = value or None
                else:
                    country_visible = False
            elif key == "IG_ABOUT_THIS_ACCOUNT:about_this_account_country_visibility":
                country_visible = value is not False

        return {
            "country": country,
            "country_visible": country_visible,
        }

    # ── Login ─────────────────────────────────────────────────────────

    def login(
        self,
        username=None,
        password=None,
        relogin=False,
        verification_code="",
    ):
        """
        Login using the latest Bloks CAA flow.

        Overrides instagrapi's legacy ``accounts/login/`` login with the
        current Bloks-based CAA login used by the Instagram Android app.
        Falls back to the parent implementation when the Bloks flow is
        not applicable.

        Parameters
        ----------
        username : str, optional
            Instagram username.
        password : str, optional
            Instagram password.
        relogin : bool
            Force re-login even if session exists.
        verification_code : str
            TFA / OTP verification code.

        Returns
        -------
        bool
            True on success.
        """
        flow = LoginFlow(self)
        return flow.login(
            username=username,
            password=password,
            verification_code=verification_code,
            relogin=relogin,
        )

    # ── Convenience methods ────────────────────────────────────────────

    def set_nav_context(
        self,
        fragment: str,
        screen: str,
        trigger: str = "cold_start",
    ) -> None:
        """
        Manually set the current navigation context.

        Useful when you know which screen the user is on.

        Parameters
        ----------
        fragment : str
            Android Fragment name (e.g. "MediaFragment").
        screen : str
            Screen identifier (e.g. "media_info").
        trigger : str
            Navigation trigger (cold_start, button, swipe, etc.)
        """
        self._nav_tracker.push(fragment, screen, trigger=trigger)

    def get_phantom_stats(self) -> dict:
        """
        Get statistics about phantom header usage.

        Returns
        -------
        dict
            Stats including request count, nav entries, etc.
        """
        return {
            "request_count": self._request_count,
            "nav_entries": self._nav_tracker.get_entry_count(),
            "last_screen": self._nav_tracker.get_last_screen(),
            "header_builder_initialized": self._header_builder is not None,
        }
