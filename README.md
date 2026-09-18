# Beenco Connect — Internal Communication & Collaboration Portal

> A fully self-hosted, on-premise Slack/WhatsApp replacement for Beenco.

## Run locally with Docker

Requirements: Docker Desktop with Compose v2.

```powershell
docker compose -f infra/compose/docker-compose.local.yml up --build
```

Open http://localhost:4173 and sign in with the local administrator from `.env`.
The local compose file runs the PostgreSQL-backed portal and persists database data in
the `postgres-data` Docker volume and uploaded files in `portal-data`. The larger `docker-compose.yml` is an infrastructure
deployment scaffold for future Keycloak, storage, search, scanning, and media integrations.

For that full stack, load the root environment explicitly:

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml up --build
```
> Real-time messaging, voice/video/screen-share, and large-file sharing (up to 15 GB/file),
> authenticated against the company Active Directory and deployed entirely inside the
> Beenco network. No third-party SaaS. No company data ever leaves the building.

This document is the **single source of truth** for development. It is written to be consumed
directly by an automated coding agent (Codex). Build to this spec. Where a decision is left
open, the spec says so explicitly and gives a recommended default.

---

## 1. Project Overview

### 1.1 Purpose
Beenco (a web3 servicing agency) currently relies on Slack, WhatsApp, and other third-party
platforms for internal chat and file sharing. This means company files and conversations live
on external servers. **Beenco Connect** replaces all of that with a single self-hosted portal
running inside the company network, so:

- All communication and files stay on-premise, inside the company's own VLANs.
- Every employee authenticates with their existing **Active Directory** account (single set of credentials).
- Large project deliverables (web3 builds, design assets, video) can be shared internally up to **15 GB per file**.
- Every security-relevant action is captured in an append-only audit log.

### 1.2 Non-Negotiable Requirements (from stakeholder)
1. **Self-hosted only.** No external SaaS dependency for chat, files, or calls. No data leaves the network.
2. **Active Directory authentication.** All employees log in with AD credentials.
3. **15 GB maximum file size** per upload, with resumable uploads (a dropped Wi-Fi connection must not restart the upload).
4. **Responsive design**, usable on desktop browsers and mobile phone browsers.
5. **Black & green theme** matching the Beenco logo (see §10 Design System).
6. **Voice + video + screen share** (full real-time calling).
7. **Web app + desktop app now; native mobile app later.** Architecture must make adding mobile straightforward.
8. **Audit logging.** (No SIEM integration is required; a Wazuh integration was scoped early on
   and later dropped — see §2.8.)
9. **Voice messages** (audio clips in chat).
10. **Calendar / events & company announcements.**
11. **Admin moderation + retention controls.**
12. **Scalable and extensible** — the team expects to add more features over time; the architecture must support that without rewrites.

### 1.3 Design Principles
- **Modular / plugin-friendly.** Each feature (chat, calls, calendar, files…) is an isolated module with a clear API boundary so new features bolt on cleanly.
- **Stateless services + shared state in Redis** so the backend scales horizontally.
- **API-first.** The web app, desktop app, and future mobile app all consume the same REST + WebSocket API. No business logic in the clients.
- **Secure by default.** TLS everywhere, least-privilege RBAC, AV scanning on uploads, full audit trail.
- **Self-contained deployment.** One `docker compose up` brings the whole stack online for a single node; the same images scale to Kubernetes.

---

## 2. Feature Specification

### 2.1 Messaging (Slack-equivalent core)
- **Channels**: public and private. Members, topic/description, pinned messages, bookmarks.
- **Direct messages (DMs)** and **group DMs**.
- **Threaded replies** on any message.
- **Reactions / emoji** (standard + custom org emoji).
- **Mentions**: `@user`, `@channel`, `@here`, plus user groups (e.g. `@dev`, `@creatives` mapped from AD groups).
- **Rich text / Markdown**: bold, italics, lists, blockquotes, links.
- **Code snippets with syntax highlighting** (this is a dev-heavy web3 shop — first-class code blocks matter).
- **Message editing & deletion** (with edit history; hard vs soft delete governed by retention policy).
- **Typing indicators, presence (online/away/offline/DND), read state, unread counters.**
- **Inline link previews** (fetched server-side, never by exposing the client; respects an allowlist to avoid SSRF).
- **Drag-and-drop file attachment** directly into the composer.
- **Full-text search** across messages and file contents (see §2.6).
- **Notifications**: in-app, desktop native (desktop app), web push (browser), and email fallback for offline/mention events.

### 2.2 File Sharing (15 GB)
- **Maximum file size: 15 GB per file** (configurable cap; default 15 GB). Enforced on both client and server.
- **Resumable, chunked uploads** so a network blip doesn't restart a multi-GB transfer (TUS protocol — see §6).
- **Object storage**: files live in self-hosted MinIO (S3-compatible), never on the app server's local disk.
- **Antivirus scan** (ClamAV) on every upload before the file is made available; infected files are quarantined and the uploader + admins are alerted.
- **Previews/thumbnails** for images, PDFs, video (poster frame), and common docs.
- **Versioning optional** (recommended): re-uploading the same filename to a channel keeps history.
- **Access control**: a file inherits the permissions of the channel/DM it was shared in. Direct download links are short-lived, signed URLs.
- **Per-channel and per-user storage quotas** (admin-configurable).
- **Files browser**: a per-channel and global "Files" view with filters (type, uploader, date, size).

### 2.3 Voice / Video / Screen Share
- **1:1 and group calls** (voice and video).
- **Screen sharing** (full screen, window, or browser tab).
- **Channel "huddles"** (lightweight always-available audio rooms per channel).
- **Self-hosted media server (LiveKit SFU)** + **coturn** TURN/STUN — no third-party media relay; media stays on-prem.
- Active speaker detection, mute/unmute, camera on/off, raise hand, participant list.
- **Optional (confirm later, build the hook now):** call recording to MinIO, live captions. Leave interfaces in place; do not implement recording in v1 unless requested.

### 2.4 Voice Messages
- Record an audio clip in the composer, see a live waveform, send it as a message.
- Stored as Opus/WebM in MinIO. Server may transcode to a web-friendly format via a background job.
- Waveform + duration shown inline; playback with scrubbing and speed control.

### 2.5 Calendar / Events & Announcements
- **Company calendar**: events with title, description, start/end, location/room, attendees, optional video-call link (auto-creates a LiveKit room).
- **Announcements**: org-wide or per-group broadcast posts, pinned to a dedicated #announcements channel and shown as a banner. Only authorized roles can post.
- **Reminders/notifications** ahead of events.
- **AD-group targeting** for events and announcements (e.g. an event only for the `dev` group).

### 2.6 Search
- Full-text search across **messages, file names, and extracted file text** (OpenSearch).
- Filters: by person, channel, date range, has-file, file type.
- Respects permissions — users only get results from channels/DMs they belong to.

### 2.7 Administration, Moderation & Retention
- **Admin console** (web): user management (synced from AD), channel management, roles, quotas, feature flags.
- **Moderation**: delete any message/file, lock channels, suspend/disable users, mark content for review.
- **Retention policies**: per-channel and global rules to auto-delete messages/files older than N days; **legal hold** to exempt specified channels/users from deletion.
- **Data export** for a user/channel (compliance / offboarding).
- **Feature flags** so new modules can be toggled per environment/role.

### 2.8 Audit Logging
- **Every security-relevant action** is written to an append-only audit log: logins (success/fail), permission changes, admin actions, file downloads, message deletions, retention/legal-hold changes, call creation, etc.
- Audit events are emitted as **structured JSON** to stdout, so any log pipeline the operator already
  runs (journald, Docker logging driver, a log shipper) can pick them up.
- Audit log is immutable to application users (no edit/delete via API).
- **Not built:** SIEM integration. This was originally scoped as a Wazuh integration (custom
  decoder/rule set, an admin-facing Security portal with simulated alerts, agents, and MITRE
  triage) — that scope was dropped; the app makes no assumption about which SIEM, if any, an
  operator runs. `infra/wazuh/` documents how to wire a real Wazuh manager to the JSON audit
  stream, kept for reference for anyone who does want that specific integration.

### 2.9 User Profiles & Directory
- Profile sourced from AD: display name, email, title, department, AD groups, avatar (uploadable override).
- Custom status text + emoji, presence.
- Org directory / people search.

---

## 3. Technology Stack

> Versions below are the recommended stable baseline. Use the latest stable patch of each.
> Everything is open-source and self-hostable. No paid/SaaS dependency.

### 3.1 Summary Table
| Layer | Technology | Why |
|---|---|---|
| **Frontend (web)** | React 18 + TypeScript, Vite | Mature, huge ecosystem, fast builds |
| **UI styling** | Tailwind CSS + custom design tokens | Fast, consistent, easy theming for black/green |
| **Component primitives** | Radix UI (headless) + custom components | Accessible, unstyled — we own the look |
| **Client state / data** | TanStack Query (server cache) + Zustand (UI state) | Simple, scalable, no boilerplate |
| **Realtime client** | Socket.IO client (WebSocket) | Reconnection, rooms, fallback handled |
| **Rich text editor** | Lexical (or TipTap/ProseMirror) | Extensible composer, Markdown + mentions + code |
| **Desktop app** | Tauri (Rust shell wrapping the web app) | Lightweight, secure, smaller than Electron; native notifications, auto-update |
| **Backend** | Node.js 20 LTS + TypeScript + NestJS | Modular architecture = easy to add features; DI, guards, gateways |
| **Realtime server** | Socket.IO with the Redis adapter | Horizontal scaling of WebSockets |
| **Auth / Identity** | Keycloak (federates Active Directory via LDAP) → OIDC to the app | SSO, MFA, group mapping, future-proof |
| **Primary database** | PostgreSQL 16 | Relational integrity for users/channels/messages |
| **ORM** | Prisma (or TypeORM) | Type-safe schema + migrations |
| **Cache / pub-sub / presence** | Redis 7 | Sessions, presence, Socket.IO adapter, rate limits |
| **Search** | OpenSearch | Full-text search; open-source ES fork |
| **Object storage** | MinIO (S3-compatible) | Self-hosted blob store for files/media |
| **Resumable uploads** | tus protocol (`tus-node-server` + `tus-js-client`) | Reliable 15 GB chunked/resumable uploads |
| **Antivirus** | ClamAV (clamd) | Scan all uploads |
| **Real-time media (calls)** | LiveKit (self-hosted SFU) | Production WebRTC, voice/video/screenshare/huddles |
| **TURN/STUN** | coturn | NAT traversal across VLANs, fully on-prem |
| **Background jobs / queue** | BullMQ (Redis-backed) | AV scans, transcoding, notifications, search indexing |
| **Reverse proxy / TLS** | Traefik (or Nginx) | Routing, TLS termination, WebSocket upgrade |
| **Internal CA / certs** | step-ca (smallstep) or internal AD CA | TLS for internal hostnames |
| **Email (notifications)** | SMTP relay (internal mail server) | Offline/mention email fallback |
| **Containerization** | Docker + Docker Compose; Kubernetes-ready | Single-node now, scale-out later |
| **Observability** | Prometheus + Grafana + Loki | Metrics, dashboards, logs |
| **CI** | GitHub Actions (or self-hosted runner) | Lint/test/build/image push |

### 3.2 Why these choices map to the requirements
- **NestJS modular structure** directly serves "scalable + we'll add more features": each feature is a Nest module with its own controller/service/gateway.
- **Keycloak in front of AD** gives AD login today, and SSO/MFA/SAML/OIDC for any future tools — without re-plumbing auth.
- **tus + MinIO** is the combination that makes 15 GB resumable uploads actually reliable.
- **Tauri** keeps the desktop app tiny and lets the *same React codebase* serve web + desktop; a future React Native / Capacitor mobile app reuses the same API.

---

## 4. System Architecture

### 4.1 Component Diagram (logical)
```
                         ┌─────────────────────────────────────────────┐
   Employee VLANs        │                Beenco Network                │
   (110–160) + Admin     │                                             │
        │                │   ┌────────────┐      ┌──────────────────┐  │
        ▼                │   │  Traefik   │      │   Keycloak       │  │
  ┌───────────┐  HTTPS   │   │ (reverse   │◄────►│  (OIDC) ──LDAP──►│──┼──► Active Directory
  │  Web app  │─────────►│   │  proxy,TLS)│      └──────────────────┘  │     (Domain Controller)
  │ (browser) │  WSS     │   └─────┬──────┘                            │
  └───────────┘          │         │                                   │
  ┌───────────┐          │   ┌─────▼───────────────────────────────┐   │
  │ Desktop   │─────────►│   │     NestJS API + Socket.IO gateway   │   │
  │ (Tauri)   │          │   │  (stateless, horizontally scalable)  │   │
  └───────────┘          │   └──┬───────┬───────┬───────┬───────┬───┘   │
                         │      │       │       │       │       │       │
                         │  ┌───▼──┐ ┌──▼───┐ ┌─▼────┐ ┌▼─────┐ ┌▼────┐ │
                         │  │Postgr│ │Redis │ │OpenS.│ │MinIO │ │Bull │ │
                         │  │  es  │ │presence│ │search│ │ +AV │ │ MQ │ │
                         │  └──────┘ └──────┘ └──────┘ └──────┘ └─────┘ │
                         │                                              │
                         │   ┌──────────────┐     ┌─────────────────┐  │
   Media (WebRTC) ◄──────┼──►│  LiveKit SFU │◄───►│     coturn      │  │
   UDP/TCP               │   └──────────────┘     │  (TURN/STUN)    │  │
                         │                         └─────────────────┘  │
                         │   Audit JSON logs ─► stdout / operator's log pipeline │
                         └─────────────────────────────────────────────┘
