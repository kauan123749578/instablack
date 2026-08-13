"""
Bloks CAA login flow — latest Instagram authentication.

Implements Instagram's current Android login flow using the Bloks CAA
(Cross App Authentication) framework, which replaced the legacy
``accounts/login/`` endpoint.  Handles device initialization, OAuth
token exchange, password submission, TFA/OTP verification, and
post-login session establishment.

Usage::

    from phantom import EnhancedClient, login

    client = EnhancedClient()
    result = login(client, "username", "password")

    # With TFA code
    result = login(client, "username", "password", verification_code="123456")
"""

import json
import logging
import random
import string
import time
import uuid
from copy import deepcopy
from typing import Any, Dict, Optional
from uuid import uuid4

import instagrapi
from instagrapi.exceptions import (
    BadCredentials,
    BadPassword,
    ChallengeRequired,
    ClientError,
    ClientThrottledError,
    FeedbackRequired,
    PleaseWaitFewMinutes,
    ReloginAttemptExceeded,
    TwoFactorRequired,
    UnknownError,
)
from instagrapi.utils.serialization import dumps

logger = logging.getLogger("phantom.login")

# Bloks CAA login actions live on b.i.instagram.com
BLOKS_DOMAIN = "b.i.instagram.com"

SUPPORTED_CAPABILITIES = [
    {"name": "SUPPORTED_SDK_VERSIONS", "value": "105.0,104.0,103.0,102.0,101.0,100.0,99.0,98.0,97.0,96.0,95.0"},
    {"name": "FACE_TRACKER_VERSION", "value": "14"},
    {"name": "COMPRESSION", "value": "ETC2_COMPRESSION"},
    {"name": "android_os_build_fingerprint", "value": "samsung/m04/m04:13/TP1A.220624.014/E045FXXU7CXK4:user/release-keys"},
    {"name": "android_os_build_manufacturer", "value": "samsung"},
    {"name": "android_os_build_model", "value": "SM-E045F"},
]

TIMELINE_FEED_REASONS = (
    "cold_start_fetch",
    "warm_start_fetch",
    "pagination",
    "pull_to_refresh",
    "auto_refresh",
)
REELS_TRAY_REASONS = ("cold_start", "pull_to_refresh")


