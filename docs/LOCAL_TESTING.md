# Local Testing

## Running the automated tests

```powershell
.venv\Scripts\python.exe scripts\test-chat-browser.py     # UI smoke test (Playwright, fixture data, no server needed)
.venv\Scripts\python.exe scripts\test-api-security.py     # auth/session/permission checks against a running portal
```

`test-api-security.py` needs the real stack running (`docker compose ... up`, or `py apps/api/portal_server.py`)
and reads the admin login from `.env`. It only ever creates and logs into throwaway `apitest-*`
accounts with random emails — it never touches the real admin account's session or password, and
deactivates every account it creates when it finishes (even if a check fails partway through).


Run the database-backed local portal from the repository root:

```powershell
py apps/api/portal_server.py
```

Then visit:

```text
https://localhost:4173
```

For camera and microphone testing, use `https://localhost:4173` on the same PC.
The portal auto-enables HTTPS when these files exist:

- `data/certs/localhost.crt`
- `data/certs/localhost.key`

If those files are missing, the server falls back to HTTP.

The local server creates a dedicated admin account from these environment variables:

- `PORTAL_ADMIN_EMAIL`, default `admin@beenco.local`
- `PORTAL_ADMIN_PASSWORD`, required when bootstrapping or rotating the local admin account
- `PORTAL_ADMIN_NAME`, default `Beenco Administrator`

Employee signup always creates a member account. Every account, message, file metadata, event,
announcement, moderation action, and audit event is stored in PostgreSQL, via `DATABASE_URL` in
`.env` (there is no local SQLite/`.db` file — only uploaded file *bytes* live on disk, under
`PORTAL_DATA_DIR`/`data/uploads`).

Calls are also stored in the database. The Calls panel shows recent call history, and an
active call can open a larger meeting view in the main workspace for video and screen sharing.

File sharing uses an allowlist before metadata is stored. Allowed extensions are:

```text
.csv .docx .gif .jpeg .jpg .json .m4a .md .mov .mp3 .mp4 .ogg .pdf .png .pptx .rtf .txt .wav .webm .webp .xlsx
```

Executable files, scripts, macro-enabled Office files, archives, HTML/XML/SVG, installers, and
similar high-risk formats are blocked.

In local testing, uploaded attachment bytes are stored under `data/uploads` and downloaded back
with their original filename, extension, and MIME type. Files uploaded before this local byte
storage was added were metadata-only and need to be re-uploaded before they can download as the
original PDF/Office/image/media file.

Google sign-up appears in the UI. For a real OAuth flow, set `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI`; without those values, the app offers a local
database-backed Google-provider fallback for testing.

To run the portal itself with Docker instead of `py apps/api/portal_server.py` (this also starts
PostgreSQL for you — set `POSTGRES_PASSWORD` in `.env` first):

```powershell
Copy-Item .env.example .env
docker compose --env-file .env -f infra/compose/docker-compose.local.yml up --build
```

`--env-file .env` is required — without it, Compose doesn't read the root `.env` file, `POSTGRES_PASSWORD`
comes through blank, and the portal can't connect to the database.

For the larger infrastructure scaffold (Keycloak, MinIO, search, etc. — see the root `README.md`):

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml up -d
```

Map the internal hostnames to the portal host in AD DNS or in a local hosts file for lab testing:

- `connect.beenco.local`
- `auth.beenco.local`
- `media.beenco.local`
- `files.beenco.local`
