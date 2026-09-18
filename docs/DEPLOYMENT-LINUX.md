# Beenco Connect Linux Deployment

## Overview

This runbook deploys Beenco Connect on the Ubuntu services host at `10.170.0.10`. Nginx terminates TLS on the host and proxies requests to the existing Python portal on `127.0.0.1:4173`. The Python app remains unchanged and does not listen directly on the LAN in this deployment. The MikroTik firewall and the host firewall are the perimeter for employee VLAN access. The portal is not exposed to the internet and must never be.

## Prerequisites

- Ubuntu 22.04 or 24.04 host at `10.170.0.10/24`, gateway `10.170.0.1`, DNS `10.170.0.1, 1.1.1.1`.
- MikroTik DNS static entry `connect.beenco.local -> 10.170.0.10` exists.
- MikroTik filter rule allowing `10.0.0.0/8 -> 10.170.0.10:443` exists and is above the `block AP2 inter-vlan` drop.
- Repo is cloned at `/opt/beenco-connect`.
- A PostgreSQL 16 instance is reachable from this host (local or remote) and `/opt/beenco-connect/.env`
  has `DATABASE_URL` pointing at it, e.g. `postgresql://beenco:<password>@<host>:5432/beenco`. The
  app has no SQLite fallback — it will not start without a reachable database.

## Step 1 - Host firewall (UFW)

If the host firewall is managed locally, allow SSH, HTTP, and HTTPS, then enable UFW:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

If the operator manages firewall policy only through the MikroTik perimeter, this step may be skipped. The install script installs `ufw` but does not enable it.

## Step 2 - TLS certificate

Path A, preferred when AD CS exists: place the AD CS-issued certificate and private key at these paths:

```text
/etc/beenco/certs/portal.crt
/etc/beenco/certs/portal.key
```

The certificate must include SAN values `DNS:connect.beenco.local` and `IP:10.170.0.10`. Set ownership and permissions:

```bash
sudo install -d -o root -g beenco -m 0750 /etc/beenco/certs
sudo chown root:beenco /etc/beenco/certs/portal.crt /etc/beenco/certs/portal.key
sudo chmod 0644 /etc/beenco/certs/portal.crt
sudo chmod 0640 /etc/beenco/certs/portal.key
```

Path B, stopgap self-signed certificate:

```bash
cd /opt/beenco-connect
sudo infra/linux/generate-cert.sh
```

This helper is manual and refuses to overwrite an existing certificate.

## Step 3 - Run the install script

Run the installer from the cloned repo:

```bash
cd /opt/beenco-connect
sudo infra/linux/install.sh
```

After reviewing the installer output, start services explicitly:

```bash
sudo systemctl start beenco-portal
sudo systemctl start nginx
```

Verify service status and the local health endpoint:

```bash
systemctl status beenco-portal nginx
curl -k https://localhost/api/health
```

Expected result: `beenco-portal` is active and bound to `127.0.0.1:4173`, Nginx is active on `80` and `443`, and the health endpoint returns a JSON object.

## Step 4 - Trust the certificate on clients

### Windows machines

Run PowerShell as Administrator:

```powershell
Import-Certificate -FilePath C:\Path\To\portal.crt -CertStoreLocation Cert:\LocalMachine\Root
```

Restart the browser before testing.

### Linux machines

Copy the certificate into the local CA store and update certificates:

```bash
sudo cp portal.crt /usr/local/share/ca-certificates/beenco-portal.crt
sudo update-ca-certificates
```

Restart the browser before testing.

### macOS, iOS, and Android

For macOS, import `portal.crt` into Keychain Access and mark it trusted for SSL. For iOS and Android, install the certificate through the device management path used by Beenco or the platform certificate settings. Once Samba AD is online and the root certificate is pushed via GPO, this manual step goes away.

## Step 5 - Verify end-to-end

From a machine on `10.110.0.0/24`, verify name resolution and TCP reachability:

```powershell
nslookup connect.beenco.local
Test-NetConnection connect.beenco.local -Port 443
```

Expected result: `nslookup` resolves `connect.beenco.local` to `10.170.0.10`, and `Test-NetConnection` reports `TcpTestSucceeded: True`. Then open `https://connect.beenco.local` in a browser. Expected result: the portal loads without a browser certificate warning after the certificate is trusted, and login works.

## Operations

### Viewing logs

```bash
journalctl -u beenco-portal -f
tail -f /var/log/beenco/audit.json
```

### Restarting cleanly

```bash
sudo systemctl restart beenco-portal
```

Nginx does not need a restart for portal code changes; the portal service does.

### Updating the portal code

```bash
cd /opt/beenco-connect
git pull
sudo systemctl restart beenco-portal
```

The database schema is managed by the app itself on startup (`init_db()` in `portal_server.py`) —
do not run any migration scripts by hand.

### Backing up

The app is PostgreSQL-backed. Uploaded files live under `/var/lib/beenco` (`PORTAL_DATA_DIR`), but
the actual data — users, messages, channels, everything else — lives in whichever PostgreSQL
instance `DATABASE_URL` in `.env` points to. **Archiving `/var/lib/beenco` alone backs up the
uploads, not the database.** Back up both:

```bash
# Database: adjust host/user/db to match DATABASE_URL in /opt/beenco-connect/.env
sudo -u beenco pg_dump "$DATABASE_URL" | gzip > /var/backups/beenco-db-$(date +%F).sql.gz

# Uploaded files
sudo tar -czf /var/backups/beenco-uploads-$(date +%F).tar.gz /var/lib/beenco
```

A daily cron entry is appropriate, but this runbook does not install one. If PostgreSQL runs on
its own host (recommended over colocating it here), that host needs its own backup schedule
instead — the `pg_dump` command above only works when this host can reach it directly.

## Security notes

- The portal must never be on a public IP or behind a public DNS record. There is no public domain involved.
- WAN inbound to `443` and `80` must remain blocked on the MikroTik; nothing here opens it.
- For remote-employee access, route users through the existing WireGuard VPN. Do not expose the portal directly.
- Admin Winbox access on the MikroTik should remain restricted to OOB and admin VLANs. This is a pre-existing concern, not a new change from this deployment.

## Rollback

If a release breaks, stop the portal, check out the previous known-good version, and start the portal again:

```bash
sudo systemctl stop beenco-portal
cd /opt/beenco-connect
git checkout <previous-tag>
sudo systemctl start beenco-portal
```

Rolling back the code does not touch the PostgreSQL database or the uploaded files under
`/var/lib/beenco` — neither is under the git directory. If the rollback needs to undo a schema
change too, restore the database from the backup taken before the release, using the `pg_dump`
above (there is no automatic down-migration).
