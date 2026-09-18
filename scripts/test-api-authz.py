"""Integration tests for authorization and input-validation rules on the portal API:
  - role hierarchy: moderator/IT admins cannot promote themselves or others to admin, cannot reset
    or deactivate an admin/super admin, and only super admins manage super admins
  - self-service password change needs the current password and returns exactly one response
  - file scan/attach/thread endpoints enforce channel access
  - malformed event/feature-flag/event-response input is rejected with 400/404, not 500/409
  - X-Forwarded-For is ignored unless TRUST_PROXY_HEADERS is set (login lockout cannot be dodged)

Runs against a real running portal + Postgres, like scripts/test-api-security.py, and reuses its
client. Creates only throwaway accounts and deactivates them afterwards.

Usage:
    python scripts/test-api-authz.py [base_url]
"""
import importlib.util
import os
import secrets
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("api_security", Path(__file__).with_name("test-api-security.py"))
api_security = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_security)
Client, check, signup, load_env_admin_creds = api_security.Client, api_security.check, api_security.signup, api_security.load_env_admin_creds

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4173"


def create_user_as(admin: Client, role: str) -> dict:
    email = f"apitest-{role}-{secrets.token_hex(4)}@beenco.local"
    password = f"Testpass-{secrets.token_hex(4)}!1"
    status, body, _ = admin.request("POST", "/api/users", {"email": email, "displayName": f"API Test {role}", "password": password, "role": role})
    check(f"super admin can create a {role}", status == 201, f"status={status} body={body}")
    client = Client(BASE_URL)
    status, body, _ = client.request("POST", "/api/auth/login", {"email": email, "password": password})
    check(f"{role} can log in", status == 200, f"status={status} body={body}")
    return {"id": body["user"]["id"], "email": email, "password": password, "client": client}