```

### 4.2 Request/Realtime flow
- **Auth**: Client → Keycloak (OIDC Authorization Code + PKCE) → receives access/refresh tokens. Keycloak federates the user from AD. API validates the JWT on every request and on WebSocket connect.
- **Messaging**: Client opens a WebSocket (Socket.IO) to the API gateway. Messages are persisted to Postgres, fanned out via the Redis adapter to all connected members, and queued for search indexing (BullMQ → OpenSearch).
- **Files**: Client uploads via tus to the upload service → stored in MinIO → BullMQ job runs ClamAV scan + thumbnailing + text extraction for search → message gets the file reference. Downloads use short-lived signed URLs.
- **Calls**: API issues a LiveKit join token (scoped to a room + permissions). Client connects directly to LiveKit; media relayed by the SFU, traversing NAT/VLAN via coturn.

### 4.3 Monorepo layout
```
beenco-connect/
├── apps/
│   ├── web/                 # React + Vite web client
│   ├── desktop/             # Tauri shell loading the web build
│   └── api/                 # NestJS backend (REST + Socket.IO + tus)
├── packages/
│   ├── ui/                  # Shared React component library + design tokens
│   ├── types/               # Shared TypeScript types / API contracts (OpenAPI-derived)
│   └── config/              # Shared lint/tsconfig/tailwind presets
├── infra/
│   ├── docker/              # Dockerfiles
│   ├── compose/             # docker-compose.yml + env templates
│   ├── k8s/                 # Helm charts / manifests (scale-out)
│   ├── livekit/             # LiveKit + coturn config
│   ├── keycloak/            # Realm export, AD federation config
│   ├── wazuh/               # Optional, not built by default — see §2.8/§11.4
│   └── traefik/             # Reverse proxy + TLS config
├── docs/
└── README.md
```
> Use a monorepo tool (pnpm workspaces + Turborepo) to share `packages/` across `apps/`.

---

## 5. Authentication & Authorization

### 5.1 Active Directory integration
- **Keycloak** is the identity provider. Configure **LDAP/AD User Federation** in a Keycloak realm (`beenco`) pointing at the Domain Controller.
- Sync AD users and **AD groups → Keycloak groups → app roles** (e.g. AD `Domain Admins`/IT → `admin`; `dev`, `creatives`, `ab` groups → matching user groups for `@mentions` and channel auto-membership).
- App uses **OIDC Authorization Code flow with PKCE**. Store tokens in memory + httpOnly refresh cookie (web); secure store (desktop).
- **MFA** available via Keycloak (TOTP/WebAuthn) — enabled for admin roles at minimum.
- On first login a local user record is provisioned/linked by the AD `objectGUID`/`sub`.

### 5.2 Authorization model (RBAC)
Roles (extensible): `super_admin`, `admin`, `moderator`, `member`, `guest`.
- Enforced with **NestJS guards** on REST routes and Socket.IO events.
- Channel-level permissions (owner/member) layered on top of org roles.
- File/download access derived from channel membership + signed URLs.

### 5.3 Sessions & tokens
- Short-lived access JWT (e.g. 5–15 min) + refresh via Keycloak.
- WebSocket auth: token passed on connect and re-validated on refresh; server disconnects on expiry/revocation.

---

## 6. File Subsystem (15 GB resumable) — Detail

- **Protocol**: tus (`tus-js-client` on the client, `tus-node-server` integrated into the API or a dedicated upload service).
- **Flow**:
  1. Client creates a tus upload (declares filename, size, mime, target channel/DM). Server rejects if size > cap (15 GB) or quota exceeded.
  2. Client uploads in chunks (e.g. 50–100 MB) with automatic resume on disconnect.
  3. On completion, the object is moved to MinIO; a BullMQ job runs: **ClamAV scan → thumbnail/poster → text extraction (Apache Tika or equivalent) → OpenSearch index**.
  4. Until the scan passes, the file is `pending`; on pass it becomes `available` and the chat message is delivered; on fail it is `quarantined` and admins alerted.
- **Downloads**: short-lived presigned MinIO URLs (e.g. 5 min), logged in the audit trail.
- **Limits**: configurable `MAX_FILE_SIZE` (default `15GB`), per-user and per-channel quotas.
- **Storage layout**: `s3://beenco-files/{channelId}/{fileId}/{originalName}`; metadata in Postgres.

