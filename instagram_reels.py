"""
Instagram com instagrapi: login, reutiliza session.json e publica Reels.

Uso (rode de dentro da pasta instagram ou informe o caminho completo):
    cd instagram
    python instagram_reels.py --video ../meu_video.mp4 --caption "Minha legenda"
    python instagram_reels.py -v video.mp4 -c "Legenda" --thumb capa.jpg

Primeira vez (ou sessao expirada): o script pede login.
"""
import argparse
import sys
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    TwoFactorRequired,
)
from instagrapi.mixins.challenge import ChallengeChoice

BASE_DIR = Path(__file__).resolve().parent
SESSION_FILE = BASE_DIR / "session.json"


def normalizar_caminho(caminho: str) -> Path:
    """Remove aspas e espacos extras do caminho (comum ao colar no terminal)."""
    caminho = caminho.strip().strip('"').strip("'")
    return Path(caminho).expanduser().resolve()


def configurar_challenge_handler(cl: Client) -> None:
    def handler(username, choice):
        if choice == ChallengeChoice.EMAIL:
            return input(f"Codigo do EMAIL para @{username}: ").strip()
        if choice == ChallengeChoice.SMS:
            return input(f"Codigo do SMS para @{username}: ").strip()
        return input(f"Codigo de verificacao para @{username}: ").strip()

    cl.challenge_code_handler = handler


def login_interativo(cl: Client) -> None:
    print("\n=== Login necessario ===")
    print("1 - Usuario e senha")
    print("2 - Session ID do navegador\n")
    opcao = input("Escolha (1 ou 2): ").strip()

    if opcao == "2":
        print("\nChrome -> instagram.com -> F12 -> Application -> Cookies -> sessionid\n")
        sessionid = input("Cole o sessionid: ").strip()
        if not sessionid:
            raise ValueError("Session ID vazio.")
        cl.login_by_sessionid(sessionid)
        return

    username = input("Usuario: ").strip()
    password = input("Senha: ").strip()
    try:
        cl.login(username, password)
    except TwoFactorRequired:
        code = input("Codigo 2FA: ").strip()
        cl.login(username, password, verification_code=code)


def obter_cliente() -> Client:
    cl = Client()
    configurar_challenge_handler(cl)

    if SESSION_FILE.exists():
        cl.load_settings(str(SESSION_FILE))
        try:
            cl.account_info()
            print("Sessao reutilizada com sucesso.")
            cl.dump_settings(str(SESSION_FILE))
            return cl
        except LoginRequired:
            print("Sessao expirada.")

    try:
        login_interativo(cl)
    except BadPassword:
        print("\n[ERRO] Login recusado (senha errada ou IP bloqueado).")
        sys.exit(1)
    except ChallengeRequired:
        print("\n[ERRO] Verificacao extra necessaria. Rode de novo.")
        sys.exit(1)
    except PleaseWaitFewMinutes:
        print("\n[ERRO] Muitas tentativas. Aguarde alguns minutos.")
        sys.exit(1)

    cl.dump_settings(str(SESSION_FILE))
    user = cl.account_info()
    print(f"Login OK! @{user.username}")
    print(f"Sessao salva em {SESSION_FILE}")
    return cl


def publicar_reel(cl: Client, video: Path, caption: str, thumb: Path | None = None) -> None:
    if not video.exists():
        raise FileNotFoundError(f"Video nao encontrado: {video}")
    if thumb is not None and not thumb.exists():
        raise FileNotFoundError(f"Capa (thumbnail) nao encontrada: {thumb}")

    print(f"Publicando Reel: {video.name}")
    print(f"Legenda: {caption!r}")
    if thumb:
        print(f"Capa: {thumb.name}")
    else:
        print("Capa: gerada automaticamente do video")
    print("Aguarde, o upload pode demorar...\n")

    media = cl.clip_upload(video, caption, thumbnail=thumb)
    print(f"Reel publicado! ID: {media.pk}")
    if media.code:
        print(f"Link: https://www.instagram.com/reel/{media.code}/")


def main():
    parser = argparse.ArgumentParser(description="Login Instagram + publicar Reel")
    parser.add_argument("--video", "-v", help="Caminho do video (.mp4)")
    parser.add_argument("--caption", "-c", default="", help="Legenda do Reel")
    parser.add_argument(
        "--thumb", "-t",
        help="Imagem de capa do Reel (.jpg, .png). Proporcao 9:16 recomendada.",
    )
    args = parser.parse_args()

    cl = obter_cliente()
    user = cl.account_info()
    print(f"Conta: @{user.username}\n")

    video_path = args.video or input("Caminho do video (.mp4): ").strip()
    caption = args.caption if args.caption else input("Legenda do Reel: ").strip()

    thumb_path = args.thumb
    if not thumb_path and not args.video:
        thumb_input = input("Capa do Reel (.jpg/.png, Enter para auto): ").strip()
        thumb_path = thumb_input or None

    if not video_path:
        print("Informe o caminho do video.")
        sys.exit(1)

    thumb = normalizar_caminho(thumb_path) if thumb_path else None
    publicar_reel(cl, normalizar_caminho(video_path), caption, thumb)


if __name__ == "__main__":
    main()
