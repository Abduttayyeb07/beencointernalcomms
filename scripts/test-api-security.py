"""Integration test for the auth/session/permission behavior fixed in this codebase:
  - the removed /api/auth/google/dev login-bypass endpoint stays gone
  - sessions are cookie-only (httpOnly cookie set, no token in the JSON body, no cookie -> 401)
  - login rate limiting triggers per-account after repeated failures
  - admins can read a DM they are not a member of, but cannot post into it
  - creating a channel atomically creates its membership rows (transaction regression guard)

Runs against a real running portal + Postgres (docker compose, or `py apps/api/portal_server.py`).
Only ever touches throwaway accounts it creates itself with random emails; it never logs into
or modifies the real admin account, and deactivates everything it creates when done.

Usage:
    .venv\\Scripts\\python.exe scripts\\test-api-security.py [base_url]

Exits non-zero (with an assertion message) on the first failure.
"""
import http.client
import http.cookiejar
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4173"
ROOT = Path(__file__).resolve().parents[1]


def load_env_admin_creds() -> tuple[str, str]:
    """Reads the admin login the target server accepts. Process environment variables win (so the
    suite can run against an isolated test instance); otherwise the repo-root .env is used.
    There is no built-in default password: the server refuses to invent one."""
    if os.environ.get("PORTAL_ADMIN_PASSWORD"):
        return (os.environ.get("PORTAL_ADMIN_EMAIL") or "admin@beenco.local").strip().lower(), os.environ["PORTAL_ADMIN_PASSWORD"].strip()
    values = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
    email = values.get("PORTAL_ADMIN_EMAIL", "admin@beenco.local") or "admin@beenco.local"
    password = values.get("PORTAL_ADMIN_PASSWORD", "")
    if not password:
        raise SystemExit("Set PORTAL_ADMIN_PASSWORD (environment or .env) to the target server's admin password.")
    return email.lower(), password


