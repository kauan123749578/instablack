#!/usr/bin/env python3
"""
Lab: criar conta Instagram via fluxo WEB CAA (GraphQL) — igual ao HTTP Toolkit.

Uso rápido:
  1. No HTTP Toolkit, copie o Request body (URL-Encoded) do POST de CADASTRO
     (response com caa_registration) para register_body.txt
  2. Copie o body do POST de CONFIRMAÇÃO (useCAAFBConfirmationFormSubmitMutation)
     para confirm_body.txt
  3. python scripts/web_signup_lab.py import register register_body.txt
     python scripts/web_signup_lab.py import confirm confirm_body.txt
  4. python scripts/web_signup_lab.py run --email seu@gmail.com

Nome feminino, senha 986004 e nascimento 05/07/2005 são automáticos.

Conta de TESTE only. doc_id expira — reimporte se der 400.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import sys
import time
import unicodedata
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

from curl_cffi import requests as cffi_requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phantom.navigation import NavigationTracker  # noqa: E402
CONFIG_PATH = ROOT / "scripts" / "web_signup_capture.json"
STATE_PATH = ROOT / "scripts" / "web_signup_state.json"
PENDING_DIR = ROOT / "scripts" / "web_signup_pending"
ACCOUNTS_DIR = ROOT / "scripts" / "web_signup_accounts"
OUT_COOKIES = ROOT / "scripts" / "web_signup_cookies.json"  # legado — ultima conta

SIGNUP_URL = "https://www.instagram.com/accounts/emailsignup/"
SIGNUP_EMAIL_CONFIRM_URL = "https://www.instagram.com/accounts/signup/emailConfirmation/"
SIGNUP_USERNAME_URL = "https://www.instagram.com/accounts/signup/username/"
GRAPHQL_URL = "https://www.instagram.com/api/graphql"
IG_WEB_ORIGIN = "https://www.instagram.com"
IG_APP_ID = "936619743392459"
WEB_ASBD_ID = "359341"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
SEC_CH_UA = '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'

# Campos conhecidos no JSON variables — substituídos no run
EMAIL_KEYS = {"email", "contactpoint", "contact_point", "contactPoint"}
PASSWORD_KEYS = {"password", "enc_password", "encrypted_password"}
USERNAME_KEYS = {"username", "user_name"}
NAME_KEYS = {"first_name", "firstname", "full_name", "name"}
CODE_KEYS = {"confirmation_code", "conf_code", "code", "verification_code"}
DAY_KEYS = {"birthday_day", "day", "birth_day"}
MONTH_KEYS = {"birthday_month", "month", "birth_month"}
YEAR_KEYS = {"birthday_year", "year", "birth_year"}
REG_INSTANCE_KEYS = {"reg_instance", "regInstance", "device_id", "deviceId"}
IG_REG_DATA_KEYS = {"ig_reg_data", "igRegData"}

DEFAULT_PASSWORD = "986004"
# 05/07/2005 (BR) = 5 de julho de 2005
DEFAULT_BIRTHDAY = (5, 7, 2005)

# Capturados do HTTP Toolkit (Aug 2026) — confirm doc_id pode precisar atualizar na tela
CAA_REGISTER_FRIENDLY = "useCAARegistrationFormSubmitMutation"
CAA_REGISTER_DOC_ID = "27029416779977343"
CAA_CONFIRM_FRIENDLY = "useCAAFBConfirmationFormSubmitMutation"
CAA_CONFIRM_DOC_ID = "24050931851170558"
CAA_FIELD_VALIDATION_FRIENDLY = "useCAARegistrationFieldValidationMutation"
CAA_FIELD_VALIDATION_ALT_NAMES = (
    "useCAARegistrationFieldValidationMutation",
    "CAARegistrationFieldValidationMutation",
    "useCAARegistrationFieldValidationSubmitMutation",
)
CAA_USERNAME_TYPEAHEAD_FRIENDLY = "useCAARegistrationUsernameTypeaheadQuery"
CAA_USERNAME_TYPEAHEAD_DOC_ID = "9643835809045186"

CAA_REG_FLOW_INFO = {"flow_name": "new_to_family_ig_default", "flow_type": "ntf"}
CAA_REG_OFFLINE_EXPERIMENT_GROUP = "caa_iteration_v3_perf_ig_4"
CAA_REG_LAYERED_HOMEPAGE_EXPERIMENT_GROUP = "Deploy: Not in Experiment"


def _browser_password(password: str) -> str:
    return f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{password}"


def _caa_reg_info_obj(
    fields: dict[str, Any],
    *,
    machine_id: str,
    verify_code: str | None = None,
    signup_code: str | None = None,
) -> dict[str, Any]:
    d, m, y = fields["birthday"]
    parts = (fields.get("name") or "User").split()
    first = parts[0]
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    reg: dict[str, Any] = {
        "first_name": first,
        "last_name": last,
        "full_name": fields["name"],
        "contactpoint": fields["email"],
        "contactpoint_type": "email",
        "confirmation_code": verify_code,
        "birthday_day": d,
        "birthday_month": m,
        "birthday_year": y,
        "encrypted_password": _browser_password(fields["password"]),
        "username": fields["username"],
        "username_prefill": fields["username"],
        "device_id": machine_id,
        "machine_id": machine_id,
        "registration_flow_id": str(uuid.uuid4()),
        "did_use_age": False,
        "is_caa_perf_enabled": True,
        "is_preform": True,
        "screen_visited": [],
        "caa_reg_flow_source": "registration_homepage",
    }
    if signup_code:
        reg["force_sign_up_code"] = signup_code
    return reg


def build_caa_register_variables(
    fields: dict[str, Any],
    ig_did: str,
    mid: str = "",
    *,
    verify_code: str | None = None,
    signup_code: str | None = None,
) -> dict:
    """Variables v1: reg_info/flow_info como JSON string (padrao CAA Meta)."""
    machine_id = mid or ig_did
    waterfall_id = str(uuid.uuid4())
    reg_info_str = json.dumps(
        _caa_reg_info_obj(
            fields,
            machine_id=machine_id,
            verify_code=verify_code,
            signup_code=signup_code,
        ),
        separators=(",", ":"),
    )
    flow_info_str = json.dumps(CAA_REG_FLOW_INFO, separators=(",", ":"))
    return {
        "input": {
            "client_mutation_id": str(uuid.uuid4()),
            "actor_id": "0",
            "machine_id": machine_id,
            "waterfall_id": waterfall_id,
            "reg_info": reg_info_str,
            "flow_info": flow_info_str,
        }
    }


def build_caa_register_variables_alt(
    fields: dict[str, Any],
    ig_did: str,
    mid: str = "",
    *,
    verify_code: str | None = None,
    signup_code: str | None = None,
) -> dict:
    """Variables v2: client_input_params + server_params."""
    machine_id = mid or ig_did
    waterfall_id = str(uuid.uuid4())
    reg_info_str = json.dumps(
        _caa_reg_info_obj(
            fields,
            machine_id=machine_id,
            verify_code=verify_code,
            signup_code=signup_code,
        ),
        separators=(",", ":"),
    )
    flow_info_str = json.dumps(CAA_REG_FLOW_INFO, separators=(",", ":"))
    d, m, y = fields["birthday"]
    parts = (fields.get("name") or "User").split()
    first = parts[0]
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    enc_pw = _browser_password(fields["password"])
    return {
        "input": {
            "client_mutation_id": str(uuid.uuid4()),
            "actor_id": "0",
            "client_input_params": {
                "contactpoint": fields["email"],
                "contactpoint_type": "email",
                "encrypted_password": enc_pw,
                "username": fields["username"],
                "first_name": first,
                "last_name": last,
                "full_name": fields["name"],
                "birthday_day": d,
                "birthday_month": m,
                "birthday_year": y,
                "username_prefill": fields["username"],
                "did_use_age": False,
            },
            "server_params": {
                "event_request_id": str(uuid.uuid4()),
                "is_from_logged_out": 1,
                "device_id": machine_id,
                "waterfall_id": waterfall_id,
                "flow_info": flow_info_str,
                "reg_info": reg_info_str,
                "layered_homepage_experiment_group": CAA_REG_LAYERED_HOMEPAGE_EXPERIMENT_GROUP,
                "offline_experiment_group": CAA_REG_OFFLINE_EXPERIMENT_GROUP,
                "current_step": 0,
            },
        }
    }


def build_caa_register_variables_web(
    fields: dict[str, Any],
    ig_did: str,
    mid: str = "",
    *,
    verify_code: str | None = None,
    signup_code: str | None = None,
) -> dict:
    """Variables web (JS CAARegistrationFormDesktopReducer.getMutationPayload)."""
    machine_id = mid or ig_did
    waterfall_id = str(uuid.uuid4())
    d, m, y = fields["birthday"]
    parts = (fields.get("name") or "User").split()
    first = parts[0]
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    enc_pw = _browser_password(fields["password"])
    reg_data: dict[str, Any] = {
        "birthday_day": str(d),
        "birthday_month": str(m),
        "birthday_year": str(y),
        "contactpoint": {"sensitive_string_value": fields["email"]},
        "contactpoint_type": "EMAIL",
        "custom_gender": "",
        "did_use_age": False,
        "firstname": {"sensitive_string_value": first},
        "fullname": {"sensitive_string_value": fields["name"]},
        "lastname": {"sensitive_string_value": last},
        "reg_passwd__": {"sensitive_string_value": enc_pw},
        "sex": "FEMALE",
        "use_custom_gender": False,
        "username": {"sensitive_string_value": fields["username"]},
    }
    if verify_code:
        reg_data["confirmation_code"] = {"sensitive_string_value": verify_code}
    return {
        "input": {
            "actor_id": "0",
            "client_mutation_id": str(uuid.uuid4()),
            "machine_id": machine_id,
            "waterfall_id": waterfall_id,
            "reg_data": reg_data,
            "sk_pipa_consent_given": None,
        }
    }


def build_caa_register_variables_web_minimal(
    fields: dict[str, Any],
    ig_did: str,
    mid: str = "",
    *,
    verify_code: str | None = None,
    signup_code: str | None = None,
) -> dict:
    """Register homepage — so email + dados basicos (sem confirmation_code)."""
    machine_id = mid or ig_did
    waterfall_id = str(uuid.uuid4())
    d, m, y = fields["birthday"]
    parts = (fields.get("name") or "User").split()
    first = parts[0]
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    enc_pw = _browser_password(fields["password"])
    reg_data: dict[str, Any] = {
        "birthday_day": d,
        "birthday_month": m,
        "birthday_year": y,
        "contactpoint": {"sensitive_string_value": fields["email"]},
        "contactpoint_type": "EMAIL",
        "custom_gender": "",
        "did_use_age": False,
        "firstname": {"sensitive_string_value": first},
        "fullname": {"sensitive_string_value": fields["name"]},
        "lastname": {"sensitive_string_value": last},
        "reg_passwd__": {"sensitive_string_value": enc_pw},
        "sex": "FEMALE",
        "use_custom_gender": False,
        "username": {"sensitive_string_value": fields["username"]},
    }
    return {
        "input": {
            "actor_id": "0",
            "client_mutation_id": str(uuid.uuid4()),
            "machine_id": machine_id,
            "waterfall_id": waterfall_id,
            "reg_data": reg_data,
            "sk_pipa_consent_given": None,
        }
    }


CAA_REGISTER_VAR_BUILDERS = (
    build_caa_register_variables_web,
    build_caa_register_variables_web_minimal,
    build_caa_register_variables,
    build_caa_register_variables_alt,
)


def _extract_doc_id_from_html(html: str, friendly_name: str) -> str | None:
    """Tenta achar doc_id no HTML/JS da pagina de signup."""
    if not html or not friendly_name:
        return None
    idx = html.find(friendly_name)
    if idx >= 0:
        chunk = html[idx : idx + 800]
        m = re.search(r'"id"\s*:\s*"(\d{10,})"', chunk)
        if m:
            return m.group(1)
        m = re.search(r"doc_id['\"]?\s*[:=]\s*['\"]?(\d{10,})", chunk)
        if m:
            return m.group(1)
    return None


FEMININE_FIRST_NAMES = (
    "Ana", "Maria", "Julia", "Beatriz", "Larissa", "Camila", "Fernanda",
    "Amanda", "Bruna", "Carolina", "Daniela", "Eduarda", "Gabriela",
    "Helena", "Isabela", "Júlia", "Letícia", "Mariana", "Natália", "Olivia",
    "Patricia", "Raquel", "Sabrina", "Tatiane", "Vanessa", "Yasmin",
    "Alice", "Bianca", "Clara", "Débora", "Elisa", "Flavia", "Giovanna",
    "Ingrid", "Joana", "Kelly", "Lorena", "Melissa", "Nicole", "Paula",
)


def build_caa_confirm_variables_browser(
    code: str,
    ig_reg_data: str,
    machine_id: str,
) -> dict:
    """Formato real do browser (DevTools Aug 2026)."""
    return {
        "input": {
            "actor_id": "0",
            "client_mutation_id": str(uuid.uuid4()),
            "conf_code": {"sensitive_string_value": code},
            "ig_reg_data": ig_reg_data,
            "machine_id": machine_id,
            "sk_pipa_consent_given": None,
            "youth_consent_decision_time": None,
        }
    }


def build_caa_confirm_variables(
    email: str, code: str, ig_did: str, *, ntf_context: str = ""
) -> dict:
    inp: dict[str, Any] = {
        "actor_id": "0",
        "client_mutation_id": str(uuid.uuid4()),
        "conf_code": code,
        "contactpoint": email,
        "reg_instance": ig_did,
        "device_id": ig_did,
    }
    if ntf_context:
        inp["ntf_context"] = ntf_context
    return {"input": inp}


def build_caa_confirm_variables_full(
    fields: dict[str, Any],
    ig_did: str,
    mid: str,
    code: str,
    *,
    signup_code: str | None = None,
    ntf_context: str = "",
) -> dict:
    """Variables com reg_info completo — igual browser ao confirmar codigo."""
    machine_id = mid or ig_did
    reg_info_str = json.dumps(
        _caa_reg_info_obj(
            fields,
            machine_id=machine_id,
            verify_code=code,
            signup_code=signup_code,
        ),
        separators=(",", ":"),
    )
    flow_info_str = json.dumps(CAA_REG_FLOW_INFO, separators=(",", ":"))
    inp: dict[str, Any] = {
        "client_mutation_id": str(uuid.uuid4()),
        "actor_id": "0",
        "machine_id": machine_id,
        "conf_code": code,
        "contactpoint": fields["email"],
        "reg_instance": ig_did,
        "device_id": ig_did,
        "reg_info": reg_info_str,
        "flow_info": flow_info_str,
    }
    if ntf_context:
        inp["ntf_context"] = ntf_context
    if signup_code:
        inp["force_sign_up_code"] = signup_code
    return {"input": inp}


CAA_CONFIRM_VAR_BUILDERS = (
    build_caa_confirm_variables_full,
    build_caa_confirm_variables,
)


def _extract_ig_reg_data(payload: Any) -> str:
    """Extrai ig_reg_data ou ntf_context (register devolve ntf_context no context)."""
    if isinstance(payload, dict):
        data = payload.get("data") if "data" in payload else payload
        if isinstance(data, dict):
            submit = data.get("caa_registration_homepage_submit")
            if isinstance(submit, dict):
                ctx = submit.get("context") or {}
                ntf = ctx.get("ntf_context") or ctx.get("ig_reg_data")
                if ntf:
                    return str(ntf)
        blob = json.dumps(payload, ensure_ascii=False)
    else:
        blob = str(payload or "")
    for key in ("ig_reg_data", "ntf_context"):
        m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', blob)
        if m:
            return m.group(1)
    return ""


def _register_submit_ok(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data") or payload
    if not isinstance(data, dict):
        return False
    submit = data.get("caa_registration_homepage_submit")
    if isinstance(submit, dict):
        status = str(submit.get("status") or "").upper()
        if status == "SUCCESS":
            return True
        if status == "HAS_VALIDATION_ERRORS":
            errs = (submit.get("errors") or {}).get("creation_errors") or []
            return not errs
    blob = json.dumps(data, ensure_ascii=False)
    return "caa_registration" in blob and "noncoercible" not in blob


def build_caa_field_validation_variables(
    fields: dict[str, Any],
    ig_did: str,
    mid: str,
    *,
    field: str,
    value: str,
) -> dict:
    """Variables v1: field + value + reg_info string (fluxo web CAA)."""
    machine_id = mid or ig_did
    reg_info = _caa_reg_info_obj(fields, machine_id=machine_id)
    if field == "CONTACTPOINT":
        reg_info["contactpoint"] = value
        reg_info["contactpoint_type"] = "email"
    elif field == "USERNAME":
        reg_info["username"] = value
        reg_info["username_prefill"] = value
    reg_info_str = json.dumps(reg_info, separators=(",", ":"))
    flow_info_str = json.dumps(CAA_REG_FLOW_INFO, separators=(",", ":"))
    return {
        "input": {
            "client_mutation_id": str(uuid.uuid4()),
            "actor_id": "0",
            "machine_id": machine_id,
            "reg_info": reg_info_str,
            "flow_info": flow_info_str,
            "field": field,
            "value": value,
        }
    }


def build_caa_field_validation_variables_alt(
    fields: dict[str, Any],
    ig_did: str,
    mid: str,
    *,
    field: str,
    value: str,
) -> dict:
    """Variables v2: field_name + field_value."""
    machine_id = mid or ig_did
    reg_info = _caa_reg_info_obj(fields, machine_id=machine_id)
    if field == "CONTACTPOINT":
        reg_info["contactpoint"] = value
        reg_info["contactpoint_type"] = "email"
    elif field == "USERNAME":
        reg_info["username"] = value
        reg_info["username_prefill"] = value
    reg_info_str = json.dumps(reg_info, separators=(",", ":"))
    flow_info_str = json.dumps(CAA_REG_FLOW_INFO, separators=(",", ":"))
    return {
        "input": {
            "client_mutation_id": str(uuid.uuid4()),
            "actor_id": "0",
            "machine_id": machine_id,
            "reg_info": reg_info_str,
            "flow_info": flow_info_str,
            "field_name": field,
            "field_value": value,
        }
    }


CAA_FIELD_VALIDATION_VAR_BUILDERS = (
    build_caa_field_validation_variables,
    build_caa_field_validation_variables_alt,
)


def build_caa_username_typeahead_variables(prefix: str = "") -> dict[str, Any]:
    """Igual browser: useCAARegistrationUsernameTypeaheadQuery."""
    return {"input": {"sensitive_string_value": prefix or ""}}


def typeahead_prefix_candidates(
    email: str,
    name: str,
    preferred: str = "",
) -> list[str]:
    """Prefixos 3–6 chars — typeahead IG falha com string vazia ou muito longa."""
    out: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        p = re.sub(r"[^a-z0-9._]", "", p.lower())[:30]
        if len(p) >= 3 and p not in seen:
            seen.add(p)
            out.append(p)

    preferred = preferred.lstrip("@").strip()
    if preferred:
        add(preferred)
        for n in (6, 5, 4, 3):
            if len(preferred) >= n:
                add(preferred[:n])

    name_seed = re.sub(
        r"[^a-z0-9._]",
        "",
        _strip_accents((name or "").split()[0]).lower(),
    )
    email_local = re.sub(r"[^a-z0-9._]", "", (email or "").split("@")[0].lower())

    for seed in (name_seed, email_local):
        if not seed:
            continue
        for n in (min(len(seed), 6), 5, 4, 3):
            if len(seed) >= n:
                add(seed[:n])

    return out


FEMININE_LAST_NAMES = (
    "Silva", "Santos", "Oliveira", "Souza", "Lima", "Costa", "Ferreira",
    "Rodrigues", "Almeida", "Nascimento", "Araujo", "Ribeiro", "Carvalho",
    "Gomes", "Martins", "Rocha", "Barbosa", "Pereira", "Castro", "Dias",
)


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def generate_feminine_name() -> str:
    """Gera nome completo feminino (username vem só do Instagram)."""
    first = random.choice(FEMININE_FIRST_NAMES)
    last = random.choice(FEMININE_LAST_NAMES)
    return f"{first} {last}"


def _response_is_username_taken(text: str) -> bool:
    low = text.lower()
    return any(
        m in low
        for m in (
            "username_is_taken",
            "username_invalid",
            "não está disponível",
            "nao esta disponivel",
            "not available",
        )
    )


class SignupHumanFlow:
    """Pausas e nav-chain simulando cliques/digitacao no cadastro web."""

    def __init__(self) -> None:
        self.nav = NavigationTracker()
        self.nav.set_initial(
            "PolarisCAAIGRegistrationHomepageRoute",
            "comet.igweb.PolarisCAAIGRegistrationHomepageRoute",
        )

    def pause(self, action: str, lo: float = 1.0, hi: float = 3.2) -> None:
        delay = random.uniform(lo, hi)
        print(f"  … {action} ({delay:.1f}s)")
        time.sleep(delay)

    def think_pause(self, chance: float = 0.2) -> None:
        if random.random() >= chance:
            return
        delay = random.uniform(1.8, 5.5)
        print(f"  … pausa ({delay:.1f}s)")
        time.sleep(delay)

    def type_pause(self, label: str, text: str) -> None:
        chars = max(1, len(text))
        delay = min(10.0, max(0.6, chars * random.uniform(0.07, 0.16)))
        print(f"  … digitando {label} ({delay:.1f}s)")
        time.sleep(delay)

    def wait_email_code_pause(self) -> None:
        delay = random.uniform(8.0, 22.0)
        print(f"  … abrindo email / lendo codigo ({delay:.1f}s)")
        time.sleep(delay)

    def code_entry_pause(self, n_digits: int = 6) -> None:
        delay = random.uniform(2.0, 5.0) + n_digits * random.uniform(0.2, 0.45)
        print(f"  … digitando codigo ({min(delay, 14.0):.1f}s)")
        time.sleep(min(delay, 14.0))

    def before_request_pause(self) -> None:
        time.sleep(random.uniform(0.35, 1.4))

    def goto_email_form(self) -> None:
        self.nav.push("PolarisEmailSignupForm", "emailsignup", trigger="button")

    def goto_email_confirm(self) -> None:
        self.nav.push(
            "PolarisEmailConfirmationRoute",
            "signup/emailConfirmation",
            trigger="button",
        )

    def goto_username(self) -> None:
        self.nav.push(
            "PolarisUsernameSignupRoute",
            "signup/username",
            trigger="button",
        )

    def nav_chain(self) -> str:
        return self.nav.get_nav_chain()


def resolve_signup_fields(
    *,
    email: str,
    name: str | None = None,
    username: str | None = None,
    password: str | None = None,
    birthday: tuple[int, int, int] | None = None,
) -> dict[str, Any]:
    return {
        "email": email.strip(),
        "name": (name or generate_feminine_name()).strip(),
        "username": username.lstrip("@").strip() if username else "",
        "password": password or DEFAULT_PASSWORD,
        "birthday": birthday or DEFAULT_BIRTHDAY,
    }


def jazoest(csrftoken: str) -> str:
    return "2" + str(sum(ord(c) for c in csrftoken))


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _pending_slug(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return slug or "pending"


def _pending_path(label: str) -> Path:
    return PENDING_DIR / f"{_pending_slug(label)}.json"


def _account_path(email: str) -> Path:
    return ACCOUNTS_DIR / f"{_pending_slug(email)}.json"


def save_account_record(record: dict, email: str) -> Path:
    path = _account_path(email)
    _save_json(path, record)
    return path


def _parse_urlencoded_body(raw: str) -> dict[str, str]:
    text = raw.strip()
    if not text:
        raise ValueError("Body vazio.")
    parsed = parse_qs(text, keep_blank_values=True)
    return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}


def _extract_body_from_curl(curl_text: str) -> str:
    """Extrai POST body de export cURL do HTTP Toolkit."""
    text = curl_text.strip()
    if not text:
        raise ValueError("cURL vazio.")
    # Junta linhas quebradas (Windows ^ ou bash \)
    text = re.sub(r"\^\s*\r?\n", "", text)
    text = re.sub(r"\\\s*\r?\n", "", text)
    for flag in ("--data-raw", "--data-binary", "--data"):
        # aspas simples ou duplas
        m = re.search(rf"{re.escape(flag)}\s+'([^']*)'", text, re.DOTALL)
        if m:
            return m.group(1)
        m = re.search(rf'{re.escape(flag)}\s+"([^"]*)"', text, re.DOTALL)
        if m:
            return m.group(1)
        m = re.search(rf"{re.escape(flag)}\s+(\S+)", text)
        if m:
            return m.group(1)
    raise ValueError(
        "Nao achei --data no cURL. No HTTP Toolkit: Export -> cURL -> copie tudo."
    )

def _extract_html_tokens(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    patterns = [
        (r'"LSD",\[\],\{"token":"([^"]+)"', "lsd"),
        (r'"lsd":"([^"]+)"', "lsd"),
        (r'"server_revision":(\d+)', "__rev"),
        (r'"__hsi":(\d+)', "__hsi"),
        (r'"hsi":(\d+)', "__hsi"),
        (r'"__spin_r":(\d+)', "__spin_r"),
        (r'"__spin_t":(\d+)', "__spin_t"),
        (r'"consistency":\{"rev":(\d+)', "__rev"),
    ]
    for pat, key in patterns:
        m = re.search(pat, html)
        if m and key not in out:
            out[key] = m.group(1)
    return out


def _deep_replace(obj: Any, replacers: list[tuple[set[str], Any]]) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            replaced = False
            for keys, val in replacers:
                if k in keys and val is not None:
                    if k == "conf_code" and isinstance(v, dict) and "sensitive_string_value" in v:
                        out[k] = {**v, "sensitive_string_value": str(val)}
                    else:
                        out[k] = val
                    replaced = True
                    break
            if not replaced:
                out[k] = _deep_replace(v, replacers)
        return out
    if isinstance(obj, list):
        return [_deep_replace(x, replacers) for x in obj]
    return obj


class WebSignupLab:
    def __init__(self, proxy: str | None = None) -> None:
        self.session = cffi_requests.Session(impersonate="chrome131")
        self._proxy = proxy
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
            shown = proxy.split("@")[-1] if "@" in proxy else proxy
            print(f"Proxy ativo: {shown}")
        self.state: dict[str, Any] = _load_json(STATE_PATH)
        self.config: dict[str, Any] = _load_json(CONFIG_PATH)
        self._last_signup_html = ""
        self._last_http_status = 0
        self._last_hard_block = False
        self._last_error_body = ""
        self._human = SignupHumanFlow()
        self.restore_session()

    def _prompt_verify_code(self, code: str | None) -> str:
        if code and str(code).strip():
            self._human.code_entry_pause(len(str(code).strip()))
            return str(code).strip()
        self._human.wait_email_code_pause()
        typed = input("Codigo do email (6 digitos): ").strip()
        self._human.code_entry_pause(len(typed) or 6)
        return typed

    def _build_account_record(
        self,
        *,
        email: str,
        fields: dict[str, Any],
        method: str,
    ) -> dict[str, Any]:
        d, m, y = fields["birthday"]
        return {
            "cookies": dict(self.session.cookies),
            "saved_at": int(time.time()),
            "email": email,
            "name": fields["name"],
            "username": fields["username"],
            "password": fields["password"],
            "birthday": f"{d:02d}/{m:02d}/{y}",
            "verify_code": self.state.get("verify_code"),
            "method": method,
            "proxy": self._proxy,
        }

    @staticmethod
    def list_accounts() -> None:
        if not ACCOUNTS_DIR.is_dir():
            print("Nenhuma conta criada ainda.")
            return
        files = sorted(ACCOUNTS_DIR.glob("*.json"))
        if not files:
            print("Nenhuma conta criada ainda.")
            return
        print(f"Contas criadas ({len(files)}):")
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                email = data.get("email", "?")
                user = data.get("username", "?")
                ts = data.get("saved_at", 0)
                print(f"  {path.name}: @{user}  {email}  saved_at={ts}")
            except Exception:
                print(f"  {path.name}: (erro ao ler)")

    @staticmethod
    def _response_is_hard_block(status: int, text: str) -> bool:
        if status == 429 and not text.strip():
            return False
        low = text.lower()
        return any(
            m in low
            for m in (
                '"spam":true',
                "feedback_required",
                "report_problem/scraping",
                "limitamos a frequência",
            )
        )

    @staticmethod
    def _response_is_needs_upgrade(text: str) -> bool:
        return "needs_upgrade" in text.lower()

    @staticmethod
    def _register_created(result: dict) -> bool:
        data = result.get("data") or {}
        blob = json.dumps(data, ensure_ascii=False)
        if "created_user_id" in blob or '"account_created":true' in blob:
            return True
        if "homepage_submit" in blob and "SUCCESS" in blob:
            return True
        return False

    def _signup_failed_summary(self, email: str, *, reason: str) -> int:
        """Mensagem clara + arquivo pending quando email/codigo ja validados."""
        print(f"\n=== cadastro NAO concluido ({reason}) ===")
        if self.state.get("signup_code") or self.state.get("ig_reg_data"):
            print("Progresso SALVO — email e codigo ja validados:")
            print(f"  email:       {email}")
            print(f"  nome:        {self.state.get('last_signup_name', '?')}")
            print(f"  username:    {self.state.get('last_signup_username', '?')}")
            print(f"  senha:       {DEFAULT_PASSWORD}")
            print(f"  verify_code: {self.state.get('verify_code', '?')}")
            print(f"  signup_code: {self.state.get('signup_code')}")
            self.archive_signup(email)
            print(
                "\n>>> Codigo expira rapido. Retome com a MESMA sessao:\n"
                f">>>   python scripts/web_signup_lab.py finish-signup --email {email}\n"
                ">>> Ou sessao nova se o codigo ja expirou:\n"
                ">>>   python scripts/web_signup_lab.py new-session\n"
                f">>>   python scripts/web_signup_lab.py run-simple --email {email}\n"
            )
        else:
            print("Sem signup_code/ig_reg_data — rode run-simple de novo.")
        return 1

    def _note_hard_block(self, text: str) -> None:
        self._last_hard_block = True
        print(
            "\n>>> Instagram bloqueou (spam / limite / automacao detectada).\n"
            ">>> PARE de rodar por algumas horas — repetir piora o bloqueio.\n"
            ">>> Progresso continua guardado em web_signup_pending/.\n"
        )
        if "scraping" in text.lower():
            print(
                ">>> Dica: sessao foi criada na sua rede e finish usou proxy — "
                "IP diferente + cookies antigos parece bot.\n"
                ">>> Quando liberar, tente finish SEM proxy (so sua rede).\n"
            )

    def restore_session(self) -> None:
        """Recupera cookies da sessao anterior (finish-signup)."""
        cookies = self.state.get("cookies") or {}
        for name, value in cookies.items():
            if value:
                self.session.cookies.set(name, str(value), domain=".instagram.com")

    def _browser_get_headers(self, referer: str | None = None) -> dict[str, str]:
        h: dict[str, str] = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "sec-ch-ua": SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
        if referer:
            h["Referer"] = referer
        return h

    def _visit_signup_page(
        self,
        url: str,
        *,
        referer: str | None = None,
        label: str = "pagina",
    ) -> None:
        """GET de pagina HTML — igual usuario navegando (atualiza lsd/csrftoken)."""
        self._human.pause(f"carregando {label}", 1.2, 3.0)
        r = self.session.get(
            url,
            headers=self._browser_get_headers(referer),
            timeout=45,
        )
        r.raise_for_status()
        if "emailsignup" in url:
            self._last_signup_html = r.text
        tokens = _extract_html_tokens(r.text)
        cookies = dict(self.session.cookies)
        if tokens.get("lsd"):
            self.state["lsd"] = tokens["lsd"]
            tok = self.state.setdefault("tokens", {})
            tok.update(tokens)
        if cookies.get("csrftoken"):
            self.state["csrftoken"] = cookies["csrftoken"]
        self.state["cookies"] = cookies
        self.save_state()

    def simulate_signup_journey(self, *, after_code: bool = False) -> None:
        """Simula cliques entre telas antes dos POSTs (nav-chain + GETs)."""
        self._human = SignupHumanFlow()
        self._visit_signup_page(SIGNUP_URL, label="cadastro")
        self._human.goto_email_form()
        self._human.pause("na tela de email", 1.5, 3.5)
        if not after_code:
            return
        self._human.goto_email_confirm()
        self._visit_signup_page(
            SIGNUP_EMAIL_CONFIRM_URL,
            referer=SIGNUP_URL,
            label="confirmacao do email",
        )
        self._human.pause("codigo OK — indo para username", 2.0, 4.0)
        self._human.goto_username()
        self._visit_signup_page(
            SIGNUP_USERNAME_URL,
            referer=SIGNUP_EMAIL_CONFIRM_URL,
            label="escolha de username",
        )
        self._human.pause("revisando perfil antes de cadastrar", 2.5, 5.5)

    def bootstrap_resume(self) -> dict[str, str]:
        """Retoma cadastro: mantem ig_did/mid/datr, so refresca lsd/csrftoken."""
        preserved_cookies = dict(self.state.get("cookies") or {})
        preserved = {
            k: self.state[k]
            for k in (
                "signup_code",
                "verify_code",
                "last_signup_email",
                "last_signup_name",
                "last_signup_username",
                "ntf_context",
                "ig_reg_data",
                "ig_did",
                "mid",
            )
            if self.state.get(k)
        }
        self.restore_session()
        for key in ("ig_did", "mid", "datr"):
            val = preserved_cookies.get(key) or preserved.get(key)
            if val:
                self.session.cookies.set(str(key), str(val), domain=".instagram.com")
        print(f"GET {SIGNUP_URL} (retomando mesma sessao/device)")
        self.simulate_signup_journey(after_code=True)
        cookies = dict(self.session.cookies)
        for key in ("ig_did", "mid", "datr"):
            if preserved_cookies.get(key):
                cookies[key] = preserved_cookies[key]
        tokens = self.state.get("tokens") or _extract_html_tokens(
            self._last_signup_html or ""
        )
        csrftoken = cookies.get("csrftoken") or self.state.get("csrftoken", "")
        if not csrftoken:
            raise RuntimeError("Sem csrftoken ao retomar sessao.")
        self.state.update(
            {
                "cookies": cookies,
                "tokens": tokens,
                "ig_did": cookies.get("ig_did") or preserved.get("ig_did", ""),
                "mid": cookies.get("mid") or preserved.get("mid", ""),
                "csrftoken": csrftoken,
                "lsd": self.state.get("lsd") or tokens.get("lsd", ""),
                "bootstrapped_at": int(time.time()),
            }
        )
        self.state.update(preserved)
        self.save_state()
        print("OK sessao retomada:")
        print(f"  ig_did={self.state.get('ig_did')}")
        print(f"  mid={self.state.get('mid')}")
        print(f"  lsd={str(self.state.get('lsd', ''))[:20]}...")
        return cookies

    def ensure_caa_defaults(self, html: str = "") -> None:
        """Config minima CAA — sem copiar body do HTTP Toolkit."""
        html = html or self._last_signup_html
        reg_doc = (
            _extract_doc_id_from_html(html, CAA_REGISTER_FRIENDLY) or CAA_REGISTER_DOC_ID
        )
        conf_doc = (
            self.config.get("confirm", {}).get("doc_id")
            or _extract_doc_id_from_html(html, CAA_CONFIRM_FRIENDLY)
            or CAA_CONFIRM_DOC_ID
        )
        self.config.setdefault("register", {})
        self.config["register"].update(
            {
                "friendly_name": CAA_REGISTER_FRIENDLY,
                "doc_id": str(reg_doc),
                "variables": {"input": {"actor_id": "0"}},
                "form": {
                    "__comet_req": "7",
                    "fb_api_caller_class": "RelayModern",
                    "__crn": "comet.igweb.PolarisCAAIGRegistrationHomepageRoute",
                },
            }
        )
        if conf_doc:
            self.config.setdefault("confirm", {})
            self.config["confirm"].update(
                {
                    "friendly_name": CAA_CONFIRM_FRIENDLY,
                    "doc_id": str(conf_doc),
                    "variables": {
                        "input": {
                            "actor_id": "0",
                            "conf_code": {"sensitive_string_value": ""},
                            "ig_reg_data": "",
                            "machine_id": "",
                            "sk_pipa_consent_given": None,
                            "youth_consent_decision_time": None,
                        }
                    },
                    "form": {
                        "__comet_req": "7",
                        "fb_api_caller_class": "RelayModern",
                        "__crn": "comet.igweb.PolarisCAAIGRegistrationHomepageRoute",
                    },
                }
            )
        fv_doc = (
            (self.config.get("field_validation") or {}).get("doc_id")
            or self._field_validation_doc_id(html)
        )
        if fv_doc:
            self.config.setdefault("field_validation", {})
            self.config["field_validation"].update(
                {
                    "friendly_name": CAA_FIELD_VALIDATION_FRIENDLY,
                    "doc_id": str(fv_doc),
                    "variables": {"input": {"actor_id": "0"}},
                    "form": {
                        "__comet_req": "7",
                        "fb_api_caller_class": "RelayModern",
                        "__crn": "comet.igweb.PolarisCAAIGRegistrationHomepageRoute",
                    },
                }
            )
        ta_doc = (
            (self.config.get("username_typeahead") or {}).get("doc_id")
            or _extract_doc_id_from_html(html, CAA_USERNAME_TYPEAHEAD_FRIENDLY)
            or CAA_USERNAME_TYPEAHEAD_DOC_ID
        )
        self.config.setdefault("username_typeahead", {})
        self.config["username_typeahead"].update(
            {
                "friendly_name": CAA_USERNAME_TYPEAHEAD_FRIENDLY,
                "doc_id": str(ta_doc),
                "variables": build_caa_username_typeahead_variables(""),
                "form": {
                    "__comet_req": "7",
                    "fb_api_caller_class": "RelayModern",
                    "__crn": "comet.igweb.PolarisCAAIGRegistrationHomepageRoute",
                },
            }
        )
        self.save_config()

    @staticmethod
    def _field_validation_doc_id(html: str) -> str | None:
        for fname in CAA_FIELD_VALIDATION_ALT_NAMES:
            doc = _extract_doc_id_from_html(html, fname)
            if doc:
                return doc
        return None

    @staticmethod
    def _extract_username_suggestions_from_graphql(payload: dict | None) -> list[str]:
        if not isinstance(payload, dict):
            return []
        inner = payload.get("data")
        if not isinstance(inner, dict):
            inner = payload
        fv = inner.get("xfb_caa_registration_field_validation")
        if not isinstance(fv, dict):
            return []
        suggestions = fv.get("username_suggestions") or []
        return [str(u) for u in suggestions if u]

    @staticmethod
    def _extract_typeahead_suggestions(payload: dict | None) -> list[str]:
        if not isinstance(payload, dict):
            return []
        inner = payload.get("data")
        if not isinstance(inner, dict):
            inner = payload
        node = inner.get("xfb_caa_registration_homepage_fetch_username_typeahead")
        if not isinstance(node, dict):
            return []
        suggestions = node.get("username_suggestions") or []
        return [str(u) for u in suggestions if u]

    def _print_graphql_result(self, label: str, result: dict) -> None:
        status = result.get("status")
        data = result.get("data") or {}
        blob = json.dumps(data, ensure_ascii=False)
        print(f"{label}: HTTP {status}")
        if "errors" in data and isinstance(data["errors"], list):
            print("  errors:", json.dumps(data["errors"], ensure_ascii=False)[:600])
        elif isinstance(data.get("data"), dict):
            inner = data["data"]
            submit = inner.get("caa_registration_homepage_submit")
            if isinstance(submit, dict):
                st = submit.get("status")
                print(f"  homepage_submit status={st}")
                if st == "HAS_VALIDATION_ERRORS":
                    ce = (submit.get("errors") or {}).get("creation_errors") or []
                    if ce:
                        print("  validation:", json.dumps(ce, ensure_ascii=False)[:400])
            elif "caa_registration" in json.dumps(inner, ensure_ascii=False):
                print("  OK cadastro — confira email")
        elif "confirmation_submit" in blob or "created_user_id" in blob:
            print("  OK confirmacao")
        elif data.get("raw"):
            print("  resposta:", str(data.get("raw"))[:600])
        else:
            print("  resposta:", blob[:600])

    def save_state(self) -> None:
        _save_json(STATE_PATH, self.state)

    def save_config(self) -> None:
        _save_json(CONFIG_PATH, self.config)

    def archive_signup(self, label: str | None = None) -> Path:
        """Guarda sessao atual (signup_code, cookies) para retomar depois."""
        label = label or self.state.get("last_signup_email") or "pending"
        path = _pending_path(label)
        payload = {
            "archived_at": int(time.time()),
            "label": label,
            "state": self.state,
        }
        _save_json(path, payload)
        print(f"Cadastro guardado: {path}")
        if self.state.get("signup_code"):
            print(f"  email:    {self.state.get('last_signup_email', '?')}")
            print(f"  username: {self.state.get('last_signup_username', '?')}")
            print(f"  signup_code: {self.state.get('signup_code')}")
            print(
                f"\nRetomar depois:\n"
                f"  python scripts/web_signup_lab.py restore-signup {_pending_slug(label)}\n"
                f"  python scripts/web_signup_lab.py finish-signup --email "
                f"{self.state.get('last_signup_email', 'EMAIL')}"
            )
        return path

    def restore_signup(self, label: str) -> None:
        path = _pending_path(label)
        if not path.is_file():
            matches = sorted(PENDING_DIR.glob("*.json")) if PENDING_DIR.is_dir() else []
            hint = ", ".join(p.stem for p in matches[:10]) or "(nenhum)"
            raise FileNotFoundError(f"Nao achei {path}. Guardados: {hint}")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.state = data.get("state") if isinstance(data.get("state"), dict) else data
        self.save_state()
        self.restore_session()
        print(f"Restaurado de {path}")
        print(f"  email: {self.state.get('last_signup_email', '?')}")

    def list_pending(self) -> None:
        if not PENDING_DIR.is_dir():
            print("Nenhum cadastro guardado.")
            return
        files = sorted(PENDING_DIR.glob("*.json"))
        if not files:
            print("Nenhum cadastro guardado.")
            return
        print("Cadastros guardados:")
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                st = data.get("state") or data
                email = st.get("last_signup_email", "?")
                code = st.get("signup_code", "—")
                user = st.get("last_signup_username", "—")
                print(f"  {path.stem}: {email} @{user} signup_code={code}")

            except Exception:
                print(f"  {path.stem}: (erro ao ler)")

    def new_session(self) -> None:
        """Limpa sessao local — use antes de testar outro email/proxy."""
        self.state = {}
        self.session.cookies.clear()
        if STATE_PATH.is_file():
            STATE_PATH.unlink()
        print("Sessao limpa. Proximo run-simple comeca do zero.")

    def bootstrap(self, *, fresh: bool = True) -> dict[str, str]:
        preserved = {
            k: self.state[k]
            for k in (
                "signup_code",
                "verify_code",
                "last_signup_email",
                "last_signup_name",
                "last_signup_username",
                "ntf_context",
                "ig_reg_data",
            )
            if self.state.get(k)
        }
        print(f"GET {SIGNUP_URL}")
        self._human = SignupHumanFlow()
        self._human.pause("abrindo pagina de cadastro", 1.5, 3.5)
        r = self.session.get(
            SIGNUP_URL,
            headers=self._browser_get_headers(),
            timeout=45,
        )
        r.raise_for_status()
        self._last_signup_html = r.text
        cookies = dict(self.session.cookies)
        tokens = _extract_html_tokens(r.text)
        csrftoken = cookies.get("csrftoken", "")
        if not csrftoken:
            raise RuntimeError("Sem csrftoken — proxy bloqueado ou página diferente.")
        if not tokens.get("lsd"):
            raise RuntimeError(
                "Não achei LSD na página. Abra /accounts/emailsignup/ no browser "
                "e reimporte os bodies (doc_id pode ter expirado)."
            )
        self.state = {
            "cookies": cookies,
            "tokens": tokens,
            "ig_did": cookies.get("ig_did", ""),
            "mid": cookies.get("mid", ""),
            "csrftoken": csrftoken,
            "lsd": tokens["lsd"],
            "bootstrapped_at": int(time.time()),
        }
        self.state.update(preserved)
        self.save_state()
        print("OK bootstrap:")
        print(f"  ig_did={self.state.get('ig_did')}")
        print(f"  mid={self.state.get('mid')}")
        print(f"  lsd={self.state.get('lsd')[:20]}...")
        return cookies

    def _graphql_headers(self, friendly_name: str) -> dict[str, str]:
        csrf = self.state.get("csrftoken") or self.session.cookies.get("csrftoken", "")
        lsd = self.state.get("lsd") or self.state.get("tokens", {}).get("lsd", "")
        return {
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.instagram.com",
            "Referer": SIGNUP_URL,
            "X-ASBD-ID": WEB_ASBD_ID,
            "X-CSRFToken": csrf,
            "X-FB-Friendly-Name": friendly_name,
            "X-FB-LSD": lsd,
            "X-IG-App-ID": IG_APP_ID,
            "X-IG-Max-Touch-Points": "0",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    def _build_form(
        self,
        step: str,
        variables: dict,
        *,
        email: str = "",
        password: str = "",
        username: str = "",
        name: str = "",
        birthday: tuple[int, int, int] | None = None,
        code: str = "",
    ) -> dict[str, str]:
        cfg = self.config.get(step) or {}
        if not cfg.get("doc_id"):
            raise RuntimeError(
                f"Falta config '{step}' em {CONFIG_PATH}. "
                f"Rode: python scripts/web_signup_lab.py import {step} <body.txt>"
            )
        form_template = deepcopy(cfg.get("form") or {})
        vars_template = deepcopy(cfg.get("variables") or variables)

        day, month, year = birthday or (1, 1, 2000)
        ig_did = self.state.get("ig_did") or ""
        replacers = [
            (EMAIL_KEYS, email),
            (PASSWORD_KEYS, password),
            (USERNAME_KEYS, username),
            (NAME_KEYS, name),
            (CODE_KEYS, code),
            (DAY_KEYS, str(day)),
            (MONTH_KEYS, str(month)),
            (YEAR_KEYS, str(year)),
            (REG_INSTANCE_KEYS, ig_did),
            (IG_REG_DATA_KEYS, self.state.get("ig_reg_data") or ""),
        ]
        if step == "confirm":
            replacers.append(({"machine_id"}, self.state.get("mid") or ig_did))
        vars_out = _deep_replace(vars_template, replacers)
        # client_mutation_id novo a cada request
        vars_out = _deep_replace(
            vars_out,
            [({"client_mutation_id", "clientMutationId"}, str(uuid.uuid4()))],
        )

        csrf = self.state.get("csrftoken", "")
        lsd = self.state.get("lsd", "")
        tokens = self.state.get("tokens") or {}

        form: dict[str, str] = {
            "av": form_template.get("av", "0"),
            "__user": form_template.get("__user", "0"),
            "__a": form_template.get("__a", "1"),
            "__req": form_template.get("__req", "1"),
            "dpr": form_template.get("dpr", "1"),
            "__ccg": form_template.get("__ccg", "GOOD"),
            "__comet_req": form_template.get("__comet_req", "7"),
            "fb_api_caller_class": form_template.get(
                "fb_api_caller_class", "RelayModern"
            ),
            "fb_api_req_friendly_name": cfg.get(
                "friendly_name", form_template.get("fb_api_req_friendly_name", "")
            ),
            "variables": json.dumps(vars_out, separators=(",", ":")),
            "server_timestamps": form_template.get("server_timestamps", "true"),
            "doc_id": str(cfg["doc_id"]),
            "lsd": lsd,
            "jazoest": jazoest(csrf),
            "__spin_b": form_template.get("__spin_b", "trunk"),
        }
        for key in ("__hs", "__rev", "__s", "__hsi", "__dyn", "__csr", "__hsdp", "__hblp", "__sjsp", "__spin_r", "__spin_t"):
            val = form_template.get(key) or tokens.get(key)
            if val:
                form[key] = str(val)
        for key in ("__crn", "qpl_active_flow_ids", "fb_api_analytics_tags", "__d", "fb_dtsg"):
            val = form_template.get(key)
            if val:
                form[key] = str(val)
        if step in ("register", "confirm") and "__crn" not in form:
            form["__crn"] = "comet.igweb.PolarisCAAIGRegistrationHomepageRoute"
        return form

    def graphql(
        self,
        step: str,
        *,
        variables_override: dict | None = None,
        **kwargs,
    ) -> dict:
        cfg = self.config.get(step) or {}
        friendly = cfg.get("friendly_name", "")
        if variables_override is not None:
            # Bypass template — usa variables prontas (CAA builtin)
            form = self._build_form(step, {}, **kwargs)
            form["variables"] = json.dumps(variables_override, separators=(",", ":"))
        else:
            form = self._build_form(step, cfg.get("variables") or {}, **kwargs)
        self._human.before_request_pause()
        print(f"POST {GRAPHQL_URL} ({friendly})")
        r = self.session.post(
            GRAPHQL_URL,
            headers=self._graphql_headers(friendly),
            data=urlencode(form),
            timeout=60,
        )
        text = r.text
        try:
            data = r.json()
        except Exception:
            data = {"raw": text[:2000], "status": r.status_code}
        if not data and not text:
            data = {"raw": f"(body vazio) status={r.status_code}", "status": r.status_code}
        elif isinstance(data, dict) and "errors" not in data and r.status_code >= 400:
            data.setdefault("raw", text[:500])
        cookies = dict(self.session.cookies)
        if cookies.get("csrftoken"):
            self.state["csrftoken"] = cookies["csrftoken"]
        self.state["cookies"] = cookies
        self.save_state()
        return {"status": r.status_code, "data": data, "headers": dict(r.headers)}

    def graphql_username_typeahead(
        self,
        prefix: str = "",
        *,
        verbose: bool = False,
    ) -> list[str]:
        """POST /api/graphql → username typeahead (igual DevTools)."""
        self.ensure_caa_defaults(self._last_signup_html)
        cfg = self.config.get("username_typeahead") or {}
        if not cfg.get("doc_id"):
            self.set_doc(
                "username_typeahead",
                CAA_USERNAME_TYPEAHEAD_DOC_ID,
                CAA_USERNAME_TYPEAHEAD_FRIENDLY,
            )
        variables = build_caa_username_typeahead_variables(prefix)
        result = self.graphql("username_typeahead", variables_override=variables)
        suggestions = self._extract_typeahead_suggestions(result.get("data"))
        if verbose:
            print(
                f"typeahead [{prefix!r}]: HTTP {result.get('status')} "
                f"sugestoes={len(suggestions)}"
            )
            if suggestions:
                print(f"  @ disponiveis: {', '.join(suggestions[:8])}")
        return suggestions

    def graphql_field_validation(
        self,
        fields: dict[str, Any],
        *,
        field: str,
        value: str,
        verbose: bool = False,
    ) -> list[str]:
        """POST /api/graphql → xfb_caa_registration_field_validation (sugestoes de @)."""
        self.ensure_caa_defaults(self._last_signup_html)
        cfg = self.config.get("field_validation") or {}
        doc_id = cfg.get("doc_id") or self._field_validation_doc_id(self._last_signup_html)
        if not doc_id:
            if verbose:
                print(
                    "field_validation: doc_id nao encontrado. Na tela HTTP Toolkit anote "
                    "doc_id do POST graphql (useCAARegistrationFieldValidationMutation):\n"
                    "  python scripts/web_signup_lab.py set-doc field_validation NUMERO "
                    "useCAARegistrationFieldValidationMutation"
                )
            return []
        if not cfg.get("doc_id"):
            self.set_doc("field_validation", str(doc_id), CAA_FIELD_VALIDATION_FRIENDLY)

        ig_did = self.state.get("ig_did") or ""
        mid = self.state.get("mid") or ""

        if self._has_imported_variables("field_validation"):
            result = self.graphql(
                "field_validation",
                email=fields["email"],
                password=fields["password"],
                username=fields.get("username") or "",
                name=fields["name"],
            )
            suggestions = self._extract_username_suggestions_from_graphql(result.get("data"))
            if verbose:
                self._print_field_validation_result(field, value, result, suggestions)
            return suggestions

        for idx, builder in enumerate(CAA_FIELD_VALIDATION_VAR_BUILDERS, start=1):
            variables = builder(
                fields, ig_did, mid, field=field, value=value
            )
            result = self.graphql("field_validation", variables_override=variables)
            suggestions = self._extract_username_suggestions_from_graphql(result.get("data"))
            if verbose or suggestions:
                self._print_field_validation_result(field, value, result, suggestions)
            if suggestions:
                return suggestions
            blob = json.dumps(result.get("data") or {}, ensure_ascii=False)
            if "noncoercible_variable_value" in blob and idx < len(CAA_FIELD_VALIDATION_VAR_BUILDERS):
                if verbose:
                    print(f"  field_validation: tentando variables v{idx + 1}...")
                continue
            if suggestions or "xfb_caa_registration_field_validation" in blob:
                break
        return []

    def _print_field_validation_result(
        self,
        field: str,
        value: str,
        result: dict,
        suggestions: list[str],
    ) -> None:
        status = result.get("status")
        payload = result.get("data") or {}
        inner = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        fv = (inner or {}).get("xfb_caa_registration_field_validation") or {}
        print(
            f"field_validation [{field}={value!r}]: HTTP {status} "
            f"status={fv.get('status', '?')} sugestoes={len(suggestions)}"
        )
        if fv.get("error"):
            err = fv["error"]
            print(f"  error: {err.get('field')} — {err.get('message')}")
        if suggestions:
            print(f"  @ disponiveis: {', '.join(suggestions[:8])}")

    def caa_username_suggestions(
        self,
        fields: dict[str, Any],
        *,
        verbose: bool = False,
    ) -> list[str]:
        """Busca @ via GraphQL CAA typeahead (igual browser / DevTools)."""
        email = fields["email"]
        preferred = (fields.get("username") or "").strip()
        prefixes = typeahead_prefix_candidates(email, fields["name"], preferred)

        seen: set[str] = set()
        merged: list[str] = []
        for prefix in prefixes:
            for user in self.graphql_username_typeahead(prefix, verbose=verbose):
                if user not in seen:
                    seen.add(user)
                    merged.append(user)
            if merged:
                return merged

        # fallback antigo (field_validation / REST)
        suggestions = self.graphql_field_validation(
            fields, field="CONTACTPOINT", value=email, verbose=verbose
        )
        if suggestions:
            return suggestions
        seed = re.sub(
            r"[^a-z0-9._]",
            "",
            _strip_accents(fields["name"].split()[0]).lower(),
        ) or "user"
        return self.graphql_field_validation(
            fields, field="USERNAME", value=seed, verbose=verbose
        )

    def _has_imported_variables(self, step: str) -> bool:
        cfg = self.config.get(step) or {}
        vars_ = cfg.get("variables") or {}
        if not vars_:
            return False
        if vars_ == {"input": {"actor_id": "0"}}:
            return False
        inp = vars_.get("input") or {}
        # Template auto-gerado (ensure_caa_defaults) — nao e captura real
        if step == "confirm":
            if inp.get("ig_reg_data") == "" and inp.get("machine_id") == "":
                return False
        if step == "register" and inp.keys() <= {"actor_id"}:
            return False
        return True

    def graphql_register(
        self,
        fields: dict[str, Any],
        *,
        builder=None,
    ) -> dict:
        ig_did = self.state.get("ig_did") or ""
        mid = self.state.get("mid") or ""
        d, m, y = fields["birthday"]
        if self._has_imported_variables("register"):
            return self.graphql(
                "register",
                email=fields["email"],
                password=fields["password"],
                username=fields["username"],
                name=fields["name"],
                birthday=(d, m, y),
            )
        build = builder or build_caa_register_variables
        vars_ = build(fields, ig_did, mid)
        return self.graphql(
            "register",
            variables_override=vars_,
            email=fields["email"],
            password=fields["password"],
            username=fields["username"],
            name=fields["name"],
            birthday=(d, m, y),
        )

    def graphql_register_finish(
        self,
        *,
        email: str,
        signup_code: str,
        fields: dict[str, Any],
        verify_code: str | None = None,
    ) -> dict | None:
        """Tenta useCAARegistrationFormSubmitMutation apos email+codigo OK."""
        self.ensure_caa_defaults(self._last_signup_html)
        verify_code = verify_code or self.state.get("verify_code") or ""
        ig_did = self.state.get("ig_did") or ""
        mid = self.state.get("mid") or ""
        for idx, builder in enumerate(CAA_REGISTER_VAR_BUILDERS, start=1):
            vars_ = builder(
                fields,
                ig_did,
                mid,
                verify_code=verify_code or None,
                signup_code=signup_code,
            )
            result = self.graphql("register", variables_override=vars_)
            self._print_graphql_result("register", result)
            if self._register_created(result):
                self._save_created_account(
                    email=email, fields=fields, method="graphql register"
                )
                return result.get("data") or {}
            data = result.get("data") or {}
            blob = json.dumps(data, ensure_ascii=False)
            if "noncoercible_variable_value" in blob and idx < len(
                CAA_REGISTER_VAR_BUILDERS
            ):
                print(f"  tentando formato de variables v{idx + 1}...")
                continue
            break
        return None

    def ensure_confirm_doc_id(self, confirm_doc_id: str | None = None) -> str | None:
        """Garante doc_id do useCAAFBConfirmationFormSubmitMutation."""
        if confirm_doc_id:
            self.set_doc("confirm", confirm_doc_id, CAA_CONFIRM_FRIENDLY)
        doc = (self.config.get("confirm") or {}).get("doc_id") or CAA_CONFIRM_DOC_ID
        if doc:
            return str(doc)
        print(
            "\n>>> FALTA doc_id de confirmacao (useCAAFBConfirmationFormSubmitMutation).\n"
            ">>> No Chrome: F12 > Network > graphql > ao colar codigo e clicar Continuar\n"
            ">>> Payload > doc_id=NUMEROS\n"
            ">>>   python scripts/web_signup_lab.py set-doc confirm NUMERO "
            "useCAAFBConfirmationFormSubmitMutation\n"
            ">>> Ou rode com: --confirm-doc-id NUMERO\n"
        )
        typed = input("doc_id confirm (numeros visiveis no DevTools): ").strip()
        if typed:
            self.set_doc("confirm", typed, CAA_CONFIRM_FRIENDLY)
            return typed
        return None

    @staticmethod
    def _confirm_created(result: dict) -> bool:
        data = result.get("data") or {}
        blob = json.dumps(data, ensure_ascii=False)
        if "created_user_id" in blob:
            return True
        if "confirmation_submit" in blob and "SUCCESS" in blob:
            return True
        cookies = dict(result.get("cookies") or {})
        return bool(cookies.get("sessionid"))

    def graphql_confirm_finish(
        self,
        *,
        email: str,
        verify_code: str,
        signup_code: str,
        fields: dict[str, Any],
        confirm_doc_id: str | None = None,
    ) -> dict | None:
        """Cria conta via GraphQL confirm — igual browser (NAO web_create_ajax)."""
        if not self.ensure_confirm_doc_id(confirm_doc_id):
            return None
        self.ensure_caa_defaults(self._last_signup_html)
        ig_did = self.state.get("ig_did") or ""
        mid = self.state.get("mid") or ""
        ntf_context = self.state.get("ntf_context") or ""
        ig_reg_data = self.state.get("ig_reg_data") or ""
        machine_id = mid or ig_did
        if not ig_reg_data and not self._has_imported_variables("confirm"):
            print(
                "\n>>> BLOQUEIO: sem ig_reg_data — confirm GraphQL nao roda.\n"
                ">>> O REST manda/valida o codigo, mas NAO gera ig_reg_data.\n"
                ">>> ig_reg_data so vem do Cadastrar (GraphQL register no browser).\n"
                ">>> Register GraphQL falhou aqui — conta nao fecha sem isso.\n"
            )
            return None
        print("--- graphql confirm (CAA — igual browser) ---")
        self._human.pause("confirmando codigo no GraphQL", 1.5, 3.0)
        confirm_attempts: list[tuple[str, dict[str, Any]]] = []
        if ig_reg_data:
            confirm_attempts.append(
                (
                    "browser",
                    build_caa_confirm_variables_browser(
                        verify_code, ig_reg_data, machine_id
                    ),
                )
            )
            confirm_attempts.append(
                (
                    "browser+ig_did",
                    build_caa_confirm_variables_browser(
                        verify_code, ig_reg_data, ig_did
                    ),
                )
            )
        if self._has_imported_variables("confirm"):
            confirm_attempts.append(("imported template", {}))
        for idx, (label, vars_) in enumerate(confirm_attempts, start=1):
            if label == "imported template":
                result = self.graphql("confirm", email=email, code=verify_code)
            else:
                if idx > 1:
                    print(f"  tentando confirm formato {label}...")
                result = self.graphql("confirm", variables_override=vars_)
            self._print_graphql_result("confirm", result)
            cookies = dict(self.session.cookies)
            if self._confirm_created(result) or cookies.get("sessionid"):
                self._save_created_account(
                    email=email, fields=fields, method="graphql confirm"
                )
                return result.get("data") or {}
            data = result.get("data") or {}
            blob = json.dumps(data, ensure_ascii=False)
            if "noncoercible_variable_value" not in blob:
                break
        return None

    def graphql_confirm(self, email: str, code: str) -> dict:
        ig_did = self.state.get("ig_did") or ""
        ntf_context = self.state.get("ntf_context") or ""
        if self._has_imported_variables("confirm"):
            return self.graphql("confirm", email=email, code=code)
        vars_ = build_caa_confirm_variables(
            email, code, ig_did, ntf_context=ntf_context
        )
        return self.graphql(
            "confirm", variables_override=vars_, email=email, code=code
        )

    @staticmethod
    def _extract_ntf_context(data: dict) -> str:
        blob = json.dumps(data, ensure_ascii=False)
        m = re.search(r'"ntf_context"\s*:\s*"([^"]+)"', blob)
        return m.group(1) if m else ""

    @staticmethod
    def _register_failed(data: dict) -> bool:
        blob = json.dumps(data, ensure_ascii=False)
        if "homepage_submit" in blob and "SUCCESS" in blob:
            return False
        if "caa_registration" in blob and "errors" not in blob:
            return False
        return "errors" in blob or (
            "noncoercible_variable_value" in blob
            or "Não foi possível processar" in blob
        )

    def run_caa(
        self,
        email: str,
        *,
        code: str | None = None,
        name: str | None = None,
        username: str | None = None,
        confirm_doc_id: str | None = None,
    ) -> int:
        """Fluxo CAA GraphQL (igual browser) — sem web_create_ajax."""
        fields = resolve_signup_fields(email=email, name=name, username=username)
        self.state["last_signup_name"] = fields["name"]
        self.state["last_signup_username"] = fields["username"]
        self.save_state()
        _print_profile(fields)

        self.bootstrap()
        self.ensure_caa_defaults(self._last_signup_html)

        if confirm_doc_id:
            self.set_doc("confirm", confirm_doc_id, CAA_CONFIRM_FRIENDLY)
        elif not (self.config.get("confirm") or {}).get("doc_id"):
            print(
                "\nSe falhar na confirmacao, olhe na tela HTTP Toolkit "
                "o doc_id do request useCAAFBConfirmationFormSubmitMutation "
                "(campo numerico) e rode:\n"
                "  python scripts/web_signup_lab.py set-doc confirm NUMERO "
                "useCAAFBConfirmationFormSubmitMutation\n"
            )

        reg = None
        for idx, builder in enumerate(CAA_REGISTER_VAR_BUILDERS, start=1):
            reg = self.graphql_register(fields, builder=builder)
            self._print_graphql_result("register", reg)
            data = reg.get("data") or {}
            if not self._register_failed(data):
                ntf = self._extract_ntf_context(data)
                if ntf:
                    self.state["ntf_context"] = ntf
                    self.save_state()
                break
            blob = json.dumps(data, ensure_ascii=False)
            if "noncoercible_variable_value" not in blob:
                return 1
            if idx < len(CAA_REGISTER_VAR_BUILDERS):
                print(f"  tentando formato de variables v{idx + 1}...")
            else:
                print(
                    "\n>>> variables ainda invalidas para o GraphQL.\n"
                    ">>> Opcao A: Chrome F12 > Network > POST graphql (cadastro) > "
                    "Copy as cURL > salve register.curl.txt\n"
                    ">>>   python scripts/web_signup_lab.py import-curl register register.curl.txt\n"
                    ">>> Opcao B: leia o JSON de variables na tela HTTP Toolkit e rode:\n"
                    ">>>   python scripts/web_signup_lab.py set-vars register '{...}'\n"
                )
                return 1

        data = (reg or {}).get("data") or {}
        blob = json.dumps(data, ensure_ascii=False)
        if reg.get("status", 0) >= 400 or (
            "errors" in blob
            and "homepage_submit" not in blob
            and "caa_registration" not in blob
        ):
            return 1

        verify_code = self._prompt_verify_code(code)
        if not (self.config.get("confirm") or {}).get("doc_id"):
            typed = input(
                "doc_id confirmacao (numeros na tela HTTP Toolkit, ou Enter para tentar mesmo assim): "
            ).strip()
            if typed:
                self.set_doc("confirm", typed, CAA_CONFIRM_FRIENDLY)

        conf = self.graphql_confirm(email, verify_code)
        self._print_graphql_result("confirm", conf)
        data = conf.get("data") or {}
        blob = json.dumps(data, ensure_ascii=False)
        cookies = dict(self.session.cookies)
        if "created_user_id" in blob or cookies.get("sessionid"):
            self._save_created_account(
                email=email,
                fields=fields,
                method="CAA GraphQL run_caa",
            )
            return 0
        return 1

    def import_body(self, step: str, body_path: Path) -> None:
        raw = body_path.read_text(encoding="utf-8")
        if raw.lstrip().startswith("curl"):
            raw = _extract_body_from_curl(raw)
        form = _parse_urlencoded_body(raw)
        self._save_imported_form(step, form)

    def import_curl(self, step: str, curl_path: Path) -> None:
        raw = curl_path.read_text(encoding="utf-8")
        body = _extract_body_from_curl(raw)
        form = _parse_urlencoded_body(body)
        self._save_imported_form(step, form)

    def _save_imported_form(self, step: str, form: dict[str, str]) -> None:
        friendly = form.get("fb_api_req_friendly_name", "")
        doc_id = form.get("doc_id", "")
        if not doc_id:
            raise ValueError("Sem doc_id — exporte o cURL completo desse request.")
        variables_raw = form.get("variables", "{}")
        try:
            variables = json.loads(variables_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"variables invalido: {exc}") from exc
        skip = {"variables", "fb_api_req_friendly_name", "doc_id"}
        form_meta = {k: v for k, v in form.items() if k not in skip}
        self.config[step] = {
            "friendly_name": friendly,
            "doc_id": doc_id,
            "variables": variables,
            "form": form_meta,
        }
        self.save_config()
        print(f"Importado '{step}' -> {CONFIG_PATH}")
        print(f"  friendly_name: {friendly}")
        print(f"  doc_id: {doc_id}")

    def register(
        self,
        *,
        email: str,
        password: str,
        username: str,
        name: str,
        birthday: tuple[int, int, int],
    ) -> dict:
        self.bootstrap()
        result = self.graphql(
            "register",
            email=email,
            password=password,
            username=username,
            name=name,
            birthday=birthday,
        )
        data = result.get("data") or {}
        blob = json.dumps(data, ensure_ascii=False)
        if "caa_registration" in blob:
            print("OK — Instagram pediu confirmação (email enviado).")
        elif "field_validation" in blob or "xfb_caa" in blob:
            print("Resposta CAA (validação/cadastro):", blob[:500])
        else:
            print("Resposta:", blob[:800])
        return result

    def confirm(self, *, email: str, code: str) -> dict:
        if not self.state.get("ig_did"):
            self.bootstrap()
        result = self.graphql("confirm", email=email, code=code)
        data = result.get("data") or {}
        blob = json.dumps(data, ensure_ascii=False)
        cookies = dict(self.session.cookies)
        if "created_user_id" in blob or cookies.get("sessionid"):
            print("OK — conta confirmada!")
            if cookies.get("ds_user_id"):
                print(f"  ds_user_id={cookies.get('ds_user_id')}")
            fields = resolve_signup_fields(
                email=email,
                name=self.state.get("last_signup_name"),
                username=self.state.get("last_signup_username"),
            )
            self._save_created_account(
                email=email, fields=fields, method="graphql confirm"
            )
        else:
            print("Resposta confirmação:", blob[:800])
        return result

    # ── Modo simples (API web antiga — NÃO precisa copiar HTTP Toolkit) ──

    def _api_headers(self, referer: str | None = None) -> dict[str, str]:
        csrf = self.state.get("csrftoken") or self.session.cookies.get("csrftoken", "")
        rev = (self.state.get("tokens") or {}).get("__rev", "")
        headers = {
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": IG_WEB_ORIGIN,
            "Referer": referer or SIGNUP_URL,
            "X-ASBD-ID": WEB_ASBD_ID,
            "X-CSRFToken": csrf,
            "X-IG-App-ID": IG_APP_ID,
            "X-IG-WWW-Claim": "0",
            "X-Requested-With": "XMLHttpRequest",
            "X-Instagram-AJAX": rev or "1045018660",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "sec-ch-ua": SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "x-ig-nav-chain": self._human.nav_chain(),
        }
        return headers

    def username_suggestions_list(
        self,
        email: str,
        name: str,
        fields: dict[str, Any] | None = None,
        *,
        referer: str | None = None,
        verbose: bool = True,
    ) -> list[str]:
        """Lista usernames — prioridade: GraphQL CAA, fallback REST legado."""
        fld = fields or {
            "email": email,
            "name": name,
            "username": "",
            "password": DEFAULT_PASSWORD,
            "birthday": DEFAULT_BIRTHDAY,
        }
        if self.state.get("lsd"):
            suggestions = self.caa_username_suggestions(fld, verbose=verbose)
            if suggestions:
                return suggestions
            if verbose:
                print("GraphQL CAA sem sugestoes — tentando endpoint REST legado...")

        first = name.split()[0] if name else "user"
        referer = referer or (
            f"{IG_WEB_ORIGIN}/accounts/signup/username/"
            if self.state.get("signup_code")
            else f"{IG_WEB_ORIGIN}/accounts/emailsignup/"
        )
        payloads = [
            {"email": email, "name": first},
            {"email": email, "name": name},
            {"email": email, "name": first, "username": ""},
        ]
        for payload in payloads:
            r = self.session.post(
                f"{IG_WEB_ORIGIN}/api/v1/web/accounts/username_suggestions/",
                headers=self._api_headers(referer),
                data=payload,
                timeout=45,
            )
            try:
                data = r.json()
            except Exception:
                if verbose:
                    print(
                        f"username_suggestions REST HTTP {r.status_code} "
                        f"(nao-JSON): {r.text[:300]!r}"
                    )
                continue
            suggestions = data.get("suggestions") or []
            if suggestions:
                return [str(u) for u in suggestions if u]
            if verbose:
                print(
                    f"username_suggestions REST HTTP {r.status_code} "
                    f"payload={payload!r} -> {json.dumps(data, ensure_ascii=False)[:400]}"
                )
        return []

    def pick_instagram_username(
        self,
        email: str,
        name: str,
        *,
        fields: dict[str, Any] | None = None,
        preferred: str | None = None,
        exclude: set[str] | None = None,
    ) -> str:
        """Username só das sugestões do Instagram — nunca inventa."""
        exclude = exclude or set()
        preferred = (preferred or "").lstrip("@").strip()
        fld = fields or {
            "email": email,
            "name": name,
            "username": preferred or "",
            "password": DEFAULT_PASSWORD,
            "birthday": DEFAULT_BIRTHDAY,
        }
        for attempt in range(1, 4):
            self._human.pause("consultando usernames disponiveis no Instagram", 0.8, 2.0)
            suggestions = self.username_suggestions_list(email, name, fields=fld)
            if suggestions:
                print(f"  sugestoes IG: {', '.join(suggestions[:5])}")
            if preferred and preferred in suggestions and preferred not in exclude:
                print(f"username do Instagram: @{preferred}")
                return preferred
            for user in suggestions:
                if user not in exclude:
                    print(f"username do Instagram: @{user}")
                    return user
            if attempt < 3:
                self._human.pause("buscando outras sugestoes de username", 1.0, 2.5)
        raise RuntimeError(
            "Instagram nao retornou username disponivel. "
            "Tente de novo em alguns minutos (rate limit)."
        )

    def ensure_instagram_username(
        self,
        fields: dict[str, Any],
        *,
        preferred: str | None = None,
        exclude: set[str] | None = None,
    ) -> str:
        username = self.pick_instagram_username(
            fields["email"],
            fields["name"],
            fields=fields,
            preferred=preferred or fields.get("username") or None,
            exclude=exclude,
        )
        fields["username"] = username
        self.state["last_signup_username"] = username
        self.save_state()
        return username

    def _save_ig_reg_data(self, payload: Any) -> bool:
        ig_reg = _extract_ig_reg_data(payload)
        if ig_reg:
            self.state["ig_reg_data"] = ig_reg
            self.state["ntf_context"] = ig_reg
            self.save_state()
            print(f"  ig_reg_data salvo ({len(ig_reg)} chars)")
            return True
        return False

    def caa_register_send_email(self, fields: dict[str, Any]) -> bool:
        """Cadastro CAA GraphQL — envia email e salva ig_reg_data para confirm."""
        self.ensure_caa_defaults(self._last_signup_html)
        self._human.goto_email_form()
        self._human.type_pause("email", fields["email"])
        self._human.think_pause()
        self.graphql_field_validation(
            fields, field="CONTACTPOINT", value=fields["email"], verbose=False
        )
        self._human.think_pause(0.35)
        self.graphql_field_validation(
            fields, field="USERNAME", value=fields["username"], verbose=False
        )
        self._human.think_pause()
        self._human.pause("clicando Cadastrar (GraphQL register)", 1.5, 3.5)
        for idx, builder in enumerate(CAA_REGISTER_VAR_BUILDERS, start=1):
            reg = self.graphql_register(fields, builder=builder)
            self._print_graphql_result("register", reg)
            data = reg.get("data") or {}
            blob = json.dumps(data, ensure_ascii=False)
            self._save_ig_reg_data(data)
            if _register_submit_ok(data) or self.state.get("ig_reg_data"):
                if self.state.get("ig_reg_data"):
                    print("register GraphQL OK — confira email")
                else:
                    print("register GraphQL OK (sem ntf_context na resposta)")
                return True
            if "noncoercible_variable_value" in blob and idx < len(
                CAA_REGISTER_VAR_BUILDERS
            ):
                print(f"  tentando register variables v{idx + 1}...")
                continue
            break
        return False

    def send_verify_email(self, email: str) -> bool:
        mid = self.state.get("mid") or self.session.cookies.get("mid", "")
        if not mid:
            raise RuntimeError("Sem mid/device_id.")
        self._human.goto_email_form()
        self._human.type_pause("email", email)
        self._human.pause("clicando em Avançar / enviar codigo", 0.5, 1.2)
        r = self.session.post(
            f"{IG_WEB_ORIGIN}/api/v1/accounts/send_verify_email/",
            headers=self._api_headers(f"{IG_WEB_ORIGIN}/accounts/emailsignup/"),
            data={"device_id": mid, "email": email},
            timeout=45,
        )
        text = r.text
        ok = '"email_sent":true' in text or '"email_sent": true' in text
        print(f"send_verify_email: HTTP {r.status_code} email_sent={ok}")
        if not ok:
            print(text[:600])
        return ok

    def check_confirmation_code(self, email: str, code: str) -> str | None:
        mid = self.state.get("mid") or self.session.cookies.get("mid", "")
        self._human.goto_email_confirm()
        self._human.type_pause("codigo 6 digitos", code)
        self._human.pause("confirmando codigo", 0.4, 1.0)
        r = self.session.post(
            f"{IG_WEB_ORIGIN}/api/v1/accounts/check_confirmation_code/",
            headers=self._api_headers(
                f"{IG_WEB_ORIGIN}/accounts/signup/emailConfirmation/"
            ),
            data={"code": code, "device_id": mid, "email": email},
            timeout=45,
        )
        try:
            data = r.json()
        except Exception:
            print(r.text[:600])
            return None
        if data.get("status") != "ok":
            print("check_confirmation_code falhou:", json.dumps(data, ensure_ascii=False)[:500])
            return None
        signup_code = data.get("signup_code")
        blob = json.dumps(data, ensure_ascii=False)
        ntf = self._extract_ntf_context(data)
        if ntf:
            self.state["ntf_context"] = ntf
        self._save_ig_reg_data(data)
        m = re.search(r'"reg_instance"\s*:\s*"([^"]+)"', blob)
        if m:
            self.state["reg_instance"] = m.group(1)
        self.save_state()
        print("check_confirmation_code OK")
        return str(signup_code) if signup_code else None

    def username_suggestion(self, email: str, name: str) -> str | None:
        suggestions = self.username_suggestions_list(email, name)
        if suggestions:
            pick = suggestions[0]
            print(f"username sugerido pelo IG: {pick}")
            return pick
        return None

    def web_create_attempt(
        self,
        fields: dict[str, Any],
        *,
        referer: str | None = None,
    ) -> bool:
        """Pre-validacao (igual browser, na tela de username apos codigo)."""
        mid = self.state.get("mid") or self.session.cookies.get("mid", "")
        d, m, y = fields["birthday"]
        first = fields["name"].split()[0] if fields["name"] else "User"
        enc_pw = f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{fields['password']}"
        payload = {
            "enc_password": enc_pw,
            "email": fields["email"],
            "username": fields["username"],
            "first_name": first,
            "month": str(m),
            "day": str(d),
            "year": str(y),
            "client_id": mid,
            "seamless_login_enabled": "1",
            "tos_version": "row",
        }
        page_ref = referer or SIGNUP_USERNAME_URL
        self._human.pause("pre-validando formulario", 1.0, 2.5)
        r = self.session.post(
            f"{IG_WEB_ORIGIN}/api/v1/web/accounts/web_create_ajax/attempt/",
            headers=self._api_headers(page_ref),
            data=payload,
            timeout=45,
        )
        text = r.text
        ok = r.status_code == 200 and ("status" in text or "dryrun" in text.lower())
        print(f"web_create_attempt: HTTP {r.status_code} ok={ok}")
        if not ok and text:
            print(text[:400])
        return ok

    def web_create_ajax(
        self,
        *,
        email: str,
        signup_code: str,
        fields: dict[str, Any],
    ) -> dict | None:
        mid = self.state.get("mid") or self.session.cookies.get("mid", "")
        d, m, y = fields["birthday"]
        first = fields["name"].split()[0] if fields["name"] else "User"
        username = fields["username"]
        password = fields["password"]
        enc_pw = f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{password}"
        payload = {
            "enc_password": enc_pw,
            "email": email,
            "username": username,
            "first_name": first,
            "month": str(m),
            "day": str(d),
            "year": str(y),
            "client_id": mid,
            "seamless_login_enabled": "1",
            "tos_version": "row",
            "force_sign_up_code": signup_code,
        }
        self._human.goto_username()
        self._human.type_pause("nome", first)
        self._human.type_pause("senha", password)
        self._human.pause(f"escolhendo @{username}", 1.2, 2.8)
        self._human.pause("clicando em Cadastrar", 1.0, 2.5)
        r = self.session.post(
            f"{IG_WEB_ORIGIN}/api/v1/web/accounts/web_create_ajax/",
            headers=self._api_headers(SIGNUP_USERNAME_URL),
            data=payload,
            timeout=60,
        )
        self._last_http_status = r.status_code
        text = r.text
        self._last_error_body = text
        cookies = dict(self.session.cookies)
        if '"account_created":true' in text or '"account_created": true' in text:
            self._save_created_account(
                email=email, fields=fields, method="web_create_ajax"
            )
            try:
                return r.json()
            except Exception:
                return {"raw": text[:500]}
        print(
            f"web_create_ajax falhou: HTTP {r.status_code}, "
            f"len={len(text)}, body={text[:800]!r}"
        )
        if self._response_is_hard_block(r.status_code, text):
            self._note_hard_block(text)
        elif r.status_code == 429:
            print(
                "\n>>> HTTP 429 com body vazio = device_id/sessao marcada como bot.\n"
                ">>> Manual funciona porque o Chrome usa cookies NOVOS (ig_did limpo).\n"
                ">>> Solucao: sessao nova + fluxo completo de uma vez:\n"
                ">>>   python scripts/web_signup_lab.py new-session\n"
                f">>>   python scripts/web_signup_lab.py run-simple --email {email}\n"
            )
        return None

    def create_account_human(
        self,
        *,
        email: str,
        signup_code: str,
        fields: dict[str, Any],
        verify_code: str | None = None,
        confirm_doc_id: str | None = None,
    ) -> dict | None:
        """Cria conta igual browser: GraphQL confirm (NAO web_create_ajax)."""
        verify_code = verify_code or self.state.get("verify_code") or ""
        result = self.graphql_confirm_finish(
            email=email,
            verify_code=verify_code,
            signup_code=signup_code,
            fields=fields,
            confirm_doc_id=confirm_doc_id,
        )
        if result:
            return result
        if self.state.get("ig_reg_data"):
            print(
                "\n>>> Confirm GraphQL falhou (ig_reg_data existe — payload ou codigo).\n"
                ">>> Codigo expira rapido. Sessao nova + run-simple de novo.\n"
            )
        else:
            print(
                "\n>>> Confirm GraphQL falhou — sem ig_reg_data (register nao rodou).\n"
            )
        return None

    def _save_created_account(
        self,
        *,
        email: str,
        fields: dict[str, Any],
        method: str,
    ) -> None:
        record = self._build_account_record(email=email, fields=fields, method=method)
        path = save_account_record(record, email)
        print(f"OK — conta criada ({method})!")
        cookies = record["cookies"]
        if cookies.get("sessionid"):
            print(f"  sessionid={str(cookies['sessionid'])[:24]}...")
        print(f"  salvo em {path}")
        _save_json(OUT_COOKIES, record)
        print(f"  (copia ultima conta em {OUT_COOKIES.name})")

    def accounts_create_instagrapi(
        self,
        *,
        email: str,
        signup_code: str,
        fields: dict[str, Any],
    ) -> dict | None:
        """POST /api/v1/accounts/create/ — mesmo endpoint final do instagrapi.signup()."""
        mid = self.state.get("mid") or self.session.cookies.get("mid", "")
        d, m, y = fields["birthday"]
        first = fields["name"].split()[0] if fields["name"] else "User"
        username = fields["username"]
        password = fields["password"]
        enc_pw = f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{password}"
        ts = str(int(time.time()))
        sn_raw = f"{email}|{ts}|{secrets.token_hex(12)}"
        payload = {
            "jazoest": str(random.randint(22300, 22399)),
            "tos_version": "row",
            "suggestedUsername": "",
            "sn_result": "",
            "do_not_auto_login_if_credentials_match": "false",
            "enc_password": enc_pw,
            "username": username,
            "first_name": first,
            "month": str(m),
            "day": str(d),
            "year": str(y),
            "device_id": mid,
            "guid": mid,
            "_uuid": mid,
            "waterfall_id": str(uuid.uuid4()),
            "one_tap_opt_in": "true",
            "email": email,
            "force_sign_up_code": signup_code,
            "qs_stamp": "",
            "sn_nonce": sn_raw,
        }
        self._human.goto_username()
        self._human.pause(f"confirmando cadastro @{username}", 0.5, 1.3)
        print("POST /api/v1/accounts/create/ (instagrapi)")
        r = self.session.post(
            f"{IG_WEB_ORIGIN}/api/v1/accounts/create/",
            headers=self._api_headers(
                f"{IG_WEB_ORIGIN}/accounts/signup/username/"
            ),
            data=payload,
            timeout=60,
        )
        self._last_http_status = r.status_code
        text = r.text
        self._last_error_body = text
        cookies = dict(self.session.cookies)
        if '"account_created":true' in text or '"account_created": true' in text:
            self._save_created_account(
                email=email, fields=fields, method="accounts/create"
            )
            try:
                return r.json()
            except Exception:
                return {"raw": text[:500]}
        if cookies.get("sessionid") and '"created_user"' in text:
            self._save_created_account(
                email=email, fields=fields, method="accounts/create"
            )
            try:
                return r.json()
            except Exception:
                return {"raw": text[:500]}
        print(
            f"accounts/create falhou: HTTP {r.status_code}, "
            f"len={len(text)}, body={text[:800]!r}"
        )
        if self._response_is_needs_upgrade(text):
            print(
                ">>> needs_upgrade = endpoint mobile bloqueado no fluxo web. "
                "Ignorando (nao adianta repetir).\n"
            )
        elif self._response_is_hard_block(r.status_code, text):
            self._note_hard_block(text)
        return None

    def create_account_retry(
        self,
        *,
        email: str,
        signup_code: str,
        fields: dict[str, Any],
        verify_code: str | None = None,
        waits: tuple[int, ...] = (0, 90, 180),
        human_only: bool = False,
        confirm_doc_id: str | None = None,
    ) -> dict | None:
        """Tenta criar conta; human_only = graphql confirm (igual browser)."""
        if human_only:
            return self.create_account_human(
                email=email,
                signup_code=signup_code,
                fields=fields,
                verify_code=verify_code,
                confirm_doc_id=confirm_doc_id,
            )
        verify_code = verify_code or self.state.get("verify_code") or ""
        steps: list[tuple[str, Any]] = [
            ("web_create_ajax", self.web_create_ajax),
        ]
        if self._has_imported_variables("register"):
            steps.append(
                (
                    "graphql register (CAA)",
                    lambda **kw: self.graphql_register_finish(
                        verify_code=verify_code, **kw
                    ),
                )
            )
        steps.append(("accounts/create (instagrapi)", self.accounts_create_instagrapi))
        used_usernames: set[str] = set()
        skip_instagrapi = False
        for attempt, delay in enumerate(waits, start=1):
            if delay:
                print(f"Aguardando {delay}s antes da tentativa {attempt} (rate limit)...")
                time.sleep(delay)
            if self._last_hard_block:
                break
            self._last_hard_block = False
            saw_429 = False
            if not fields.get("username"):
                self.ensure_instagram_username(fields, exclude=used_usernames)
            for label, fn in steps:
                if label.startswith("accounts/create") and skip_instagrapi:
                    continue
                print(f"--- {label} ---")
                result = fn(email=email, signup_code=signup_code, fields=fields)
                if result:
                    return result
                if self._response_is_needs_upgrade(self._last_error_body):
                    skip_instagrapi = True
                if _response_is_username_taken(self._last_error_body):
                    old = fields["username"]
                    used_usernames.add(old)
                    print(f">>> @{old} recusado — pedindo nova sugestao ao Instagram...")
                    self.ensure_instagram_username(
                        fields, exclude=used_usernames
                    )
                    result = fn(email=email, signup_code=signup_code, fields=fields)
                    if result:
                        return result
                if self._last_hard_block:
                    break
                if self._last_http_status == 429:
                    saw_429 = True
            if self._last_hard_block:
                break
            if not saw_429:
                break
        return None

    def web_create_ajax_retry(
        self,
        *,
        email: str,
        signup_code: str,
        fields: dict[str, Any],
        waits: tuple[int, ...] = (0, 90, 180),
    ) -> dict | None:
        return self.create_account_retry(
            email=email,
            signup_code=signup_code,
            fields=fields,
            waits=waits,
        )

    def _finish_signup_caa_confirm(
        self,
        email: str,
        verify_code: str,
        *,
        confirm_doc_id: str | None = None,
    ) -> int:
        """Fallback: confirmacao GraphQL apos email+codigo OK."""
        print("\nTentando confirmacao CAA GraphQL...")
        self.ensure_caa_defaults(self._last_signup_html)
        if confirm_doc_id:
            self.set_doc("confirm", confirm_doc_id, CAA_CONFIRM_FRIENDLY)
        conf_doc = (self.config.get("confirm") or {}).get("doc_id")
        if not conf_doc:
            print(
                "Sem doc_id de confirmacao (useCAAFBConfirmationFormSubmitMutation).\n"
                "Capture no DevTools ao clicar Cadastrar e rode:\n"
                "  python scripts/web_signup_lab.py set-doc confirm NUMERO "
                "useCAAFBConfirmationFormSubmitMutation\n"
            )
            return 1
        try:
            conf = self.graphql_confirm(email, verify_code)
        except RuntimeError as exc:
            print(f"confirm GraphQL abortado: {exc}")
            return 1
        self._print_graphql_result("confirm", conf)
        data = conf.get("data") or {}
        blob = json.dumps(data, ensure_ascii=False)
        cookies = dict(self.session.cookies)
        if "created_user_id" in blob or cookies.get("sessionid"):
            fields = resolve_signup_fields(
                email=email,
                name=self.state.get("last_signup_name"),
                username=self.state.get("last_signup_username"),
            )
            self._save_created_account(
                email=email, fields=fields, method="CAA confirm finish"
            )
            return 0
        return 1

    def finish_signup(
        self,
        email: str,
        *,
        code: str | None = None,
        username: str | None = None,
        confirm_doc_id: str | None = None,
    ) -> int:
        """Continua cadastro com sessao salva (signup_code ou ig_reg_data CAA)."""
        signup_code = self.state.get("signup_code") or ""
        ig_reg_data = self.state.get("ig_reg_data") or ""
        if not signup_code and not ig_reg_data:
            print(
                "Sem progresso salvo (signup_code ou ig_reg_data).\n"
                "Rode run-simple primeiro."
            )
            return 1
        verify_code = (code or self.state.get("verify_code") or "").strip()
        if not verify_code:
            verify_code = self._prompt_verify_code(None)
        fields = resolve_signup_fields(
            email=email,
            name=self.state.get("last_signup_name"),
            username=username or self.state.get("last_signup_username"),
        )
        saved_user = (fields.get("username") or "").strip()
        self.bootstrap_resume()
        if saved_user:
            print(f"username salvo: @{saved_user} (sem reconsultar API)")
        else:
            try:
                self.ensure_instagram_username(fields, preferred=username or None)
            except RuntimeError as exc:
                print(f"Erro: {exc}")
                return 1
        _print_profile(fields)
        if signup_code:
            print(f"signup_code salvo: {signup_code[:8]}...")
        elif ig_reg_data:
            print(f"ig_reg_data salvo ({len(ig_reg_data)} chars) — fluxo CAA GraphQL")

        if self.create_account_retry(
            email=email,
            signup_code=str(signup_code),
            fields=fields,
            human_only=True,
            verify_code=verify_code,
            confirm_doc_id=confirm_doc_id,
        ):
            return 0
        return self._signup_failed_summary(
            email,
            reason="confirm GraphQL falhou — codigo pode ter expirado",
        )

    def probe_signup(self, email: str) -> int:
        """Mostra respostas cruas dos endpoints web (debug / comparar com HTTP Toolkit)."""
        if not self.state.get("lsd"):
            self.bootstrap()
        name = self.state.get("last_signup_name") or "Test User"
        print("\n=== probe: CAA username typeahead ===")
        self.ensure_caa_defaults(self._last_signup_html)
        probe_fields = {
            "email": email,
            "name": name,
            "username": "",
            "password": DEFAULT_PASSWORD,
            "birthday": DEFAULT_BIRTHDAY,
        }
        self.caa_username_suggestions(probe_fields, verbose=True)
        print("\n=== probe: send_verify_email (nao envia se dry-run) ===")
        print("  signup_code salvo:", self.state.get("signup_code") or "(nenhum)")
        print("  verify_code salvo:", self.state.get("verify_code") or "(nenhum)")
        print("\nFiltre no HTTP Toolkit / DevTools por:")
        print("  • graphql + useCAARegistrationUsernameTypeaheadQuery")
        print("    (response: xfb_caa_registration_homepage_fetch_username_typeahead)")
        print("  • send_verify_email")
        print("  • check_confirmation_code")
        print("  • web_create_ajax")
        print("  • graphql + useCAARegistrationFormSubmitMutation")
        print("  • graphql + useCAAFBConfirmationFormSubmitMutation")
        print("\nChrome DevTools (gratis): F12 > Network > botao direito POST > Copy as cURL")
        print("  python scripts/web_signup_lab.py import-curl register curl.txt")
        return 0

    @staticmethod
    def print_capture_guide() -> None:
        print(
            """