---

## 7. Realtime Messaging — Detail

### 7.1 WebSocket events (Socket.IO namespaces/events)
| Event | Direction | Payload (summary) |
|---|---|---|
| `message:send` | C→S | channelId, body, attachments[], parentId? |
| `message:new` | S→C | full message object |
| `message:edit` / `message:delete` | C↔S | messageId, body? |
| `reaction:add` / `reaction:remove` | C↔S | messageId, emoji |
| `typing:start` / `typing:stop` | C↔S | channelId, userId |
| `presence:update` | S→C | userId, status |
| `read:update` | C→S | channelId, lastReadMessageId |
| `channel:joined` / `channel:left` | S→C | channelId, userId |
| `call:invite` / `call:ended` | S→C | roomId, channelId |
| `notification:new` | S→C | notification object |
> Scale with the **Socket.IO Redis adapter** so any API instance can deliver to any connected client.

---

## 8. Voice/Video — Detail (LiveKit + coturn)
- Deploy **LiveKit server** self-hosted. API mints **JonWebToken room tokens** scoped to room + capabilities (publish/subscribe/screenshare).
- Deploy **coturn** for TURN/STUN so clients on different VLANs can connect.
- Rooms map to: a DM, a group call, a channel huddle, or a calendar event.
- Client uses `livekit-client` SDK in React; reuse the same components in desktop (Tauri) and future mobile.
- **Ports** (open in firewall — see §11): LiveKit `7880/tcp` (signaling), `7881/tcp` (TCP fallback), `50000–60000/udp` (RTP); coturn `3478/udp+tcp`, `5349/tcp` (TLS), relay UDP range.