class LoginFlow:
    """
    Complete Instagram Bloks CAA login lifecycle.

    Wraps an :class:`EnhancedClient` and drives the login flow step by
    step, matching the real Instagram Android app's behaviour.

    Parameters
    ----------
    client : instagrapi.Client
        An (unauthenticated) client instance (EnhancedClient recommended).
    """

    def __init__(self, client: instagrapi.Client) -> None:
        self.client = client
        self.waterfall_id: str = ""
        self._aac_data: Dict[str, Any] = {}
        self._login_attempt_count: int = 0
        self._attest_nonce: str = ""

    # ── Public API ─────────────────────────────────────────────────────

    def login(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        verification_code: str = "",
        relogin: bool = False,
    ) -> bool:
        """
        Login using the Bloks CAA flow.

        Parameters
        ----------
        username : str, optional
            Instagram username.
        password : str, optional
            Instagram password.
        verification_code : str
            TFA / OTP verification code (if required).
        relogin : bool
            Force re-login even if session exists.

        Returns
        -------
        bool
            True on success.
        """
        if username and password:
            self.client.username = username
            self.client.password = password
        if self.client.username is None or self.client.password is None:
            raise BadCredentials("Both username and password must be provided.")

        if relogin:
            self._clear_session()
            if self.client.relogin_attempt > 1:
                raise ReloginAttemptExceeded()
            self.client.relogin_attempt += 1

        if self.client.user_id and not relogin:
            return True

        # Phase 1 — Pre-login device init
        try:
            self._pre_login_flow()
        except (PleaseWaitFewMinutes, ClientThrottledError):
            logger.warning("Ignore 429 during pre-login: continue")

        # Phase 2 — OAuth token fetch (submit username)
        self._fetch_oauth_token()

        # Phase 3 — Send login request (encrypted password)
        result = self._send_login()

        # Check for specific errors from the Bloks response
        last_json = deepcopy(self.client.last_json) if isinstance(self.client.last_json, dict) else {}
        self._raise_if_bloks_error(last_json)

        # Phase 4 — Handle TFA if needed (fluxo Bloks do instagrapi.mixins.auth)
        if self._needs_two_factor(result):
            code = (verification_code or "").strip()
            context = self._extract_context(result)
            if not code:
                raise TwoFactorRequired(
                    "Instagram returned a Bloks two-factor context; "
                    "provide verification_code for login",
                    response=getattr(result, "response", None),
                )
            if not context:
                raise TwoFactorRequired(
                    "Two-factor required but no verification context in Bloks response"
                )

            login_json = (
                deepcopy(self.client.last_json)
                if isinstance(self.client.last_json, dict)
                else {}
            )
            login_json["two_step_verification_context"] = context
            if isinstance(result, dict):
                nested_ctx = self.client.bloks_extract_two_step_verification_context(result)
                if nested_ctx:
                    context = nested_ctx
                    login_json["two_step_verification_context"] = nested_ctx

            # Mesma sequência de instagrapi._login_with_bloks_two_factor:
            # entrypoint → method_picker → select_method → (enter_*) → verify
            # + bloks_apply_login_response
            challenge = self.client._infer_bloks_two_factor_challenge(login_json, code)
            self.client.bloks_two_step_verification_entrypoint(context)
            self.client.bloks_two_step_verification_method_picker(context)
            self.client.bloks_two_step_verification_select_method(
                context, selected_method=challenge
            )
            if challenge == "backup_codes":
                self.client.bloks_two_step_verification_enter_backup_code(context)
                code = self.client._normalize_backup_code(code)
            elif challenge == "totp":
                # App abre a tela TOTP antes do verify; instagrapi expõe o método
                # mas o helper legado não chama — aqui chamamos.
                try:
                    self.client.bloks_two_step_verification_enter_totp_code(context)
                except Exception:
                    logger.debug("enter_totp_code skipped", exc_info=True)

            tfa_result = self.client.bloks_two_step_verification_verify_code(
                context,
                code,
                challenge=challenge,
            )
            last_after = (
                deepcopy(self.client.last_json)
                if isinstance(self.client.last_json, dict)
                else {}
            )
            self._raise_if_bloks_error(last_after)

            if self._apply_login(tfa_result) or self._apply_login(last_after):
                self._dual_tokens()
                self._post_login_flow()
                self.client.last_login = time.time()
                self.client.relogin_attempt = 0
                return True

            # Código já foi enviado: NÃO levantar TwoFactorRequired (UI em loop).
            raise UnknownError(
                "Instagram aceitou o pedido de 2FA Bloks, mas a resposta não "
                "trouxe o payload de login (authorization/sessionid). "
                "Troque a proxy ou tente de novo com um código fresco."
            )

        # Phase 5 — Apply login response (sem 2FA) and establish session
        applied = self._apply_login(result)
        if not applied:
            login_json = deepcopy(self.client.last_json) if isinstance(self.client.last_json, dict) else {}
            error_message = login_json.get("message", "")
            error_type = login_json.get("error_type", "")

            if error_message == "challenge_required":
                raise ChallengeRequired(
                    "Instagram requires a login challenge for this account. "
                    "Complete verification in the Instagram app and retry.",
                    response=getattr(result, "response", None),
                    **self._exception_context(login_json),
                )
            if "feedback_required" in error_message or error_type == "feedback_required":
                raise FeedbackRequired(
                    error_message or "Instagram requires feedback action before login",
                    response=getattr(result, "response", None),
                    **self._exception_context(login_json),
                )

            # 2FA às vezes só aparece no action string — não cair no erro genérico.
            code = (verification_code or "").strip()
            if not code and (
                self._needs_two_factor(result)
                or self._needs_two_factor(login_json)
                or "two_step" in str(result).lower()
                or "two_factor" in str(login_json).lower()
            ):
                raise TwoFactorRequired(
                    "Instagram returned a Bloks two-factor context; "
                    "provide verification_code for login",
                    response=getattr(result, "response", None),
                )

            if error_message:
                raise UnknownError(
                    f"Login failed. Instagram response: {error_message}",
                    response=getattr(result, "response", None),
                    **self._exception_context(login_json),
                )

            action_prefix = ""
            try:
                action_prefix = str(
                    (result or {}).get("layout", {}).get("bloks_payload", {}).get("action", "")
                )[:240]
            except Exception:
                pass
            logger.warning(
                "Bloks login sem auth payload (user=%s action_prefix=%r keys=%s)",
                getattr(self.client, "username", ""),
                action_prefix,
                list(login_json.keys())[:25] if isinstance(login_json, dict) else [],
            )
            raise UnknownError(
                "Bloks login response did not contain embedded auth payload. "
                "The account may require a different verification flow.",
                response=getattr(result, "response", None),
                **self._exception_context(login_json),
            )

        # Phase 6 — Post-login flow
        self._post_login_flow()

        self.client.last_login = time.time()
        self.client.relogin_attempt = 0
        return True

    # ── Phase 1: Pre-login ─────────────────────────────────────────────

    def _pre_login_flow(self) -> None:
        """Run pre-login device initialisation sequence."""
        self.waterfall_id = str(uuid4())
        self._aac_data = self._generate_aac()

        # 1a. Dual tokens — device token initialisation
        self._dual_tokens()

        # 1b. Android keystore attestation (best-effort)
        self._attestation()

        # 1c. Bloks CAA init — login context setup
        self._bloks_init()

    def _dual_tokens(self, login: bool = True) -> Dict:
        """Initialise device tokens via ``zr/dual_tokens/``."""
        data = {
            "device_id": self.client.android_device_id,
            "custom_device_id": self.client.uuid,
            "normal_token_hash": "",
            "fetch_reason": "token_expired",
        }
        return self.client.private_request("zr/dual_tokens/", data, login=login)

    def _attestation(self) -> None:
        """Create Android keystore attestation (best-effort)."""
        try:
            data = {
                "app_scoped_device_id": self.client.uuid,
                "key_hash": "",
                "device_id": self.client.android_device_id,
            }
            resp = self.client.private_request(
                "attestation/create_android_keystore/",
                data,
                login=True,
            )
            self._attest_nonce = resp.get("challenge_nonce", "")
        except ClientError as e:
            logger.debug("Attestation skipped (non-fatal): %s", e)
            self._attest_nonce = ""

    def _bloks_init(self) -> Dict:
        """Initialize Bloks CAA login context."""
        params = {
            "client_input_params": {
                "family_device_id": self.client.phone_id,
                "device_id": self.client.android_device_id,
                "offline_experiment_group": "caa_iteration_v3_perf_ig_4",
                "waterfall_id": self.waterfall_id,
                "qe_device_id": self.client.uuid,
                "show_internal_settings": False,
                "disable_auto_login": False,
                "disable_recursive_auto_login_interstitial": True,
                "use_auto_login_interstitial": True,
                "auto_login_interstitial_experiment_group_name": "",
                "is_from_logged_out": True,
                "is_from_logged_in_switcher": False,
                "logged_out_user": "",
                "last_auto_login_time": 0,
                "logout_source": "",
                "account_list": [],
                "blocked_uid": [],
                "sim_phone_numbers": [],
                "is_from_registration_reminder": False,
                "launched_url": "",
                "layered_homepage_experiment_group": "Deploy: Not in Experiment",
                "INTERNAL_INFRA_THEME": "THREE_C",
            },
            "server_params": {},
        }
        return self.client.bloks_async_action(
            "com.bloks.www.bloks.caa.login.process_client_data_and_redirect",
            params,
            domain=BLOKS_DOMAIN,
        )

    # ── Phase 2: OAuth token fetch ─────────────────────────────────────

    def _fetch_oauth_token(self) -> Dict:
        """Submit username via OAuth token fetch."""
        params = {
            "client_input_params": {
                "username_input": self.client.username,
                "aac": dumps(self._aac_data),
                "lois_settings": {"lois_token": ""},
                "cloud_trust_token": None,
                "zero_balance_state": "",
                "network_bssid": None,
            },
            "server_params": {
                "waterfall_id": self.waterfall_id,
                "device_id": self.client.android_device_id,
                "family_device_id": self.client.phone_id,
                "qe_device_id": self.client.uuid,
                "login_surface": "login_home",
                "login_entry_point": "logged_out",
                "is_from_logged_out": 0,
                "is_from_logged_in_switcher": 0,
                "is_platform_login": 0,
                "offline_experiment_group": "caa_iteration_v3_perf_ig_4",
                "layered_homepage_experiment_group": "Deploy: Not in Experiment",
                "access_flow_version": "pre_mt_behavior",
                "INTERNAL__latency_qpl_marker_id": 36707139,
                "INTERNAL__latency_qpl_instance_id": int(time.time() * 1000000),
            },
        }
        return self.client.bloks_async_action(
            "com.bloks.www.caa.login.oauth.token.fetch.async",
            params,
            domain=BLOKS_DOMAIN,
        )

    # ── Phase 3: Send login request ────────────────────────────────────

    def _build_attestation_header(self) -> str:
        """Build an x-ig-attest-params header value.

        The real Instagram app uses Android KeyStore attestation, which we
        cannot replicate in software. We build a minimal placeholder — the
        server may still accept the request without valid attestation.
        """
        import base64, hashlib, os
        nonce = self._attest_nonce or base64.b64encode(os.urandom(32)).decode()[:43]
        fake_signed = base64.b64encode(os.urandom(64)).decode()[:80]
        fake_key_hash = hashlib.sha256(os.urandom(32)).hexdigest()
        return dumps({
            "attestation": [{
                "version": 2,
                "type": "keystore",
                "errors": [0],
                "challenge_nonce": nonce,
                "signed_nonce": fake_signed,
                "key_hash": fake_key_hash,
            }]
        })

    def _send_login(self, try_num: int = 1) -> Dict:
        """Send encrypted password via Bloks CAA login request.

        Delega ao ``bloks_caa_login_send_request`` do instagrapi. A versão
        antiga inventava ``x-ig-attest-params`` (keystore fake) e o Instagram
        respondia **sem** auth payload → erro “did not contain embedded auth”.
        O helper oficial deixa attestation vazio de propósito.
        """
        self._login_attempt_count += 1
        return self.client.bloks_caa_login_send_request(
            self.client.password,
            username=self.client.username or "",
            login_attempt_count=self._login_attempt_count,
            try_num=try_num,
            waterfall_id=self.waterfall_id or "",
        )

    # ── Phase 4: Two-factor authentication ─────────────────────────────

    def _needs_two_factor(self, result: Dict) -> bool:
        """Check if the login result requires two-factor verification."""
        context = self.client.bloks_extract_two_step_verification_context(result)
        if context:
            return True
        last_json = deepcopy(self.client.last_json) if isinstance(self.client.last_json, dict) else {}
        if last_json.get("error") == "two_factor_required" or last_json.get("two_factor_info"):
            return True
        if "two_step_verification_context" in str(result):
            return True
        return False

    def _extract_context(self, result: Dict) -> str:
        """Extract two-step verification context from login result."""
        context = self.client.bloks_extract_two_step_verification_context(result)
        if context:
            return context
        last_json = deepcopy(self.client.last_json) if isinstance(self.client.last_json, dict) else {}
        return self.client._extract_two_step_verification_context(last_json)

    # ── Phase 5: Apply login response ──────────────────────────────────

    @staticmethod
    def _header_get(headers: Any, *names: str) -> Optional[str]:
        """Case-insensitive header lookup (curl_cffi / PhantomResponse)."""
        if not headers:
            return None
        try:
            items = dict(headers).items()
        except Exception:
            return None
        lower_map = {str(k).lower(): v for k, v in items}
        for name in names:
            value = lower_map.get(name.lower())
            if value:
                return value
        return None

    def _apply_login(self, result: Dict) -> bool:
        """Apply the Bloks login response to the client session.

        Mirrors instagrapi ``bloks_apply_login_response`` + header/cookie
        fallbacks (PhantomResponse headers are case-sensitive dicts).
        """
        candidates: list = []
        if isinstance(result, dict):
            candidates.append(result)
        last_json = getattr(self.client, "last_json", None)
        if isinstance(last_json, dict) and last_json not in candidates:
            candidates.append(last_json)

        for candidate in candidates:
            try:
                if self.client.bloks_apply_login_response(candidate):
                    self._dual_tokens()
                    return True
            except Exception:
                logger.debug("bloks_apply_login_response failed", exc_info=True)
            try:
                parsed = self.client.bloks_extract_login_response(candidate)
                if parsed and self.client.bloks_apply_login_response(parsed):
                    self._dual_tokens()
                    return True
            except Exception:
                logger.debug("bloks_extract_login_response failed", exc_info=True)

        last_resp = getattr(self.client, "last_response", None)
        headers = getattr(last_resp, "headers", None) if last_resp is not None else None
        ig_auth = self._header_get(headers, "ig-set-authorization", "IG-Set-Authorization")
        if ig_auth:
            self.client.authorization_data = self.client.parse_authorization(ig_auth)
            auth_header = (
                f"Bearer IGT:2:{ig_auth}" if ":" not in str(ig_auth) else ig_auth
            )
            self.client.private.headers["Authorization"] = auth_header
            self._dual_tokens()
            return True

        # Sessão já pode ter sido aplicada via Set-Cookie no transport.
        try:
            cookies = getattr(self.client, "cookie_dict", {}) or {}
            auth_data = getattr(self.client, "authorization_data", None) or {}
            if cookies.get("sessionid") or auth_data.get("sessionid") or auth_data.get(
                "ds_user_id"
            ):
                self._dual_tokens()
                return True
        except Exception:
            pass
        return False

    # ── Phase 6: Post-login flow ───────────────────────────────────────

    def _post_login_flow(self) -> None:
        """Emulate app behaviour after successful login."""
        checks = []
        try:
            self._get_account_family()
        except Exception as e:
            logger.debug("get_account_family skipped: %s", e)
        try:
            self._push_register()
        except Exception as e:
            logger.debug("push_register skipped: %s", e)
        try:
            self._write_supported_capabilities()
        except Exception as e:
            logger.debug("write_supported_capabilities skipped: %s", e)
        try:
            checks.append(self._get_reels_tray_feed("cold_start"))
        except Exception as e:
            logger.debug("reels_tray skipped: %s", e)
        try:
            checks.append(self._get_timeline_feed(["cold_start_fetch"]))
        except Exception as e:
            logger.debug("timeline skipped: %s", e)
        return all(checks)

    def _get_account_family(self) -> Dict:
        """Fetch account family info (multi-account support)."""
        return self.client.private_request("multiple_accounts/get_account_family/", {})

    def _push_register(self) -> Dict:
        """Register device for push notifications."""
        data = {
            "device_type": "android_push",
            "is_main_push_channel": True,
            "device_token": "",
            "users": str(self.client.user_id),
            "locale": self.client.locale,
            "family_device_id": self.client.phone_id,
            "udid": "",
            "_uuid": self.client.uuid,
        }
        return self.client.private_request("push/register/", data, with_signature=False)

    def _write_supported_capabilities(self) -> Dict:
        """Write supported device capabilities."""
        data = {
            "device_id": self.client.uuid,
            "supported_capabilities_new": json.dumps(SUPPORTED_CAPABILITIES),
            "_uuid": self.client.uuid,
        }
        return self.client.private_request("creatives/write_supported_capabilities/", data)

    def _get_reels_tray_feed(self, reason: str = "cold_start") -> Dict:
        """Fetch reels tray feed."""
        data = {
            "supported_capabilities_new": json.dumps(SUPPORTED_CAPABILITIES),
            "reason": reason,
            "timezone_offset": str(self.client.timezone_offset),
            "tray_session_id": self.client.tray_session_id,
            "request_id": self.client.request_id,
            "page_size": 50,
            "_uuid": self.client.uuid,
        }
        if reason == "cold_start":
            data["reel_tray_impressions"] = {}
        else:
            data["reel_tray_impressions"] = {str(self.client.user_id): str(time.time())}
        return self.client.private_request("feed/reels_tray/", data, with_signature=False)

    def _get_timeline_feed(self, reason: list = None) -> Dict:
        """Fetch main timeline feed."""
        reason = reason or ["cold_start_fetch"]
        request_time = str(int(time.time() * 1000))
        data = {
            "app_start_time": request_time,
            "has_camera_permission": "1",
            "feed_view_info": "[]",
            "client_recorded_request_time_ms": request_time,
            "client_seen_store_media_list": "",
            "client_view_state_media_list": "[]",
            "device_timezone_name": self.client.timezone_name,
            "feed_reshare_info": "",
            "phone_id": self.client.phone_id,
            "reason": reason[0],
            "battery_level": random.randint(50, 100),
            "timezone_offset": str(self.client.timezone_offset),
            "device_id": self.client.uuid,
            "include_attribution_ui_data": "true",
            "push_disabled": "true",
            "_uuid": self.client.uuid,
            "is_charging": random.randint(0, 1),
            "is_dark_mode": 1,
            "will_sound_on": random.randint(0, 1),
            "session_id": self.client.client_session_id,
            "bloks_versioning_id": self.client.bloks_versioning_id,
        }
        return self.client.private_request(
            "feed/timeline/",
            json.dumps(data),
            with_signature=False,
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _raise_if_bloks_error(self, last_json: Dict) -> None:
        """Raise appropriate exception if ``last_json`` contains a known error indicator.

        Bloks CAA login responses return HTTP 200 even on failure (bad
        password, challenge, etc.), with the error signalled inside the
        response body.  This method inspects ``last_json`` and surfaces
        the correct exception so callers don't get a misleading
        "no embedded auth payload" message.
        """
        message = (last_json.get("message") or "").strip()
        error_type = (last_json.get("error_type") or "").strip()

        if not message and not error_type:
            return

        if message == "challenge_required":
            raise ChallengeRequired(
                "Instagram requires a challenge for this login attempt",
                **deepcopy(last_json),
            )
        if error_type == "bad_password":
            msg = last_json.get("message", "").strip()
            if msg and not msg.endswith("."):
                msg = f"{msg}. "
            last_json["message"] = (
                f"{msg}If you are sure that the password is correct, "
                "then change your IP address, because it is added to "
                "the blacklist of the Instagram Server"
            )
            raise BadPassword(**deepcopy(last_json))
        if error_type == "feedback_required" or "feedback_required" in message:
            raise FeedbackRequired(
                message or "Feedback required",
                **deepcopy(last_json),
            )
        if message:
            raise UnknownError(
                f"Login failed: {message}",
                **deepcopy(last_json),
            )

    def _generate_aac(self) -> Dict[str, Any]:
        """Generate AAC data — empty aaccs like instagrapi (não inventar secret)."""
        return {
            "aac_init_timestamp": int(time.time()),
            "aaccs": "",
            "aacjid": str(uuid4()),
        }

    def _generate_aac_challenge_secret(self) -> str:
        """Deprecated helper — kept for callers; returns empty (sem fake)."""
        return ""

    def _clear_session(self) -> None:
        """Clear client session state for relogin."""
        if hasattr(self.client, "_clear_session_state"):
            self.client._clear_session_state(
                clear_authorization_data=True,
                clear_authorization_header=True,
                clear_private_cookies=True,
                clear_public_cookies=True,
            )

    @staticmethod
    def _exception_context(data: Dict) -> Dict:
        context = deepcopy(data)
        message = context.pop("message", None)
        if message is not None:
            context["instagram_message"] = message
        return context


# ── Module-level convenience ──────────────────────────────────────────


def login(
    client: instagrapi.Client,
    username: Optional[str] = None,
    password: Optional[str] = None,
    verification_code: str = "",
    relogin: bool = False,
) -> bool:
    """
    Login to Instagram using the latest Bloks CAA flow.

    This is a convenience wrapper around :class:`LoginFlow`.

    Parameters
    ----------
    client : EnhancedClient
        Phantom-enhanced instagrapi client.
    username : str, optional
        Instagram username.
    password : str, optional
        Instagram password.
    verification_code : str
        TFA / OTP code (if required).
    relogin : bool
        Force re-login.

    Returns
    -------
    bool
        True on success.
    """
    flow = LoginFlow(client)
    return flow.login(
        username=username,
        password=password,
        verification_code=verification_code,
        relogin=relogin,
    )