=== Capturar cadastro Instagram (HTTP Toolkit / DevTools) ===

1. Abra https://www.instagram.com/accounts/emailsignup/ no Chrome
   (com HTTP Toolkit interceptando OU F12 > Network)

2. Faca o cadastro MANUAL ate o fim — anote cada POST:

   ORDEM TIPICA (CAA web 2026):
   ┌─────────────────────────────────────────────────────────────┐
   │ POST .../send_verify_email/          → manda codigo email   │
   │ POST .../check_confirmation_code/    → valida 6 digitos     │
   │ POST .../api/graphql  → UsernameTypeaheadQuery (@ sugestoes)     │
   │ POST .../username_suggestions/       → legado (fallback)              │
   │ POST .../web_create_ajax/            → cria conta           │
   │ POST .../api/graphql                 → CAA register/confirm │
   └─────────────────────────────────────────────────────────────┘

3. No HTTP Toolkit: clique cada linha www.instagram.com e LEIA na tela:
   - URL / Path
   - Request headers (X-FB-Friendly-Name, doc_id se graphql)
   - Request body (mesmo sem copiar: anote doc_id numerico)

4. GraphQL — anote na tela:
   - doc_id (numero grande)
   - fb_api_req_friendly_name (ex: useCAARegistrationFormSubmitMutation)

