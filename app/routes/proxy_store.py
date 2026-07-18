"""Loja de proxies com pagamento PIX via GBProxies."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.templating import templates
from core.database import get_db
from core.gbproxies import (
    GBProxiesError,
    create_pix_order,
    get_order,
    is_configured,
    normalized_order,
)
from models.models import ProxyOrder, User

router = APIRouter(prefix="/proxy-store", tags=["proxy-store"])


def _apply_provider_data(order: ProxyOrder, payload: dict) -> dict:
    data = normalized_order(payload)
    order.status = data["status"]
    order.paid = data["paid"]
    order.amount = data["amount"]
    order.pix_code = data["pix_code"]
    order.qr_code = data["qr_code"]
    order.proxies_json = json.dumps(data["proxies"], ensure_ascii=False)
    order.response_json = json.dumps(payload, ensure_ascii=False)
    return data


@router.get("")
def proxy_store(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    orders = db.scalars(
        select(ProxyOrder)
        .where(ProxyOrder.user_id == user.id)
        .order_by(desc(ProxyOrder.created_at))
        .limit(30)
    ).all()
    order_rows = []
    for order in orders:
        try:
            proxies = json.loads(order.proxies_json or "[]")
        except (json.JSONDecodeError, TypeError):
            proxies = []
        order_rows.append({"order": order, "proxies": proxies if isinstance(proxies, list) else []})
    return templates.TemplateResponse(
        "proxy_store.html",
        {
            "request": request,
            "user": user,
            "order_rows": order_rows,
            "store_configured": is_configured(),
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/orders")
def create_order(
    proxy_type: str = Form("ipv4"),
    country_id: int = Form(1),
    quantity: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if proxy_type not in ("ipv4",):
        return RedirectResponse("/proxy-store?error=type", status_code=status.HTTP_303_SEE_OTHER)
    if country_id <= 0 or quantity < 1 or quantity > 500:
        return RedirectResponse("/proxy-store?error=quantity", status_code=status.HTTP_303_SEE_OTHER)
    try:
        payload = create_pix_order(
            proxy_type=proxy_type,
            country_id=country_id,
            quantity=quantity,
        )
        data = normalized_order(payload)
    except GBProxiesError:
        return RedirectResponse("/proxy-store?error=provider", status_code=status.HTTP_303_SEE_OTHER)
    if not data["provider_order_id"]:
        return RedirectResponse("/proxy-store?error=response", status_code=status.HTTP_303_SEE_OTHER)

    order = ProxyOrder(
        user_id=user.id,
        provider_order_id=data["provider_order_id"],
        proxy_type=proxy_type,
        country_id=country_id,
        quantity=quantity,
    )
    _apply_provider_data(order, payload)
    db.add(order)
    db.commit()
    return RedirectResponse(
        f"/proxy-store?ok=created#order-{order.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/orders/{order_id}/status")
def order_status(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = db.get(ProxyOrder, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    was_paid = order.paid
    try:
        payload = get_order(order.provider_order_id)
        data = _apply_provider_data(order, payload)
        db.commit()
    except GBProxiesError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    if order.paid and not was_paid:
        from core.notifications import create_notification

        create_notification(
            user.id,
            "Proxies liberadas",
            f"Seu pedido #{order.provider_order_id} foi pago e os proxies já estão disponíveis.",
            kind="info",
            link="/proxy-store",
            force=True,
        )
    return {
        "ok": True,
        "status": order.status,
        "paid": order.paid,
        "proxies": data["proxies"],
    }