---

## 9. Data Model (core tables)
> Prisma schema; only the essential tables/columns shown. Add indexes on FKs and search/sort columns.

- **users**: id, ad_object_guid, username, display_name, email, title, department, avatar_url, status_text, status_emoji, presence, role, is_active, created_at.
- **groups**: id, name, ad_group_dn, type (ad_synced|custom).
- **user_groups**: user_id, group_id.
- **channels**: id, name, slug, type (public|private|dm|group_dm), topic, description, created_by, is_archived, retention_days (nullable), legal_hold (bool), created_at.
- **channel_members**: channel_id, user_id, role (owner|member), last_read_message_id, muted, joined_at.
- **messages**: id, channel_id, author_id, parent_id (nullable, threads), body (rich/markdown), edited_at, deleted_at (soft delete), created_at.
- **message_attachments**: id, message_id, file_id.
- **files**: id, channel_id, uploader_id, original_name, mime, size_bytes, storage_key, status (pending|available|quarantined), checksum, version, created_at.
- **reactions**: message_id, user_id, emoji.
- **events** (calendar): id, title, description, starts_at, ends_at, location, livekit_room, created_by, target_group_id (nullable).
- **event_attendees**: event_id, user_id, response.
- **announcements**: id, title, body, author_id, target_group_id (nullable), pinned_until, created_at.
- **notifications**: id, user_id, type, payload (jsonb), read_at, created_at.
- **audit_log**: id, actor_id, action, target_type, target_id, ip, user_agent, metadata (jsonb), created_at  *(append-only)*.
- **retention_jobs / legal_holds**: as needed to drive the retention engine.