def main() -> None:
    admin_email, admin_password = load_env_admin_creds()
    admin = Client(BASE_URL)
    created_user_ids: list[str] = []
    created_channel_ids: list[str] = []
    try:
        print(f"Target: {BASE_URL}")
        status, body, _ = admin.request("POST", "/api/auth/login", {"email": admin_email, "password": admin_password})
        check("super admin logs in", status == 200, f"status={status} body={body}")
        super_admin_id = body["user"]["id"]

        moderator = create_user_as(admin, "moderator")
        it_admin = create_user_as(admin, "it_admin")
        plain_admin = create_user_as(admin, "admin")
        created_user_ids += [moderator["id"], it_admin["id"], plain_admin["id"]]
        mod: Client = moderator["client"]

        # --- Role hierarchy -------------------------------------------------------------------
        status, body, _ = mod.request("PATCH", f"/api/users/{moderator['id']}", {"role": "admin"})
        check("moderator cannot promote themselves to admin", status == 403, f"status={status} body={body}")
        status, body, _ = it_admin["client"].request("PATCH", f"/api/users/{moderator['id']}", {"role": "admin"})
        check("IT admin cannot promote someone to admin", status == 403, f"status={status} body={body}")
        status, body, _ = mod.request("POST", "/api/users", {"email": f"apitest-esc-{secrets.token_hex(4)}@beenco.local", "displayName": "Escalation", "password": "Escalation-1234", "role": "admin"})
        check("moderator cannot create an admin account", status == 403, f"status={status} body={body}")
        status, body, _ = mod.request("PATCH", f"/api/users/{super_admin_id}", {"newPassword": "Hijacked-Pass-123"})
        check("moderator cannot reset the super admin's password", status == 403, f"status={status} body={body}")
        status, body, _ = mod.request("PATCH", f"/api/admin/users/{plain_admin['id']}", {"isActive": False})
        check("moderator cannot deactivate an admin", status == 403, f"status={status} body={body}")
        status, body, _ = plain_admin["client"].request("PATCH", f"/api/users/{super_admin_id}", {"newPassword": "Hijacked-Pass-123"})
        check("admin cannot reset the super admin's password", status == 403, f"status={status} body={body}")
        status, body, _ = admin.request("PATCH", f"/api/users/{moderator['id']}", {"role": "member"})
        check("super admin can still change roles", status == 200, f"status={status} body={body}")
        status, body, _ = admin.request("PATCH", f"/api/users/{moderator['id']}", {"role": "moderator"})
        check("super admin can restore the moderator role", status == 200, f"status={status} body={body}")
        member = signup(Client(BASE_URL), "member")
        created_user_ids.append(member["id"])
        status, body, _ = mod.request("PATCH", f"/api/users/{member['id']}", {"newPassword": "Moderator-Reset-1"})
        check("moderator can still reset a regular member's password", status == 200, f"status={status} body={body}")

        # --- Self-service password change ---------------------------------------------------
        alice = signup(Client(BASE_URL), "alice")
        created_user_ids.append(alice["id"])
        ac: Client = alice["client"]
        status, body, _ = ac.request("PATCH", f"/api/users/{alice['id']}", {"newPassword": "Brand-New-Pass-1"})
        check("self password change without currentPassword is rejected", status == 400, f"status={status} body={body}")
        status, body, _ = ac.request("PATCH", f"/api/users/{alice['id']}", {"newPassword": "Brand-New-Pass-1", "currentPassword": "wrong-password"})
        check("self password change with a wrong currentPassword is rejected", status == 403, f"status={status} body={body}")
        status, body, _ = ac.request("PATCH", f"/api/users/{alice['id']}", {"newPassword": "Brand-New-Pass-1", "currentPassword": alice["password"]})
        check("self password change with the right currentPassword succeeds", status == 200 and body.get("ok") is True, f"status={status} body={body}")
        status, body, _ = Client(BASE_URL).request("POST", "/api/auth/login", {"email": alice["email"], "password": "Brand-New-Pass-1"})
        check("the new password works", status == 200, f"status={status} body={body}")
        status, body, _ = ac.request("GET", "/api/session")
        check("the connection still speaks valid HTTP after a password change", status == 200, f"status={status} body={body}")

        # --- File and thread access ---------------------------------------------------------
        bob = signup(Client(BASE_URL), "bob")
        created_user_ids.append(bob["id"])
        bc: Client = bob["client"]
        status, ch, _ = bc.request("POST", "/api/channels", {"name": f"authz-{secrets.token_hex(3)}", "type": "private"})
        check("bob creates a private group", status == 201, f"status={status} body={ch}")
        private_id = ch["channelId"]
        created_channel_ids.append(private_id)
        status, f, _ = bc.request("POST", "/api/files", {"channelId": private_id, "originalName": "plan.txt", "mime": "text/plain", "sizeBytes": 10})
        check("bob registers a file in his private group", status == 201, f"status={status} body={f}")
        status, body, _ = ac.request("POST", f"/api/files/{f['fileId']}/scan", {})
        check("an outsider cannot scan a file in a private group", status == 403, f"status={status} body={body}")
        status, boot, _ = ac.request("GET", "/api/bootstrap")
        general = next(c for c in boot["channels"] if c["slug"] == "general")
        status, body, _ = ac.request("POST", "/api/messages", {"channelId": general["id"], "body": "look", "attachments": [f["fileId"]]})
        check("attaching a file from another channel is rejected", status == 400, f"status={status} body={body}")
        status, body, _ = ac.request("POST", "/api/messages", {"channelId": general["id"], "body": "reply", "parentId": f["messageId"]})
        check("replying into a thread from another channel is rejected", status == 400, f"status={status} body={body}")
        status, body, _ = bc.request("POST", f"/api/files/{f['fileId']}/scan", {})
        check("the uploader can still scan their own file", status == 200, f"status={status} body={body}")

        # --- Input validation ---------------------------------------------------------------
        status, body, _ = ac.request("POST", "/api/events", {"title": "No start"})
        check("event without startsAt is a 400, not a 500", status == 400, f"status={status} body={body}")
        status, body, _ = ac.request("POST", "/api/events/event_does_not_exist/response", {"response": "accepted"})
        check("responding to a missing event is a 404", status == 404, f"status={status} body={body}")
        status, body, _ = admin.request("PUT", "/api/admin/feature-flags", {"value": True})
        check("feature flag without a key is a 400", status == 400, f"status={status} body={body}")

        # --- Proxy headers are not trusted by default ---------------------------------------
        # Password spraying: one failure per account (so the per-account limit never trips) from
        # one source. Only the per-IP limit can stop it, and a spoofable client IP would defeat it.
        # Opt-in: it locks this machine's IP out of logins for LOGIN_LOCKOUT_WINDOW_MINUTES, so only
        # run it against a dedicated test instance (ideally with a short window).
        if os.environ.get("BEENCO_TEST_IP_LOCKOUT") != "1":
            print("[SKIP] per-IP lockout spoofing check (set BEENCO_TEST_IP_LOCKOUT=1 to run)")
            print("\nALL AUTHZ CHECKS PASSED")
            return
        attacker = Client(BASE_URL)
        codes = []
        for attempt in range(30):
            email = f"apitest-spray-{secrets.token_hex(4)}@beenco.local"
            status, _, _ = attacker.request("POST", "/api/auth/login", {"email": email, "password": "wrong"}, headers={"X-Forwarded-For": f"203.0.113.{attempt}"})
            codes.append(status)
            if status == 429:
                break
        check("rotating X-Forwarded-For does not avoid the per-IP lockout", 429 in codes, f"codes={codes}")

        print("\nALL AUTHZ CHECKS PASSED")
    finally:
        if admin.request("GET", "/api/session")[1].get("user") is None:
            admin.request("POST", "/api/auth/login", {"email": admin_email, "password": admin_password})
        for channel_id in created_channel_ids:
            admin.request("DELETE", f"/api/channels/{channel_id}")
        for user_id in created_user_ids:
            admin.request("PATCH", f"/api/admin/users/{user_id}", {"isActive": False})
        print(f"Cleaned up {len(created_channel_ids)} channel(s) and {len(created_user_ids)} account(s).")


if __name__ == "__main__":
    main()
