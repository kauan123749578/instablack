"""Camuflagem — ferramenta privada só para is_owner (processamento no browser)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.deps import get_owner_only
from app.templating import templates
from models.models import User

router = APIRouter(prefix="/camuflagem", tags=["camuflagem"])


@router.get("")
def camuflagem_page(
    request: Request,
    user: User = Depends(get_owner_only),
):
    # COOP/COEP: necessário para FFmpeg.wasm (SharedArrayBuffer) na aba Metadados
    resp = templates.TemplateResponse(
        "camuflagem.html",
        {
            "request": request,
            "user": user,
        },
    )
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    return resp