---

## 10. Design System (Black & Green — Beenco brand)

### 10.1 Brand colors
Beenco logo = black background, bright lime/chartreuse signature. Use this palette (design tokens):

```
/* Core brand */
--beenco-lime:        #C4F82A;   /* primary accent — the logo green */
--beenco-lime-bright: #D6FF4D;   /* hover / highlight */
--beenco-lime-dim:    #9CCB1F;   /* pressed / muted accent */
--beenco-lime-soft:   rgba(196,248,42,0.12); /* tint backgrounds, mention bg */

/* Neutrals (dark-first) */
--bg-base:    #0A0A0A;   /* app background (near-black, matches logo) */
--bg-surface: #141414;   /* cards, sidebars */
--bg-elevated:#1E1E1E;   /* modals, popovers, hovered rows */
--border:     #2A2A2A;
--text-primary:   #F5F5F5;
--text-secondary: #A3A3A3;
--text-muted:     #6B6B6B;

/* Semantic */
--success: #C4F82A;  /* reuse lime for positive/online */
--warning: #FFC857;
--danger:  #FF5C5C;
--info:    #5CC8FF;
--online:  #C4F82A;  --away: #FFC857;  --dnd: #FF5C5C;  --offline:#6B6B6B;
```
- **Primary buttons / active states / links / unread badges / mentions / online dots** use `--beenco-lime`.
- **Lime on black**: ensure text *on* lime uses near-black (`#0A0A0A`) for contrast/legibility.
- Maintain WCAG AA contrast. (A light/high-contrast theme is a later option; ship dark brand theme first.)