5. Importar no lab (Chrome DevTools copy cURL — gratis):
   python scripts/web_signup_lab.py import-curl register register.curl.txt
   python scripts/web_signup_lab.py import-curl confirm confirm.curl.txt

   Ou so o doc_id:
   python scripts/web_signup_lab.py set-doc username_typeahead 9643835809045186 useCAARegistrationUsernameTypeaheadQuery
   python scripts/web_signup_lab.py set-doc register NUMERO useCAARegistrationFormSubmitMutation
   python scripts/web_signup_lab.py set-doc confirm NUMERO useCAAFBConfirmationFormSubmitMutation

6. Testar endpoints sem cadastrar:
   python scripts/web_signup_lab.py probe-signup --email seu@gmail.com

IMPORTANTE: typeahead (UsernameTypeaheadQuery) responde com prefixo curto
(ex. cmia, wqiik) mesmo no inicio do cadastro. O REST username_suggestions
so responde DEPOIS do email confirmado — run-simple pede username apos o codigo.
"""
        )

    def run_simple(
        self,
        email: str,
        *,
        code: str | None = None,
        name: str | None = None,
        username: str | None = None,
        confirm_doc_id: str | None = None,
    ) -> int:
        """Fluxo email API — o que ja funcionou (envia codigo, valida, cria)."""
        fields = resolve_signup_fields(email=email, name=name, username=username)
        self.state["last_signup_email"] = email
        self.state["last_signup_name"] = fields["name"]
        self.save_state()

        self.bootstrap()
        self.ensure_caa_defaults(self._last_signup_html)
        print(f"Nome: {fields['name']}")
        try:
            self.ensure_instagram_username(fields, preferred=username or None)
        except RuntimeError as exc:
            print(f"Erro username: {exc}")
            return 1

        if not self.caa_register_send_email(fields):
            print(
                "\n>>> register GraphQL falhou — tentando REST send_verify_email "
                "(confirm pode falhar sem ig_reg_data)...\n"
            )
            if not self.send_verify_email(email):
                return 1

        verify_code = self._prompt_verify_code(code)
        if self.state.get("ig_reg_data"):
            self.state["verify_code"] = verify_code
            self.save_state()
            self.simulate_signup_journey(after_code=True)
            _print_profile(fields)
            if self.create_account_retry(
                email=email,
                signup_code=self.state.get("signup_code") or "",
                fields=fields,
                human_only=True,
                verify_code=verify_code,
                confirm_doc_id=confirm_doc_id,
            ):
                return 0
            return self._signup_failed_summary(
                email, reason="confirm GraphQL falhou — confira ig_reg_data"
            )

        signup_code = self.check_confirmation_code(email, verify_code)
        if not signup_code:
            return 1
        self.state["signup_code"] = signup_code
        self.state["verify_code"] = verify_code
        self.save_state()

        self.simulate_signup_journey(after_code=True)
        try:
            self.ensure_instagram_username(fields, preferred=username or None)
        except RuntimeError as exc:
            print(f"Erro: {exc}")
            print(
                "\n>>> Username vazio apos confirmar email.\n"
                ">>> Capture no HTTP Toolkit o POST username_suggestions "
                "(depois do codigo) e rode:\n"
                ">>>   python scripts/web_signup_lab.py capture-guide\n"
                ">>>   python scripts/web_signup_lab.py probe-signup "
                f"--email {email}\n"
            )
            return 1
        _print_profile(fields)

        if self.create_account_retry(
            email=email,
            signup_code=signup_code,
            fields=fields,
            human_only=True,
            verify_code=verify_code,
            confirm_doc_id=confirm_doc_id,
        ):
            return 0
        return self._signup_failed_summary(
            email,
            reason="register GraphQL falhou — sem ig_reg_data para confirm",
        )

    def set_doc(self, step: str, doc_id: str, friendly_name: str) -> None:
        """Se conseguir LER doc_id na tela (nao copiar body inteiro)."""
        existing = deepcopy(self.config.get(step) or {})
        existing.update(
            {
                "friendly_name": friendly_name,
                "doc_id": str(doc_id),
                "variables": existing.get("variables") or {"input": {"actor_id": "0"}},
                "form": existing.get("form") or {},
            }
        )
        self.config[step] = existing
        self.save_config()
        print(f"doc_id salvo para '{step}': {doc_id}")

    def set_vars(self, step: str, variables_json: str) -> None:
        """Cola o JSON de variables que aparece na tela HTTP Toolkit."""
        try:
            variables = json.loads(variables_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON invalido: {exc}") from exc
        if not isinstance(variables, dict):
            raise ValueError("variables precisa ser um objeto JSON.")
        existing = deepcopy(self.config.get(step) or {})
        existing["variables"] = variables
        if not existing.get("doc_id"):
            existing["doc_id"] = {
                "register": CAA_REGISTER_DOC_ID,
                "confirm": CAA_CONFIRM_DOC_ID,
                "field_validation": "",
                "username_typeahead": CAA_USERNAME_TYPEAHEAD_DOC_ID,
            }.get(step, "")
        if not existing.get("friendly_name"):
            existing["friendly_name"] = {
                "register": CAA_REGISTER_FRIENDLY,
                "confirm": CAA_CONFIRM_FRIENDLY,
                "field_validation": CAA_FIELD_VALIDATION_FRIENDLY,
                "username_typeahead": CAA_USERNAME_TYPEAHEAD_FRIENDLY,
            }.get(step, "")
        existing.setdefault("form", {})
        self.config[step] = existing
        self.save_config()
        print(f"variables salvas para '{step}' em {CONFIG_PATH}")


def _parse_birthday(s: str) -> tuple[int, int, int]:
    """Aceita YYYY-MM-DD ou DD/MM/YYYY."""
    s = s.strip()
    if "/" in s:
        parts = s.split("/")
        if len(parts) != 3:
            raise argparse.ArgumentTypeError("Use DD/MM/YYYY")
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        return d, m, y
    parts = s.split("-")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD ou DD/MM/YYYY")
    y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    return d, m, y


def _print_profile(fields: dict[str, Any]) -> None:
    d, m, y = fields["birthday"]
    print("Perfil do cadastro:")
    print(f"  email:    {fields['email']}")
    print(f"  nome:     {fields['name']}")
    print(f"  username: {fields['username']}")
    print(f"  senha:    {fields['password']}")
    print(f"  nasc.:    {d:02d}/{m:02d}/{y}")


def run_instagrapi_signup(
    email: str,
    *,
    proxy: str | None = None,
    name: str | None = None,
    username: str | None = None,
    use_caa: bool = False,
) -> int:
    """Cadastro 100%% instagrapi (API mobile / Bloks CAA)."""
    from instagrapi import Client
    from instagrapi.mixins.challenge import ChallengeChoice

    fields = resolve_signup_fields(email=email, name=name, username=username)
    _print_profile(fields)
    d, m, y = fields["birthday"]

    cl = Client()
    if proxy:
        cl.set_proxy(proxy)
        print(f"Proxy: {proxy.split('@')[-1] if '@' in proxy else proxy}")

    def code_handler(uname: str, choice) -> str:
        email_choices = {
            ChallengeChoice.EMAIL,
            ChallengeChoice.EMAIL.value,
            getattr(ChallengeChoice.EMAIL, "name", "EMAIL"),
            1,
        }
        kind = "email" if choice in email_choices else "sms"
        return input(f"Codigo ({kind}, 6 digitos): ").strip()

    cl.challenge_code_handler = code_handler

    mode = "signup_caa_email" if use_caa else "signup"
    print(f"Modo instagrapi: {mode}\n")
    try:
        if use_caa:
            user = cl.signup_caa_email(
                fields["username"],
                fields["password"],
                email,
                full_name=fields["name"],
                year=y,
                month=m,
                day=d,
            )
        else:
            user = cl.signup(
                fields["username"],
                fields["password"],
                email=email,
                full_name=fields["name"],
                year=y,
                month=m,
                day=d,
            )
    except Exception as exc:
        print(f"instagrapi falhou: {exc}")
        last = getattr(cl, "last_json", None) or getattr(cl, "last_response", None)
        if last is not None:
            try:
                if hasattr(last, "text"):
                    print("ultima resposta:", str(last.text)[:1200])
                else:
                    print("ultima resposta:", json.dumps(last, ensure_ascii=False)[:1200])
            except Exception:
                pass
        if use_caa:
            print(
                "\nDica: tente sem --caa (signup legado):\n"
                f"  python scripts/web_signup_lab.py --proxy \"...\" "
                f"run-instagrapi --email {email}"
            )
        return 1

    print(f"OK — conta criada (@{user.username}, id={user.pk})")
    settings = cl.get_settings()
    record = {
        "instagrapi_settings": settings,
        "saved_at": int(time.time()),
        "email": email,
        "name": fields["name"],
        "username": fields["username"],
        "password": fields["password"],
        "birthday": f"{d:02d}/{m:02d}/{y}",
        "method": mode,
        "proxy": proxy,
    }
    path = save_account_record(record, email)
    _save_json(OUT_COOKIES, record)
    print(f"  salvo em {path}")
    print(f"  (copia ultima conta em {OUT_COOKIES.name})")
    return 0


def _run_register(lab: WebSignupLab, fields: dict[str, Any]) -> None:
    lab.state["last_signup_name"] = fields["name"]
    lab.state["last_signup_username"] = fields["username"]
    lab.save_state()
    _print_profile(fields)
    lab.register(
        email=fields["email"],
        password=fields["password"],
        username=fields["username"],
        name=fields["name"],
        birthday=fields["birthday"],
    )

def main() -> int:
    p = argparse.ArgumentParser(description="Lab signup web Instagram (CAA GraphQL)")
    p.add_argument(
        "--proxy",
        help="http://user:pass@host:port (ou env SIGNUP_PROXY)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("bootstrap", help="Só carrega cookies + LSD da página de signup")

    imp = sub.add_parser("import", help="Importa body URL-Encoded (ou cURL) de um arquivo")
    _CAA_STEPS = ["register", "confirm", "field_validation", "username_typeahead"]
    imp.add_argument("step", choices=_CAA_STEPS)
    imp.add_argument("body_file", type=Path)

    icurl = sub.add_parser("import-curl", help="Importa export cURL do HTTP Toolkit")
    icurl.add_argument("step", choices=_CAA_STEPS)
    icurl.add_argument("curl_file", type=Path)

    gen = sub.add_parser("gen-profile", help="Mostra nome/username gerados (teste)")
    gen.add_argument("--count", type=int, default=5)

    reg = sub.add_parser("register", help="Envia cadastro (precisa import register)")
    reg.add_argument("--email", required=True)
    reg.add_argument("--username", help="Opcional — senão gera automático")
    reg.add_argument("--name", help="Opcional — senão gera nome feminino")

    conf = sub.add_parser("confirm", help="Confirma código do email")
    conf.add_argument("--email", required=True)
    conf.add_argument("--code", required=True)

    run = sub.add_parser("run", help="Cadastro + confirmação interativa")
    run.add_argument("--email", required=True)
    run.add_argument("--username", help="Opcional — senão gera automático")
    run.add_argument("--name", help="Opcional — senão gera nome feminino")
    run.add_argument("--code", help="Se já tiver o código do email")

    simple = sub.add_parser(
        "run-simple",
        help="Cadastro via email (envia codigo, valida, cria conta)",
    )
    simple.add_argument("--email", required=True)
    simple.add_argument("--username", help="Opcional")
    simple.add_argument("--name", help="Opcional")
    simple.add_argument("--code", help="Codigo do email se ja tiver")
    simple.add_argument(
        "--confirm-doc-id",
        help="doc_id confirmacao CAA (HTTP Toolkit, opcional)",
    )

    finish = sub.add_parser(
        "finish-signup",
        help="Continua cadastro com signup_code salvo (sem novo email)",
    )
    finish.add_argument("--email", required=True)
    finish.add_argument("--code", help="Codigo email usado antes (ex: 417053)")
    finish.add_argument(
        "--username",
        help="Opcional — so usa se estiver nas sugestoes do Instagram",
    )
    finish.add_argument("--confirm-doc-id", help="doc_id confirmacao CAA (opcional)")

    ig = sub.add_parser(
        "run-instagrapi",
        help="Cadastro via instagrapi (mobile API / Bloks CAA)",
    )
    ig.add_argument("--email", required=True)
    ig.add_argument("--username", help="Opcional")
    ig.add_argument("--name", help="Opcional")
    ig.add_argument(
        "--caa",
        action="store_true",
        help="Usa signup_caa_email (Bloks) em vez de signup() legado",
    )

    caa = sub.add_parser("run-caa", help="Cadastro CAA GraphQL (igual browser)")
    caa.add_argument("--email", required=True)
    caa.add_argument("--username", help="Opcional")
    caa.add_argument("--name", help="Opcional")
    caa.add_argument("--code", help="Codigo do email se ja tiver")
    caa.add_argument(
        "--confirm-doc-id",
        help="doc_id numerico do request confirmation_submit (da tela HTTP Toolkit)",
    )

    sdoc = sub.add_parser("set-doc", help="Digita doc_id que aparece na tela")
    sdoc.add_argument("step", choices=_CAA_STEPS)
    sdoc.add_argument("doc_id")
    sdoc.add_argument("friendly_name")

    svars = sub.add_parser(
        "set-vars",
        help="Cola JSON de variables da tela HTTP Toolkit (campo variables)",
    )
    svars.add_argument("step", choices=_CAA_STEPS)
    svars.add_argument("variables_json")

    arch = sub.add_parser("archive-signup", help="Guarda cadastro atual (signup_code)")
    arch.add_argument(
        "label",
        nargs="?",
        help="Nome do backup (default: email). Ex: primewqii",
    )

    rest = sub.add_parser("restore-signup", help="Restaura cadastro guardado")
    rest.add_argument("label", help="Nome do backup (ex: primewqii_gmail_com)")

    sub.add_parser("list-pending", help="Lista cadastros guardados")

    sub.add_parser("list-accounts", help="Lista contas criadas (web_signup_accounts/)")

    sub.add_parser("new-session", help="Limpa sessao — novo email/proxy")

    sub.add_parser("capture-guide", help="Como capturar fluxo no HTTP Toolkit / DevTools")

    probe = sub.add_parser(
        "probe-signup",
        help="Debug: mostra resposta crua de username_suggestions etc.",
    )
    probe.add_argument("--email", required=True)

    args = p.parse_args()
    proxy = getattr(args, "proxy", None) or os.environ.get("SIGNUP_PROXY") or None
    lab = WebSignupLab(proxy=proxy)

    try:
        if args.cmd == "list-accounts":
            WebSignupLab.list_accounts()
            return 0
        if args.cmd == "bootstrap":
            lab.bootstrap()
            return 0
        if args.cmd == "import":
            lab.import_body(args.step, args.body_file)
            return 0
        if args.cmd == "import-curl":
            lab.import_curl(args.step, args.curl_file)
            return 0
        if args.cmd == "gen-profile":
            for _ in range(max(1, args.count)):
                name = generate_feminine_name()
                print(f"{name}  ->  (username vem do Instagram no run-simple)")
            return 0
        if args.cmd == "register":
            fields = resolve_signup_fields(
                email=args.email,
                name=getattr(args, "name", None),
                username=getattr(args, "username", None),
            )
            _run_register(lab, fields)
            return 0
        if args.cmd == "confirm":
            lab.confirm(email=args.email, code=args.code.strip())
            return 0
        if args.cmd == "run":
            fields = resolve_signup_fields(
                email=args.email,
                name=getattr(args, "name", None),
                username=getattr(args, "username", None),
            )
            _run_register(lab, fields)
            code = (args.code or input("Código do email (6 dígitos): ")).strip()
            lab.confirm(email=fields["email"], code=code)
            return 0
        if args.cmd == "run-simple":
            return lab.run_simple(
                args.email,
                code=getattr(args, "code", None),
                name=getattr(args, "name", None),
                username=getattr(args, "username", None),
                confirm_doc_id=getattr(args, "confirm_doc_id", None),
            )
        if args.cmd == "finish-signup":
            return lab.finish_signup(
                args.email,
                code=getattr(args, "code", None),
                username=getattr(args, "username", None),
                confirm_doc_id=getattr(args, "confirm_doc_id", None),
            )
        if args.cmd == "run-instagrapi":
            return run_instagrapi_signup(
                args.email,
                proxy=proxy,
                name=getattr(args, "name", None),
                username=getattr(args, "username", None),
                use_caa=getattr(args, "caa", False),
            )
        if args.cmd == "run-caa":
            return lab.run_caa(
                args.email,
                code=getattr(args, "code", None),
                name=getattr(args, "name", None),
                username=getattr(args, "username", None),
                confirm_doc_id=getattr(args, "confirm_doc_id", None),
            )
        if args.cmd == "set-doc":
            lab.set_doc(args.step, args.doc_id, args.friendly_name)
            return 0
        if args.cmd == "set-vars":
            lab.set_vars(args.step, args.variables_json)
            return 0
        if args.cmd == "archive-signup":
            lab.archive_signup(getattr(args, "label", None))
            return 0
        if args.cmd == "restore-signup":
            lab.restore_signup(args.label)
            return 0
        if args.cmd == "list-pending":
            lab.list_pending()
            return 0
        if args.cmd == "new-session":
            lab.new_session()
            return 0
        if args.cmd == "capture-guide":
            lab.print_capture_guide()
            return 0
        if args.cmd == "probe-signup":
            return lab.probe_signup(args.email)
    except Exception as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
