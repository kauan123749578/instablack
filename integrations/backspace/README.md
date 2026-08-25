# Backspace no Instablack

O Instablack usa [Backspace](https://github.com/TheZwiss/backspace) para **chat, voz, vídeo e tela** via `/chat`.

## Arquitetura

```
┌─────────────────────┐         ┌──────────────────────────┐
│  Instablack (Railway)│  iframe │  Backspace (VPS + Docker) │
│  /chat               │ ──────► │  chat.seudominio.com     │
│  provisiona conta    │  API    │  LiveKit + WebSocket     │
└─────────────────────┘         └──────────────────────────┘
```

- **Instablack** continua no Railway (Instagram, automações, dashboard).
- **Backspace** roda num **VPS** com Docker (voz precisa de portas UDP WebRTC).
- Usuários entram em **Chat** no menu → conta Backspace criada automaticamente.

## 1. Subir Backspace (VPS)

Requisitos: Linux, Docker, domínio apontando pro VPS.

```bash
cd integrations/backspace
git clone https://github.com/TheZwiss/backspace.git upstream   # opcional (install oficial)
# OU use o docker-compose desta pasta:

cp .env.example .env
# Edite DOMAIN, JWT_SECRET (openssl rand -hex 32), LIVEKIT_* se quiser voz

docker compose --profile voice up -d
```

**Portas firewall (com voz):**

| Porta | Protocolo | Uso |
|-------|-----------|-----|
| 80, 443 | TCP | HTTPS (Caddy) |
| 3478 | UDP | TURN |
| 7881 | TCP | WebRTC fallback |
| 50000–60000 | UDP | Mídia WebRTC |

Instalação oficial (recomendada na primeira vez):

```bash
git clone https://github.com/TheZwiss/backspace.git
cd backspace
./install.sh
```

## 2. Configurar Instablack (Railway)

Variáveis no serviço **web**:

```env
BACKSPACE_ENABLED=true
BACKSPACE_URL=https://chat.seudominio.com
```

Redeploy. O menu **Chat** aparece para quem tem `allow_voice_room` (ou owner).

## 3. Fluxo do usuário

1. Clica **Chat** no Instablack
2. Backend cria/vincula conta Backspace (`@username` Instablack → username Backspace)
3. Popup injeta JWT no Backspace (login automático)
4. Iframe mostra Discord completo (canais, voz, tela 4K, DMs)

Se o popup for bloqueado: **Abrir em nova aba** ou login manual (mesmo `@username`).

## 4. Limitações

| Cenário | Funciona? |
|---------|-----------|
| Backspace no VPS + Instablack Railway | ✅ Sim |
| Tudo só no Railway | ❌ Voz WebRTC precisa UDP no VPS |
| Cloudflare Tunnel só | ❌ Backspace docs: sem voz em tunnel mode |

## Links

- [Backspace GitHub](https://github.com/TheZwiss/backspace)
- [Backspace docs](https://github.com/TheZwiss/backspace/tree/main/docs/systems)
- [LiveKit no Backspace](https://github.com/TheZwiss/backspace#voice--video)
