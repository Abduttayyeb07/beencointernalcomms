# Beenco Connect Architecture

The README remains the product contract. This implementation adds a runnable static portal and
the production boundaries expected by the contract.

## Current Local Build

- `apps/api/portal_server.py` serves the web app and API on `http://127.0.0.1:4173`.
- PostgreSQL (already in place today, not just a production target) stores users, sessions,
  channels, memberships, messages, reactions, file metadata, events, announcements, retention
  settings, feature flags, notifications, and audit logs, via `DATABASE_URL`.
- `apps/web` is a dependency-free PWA-style browser client that no longer contains hard-coded
  users or communication state.
- The browser session lives in an httpOnly cookie the server sets on login; the page's own
  JavaScript never holds the session token, and `localStorage` is not used for auth.

## Production Target

- Web: React 18, TypeScript, Vite, Tailwind, Radix, TanStack Query, Zustand, Socket.IO client.
- API: NestJS, Prisma (PostgreSQL is already the database today; NestJS/Prisma is the still-pending
  rewrite of the API layer itself), Redis, Socket.IO Redis adapter, BullMQ.
- Files: tus resumable uploads, MinIO object storage, ClamAV scan worker, preview/text extraction,
  OpenSearch indexing, signed download URLs.
- Calls: LiveKit SFU and coturn, with API-minted room tokens.
- Auth: Keycloak realm federated to Active Directory via LDAP, OIDC Authorization Code + PKCE.
- Optional Google SSO: Google OAuth can be enabled with server environment variables, but AD
  remains the production employee authority for this intranet requirement.
- Audit: append-only JSON stream. No SIEM is required or assumed; `infra/wazuh/` documents, for
  reference, how to wire this stream into a Wazuh manager for anyone who specifically wants that.

## Module Boundaries

- `auth`: OIDC validation, AD group mapping, local user provisioning.
- `messaging`: channels, DMs, messages, threads, reactions, read state, typing, presence.
- `files`: upload sessions, quotas, MinIO keys, scan state, signed URLs.
- `calls`: room lifecycle, LiveKit tokens, huddles, screen-share permissions.
- `calendar`: events, attendees, reminders, event rooms.
- `announcements`: broadcast posts, banners, #announcements channel pinning.
- `search`: OpenSearch indexing and permission-scoped queries.
- `admin`: roles, feature flags, moderation, retention, legal hold, export.
- `audit`: immutable event writer, JSON schema usable by any log pipeline or SIEM.

## Important Hook

Call recording and live captions are intentionally left as future interfaces. The UI and API
contract keep room IDs and media permissions explicit so those features can be added later
without changing the client model.
