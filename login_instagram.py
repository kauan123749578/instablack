"""
Login Instagram → session.json (sessão MOBILE do instagrapi, não cookie do Chrome).

Usa o mesmo fluxo de produção: core.instagram.login_with_credentials
(device UUID estável por @ + proxy obrigatória).

Uso:
  python login_instagram.py --user deborateixei091
  python login_instagram.py --user X --code 123456   # se pedir 2FA

Proxy: IG_PROXY ou DEFAULT_PROXY no .env
  http://user:pass@host:port  |  host:port:user:pass

Por que user/senha falha com BadPassword:
  O IG bloqueia login private-API por confiança (IP/device), não é “bug” do
  instagrapi 2.18.12. Cookie do navegador expira rápido; a sessão que SEGURA
  é a gerada UMA vez por login(senha) + dump_settings — daí você só recarrega
  o session.json (sem logar de novo).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from core.instagram import (  # noqa: E402
    InstagramAuthError,
    InstagramTwoFactorRequired,
    login_with_credentials,
    normalize_proxy,
)

OUT = Path("session.json")


def _parse_args() -> argparse.Namespace:
    env_proxy = (
        os.environ.get("IG_PROXY", "").strip()
        or os.environ.get("DEFAULT_PROXY", "").strip()
        or None
    )
    p = argparse.ArgumentParser(
        description="Login user/senha (instagrapi) → session.json durável"
    )
    p.add_argument("--user", "-u", help="Username Instagram")
    p.add_argument("--password", "-p", help="Senha (melhor digitar no prompt)")
    p.add_argument("--code", help="Código 2FA / authenticator, se já tiver")
    p.add_argument("--proxy", default=env_proxy, help="Proxy (ou .env)")
    p.add_argument("--out", type=Path, default=OUT)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    username = (args.user or input("Username: ")).strip().lstrip("@")
    if not username:
        print("Username vazio.", file=sys.stderr)
        return 1

    password = args.password or getpass.getpass("Password: ")
    if not password:
        print("Senha vazia.", file=sys.stderr)
        return 1

    if not args.proxy:
        print(
            "Proxy obrigatória (DEFAULT_PROXY / IG_PROXY / --proxy).\n"
            "Sem proxy fixa o IG quase sempre devolve BadPassword.",
            file=sys.stderr,
        )
        return 1

    try:
        proxy = normalize_proxy(args.proxy)
    except Exception as exc:
        print(f"Proxy inválida: {exc}", file=sys.stderr)
        return 1

    print(f"Proxy: {proxy.split('@')[-1]}")
    print("Login via core.instagram (UUID estável + private API)…")

    code = (args.code or "").strip() or None
    try:
        settings = login_with_credentials(
            username, password, verification_code=code, proxy=proxy
        )
    except InstagramTwoFactorRequired:
        code = input("Código 2FA (authenticator): ").strip()
        if not code:
            print("2FA necessário.", file=sys.stderr)
            return 2
        try:
            settings = login_with_credentials(
                username, password, verification_code=code, proxy=proxy
            )
        except InstagramAuthError as exc:
            print(f"Falha após 2FA: {exc}", file=sys.stderr)
            return 3
    except InstagramAuthError as exc:
        print(
            f"\nLogin recusado: {exc}\n\n"
            "Isso NÃO se resolve “atualizando a lib”. O Instagram está\n"
            "rejeitando o login private-API dessa conta+proxy agora.\n\n"
            "Pra sessão que SEGURA (não cookie do Chrome):\n"
            "  1) Pare de tentar em loop (piora)\n"
            "  2) Confirme a senha no app oficial no celular\n"
            "  3) Espere algumas horas / use proxy residencial sticky\n"
            "     que a conta já usou (mesmo país)\n"
            "  4) Quando o login senha passar UMA vez, o session.json\n"
            "     gerado aqui é a sessão mobile — reutilize SEM logar de novo\n\n"
            "Pra postar Reels em produção sem depender disso: API oficial Meta\n"
            "(fluxo provider=meta no Instablack).",
            file=sys.stderr,
        )
        return 4

    args.out.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    u = (settings.get("username") or username).lstrip("@")
    print(f"OK @{u}")
    print(f"session.json salvo em: {args.out.resolve()}")
    print("Daqui pra frente: load_settings + account_info — NÃO chamar login() de novo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
