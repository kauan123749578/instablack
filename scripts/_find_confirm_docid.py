#!/usr/bin/env python3
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from curl_cffi import requests as cr
from scripts.web_signup_lab import (
    CAA_CONFIRM_FRIENDLY,
    CAA_REGISTER_FRIENDLY,
    SIGNUP_URL,
    _extract_doc_id_from_html,
)

r = cr.get(SIGNUP_URL, impersonate="chrome131", timeout=45)
html = r.text
print(f"HTML: {len(html)} bytes\n")

for name in (
    CAA_REGISTER_FRIENDLY,
    CAA_CONFIRM_FRIENDLY,
    "useCAARegistrationUsernameTypeaheadQuery",
):
    print(f"  {name}: {_extract_doc_id_from_html(html, name) or 'NAO'}")

names = re.findall(r"useCAA[A-Za-z0-9]{5,80}(?:Mutation|Query)", html)
print("\nMutations/queries useCAA:")
for n in sorted(set(names)):
    print(f"  {n}: {_extract_doc_id_from_html(html, n) or '?'}")
