"""
Header builder for realistic Instagram API requests.

Generates the headers that the real Instagram Android app sends but
instagrapi omits.  All values are derived from device settings, session
state, and captured traffic patterns.
"""

import json
import random
import time
import uuid
import hashlib
import hmac
import base64
from typing import Optional


class HeaderBuilder:
    """
    Builds per-request headers that match real Instagram traffic.

    Usage::

        builder = HeaderBuilder(
            user_id=14665267433,
            device_id="875187c3-8663-42aa-b8b0-df68384ca706",
            family_device_id="d6f9daa1-1631-4bad-9b6f-ee30bbc8c295",
            android_id="android-43ee6f3239ba62a8",
            mid="ajBSZAABAAGZcQj5IUkirxy3rac2",
        )

        headers = builder.build_for_endpoint(
            endpoint="media/3921072280461367322/comment/",
            nav_chain="MainFeedFragment:feed_timeline:1:cold_start:...",
        )
    """

    def __init__(
        self,
        user_id: Optional[int] = None,
        device_id: Optional[str] = None,
        family_device_id: Optional[str] = None,
        android_id: Optional[str] = None,
        mid: Optional[str] = None,
        session_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> None:
        self.user_id = user_id
        self.device_id = device_id or str(uuid.uuid4())
        self.family_device_id = family_device_id or str(uuid.uuid4())
        self.android_id = android_id or f"android-{uuid.uuid4().hex[:16]}"
        self.mid = mid or ""
        self.session_id = session_id or str(uuid.uuid4())
        self.username = username

        # Generate stable identifiers once
        self._appnetsession_nid = uuid.uuid4().hex
        self._appnetsession_sid = uuid.uuid4().hex
        self._conn_uuid_client = uuid.uuid4().hex
        self._session_private = base64.b64encode(uuid.uuid4().bytes[:16]).decode()
        self._fb_session_counter = random.randint(1, 5)

    def build_for_endpoint(
        self,
        endpoint: str,
        nav_chain: Optional[str] = None,
        client_endpoint: Optional[str] = None,
        friendly_name: Optional[str] = None,
    ) -> dict:
        """
        Build the full set of missing headers for a specific endpoint.

        Parameters
        ----------
        endpoint : str
            The API endpoint being called (e.g. "media/{id}/comment/").
        nav_chain : str, optional
            The x-ig-nav-chain value. Generated if not provided.
        client_endpoint : str, optional
            Override for x-ig-client-endpoint.
        friendly_name : str, optional
            Override for x-fb-friendly-name.

        Returns
        -------
        dict
            Header dict to merge into the request.
        """
        now = time.time()
        headers = {}

        # ── Core auth/session headers ──────────────────────────────────
        if self.user_id:
            headers["IG-INTENDED-USER-ID"] = str(self.user_id)
            headers["IG-U-DS-USER-ID"] = str(self.user_id)

        # ── Navigation / screen context ────────────────────────────────
        if nav_chain:
            headers["x-ig-nav-chain"] = nav_chain

        if client_endpoint:
            headers["x-ig-client-endpoint"] = client_endpoint
        else:
            headers["x-ig-client-endpoint"] = "UnknownFragment:unknown:0"

        if friendly_name:
            headers["x-fb-friendly-name"] = friendly_name

        # ── Bloks prism (UI rendering state) ───────────────────────────
        headers["x-bloks-prism-button-version"] = "INDIGO_PRIMARY_BORDERED_SECONDARY"
        headers["x-bloks-prism-colors-enabled"] = "true"
        headers["x-bloks-prism-extended-palette-gray"] = "false"
        headers["x-bloks-prism-extended-palette-indigo"] = "true"
        headers["x-bloks-prism-extended-palette-polish-enabled"] = "true"
        headers["x-bloks-prism-extended-palette-red"] = "true"
        headers["x-bloks-prism-extended-palette-rest-of-colors"] = "true"
        headers["x-bloks-prism-font-enabled"] = "true"
        headers["x-bloks-prism-indigo-link-version"] = "1"

        # ── Network / connection ───────────────────────────────────────
        headers["x-fb-network-properties"] = "Wifi;Validated;"
        headers["x-fb-connection-type"] = "WIFI"
        headers["x-ig-connection-type"] = "WIFI"
        headers["x-ig-is-foldable"] = "false"

        # ── Device languages ───────────────────────────────────────────
        headers["x-ig-device-languages"] = json.dumps(
            {"system_languages": "pt-BR", "keyboard_language": "pt-BR"},
            separators=(",", ":"),
        )

        # ── Request analytics / tracking ───────────────────────────────
        headers["x-fb-request-analytics-tags"] = json.dumps(
            {
                "network_tags": {
                    "product": "567067343352427",
                    "surface": "undefined",
                    "request_category": "api",
                    "purpose": "fetch",
                    "retry_attempt": "0",
                }
            },
            separators=(",", ":"),
        )

        # ── Server cluster ─────────────────────────────────────────────
        headers["x-fb-client-ip"] = "True"
        headers["x-fb-server-cluster"] = "True"

        # ── Session tracking ───────────────────────────────────────────
        headers["x-fb-appnetsession-nid"] = f"{self._appnetsession_nid},Wifi"
        headers["x-fb-appnetsession-sid"] = self._appnetsession_sid
        headers["x-fb-conn-uuid-client"] = self._conn_uuid_client
        headers["x-fb-session-id"] = (
            f"nid={self.mid or 'unknown'};nc={self._fb_session_counter};"
            f"fc=2;bc=1;"
        )
        headers["x-fb-session-private"] = self._session_private
        headers["x-fb-rmd"] = "state=URL_ELIGIBLE"

        # ── Pigeon session ─────────────────────────────────────────────
        headers["x-pigeon-rawclienttime"] = f"{now:.3f}"
        headers["x-pigeon-session-id"] = f"UFS-{uuid.uuid4()}-1"

        # ── Retry flag ─────────────────────────────────────────────────
        headers["x-tigon-is-retry"] = "False"

        # ── Device metadata (x-meta-usdid, x-meta-zca) ────────────────
        headers["x-meta-usdid"] = f"{uuid.uuid4()}.{int(now)}.MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAETuJGuNfvrYhX_taSeERc2umIb_Tpg_szJ5EiQXos-5NMd1u9-YvPNyedIntgKSytXM3Y0g9aJt0bEMcoAM4fAA.MEUCICZTG5O93VOFaeVX4eeK0Y0rBR_Zo0b0lbfXbDqgR3LQAiEA5VznG696vuXsDgB31ZxK1n3S5oMBf8CkFmesZoRhmsk"
        headers["x-meta-zca"] = self._build_meta_zca()

        return headers

    def build_graphql_headers(
        self,
        friendly_name: str,
        client_doc_id: str,
        bloks_version_id: str = "882d784bf1ed38e7879e6d2641257ec2fa0dcb50b92ed33c4b8c87a291e4d2f8",
        product_id: str = "567067343352427",
        nav_chain: Optional[str] = None,
        accept_encoding: str = "zstd",
    ) -> dict:
        """
        Build headers for a Bloks GraphQL request (graphql_www).

        Parameters
        ----------
        friendly_name : str
            The x-fb-friendly-name value (e.g. "IGBloksAppRootQuery-com.bloks.www.ig.about_this_account").
        client_doc_id : str
            The x-client-doc-id value.
        bloks_version_id : str
            The x-bloks-version-id value.
        product_id : str
            The x-ig-app-id (Bloks product ID, typically 567067343352427).
        nav_chain : str, optional
            Navigation chain string.
        accept_encoding : str
            Accept-Encoding value.

        Returns
        -------
        dict
            Headers for the GraphQL request.
        """
        now = time.time()
        headers = {
            "accept-language": "en-IN, en-US",
            "content-type": "application/x-www-form-urlencoded",
            "ig-intended-user-id": str(self.user_id) if self.user_id else "",
            "ig-u-ds-user-id": str(self.user_id) if self.user_id else "",
            "x-bloks-version-id": bloks_version_id,
            "x-client-doc-id": client_doc_id,
            "x-fb-client-ip": "True",
            "x-fb-friendly-name": friendly_name,
            "x-fb-request-analytics-tags": json.dumps(
                {"network_tags": {"product": product_id, "request_category": "graphql", "purpose": "fetch", "retry_attempt": "0"}},
                separators=(",", ":"),
            ),
            "x-fb-server-cluster": "True",
            "x-ig-android-id": self.android_id,
            "x-ig-app-id": product_id,
            "x-ig-app-locale": "pt_BR",
            "x-ig-capabilities": "3brTv10=",
            "x-ig-device-id": self.device_id,
            "x-ig-device-locale": "pt_BR",
            "x-ig-is-foldable": "false",
            "x-ig-mapped-locale": "pt_BR",
            "x-ig-timezone-offset": "-10800",
            "x-ig-validate-null-in-legacy-dict": "true",
            "x-mid": self.mid,
            "x-pigeon-rawclienttime": f"{now:.3f}",
            "x-pigeon-session-id": f"UFS-{uuid.uuid4()}-1",
            "x-root-field-name": "bloks_app",
            "x-tigon-is-retry": "False",
            "accept-encoding": accept_encoding,
            "x-fb-appnetsession-nid": f"{self._appnetsession_nid},Cell",
            "x-fb-appnetsession-sid": self._appnetsession_sid,
            "x-fb-conn-uuid-client": self._conn_uuid_client,
            "x-fb-http-engine": "Tigon/MNS/TCP",
            "x-fb-rmd": "state=URL_ELIGIBLE",
            "x-graphql-client-library": "pando",
            "x-graphql-request-purpose": "fetch",
            "priority": "u=1",
        }

        # Add the user agent if available
        if self.username:
            headers["user-agent"] = (
                f"Instagram 434.0.0.44.74 Android (33/13; 300dpi; 720x1600; "
                f"samsung; SM-E045F; m04; mt6765; pt_BR; 996255552)"
            )
        else:
            headers["user-agent"] = (
                "Instagram 434.0.0.44.74 Android (33/13; 300dpi; 720x1600; "
                "samsung; SM-E045F; m04; mt6765; pt_BR; 996255552)"
            )

        # Add ig-u-rur from session if available
        headers["x-meta-usdid"] = f"{uuid.uuid4()}.{int(now)}.MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEJRyhCnIBN1_CbNEmmUfCpTZURV89q-zq5Hq3qyBkvFq2Ly5nXHFnkeXDNJugbwuC41..."
        headers["x-meta-zca"] = self._build_meta_zca()

        if nav_chain:
            headers["x-ig-nav-chain"] = nav_chain

        return headers

    def build_auth_header(self, authorization_data: dict) -> str:
        """
        Build the Authorization Bearer token from session data.

        The real app sends: Bearer IGT:2:{base64_json}
        where the JSON contains ds_user_id and sessionid.

        instagrapi already handles this via self.authorization, so this
        is provided for completeness / override if needed.
        """
        if not authorization_data:
            return ""
        payload = json.dumps(authorization_data, separators=(",", ":"))
        b64 = base64.b64encode(payload.encode()).decode()
        return f"Bearer IGT:2:{b64}"

    def build_www_claim(self, secret_key: Optional[bytes] = None) -> str:
        """
        Build a plausible x-ig-www-claim HMAC value.

        The real app computes: hmac.{base64_hmac}

        Parameters
        ----------
        secret_key : bytes, optional
            HMAC secret. Falls back to device-based key if not provided.
        """
        if secret_key is None:
            # Use a deterministic key derived from device ID
            secret_key = hashlib.sha256(self.device_id.encode()).digest()

        message = f"{self.user_id}:{self.device_id}:{int(time.time())}".encode()
        sig = hmac.new(secret_key, message, hashlib.sha256).digest()
        return f"hmac.{base64.b64encode(sig).decode()}"

    def _build_meta_zca(self) -> str:
        """
        Build the x-meta-zca device attestation payload.

        This is a large JSON blob containing device capabilities,
        attestation data, and plugin status.  The real app generates
        this from native code; we construct a plausible replica.
        """
        now_ms = int(time.time() * 1000)

        zca = {
            "android": {
                "aka": {
                    "dataToSign": json.dumps(
                        {"time": str(now_ms), "hash": f"_i2q5Hvn9-gjOT-6sD0Hg8WmIguaISw6z8OGnuF0G8"},
                        separators=(",", ":"),
                    ),
                    "signedData": "MEUCIQDzOdlBBYajcxldY275OHEN0o4k5_5KnUCxwIkeCbGPQIgQdMDAmrHzlKyUI9xw31vn5UJK-DSbl7fLV8x6JdvyHk",
                    "keyHash": "b3d0ec4b305b2bed1206fc37449e8f1814b44d371eea24912767a5fec6499c0d",
                    "lastUploadedKeyTimeMs": now_ms,
                },
                "gpia": {
                    "token": (
                        "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9."
                        "eyJpc3MiOiJnb29nbGUiLCJleHAiOjE3ODE2NTE2ODksImp0aSI6ImV5SmhiR2NpT2lKQk1qVTJ"
                        "TMWNpTENKbGJtTWlPaUpCTWpVMlIwTk5JbjAubVBFZDBwNUpMb0MyRjBqWTBRd2RwbElLUmR0"
                        "Qm5lMkhwLVQ0clYteVpZalo1X2FHa2M2NmNnLlZMQzhUVlpYa3dVekxuWWUuTkFLMWlGN0FK"
                        "VnhuNzdQc3RPdFBVUzBSYjRiREFKTHhkRk9RaGl2Q29PTmgzTzgteVFKa19EYTJOTjNlUjYw"
                        "UGlHYjVxNUFSczJsZlNWLW1qUzkwMUhQblgtS2J5TDJQZC1hV3g1WE9ZWG9qUVcxTERUMGVh"
                        "VG1LZzdlUjJHdGtCT08yUUdINUF4UXhWSi1Id0xqSVhRZTB4VlBlX2p4U28xS2hTSmRfNWNW"
                        "M3lldFg5UUxxWVJILUlucDBjZ0tjVlNuckQ0bDlvcHJiMzhlcHZ1a21KY2kxOWVPNjAycTdN"
                        "ajdBMGFfZV9yU1lhRlBFNEZSN2NpRXp6YmhnREl3dldVYjNUSWVPVWhIRjNna0xIb0JoRG5N"
                        "N3dMblQzdE4zQ1Z4ZE91WGZCNDFMOGNzdEE3YTNEQVpGX1RqMWJvY2VaMnpFbk9hR0lRSzJE"
                        "NTdmU0RXY0ZybHdSMzlGOVd6V1h1QXYyaVBaNDRodnFvblppT0FkbGpJZmpkSmU4NTFTV2Jn"
                        "M3JxREhibE9UcGRXNmFqOE5sXzhRYU1MUnNEcVloS0M3cHJCek1YeTYzYjNIcEVDYWQzUG4z"
                        "alo2NXJWeFA2LXdZcV9qOG1LVG1ESWI5Q1BPTjhFeHlaR2dpZ2ZMNTN6WGRld2ZsZHpKdHVx"
                        "dHc3aE14dDRkanZSRk9PVzhYTmR2TzhXQUl1TjJXVE5QWVdoa21hdXFCbDZDOEwzYmR6OUVT"
                        "SUd0UGExajYxNzR3ZXJkLWJLYWhzNVR4QjZDVlk1QVVfbDJhOHhXRlBNd0ljNzlMcGVOQ0c0"
                        "bG9kSHJMYkpUWkh4N2pCRktRVFA0MGo4Zkh0ME4wMm8yMm5pZzZHblhkelRadWs4RUtjLWpn"
                        "OE44TmladjJ2dEhCdjJPZURNMlNJdHd5dHNBUm5sZ1Q2UEQ0WnBIcUwyaVk5bjdSY2psV05C"
                        "RldYMDJOUWIyaUxvRllaU1ctbzJPN0VUSVdLcjExbGFtRGNld1QzRENqcFRoMVpiUnFWQVZF"
                        "SU1mSFNEdmVxLXZUbzg4dXp6RTRmZC1JSHJGMmdyX0tvU182Mk5zNE05LTdxeUtibXAxbDBs"
                        "X0RqOS1hNzhySWM1MmxCUWRjXzU0MmRkekEwR3ZfbHdWUUtOLUVOZXpDcm5KN1BQRTVwT21C"
                        "ZFZmRThBdnFFNW9KRUpnUTc2V3AzN0hTQ2Y5RWZJQlFlMll4d25HMUhwakNYYmxNdy0tYlQx"
                        "V1lSMUlxc1NRei1keFhBQ2p2bkFVM1N6SGw2QTBoMDhiNkdkV0swajRLVXdMZFQyTUhQLVp0"
                        "dmEtNGU1aERqQ2gtTXEtTC1Bdnh4bE4yMnM3MnJheUpBa3VHZkhjMUJnMVBsazJtOE44U013"
                        "OXpoclc5NVdhZnhaVWltZy03ODZtREtvMkgtZDU2cjdUMkJIUFdSMFNld2V1d3IyOWV5WnRj"
                        "X1NtUjFVSk1pdS13LUx1d29VRjByY3k4bnVWVmlqbDkyNlFIcjBaWElsM0lVdkwtYVF4cmJL"
                        "eEswNk9iY1hBRXZnaWJqcDlxSW5MbjBSVUVrb1dKMUFubzhnZUZ5U1B5dnQ5Q1ZnVVJuWmJ0"
                        "ZVhKcEFFblB4YnNQSFIzdk8ta0hEVDkta01aVkhaOVN5TWFBTkVUN1RzSWV6d21lTjcyM2Rw"
                        "UXBzZUp4a3I0MjBMbU1NOWp6NkxvaVpodS1fWXAzZFppalRZR01Sb0tZQ2tocjN5Wm1QdzNW"
                        "QzAyWG5WWVBZSE1lYmNKblVZVm1NRXZPTXZzZjRiUndKbWNPa1RReXB1MG1adVZDTE1GVDNX"
                        "dGN3YzRqU01abnFobnYzaUlZbjR6ckxqbzBvRHp6U0M3ckw1NTVob0lnVlE5eEJpM05vejRa"
                        "Mk12OHRBMHhOb0dFMjkxRnVnOV9yMWJZVDEtR3d5SDkyZGF0Y3Q5WEpVbHNNeHM1aHRRMDZi"
                        "VkQxZDI4MVN1MEp5S1IxTzZfUU45cWRaaG9YU1Q4WEpIckhpcHljWllvdC11emhmd3ItTWM3"
                        "ZGpRU1RjS1JFdU9wbm9LcGZTYUpZYnJqbE94NDQ4b1FJT0FOQlhRbTNRY2h2Mk9SSXc2MTlh"
                        "eE1KemxkOFAyX1dNaTBpd3lqbnpOMFc5eW9fUWlxUlp6RkhrUXF0MzB6RU5rSFVYa2N6OWFu"
                        "SjMxUi1xRS1jeDBiOTNqc2R5alpjbFE4LXFBdy0xMmp5ZjY4UGU1OXhUSTFnRjh1YXZwRXk3"
                        "OF95Rjltc3BObUJocGl3emdFWnpaTlhfbHpNOGYwd2Z0VXFpaU9HRnJaVmZhRkVXMHBHR00y"
                        "VXUzN1R0bzd6REppd2VtRzJQUHFFdGRmLTNmcmNrbmkxcXRpYmJueVRYN1RJakhnNkdTcURu"
                        "dlI4VHljR3QzMGpodUw0MFNiNlkzXzRaeElGVkR0TVg3WW9reFV1dVY2M2pmNWhTY3pnTWVz"
                        "VGZIdG54NXo2Q2pxRHlmMGphN0RvQUZFYWlHLkRYdi0wWWVURXU4NHhXRHFJQkw3MmcifSwic"
                        "GF5bG9hZCI6eyJwbHVnaW5zIjp7ImJhdCI6eyJzdGEiOiJGdWxsIiwibHZsIjoxMDB9LCJzY3"
                        "QiOnt9LCJhZGIiOnsidXNiIjoxLCJhZGIiOjEsInVzYl9hZGIiOjF9fX19fQ",
                    ),
                    "tokenType": "EAR",
                },
            },
        }

        return json.dumps(zca, separators=(",", ":"))

    @staticmethod
    def get_accept_encoding() -> str:
        """Return the Accept-Encoding header the real app uses."""
        return "zstd"

    @staticmethod
    def get_content_type() -> str:
        """Return the Content-Type for POST requests."""
        return "application/x-www-form-urlencoded; charset=UTF-8"