### 10.2 Typography
- UI font: **Inter** (or system stack). Monospace for code: **JetBrains Mono**.
- Type scale: 12 / 14 / 16 / 20 / 24 / 32. Base body 14–15px.

### 10.3 Layout & responsiveness
- **Three-pane desktop layout**: left nav (workspaces/channels/DMs) · center (message list + composer) · right (thread/details/call panel).
- **Tablet**: collapsible left nav.
- **Mobile browser**: single-pane with bottom tab bar (Channels · DMs · Calls · Activity · You); swipe/back navigation; composer pinned to bottom; safe-area aware.
- Breakpoints: `sm 640 / md 768 / lg 1024 / xl 1280`.
- **PWA**: installable, offline shell, push notifications — makes the mobile-browser experience app-like before native mobile ships.
- Accessibility: keyboard navigation, ARIA, focus rings (in lime), prefers-reduced-motion.

### 10.4 Component inventory (build in `packages/ui`)
Sidebar, channel list item (with unread/mention states), message row (avatar, name, time, body, reactions, thread indicator), composer (rich text + attach + voice record + emoji), thread panel, file card, image/video viewer, call window + controls, presence dot, avatar, modal, toast, search bar + results, admin tables, calendar/agenda view, announcement banner.

---

## 11. Network & Deployment Requirements (Beenco-specific)

