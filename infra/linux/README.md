<!-- TODO(confirm): DEPLOY-INSTRUCTIONS-FOR-CODEX.md says "seven new files" in prose, but section 1 lists six concrete file paths. This change implements only the six concrete paths listed in section 1. -->

This folder contains Linux deployment artifacts for the Beenco Ubuntu services host. The canonical runbook is `docs/DEPLOYMENT-LINUX.md`, which describes how these files fit together with Nginx, systemd, the operator-supplied certificate, and the existing Python portal.

None of these files run automatically. The operator invokes `generate-cert.sh` and `install.sh` manually, reviews the output, and then starts the systemd and Nginx services explicitly.
