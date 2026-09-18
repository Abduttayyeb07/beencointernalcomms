#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/opt/beenco-connect"
CERT_DIR="/etc/beenco/certs"
CERT_FILE="${CERT_DIR}/portal.crt"
KEY_FILE="${CERT_DIR}/portal.key"
NGINX_AVAILABLE="/etc/nginx/sites-available/connect.beenco.local.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/connect.beenco.local.conf"
UNIT_FILE="/etc/systemd/system/beenco-portal.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "install.sh must be run as root. Use: sudo infra/linux/install.sh" >&2
  exit 1
fi

if [[ ! -d "${REPO_DIR}" || ! -f "${REPO_DIR}/apps/api/portal_server.py" ]]; then
  echo "Expected repo at ${REPO_DIR} with apps/api/portal_server.py. Clone the repo there first." >&2
  exit 1
fi

apt update
apt install -y nginx python3 python3-venv openssl ufw ca-certificates

if ! getent group beenco >/dev/null; then
  groupadd --system beenco
fi

if ! id -u beenco >/dev/null 2>&1; then
  useradd --system --gid beenco --home "${REPO_DIR}" --shell /usr/sbin/nologin beenco
fi

install -d -o beenco -g beenco -m 0750 /var/lib/beenco
install -d -o beenco -g beenco -m 0750 /var/log/beenco
install -d -o root -g beenco -m 0750 "${CERT_DIR}"

# The app is PostgreSQL-backed (apps/api/portal_server.py connects via DATABASE_URL, loaded from
# .env at the repo root) — it does not use a local SQLite file, so there is nothing to copy here.
# This venv is what the systemd unit actually runs, since the portal's dependencies (psycopg,
# python-dotenv) are not available in the system Python.
VENV_DIR="${REPO_DIR}/.venv"
if [[ ! -x "${VENV_DIR}/bin/python3" ]]; then
  python3 -m venv "${VENV_DIR}"
fi
"${VENV_DIR}/bin/pip" install --upgrade pip --quiet
"${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/apps/api/requirements.txt" --quiet
chown -R beenco:beenco "${VENV_DIR}"

if [[ ! -f "${REPO_DIR}/.env" ]]; then
  echo "Warning: ${REPO_DIR}/.env does not exist. Copy .env.example to .env and set at least" >&2
  echo "DATABASE_URL to a reachable PostgreSQL instance before starting beenco-portal." >&2
elif ! grep -q '^DATABASE_URL=' "${REPO_DIR}/.env"; then
  echo "Warning: DATABASE_URL is not set in ${REPO_DIR}/.env. The portal will fall back to" >&2
  echo "postgresql://beenco:beenco@localhost:5432/beenco, which is almost certainly wrong here." >&2
fi

if [[ ! -f "${CERT_FILE}" || ! -f "${KEY_FILE}" ]]; then
  cat >&2 <<MSG
Missing TLS certificate files:
  ${CERT_FILE}
  ${KEY_FILE}

Run sudo ${REPO_DIR}/infra/linux/generate-cert.sh first, or place an AD CS-issued certificate
and key at those paths with ownership root:beenco before re-running install.sh.
MSG
  exit 1
fi

cp "${REPO_DIR}/infra/nginx/connect.beenco.local.conf" "${NGINX_AVAILABLE}"
ln -sfn "${NGINX_AVAILABLE}" "${NGINX_ENABLED}"
rm -f /etc/nginx/sites-enabled/default

cp "${REPO_DIR}/infra/systemd/beenco-portal.service" "${UNIT_FILE}"
systemctl daemon-reload
systemctl enable beenco-portal
systemctl enable nginx

if ! nginx -t; then
  echo "Nginx configuration validation failed. Fix the error above before starting services." >&2
  exit 1
fi

cat <<MSG

Install checks completed. Review the output above, then start services manually:

  systemctl start beenco-portal
  systemctl start nginx

Verify:

  systemctl status beenco-portal nginx
  curl -k https://localhost/api/health

install.sh did not start or restart any service automatically.
MSG