> The existing MikroTik config (per the network deployment docs) **blocks inter-VLAN traffic by default**
> (`drop src 10.0.0.0/8 dst 10.0.0.0/8`). The portal must be reachable by **all employee VLANs (110–160)**,
> so explicit firewall allow rules are required. Recommended: place the portal servers in a dedicated
> **server subnet/VLAN** (e.g. a new VLAN or the management range) and allow the needed ports inbound from each user VLAN.

### 11.1 Ports to open (from user VLANs → portal server)
| Service | Port(s) | Proto | Notes |
|---|---|---|---|
| Web/API (HTTPS) | 443 | TCP | Traefik; includes WSS upgrade |
| HTTP redirect | 80 | TCP | redirect to 443 only |
| Keycloak | via 443 | TCP | behind Traefik (e.g. `auth.beenco.local`) |
| LiveKit signaling | 7880 | TCP | behind Traefik or direct |
| LiveKit TCP fallback | 7881 | TCP | |
| LiveKit RTP | 50000–60000 | UDP | media |
| coturn | 3478 | UDP/TCP | STUN/TURN |
| coturn TLS | 5349 | TCP | TURNS |
| coturn relay | (range) | UDP | configurable relay range |

### 11.2 DNS / hostnames (internal)
- `connect.beenco.local` → web/API, `auth.beenco.local` → Keycloak, `media.beenco.local` → LiveKit, `files.beenco.local` → MinIO (or behind API). Use the internal DNS / AD DNS.

### 11.3 TLS
- Use the internal CA (AD Certificate Services or step-ca) to issue certs for the `*.beenco.local` hosts; distribute the root CA to client machines (GPO).

### 11.4 SIEM integration (optional, not built)
No SIEM is wired up by default; the audit trail is just a JSON stream on stdout that any log
pipeline can consume. `infra/wazuh/` documents, for reference, how an operator who specifically
wants Wazuh could install the Wazuh agent on the portal host(s) pointed at that JSON stream, and
ship the decoders/rules there so events become Wazuh alerts.

---

## 12. Deployment

### 12.1 Single-node (Docker Compose) — default
`infra/compose/docker-compose.yml` brings up: traefik, api, web (static via traefik), keycloak, postgres, redis, opensearch, minio, clamav, livekit, coturn, (prometheus/grafana/loki optional).
- All config via `.env` (see §13). One command: `docker compose up -d`.
- Persistent volumes for postgres, minio, opensearch, redis (AOF).

### 12.2 Scale-out (Kubernetes) — when needed
- Stateless `api`/`web` as Deployments with HPA; Redis + Socket.IO adapter handle multi-instance realtime.
- Postgres (operator or managed-on-prem), OpenSearch cluster, MinIO distributed mode, LiveKit horizontal scaling.
- Provide Helm charts in `infra/k8s/`.

### 12.3 Backups & DR
- Nightly `pg_dump` + WAL archiving; MinIO bucket replication/snapshot; OpenSearch snapshots to MinIO; Keycloak realm export.
- Document restore procedure in `docs/`. Test restores.

---

## 13. Configuration (environment variables)
Provide `.env.example`. Key vars:
```
# Core
NODE_ENV, APP_BASE_URL=https://connect.beenco.local
# Auth (Keycloak/OIDC)
OIDC_ISSUER=https://auth.beenco.local/realms/beenco
OIDC_CLIENT_ID, OIDC_CLIENT_SECRET
# AD federation is configured inside Keycloak (LDAP_URL, BIND_DN, etc.)
# Database
DATABASE_URL=postgres://...:5432/beenco
# Redis
REDIS_URL=redis://...:6379
# Search
OPENSEARCH_URL, OPENSEARCH_USER, OPENSEARCH_PASS
# Object storage
S3_ENDPOINT=https://files.beenco.local, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET=beenco-files
MAX_FILE_SIZE=15GB
# Antivirus
CLAMAV_HOST, CLAMAV_PORT=3310
# Media
LIVEKIT_URL=wss://media.beenco.local, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
TURN_URL, TURN_SECRET
# Mail
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM
# Audit / SIEM
AUDIT_LOG_PATH=/var/log/beenco/audit.json
# Push
VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY
```

