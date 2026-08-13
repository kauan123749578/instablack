#!/usr/bin/env python3
"""Busca doc_id de field_validation no HTML de signup."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from curl_cffi import requests as cr

from web_signup_lab import (
    CAA_FIELD_VALIDATION_ALT_NAMES,
    SIGNUP_URL,
    _extract_doc_id_from_html,
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

r = cr.get(
    SIGNUP_URL,
    impersonate="chrome131",
    headers={"User-Agent": UA},
    timeout=45,
)
html = r.text
print(f"HTML: {len(html)} bytes\n")

for name in CAA_FIELD_VALIDATION_ALT_NAMES:
    doc = _extract_doc_id_from_html(html, name)
    print(f"  {name}: {doc or 'NAO ACHOU'}")

patterns = [
    r"FieldValidation.{0,150}?\"id\"\s*:\s*\"(\d{10,})\"",
    r"field_validation.{0,200}?\"id\"\s*:\s*\"(\d{10,})\"",
    r"xfb_caa_registration_field_validation.{0,200}?\"id\"\s*:\s*\"(\d{10,})\"",
]
for pat in patterns:
    for m in re.finditer(pat, html, re.I | re.DOTALL):
        print(f"  regex ({pat[:40]}...): {m.group(1)}")

names = re.findall(r"useCAA[A-Za-z]{10,80}Mutation", html)
if names:
    print("\nMutations useCAA no HTML:")
    for n in sorted(set(names)):
        doc = _extract_doc_id_from_html(html, n)
        print(f"  {n}: {doc or '?'}")
