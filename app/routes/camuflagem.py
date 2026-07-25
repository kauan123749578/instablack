"""Camuflagem — ferramenta para usuários autenticados (processamento no browser)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.deps import get_current_user
from app.templating import templates
from models.models import User

router = APIRouter(prefix="/camuflagem", tags=["camuflagem"])


@router.get("")
def camuflagem_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    resp = templates.TemplateResponse(
        "camuflagem.html",
        {
            "request": request,
            "user": user,
        },
    )
    # Isola a página (processamento pesado de imagem/vídeo no browser)
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    return resp
