# barto-link

Personal push-notification gateway for the BartoAI ecosystem.

Receives push requests from local tools (dbticker, mailcontrol, …) and
forwards them via APNs to the BartoLink iOS app.

## Architecture
## Components

- `src/main.py` — FastAPI app with `/health`, `/tokens/*`, `/push`
- `src/apns_client.py` — APNs sender with JWT auth (lazy-init)
- `src/tokens.py` — SQLite-backed device-token registry
- `src/auth.py` — Bearer-token verification (timing-safe)
- `src/config.py` — Pydantic-settings, loaded from `.env`

## Layout
/opt/barto-link/                  Code
/etc/barto-link/                  Config + secrets (chmod 700)
/etc/barto-link/auth-keys/*.p8    Apple APNs auth key
/var/lib/barto-link/tokens.db     SQLite registry
## Endpoints

| Endpoint               | Auth   | Purpose                                |
|------------------------|--------|----------------------------------------|
| `GET /health`          | none   | Liveness check                          |
| `POST /tokens/register`| Bearer | iOS app registers its APNs token       |
| `GET /tokens/list`     | Bearer | List active tokens (admin)             |
| `POST /push`           | Bearer | Send push to all active tokens         |

## Configuration

All config lives in `/etc/barto-link/barto-link.env` (mode 0600, owner barto).
Loaded automatically via the `.env` symlink in this directory.

Required:
- `APPLE_TEAM_ID`
- `APPLE_KEY_ID`
- `APPLE_KEY_PATH`
- `APPLE_BUNDLE_ID`
- `APNS_ENVIRONMENT` — sandbox or production
- `BARTO_LINK_API_TOKEN` — master bearer token

## Running

Production: managed via systemd
```bash
sudo systemctl status barto-link.service
```

Development:
```bash
source .venv/bin/activate
uvicorn src.main:app --host 127.0.0.1 --port 8765 --reload
```

## Testing

Send a synthetic push via CLI:
```bash
python tools/send_test_push.py <DEVICE_TOKEN>
```

## License

(C) 2026 Bartosz Stryjewski. All rights reserved.
