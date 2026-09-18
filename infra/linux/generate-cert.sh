#!/usr/bin/env bash
set -euo pipefail

CERT_DIR="/etc/beenco/certs"
CERT_FILE="${CERT_DIR}/portal.crt"
KEY_FILE="${CERT_DIR}/portal.key"

if [[ "${EUID}" -ne 0 ]]; then
  echo "generate-cert.sh must be run as root. Use: sudo infra/linux/generate-cert.sh" >&2
  exit 1
fi

if [[ -f "${CERT_FILE}" ]]; then
  echo "${CERT_FILE} already exists. Move the old certificate aside before generating a new one." >&2
  exit 1
fi

if [[ -f "${KEY_FILE}" ]]; then
  echo "${KEY_FILE} already exists. Move the old key aside before generating a new certificate." >&2
  exit 1
fi

# TODO(confirm): The runbook allows this helper before install.sh, but ownership requires
# group beenco. This creates only the system group when missing; install.sh creates the user.
if ! getent group beenco >/dev/null; then
  groupadd --system beenco
fi

install -d -o root -g beenco -m 0750 "${CERT_DIR}"

openssl req -x509 -nodes -days 825 \
  -newkey rsa:2048 \
  -keyout "${KEY_FILE}" \
  -out "${CERT_FILE}" \
  -subj "/CN=connect.beenco.local/O=Beenco/C=PK" \
  -addext "subjectAltName=DNS:connect.beenco.local,IP:10.170.0.10"

chmod 0644 "${CERT_FILE}"
chmod 0640 "${KEY_FILE}"
chown root:beenco "${KEY_FILE}" "${CERT_FILE}"

cat <<MSG

Generated a self-signed Beenco Connect certificate at:
  ${CERT_FILE}
  ${KEY_FILE}

This is a stopgap until Samba AD CS or Windows AD CS is online. Distribute the certificate
to client trust stores before browser testing. See docs/DEPLOYMENT-LINUX.md section
"Step 4 - Trust the certificate on clients".
MSG
