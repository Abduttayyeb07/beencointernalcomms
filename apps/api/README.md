# Beenco Connect API

This folder contains a single-file Python HTTP adapter backed by PostgreSQL, so the portal can
persist users, sessions, channels, messages, reactions, files, events, announcements, feature
flags, retention settings, and audit logs. It needs `psycopg` and `python-dotenv` installed
(`pip install -r apps/api/requirements.txt`) — see `apps/api/requirements.txt`.

Run locally from the repository root:

```powershell
py apps/api/portal_server.py
```

The web app and API are then available at `http://127.0.0.1:4173`. The database connection comes
from `DATABASE_URL` in `.env` (default: `postgresql://beenco:beenco@localhost:5432/beenco`); see
the root `README.md` for the `docker compose` command that starts Postgres alongside the portal.

Production implementation target:

- NestJS modules: `auth`, `users`, `channels`, `messages`, `files`, `calls`, `calendar`,
  `notifications`, `admin`, `audit`, `search`.
- Socket.IO gateway with Redis adapter for realtime delivery.
- Prisma schema matching the README core tables.
- tus upload integration backed by MinIO and ClamAV scan jobs through BullMQ.
- OpenSearch indexing workers for messages, file names, and extracted file text.
- LiveKit token service for calls, huddles, and event rooms.

The API contract lives in `packages/types/openapi.yaml`.
