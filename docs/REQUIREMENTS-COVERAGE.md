# Requirements Coverage

| README Area | Current Coverage | Production Hook |
| --- | --- | --- |
| AD authentication | Dedicated admin login, member-only employee signup, session cookies, logout, audit event | Keycloak realm export and OpenAPI session contract |
| Google sign-up | UI option, real OAuth URL generation when env vars exist, local provider fallback for testing | Google OAuth must be explicitly configured; AD remains production source of truth |
| Channels, DMs, group DMs | Database-backed channels and DMs, membership and private/public channel metadata | `channels` and `messages` API paths |
| Threads | Working thread panel with replies | `parentId` message contract |
| Reactions | Working reaction add/remove with audit | reaction API paths |
| Markdown/code | Working markdown rendering and code blocks | message body contract |
| Editing/deletion | Working edit history and soft delete | message patch/delete paths |
| Presence/profile/directory | Working profile panel, presence changes, people directory | `users` and session schemas |
| Search | Working client query over database-loaded permission-scoped messages/files | `/search` OpenSearch contract |
| 15 GB files | Client-side 15 GB cap, resumable upload pause/resume simulation, scan states | tus upload contract, MinIO, ClamAV services |
| Signed downloads | Working signed URL JSON simulation and audit | `/files/{fileId}/download` |
| Voice/video/screen share | Working call room controls and local media permission hooks | `/calls/token`, LiveKit, coturn |
| Voice messages | Working recorder with waveform and fallback simulated clip | file kind + upload pipeline |
| Calendar/events | Working event list, creation, attendee responses | `/events` |
| Announcements | Working banner and admin broadcast post | `/announcements` |
| Admin/moderation | Working roles, feature flags, channel lock, export, retention/legal hold | admin module and retention contract |
| Audit logging | PostgreSQL audit log, export, stdout JSON | API stdout JSON; SIEM (Wazuh or otherwise) is optional and not built — see `infra/wazuh` for reference only |
| Responsive/PWA | Desktop three-pane, tablet slide panel, mobile tabs, manifest, service worker | React/Vite PWA migration path |
| Desktop app | Not built locally | Tauri shell remains milestone M7 |
| Docker compose | Self-hosted service skeleton plus static web and API adapter | Expand API to full NestJS service |

## Known Local Limitations

- This workspace does not currently have Node/npm/pnpm on PATH, so React/Nest/Tauri packages
  were not installed or built locally.
- Real AD, MinIO, ClamAV, OpenSearch, Redis, LiveKit, coturn, and Google OAuth behavior
  requires the Docker/self-hosted environment and service credentials. PostgreSQL is the exception —
  the local server already uses it, via `DATABASE_URL`. A SIEM (Wazuh or otherwise) is optional
  and not required at all; the app only emits a JSON audit stream any SIEM can consume.
- The local server (a single Python file, PostgreSQL-backed) is suitable for functional validation.
  Production should replace it with the NestJS API modules described in the README; the database
  itself does not need to change.
