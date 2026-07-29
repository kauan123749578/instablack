#!/usr/bin/env python3
"""Stress test de capacidade (50–70 users) SEM Instagram real.

Requisitos:
  - META_HTTP_MOCK=true (e opcional META_HTTP_MOCK_DELAY_MS=50–200)
  - Workers: publish + misc rodando
  - Redis + DB configurados

Critérios de aceite (plano):
  - schedule_lag p95 < 120s em GET /admin/metrics
  - fila publish sem crescimento monotônico
  - sem waiting_locks / idle in transaction longo

Uso típico (staging):

  export META_HTTP_MOCK=true
  export META_HTTP_MOCK_DELAY_MS=100
  # sobe worker-publish + worker-misc + beat
  python scripts/stress_meta_mock.py --users 60 --accounts 40 --burst 200

O script enfileira N tasks publish_once mockáveis via _execute_publish Meta
quando as contas forem provider=meta. Se não houver contas Meta no DB, usa
um dry-run que só mede profundidade de fila com tasks no-op via countdown.

Para carga realista: crie seed de contas Meta de teste no staging e rode
com --mode live.
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Stress Meta mock / capacidade")
    parser.add_argument("--users", type=int, default=60, help="Usuários simulados (doc)")
    parser.add_argument("--accounts", type=int, default=40, help="Contas/user (doc)")
    parser.add_argument("--burst", type=int, default=200, help="Tasks a enfileirar")
    parser.add_argument(
        "--mode",
        choices=("dry", "live"),
        default="dry",
        help="dry=só imprime plano; live=enfileira publish_once se houver contas",
    )
    parser.add_argument("--delay", type=float, default=0.05, help="Pausa entre enqueues")
    args = parser.parse_args()

    mock = os.environ.get("META_HTTP_MOCK", "").lower() in ("1", "true", "yes")
    print("=== stress_meta_mock ===")
    print(f"META_HTTP_MOCK={mock}")
    print(f"cenário doc: ~{args.users} users × {args.accounts} accounts")
    print(f"burst={args.burst} mode={args.mode}")
    print()
    print("Aceite: GET /admin/metrics → schedule_lag_seconds.p95 < 120")
    print("         queue_depth.publish estável após o burst")
    print()

    if not mock and args.mode == "live":
        print("ERRO: defina META_HTTP_MOCK=true antes do mode=live", file=sys.stderr)
        return 2

    if args.mode == "dry":
        demand_h = args.users * min(args.accounts, 50)
        print(f"Demanda aproximada 1 Reel/h: ~{demand_h} pubs/hora")
        print("Com META_GLOBAL_MAX_CONCURRENT=18 e T≈1s (mock):")
        print(f"  throughput teórico ≈ {18 * 3600} pubs/hora (mock rápido)")
        print("Com T≈60s real: ≈ 18*60 = 1080 pubs/hora")
        print()
        print("Próximos passos:")
        print("  1. Deploy worker-publish + worker-misc")
        print("  2. META_HTTP_MOCK=true no worker-publish (staging)")
        print("  3. python scripts/stress_meta_mock.py --mode live --burst 200")
        print("  4. Observar /admin/metrics por 10–30 min")
        return 0

    # live: enfileira publish_once para contas Meta existentes
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sqlalchemy import select

    from celery_app.tasks.publish import publish_once
    from core.database import session_scope
    from models.models import InstagramAccount

    with session_scope() as db:
        rows = list(
            db.execute(
                select(InstagramAccount.id, InstagramAccount.username)
                .where(
                    InstagramAccount.provider == "meta",
                    InstagramAccount.status.notin_(("deleted", "paused", "banned")),
                )
                .limit(max(1, args.burst))
            ).all()
        )

    if not rows:
        print("Nenhuma conta Meta no banco — crie seed no staging ou use --mode dry")
        return 1

    enqueued = 0
    for i in range(args.burst):
        acc_id, uname = rows[i % len(rows)]
        publish_once.apply_async(
            kwargs={
                "account_id": int(acc_id),
                "video_key": "videos/stress-mock.mp4",
                "thumb_key": None,
                "caption": f"stress mock {i}",
                "content_type": "reel",
            },
            countdown=int(i * args.delay),
        )
        enqueued += 1
        if enqueued % 50 == 0:
            print(f"enqueued {enqueued}/{args.burst} (last=@{uname})")

    print(f"OK: {enqueued} tasks na fila publish")
    print("Monitore: curl -b 'session=...' https://SEU-HOST/admin/metrics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