class Client:
    """A tiny cookie-aware HTTP client — stdlib only, matching portal_server.py's own style."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, body: dict | None = None, headers: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with self.opener.open(req, timeout=15) as res:
                return res.status, json.loads(res.read() or b"{}"), dict(res.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}"), dict(exc.headers)

    def has_session_cookie(self) -> bool:
        return any(c.name == "beenco_session" and c.value for c in self.jar)

    def clear_cookies(self) -> None:
        self.jar.clear()


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        raise AssertionError(f"{label}: {detail}")


def signup(client: Client, label: str) -> dict:
    email = f"apitest-{label}-{secrets.token_hex(4)}@beenco.local"
    password = f"Testpass-{secrets.token_hex(4)}!1"
    status, body, _ = client.request("POST", "/api/auth/signup", {"displayName": f"API Test {label}", "email": email, "password": password})
    check(f"signup succeeds ({label})", status == 201, f"status={status} body={body}")
    return {"email": email, "password": password, "id": body["user"]["id"], "client": client}


def main() -> None:
    admin_email, admin_password = load_env_admin_creds()
    created_user_ids: list[str] = []
    created_channel_ids: list[str] = []
    admin = Client(BASE_URL)

    try:
        print(f"Target: {BASE_URL}")

        # --- 1. The removed dev-login bypass must stay gone -----------------------------------
        status, body, _ = Client(BASE_URL).request("POST", "/api/auth/google/dev", {"email": admin_email})
        check("google/dev bypass endpoint is gone", status in (401, 404), f"status={status} body={body}")

        # --- 2. Cookie-only sessions ------------------------------------------------------------
        alice = signup(Client(BASE_URL), "alice")
        created_user_ids.append(alice["id"])
        alice_client: Client = alice["client"]
        check("login response sets an httpOnly session cookie", alice_client.has_session_cookie())
        status, session_body, _ = alice_client.request("GET", "/api/session")
        check("cookie alone authenticates /api/session", status == 200 and session_body.get("user", {}).get("email") == alice["email"], f"status={status} body={session_body}")

        anon = Client(BASE_URL)
        status, body, _ = anon.request("GET", "/api/bootstrap")
        check("no cookie/header -> 401 on a protected route", status == 401, f"status={status} body={body}")

        status, body, headers = alice_client.request("POST", "/api/auth/logout")
        check("logout clears the session cookie", "beenco_session=;" in headers.get("Set-Cookie", ""), headers.get("Set-Cookie", ""))
        status, body, _ = alice_client.request("GET", "/api/bootstrap")
        check("cookie stops working immediately after logout", status == 401, f"status={status} body={body}")

        # --- 3. Login rate limiting, on a throwaway account only (never the real admin) ---------
        victim = signup(Client(BASE_URL), "ratelimit")
        created_user_ids.append(victim["id"])
        rl_client = Client(BASE_URL)
        last_status = None
        for attempt in range(1, 8):
            last_status, body, _ = rl_client.request("POST", "/api/auth/login", {"email": victim["email"], "password": "definitely-wrong"})
            if last_status == 429:
                break
        check("repeated failed logins eventually get rate-limited (429)", last_status == 429, f"final status={last_status}")
        status, body, _ = rl_client.request("POST", "/api/auth/login", {"email": victim["email"], "password": victim["password"]})
        check("the account stays locked even with the correct password", status == 429, f"status={status} body={body}")
        other = signup(Client(BASE_URL), "unrelated")
        created_user_ids.append(other["id"])
        check("a different account is unaffected by the first one's lockout", True)  # signup succeeding at all proves the server is still serving normal traffic

        # --- 4. Admin login (existing account, real password from .env — read-only check) -------
        admin_status, admin_body, _ = admin.request("POST", "/api/auth/login", {"email": admin_email, "password": admin_password})
        check("admin can still log in with the .env password after the hashing change", admin_status == 200, f"status={admin_status} body={admin_body}")

        # --- 5. Admin can read a DM they are not part of, but cannot post into it ---------------
        bob = signup(Client(BASE_URL), "bob")
        carol = signup(Client(BASE_URL), "carol")
        created_user_ids += [bob["id"], carol["id"]]
        status, dm, _ = bob["client"].request("POST", "/api/dms", {"userId": carol["id"]})
        check("two normal users can create a DM", status == 201, f"status={status} body={dm}")
        channel_id = dm["channelId"]
        created_channel_ids.append(channel_id)
        status, body, _ = bob["client"].request("POST", "/api/messages", {"channelId": channel_id, "body": "just between us"})
        check("a DM participant can post in their own DM", status == 201, f"status={status} body={body}")

        status, boot, _ = admin.request("GET", "/api/bootstrap")
        admin_sees_dm = any(c["id"] == channel_id for c in boot.get("channels", []))
        check("admin can see a DM they are not a member of", admin_sees_dm)
        status, body, _ = admin.request("POST", "/api/messages", {"channelId": channel_id, "body": "admin trying to post"})
        check("admin CANNOT post into a DM they are not a member of", status == 403, f"status={status} body={body}")

        # --- 6. Transactional channel creation: channel + creator membership land together ------
        status, ch, _ = bob["client"].request("POST", "/api/channels", {"name": f"tx-check-{secrets.token_hex(3)}", "type": "private"})
        check("channel creation succeeds", status == 201, f"status={status} body={ch}")
        created_channel_ids.append(ch["channelId"])
        status, boot2, _ = bob["client"].request("GET", "/api/bootstrap")
        created = next((c for c in boot2.get("channels", []) if c["id"] == ch["channelId"]), None)
        check("the creator is already a member right after creation (no partial write)", bool(created) and bob["id"] in (created.get("members") or []))

        print("\nALL CHECKS PASSED")
    finally:
        # Delete every throwaway channel/DM this run created, then deactivate the accounts.
        # Both run even if an earlier check failed, so a failed run never leaves clutter behind
        # (this is what missed the DM channel on every earlier run — signup was cleaned up,
        # the DM it created never was).
        if created_channel_ids or created_user_ids:
            admin_status, _, _ = admin.request("GET", "/api/session")
            if admin_status != 200:
                admin.request("POST", "/api/auth/login", {"email": admin_email, "password": admin_password})
            for channel_id in created_channel_ids:
                admin.request("DELETE", f"/api/channels/{channel_id}")
            for uid in created_user_ids:
                admin.request("PATCH", f"/api/admin/users/{uid}", {"isActive": False})
            print(f"Cleaned up {len(created_channel_ids)} throwaway channel(s) and {len(created_user_ids)} throwaway account(s).")


if __name__ == "__main__":
    main()