---

## 14. Security Hardening (build-in, not optional)
- TLS everywhere (internal CA); HSTS; secure/httpOnly cookies.
- All input validated (class-validator); output encoded; CSP headers.
- **SSRF protection** on link-preview/unfurl fetcher (domain allowlist, no internal IP ranges).
- Rate limiting (Redis) on auth, uploads, messages.
- AV scan all uploads; reject executables by policy if desired.
- RBAC enforced server-side on every route + socket event (never trust the client).
- Signed, short-lived download URLs; no public buckets.
- Secrets in a secret store / Docker secrets, never in the repo.
- Full audit trail (§2.8), emitted as JSON for any SIEM the operator chooses to point at it.
- Dependency scanning in CI; pinned base images; non-root containers.

---

## 15. Build Order / Milestones (for the coding agent)
1. **M0 — Scaffold**: monorepo (pnpm + Turborepo), CI, lint/format, `packages/ui` design tokens (black/green), Docker Compose skeleton.
2. **M1 — Auth**: Keycloak realm + AD federation, OIDC login on web, user provisioning, RBAC guards, base shell layout.
3. **M2 — Messaging core**: channels, DMs, messages, Socket.IO + Redis adapter, threads, reactions, presence, typing, read state, search indexing pipeline.
4. **M3 — Files (15 GB)**: tus resumable upload, MinIO, ClamAV scan job, previews, text extraction, OpenSearch search UI, signed downloads, quotas.
5. **M4 — Calls**: LiveKit + coturn, 1:1/group/huddle, screen share, call UI; voice messages.
6. **M5 — Calendar/Announcements + Notifications**: events, announcements banner, in-app/web-push/desktop/email notifications.
7. **M6 — Admin/Moderation/Retention** + **Audit log**.
8. **M7 — Desktop app (Tauri)**: package the web build, native notifications, auto-update.
9. **M8 — Hardening, observability, backups, docs, load testing.**
10. **Later — Native mobile** (React Native or Capacitor) reusing the same API/design tokens.

---

## 16. Acceptance Criteria (Definition of Done)
- An AD user logs in with their domain credentials; no separate signup.
- Public/private channels, DMs, threads, reactions, presence, typing, search all work in real time across multiple browser tabs/instances.
- A **15 GB file** uploads successfully, survives a mid-upload network drop (resume), is AV-scanned, previewable, searchable by name and content, and downloadable via a short-lived link.
- Two users on **different VLANs** can hold a video call with screen share through the self-hosted LiveKit + coturn.
- Voice messages record, send, and play with a waveform.
- Calendar events + announcements deliver with notifications.
- Admins can moderate content and configure retention; legal hold blocks deletion.
- Every security-relevant action appears in the audit log and in the stdout JSON stream.
- UI is fully responsive (desktop, tablet, mobile browser) in the black/green brand theme, installable as a PWA.
- Desktop (Tauri) app installs and runs with native notifications.
- `docker compose up` brings the whole stack online from `.env`.

---

## 17. Open / Future (do NOT build in v1 unless asked — leave hooks)
- Call recording & live captions/transcription.
- Native mobile apps.
- Custom emoji management UI, scheduled messages, message bookmarks/saved items, slash commands & bots/webhooks (internal automations), per-team workspaces if Beenco grows multi-team.
- Light/high-contrast theme.

> When new features are requested, add them as new NestJS modules + UI feature folders + (if needed) tables and OpenSearch mappings. The architecture is intentionally modular to absorb them without rewrites.

---

## 18. Notes for the Coding Agent (Codex)
- Treat this README as the contract. Generate an **OpenAPI spec** and derive shared types in `packages/types`.
- Write services test-first where practical; include integration tests for auth, upload, and realtime.
- Keep **all business logic in the API**; clients are thin.
- Never introduce a third-party SaaS dependency — everything self-hosted.
- Use the exact design tokens in §10. Match the Beenco logo aesthetic: confident, minimal, black canvas with lime accents.
- If a requirement is ambiguous, prefer the documented recommended default and leave a clear `TODO(confirm):` comment rather than blocking.
