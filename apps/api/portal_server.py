from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import mimetypes
import os
import queue
import re
import secrets
import shutil
import socket
import ssl
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http import cookies
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "env")

# --- Amazon Bedrock Configuration ---
ENABLE_BEDROCK_API = os.environ.get("ENABLE_BEDROCK_API", "True").strip().lower() in ("true", "1", "yes")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
AWS_BEDROCK_REGION = os.environ.get("AWS_BEDROCK_REGION", "eu-central-1").strip()
AWS_BEDROCK_MODEL = os.environ.get("AWS_BEDROCK_MODEL", "qwen.qwen3-32b-v1:0").strip()
AWS_BEDROCK_ENDPOINT_URL = os.environ.get("AWS_BEDROCK_ENDPOINT_URL", "").strip()
LOCAL_FALLBACK_MODEL = os.environ.get("LOCAL_FALLBACK_MODEL", "qwen.qwen3-32b-v1:0").strip()

def invoke_bedrock_llm(prompt: str, system_prompt: str | None = None) -> str | None:
    """Invokes Amazon Bedrock model using standard library AWS SigV4 signed requests."""
    if not ENABLE_BEDROCK_API or not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        return None

    try:
        region = AWS_BEDROCK_REGION or "eu-central-1"
        service = "bedrock"
        model_id = AWS_BEDROCK_MODEL or "qwen.qwen3-32b-v1:0"
        host = f"bedrock-runtime.{region}.amazonaws.com"
        
        endpoint = AWS_BEDROCK_ENDPOINT_URL or f"https://{host}/model/{model_id}/invoke"
        canonical_uri = f"/model/{urllib.parse.quote(model_id, safe='')}/invoke"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body_dict = {
            "messages": messages,
            "max_tokens": 1500,
            "temperature": 0.7,
        }
        request_parameters = json.dumps(body_dict)

        t = datetime.now(timezone.utc)
        amz_date = t.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = t.strftime("%Y%m%d")

        canonical_querystring = ""
        canonical_headers = f"content-type:application/json\nhost:{host}\nx-amz-date:{amz_date}\n"
        signed_headers = "content-type;host;x-amz-date"
        payload_hash = hashlib.sha256(request_parameters.encode("utf-8")).hexdigest()
        canonical_request = f"POST\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"

        def _sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = _sign(("AWS4" + AWS_SECRET_ACCESS_KEY).encode("utf-8"), date_stamp)
        k_region = _sign(k_date, region)
        k_service = _sign(k_region, service)
        signing_key = _sign(k_service, "aws4_request")

        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization_header = f"{algorithm} Credential={AWS_ACCESS_KEY_ID}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"

        headers = {
            "Content-Type": "application/json",
            "X-Amz-Date": amz_date,
            "Authorization": authorization_header,
        }

        req = urllib.request.Request(endpoint, data=request_parameters.encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if "choices" in res_data and res_data["choices"]:
                msg_content = res_data["choices"][0].get("message", {}).get("content")
                if msg_content:
                    cleaned = msg_content.strip()
                    if cleaned.startswith("```markdown") or cleaned.startswith("```"):
                        first_nl = cleaned.find("\n")
                        if first_nl != -1:
                            cleaned = cleaned[first_nl + 1:]
                        if cleaned.endswith("```"):
                            cleaned = cleaned[:-3].strip()
                    return cleaned.strip()
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print(f"[Bedrock LLM Warning] Bedrock HTTP {exc.code}: {err_body}")
        return None
    except Exception as exc:
        print(f"[Bedrock LLM Warning] Could not invoke Amazon Bedrock model ({exc}). Falling back to local/native report summarizer.")
        return None
    return None

WEB_ROOT = ROOT / "apps" / "web"
DATA_DIR = Path(os.environ.get("PORTAL_DATA_DIR", ROOT / "data"))
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://beenco:beenco@localhost:5432/beenco")
UPLOAD_ROOT = DATA_DIR / "uploads"
PORT = int(os.environ.get("PORT", "4173"))
HOST = os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0"
TWITTERAPI_IO_KEY = os.environ.get("TWITTERAPI_IO_KEY", "").strip()
TWITTER_MENTIONS_RETENTION_DAYS = max(1, int(os.environ.get("TWITTER_MENTIONS_RETENTION_DAYS", "90")))
TWITTER_MENTIONS_MAX_PAGES = max(1, min(5, int(os.environ.get("TWITTER_MENTIONS_MAX_PAGES", "1"))))
TWITTER_MENTIONS_RATE_LIMIT_COOLDOWN_MINUTES = max(5, int(os.environ.get("TWITTER_MENTIONS_RATE_LIMIT_COOLDOWN_MINUTES", "30")))
TWITTER_MENTIONS_ACCOUNT_INTERVAL_SECONDS = max(15, int(os.environ.get("TWITTER_MENTIONS_ACCOUNT_INTERVAL_SECONDS", "60")))
TWITTER_MENTIONS_DAY_TIMEZONE = os.environ.get("TWITTER_MENTIONS_DAY_TIMEZONE", "Asia/Karachi").strip() or "Asia/Karachi"
TWITTER_MENTION_ACCOUNTS = [
    item.strip().lstrip("@")
    for item in os.environ.get(
        "TWITTER_MENTION_ACCOUNTS",
        "PermaPod_xyz,NawaFinance,Ask_ORO,Valdora_finance,RukanPay,IndexLitro,nomyxio,BTCS_SA,"
        "fuzefinance,taurus_hq,ZIGScan,HiCryptoComics,DegenTer_Bot,Arkive_live,KOREIT_IO,BeencoLabs,"
        "decodeweb3AI,FracksProtocol,LendraOne,Pollin8_,MemesDotFun_,Toknex_xyz",
    ).split(",")
    if item.strip().lstrip("@")
]
DEFAULT_SSL_CERT_FILE = DATA_DIR / "certs" / "localhost.crt"
DEFAULT_SSL_KEY_FILE = DATA_DIR / "certs" / "localhost.key"
SSL_CERT_FILE = os.environ.get("SSL_CERT_FILE", str(DEFAULT_SSL_CERT_FILE) if DEFAULT_SSL_CERT_FILE.exists() else "").strip()
SSL_KEY_FILE = os.environ.get("SSL_KEY_FILE", str(DEFAULT_SSL_KEY_FILE) if DEFAULT_SSL_KEY_FILE.exists() else "").strip()
URL_SCHEME = "https" if SSL_CERT_FILE and SSL_KEY_FILE else "http"
# When a reverse proxy (Nginx, Traefik, Cloudflare, etc.) terminates TLS in front of this app —
# the normal production setup — the app itself only ever sees plain HTTP from the proxy, so
# URL_SCHEME alone would say "http" even though real users are on https://. That would silently
# drop the Secure flag from the session cookie. Set TRUST_PROXY_HEADERS=true once the proxy is
# configured to set X-Forwarded-Proto (and strip any client-supplied one) — never enable this if
# the app is reachable directly, since a client could otherwise spoof that header.
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in ("1", "true", "yes")


class LiveEventBroker:
    """Thread-safe event broker for streaming Server-Sent Events (SSE) to connected clients."""

    def __init__(self) -> None:
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.add(q)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def broadcast(self, event_type: str, data: dict | None = None) -> None:
        payload = {
            "type": event_type,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            dead: list[queue.Queue] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except Exception:
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)


LIVE_BROKER = LiveEventBroker()

SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "14"))
OAUTH_STATE_TTL_MINUTES = 10
# Optional comma-separated allowlist for Google sign-in, e.g. "beenco.io". Empty = any verified email.
GOOGLE_ALLOWED_DOMAINS = {d.strip().lower().lstrip("@") for d in os.environ.get("GOOGLE_ALLOWED_DOMAINS", "").split(",") if d.strip()}
MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024 * 1024
MAX_LOCAL_UPLOAD_BYTES = int(os.environ.get("MAX_LOCAL_UPLOAD_BYTES", str(512 * 1024 * 1024)))
MAX_JSON_BODY_BYTES = int(os.environ.get("MAX_JSON_BODY_BYTES", str(32 * 1024 * 1024)))
MAX_PREVIEW_DATA_URL_BYTES = int(os.environ.get("MAX_PREVIEW_DATA_URL_BYTES", str(8 * 1024 * 1024)))
MAX_FILENAME_LENGTH = 180
ALLOWED_FILE_EXTENSIONS = {
    ".csv",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".json",
    ".m4a",
    ".md",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".pdf",
    ".png",
    ".pptx",
    ".rtf",
    ".txt",
    ".wav",
    ".webm",
    ".webp",
    ".xlsx",
}
VOICE_FILE_EXTENSIONS = {".webm", ".wav", ".mp3", ".m4a", ".ogg"}
BLOCKED_FILE_EXTENSIONS = {
    ".7z",
    ".apk",
    ".app",
    ".bat",
    ".bin",
    ".cmd",
    ".com",
    ".dll",
    ".dmg",
    ".docm",
    ".exe",
    ".gz",
    ".hta",
    ".html",
    ".iso",
    ".jar",
    ".js",
    ".lnk",
    ".msi",
    ".php",
    ".pl",
    ".ps1",
    ".psm1",
    ".py",
    ".rar",
    ".rb",
    ".reg",
    ".scr",
    ".scf",
    ".sh",
    ".svg",
    ".tar",
    ".vbs",
    ".wsf",
    ".xlsm",
    ".xml",
    ".zip",
}
ALLOWED_FILE_MIME_TYPES = {
    "application/json",
    "application/pdf",
    "application/rtf",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/wave",
    "audio/webm",
    "audio/x-wav",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/csv",
    "text/markdown",
    "text/plain",
    "video/mp4",
    "video/quicktime",
    "video/webm",
}
BLOCKED_FILE_MIME_TYPES = {
    "application/gzip",
    "application/java-archive",
    "application/javascript",
    "application/msword",
    "application/vnd.microsoft.portable-executable",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    "application/vnd.ms-word.document.macroenabled.12",
    "application/x-7z-compressed",
    "application/x-bat",
    "application/x-dosexec",
    "application/x-msdownload",
    "application/x-msdos-program",
    "application/x-rar-compressed",
    "application/x-sh",
    "application/x-tar",
    "application/zip",
    "image/svg+xml",
    "text/html",
    "text/javascript",
    "text/xml",
}


class RequestTooLarge(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_remote_host(value: str) -> str:
    host = str(value or "").strip()
    if not host:
        return ""
    if len(host) > 253 or re.search(r"[\s/\\]", host) or "://" in host:
        raise ValueError("Use a LAN computer name, IP address, or host:port for remote desktop.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}", host):
        raise ValueError("Remote desktop host contains unsupported characters.")
    return host


def uid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12).replace('-', '').replace('_', '')}"


def slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    return "-".join(part for part in slug.split("-") if part)[:64] or "channel"


def json_dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def json_loads(value, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def list_from_payload(value) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[\n,]+", str(value or ""))
    normalized: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text[:500])
    return normalized


def format_size(bytes_value: int) -> str:
    size = float(bytes_value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{bytes_value} B"


def local_access_urls() -> list[str]:
    addresses: list[str] = []

    def add_address(address: str) -> None:
        address = address.strip()
        if not address or address in {"0.0.0.0", "::"} or address in addresses:
            return
        addresses.append(address)

    if HOST not in {"0.0.0.0", "::"}:
        add_address(HOST)
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if address and not address.startswith("127."):
                add_address(address)
    except OSError:
        pass
    add_address("127.0.0.1")
    add_address("localhost")
    return [f"{URL_SCHEME}://{f'[{address}]' if ':' in address and not address.startswith('[') else address}:{PORT}" for address in addresses]


def fallback_voice_data_url(duration_seconds: int | None = None) -> str:
    sample_rate = 8000
    duration = max(1, min(int(duration_seconds or 1), 5))
    samples = sample_rate * duration
    pcm = bytearray()
    for index in range(samples):
        envelope = math.sin(math.pi * index / max(samples - 1, 1))
        sample = math.sin(2 * math.pi * 440 * index / sample_rate) * 0.28 * envelope
        pcm.extend(int(sample * 32767).to_bytes(2, "little", signed=True))
    data_size = len(pcm)
    header = (
        b"RIFF"
        + (36 + data_size).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + sample_rate.to_bytes(4, "little")
        + (sample_rate * 2).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + data_size.to_bytes(4, "little")
    )
    return f"data:audio/wav;base64,{base64.b64encode(header + pcm).decode('ascii')}"


def file_policy_payload() -> dict:
    return {
        "allowedExtensions": sorted(ALLOWED_FILE_EXTENSIONS | VOICE_FILE_EXTENSIONS),
        "blockedExtensions": sorted(BLOCKED_FILE_EXTENSIONS),
        "allowedMimeTypes": sorted(ALLOWED_FILE_MIME_TYPES),
        "maxFileSizeBytes": MAX_FILE_SIZE_BYTES,
        "directUploadMaxBytes": min(MAX_FILE_SIZE_BYTES, MAX_LOCAL_UPLOAD_BYTES),
        "maxJsonBodyBytes": MAX_JSON_BODY_BYTES,
        "maxPreviewDataUrlBytes": MAX_PREVIEW_DATA_URL_BYTES,
        "scannerRequired": True,
        "policy": "allowlist",
    }


def clean_original_filename(value: str | None) -> str:
    name = str(value or "upload.bin").strip()
    if not name:
        name = "upload.bin"
    if len(name) > MAX_FILENAME_LENGTH:
        raise ValueError(f"File name must be {MAX_FILENAME_LENGTH} characters or less.")
    if any(ord(ch) < 32 for ch in name) or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("File name cannot include paths or control characters.")
    return name


def normalized_mime(value: str | None) -> str:
    return (str(value or "application/octet-stream").split(";", 1)[0].strip().lower() or "application/octet-stream")


def upload_storage_path(channel_id: str, file_id: str, original_name: str) -> Path:
    root = UPLOAD_ROOT.resolve()
    path = (UPLOAD_ROOT / channel_id / file_id / original_name).resolve()
    if root != path and root not in path.parents:
        raise ValueError("Invalid upload storage path.")
    return path


def remove_upload_dir(channel_id: str, file_id: str | None = None) -> None:
    """Deletes stored upload bytes for one file (or a whole channel) after its rows are gone."""
    root = UPLOAD_ROOT.resolve()
    target = (UPLOAD_ROOT / channel_id / file_id if file_id else UPLOAD_ROOT / channel_id).resolve()
    if target == root or root not in target.parents or not target.exists():
        return
    try:
        shutil.rmtree(target)
    except OSError as error:
        print(f"[Uploads] Could not remove {target}: {error}", file=sys.stderr, flush=True)


def content_disposition(original_name: str) -> str:
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("._") or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{urllib.parse.quote(original_name)}"


def parse_content_disposition(value: str) -> dict:
    result: dict[str, str] = {}
    for part in value.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, raw_val = part.split("=", 1)
        result[key.strip().lower()] = raw_val.strip().strip('"')
    return result


def validate_upload_metadata(body: dict) -> dict:
    kind = str(body.get("kind") or "file").strip().lower()
    if kind not in {"file", "voice"}:
        raise ValueError("Unsupported upload kind.")

    original_name = clean_original_filename(body.get("originalName"))
    extension = Path(original_name).suffix.lower()
    if not extension:
        raise ValueError("Files must include an allowed extension.")

    try:
        size = int(body.get("sizeBytes", 0))
    except (TypeError, ValueError):
        raise ValueError("File size must be numeric.")
    if size < 0:
        raise ValueError("File size cannot be negative.")
    if size > MAX_FILE_SIZE_BYTES:
        raise RequestTooLarge("File exceeds the 15 GB maximum.")

    mime = normalized_mime(body.get("mime"))
    if kind == "voice":
        if extension not in VOICE_FILE_EXTENSIONS:
            raise ValueError("Voice messages must use webm, wav, mp3, m4a, or ogg audio.")
        if mime != "application/octet-stream" and not mime.startswith("audio/"):
            raise ValueError("Voice messages must use an audio MIME type.")
        preview_data_url = str(body.get("previewDataUrl", "")).strip()
        if preview_data_url:
            preview_lower = preview_data_url[:64].lower()
            if not (preview_lower.startswith("data:audio/") and ";base64," in preview_lower):
                raise ValueError("Voice preview must be a base64 audio data URL.")
            if len(preview_data_url.encode("utf-8")) > MAX_PREVIEW_DATA_URL_BYTES:
                raise RequestTooLarge("Voice preview is too large.")
        return {"kind": kind, "originalName": original_name, "extension": extension, "mime": mime, "sizeBytes": size}

    if extension in BLOCKED_FILE_EXTENSIONS or extension not in ALLOWED_FILE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_FILE_EXTENSIONS))
        raise ValueError(f"File type {extension} is not allowed. Allowed extensions: {allowed}.")
    if mime in BLOCKED_FILE_MIME_TYPES:
        raise ValueError(f"File MIME type {mime} is blocked by policy.")
    if mime != "application/octet-stream" and mime not in ALLOWED_FILE_MIME_TYPES:
        raise ValueError(f"File MIME type {mime} is not allowed for sharing.")
    return {"kind": kind, "originalName": original_name, "extension": extension, "mime": mime, "sizeBytes": size}


# OWASP's current PBKDF2-HMAC-SHA256 recommendation (2023 revision of the Password Storage
# Cheat Sheet); the previous constant here (210_000) was from an older revision.
PBKDF2_ITERATIONS = 600_000
# Iteration count assumed for a hash stored in the old format, before it carried its own count.
LEGACY_PBKDF2_ITERATIONS = 210_000


def hash_password(password: str, salt: str | None = None, iterations: int = PBKDF2_ITERATIONS) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    # The iteration count travels with the hash (`<iterations>$<digest>`) so verification always
    # knows which cost parameter produced it — old and new hashes can be verified side by side,
    # and the count can be raised again later without invalidating existing passwords.
    return f"{iterations}${base64.b64encode(digest).decode('ascii')}", salt


def hash_iterations_used(stored_digest: str) -> int:
    if "$" in stored_digest:
        prefix, _, _ = stored_digest.partition("$")
        if prefix.isdigit():
            return int(prefix)
    return LEGACY_PBKDF2_ITERATIONS


def verify_password(password: str, stored_digest: str, salt: str) -> bool:
    iterations = hash_iterations_used(stored_digest)
    raw_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    candidate_b64 = base64.b64encode(raw_digest).decode("ascii")
    # A legacy hash was stored as the bare base64 digest, with no "<iterations>$" prefix.
    candidate = candidate_b64 if "$" not in stored_digest else f"{iterations}${candidate_b64}"
    return hmac.compare_digest(candidate, stored_digest)


def needs_rehash(stored_digest: str) -> bool:
    return hash_iterations_used(stored_digest) < PBKDF2_ITERATIONS


class PostgresConnection:
    """Small compatibility layer that keeps the existing query code readable."""

    def __init__(self, url: str):
        self.connection = psycopg.connect(url, autocommit=True, row_factory=dict_row)

    @staticmethod
    def _sql(query: str) -> str:
        query = query.replace("?", "%s")
        query = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", query)
        query = re.sub(r"%(?!s\b|b\b|t\b|\()", "%%", query)
        query = re.sub(r"^\s*INSERT OR (?:IGNORE|REPLACE) INTO", "INSERT INTO", query, flags=re.IGNORECASE)
        if re.match(r"^\s*INSERT INTO", query, flags=re.IGNORECASE) and "ON CONFLICT" not in query.upper():
            if "INSERT INTO settings" in query:
                query += " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            elif "INSERT INTO feature_flags" in query:
                query += " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            elif "INSERT INTO event_attendees" in query:
                query += " ON CONFLICT (event_id, user_id) DO UPDATE SET response = EXCLUDED.response"
            elif "INSERT INTO channel_read_states" in query:
                query += " ON CONFLICT (channel_id, user_id) DO UPDATE SET last_read_message_id = EXCLUDED.last_read_message_id, updated_at = EXCLUDED.updated_at"
            elif query.strip().upper().startswith("INSERT INTO"):
                query += " ON CONFLICT DO NOTHING"
        return query

    def execute(self, query: str, params: tuple | list | None = None):
        return self.connection.execute(self._sql(query), params or ())

    def executemany(self, query: str, params):
        with self.connection.cursor() as cursor:
            return cursor.executemany(self._sql(query), params)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)

    def transaction(self):
        """Groups several writes into one atomic unit: all commit together, or all roll back
        together if an exception is raised inside the block. Safe to use on this autocommit
        connection — psycopg3's transaction() context manager suspends autocommit for the
        block and restores it afterward. Nesting is fine (inner blocks become savepoints)."""
        return self.connection.transaction()

    def close(self) -> None:
        self.connection.close()


def db() -> PostgresConnection:
    return PostgresConnection(DATABASE_URL)


def init_db() -> None:
    with closing(db()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              email TEXT NOT NULL UNIQUE,
              username TEXT NOT NULL UNIQUE,
              display_name TEXT NOT NULL,
              password_hash TEXT,
              password_salt TEXT,
              provider TEXT NOT NULL DEFAULT 'local',
              provider_subject TEXT,
              title TEXT DEFAULT '',
              department TEXT DEFAULT '',
              role TEXT NOT NULL DEFAULT 'member',
              presence TEXT NOT NULL DEFAULT 'online',
              status_text TEXT DEFAULT '',
              status_emoji TEXT DEFAULT '',
              avatar_url TEXT DEFAULT '',
              is_active INTEGER NOT NULL DEFAULT 1,
              last_seen_at TEXT DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS channels (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              slug TEXT NOT NULL UNIQUE,
              type TEXT NOT NULL CHECK(type IN ('public','private','dm','group_dm')),
              topic TEXT DEFAULT '',
              description TEXT DEFAULT '',
              created_by TEXT,
              retention_days INTEGER,
              legal_hold INTEGER NOT NULL DEFAULT 0,
              locked INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS channel_members (
              channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              role TEXT NOT NULL DEFAULT 'member',
              last_read_message_id TEXT,
              typing_until TEXT DEFAULT '',
              muted INTEGER NOT NULL DEFAULT 0,
              joined_at TEXT NOT NULL,
              PRIMARY KEY(channel_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS channel_read_states (
              channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              last_read_message_id TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(channel_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,
              channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
              author_id TEXT NOT NULL REFERENCES users(id),
              parent_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
              body TEXT NOT NULL,
              is_urgent INTEGER NOT NULL DEFAULT 0,
              acknowledged_at TEXT DEFAULT '',
              acknowledged_by TEXT DEFAULT '',
              edited_at TEXT,
              deleted_at TEXT,
              edit_history TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files (
              id TEXT PRIMARY KEY,
              channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
              uploader_id TEXT NOT NULL REFERENCES users(id),
              original_name TEXT NOT NULL,
              mime TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending','available','quarantined')),
              checksum TEXT DEFAULT '',
              version INTEGER NOT NULL DEFAULT 1,
              storage_key TEXT NOT NULL,
              kind TEXT DEFAULT 'file',
              duration INTEGER DEFAULT 0,
              waveform TEXT NOT NULL DEFAULT '[]',
              preview_data_url TEXT DEFAULT '',
              extracted_text TEXT DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS message_attachments (
              message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
              file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              PRIMARY KEY(message_id, file_id)
            );

            CREATE TABLE IF NOT EXISTS reactions (
              message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              emoji TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(message_id, user_id, emoji)
            );

            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              description TEXT DEFAULT '',
              starts_at TEXT NOT NULL,
              ends_at TEXT NOT NULL,
              location TEXT DEFAULT '',
              livekit_room TEXT DEFAULT '',
              created_by TEXT NOT NULL REFERENCES users(id),
              target_group_id TEXT,
              client_name TEXT DEFAULT '',
              event_type TEXT DEFAULT 'meeting',
              advance_notice_days INTEGER DEFAULT 4,
              last_alerted_date TEXT DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS weekly_activity_reports (
              id TEXT PRIMARY KEY,
              user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
              report_type TEXT NOT NULL DEFAULT 'user',
              report_trigger TEXT NOT NULL DEFAULT 'manual',
              generated_by TEXT DEFAULT '',
              week_start TEXT NOT NULL,
              week_end TEXT NOT NULL,
              title TEXT NOT NULL,
              summary TEXT NOT NULL,
              collaborators TEXT NOT NULL DEFAULT '[]',
              channels_involved TEXT NOT NULL DEFAULT '[]',
              metrics TEXT NOT NULL DEFAULT '{}',
              delivered_to_user_at TEXT DEFAULT '',
              delivered_to_admin_at TEXT DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_attendees (
              event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              response TEXT NOT NULL DEFAULT 'pending',
              PRIMARY KEY(event_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS announcements (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              body TEXT NOT NULL,
              author_id TEXT NOT NULL REFERENCES users(id),
              target_group_id TEXT,
              pinned_until TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              type TEXT NOT NULL,
              payload TEXT NOT NULL DEFAULT '{}',
              read_at TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS x_mentions (
              tweet_id TEXT PRIMARY KEY,
              monitored_usernames TEXT NOT NULL DEFAULT '[]',
              tweet_url TEXT NOT NULL,
              tweet_text TEXT NOT NULL DEFAULT '',
              author_username TEXT NOT NULL DEFAULT '',
              author_name TEXT NOT NULL DEFAULT '',
              author_profile_picture TEXT NOT NULL DEFAULT '',
              like_count INTEGER NOT NULL DEFAULT 0,
              reply_count INTEGER NOT NULL DEFAULT 0,
              retweet_count INTEGER NOT NULL DEFAULT 0,
              quote_count INTEGER NOT NULL DEFAULT 0,
              view_count INTEGER NOT NULL DEFAULT 0,
              tweet_created_at TEXT NOT NULL,
              raw_payload TEXT NOT NULL DEFAULT '{}',
              discovered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workspace_tasks (
              id TEXT PRIMARY KEY,
              channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
              assignee_id TEXT REFERENCES users(id) ON DELETE SET NULL,
              creator_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              title TEXT NOT NULL,
              subject TEXT DEFAULT '',
              reference_links TEXT NOT NULL DEFAULT '[]',
              deadline TEXT DEFAULT '',
              tags TEXT NOT NULL DEFAULT '[]',
              status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_progress','done','cancelled')),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS remote_assist_sessions (
              id TEXT PRIMARY KEY,
              requester_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              target_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              target_host TEXT DEFAULT '',
              reason TEXT DEFAULT '',
              status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected','cancelled','ended')),
              created_at TEXT NOT NULL,
              responded_at TEXT DEFAULT '',
              ended_at TEXT DEFAULT '',
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log (
              id TEXT PRIMARY KEY,
              actor_id TEXT,
              action TEXT NOT NULL,
              target_type TEXT NOT NULL,
              target_id TEXT NOT NULL,
              ip TEXT DEFAULT '',
              user_agent TEXT DEFAULT '',
              metadata TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feature_flags (
              key TEXT PRIMARY KEY,
              value INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS employees (
              id TEXT PRIMARY KEY,
              user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
              ad_object_guid TEXT DEFAULT '',
              full_name TEXT NOT NULL,
              cnic TEXT DEFAULT '',
              email TEXT DEFAULT '',
              phone TEXT DEFAULT '',
              department TEXT DEFAULT '',
              designation TEXT DEFAULT '',
              employment_type TEXT DEFAULT 'full-time',
              status TEXT DEFAULT 'active',
              joining_date TEXT DEFAULT '',
              exit_date TEXT DEFAULT '',
              manager_id TEXT REFERENCES employees(id) ON DELETE SET NULL,
              location TEXT DEFAULT '',
              photo_url TEXT DEFAULT '',
              biometric_user_id TEXT DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY,
              asset_tag TEXT NOT NULL UNIQUE,
              category TEXT NOT NULL,
              model TEXT DEFAULT '',
              serial TEXT DEFAULT '',
              status TEXT NOT NULL DEFAULT 'available',
              purchase_date TEXT DEFAULT '',
              value REAL DEFAULT 0,
              warranty_expiry TEXT DEFAULT '',
              condition TEXT DEFAULT 'good',
              notes TEXT DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS asset_assignments (
              id TEXT PRIMARY KEY,
              asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
              employee_id TEXT NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
              assigned_at TEXT NOT NULL,
              expected_return TEXT DEFAULT '',
              returned_at TEXT DEFAULT '',
              condition_out TEXT DEFAULT '',
              condition_in TEXT DEFAULT '',
              assigned_by TEXT REFERENCES users(id),
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attendance_days (
              id TEXT PRIMARY KEY,
              employee_id TEXT NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
              date TEXT NOT NULL,
              first_in TEXT DEFAULT '',
              last_out TEXT DEFAULT '',
              worked_minutes INTEGER DEFAULT 0,
              status TEXT DEFAULT 'present',
              corrected_by TEXT REFERENCES users(id),
              correction_reason TEXT DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE(employee_id, date)
            );

            CREATE TABLE IF NOT EXISTS leave_requests (
              id TEXT PRIMARY KEY,
              employee_id TEXT NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
              leave_type TEXT NOT NULL DEFAULT 'annual',
              start_date TEXT NOT NULL,
              end_date TEXT NOT NULL,
              days REAL NOT NULL DEFAULT 1,
              reason TEXT DEFAULT '',
              status TEXT NOT NULL DEFAULT 'pending',
              approver_id TEXT REFERENCES users(id),
              decided_at TEXT DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hr_documents (
              id TEXT PRIMARY KEY,
              employee_id TEXT NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
              type TEXT DEFAULT 'document',
              title TEXT NOT NULL,
              storage_key TEXT NOT NULL,
              mime TEXT DEFAULT 'application/octet-stream',
              size_bytes INTEGER DEFAULT 0,
              expiry_date TEXT DEFAULT '',
              uploaded_by TEXT REFERENCES users(id),
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_tasks (
              id TEXT PRIMARY KEY,
              employee_id TEXT REFERENCES employees(id) ON DELETE CASCADE,
              kind TEXT NOT NULL DEFAULT 'onboarding',
              title TEXT NOT NULL,
              assignee_role TEXT DEFAULT 'it_admin',
              due_date TEXT DEFAULT '',
              status TEXT DEFAULT 'open',
              completed_by TEXT REFERENCES users(id),
              completed_at TEXT DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(parent_id);
            CREATE INDEX IF NOT EXISTS idx_files_channel ON files(channel_id);
            CREATE INDEX IF NOT EXISTS idx_workspace_tasks_channel ON workspace_tasks(channel_id, status, deadline);
            CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_x_mentions_created ON x_mentions(tweet_created_at);
            CREATE INDEX IF NOT EXISTS idx_remote_assist_users ON remote_assist_sessions(requester_id, target_user_id, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_employees_user ON employees(user_id);
            CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
            CREATE INDEX IF NOT EXISTS idx_assignments_employee ON asset_assignments(employee_id);
            CREATE INDEX IF NOT EXISTS idx_attendance_employee ON attendance_days(employee_id, date);
            """
        )
        ensure_schema_migrations(conn)
        ensure_default_workspace(conn)
        ensure_admin_account(conn)
        ensure_employee_records(conn)


def ensure_schema_migrations(conn: PostgresConnection) -> None:
    def columns(table: str) -> set[str]:
        return {row["name"] for row in conn.execute(
            "SELECT column_name AS name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = ?",
            (table,),
        ).fetchall()}

    user_columns = columns("users")
    if "last_seen_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN last_seen_at TEXT DEFAULT ''")
    member_columns = columns("channel_members")
    if "typing_until" not in member_columns:
        conn.execute("ALTER TABLE channel_members ADD COLUMN typing_until TEXT DEFAULT ''")
    file_columns = columns("files")
    if "preview_data_url" not in file_columns:
        conn.execute("ALTER TABLE files ADD COLUMN preview_data_url TEXT DEFAULT ''")
    msg_columns = columns("messages")
    if "is_urgent" not in msg_columns:
        conn.execute("ALTER TABLE messages ADD COLUMN is_urgent INTEGER NOT NULL DEFAULT 0")
    if "acknowledged_at" not in msg_columns:
        conn.execute("ALTER TABLE messages ADD COLUMN acknowledged_at TEXT DEFAULT ''")
    if "acknowledged_by" not in msg_columns:
        conn.execute("ALTER TABLE messages ADD COLUMN acknowledged_by TEXT DEFAULT ''")
    event_columns = columns("events")
    if "client_name" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN client_name TEXT DEFAULT ''")
    if "event_type" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN event_type TEXT DEFAULT 'meeting'")
    if "advance_notice_days" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN advance_notice_days INTEGER DEFAULT 4")
    if "last_alerted_date" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN last_alerted_date TEXT DEFAULT ''")
    report_columns = columns("weekly_activity_reports")
    if "report_type" not in report_columns:
        conn.execute("ALTER TABLE weekly_activity_reports ADD COLUMN report_type TEXT NOT NULL DEFAULT 'user'")
    if "report_trigger" not in report_columns:
        conn.execute("ALTER TABLE weekly_activity_reports ADD COLUMN report_trigger TEXT NOT NULL DEFAULT 'manual'")
    if "generated_by" not in report_columns:
        conn.execute("ALTER TABLE weekly_activity_reports ADD COLUMN generated_by TEXT DEFAULT ''")
    # Combined team reports are not tied to a single user.
    conn.execute("ALTER TABLE weekly_activity_reports ALTER COLUMN user_id DROP NOT NULL")
    # The Wazuh admin portal (simulated alerts/agents/rules) was removed; drop its leftover table.
    conn.execute("DROP TABLE IF EXISTS wazuh_alert_triage")
    # Voice/video calling and its handler code (never actually wired to a route) were removed;
    # drop these now-orphaned tables too.
    conn.execute("DROP TABLE IF EXISTS call_signals")
    conn.execute("DROP TABLE IF EXISTS call_participants")
    conn.execute("DROP TABLE IF EXISTS calls")
    conn.execute("DELETE FROM feature_flags WHERE key = 'calls'")
    voice_rows = conn.execute(
        "SELECT id, duration FROM files WHERE kind = 'voice' AND COALESCE(preview_data_url, '') = ''"
    ).fetchall()
    for row in voice_rows:
        conn.execute("UPDATE files SET preview_data_url = ? WHERE id = ?", (fallback_voice_data_url(row["duration"]), row["id"]))
    # Recover voice messages that have audio in preview_data_url but no file on disk
    legacy_voice = conn.execute(
        "SELECT id, channel_id, original_name, preview_data_url FROM files "
        "WHERE kind = 'voice' AND COALESCE(preview_data_url, '') != '' AND status = 'available'"
    ).fetchall()
    for row in legacy_voice:
        storage_path = upload_storage_path(row["channel_id"], row["id"], row["original_name"])
        if storage_path.exists():
            continue
        preview = row["preview_data_url"] or ""
        if not (preview.startswith("data:") and ";base64," in preview):
            continue
        try:
            audio_bytes = base64.b64decode(preview.split(";base64,", 1)[1])
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage_path.write_bytes(audio_bytes)
            conn.execute(
                "UPDATE files SET size_bytes = ?, checksum = ? WHERE id = ?",
                (len(audio_bytes), hashlib.sha256(audio_bytes).hexdigest(), row["id"]),
            )
        except Exception:
            pass


def ensure_default_workspace(conn: PostgresConnection) -> None:
    count = conn.execute("SELECT COUNT(*) AS total FROM channels").fetchone()["total"]
    now = utc_now()
    if not count:
        channels = [
            ("ch_announcements", "announcements", "announcements", "public", "Company broadcasts", "Pinned company-wide announcements.", None, None, 1),
            ("ch_general", "general", "general", "public", "Company-wide chat", "Default employee channel.", None, 365, 0),
        ]
        conn.executemany(
            """
            INSERT INTO channels (id, name, slug, type, topic, description, created_by, retention_days, legal_hold, locked, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            [(*channel, now) for channel in channels],
        )
    for key, value in {
        "retention_global_days": "365",
        "google_signup_enabled": "1",
        "ad_login_enabled": "1",
    }.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    for key, value in {
        "voiceMessages": 1,
        "filePreview": 1,
        "legalHold": 1,
        "desktopNotifications": 1,
        "pushNotifications": 1,
        "adminCommandCenter": 1,
        "hrPortal": 1,
        "shufflePortal": 1,
    }.items():
        conn.execute("INSERT OR IGNORE INTO feature_flags (key, value) VALUES (?, ?)", (key, value))


ADMIN_PASSWORD_PLACEHOLDERS = ("change-this-admin-password", "Change@123")


def ensure_admin_account(conn: PostgresConnection) -> None:
    email = os.environ.get("PORTAL_ADMIN_EMAIL", "admin@beenco.local").strip().lower()
    raw_password = os.environ.get("PORTAL_ADMIN_PASSWORD", "").strip()
    configured = bool(raw_password) and raw_password not in ADMIN_PASSWORD_PLACEHOLDERS
    display_name = os.environ.get("PORTAL_ADMIN_NAME", "Beenco Administrator")
    if not email:
        return
    existing = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if configured:
        password = raw_password
    elif existing and existing["password_hash"] and not any(
        verify_password(placeholder, existing["password_hash"], existing["password_salt"]) for placeholder in ADMIN_PASSWORD_PLACEHOLDERS
    ):
        # No password configured: keep whatever real password the admin already has.
        password = None
    else:
        # First start (or an admin still on a publicly known placeholder) with no password
        # configured. Never fall back to a guessable built-in default: mint a random one and
        # show it once, on the operator's console only.
        password = secrets.token_urlsafe(18)
        print(
            f"[SECURITY] PORTAL_ADMIN_PASSWORD is not set. Generated a one-time password for {email}: {password}\n"
            "[SECURITY] Set PORTAL_ADMIN_PASSWORD in .env to choose your own; this value is not shown again.",
            file=sys.stderr,
            flush=True,
        )
    changed = False
    if existing:
        # A configured PORTAL_ADMIN_PASSWORD is authoritative and is re-applied on every start.
        is_pwd_valid = password is None or bool(existing["password_hash"] and verify_password(password, existing["password_hash"], existing["password_salt"]))
        changed = existing["role"] != "super_admin" or not is_pwd_valid
        if changed and password is None:
            conn.execute("UPDATE users SET role = 'super_admin', is_active = 1 WHERE email = ?", (email,))
        elif changed:
            password_hash, password_salt = hash_password(password)
            conn.execute(
                """
                UPDATE users
                SET display_name = ?, username = 'admin', password_hash = ?, password_salt = ?,
                    provider = 'local', role = 'super_admin', is_active = 1, presence = 'online',
                    title = 'Portal Administrator', department = 'IT'
                WHERE email = ?
                """,
                (display_name, password_hash, password_salt, email),
            )
        admin_id = existing["id"]
    else:
        password_hash, password_salt = hash_password(password)
        username_owner = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
        if username_owner:
            admin_id = username_owner["id"]
            conn.execute(
                """
                UPDATE users
                SET email = ?, display_name = ?, password_hash = ?, password_salt = ?,
                    provider = 'local', provider_subject = '', role = 'super_admin',
                    is_active = 1, presence = 'online', title = 'Portal Administrator',
                    department = 'IT', status_text = 'Administrator account'
                WHERE id = ?
                """,
                (email, display_name, password_hash, password_salt, admin_id),
            )
        else:
            admin_id = uid("u")
            conn.execute(
                """
                INSERT INTO users (
                  id, email, username, display_name, password_hash, password_salt, provider,
                  provider_subject, title, department, role, presence, status_text, created_at
                )
                VALUES (?, ?, 'admin', ?, ?, ?, 'local', '', 'Portal Administrator', 'IT',
                        'super_admin', 'online', 'Administrator account', ?)
                """,
                (admin_id, email, display_name, password_hash, password_salt, utc_now()),
            )
            inserted = conn.execute("SELECT id FROM users WHERE id = ?", (admin_id,)).fetchone()
            if not inserted:
                inserted = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if inserted:
                admin_id = inserted["id"]
        changed = True
    join_public_channels(conn, admin_id)
    if changed:
        write_audit(conn, admin_id, "user.admin.ensured", "user", admin_id, {"email": email, "role": "super_admin"})


def ensure_employee_records(conn: PostgresConnection) -> None:
    now = utc_now()
    for user in conn.execute("SELECT * FROM users WHERE is_active = 1").fetchall():
        existing = conn.execute("SELECT id FROM employees WHERE user_id = ?", (user["id"],)).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT INTO employees (
              id, user_id, full_name, email, department, designation, employment_type,
              status, joining_date, location, biometric_user_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'full-time', 'active', ?, 'Beenco HQ', ?, ?, ?)
            """,
            (
                uid("emp"),
                user["id"],
                user["display_name"],
                user["email"],
                user["department"] or "Unassigned",
                user["title"] or user["role"],
                now[:10],
                user["username"],
                now,
                now,
            ),
        )


# Login throttling: after this many failures in the window, further attempts are blocked
# until the oldest failure ages out. Checked per-email (protects one account from a targeted
# guesser) and per-IP (slows a script trying many accounts from one source), independently.
LOGIN_LOCKOUT_WINDOW_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_WINDOW_MINUTES", "15"))
LOGIN_MAX_ATTEMPTS_PER_EMAIL = int(os.environ.get("LOGIN_MAX_ATTEMPTS_PER_EMAIL", "5"))
LOGIN_MAX_ATTEMPTS_PER_IP = int(os.environ.get("LOGIN_MAX_ATTEMPTS_PER_IP", "20"))


def login_lockout_remaining_seconds(conn: PostgresConnection, email: str, ip: str) -> int:
    """Returns how many seconds until the login attempt is allowed again, or 0 if it's allowed now."""
    window_start = (datetime.now(timezone.utc) - timedelta(minutes=LOGIN_LOCKOUT_WINDOW_MINUTES)).isoformat(timespec="seconds")
    checks = [(email, LOGIN_MAX_ATTEMPTS_PER_EMAIL, "target_id = ?")]
    if ip:
        checks.append((ip, LOGIN_MAX_ATTEMPTS_PER_IP, "ip = ?"))
    remaining = 0
    for value, limit, column_clause in checks:
        if not value:
            continue
        rows = conn.execute(
            f"SELECT created_at FROM audit_log WHERE action = 'auth.login.fail' AND {column_clause} AND created_at > ? ORDER BY created_at ASC",
            (value, window_start),
        ).fetchall()
        if len(rows) >= limit:
            oldest = datetime.fromisoformat(rows[0]["created_at"])
            free_at = oldest + timedelta(minutes=LOGIN_LOCKOUT_WINDOW_MINUTES)
            remaining = max(remaining, int((free_at - datetime.now(timezone.utc)).total_seconds()))
    return max(0, remaining)


def write_audit(
    conn: PostgresConnection,
    actor_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict | None = None,
    ip: str = "",
    user_agent: str = "",
) -> None:
    event = {
        "id": uid("audit"),
        "actor_id": actor_id,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "ip": ip,
        "user_agent": user_agent[:180],
        "metadata": json_dumps(metadata or {}),
        "created_at": utc_now(),
    }
    conn.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, target_type, target_id, ip, user_agent, metadata, created_at)
        VALUES (:id, :actor_id, :action, :target_type, :target_id, :ip, :user_agent, :metadata, :created_at)
        """,
        event,
    )
    stdout_event = {
        "source": "beenco-connect",
        "actorId": actor_id,
        "action": action,
        "targetType": target_type,
        "targetId": target_id,
        "ip": ip,
        "userAgent": user_agent[:180],
        "metadata": metadata or {},
        "createdAt": event["created_at"],
    }
    print(json_dumps(stdout_event), flush=True)


def user_count(conn: PostgresConnection) -> int:
    return conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]


SUPER_ADMIN_ROLES = {"super_admin"}
ADMIN_ROLES = {"super_admin", "admin"}
COMMAND_CENTER_ROLES = {"super_admin", "admin", "moderator", "it_admin", "security_analyst", "hr_admin", "hr_viewer"}
INTRANET_ADMIN_ROLES = {"super_admin", "admin", "moderator", "it_admin"}
SHUFFLE_ROLES = {"super_admin", "admin", "moderator", "it_admin", "security_analyst"}
HR_ROLES = {"super_admin", "admin", "hr_admin", "hr_viewer"}
HR_WRITE_ROLES = {"super_admin", "admin", "hr_admin"}


ALL_ROLES = ("super_admin", "admin", "moderator", "it_admin", "security_analyst", "hr_admin", "hr_viewer", "member", "guest")


def role_rank(role: str) -> int:
    """super_admin > admin > staff roles (moderator, IT, security, HR) > member/guest."""
    if role == "super_admin":
        return 3
    if role == "admin":
        return 2
    if role in COMMAND_CENTER_ROLES:
        return 1
    return 0


def can_manage_user(actor: dict, target_role: str) -> bool:
    """Whether `actor` may reset the password, suspend, or edit the profile of someone holding
    `target_role`. Only a super admin may act on an equal-or-higher role, so a moderator cannot
    take over an admin account and an admin cannot take over another admin."""
    return actor["role"] == "super_admin" or role_rank(actor["role"]) > role_rank(target_role)


def can_grant_role(actor_role: str, new_role: str) -> bool:
    """Whether `actor_role` may give someone `new_role` (when creating or editing an account)."""
    if new_role == "super_admin":
        return actor_role == "super_admin"
    if new_role in ("member", "guest"):
        return actor_role in INTRANET_ADMIN_ROLES
    return actor_role in ADMIN_ROLES


def validate_new_password(password: str) -> str:
    if len(password) < 10 or not any(ch.isupper() for ch in password) or not any(ch.islower() for ch in password) or not any(ch.isdigit() for ch in password):
        raise ValueError("Password must be at least 10 characters and include uppercase, lowercase, and a number.")
    return password


def has_command_center_access(user: dict) -> bool:
    return user["role"] in COMMAND_CENTER_ROLES


def can_access_portal(user: dict, portal_id: str) -> bool:
    role = user["role"]
    if portal_id == "intranet-admin":
        return role in INTRANET_ADMIN_ROLES
    if portal_id == "shuffle":
        return role in SHUFFLE_ROLES
    if portal_id == "hr":
        return role in HR_ROLES
    return False


def can_write_hr(user: dict) -> bool:
    return user["role"] in HR_WRITE_ROLES


def username_from_email(email: str) -> str:
    return slugify(email.split("@", 1)[0]).replace("-", ".")


def create_user(
    conn: PostgresConnection,
    email: str,
    display_name: str,
    password: str | None,
    provider: str,
    provider_subject: str = "",
    role: str = "member",
    title: str = "",
    department: str = "",
    username: str | None = None,
) -> dict:
    normalized_email = email.strip().lower()
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("A valid company email is required.")
    if role not in ("super_admin", "admin", "moderator", "member", "guest", "it_admin", "security_analyst", "hr_admin", "hr_viewer"):
        role = "member"
    password_hash = password_salt = None
    if password:
        password_hash, password_salt = hash_password(password)
    user_id = uid("u")
    base_username = (username.strip() if username else "").lower() or username_from_email(normalized_email)
    suffix = 1
    candidate = base_username
    while conn.execute("SELECT 1 FROM users WHERE username = ?", (candidate,)).fetchone():
        suffix += 1
        candidate = f"{base_username}.{suffix}"
    # A crash between the user row and channel membership would otherwise create an account
    # that exists but can't see #general — effectively locked out with no obvious cause.
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO users (
              id, email, username, display_name, password_hash, password_salt, provider,
              provider_subject, title, department, role, presence, status_text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'online', '', ?)
            """,
            (user_id, normalized_email, candidate, display_name.strip() or candidate, password_hash, password_salt, provider, provider_subject, title.strip(), department.strip(), role, utc_now()),
        )
        join_public_channels(conn, user_id)
        write_audit(conn, user_id, "user.created", "user", user_id, {"provider": provider, "role": role})
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def join_public_channels(conn: PostgresConnection, user_id: str) -> None:
    now = utc_now()
    channels = conn.execute("SELECT id FROM channels WHERE type = 'public'").fetchall()
    for channel in channels:
        conn.execute(
            "INSERT OR IGNORE INTO channel_members (channel_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
            (channel["id"], user_id, now),
        )


def create_session(conn: PostgresConnection, user_id: str) -> str:
    token = secrets.token_urlsafe(36)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    now = utc_now()
    with conn.transaction():
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now, expires.isoformat(timespec="seconds")),
        )
        conn.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (now, user_id))
    return token


def is_recent(value: str | None, seconds: int = 300) -> bool:
    if not value:
        return False
    try:
        seen = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - seen <= timedelta(seconds=seconds)


def is_user_online(row: dict) -> bool:
    # Presence is purely automatic now: online means "active in the app within the last 5
    # minutes," derived straight from last_seen_at. There is no manual online/away/dnd/offline
    # picker to fall out of sync with reality anymore.
    return is_recent(row["last_seen_at"], 300)


def row_to_user(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "username": row["username"],
        "displayName": row["display_name"],
        "title": row["title"] or "",
        "department": row["department"] or "",
        "role": row["role"],
        "presence": "online" if is_user_online(row) else "offline",
        "statusText": row["status_text"] or "",
        "statusEmoji": row["status_emoji"] or "",
        "avatarUrl": row["avatar_url"] or "",
        "provider": row["provider"],
        "isActive": bool(row["is_active"]),
        "lastSeenAt": row["last_seen_at"] or "",
        "isOnline": is_user_online(row),
        "groups": [row["role"]],
    }


def serialize_channel(conn: PostgresConnection, row: dict, user: dict | None = None) -> dict:
    members = conn.execute("SELECT user_id FROM channel_members WHERE channel_id = ?", (row["id"],)).fetchall()
    member_ids = [member["user_id"] for member in members]
    member_placeholders = ",".join("?" for _ in member_ids) or "''"
    users_by_id = {
        item["id"]: item
        for item in conn.execute(
            f"SELECT * FROM users WHERE id IN ({member_placeholders})",
            member_ids,
        ).fetchall()
    }
    unread = mentions = 0
    last_read_message_id = ""
    if user:
        read_state = conn.execute(
            "SELECT last_read_message_id, updated_at FROM channel_read_states WHERE channel_id = ? AND user_id = ?",
            (row["id"], user["id"]),
        ).fetchone()
        if read_state:
            last_read_message_id = read_state["last_read_message_id"] or ""
        else:
            membership = conn.execute(
                "SELECT last_read_message_id FROM channel_members WHERE channel_id = ? AND user_id = ?",
                (row["id"], user["id"]),
            ).fetchone()
            last_read_message_id = membership["last_read_message_id"] if membership and membership["last_read_message_id"] else ""
        last_read = None
        if last_read_message_id:
            last_read = conn.execute("SELECT created_at FROM messages WHERE id = ?", (last_read_message_id,)).fetchone()
        params: list = [row["id"], user["id"]]
        after_clause = ""
        if last_read:
            after_clause = "AND created_at > ?"
            params.append(last_read["created_at"])
        elif read_state and read_state["updated_at"]:
            after_clause = "AND created_at > ?"
            params.append(read_state["updated_at"])
        unread_rows = conn.execute(
            f"""
            SELECT body FROM messages
            WHERE channel_id = ? AND parent_id IS NULL AND deleted_at IS NULL AND author_id != ? {after_clause}
            """,
            params,
        ).fetchall()
        unread = len(unread_rows)
        mention_tokens = [f"@{user['username']}".lower(), "@here", "@channel"]
        mentions = sum(1 for item in unread_rows if any(token in item["body"].lower() for token in mention_tokens))
    active_typers = []
    for item in conn.execute(
        "SELECT user_id, typing_until FROM channel_members WHERE channel_id = ? AND COALESCE(typing_until, '') > ?",
        (row["id"], utc_now()),
    ).fetchall():
        if not user or item["user_id"] != user["id"]:
            active_typers.append(item["user_id"])
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "type": row["type"],
        "topic": row["topic"] or "",
        "description": row["description"] or "",
        "createdBy": row["created_by"],
        "members": member_ids,
        "ownerIds": list(set([member["user_id"] for member in conn.execute("SELECT user_id FROM channel_members WHERE channel_id = ? AND role = 'owner'", (row["id"],)).fetchall()] + ([row["created_by"]] if row["created_by"] else []))),
        "unread": unread,
        "mentions": mentions,
        "lastReadMessageId": last_read_message_id,
        "onlineMemberCount": sum(1 for member_id in member_ids if member_id in users_by_id and is_user_online(users_by_id[member_id])),
        "typingUserIds": active_typers,
        "legalHold": bool(row["legal_hold"]),
        "retentionDays": row["retention_days"],
        "locked": bool(row["locked"]),
        "pinned": [],
        "createdAt": row["created_at"],
    }


def visible_channel_ids(conn: PostgresConnection, user: dict) -> list[str]:
    # Admins and super admins can see every channel, private group and DM.
    if user["role"] in ADMIN_ROLES:
        return [row["id"] for row in conn.execute("SELECT id FROM channels").fetchall()]
    # All other users ONLY see public channels and private/DM channels where they are a member:
    rows = conn.execute(
        """
        SELECT DISTINCT c.id FROM channels c
        LEFT JOIN channel_members cm ON cm.channel_id = c.id AND cm.user_id = ?
        WHERE c.type = 'public' OR cm.user_id = ?
        """,
        (user["id"], user["id"]),
    ).fetchall()
    return [row["id"] for row in rows]


def can_access_channel(conn: PostgresConnection, user: dict, channel_id: str, write: bool = False) -> bool:
    channel = conn.execute("SELECT type FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if not channel:
        return False
    if channel["type"] == "public":
        return True
    if channel["type"] == "private" and user["role"] in ADMIN_ROLES:
        return True
    # Admins can read any DM, but only participants can post in one.
    if channel["type"] in ("dm", "group_dm") and user["role"] in ADMIN_ROLES and not write:
        return True
    return bool(
        conn.execute(
            "SELECT 1 FROM channel_members WHERE channel_id = ? AND user_id = ?",
            (channel_id, user["id"]),
        ).fetchone()
    )


def is_channel_owner(conn: PostgresConnection, user: dict, channel_id: str) -> bool:
    if user["role"] in ADMIN_ROLES:
        return True
    channel = conn.execute("SELECT created_by FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if channel and channel["created_by"] == user["id"]:
        return True
    return bool(
        conn.execute(
            "SELECT 1 FROM channel_members WHERE channel_id = ? AND user_id = ? AND role = 'owner'",
            (channel_id, user["id"]),
        ).fetchone()
    )


def serialize_message(conn: PostgresConnection, row: dict) -> dict:
    reactions: dict[str, list[str]] = {}
    for reaction in conn.execute("SELECT emoji, user_id FROM reactions WHERE message_id = ?", (row["id"],)).fetchall():
        reactions.setdefault(reaction["emoji"], []).append(reaction["user_id"])
    attachments = []
    if not row["deleted_at"]:
        attachments = [item["file_id"] for item in conn.execute("SELECT file_id FROM message_attachments WHERE message_id = ?", (row["id"],)).fetchall()]
    return {
        "id": row["id"],
        "channelId": row["channel_id"],
        "authorId": row["author_id"],
        "parentId": row["parent_id"],
        "body": row["body"],
        "editedAt": row["edited_at"],
        "deletedAt": row["deleted_at"],
        "editHistory": json_loads(row["edit_history"], []),
        "createdAt": row["created_at"],
        "reactions": reactions,
        "attachments": attachments,
        "isUrgent": bool(row.get("is_urgent", 0)),
        "acknowledgedAt": row.get("acknowledged_at", "") or "",
        "acknowledgedBy": row.get("acknowledged_by", "") or "",
    }


def serialize_file(row: dict) -> dict:
    mime = normalized_mime(row.get("mime"))
    ext = Path(row.get("original_name") or "").suffix.lower()
    is_previewable = (
        mime.startswith(("image/", "audio/", "video/", "text/"))
        or mime in ("application/pdf", "application/json", "application/javascript", "application/xml", "application/x-yaml")
        or ext in (".pdf", ".txt", ".md", ".json", ".csv", ".js", ".ts", ".py", ".html", ".css", ".xml", ".yaml", ".yml", ".sql", ".log")
    )
    return {
        "id": row["id"],
        "channelId": row["channel_id"],
        "uploaderId": row["uploader_id"],
        "originalName": row["original_name"],
        "mime": row["mime"],
        "sizeBytes": row["size_bytes"],
        "status": row["status"],
        "checksum": row["checksum"] or "",
        "version": row["version"],
        "storageKey": row["storage_key"],
        "kind": row["kind"] or "file",
        "duration": row["duration"] or 0,
        "waveform": json_loads(row["waveform"], []),
        "previewUrl": row["preview_data_url"] or (f"/api/files/{row['id']}/preview" if is_previewable and row["status"] == "available" else ""),
        "extractedText": row["extracted_text"] or "",
        "createdAt": row["created_at"],
    }


def serialize_event(conn: PostgresConnection, row: dict) -> dict:
    attendees = {
        item["user_id"]: item["response"]
        for item in conn.execute("SELECT user_id, response FROM event_attendees WHERE event_id = ?", (row["id"],)).fetchall()
    }
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"] or "",
        "startsAt": row["starts_at"],
        "endsAt": row["ends_at"],
        "location": row["location"] or "",
        "livekitRoom": row["livekit_room"] or "",
        "createdBy": row["created_by"],
        "targetGroupId": row["target_group_id"],
        "clientName": row.get("client_name") or "",
        "eventType": row.get("event_type") or "meeting",
        "advanceNoticeDays": row.get("advance_notice_days") if row.get("advance_notice_days") is not None else 4,
        "lastAlertedDate": row.get("last_alerted_date") or "",
        "attendees": attendees,
        "createdAt": row["created_at"],
    }


def serialize_weekly_report(row: dict) -> dict:
    if not row:
        return {}
    return {
        "id": row["id"],
        "userId": row["user_id"] or "",
        "reportType": row.get("report_type") or "user",
        "trigger": row.get("report_trigger") or "manual",
        "generatedBy": row.get("generated_by") or "",
        "weekStart": row["week_start"],
        "weekEnd": row["week_end"],
        "title": row["title"],
        "summary": row["summary"],
        "collaborators": json_loads(row.get("collaborators") or "[]", []),
        "channelsInvolved": json_loads(row.get("channels_involved") or "[]", []),
        "metrics": json_loads(row.get("metrics") or "{}", {}),
        "deliveredToUserAt": row.get("delivered_to_user_at") or "",
        "deliveredToAdminAt": row.get("delivered_to_admin_at") or "",
        "createdAt": row["created_at"],
    }


def serialize_notification(row: dict) -> dict:
    d = dict(row)
    d["readAt"] = d.get("read_at")
    d["createdAt"] = d.get("created_at")
    d["userId"] = d.get("user_id")
    return d


def serialize_x_mention(row: dict) -> dict:
    return {
        "tweetId": row["tweet_id"],
        "monitoredUsernames": json_loads(row.get("monitored_usernames") or "[]", []),
        "tweetUrl": row.get("tweet_url") or "",
        "text": row.get("tweet_text") or "",
        "authorUsername": row.get("author_username") or "",
        "authorName": row.get("author_name") or row.get("author_username") or "Unknown",
        "authorProfilePicture": row.get("author_profile_picture") or "",
        "likeCount": int(row.get("like_count") or 0),
        "replyCount": int(row.get("reply_count") or 0),
        "retweetCount": int(row.get("retweet_count") or 0),
        "quoteCount": int(row.get("quote_count") or 0),
        "viewCount": int(row.get("view_count") or 0),
        "createdAt": row.get("tweet_created_at") or "",
        "discoveredAt": row.get("discovered_at") or "",
    }


def serialize_remote_assist(conn: PostgresConnection, row: dict) -> dict:
    return {
        "id": row["id"],
        "requesterId": row["requester_id"],
        "targetUserId": row["target_user_id"],
        "targetHost": row["target_host"] or "",
        "reason": row["reason"] or "",
        "status": row["status"],
        "createdAt": row["created_at"],
        "respondedAt": row["responded_at"] or "",
        "endedAt": row["ended_at"] or "",
        "updatedAt": row["updated_at"],
        "rdpReady": bool(row["target_host"] and row["status"] == "accepted"),
    }


def serialize_announcement(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "body": row["body"],
        "authorId": row["author_id"],
        "targetGroupId": row["target_group_id"],
        "pinnedUntil": row["pinned_until"],
        "createdAt": row["created_at"],
    }


def serialize_audit(row: dict) -> dict:
    return {
        "id": row["id"],
        "actorId": row["actor_id"],
        "action": row["action"],
        "targetType": row["target_type"],
        "targetId": row["target_id"],
        "ip": row["ip"] or "",
        "userAgent": row["user_agent"] or "",
        "metadata": json_loads(row["metadata"], {}),
        "createdAt": row["created_at"],
    }


def serialize_employee(row: dict) -> dict:
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "adObjectGuid": row["ad_object_guid"] or "",
        "fullName": row["full_name"],
        "cnic": row["cnic"] or "",
        "email": row["email"] or "",
        "phone": row["phone"] or "",
        "department": row["department"] or "",
        "designation": row["designation"] or "",
        "employmentType": row["employment_type"] or "full-time",
        "status": row["status"] or "active",
        "joiningDate": row["joining_date"] or "",
        "exitDate": row["exit_date"] or "",
        "managerId": row["manager_id"],
        "location": row["location"] or "",
        "photoUrl": row["photo_url"] or "",
        "biometricUserId": row["biometric_user_id"] or "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"] or "",
    }


def serialize_asset(row: dict) -> dict:
    return {
        "id": row["id"],
        "assetTag": row["asset_tag"],
        "category": row["category"],
        "model": row["model"] or "",
        "serial": row["serial"] or "",
        "status": row["status"] or "available",
        "purchaseDate": row["purchase_date"] or "",
        "value": row["value"] or 0,
        "warrantyExpiry": row["warranty_expiry"] or "",
        "condition": row["condition"] or "good",
        "notes": row["notes"] or "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"] or "",
    }


def serialize_assignment(row: dict) -> dict:
    return {
        "id": row["id"],
        "assetId": row["asset_id"],
        "employeeId": row["employee_id"],
        "assignedAt": row["assigned_at"],
        "expectedReturn": row["expected_return"] or "",
        "returnedAt": row["returned_at"] or "",
        "conditionOut": row["condition_out"] or "",
        "conditionIn": row["condition_in"] or "",
        "assignedBy": row["assigned_by"],
        "createdAt": row["created_at"],
    }


def serialize_attendance(row: dict) -> dict:
    return {
        "id": row["id"],
        "employeeId": row["employee_id"],
        "date": row["date"],
        "firstIn": row["first_in"] or "",
        "lastOut": row["last_out"] or "",
        "workedMinutes": row["worked_minutes"] or 0,
        "status": row["status"] or "present",
        "correctedBy": row["corrected_by"],
        "correctionReason": row["correction_reason"] or "",
        "createdAt": row["created_at"],
    }


def serialize_leave(row: dict) -> dict:
    return {
        "id": row["id"],
        "employeeId": row["employee_id"],
        "leaveType": row["leave_type"],
        "startDate": row["start_date"],
        "endDate": row["end_date"],
        "days": row["days"],
        "reason": row["reason"] or "",
        "status": row["status"],
        "approverId": row["approver_id"],
        "decidedAt": row["decided_at"] or "",
        "createdAt": row["created_at"],
    }


def serialize_task(row: dict) -> dict:
    return {
        "id": row["id"],
        "employeeId": row["employee_id"],
        "kind": row["kind"],
        "title": row["title"],
        "assigneeRole": row["assignee_role"] or "",
        "dueDate": row["due_date"] or "",
        "status": row["status"],
        "completedBy": row["completed_by"],
        "completedAt": row["completed_at"] or "",
        "createdAt": row["created_at"],
    }


def serialize_workspace_task(row: dict) -> dict:
    return {
        "id": row["id"],
        "channelId": row["channel_id"],
        "assigneeId": row["assignee_id"],
        "creatorId": row["creator_id"],
        "title": row["title"],
        "subject": row["subject"] or "",
        "referenceLinks": json_loads(row["reference_links"], []),
        "deadline": row["deadline"] or "",
        "tags": json_loads(row["tags"], []),
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def check_advance_calendar_reminders(conn: PostgresConnection) -> int:
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    events = conn.execute("SELECT * FROM events WHERE starts_at > ?", (now.isoformat(),)).fetchall()
    alerts_created = 0
    for ev in events:
        try:
            starts_str = ev["starts_at"].replace("Z", "+00:00")
            starts_dt = datetime.fromisoformat(starts_str)
        except Exception:
            continue
        days_until = (starts_dt.date() - now.date()).days
        advance_days = int(ev.get("advance_notice_days") if ev.get("advance_notice_days") is not None else 4)
        if 1 <= days_until <= advance_days:
            last_alerted = ev.get("last_alerted_date") or ""
            if last_alerted != today_str:
                client_name = ev.get("client_name") or ""
                ev_type = ev.get("event_type") or "meeting"
                if client_name:
                    title = f"📅 Upcoming: Client meeting with {client_name} (in {days_until} day{'s' if days_until > 1 else ''})"
                    snippet = f"{client_name} is scheduled to meet for '{ev['title']}' on {starts_dt.strftime('%a, %b %d at %I:%M %p')}. Plan your schedule accordingly."
                else:
                    title = f"📅 Upcoming: {ev['title']} (in {days_until} day{'s' if days_until > 1 else ''})"
                    snippet = f"Scheduled for {starts_dt.strftime('%a, %b %d at %I:%M %p')}. Plan your schedule accordingly."

                attendee_rows = conn.execute("SELECT user_id FROM event_attendees WHERE event_id = ?", (ev["id"],)).fetchall()
                target_user_ids = {r["user_id"] for r in attendee_rows}
                target_user_ids.add(ev["created_by"])

                for uid_target in target_user_ids:
                    existing = conn.execute(
                        "SELECT id FROM notifications WHERE user_id = ? AND type = 'calendar_advance' AND payload LIKE ? AND created_at >= ?",
                        (uid_target, f'%"{ev["id"]}"%', today_str),
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            "INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (?, ?, 'calendar_advance', ?, ?)",
                            (
                                uid("note"),
                                uid_target,
                                json_dumps({
                                    "eventId": ev["id"],
                                    "eventTitle": ev["title"],
                                    "clientName": client_name,
                                    "eventType": ev_type,
                                    "daysUntil": days_until,
                                    "startsAt": ev["starts_at"],
                                    "location": ev.get("location", ""),
                                    "snippet": snippet,
                                }),
                                utc_now(),
                            ),
                        )
                        alerts_created += 1
                conn.execute("UPDATE events SET last_alerted_date = ? WHERE id = ?", (today_str, ev["id"]))
    return alerts_created


try:
    WEEKLY_REPORT_TZ = ZoneInfo(os.environ.get("WEEKLY_REPORT_TIMEZONE", "Asia/Karachi").strip() or "Asia/Karachi")
except Exception:
    WEEKLY_REPORT_TZ = timezone(timedelta(hours=5))
WEEKLY_REPORT_HOUR = int(os.environ.get("WEEKLY_REPORT_HOUR", "9"))

WEEKLY_REPORT_JOB: dict[str, Any] = {
    "running": False,
    "scope": "",
    "startedAt": "",
    "finishedAt": "",
    "lastError": "",
    "lastReportId": "",
}
WEEKLY_REPORT_LOCK = threading.Lock()


def report_window_iso(start_dt: datetime, end_dt: datetime) -> tuple[str, str]:
    # Stored timestamps are UTC ISO strings, so compare in UTC.
    return (
        start_dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
        end_dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )


def report_period_labels(start_dt: datetime, end_dt: datetime) -> tuple[str, str]:
    start_local = start_dt.astimezone(WEEKLY_REPORT_TZ)
    end_local = end_dt.astimezone(WEEKLY_REPORT_TZ)
    # A window ending exactly at midnight covers the previous day.
    if (end_local.hour, end_local.minute, end_local.second) == (0, 0, 0):
        end_local -= timedelta(seconds=1)
    return start_local.strftime("%b %d, %Y"), end_local.strftime("%b %d, %Y")


def previous_calendar_week(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Monday 00:00 to the following Monday 00:00 (report timezone) for the week before `now`."""
    local_now = (now or datetime.now(timezone.utc)).astimezone(WEEKLY_REPORT_TZ)
    this_monday = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=local_now.weekday())
    return this_monday - timedelta(days=7), this_monday


def notify_report_admins(conn: PostgresConnection, payload: dict) -> None:
    admins = conn.execute("SELECT id FROM users WHERE role IN ('super_admin', 'admin') AND is_active = 1").fetchall()
    with conn.transaction():
        for admin in admins:
            conn.execute(
                "INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (?, ?, 'ai_report_admin', ?, ?)",
                (uid("note"), admin["id"], json_dumps(payload), utc_now()),
            )


def collect_user_week_activity(conn: PostgresConnection, user: dict, start_iso: str, end_iso: str) -> dict:
    user_id = user["id"]
    messages = conn.execute(
        """
        SELECT m.*, c.name AS channel_name, c.type AS channel_type, c.slug AS channel_slug
        FROM messages m
        JOIN channels c ON c.id = m.channel_id
        WHERE m.author_id = ? AND m.created_at >= ? AND m.created_at <= ? AND m.deleted_at IS NULL
        ORDER BY m.created_at ASC
        """,
        (user_id, start_iso, end_iso),
    ).fetchall()

    dm_channels = conn.execute(
        """
        SELECT c.id, c.type, cm_other.user_id AS peer_id, u.display_name AS peer_name, u.username AS peer_handle
        FROM channels c
        JOIN channel_members cm_me ON cm_me.channel_id = c.id AND cm_me.user_id = ?
        JOIN channel_members cm_other ON cm_other.channel_id = c.id AND cm_other.user_id != ?
        JOIN users u ON u.id = cm_other.user_id
        WHERE c.type IN ('dm', 'group_dm')
        """,
        (user_id, user_id),
    ).fetchall()

    collaborator_stats = {}
    for dmc in dm_channels:
        peer_name = dmc["peer_name"]
        peer_id = dmc["peer_id"]
        dm_msgs = conn.execute(
            """
            SELECT m.body, m.author_id, m.created_at
            FROM messages m
            WHERE m.channel_id = ? AND m.created_at >= ? AND m.created_at <= ? AND m.deleted_at IS NULL
            ORDER BY m.created_at ASC
            """,
            (dmc["id"], start_iso, end_iso),
        ).fetchall()
        if dm_msgs:
            my_count = sum(1 for m in dm_msgs if m["author_id"] == user_id)
            peer_count = len(dm_msgs) - my_count
            snippets = [m["body"][:90] for m in dm_msgs if m["author_id"] == user_id][:3]
            collaborator_stats[peer_id] = {
                "id": peer_id,
                "name": peer_name,
                "handle": dmc["peer_handle"],
                "totalMessages": len(dm_msgs),
                "sentByMe": my_count,
                "received": peer_count,
                "topics": snippets,
            }

    channel_stats = {}
    hashtags_found = []
    for msg in messages:
        cname = msg["channel_name"]
        if cname not in channel_stats:
            channel_stats[cname] = {
                "channelId": msg["channel_id"],
                "type": msg["channel_type"],
                "count": 0,
                "snippets": [],
            }
        channel_stats[cname]["count"] += 1
        if len(channel_stats[cname]["snippets"]) < 3:
            channel_stats[cname]["snippets"].append(msg["body"][:100])
        tags = re.findall(r"#([A-Za-z0-9_\-]+)", msg["body"])
        hashtags_found.extend(tags)

    tasks = conn.execute(
        "SELECT * FROM workspace_tasks WHERE (creator_id = ? OR assignee_id = ?) AND created_at >= ? AND created_at <= ?",
        (user_id, user_id, start_iso, end_iso),
    ).fetchall()
    files = conn.execute(
        "SELECT * FROM files WHERE uploader_id = ? AND created_at >= ? AND created_at <= ?",
        (user_id, start_iso, end_iso),
    ).fetchall()

    collabs_list = list(collaborator_stats.values())
    collabs_list.sort(key=lambda c: c["totalMessages"], reverse=True)
    chans_list = list(channel_stats.items())
    chans_list.sort(key=lambda x: x[1]["count"], reverse=True)

    collab_count = len(collabs_list)
    chan_count = len(chans_list)
    msg_count = len(messages)
    tags_unique = list(dict.fromkeys(hashtags_found))

    collab_lines = []
    if collabs_list:
        for c in collabs_list[:6]:
            snips = " • ".join(f'"{s}"' for s in c["topics"]) if c["topics"] else "Direct collaboration"
            collab_lines.append(f"- **{c['name']}** (@{c['handle']}): {c['totalMessages']} direct messages exchanged ({c['sentByMe']} sent). *Focus:* {snips}")
    else:
        collab_lines.append("- No 1-on-1 direct message conversations recorded during this period.")

    chan_lines = []
    if chans_list:
        for cname, cdata in chans_list[:6]:
            snips = " • ".join(f'"{s}"' for s in cdata["snippets"]) if cdata["snippets"] else "General group participation"
            chan_lines.append(f"- **#{cname}** ({cdata['type']}): {cdata['count']} messages posted. *Contributions:* {snips}")
    else:
        chan_lines.append("- No public or private group channel messages posted this week.")

    task_lines = []
    if tasks:
        for t in tasks[:6]:
            task_lines.append(f"- **{t['title']}** [{t['status'].upper()}] - {t.get('subject', '') or 'Workspace action item'}")
    else:
        task_lines.append("- No new tasks assigned or created this week.")

    file_lines = []
    if files:
        for f in files[:6]:
            file_lines.append(f"- **{f['original_name']}** ({f['kind']})")
    else:
        file_lines.append("- No new files uploaded this week.")

    tag_str = ", ".join(f"#{t}" for t in tags_unique[:6]) if tags_unique else "General workspace coordination"

    return {
        "msg_count": msg_count,
        "chan_count": chan_count,
        "collab_count": collab_count,
        "collabs_list": collabs_list,
        "chans_list": chans_list,
        "tasks": tasks,
        "files": files,
        "tags_unique": tags_unique,
        "tag_str": tag_str,
        "collab_lines": collab_lines,
        "chan_lines": chan_lines,
        "task_lines": task_lines,
        "file_lines": file_lines,
        "is_active": bool(msg_count or tasks or files),
    }


def generate_weekly_user_report(conn: PostgresConnection, user_id: str, week_start_dt: datetime | None = None, week_end_dt: datetime | None = None, generated_by: str = "") -> dict:
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise ValueError("User not found")
    week_end_dt = week_end_dt or datetime.now(timezone.utc)
    week_start_dt = week_start_dt or (week_end_dt - timedelta(days=7))
    start_iso, end_iso = report_window_iso(week_start_dt, week_end_dt)
    start_str, end_str = report_period_labels(week_start_dt, week_end_dt)

    activity = collect_user_week_activity(conn, user, start_iso, end_iso)
    msg_count = activity["msg_count"]
    chan_count = activity["chan_count"]
    collab_count = activity["collab_count"]
    collabs_list = activity["collabs_list"]
    chans_list = activity["chans_list"]
    tasks = activity["tasks"]
    files = activity["files"]
    tags_unique = activity["tags_unique"]
    tag_str = activity["tag_str"]
    collab_lines = activity["collab_lines"]
    chan_lines = activity["chan_lines"]
    task_lines = activity["task_lines"]
    file_lines = activity["file_lines"]
    title = f"Weekly Activity Digest: {user['display_name']} ({start_str} - {end_str})"

    llm_narrative = None
    if ENABLE_BEDROCK_API:
        llm_prompt = f"""You are the Executive AI Assistant for Beenco Connect. Synthesize the weekly communication & activity report for {user['display_name']} (@{user['username']}) for {start_str} to {end_str}.

Raw Workspace Intelligence:
- Employee: {user['display_name']} (@{user['username']}) - Role: {user['role']}
- Total messages sent: {msg_count} across {chan_count} channels
- 1-on-1 Direct Message Collaborators:
{chr(10).join(collab_lines)}
- Group & Channel Discussions:
{chr(10).join(chan_lines)}
- Deliverables & Tasks:
{chr(10).join(task_lines)}
- Shared Files:
{chr(10).join(file_lines)}
- Core Topics & Hashtags: {tag_str}

Please generate an executive, comprehensive weekly activity briefing in Markdown format with the following sections:
### 📊 Executive Summary
### 👥 1-on-1 Direct Communications (Who they talked to and discussion context)
### 💬 Group & Channel Discussions (Topics, decisions, and group collaborations)
### 📋 Deliverables, Tasks & Shared Files
### 📈 Strategic Takeaways & Collaboration Assessment

Ensure the report is professional, concise, insightful, and clearly articulates who the employee collaborated with, what topics were addressed, and accomplishments achieved.
IMPORTANT: Do NOT wrap your entire answer in ```markdown or ``` code fences. Output clean raw markdown directly."""
        llm_narrative = invoke_bedrock_llm(llm_prompt, system_prompt="You are an expert executive reporting AI. Provide clear, structured, and insightful raw markdown weekly summaries. Do not wrap entire output in backticks.")

    if llm_narrative:
        summary_md = f"""# 🤖 AI Weekly Activity & Communication Digest
**Employee:** {user['display_name']} (@{user['username']}) | **Role:** {user['role']}
**Reporting Period:** {start_str} to {end_str}
**AI Model:** Amazon Bedrock ({AWS_BEDROCK_MODEL})

---

{llm_narrative}

---

### 📈 Activity & Collaboration Metrics
- **Total Messages Sent:** {msg_count}
- **Active Team Channels:** {chan_count}
- **Colleagues Interacted With:** {collab_count}
- **Top Collaborator:** {collabs_list[0]['name'] if collabs_list else 'N/A'}
- **Tasks & Commitments:** {len(tasks)}
- **Files Shared:** {len(files)}
"""
        used_model = AWS_BEDROCK_MODEL
        used_provider = "Amazon Bedrock"
    else:
        used_model = LOCAL_FALLBACK_MODEL
        used_provider = "Local Fallback"
        summary_md = f"""# 🤖 AI Weekly Activity & Communication Digest
**Employee:** {user['display_name']} (@{user['username']}) | **Role:** {user['role']}
**Reporting Period:** {start_str} to {end_str}
**AI Model:** Local Fallback ({LOCAL_FALLBACK_MODEL})

---

### 📊 Executive Summary
During this week, **{user['display_name']}** contributed **{msg_count}** messages across **{chan_count}** channels and coordinated with **{collab_count}** colleagues in 1-on-1 conversations. Key topics and project initiatives included: {tag_str}.

---

### 👥 1-on-1 Direct Communications (Who they talked to)
{chr(10).join(collab_lines)}

---

### 💬 Group & Channel Discussions (What was discussed in groups)
{chr(10).join(chan_lines)}

---

### 📋 Deliverables, Tasks & Shared Files
**Tasks Handled:**
{chr(10).join(task_lines)}

**Shared Documents & Media:**
{chr(10).join(file_lines)}

---

### 📈 Activity & Collaboration Metrics
- **Total Messages Sent:** {msg_count}
- **Active Team Channels:** {chan_count}
- **Colleagues Interacted With:** {collab_count}
- **Top Collaborator:** {collabs_list[0]['name'] if collabs_list else 'N/A'}
- **Tasks & Commitments:** {len(tasks)}
- **Files Shared:** {len(files)}
"""

    report_id = uid("rep")
    metrics_json = json_dumps({
        "totalMessages": msg_count,
        "channelsCount": chan_count,
        "collaboratorsCount": collab_count,
        "tasksCount": len(tasks),
        "filesCount": len(files),
        "topCollaborator": collabs_list[0]['name'] if collabs_list else "",
        "hashtags": tags_unique,
        "aiModel": used_model,
        "aiProvider": used_provider,
    })
    collabs_json = json_dumps([{"id": c["id"], "name": c["name"], "handle": c["handle"], "messages": c["totalMessages"]} for c in collabs_list])
    chans_json = json_dumps([{"name": k, "type": v["type"], "count": v["count"]} for k, v in chans_list])

    conn.execute(
        """
        INSERT INTO weekly_activity_reports (id, user_id, report_type, report_trigger, generated_by, week_start, week_end, title, summary, collaborators, channels_involved, metrics, delivered_to_user_at, delivered_to_admin_at, created_at)
        VALUES (?, ?, 'user', 'manual', ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
        """,
        (report_id, user_id, generated_by, start_iso, end_iso, title, summary_md, collabs_json, chans_json, metrics_json, utc_now(), utc_now()),
    )

    # Reports are admin-only: employees are never notified.
    notify_report_admins(conn, {
        "reportId": report_id,
        "userId": user_id,
        "userName": user["display_name"],
        "title": f"📊 AI Weekly Report: {user['display_name']}",
        "weekStart": start_iso,
        "weekEnd": end_iso,
        "snippet": f"Weekly digest generated for {user['display_name']}: {msg_count} messages, {collab_count} collaborators.",
    })

    row = conn.execute("SELECT * FROM weekly_activity_reports WHERE id = ?", (report_id,)).fetchone()
    return serialize_weekly_report(row)


def md_cell(value: Any) -> str:
    return str(value).replace("|", "/").replace("\n", " ")


def generate_combined_weekly_report(conn: PostgresConnection, week_start_dt: datetime, week_end_dt: datetime, trigger: str = "manual", generated_by: str = "") -> dict:
    start_iso, end_iso = report_window_iso(week_start_dt, week_end_dt)
    start_str, end_str = report_period_labels(week_start_dt, week_end_dt)
    users = conn.execute("SELECT * FROM users WHERE is_active = 1 ORDER BY display_name ASC").fetchall()

    active: list[tuple[dict, dict]] = []
    inactive: list[dict] = []
    for member in users:
        activity = collect_user_week_activity(conn, member, start_iso, end_iso)
        if activity["is_active"]:
            active.append((member, activity))
        else:
            inactive.append(member)

    ai_used = False
    sections = []
    table_rows = []
    user_metrics = []
    channel_totals: dict[str, dict] = {}
    totals = {"messages": 0, "tasks": 0, "files": 0}

    for member, act in active:
        name = member["display_name"]
        handle = member["username"]
        totals["messages"] += act["msg_count"]
        totals["tasks"] += len(act["tasks"])
        totals["files"] += len(act["files"])
        for cname, cdata in act["chans_list"]:
            entry = channel_totals.setdefault(cname, {"name": cname, "type": cdata["type"], "count": 0})
            entry["count"] += cdata["count"]

        narrative = None
        if ENABLE_BEDROCK_API:
            narrative = invoke_bedrock_llm(
                f"""Summarize {name} (@{handle}, role: {member['role']}) work activity for {start_str} to {end_str}.

Data:
- Messages sent: {act['msg_count']} across {act['chan_count']} channels
- 1-on-1 conversations:
{chr(10).join(act['collab_lines'])}
- Channel contributions:
{chr(10).join(act['chan_lines'])}
- Tasks:
{chr(10).join(act['task_lines'])}
- Files:
{chr(10).join(act['file_lines'])}
- Topics/hashtags: {act['tag_str']}

Write 3 to 5 concise markdown bullet points covering what they worked on, who they collaborated with, and notable outcomes. No headings, no intro sentence, no code fences.""",
                system_prompt="You write short, factual employee activity summaries for an executive team report. Output raw markdown bullets only.",
            )
        if narrative:
            ai_used = True
        else:
            narrative = "\n".join([
                f"- Sent **{act['msg_count']}** messages across **{act['chan_count']}** channels; worked with **{act['collab_count']}** colleagues in direct messages.",
                *act["chan_lines"][:3],
                *act["collab_lines"][:3],
                *(act["task_lines"][:3] if act["tasks"] else []),
                *(act["file_lines"][:3] if act["files"] else []),
            ])

        sections.append(
            f"## 👤 {name} (@{handle})\n"
            f"**Role:** {member['role']} | **Messages:** {act['msg_count']} | **Channels:** {act['chan_count']} | "
            f"**Colleagues:** {act['collab_count']} | **Tasks:** {len(act['tasks'])} | **Files:** {len(act['files'])}\n\n"
            f"{narrative}"
        )
        table_rows.append(f"| {md_cell(name)} | {act['msg_count']} | {act['chan_count']} | {act['collab_count']} | {len(act['tasks'])} | {len(act['files'])} |")
        user_metrics.append({
            "id": member["id"],
            "name": name,
            "handle": handle,
            "messages": act["msg_count"],
            "channels": act["chan_count"],
            "collaborators": act["collab_count"],
            "tasks": len(act["tasks"]),
            "files": len(act["files"]),
        })

    top_channels = sorted(channel_totals.values(), key=lambda c: c["count"], reverse=True)
    overview = None
    if ENABLE_BEDROCK_API and active:
        stats = "\n".join(
            f"- {u['name']} (@{u['handle']}): {u['messages']} messages, {u['channels']} channels, {u['collaborators']} DM colleagues, {u['tasks']} tasks, {u['files']} files"
            for u in user_metrics
        )
        busiest = ", ".join(f"#{c['name']} ({c['count']})" for c in top_channels[:5]) or "none"
        overview = invoke_bedrock_llm(
            f"""Write the team overview for the Beenco Connect weekly activity report, {start_str} to {end_str}.

Team: {len(users)} active accounts, {len(active)} with activity this week, {len(inactive)} without.
Totals: {totals['messages']} messages, {totals['tasks']} tasks, {totals['files']} files.
Busiest channels: {busiest}
Per-person activity:
{stats}

Write one short paragraph summarizing the team's week, then 3 to 5 markdown bullet points with key takeaways (engagement, collaboration patterns, anyone notably active). No headings, no code fences.""",
            system_prompt="You write concise executive team summaries. Output raw markdown only.",
        )
        if overview:
            ai_used = True
    if not overview:
        overview = (
            f"**{len(active)}** of **{len(users)}** team members were active this week, sending **{totals['messages']}** messages, "
            f"handling **{totals['tasks']}** tasks and sharing **{totals['files']}** files."
            + (f" Busiest channels: {', '.join('#' + c['name'] for c in top_channels[:3])}." if top_channels else "")
        )

    used_model = AWS_BEDROCK_MODEL if ai_used else LOCAL_FALLBACK_MODEL
    used_provider = "Amazon Bedrock" if ai_used else "Local Fallback"
    parts = [
        "# 🤖 Team Weekly Activity Report",
        f"**Reporting Period:** {start_str} to {end_str}",
        f"**Team Members Covered:** {len(users)} ({len(active)} active) | **AI Model:** {used_provider} ({used_model})",
        "",
        "---",
        "",
        "### 📊 Team Overview",
        overview,
    ]
    if table_rows:
        parts += [
            "", "---", "",
            "### 📋 At a Glance",
            "| Employee | Messages | Channels | Colleagues | Tasks | Files |",
            "|---|---|---|---|---|---|",
            *table_rows,
        ]
    for section in sections:
        parts += ["", "---", "", section]
    if inactive:
        parts += ["", "---", "", "### 💤 No Activity This Week", *[f"- {m['display_name']} (@{m['username']})" for m in inactive]]
    summary_md = "\n".join(parts) + "\n"

    report_id = uid("rep")
    title = f"Team Weekly Report ({start_str} - {end_str})"
    metrics_json = json_dumps({
        "reportType": "combined",
        "trigger": trigger,
        "usersCovered": len(users),
        "activeUsers": len(active),
        "inactiveUsers": [{"id": m["id"], "name": m["display_name"], "handle": m["username"]} for m in inactive],
        "totalMessages": totals["messages"],
        "tasksCount": totals["tasks"],
        "filesCount": totals["files"],
        "channelsCount": len(top_channels),
        "users": user_metrics,
        "aiModel": used_model,
        "aiProvider": used_provider,
    })
    conn.execute(
        """
        INSERT INTO weekly_activity_reports (id, user_id, report_type, report_trigger, generated_by, week_start, week_end, title, summary, collaborators, channels_involved, metrics, delivered_to_user_at, delivered_to_admin_at, created_at)
        VALUES (?, NULL, 'combined', ?, ?, ?, ?, ?, ?, '[]', ?, ?, '', ?, ?)
        """,
        (report_id, trigger, generated_by, start_iso, end_iso, title, summary_md, json_dumps(top_channels), metrics_json, utc_now(), utc_now()),
    )
    notify_report_admins(conn, {
        "reportId": report_id,
        "userName": "All team members",
        "title": f"📊 {title}",
        "weekStart": start_iso,
        "weekEnd": end_iso,
        "snippet": f"{'Scheduled' if trigger == 'scheduled' else 'Admin-requested'} team report: {len(active)} of {len(users)} members active, {totals['messages']} messages.",
    })
    row = conn.execute("SELECT * FROM weekly_activity_reports WHERE id = ?", (report_id,)).fetchone()
    return serialize_weekly_report(row)


def run_combined_report_job(week_start_dt: datetime, week_end_dt: datetime, trigger: str, generated_by: str = "") -> bool:
    """Runs one combined report at a time; returns False if a job is already running."""
    with WEEKLY_REPORT_LOCK:
        if WEEKLY_REPORT_JOB["running"]:
            return False
        WEEKLY_REPORT_JOB.update({"running": True, "scope": trigger, "startedAt": utc_now(), "finishedAt": "", "lastError": ""})
    try:
        with closing(db()) as conn:
            report = generate_combined_weekly_report(conn, week_start_dt, week_end_dt, trigger=trigger, generated_by=generated_by)
        WEEKLY_REPORT_JOB["lastReportId"] = report.get("id", "")
    except Exception as exc:
        WEEKLY_REPORT_JOB["lastError"] = str(exc)
        print(f"[Weekly Report Error] {exc}", flush=True)
    finally:
        WEEKLY_REPORT_JOB.update({"running": False, "finishedAt": utc_now()})
    return True


def start_weekly_report_scheduler() -> None:
    """Generates the combined team report for the previous week every Monday at WEEKLY_REPORT_HOUR (report timezone)."""
    def scheduler_loop() -> None:
        while True:
            try:
                now_local = datetime.now(WEEKLY_REPORT_TZ)
                week_start_dt, week_end_dt = previous_calendar_week(now_local)
                due_at = week_end_dt.replace(hour=WEEKLY_REPORT_HOUR)
                week_key = week_start_dt.strftime("%Y-%m-%d")
                # Catch up later in the week too, in case the server was down on Monday morning.
                if now_local >= due_at:
                    with closing(db()) as conn:
                        done = conn.execute("SELECT value FROM settings WHERE key = 'weekly_report_last_scheduled_week'").fetchone()
                        should_run = False
                        if not done or done["value"] != week_key:
                            # Mark first so a failing run is not retried every 30 seconds.
                            conn.execute("INSERT INTO settings (key, value) VALUES ('weekly_report_last_scheduled_week', ?)", (week_key,))
                            # On the very first start, don't backfill a week whose Monday run already passed.
                            should_run = bool(done)
                    if should_run:
                        print(f"[Weekly Report Scheduler] Generating team report for week of {week_key}", flush=True)
                        run_combined_report_job(week_start_dt, week_end_dt, trigger="scheduled")
            except Exception as exc:
                print(f"[Weekly Report Scheduler Error] {exc}", flush=True)
            time.sleep(30)

    threading.Thread(target=scheduler_loop, daemon=True, name="WeeklyTeamReportScheduler").start()


TWITTER_MENTIONS_STATE: dict[str, Any] = {
    "running": False,
    "last_run": None,
    "last_error": None,
    "new_mentions": 0,
    "cooldown_until": None,
    "account_cooldowns": {},
    "next_account_index": 0,
    "last_checked_account": "",
}
TWITTER_MENTIONS_LOCK = threading.Lock()


class TwitterMentionsRateLimited(RuntimeError):
    pass


def normalize_twitter_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return utc_now()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def twitter_mentions_today_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    try:
        day_tz = ZoneInfo(TWITTER_MENTIONS_DAY_TIMEZONE)
    except Exception:
        day_tz = timezone.utc
    local_now = now.astimezone(day_tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc)


def twitter_mentions_account_setting_key(username: str) -> str:
    safe_username = re.sub(r"[^A-Za-z0-9_]+", "_", username.strip().lstrip("@")).lower()
    return f"twitter_mentions_last_poll:{safe_username}"


def parse_utc_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def next_twitter_mentions_account(now: datetime) -> str | None:
    if not TWITTER_MENTION_ACCOUNTS:
        return None
    cooldowns = TWITTER_MENTIONS_STATE.setdefault("account_cooldowns", {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}
        TWITTER_MENTIONS_STATE["account_cooldowns"] = cooldowns
    start_index = int(TWITTER_MENTIONS_STATE.get("next_account_index") or 0) % len(TWITTER_MENTION_ACCOUNTS)
    for offset in range(len(TWITTER_MENTION_ACCOUNTS)):
        account_index = (start_index + offset) % len(TWITTER_MENTION_ACCOUNTS)
        username = TWITTER_MENTION_ACCOUNTS[account_index]
        cooldown_dt = parse_utc_datetime(cooldowns.get(username.lower()))
        if cooldown_dt and now < cooldown_dt:
            continue
        cooldowns.pop(username.lower(), None)
        TWITTER_MENTIONS_STATE["next_account_index"] = (account_index + 1) % len(TWITTER_MENTION_ACCOUNTS)
        return username
    return None


def fetch_twitter_mentions_for_account(username: str, since_unix: int) -> tuple[str, list[dict]]:
    tweets: list[dict] = []
    cursor = ""
    seen_cursors: set[str] = set()
    for _ in range(TWITTER_MENTIONS_MAX_PAGES):
        params: dict[str, Any] = {"userName": username, "sinceTime": since_unix}
        if cursor:
            params["cursor"] = cursor
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"https://api.twitterapi.io/twitter/user/mentions?{query}",
            headers={"X-API-Key": TWITTERAPI_IO_KEY, "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise TwitterMentionsRateLimited(f"@{username}: HTTP Error 429: Too Many Requests") from exc
            raise
        if payload.get("status") not in (None, "success"):
            raise RuntimeError(payload.get("message") or f"Twitter API returned an error for @{username}.")
        tweets.extend(tweet for tweet in payload.get("tweets", []) if isinstance(tweet, dict))
        next_cursor = str(payload.get("next_cursor") or "")
        if not payload.get("has_next_page") or not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return username, tweets


def poll_twitter_mentions() -> int:
    if not TWITTERAPI_IO_KEY or not TWITTER_MENTION_ACCOUNTS:
        return 0
    if not TWITTER_MENTIONS_LOCK.acquire(blocking=False):
        return 0
    TWITTER_MENTIONS_STATE.update({"running": True, "last_error": None, "new_mentions": 0})
    started_at = datetime.now(timezone.utc)
    try:
        username = next_twitter_mentions_account(started_at)
        if not username:
            cooldowns = TWITTER_MENTIONS_STATE.get("account_cooldowns") or {}
            next_retry = min(
                (dt for dt in (parse_utc_datetime(value) for value in cooldowns.values()) if dt and dt > started_at),
                default=None,
            )
            if next_retry:
                TWITTER_MENTIONS_STATE.update({
                    "cooldown_until": next_retry.isoformat(),
                    "last_error": f"All monitored X accounts are cooling down. Next retry after {next_retry.isoformat()}.",
                })
            return 0
        TWITTER_MENTIONS_STATE["last_checked_account"] = username

        with closing(db()) as conn:
            account_key = twitter_mentions_account_setting_key(username)
            last_poll_row = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (account_key,),
            ).fetchone()
        today_start = twitter_mentions_today_start(started_at)
        fallback_since = today_start
        since_dt = parse_utc_datetime((last_poll_row or {}).get("value")) or fallback_since
        if since_dt < today_start:
            since_dt = today_start
        since_unix = int(since_dt.timestamp())

        fetched: list[tuple[str, list[dict]]] = []
        errors: list[str] = []
        rate_limited = False
        account_succeeded = False
        try:
            fetched.append(fetch_twitter_mentions_for_account(username, since_unix))
            account_succeeded = True
            account_cooldowns = TWITTER_MENTIONS_STATE.get("account_cooldowns") or {}
            if isinstance(account_cooldowns, dict):
                account_cooldowns.pop(username.lower(), None)
            TWITTER_MENTIONS_STATE["cooldown_until"] = None
        except TwitterMentionsRateLimited as exc:
            errors.append(str(exc))
            rate_limited = True
        except Exception as exc:
            errors.append(f"@{username}: {exc}")

        merged: dict[str, dict[str, Any]] = {}
        for monitored_username, tweets in fetched:
            for tweet in tweets:
                tweet_id = str(tweet.get("id") or "").strip()
                if not tweet_id:
                    continue
                item = merged.setdefault(tweet_id, {"tweet": tweet, "accounts": set()})
                item["accounts"].add(monitored_username)

        inserted_count = 0
        with closing(db()) as conn:
            super_admins = conn.execute(
                "SELECT id FROM users WHERE role = 'super_admin' AND is_active = 1"
            ).fetchall()
            for tweet_id, item in merged.items():
                tweet = item["tweet"]
                author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
                author_username = str(author.get("userName") or "").strip()
                author_name = str(author.get("name") or author_username or "Unknown").strip()
                tweet_url = str(tweet.get("url") or "").strip()
                if not tweet_url and author_username:
                    tweet_url = f"https://x.com/{urllib.parse.quote(author_username)}/status/{urllib.parse.quote(tweet_id)}"
                accounts = sorted(item["accounts"], key=str.lower)
                created_at = normalize_twitter_datetime(tweet.get("createdAt"))
                discovered_at = utc_now()
                inserted = conn.execute(
                    """
                    INSERT INTO x_mentions (
                      tweet_id, monitored_usernames, tweet_url, tweet_text, author_username, author_name,
                      author_profile_picture, like_count, reply_count, retweet_count, quote_count, view_count,
                      tweet_created_at, raw_payload, discovered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (tweet_id) DO NOTHING
                    RETURNING tweet_id
                    """,
                    (
                        tweet_id,
                        json_dumps(accounts),
                        tweet_url,
                        str(tweet.get("text") or ""),
                        author_username,
                        author_name,
                        str(author.get("profilePicture") or ""),
                        int(tweet.get("likeCount") or 0),
                        int(tweet.get("replyCount") or 0),
                        int(tweet.get("retweetCount") or 0),
                        int(tweet.get("quoteCount") or 0),
                        int(tweet.get("viewCount") or 0),
                        created_at,
                        json_dumps(tweet),
                        discovered_at,
                    ),
                ).fetchone()
                if not inserted:
                    continue
                inserted_count += 1
                notification_payload = json_dumps({
                    "tweetId": tweet_id,
                    "tweetUrl": tweet_url,
                    "snippet": str(tweet.get("text") or "")[:300],
                    "authorName": author_name,
                    "authorUsername": author_username,
                    "authorProfilePicture": str(author.get("profilePicture") or ""),
                    "monitoredAccounts": accounts,
                    "tweetCreatedAt": created_at,
                    "likeCount": int(tweet.get("likeCount") or 0),
                    "replyCount": int(tweet.get("replyCount") or 0),
                    "retweetCount": int(tweet.get("retweetCount") or 0),
                    "viewCount": int(tweet.get("viewCount") or 0),
                })
                for admin in super_admins:
                    conn.execute(
                        "INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (?, ?, 'x_mention', ?, ?)",
                        (uid("note"), admin["id"], notification_payload, discovered_at),
                    )
            if account_succeeded:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    (account_key, started_at.isoformat()),
                )
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES ('twitter_mentions_last_poll', ?)",
                    (started_at.isoformat(),),
                )
            retention_cutoff = (started_at - timedelta(days=TWITTER_MENTIONS_RETENTION_DAYS)).isoformat()
            conn.execute("DELETE FROM x_mentions WHERE tweet_created_at < ?", (retention_cutoff,))
            conn.execute("DELETE FROM x_mentions WHERE tweet_created_at < ?", (today_start.isoformat(),))

        if rate_limited:
            cooldown_until = started_at + timedelta(minutes=TWITTER_MENTIONS_RATE_LIMIT_COOLDOWN_MINUTES)
            account_cooldowns = TWITTER_MENTIONS_STATE.setdefault("account_cooldowns", {})
            if isinstance(account_cooldowns, dict):
                account_cooldowns[username.lower()] = cooldown_until.isoformat()
            TWITTER_MENTIONS_STATE["cooldown_until"] = cooldown_until.isoformat()
        TWITTER_MENTIONS_STATE.update({
            "last_run": started_at.isoformat(),
            "last_error": "; ".join(errors[:4]) if errors else None,
            "new_mentions": inserted_count,
        })
        if errors:
            print(f"[Twitter Mentions Warning] {'; '.join(errors[:4])}", flush=True)
        print(f"[Twitter Mentions] Checked @{username}; {inserted_count} new tweet(s). Next account in {TWITTER_MENTIONS_ACCOUNT_INTERVAL_SECONDS}s.", flush=True)
        return inserted_count
    except Exception as exc:
        TWITTER_MENTIONS_STATE["last_error"] = str(exc)
        print(f"[Twitter Mentions Error] {exc}", flush=True)
        return 0
    finally:
        TWITTER_MENTIONS_STATE["running"] = False
        TWITTER_MENTIONS_LOCK.release()


def trigger_twitter_mentions_poll() -> bool:
    if not TWITTERAPI_IO_KEY or TWITTER_MENTIONS_STATE.get("running"):
        return False
    threading.Thread(target=poll_twitter_mentions, daemon=True, name="TwitterMentionsManualPoll").start()
    return True


def start_twitter_mentions_scheduler() -> None:
    if not TWITTERAPI_IO_KEY:
        print("Twitter Mentions Monitor: disabled (TWITTERAPI_IO_KEY is not configured)", flush=True)
        return

    def scheduler_loop() -> None:
        while True:
            poll_twitter_mentions()
            time.sleep(TWITTER_MENTIONS_ACCOUNT_INTERVAL_SECONDS)

    threading.Thread(target=scheduler_loop, daemon=True, name="TwitterMentionsScheduler").start()


BLOCKCHAIN_BRIEFING_STATE: dict[str, Any] = {
    "running": False,
    "last_run": None,
    "last_error": None,
}


def execute_blockchain_briefing_sync() -> bool:
    """Invokes run_briefing.py to fetch intelligence, synthesize via Bedrock Qwen, and update HTML."""
    global BLOCKCHAIN_BRIEFING_STATE
    BLOCKCHAIN_BRIEFING_STATE["running"] = True
    BLOCKCHAIN_BRIEFING_STATE["last_error"] = None
    try:
        import run_briefing
        run_briefing.run_pipeline()
        BLOCKCHAIN_BRIEFING_STATE["last_run"] = datetime.now(timezone.utc).isoformat()
        return True
    except Exception as exc:
        BLOCKCHAIN_BRIEFING_STATE["last_error"] = str(exc)
        print(f"[Blockchain Briefing Error] {exc}", flush=True)
        return False
    finally:
        BLOCKCHAIN_BRIEFING_STATE["running"] = False


def start_daily_briefing_scheduler() -> None:
    """Daemon thread checking local time every 30s; runs briefing at 09:00 AM daily."""
    def scheduler_loop():
        last_date = None
        while True:
            try:
                now = datetime.now()
                today_str = now.strftime("%Y-%m-%d")
                # Trigger when local clock is at 09:00 AM (hour 9, minute 0) and not yet run today
                if now.hour == 9 and now.minute == 0 and last_date != today_str:
                    if not BLOCKCHAIN_BRIEFING_STATE.get("running"):
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Daily Scheduler] Executing 09:00 AM daily blockchain intelligence briefing...", flush=True)
                        last_date = today_str
                        execute_blockchain_briefing_sync()
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Daily Scheduler] 09:00 AM daily briefing complete.", flush=True)
            except Exception as e:
                print(f"[Scheduler Loop Error] {e}", flush=True)
            time.sleep(30)

    t = threading.Thread(target=scheduler_loop, daemon=True, name="DailyBlockchainBriefingScheduler")
    t.start()


class PortalHandler(SimpleHTTPRequestHandler):
    server_version = "BeencoConnect/0.2"

    def translate_path(self, path: str) -> str:
        parsed = urllib.parse.urlparse(path)
        clean = parsed.path.lstrip("/")
        if not clean or clean.startswith("api/"):
            return str(WEB_ROOT / "index.html")
        target = (WEB_ROOT / clean).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            return str(WEB_ROOT / "index.html")
        if target.is_dir():
            target = target / "index.html"
        if not target.exists():
            return str(WEB_ROOT / "index.html")
        return str(target)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(self), display-capture=(), geolocation=(), payment=(), usb=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'self'; "
            "object-src 'none'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://pbs.twimg.com https://abs.twimg.com; "
            "font-src 'self' data:; "
            "connect-src 'self' https: wss:; "
            "media-src 'self' data: blob:; "
            "worker-src 'self'; "
            "frame-src 'self'; "
            "frame-ancestors 'self'; "
            "form-action 'self'",
        )
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path in ("", "/index.html") or clean_path.endswith((".js", ".css", ".html")):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        elif self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self.handle_api("GET")
            return
        super().do_GET()

    def do_POST(self) -> None:
        self.handle_api("POST")

    def do_PATCH(self) -> None:
        self.handle_api("PATCH")

    def do_PUT(self) -> None:
        self.handle_api("PUT")

    def do_DELETE(self) -> None:
        self.handle_api("DELETE")

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            raise ValueError("Invalid Content-Length header.")
        if not length:
            return {}
        if length > MAX_JSON_BODY_BYTES:
            raise RequestTooLarge(f"JSON request body exceeds the {MAX_JSON_BODY_BYTES // (1024 * 1024)} MB limit.")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Malformed JSON body.")

    def read_multipart(self) -> tuple[dict[str, str], dict[str, dict]]:
        content_type = self.headers.get("Content-Type", "")
        match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type)
        if "multipart/form-data" not in content_type or not match:
            raise ValueError("Expected multipart form data.")
        boundary = (match.group(1) or match.group(2) or "").encode("utf-8")
        if not boundary:
            raise ValueError("Multipart boundary is missing.")
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            raise ValueError("Invalid Content-Length header.")
        if length <= 0:
            raise ValueError("Upload body is empty.")
        if length > min(MAX_FILE_SIZE_BYTES, MAX_LOCAL_UPLOAD_BYTES):
            raise RequestTooLarge(f"Local direct uploads are limited to {format_size(min(MAX_FILE_SIZE_BYTES, MAX_LOCAL_UPLOAD_BYTES))}.")
        raw = self.rfile.read(length)
        fields: dict[str, str] = {}
        files: dict[str, dict] = {}
        delimiter = b"--" + boundary
        for part in raw.split(delimiter):
            if not part or part in {b"--", b"--\r\n"}:
                continue
            if part.startswith(b"\r\n"):
                part = part[2:]
            if part.endswith(b"--\r\n"):
                part = part[:-4]
            elif part.endswith(b"--"):
                part = part[:-2]
            if part.endswith(b"\r\n"):
                part = part[:-2]
            header_blob, separator, content = part.partition(b"\r\n\r\n")
            if not separator:
                continue
            headers: dict[str, str] = {}
            for line in header_blob.decode("latin-1", errors="ignore").split("\r\n"):
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
            disposition = parse_content_disposition(headers.get("content-disposition", ""))
            name = disposition.get("name", "")
            if not name:
                continue
            filename = disposition.get("filename")
            if filename is None:
                fields[name] = content.decode("utf-8", errors="replace")
            else:
                files[name] = {
                    "filename": filename,
                    "content": content,
                    "contentType": normalized_mime(headers.get("content-type")),
                }
        return fields, files

    def write_json(self, status: int, value, headers: dict | None = None) -> None:
        body = json_dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def write_error(self, status: int, message: str, headers: dict | None = None) -> None:
        self.write_json(status, {"error": message}, headers)

    def session_token(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        cookie_header = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie(cookie_header)
        morsel = jar.get("beenco_session")
        return morsel.value if morsel else None

    def current_user(self, conn: PostgresConnection) -> dict | None:
        token = self.session_token()
        if not token:
            return None
        row = conn.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ? AND sessions.expires_at > ?
            """,
            (token, utc_now()),
        ).fetchone()
        if row:
            conn.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (utc_now(), row["id"]))
            row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        return row

    def require_user(self, conn: PostgresConnection) -> dict | None:
        user = self.current_user(conn)
        if not user:
            self.write_error(401, "Authentication required.")
            return None
        if not user["is_active"]:
            self.write_error(403, "User is disabled.")
            return None
        return user

    def require_report_admin(self, user: dict) -> bool:
        # AI reports include DM snippets, so they are limited to admins (not moderators/IT).
        if user["role"] not in ADMIN_ROLES:
            self.write_error(403, "Only admins can access AI weekly reports.")
            return False
        return True

    def require_admin(self, user: dict) -> bool:
        if user["role"] not in INTRANET_ADMIN_ROLES:
            self.write_error(403, "Admin or moderator role required.")
            return False
        return True

    def client_ip(self) -> str:
        # The client IP drives the per-IP login lockout and every audit record, so forwarding
        # headers are only honored behind a trusted proxy. Even then the LEFTMOST X-Forwarded-For
        # entry is client-controlled; the rightmost one is what the proxy itself appended.
        if TRUST_PROXY_HEADERS:
            real_ip = self.headers.get("X-Real-IP", "").strip()
            if real_ip:
                return real_ip
            forwarded = [part.strip() for part in self.headers.get("X-Forwarded-For", "").split(",") if part.strip()]
            if forwarded:
                return forwarded[-1]
        return self.client_address[0]

    def is_request_https(self) -> bool:
        if TRUST_PROXY_HEADERS:
            forwarded = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
            if forwarded:
                return forwarded == "https"
        return URL_SCHEME == "https"

    def same_origin_mutation(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            parsed = urllib.parse.urlparse(origin)
        except ValueError:
            return False
        return parsed.netloc.lower() == self.headers.get("Host", "").lower()

    def stream_events(self, user: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        client_queue: queue.Queue = queue.Queue(maxsize=100)
        LIVE_BROKER.subscribe(client_queue)
        try:
            init_payload = json.dumps({"ok": True, "userId": user["id"]})
            self.wfile.write(f"event: connected\ndata: {init_payload}\n\n".encode("utf-8"))
            self.wfile.flush()

            while True:
                try:
                    event = client_queue.get(timeout=15.0)
                    evt_type = event.get("type", "message")
                    evt_data = json.dumps(event)
                    self.wfile.write(f"event: {evt_type}\ndata: {evt_data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.error):
            pass
        finally:
            LIVE_BROKER.unsubscribe(client_queue)

    def handle_api(self, method: str) -> None:
        if method in {"POST", "PATCH", "PUT", "DELETE"} and not self.same_origin_mutation():
            self.write_error(403, "Cross-origin state-changing requests are blocked.")
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/api/events" and method == "GET":
            with closing(db()) as conn:
                user = self.current_user(conn)
                if not user:
                    token_param = query.get("token", [""])[0].strip()
                    if token_param:
                        row = conn.execute(
                            "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token = ? AND sessions.expires_at > ?",
                            (token_param, utc_now()),
                        ).fetchone()
                        if row and row["is_active"]:
                            user = row
            if not user:
                self.write_error(401, "Authentication required.")
                return
            self.stream_events(user)
            return
        try:
            with closing(db()) as conn:
                self.route_api(conn, method, path, query)
        except RequestTooLarge as error:
            self.write_error(413, str(error))
        except ValueError as error:
            self.write_error(400, str(error))
        except psycopg.errors.IntegrityError as error:
            # The constraint text names tables/columns; keep it in the server log only.
            print(f"API integrity error on {method} {path}: {error}", file=sys.stderr, flush=True)
            self.write_error(409, "The request conflicts with existing data.")
        except Exception:
            print(f"API error on {method} {path}:\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            self.write_error(500, "Unexpected server error.")

    def route_api(self, conn: PostgresConnection, method: str, path: str, query: dict) -> None:
        if path == "/api/health":
            conn.execute("SELECT 1").fetchone()
            self.write_json(200, {"ok": True, "db": "postgresql", "scheme": URL_SCHEME, "accessUrls": local_access_urls()})
            return

        if path == "/api/session" and method == "GET":
            user = self.current_user(conn)
            self.write_json(200, {"user": row_to_user(user), "config": self.public_config(conn)})
            return

        if path == "/api/auth/signup" and method == "POST":
            body = self.read_json()
            password = validate_new_password(str(body.get("password", "")))
            user = create_user(conn, body.get("email", ""), body.get("displayName", ""), password, "local")
            token = create_session(conn, user["id"])
            # The session lives in an httpOnly cookie; it is never handed to page JavaScript.
            self.write_json(201, {"user": row_to_user(user)}, self.session_headers(token))
            return

        if path == "/api/auth/login" and method == "POST":
            body = self.read_json()
            email = str(body.get("email", "")).strip().lower()
            password = str(body.get("password", ""))
            client_ip = self.client_ip()
            lockout_seconds = login_lockout_remaining_seconds(conn, email, client_ip)
            if lockout_seconds > 0:
                write_audit(conn, None, "auth.login.blocked", "user", email or "unknown", {"provider": "local", "retryAfterSeconds": lockout_seconds}, client_ip, self.headers.get("User-Agent", ""))
                self.write_error(429, f"Too many failed attempts. Try again in {math.ceil(lockout_seconds / 60)} minute(s).", headers={"Retry-After": str(lockout_seconds)})
                return
            user = conn.execute("SELECT * FROM users WHERE email = ? AND is_active = 1", (email,)).fetchone()
            if not user or not user["password_hash"] or not verify_password(password, user["password_hash"], user["password_salt"]):
                write_audit(conn, None, "auth.login.fail", "user", email or "unknown", {"provider": "local"}, client_ip, self.headers.get("User-Agent", ""))
                self.write_error(401, "Invalid email or password.")
                return
            if needs_rehash(user["password_hash"]):
                # Transparently upgrades an older/weaker hash to the current iteration count —
                # the user never notices, and nothing is lost since we have the plaintext right now.
                new_hash, new_salt = hash_password(password)
                conn.execute("UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?", (new_hash, new_salt, user["id"]))
            token = create_session(conn, user["id"])
            write_audit(conn, user["id"], "auth.login.success", "user", user["id"], {"provider": user["provider"]}, self.client_ip(), self.headers.get("User-Agent", ""))
            self.write_json(200, {"user": row_to_user(user)}, self.session_headers(token))
            return

        if path == "/api/auth/logout" and method == "POST":
            token = self.session_token()
            user = self.current_user(conn)
            if token:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            if user and not conn.execute("SELECT 1 FROM sessions WHERE user_id = ? AND expires_at > ?", (user["id"], utc_now())).fetchone():
                # Clearing last_seen_at (not bumping it to now) is what actually takes effect —
                # online is derived purely from its recency, so setting it to "now" here would
                # have left a just-logged-out user showing online for the next 5 minutes.
                conn.execute("UPDATE users SET last_seen_at = '' WHERE id = ?", (user["id"],))
            write_audit(conn, user["id"] if user else None, "auth.logout", "session", token[:12] if token else "none", {}, self.client_ip(), self.headers.get("User-Agent", ""))
            self.write_json(200, {"ok": True}, self.clear_session_headers())
            return

        if path == "/api/auth/google/start" and method == "GET":
            client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
            redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", f"http://127.0.0.1:{PORT}/api/auth/google/callback")
            if not client_id:
                self.write_json(
                    200,
                    {
                        "configured": False,
                        "mode": "local_provider",
                        "message": "Local Google provider is ready for this development portal.",
                    },
                )
                return
            state = secrets.token_urlsafe(18)
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"oauth_state:{state}", utc_now()))
            params = urllib.parse.urlencode(
                {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": "openid email profile",
                    "state": state,
                    "access_type": "offline",
                    "prompt": "select_account",
                }
            )
            self.write_json(200, {"configured": True, "url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}"})
            return

        if path == "/api/auth/google/callback" and method == "GET":
            self.handle_google_callback(conn, query)
            return

        user = self.require_user(conn)
        if not user:
            return

        if path == "/api/bootstrap" and method == "GET":
            self.write_json(200, self.bootstrap(conn, user))
            return

        if path == "/api/admin/x-mentions/refresh" and method == "POST":
            if user["role"] not in SUPER_ADMIN_ROLES:
                self.write_error(403, "Super Admin role required.")
                return
            if not TWITTERAPI_IO_KEY:
                self.write_error(503, "TWITTERAPI_IO_KEY is not configured on the server.")
                return
            started = trigger_twitter_mentions_poll()
            self.write_json(202, {"started": started, "running": True})
            return

        if path.startswith("/api/admin/users/") and method == "PATCH":
            self.update_user(conn, user, path.split("/")[4])
            return

        if path.startswith("/api/admin/users/") and method == "DELETE":
            self.delete_user_endpoint(conn, user, path.split("/")[4])
            return

        if path == "/api/admin/pending-pings" and method == "GET":
            if not self.require_admin(user):
                return
            mins = int((query.get("mins") or ["30"])[0])
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=mins)).isoformat()
            stale_leave = conn.execute(
                """
                SELECT l.*, COALESCE(e.full_name, 'Employee') AS display_name, COALESCE(e.email, '') AS email
                FROM leave_requests l
                LEFT JOIN employees e ON e.id = l.employee_id
                WHERE l.status = 'pending' AND l.created_at <= ?
                ORDER BY l.created_at
                """,
                (cutoff,),
            ).fetchall()
            stale_remote = conn.execute(
                """
                SELECT r.*, COALESCE(u.display_name, 'Requester') AS requester_name
                FROM remote_assist_sessions r
                LEFT JOIN users u ON u.id = r.requester_id
                WHERE r.status = 'pending' AND r.created_at <= ?
                ORDER BY r.created_at
                """,
                (cutoff,),
            ).fetchall()
            stale_notes = conn.execute(
                """
                SELECT * FROM notifications
                WHERE user_id = ? AND read_at IS NULL AND created_at <= ?
                ORDER BY created_at DESC
                """,
                (user["id"], cutoff),
            ).fetchall()
            items = []
            for row in stale_leave:
                items.append({
                    "id": row["id"],
                    "type": "leave_request",
                    "title": f"Leave Request: {row['display_name']}",
                    "detail": f"{row['leave_type'].replace('_', ' ').title()} ({row['start_date']} to {row['end_date']}) - {row['reason'][:90]}",
                    "createdAt": row["created_at"],
                    "portal": "hr",
                })
            for row in stale_remote:
                items.append({
                    "id": row["id"],
                    "type": "remote_assist",
                    "title": f"Remote Assistance: {row['requester_name']}",
                    "detail": f"Target host: {row['target_host']} - {row['reason'][:90]}",
                    "createdAt": row["created_at"],
                    "portal": "chat",
                })
            for row in stale_notes:
                payload = {}
                try:
                    payload = json.loads(row["payload"] or "{}")
                except Exception:
                    pass
                snippet = payload.get("snippet") or payload.get("tag") or row["type"]
                items.append({
                    "id": row["id"],
                    "type": f"notification_{row['type']}",
                    "title": f"Unread {row['type'].replace('_', ' ').title()}: {payload.get('tag', '') or snippet[:40]}",
                    "detail": snippet[:120],
                    "createdAt": row["created_at"],
                    "channelId": payload.get("channelId"),
                    "messageId": payload.get("messageId"),
                    "isTag": row["type"] == "tag",
                    "tag": payload.get("tag"),
                })
            urgent_messages = conn.execute(
                """
                SELECT m.*, u.display_name AS author_name, u.username AS author_username, c.name AS channel_name, c.type AS channel_type
                FROM messages m
                JOIN users u ON u.id = m.author_id
                JOIN channels c ON c.id = m.channel_id
                WHERE m.is_urgent = 1 AND (m.acknowledged_at IS NULL OR m.acknowledged_at = '')
                  AND m.author_id != ? AND m.deleted_at IS NULL
                ORDER BY m.created_at DESC
                """,
                (user["id"],),
            ).fetchall()
            for row in urgent_messages:
                if can_access_channel(conn, user, row["channel_id"]):
                    items.append({
                        "id": row["id"],
                        "type": "urgent_message",
                        "title": f"⚠️ URGENT: Message from {row['author_name']}",
                        "detail": row["body"][:140],
                        "createdAt": row["created_at"],
                        "channelId": row["channel_id"],
                        "channelName": row["channel_name"],
                        "messageId": row["id"],
                        "authorName": row["author_name"],
                        "authorUsername": row["author_username"],
                        "isUrgent": True,
                        "portal": "chat",
                    })
            self.write_json(200, {
                "hasPendingPings": len(items) > 0,
                "staleCount": len(items),
                "staleThresholdMinutes": mins,
                "items": items,
            })
            return

        if path == "/api/notifications/read" and method == "POST":
            body = self.read_json()
            note_id = body.get("id")
            all_notes = body.get("all", False)
            now = utc_now()
            if all_notes:
                conn.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (now, user["id"]))
            elif note_id:
                conn.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND id = ?", (now, user["id"], note_id))
            self.write_json(200, {"ok": True, "readAt": now})
            return

        if path.startswith("/api/hr/"):
            self.route_hr(conn, user, method, path)
            return

        if path.startswith("/api/shuffle/"):
            self.route_shuffle(conn, user, method, path, query)
            return

        if path == "/api/channels" and method == "POST":
            self.create_channel(conn, user)
            return

        if path == "/api/dms" and method == "POST":
            self.create_dm(conn, user)
            return

        if path.startswith("/api/channels/") and path.endswith("/members") and method == "POST":
            self.add_channel_member(conn, user, path.split("/")[3])
            return

        if path.startswith("/api/channels/") and "/members/" in path and method == "DELETE":
            parts = path.split("/")
            self.remove_channel_member(conn, user, parts[3], parts[5])
            return

        if path.startswith("/api/channels/") and len(path.split("/")) == 4 and method == "DELETE":
            self.delete_channel(conn, user, path.split("/")[3])
            return

        if path == "/api/messages" and method == "POST":
            self.create_message(conn, user)
            return

        if path == "/api/files/upload" and method == "POST":
            self.create_file_upload(conn, user)
            return

        if path.startswith("/api/messages/"):
            message_id = path.split("/")[3]
            if len(path.split("/")) == 5 and path.endswith("/reactions") and method == "POST":
                self.toggle_reaction(conn, user, message_id)
                return
            if len(path.split("/")) == 5 and path.endswith("/acknowledge") and method == "POST":
                self.acknowledge_message(conn, user, message_id)
                return
            if method == "PATCH":
                self.edit_message(conn, user, message_id)
                return
            if method == "DELETE":
                self.delete_message(conn, user, message_id)
                return

        if path == "/api/files" and method == "POST":
            self.create_file(conn, user)
            return

        if path.startswith("/api/files/") and path.endswith("/scan") and method == "POST":
            self.scan_file(conn, user, path.split("/")[3])
            return

        if path.startswith("/api/files/") and path.endswith("/download") and method == "POST":
            self.download_file(conn, user, path.split("/")[3])
            return

        if path.startswith("/api/files/") and path.endswith("/preview") and method == "GET":
            self.preview_file(conn, user, path.split("/")[3])
            return

        if path.startswith("/api/files/") and len(path.split("/")) == 4 and method == "GET":
            self.get_file_info(conn, user, path.split("/")[3])
            return

        if path.startswith("/api/files/") and len(path.split("/")) == 4 and method == "DELETE":
            self.delete_file(conn, user, path.split("/")[3])
            return

        if path == "/api/events" and method == "POST":
            self.create_event(conn, user)
            return

        if path.startswith("/api/events/") and method == "DELETE":
            self.delete_event(conn, user, path.split("/")[3])
            return

        if path.startswith("/api/events/") and path.endswith("/response") and method == "POST":
            self.event_response(conn, user, path.split("/")[3])
            return

        if path == "/api/ai/reports/generate" and method == "POST":
            self.generate_ai_reports_endpoint(conn, user)
            return

        if path == "/api/ai/reports/status" and method == "GET":
            self.ai_report_job_status(user)
            return

        if path == "/api/blockchain-briefing/generate" and method == "POST":
            self.generate_blockchain_briefing_endpoint(user)
            return

        if path == "/api/blockchain-briefing/status" and method == "GET":
            self.get_blockchain_briefing_status(user)
            return

        if path == "/api/ai/reports" and method == "DELETE":
            self.clear_ai_reports_endpoint(conn, user)
            return

        if path.startswith("/api/ai/reports/") and method == "DELETE":
            self.delete_ai_report_endpoint(conn, user, path.split("/")[4])
            return

        if path == "/api/announcements" and method == "POST":
            self.create_announcement(conn, user)
            return

        if path == "/api/users" and method == "POST":
            self.create_user_admin_endpoint(conn, user)
            return

        if path.startswith("/api/users/") and method == "PATCH":
            self.update_user(conn, user, path.split("/")[3])
            return

        if path.startswith("/api/users/") and method == "DELETE":
            self.delete_user_endpoint(conn, user, path.split("/")[3])
            return

        if path.startswith("/api/channels/") and path.endswith("/read") and method == "POST":
            self.mark_channel_read(conn, user, path.split("/")[3])
            return

        if path.startswith("/api/channels/") and path.endswith("/typing") and method == "POST":
            self.update_typing(conn, user, path.split("/")[3])
            return

        if path == "/api/admin/retention" and method == "PUT":
            self.update_retention(conn, user)
            return

        if path == "/api/admin/feature-flags" and method == "PUT":
            self.update_feature_flag(conn, user)
            return

        if path.startswith("/api/channels/") and path.endswith("/lock") and method == "POST":
            self.toggle_channel_lock(conn, user, path.split("/")[3])
            return

        if path == "/api/tasks" and method == "POST":
            self.create_workspace_task(conn, user)
            return

        if path.startswith("/api/tasks/") and method == "PATCH":
            self.update_workspace_task(conn, user, path.split("/")[3])
            return

        if path == "/api/remote-assist" and method == "GET":
            self.write_json(200, {"sessions": self.remote_assist_sessions(conn, user)})
            return

        if path == "/api/remote-assist" and method == "POST":
            self.create_remote_assist(conn, user)
            return

        if path.startswith("/api/remote-assist/") and path.endswith("/respond") and method == "POST":
            self.respond_remote_assist(conn, user, path.split("/")[3])
            return

        if path.startswith("/api/remote-assist/") and path.endswith("/end") and method == "POST":
            self.end_remote_assist(conn, user, path.split("/")[3])
            return

        self.write_error(404, "Unknown API route.")

    def public_config(self, conn: PostgresConnection) -> dict:
        settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings").fetchall() if not row["key"].startswith("oauth_state:")}
        return {
            "auth": {
                "adLoginEnabled": settings.get("ad_login_enabled", "1") == "1",
                "googleSignupEnabled": settings.get("google_signup_enabled", "1") == "1",
                "googleOAuthConfigured": bool(os.environ.get("GOOGLE_CLIENT_ID")),
                "productionAuth": "Keycloak OIDC federated to Active Directory",
            },
            "network": {
                "host": HOST,
                "port": PORT,
                "scheme": "https" if self.is_request_https() else URL_SCHEME,
                "httpsEnabled": self.is_request_https(),
                "accessUrls": local_access_urls(),
            },
            "remoteDesktop": {
                "mode": "consent-rdp",
                "requiresAcceptance": True,
                "credentialStorage": "none",
            },
            "maxFileSizeBytes": MAX_FILE_SIZE_BYTES,
            "files": file_policy_payload(),
        }

    def session_headers(self, token: str) -> dict:
        max_age = SESSION_DAYS * 24 * 60 * 60
        secure = "; Secure" if self.is_request_https() else ""
        return {"Set-Cookie": f"beenco_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}{secure}"}

    def clear_session_headers(self) -> dict:
        secure = "; Secure" if self.is_request_https() else ""
        return {"Set-Cookie": f"beenco_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0{secure}"}

    def bootstrap(self, conn: PostgresConnection, user: dict) -> dict:
        channel_ids = visible_channel_ids(conn, user)
        placeholders = ",".join("?" for _ in channel_ids) or "''"
        channels = [serialize_channel(conn, row, user) for row in conn.execute(f"SELECT * FROM channels WHERE id IN ({placeholders}) ORDER BY type, name", channel_ids).fetchall()]
        messages = [serialize_message(conn, row) for row in conn.execute(f"SELECT * FROM messages WHERE channel_id IN ({placeholders}) ORDER BY created_at", channel_ids).fetchall()]
        files = [
            serialize_file(row)
            for row in conn.execute(
                f"""
                SELECT DISTINCT files.* FROM files
                JOIN message_attachments ON message_attachments.file_id = files.id
                JOIN messages ON messages.id = message_attachments.message_id
                WHERE files.channel_id IN ({placeholders})
                  AND messages.deleted_at IS NULL
                ORDER BY files.created_at DESC
                """,
                channel_ids,
            ).fetchall()
        ]
        tasks = [serialize_workspace_task(row) for row in conn.execute(f"SELECT * FROM workspace_tasks WHERE channel_id IN ({placeholders}) ORDER BY status, COALESCE(deadline, ''), created_at DESC", channel_ids).fetchall()]
        events = [serialize_event(conn, row) for row in conn.execute("SELECT * FROM events ORDER BY starts_at").fetchall()]
        announcements = [serialize_announcement(row) for row in conn.execute("SELECT * FROM announcements ORDER BY created_at DESC").fetchall()]
        audit_query = "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 120"
        audit_params: tuple = ()
        if user["role"] not in INTRANET_ADMIN_ROLES:
            audit_query = "SELECT * FROM audit_log WHERE actor_id = ? ORDER BY created_at DESC LIMIT 60"
            audit_params = (user["id"],)
        check_advance_calendar_reminders(conn)
        # AI reports are admin-only; employees never receive their own.
        weekly_reports = []
        admin_weekly_reports = []
        if user["role"] in ADMIN_ROLES:
            admin_weekly_reports = [
                serialize_weekly_report(row)
                for row in conn.execute(
                    "SELECT * FROM weekly_activity_reports ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
            ]

        if user["role"] in ADMIN_ROLES:
            user_notes = conn.execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)).fetchall()
        else:
            raw_notes = conn.execute(
                "SELECT * FROM notifications WHERE user_id = ? AND type NOT IN ('tag', 'ai_report', 'ai_report_admin') ORDER BY created_at DESC",
                (user["id"],)
            ).fetchall()
            user_notes = []
            for rn in raw_notes:
                if rn["type"] == "urgent":
                    try:
                        p = json_loads(rn["payload"], {})
                        ch = conn.execute("SELECT type FROM channels WHERE id = ?", (p.get("channelId"),)).fetchone()
                        if ch and ch["type"] in ("dm", "group_dm"):
                            user_notes.append(rn)
                    except Exception:
                        pass
                else:
                    user_notes.append(rn)

        x_mentions = []
        x_mentions_last_poll = ""
        if user["role"] in SUPER_ADMIN_ROLES:
            x_mentions_since = twitter_mentions_today_start().isoformat()
            x_mentions = [
                serialize_x_mention(row)
                for row in conn.execute(
                    "SELECT * FROM x_mentions WHERE tweet_created_at >= ? ORDER BY tweet_created_at DESC LIMIT 500",
                    (x_mentions_since,),
                ).fetchall()
            ]
            last_poll = conn.execute(
                "SELECT value FROM settings WHERE key = 'twitter_mentions_last_poll'"
            ).fetchone()
            x_mentions_last_poll = (last_poll or {}).get("value", "")

        return {
            "currentUser": row_to_user(user),
            "users": [row_to_user(row) for row in conn.execute("SELECT * FROM users WHERE is_active = 1 ORDER BY display_name").fetchall()],
            "groups": [{"id": f"role_{role}", "name": role, "type": "role", "members": [item["id"] for item in conn.execute("SELECT id FROM users WHERE role = ? AND is_active = 1", (role,)).fetchall()]} for role in ["super_admin", "admin", "moderator", "it_admin", "security_analyst", "hr_admin", "hr_viewer", "member", "guest"]],
            "channels": channels,
            "messages": messages,
            "files": files,
            "tasks": tasks,
            "events": events,
            "announcements": announcements,
            "notifications": [serialize_notification(row) for row in user_notes],
            "xMentions": x_mentions,
            "xMentionStatus": {
                "configured": bool(TWITTERAPI_IO_KEY),
                "running": bool(TWITTER_MENTIONS_STATE.get("running")),
                "lastPoll": x_mentions_last_poll or TWITTER_MENTIONS_STATE.get("last_run") or "",
                "lastError": TWITTER_MENTIONS_STATE.get("last_error") or "",
                "cooldownUntil": TWITTER_MENTIONS_STATE.get("cooldown_until") or "",
                "accountIntervalSeconds": TWITTER_MENTIONS_ACCOUNT_INTERVAL_SECONDS,
                "lastCheckedAccount": TWITTER_MENTIONS_STATE.get("last_checked_account") or "",
                "retentionDays": TWITTER_MENTIONS_RETENTION_DAYS,
                "dayTimezone": TWITTER_MENTIONS_DAY_TIMEZONE,
                "monitoredAccounts": TWITTER_MENTION_ACCOUNTS if user["role"] in SUPER_ADMIN_ROLES else [],
            },
            "weeklyReports": weekly_reports,
            "adminWeeklyReports": admin_weekly_reports,
            "remoteAssists": self.remote_assist_sessions(conn, user),
            "audit": [serialize_audit(row) for row in conn.execute(audit_query, audit_params).fetchall()],
            "settings": {
                "retentionGlobalDays": int(conn.execute("SELECT value FROM settings WHERE key = 'retention_global_days'").fetchone()["value"]),
            },
            "featureFlags": {row["key"]: bool(row["value"]) for row in conn.execute("SELECT key, value FROM feature_flags").fetchall()},
            "config": self.public_config(conn),
            "admin": self.command_center_bootstrap(conn, user) if has_command_center_access(user) else None,
        }

    def command_center_bootstrap(self, conn: PostgresConnection, user: dict) -> dict:
        allowed_channel_ids = set(visible_channel_ids(conn, user))
        intranet_channels = [serialize_channel(conn, row, user) for row in conn.execute("SELECT * FROM channels ORDER BY type, name").fetchall() if row["id"] in allowed_channel_ids]
        return {
            "portals": self.admin_portals(conn, user),
            "activity": self.admin_activity(conn, user, 80),
            "intranet": {
                "storage": self.storage_overview(conn),
                "users": [row_to_user(row) for row in conn.execute("SELECT * FROM users ORDER BY display_name").fetchall()],
                "channels": intranet_channels,
            },
            "hr": self.hr_payload(conn, user),
            "shuffle": self.shuffle_payload(conn, user),
        }

    def admin_portals(self, conn: PostgresConnection, user: dict) -> list[dict]:
        if not has_command_center_access(user):
            return []
        active_users = conn.execute("SELECT COUNT(*) AS total FROM users WHERE is_active = 1").fetchone()["total"]
        pending_leave = conn.execute("SELECT COUNT(*) AS total FROM leave_requests WHERE status = 'pending'").fetchone()["total"]
        assigned_assets = conn.execute("SELECT COUNT(*) AS total FROM assets WHERE status = 'assigned'").fetchone()["total"]
        failed_runs = len([item for item in self.shuffle_workflows(conn) if item["status"] == "failed"])
        catalog = [
            {
                "id": "intranet-admin",
                "name": "Intranet Admin",
                "icon": "IA",
                "description": "Users, groups, moderation, retention, files, tasks, and audit.",
                "badge": f"{active_users} active users",
                "status": "available",
            },
            {
                "id": "shuffle",
                "name": "Shuffle SOAR",
                "icon": "SO",
                "description": "Workflow health, executions, connector inventory, and run-now controls.",
                "badge": f"{failed_runs} failed runs",
                "status": "configured" if os.environ.get("SHUFFLE_API_URL") else "local-ready",
            },
            {
                "id": "hr",
                "name": "HR & Assets",
                "icon": "HR",
                "description": "Employees, assets, attendance, leave, documents, onboarding, and reports.",
                "badge": f"{pending_leave} pending leave / {assigned_assets} assets",
                "status": "available",
            },
        ]
        return [portal for portal in catalog if can_access_portal(user, portal["id"])]

    def admin_activity(self, conn: PostgresConnection, user: dict, limit: int = 120) -> list[dict]:
        if not has_command_center_access(user):
            return []
        rows = conn.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [serialize_audit(row) for row in rows]

    def storage_overview(self, conn: PostgresConnection) -> dict:
        rows = conn.execute(
            """
            SELECT users.id, users.display_name, users.email, COUNT(files.id) AS files, COALESCE(SUM(files.size_bytes), 0) AS bytes
            FROM users
            LEFT JOIN files ON files.uploader_id = users.id
            GROUP BY users.id
            ORDER BY bytes DESC
            """
        ).fetchall()
        by_channel = conn.execute(
            """
            SELECT channels.id, channels.name, COUNT(files.id) AS files, COALESCE(SUM(files.size_bytes), 0) AS bytes
            FROM channels
            LEFT JOIN files ON files.channel_id = channels.id
            GROUP BY channels.id
            ORDER BY bytes DESC
            """
        ).fetchall()
        total = sum(row["bytes"] for row in rows)
        return {
            "totalBytes": total,
            "totalFiles": sum(row["files"] for row in rows),
            "byUser": [dict(row) for row in rows],
            "byChannel": [dict(row) for row in by_channel],
            "maxFileSizeBytes": MAX_FILE_SIZE_BYTES,
        }

    def hr_payload(self, conn: PostgresConnection, user: dict) -> dict:
        if not can_access_portal(user, "hr"):
            return {}
        employees = [serialize_employee(row) for row in conn.execute("SELECT * FROM employees ORDER BY full_name").fetchall()]
        assets = [serialize_asset(row) for row in conn.execute("SELECT * FROM assets ORDER BY asset_tag").fetchall()]
        assignments = [serialize_assignment(row) for row in conn.execute("SELECT * FROM asset_assignments ORDER BY assigned_at DESC").fetchall()]
        attendance = [serialize_attendance(row) for row in conn.execute("SELECT * FROM attendance_days ORDER BY date DESC LIMIT 120").fetchall()]
        leave = [serialize_leave(row) for row in conn.execute("SELECT * FROM leave_requests ORDER BY created_at DESC LIMIT 120").fetchall()]
        tasks = [serialize_task(row) for row in conn.execute("SELECT * FROM workflow_tasks ORDER BY created_at DESC LIMIT 120").fetchall()]
        return {
            "employees": employees,
            "assets": assets,
            "assignments": assignments,
            "attendance": attendance,
            "leaveRequests": leave,
            "tasks": tasks,
            "summary": {
                "headcount": len([item for item in employees if item["status"] == "active"]),
                "assetsAssigned": len([item for item in assets if item["status"] == "assigned"]),
                "assetsAvailable": len([item for item in assets if item["status"] == "available"]),
                "pendingLeave": len([item for item in leave if item["status"] == "pending"]),
                "openTasks": len([item for item in tasks if item["status"] != "done"]),
            },
        }

    def shuffle_workflows(self, conn: PostgresConnection) -> list[dict]:
        return [
            {"id": "wf-offboarding", "name": "Employee offboarding security checklist", "enabled": True, "status": "healthy", "lastRun": "", "successRate": 100},
            {"id": "wf-file-quarantine", "name": "Quarantined file response", "enabled": True, "status": "idle", "lastRun": "", "successRate": 100},
        ]

    def shuffle_payload(self, conn: PostgresConnection, user: dict) -> dict:
        if not can_access_portal(user, "shuffle"):
            return {}
        workflows = self.shuffle_workflows(conn)
        return {
            "configured": bool(os.environ.get("SHUFFLE_API_URL")),
            "editorUrl": os.environ.get("SHUFFLE_EDITOR_URL", "https://shuffle.beenco.local"),
            "workflows": workflows,
            "executions": [
                {"id": "exec-local-1", "workflowId": "wf-file-quarantine", "status": "success", "durationMs": 1200, "triggeredBy": "portal-audit", "createdAt": utc_now()},
            ],
            "apps": [
                {"id": "email", "name": "Internal SMTP", "health": "pending-config"},
            ],
            "triggers": [
                {"id": "trig-quarantine", "workflowId": "wf-file-quarantine", "type": "webhook", "enabled": True},
            ],
        }

    def route_hr(self, conn: PostgresConnection, user: dict, method: str, path: str) -> None:
        if not can_access_portal(user, "hr"):
            self.write_error(403, "HR portal access required.")
            return
        if path == "/api/hr/employees" and method == "POST":
            self.create_employee(conn, user)
            return
        if path.startswith("/api/hr/employees/") and method == "PATCH":
            self.update_employee(conn, user, path.split("/")[4])
            return
        if path == "/api/hr/assets" and method == "POST":
            self.create_asset(conn, user)
            return
        if path.startswith("/api/hr/assets/") and path.endswith("/assign") and method == "POST":
            self.assign_asset(conn, user, path.split("/")[4])
            return
        if path == "/api/hr/attendance/corrections" and method == "POST":
            self.correct_attendance(conn, user)
            return
        if path == "/api/hr/leave" and method == "POST":
            self.create_leave_request(conn, user)
            return
        if path.startswith("/api/hr/leave/") and path.endswith("/decide") and method == "POST":
            self.decide_leave(conn, user, path.split("/")[4])
            return
        self.write_error(404, "Unknown HR route.")

    def create_employee(self, conn: PostgresConnection, user: dict) -> None:
        if not can_write_hr(user):
            self.write_error(403, "HR write access required.")
            return
        body = self.read_json()
        full_name = str(body.get("fullName", "")).strip()
        if not full_name:
            raise ValueError("Employee full name is required.")
        now = utc_now()
        employee_id = uid("emp")
        conn.execute(
            """
            INSERT INTO employees (
              id, user_id, full_name, cnic, email, phone, department, designation,
              employment_type, status, joining_date, manager_id, location, biometric_user_id,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                employee_id,
                body.get("userId") or None,
                full_name,
                body.get("cnic", ""),
                body.get("email", ""),
                body.get("phone", ""),
                body.get("department", ""),
                body.get("designation", ""),
                body.get("employmentType", "full-time"),
                body.get("status", "active"),
                body.get("joiningDate", now[:10]),
                body.get("managerId") or None,
                body.get("location", ""),
                body.get("biometricUserId", ""),
                now,
                now,
            ),
        )
        write_audit(conn, user["id"], "hr.employee.created", "employee", employee_id, {"fullName": full_name}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(201, {"ok": True, "employeeId": employee_id})

    def update_employee(self, conn: PostgresConnection, user: dict, employee_id: str) -> None:
        if not can_write_hr(user):
            self.write_error(403, "HR write access required.")
            return
        body = self.read_json()
        allowed = {
            "fullName": "full_name",
            "cnic": "cnic",
            "email": "email",
            "phone": "phone",
            "department": "department",
            "designation": "designation",
            "employmentType": "employment_type",
            "status": "status",
            "joiningDate": "joining_date",
            "exitDate": "exit_date",
            "managerId": "manager_id",
            "location": "location",
            "biometricUserId": "biometric_user_id",
        }
        fields = []
        params = []
        for api_key, db_key in allowed.items():
            if api_key in body:
                fields.append(f"{db_key} = ?")
                params.append(body[api_key] or None if api_key == "managerId" else body[api_key])
        if not fields:
            self.write_json(200, {"ok": True})
            return
        fields.append("updated_at = ?")
        params.extend([utc_now(), employee_id])
        conn.execute(f"UPDATE employees SET {', '.join(fields)} WHERE id = ?", params)
        write_audit(conn, user["id"], "hr.employee.updated", "employee", employee_id, {key: body[key] for key in body}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def create_asset(self, conn: PostgresConnection, user: dict) -> None:
        if not can_write_hr(user):
            self.write_error(403, "HR write access required.")
            return
        body = self.read_json()
        asset_tag = str(body.get("assetTag", "")).strip()
        category = str(body.get("category", "")).strip()
        if not asset_tag or not category:
            raise ValueError("Asset tag and category are required.")
        asset_id = uid("asset")
        now = utc_now()
        conn.execute(
            """
            INSERT INTO assets (id, asset_tag, category, model, serial, status, purchase_date, value, warranty_expiry, condition, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                asset_tag,
                category,
                body.get("model", ""),
                body.get("serial", ""),
                body.get("status", "available"),
                body.get("purchaseDate", ""),
                float(body.get("value", 0) or 0),
                body.get("warrantyExpiry", ""),
                body.get("condition", "good"),
                body.get("notes", ""),
                now,
                now,
            ),
        )
        write_audit(conn, user["id"], "hr.asset.created", "asset", asset_id, {"assetTag": asset_tag}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(201, {"ok": True, "assetId": asset_id})

    def assign_asset(self, conn: PostgresConnection, user: dict, asset_id: str) -> None:
        if not can_write_hr(user):
            self.write_error(403, "HR write access required.")
            return
        body = self.read_json()
        employee_id = body.get("employeeId")
        if not conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone():
            self.write_error(404, "Asset not found.")
            return
        if not conn.execute("SELECT 1 FROM employees WHERE id = ?", (employee_id,)).fetchone():
            self.write_error(404, "Employee not found.")
            return
        now = utc_now()
        assignment_id = uid("assign")
        # Closing the old assignment and opening the new one must happen together, or a
        # crash in between leaves the asset with either two open assignments or none.
        with conn.transaction():
            conn.execute("UPDATE asset_assignments SET returned_at = ? WHERE asset_id = ? AND COALESCE(returned_at, '') = ''", (now, asset_id))
            conn.execute(
                """
                INSERT INTO asset_assignments (id, asset_id, employee_id, assigned_at, expected_return, condition_out, assigned_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (assignment_id, asset_id, employee_id, body.get("assignedAt", now[:10]), body.get("expectedReturn", ""), body.get("conditionOut", ""), user["id"], now),
            )
            conn.execute("UPDATE assets SET status = 'assigned', updated_at = ? WHERE id = ?", (now, asset_id))
            write_audit(conn, user["id"], "hr.asset.assigned", "asset", asset_id, {"employeeId": employee_id}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True, "assignmentId": assignment_id})

    def correct_attendance(self, conn: PostgresConnection, user: dict) -> None:
        if not can_write_hr(user):
            self.write_error(403, "HR write access required.")
            return
        body = self.read_json()
        employee_id = body.get("employeeId")
        date = body.get("date") or utc_now()[:10]
        if not conn.execute("SELECT 1 FROM employees WHERE id = ?", (employee_id,)).fetchone():
            self.write_error(404, "Employee not found.")
            return
        attendance_id = uid("att")
        conn.execute(
            """
            INSERT INTO attendance_days (id, employee_id, date, first_in, last_out, worked_minutes, status, corrected_by, correction_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(employee_id, date) DO UPDATE SET
              first_in = excluded.first_in, last_out = excluded.last_out, worked_minutes = excluded.worked_minutes,
              status = excluded.status, corrected_by = excluded.corrected_by, correction_reason = excluded.correction_reason
            """,
            (
                attendance_id,
                employee_id,
                date,
                body.get("firstIn", ""),
                body.get("lastOut", ""),
                int(body.get("workedMinutes", 0) or 0),
                body.get("status", "present"),
                user["id"],
                body.get("reason", "Manual correction"),
                utc_now(),
            ),
        )
        write_audit(conn, user["id"], "hr.attendance.corrected", "employee", employee_id, {"date": date}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def create_leave_request(self, conn: PostgresConnection, user: dict) -> None:
        body = self.read_json()
        employee_id = body.get("employeeId")
        if not can_write_hr(user):
            own_employee = conn.execute("SELECT id FROM employees WHERE user_id = ?", (user["id"],)).fetchone()
            if not own_employee or own_employee["id"] != employee_id:
                self.write_error(403, "You can only request leave for yourself.")
                return
        if not conn.execute("SELECT 1 FROM employees WHERE id = ?", (employee_id,)).fetchone():
            self.write_error(404, "Employee not found.")
            return
        request_id = uid("leave")
        conn.execute(
            """
            INSERT INTO leave_requests (id, employee_id, leave_type, start_date, end_date, days, reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                request_id,
                employee_id,
                body.get("leaveType", "annual"),
                body.get("startDate"),
                body.get("endDate"),
                float(body.get("days", 1) or 1),
                body.get("reason", ""),
                utc_now(),
            ),
        )
        write_audit(conn, user["id"], "hr.leave.requested", "leave_request", request_id, {"employeeId": employee_id}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(201, {"ok": True, "leaveRequestId": request_id})

    def decide_leave(self, conn: PostgresConnection, user: dict, request_id: str) -> None:
        if not can_write_hr(user):
            self.write_error(403, "HR write access required.")
            return
        body = self.read_json()
        status = body.get("status")
        if status not in ("approved", "rejected"):
            raise ValueError("Leave decision must be approved or rejected.")
        conn.execute(
            "UPDATE leave_requests SET status = ?, approver_id = ?, decided_at = ? WHERE id = ?",
            (status, user["id"], utc_now(), request_id),
        )
        write_audit(conn, user["id"], f"hr.leave.{status}", "leave_request", request_id, {}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def route_shuffle(self, conn: PostgresConnection, user: dict, method: str, path: str, query: dict) -> None:
        if not can_access_portal(user, "shuffle"):
            self.write_error(403, "Shuffle portal access required.")
            return
        if path.startswith("/api/shuffle/workflows/") and path.endswith("/execute") and method == "POST":
            workflow_id = path.split("/")[4]
            body = self.read_json()
            write_audit(conn, user["id"], "shuffle.workflow.executed", "shuffle_workflow", workflow_id, body, self.client_ip(), self.headers.get("User-Agent", ""))
            self.write_json(200, {"ok": True, "executionId": uid("exec"), "mode": "audited-local-hook"})
            return
        self.write_error(404, "Unknown Shuffle route.")

    def remote_assist_sessions(self, conn: PostgresConnection, user: dict, limit: int = 80) -> list[dict]:
        safe_limit = max(1, min(int(limit), 100))
        rows = conn.execute(
            """
            SELECT * FROM remote_assist_sessions
            WHERE requester_id = ? OR target_user_id = ?
            ORDER BY
              CASE status WHEN 'pending' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END,
              updated_at DESC
            LIMIT ?
            """,
            (user["id"], user["id"], safe_limit),
        ).fetchall()
        return [serialize_remote_assist(conn, row) for row in rows]

    def notify_user(self, conn: PostgresConnection, user_id: str, notification_type: str, payload: dict) -> None:
        conn.execute(
            "INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid("note"), user_id, notification_type, json_dumps(payload), utc_now()),
        )

    def create_remote_assist(self, conn: PostgresConnection, user: dict) -> None:
        body = self.read_json()
        target_id = str(body.get("targetUserId") or "").strip()
        if not target_id:
            raise ValueError("Select an employee to request remote assistance from.")
        if target_id == user["id"]:
            raise ValueError("Select a different employee.")
        target = conn.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (target_id,)).fetchone()
        if not target:
            self.write_error(404, "Selected employee was not found.")
            return
        target_host = clean_remote_host(body.get("targetHost", ""))
        reason = str(body.get("reason") or "").strip()[:500]
        now = utc_now()
        session_id = uid("rdp")
        conn.execute(
            """
            INSERT INTO remote_assist_sessions (id, requester_id, target_user_id, target_host, reason, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (session_id, user["id"], target_id, target_host, reason, now, now),
        )
        payload = {"sessionId": session_id, "requesterId": user["id"], "targetUserId": target_id, "reason": reason}
        self.notify_user(conn, target_id, "remote_assist.request", payload)
        write_audit(conn, user["id"], "remote_assist.requested", "remote_assist", session_id, payload, self.client_ip(), self.headers.get("User-Agent", ""))
        row = conn.execute("SELECT * FROM remote_assist_sessions WHERE id = ?", (session_id,)).fetchone()
        self.write_json(201, {"ok": True, "session": serialize_remote_assist(conn, row)})

    def respond_remote_assist(self, conn: PostgresConnection, user: dict, session_id: str) -> None:
        body = self.read_json()
        response = str(body.get("response") or "").lower()
        if response not in {"accepted", "rejected"}:
            raise ValueError("Remote assistance response must be accepted or rejected.")
        row = conn.execute("SELECT * FROM remote_assist_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            self.write_error(404, "Remote assistance request was not found.")
            return
        if row["target_user_id"] != user["id"]:
            self.write_error(403, "Only the requested employee can respond.")
            return
        if row["status"] == response:
            self.write_json(200, {"ok": True, "session": serialize_remote_assist(conn, row)})
            return
        if row["status"] != "pending":
            self.write_error(409, "This remote assistance request has already been handled.")
            return
        now = utc_now()
        if response == "accepted":
            target_host = clean_remote_host(body.get("targetHost") or row["target_host"])
            if not target_host:
                raise ValueError("Enter this PC's LAN name or IP address before accepting.")
            conn.execute(
                "UPDATE remote_assist_sessions SET status = 'accepted', target_host = ?, responded_at = ?, updated_at = ? WHERE id = ?",
                (target_host, now, now, session_id),
            )
            notification_type = "remote_assist.accepted"
        else:
            conn.execute(
                "UPDATE remote_assist_sessions SET status = 'rejected', responded_at = ?, ended_at = ?, updated_at = ? WHERE id = ?",
                (now, now, now, session_id),
            )
            notification_type = "remote_assist.rejected"
        payload = {"sessionId": session_id, "userId": user["id"], "targetUserId": row["target_user_id"], "requesterId": row["requester_id"], "response": response}
        self.notify_user(conn, row["requester_id"], notification_type, payload)
        write_audit(conn, user["id"], notification_type, "remote_assist", session_id, payload, self.client_ip(), self.headers.get("User-Agent", ""))
        updated = conn.execute("SELECT * FROM remote_assist_sessions WHERE id = ?", (session_id,)).fetchone()
        self.write_json(200, {"ok": True, "session": serialize_remote_assist(conn, updated)})

    def end_remote_assist(self, conn: PostgresConnection, user: dict, session_id: str) -> None:
        row = conn.execute("SELECT * FROM remote_assist_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            self.write_error(404, "Remote assistance session was not found.")
            return
        if user["id"] not in {row["requester_id"], row["target_user_id"]}:
            self.write_error(403, "You are not part of this remote assistance session.")
            return
        if row["status"] in {"ended", "rejected", "cancelled"}:
            self.write_json(200, {"ok": True, "session": serialize_remote_assist(conn, row)})
            return
        now = utc_now()
        status = "cancelled" if row["status"] == "pending" else "ended"
        conn.execute(
            "UPDATE remote_assist_sessions SET status = ?, ended_at = ?, updated_at = ? WHERE id = ?",
            (status, now, now, session_id),
        )
        other_id = row["target_user_id"] if user["id"] == row["requester_id"] else row["requester_id"]
        payload = {"sessionId": session_id, "userId": user["id"], "status": status}
        self.notify_user(conn, other_id, "remote_assist.ended", payload)
        write_audit(conn, user["id"], f"remote_assist.{status}", "remote_assist", session_id, payload, self.client_ip(), self.headers.get("User-Agent", ""))
        updated = conn.execute("SELECT * FROM remote_assist_sessions WHERE id = ?", (session_id,)).fetchone()
        self.write_json(200, {"ok": True, "session": serialize_remote_assist(conn, updated)})

    def create_channel(self, conn: PostgresConnection, user: dict) -> None:
        body = self.read_json()
        name = str(body.get("name", "")).strip()
        channel_type = body.get("type", "public")
        if channel_type not in ("public", "private"):
            raise ValueError("Channel type must be public or private.")
        if not name:
            raise ValueError("Group or channel name is required.")
        if channel_type == "public" and user["role"] not in INTRANET_ADMIN_ROLES:
            self.write_error(403, "Admin role required to create public channels.")
            return
        channel_id = uid("ch")
        slug = slugify(name)
        suffix = 1
        candidate = slug
        while conn.execute("SELECT 1 FROM channels WHERE slug = ?", (candidate,)).fetchone():
            suffix += 1
            candidate = f"{slug}-{suffix}"
        members = [str(member_id) for member_id in (body.get("memberIds") or []) if member_id]
        members.append(user["id"])
        if channel_type == "public":
            members = [row["id"] for row in conn.execute("SELECT id FROM users WHERE is_active = 1").fetchall()]
        else:
            requested_members = sorted(set(members))
            member_placeholders = ",".join("?" for _ in requested_members)
            active_members = {
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM users WHERE is_active = 1 AND id IN ({member_placeholders})",
                    tuple(requested_members),
                ).fetchall()
            }
            members = list(active_members | {user["id"]})
        # A crash partway through would otherwise leave a channel with no members and no owner.
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO channels (id, name, slug, type, topic, description, created_by, retention_days, legal_hold, locked, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (channel_id, name, candidate, channel_type, body.get("topic", ""), body.get("description", ""), user["id"], body.get("retentionDays"), utc_now()),
            )
            for member_id in sorted(set(members)):
                role = "owner" if member_id == user["id"] else "member"
                conn.execute("INSERT OR IGNORE INTO channel_members (channel_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)", (channel_id, member_id, role, utc_now()))
            write_audit(conn, user["id"], "channel.created", "channel", channel_id, {"type": channel_type}, self.client_ip(), self.headers.get("User-Agent", ""))
        LIVE_BROKER.broadcast("channel:updated", {"channelId": channel_id})
        self.write_json(201, {"ok": True, "channelId": channel_id})

    def create_dm(self, conn: PostgresConnection, user: dict) -> None:
        body = self.read_json()
        other_id = body.get("userId")
        if not other_id or other_id == user["id"]:
            raise ValueError("Select a different user.")
        other = conn.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (other_id,)).fetchone()
        if not other:
            raise ValueError("User not found.")
        memberships = conn.execute(
            """
            SELECT c.id FROM channels c
            JOIN channel_members a ON a.channel_id = c.id AND a.user_id = ?
            JOIN channel_members b ON b.channel_id = c.id AND b.user_id = ?
            WHERE c.type = 'dm'
            """,
            (user["id"], other_id),
        ).fetchall()
        if memberships:
            self.write_json(200, {"ok": True, "channelId": memberships[0]["id"]})
            return
        channel_id = uid("chan")
        name = f"dm-{user['display_name']}-{other['display_name']}"
        slug = slugify(f"dm-{user['id']}-{other_id}")
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO channels (id, name, slug, type, topic, description, created_by, legal_hold, locked, created_at)
                VALUES (?, ?, ?, 'dm', 'Direct message', 'Private direct conversation.', ?, 0, 0, ?)
                """,
                (channel_id, name, slug, user["id"], utc_now()),
            )
            for member_id in (user["id"], other_id):
                conn.execute("INSERT INTO channel_members (channel_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)", (channel_id, member_id, utc_now()))
            write_audit(conn, user["id"], "dm.created", "channel", channel_id, {"memberId": other_id}, self.client_ip(), self.headers.get("User-Agent", ""))
        LIVE_BROKER.broadcast("channel:updated", {"channelId": channel_id})
        self.write_json(201, {"ok": True, "channelId": channel_id})

    def add_channel_member(self, conn: PostgresConnection, user: dict, channel_id: str) -> None:
        channel = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if not channel:
            self.write_error(404, "Group or channel not found.")
            return
        if channel["type"] not in ("private", "public"):
            self.write_error(400, "Members can only be added to channels or groups.")
            return
        if not is_channel_owner(conn, user, channel_id):
            self.write_error(403, "Only the group owner or an admin can add members.")
            return
        body = self.read_json()
        target_id = str(body.get("userId", "")).strip()
        target = conn.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (target_id,)).fetchone()
        if not target:
            raise ValueError("User not found.")
        conn.execute("INSERT OR IGNORE INTO channel_members (channel_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)", (channel_id, target_id, utc_now()))
        write_audit(conn, user["id"], "channel.member.added", "channel", channel_id, {"userId": target_id}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def remove_channel_member(self, conn: PostgresConnection, user: dict, channel_id: str, target_id: str) -> None:
        channel = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if not channel:
            self.write_error(404, "Group or channel not found.")
            return
        if channel["type"] not in ("private", "public"):
            self.write_error(400, "Members can only be removed from channels or groups.")
            return
        if not is_channel_owner(conn, user, channel_id) and user["id"] != target_id:
            self.write_error(403, "Only the group owner, an admin, or the member themselves can remove this membership.")
            return
        owner_row = conn.execute("SELECT role FROM channel_members WHERE channel_id = ? AND user_id = ?", (channel_id, target_id)).fetchone()
        if owner_row and owner_row["role"] == "owner":
            remaining_owners = conn.execute("SELECT COUNT(*) AS total FROM channel_members WHERE channel_id = ? AND role = 'owner' AND user_id != ?", (channel_id, target_id)).fetchone()["total"]
            if remaining_owners == 0:
                self.write_error(400, "Assign another owner before removing the last group owner.")
                return
        conn.execute("DELETE FROM channel_members WHERE channel_id = ? AND user_id = ?", (channel_id, target_id))
        write_audit(conn, user["id"], "channel.member.removed", "channel", channel_id, {"userId": target_id}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def delete_channel(self, conn: PostgresConnection, user: dict, channel_id: str) -> None:
        channel = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if not channel:
            self.write_error(404, "Channel not found.")
            return

        slug = (channel.get("slug") or "").strip().lower()
        if slug in ("general", "announcements"):
            self.write_error(400, f"The '#{channel['name']}' channel is a protected workspace channel and cannot be deleted.")
            return

        if not is_channel_owner(conn, user, channel_id):
            self.write_error(403, "Only the channel creator/owner, an Admin, or a Super Admin can delete this channel.")
            return

        channel_name = channel["name"]
        # A crash mid-delete would otherwise leave orphaned messages/tasks with no channel.
        with conn.transaction():
            conn.execute("DELETE FROM channel_read_states WHERE channel_id = ?", (channel_id,))
            conn.execute("DELETE FROM channel_members WHERE channel_id = ?", (channel_id,))
            conn.execute("DELETE FROM workspace_tasks WHERE channel_id = ?", (channel_id,))
            conn.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
            conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))

            write_audit(
                conn,
                user["id"],
                "channel.deleted",
                "channel",
                channel_id,
                {"name": channel_name, "slug": slug, "type": channel["type"]},
                self.client_ip(),
                self.headers.get("User-Agent", ""),
            )
        remove_upload_dir(channel_id)
        self.write_json(200, {"ok": True, "deletedChannelId": channel_id, "name": channel_name})

    def create_workspace_task(self, conn: PostgresConnection, user: dict) -> None:
        body = self.read_json()
        channel_id = str(body.get("channelId", "")).strip()
        if not can_access_channel(conn, user, channel_id, write=True):
            self.write_error(403, "No access to this group or channel.")
            return
        if not is_channel_owner(conn, user, channel_id):
            self.write_error(403, "Only the group/channel owner or an admin can create tasks here.")
            return
        title = str(body.get("title", "")).strip()
        if not title:
            raise ValueError("Task title is required.")
        subject = str(body.get("subject", "")).strip()
        deadline = str(body.get("deadline", "")).strip()
        if deadline:
            try:
                datetime.strptime(deadline, "%Y-%m-%d")
            except ValueError:
                raise ValueError("Deadline must use YYYY-MM-DD format.")
        assignee_id = str(body.get("assigneeId") or "").strip() or None
        if assignee_id:
            assignee = conn.execute(
                """
                SELECT users.id FROM users
                JOIN channel_members ON channel_members.user_id = users.id
                WHERE users.id = ? AND users.is_active = 1 AND channel_members.channel_id = ?
                """,
                (assignee_id, channel_id),
            ).fetchone()
            if not assignee:
                raise ValueError("Task assignee must be an active member of this group.")
        links = list_from_payload(body.get("referenceLinks"))
        tags = [item[:40] for item in list_from_payload(body.get("tags"))]
        task_id = uid("task")
        now = utc_now()
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO workspace_tasks (
                  id, channel_id, assignee_id, creator_id, title, subject,
                  reference_links, deadline, tags, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (task_id, channel_id, assignee_id, user["id"], title, subject, json_dumps(links), deadline, json_dumps(tags), now, now),
            )
            summary = [f"Task created: **{title}**"]
            if subject:
                summary.append(subject)
            if deadline:
                summary.append(f"Deadline: {deadline}")
            if assignee_id:
                summary.append(f"Assigned to @{conn.execute('SELECT username FROM users WHERE id = ?', (assignee_id,)).fetchone()['username']}")
            if tags:
                summary.append("Tags: " + ", ".join(tags))
            if links:
                summary.append("Reference links:\n" + "\n".join(links))
            message_id = uid("msg")
            conn.execute(
                "INSERT INTO messages (id, channel_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (message_id, channel_id, user["id"], "\n".join(summary), now),
            )
            for member in conn.execute(
                "SELECT user_id FROM channel_members WHERE channel_id = ? AND user_id != ?",
                (channel_id, user["id"]),
            ).fetchall():
                conn.execute(
                    "INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (?, ?, 'task.created', ?, ?)",
                    (uid("note"), member["user_id"], json_dumps({"channelId": channel_id, "taskId": task_id, "messageId": message_id, "snippet": title}), now),
                )
            write_audit(conn, user["id"], "task.created", "task", task_id, {"channelId": channel_id, "deadline": deadline, "tags": tags}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(201, {"ok": True, "taskId": task_id, "messageId": message_id})

    def update_workspace_task(self, conn: PostgresConnection, user: dict, task_id: str) -> None:
        row = conn.execute("SELECT * FROM workspace_tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            self.write_error(404, "Task not found.")
            return
        if not can_access_channel(conn, user, row["channel_id"]):
            self.write_error(403, "No access to this task.")
            return
        body = self.read_json()
        status = str(body.get("status", "")).strip()
        if status not in {"open", "in_progress", "done", "cancelled"}:
            raise ValueError("Unsupported task status.")
        if user["role"] not in INTRANET_ADMIN_ROLES and user["id"] not in {row["assignee_id"], row["creator_id"]}:
            self.write_error(403, "Only admins, creators, or assignees can update this task.")
            return
        conn.execute("UPDATE workspace_tasks SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), task_id))
        write_audit(conn, user["id"], "task.updated", "task", task_id, {"status": status}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def create_message(self, conn: PostgresConnection, user: dict) -> None:
        body = self.read_json()
        channel_id = body.get("channelId")
        if not can_access_channel(conn, user, channel_id, write=True):
            self.write_error(403, "No access to this channel.")
            return
        channel = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if channel["locked"] and user["role"] not in INTRANET_ADMIN_ROLES:
            self.write_error(423, "Channel is locked.")
            return
        message_body = str(body.get("body", "")).strip()
        if not message_body:
            self.write_error(400, "Message body is required.")
            return
        parent_id = body.get("parentId") or None
        if parent_id is not None:
            parent = conn.execute("SELECT channel_id FROM messages WHERE id = ?", (str(parent_id),)).fetchone()
            if not parent or parent["channel_id"] != channel_id:
                raise ValueError("Thread parent must be a message in this channel.")
        attachments = body.get("attachments") or []
        if not isinstance(attachments, list) or not all(isinstance(item, str) for item in attachments):
            raise ValueError("Attachments must be a list of file IDs.")
        attachments = list(dict.fromkeys(attachments))
        if attachments:
            placeholders = ",".join("?" for _ in attachments)
            matching = conn.execute(f"SELECT COUNT(*) AS total FROM files WHERE id IN ({placeholders}) AND channel_id = ?", (*attachments, channel_id)).fetchone()["total"]
            if matching != len(attachments):
                raise ValueError("Attachments must be files shared in this channel.")
        message_id = uid("msg")
        is_urgent = 1 if body.get("isUrgent") else 0
        # The message, its attachments, read-state bump, and every notification it triggers
        # commit together — otherwise a mid-request crash could leave a message with missing
        # attachments or with mentions/tags that silently never notified anyone.
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO messages (id, channel_id, author_id, parent_id, body, is_urgent, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, channel_id, user["id"], parent_id, message_body, is_urgent, utc_now()),
            )
            if not parent_id:
                conn.execute(
                    "UPDATE channel_members SET last_read_message_id = ? WHERE channel_id = ? AND user_id = ?",
                    (message_id, channel_id, user["id"]),
                )
            for file_id in attachments:
                conn.execute("INSERT OR IGNORE INTO message_attachments (message_id, file_id) VALUES (?, ?)", (message_id, file_id))
            lower_body = message_body.lower()
            notify_all = "@channel" in lower_body or "@here" in lower_body
            members = conn.execute(
                """
                SELECT users.* FROM channel_members
                JOIN users ON users.id = channel_members.user_id
                WHERE channel_members.channel_id = ? AND users.id != ? AND users.is_active = 1
                """,
                (channel_id, user["id"]),
            ).fetchall()
            for member in members:
                mentioned = notify_all or f"@{member['username']}".lower() in lower_body
                if mentioned:
                    conn.execute(
                        "INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (?, ?, 'mention', ?, ?)",
                        (
                            uid("note"),
                            member["id"],
                            json_dumps({"channelId": channel_id, "messageId": message_id, "authorId": user["id"], "snippet": message_body[:160]}),
                            utc_now(),
                        ),
                    )
            if is_urgent:
                urgent_recipients = members
                for rec in urgent_recipients:
                    conn.execute(
                        "INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (?, ?, 'urgent', ?, ?)",
                        (
                            uid("note"),
                            rec["id"],
                            json_dumps({
                                "channelId": channel_id,
                                "channelName": channel["name"] if channel else "",
                                "messageId": message_id,
                                "authorId": user["id"],
                                "authorName": user["display_name"],
                                "authorUsername": user["username"],
                                "snippet": message_body[:180],
                                "isUrgent": True,
                            }),
                            utc_now(),
                        ),
                    )
                write_audit(conn, user["id"], "message.created.urgent", "message", message_id, {"channelId": channel_id}, self.client_ip(), self.headers.get("User-Agent", ""))
            hashtags = list(dict.fromkeys(re.findall(r"#([A-Za-z0-9_\-]+)", message_body)))
            if hashtags and channel and channel["type"] in ("public", "private"):
                super_admins = conn.execute(
                    "SELECT * FROM users WHERE role IN ('super_admin', 'admin') AND is_active = 1"
                ).fetchall()
                tag_names = [f"#{h}" for h in hashtags]
                is_oro = any(h.lower() == "oro" for h in hashtags)
                for sa in super_admins:
                    conn.execute(
                        "INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (?, ?, 'tag', ?, ?)",
                        (
                            uid("note"),
                            sa["id"],
                            json_dumps({
                                "channelId": channel_id,
                                "channelName": channel["name"] if channel else "",
                                "messageId": message_id,
                                "authorId": user["id"],
                                "authorName": user["display_name"],
                                "authorUsername": user["username"],
                                "tags": tag_names,
                                "tag": tag_names[0],
                                "isOro": is_oro,
                                "snippet": message_body[:180],
                            }),
                            utc_now(),
                        ),
                    )
                write_audit(conn, user["id"], "tag.alert", "message", message_id, {"tags": hashtags, "channelId": channel_id, "isOro": is_oro}, self.client_ip(), self.headers.get("User-Agent", ""))
            write_audit(conn, user["id"], "message.created", "message", message_id, {"channelId": channel_id, "parentId": parent_id, "isUrgent": is_urgent}, self.client_ip(), self.headers.get("User-Agent", ""))
        LIVE_BROKER.broadcast("message:created", {"channelId": channel_id, "messageId": message_id, "authorId": user["id"], "isUrgent": is_urgent})
        for fid in attachments:
            LIVE_BROKER.broadcast("file:uploaded", {"channelId": channel_id, "fileId": fid, "messageId": message_id})
        self.write_json(201, {"ok": True, "messageId": message_id})

    def acknowledge_message(self, conn: PostgresConnection, user: dict, message_id: str) -> None:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not row:
            self.write_error(404, "Message not found.")
            return
        if not can_access_channel(conn, user, row["channel_id"]):
            self.write_error(403, "No access to this channel.")
            return
        now = utc_now()
        conn.execute(
            "UPDATE messages SET acknowledged_at = ?, acknowledged_by = ? WHERE id = ?",
            (now, user["id"], message_id),
        )
        conn.execute(
            "UPDATE notifications SET read_at = ? WHERE user_id = ? AND payload LIKE ? AND read_at IS NULL",
            (now, user["id"], f'%"{message_id}"%'),
        )
        write_audit(conn, user["id"], "message.acknowledged", "message", message_id, {"channelId": row["channel_id"]}, self.client_ip(), self.headers.get("User-Agent", ""))
        LIVE_BROKER.broadcast("message:acknowledged", {"channelId": row["channel_id"], "messageId": message_id, "userId": user["id"]})
        self.write_json(200, {
            "ok": True,
            "messageId": message_id,
            "acknowledgedAt": now,
            "acknowledgedBy": user["id"],
            "acknowledgedByName": user["display_name"],
        })

    def toggle_reaction(self, conn: PostgresConnection, user: dict, message_id: str) -> None:
        body = self.read_json()
        emoji = str(body.get("emoji", ""))
        if not emoji:
            raise ValueError("Emoji is required.")
        message = conn.execute("SELECT channel_id FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not message:
            self.write_error(404, "Message not found.")
            return
        if not can_access_channel(conn, user, message["channel_id"], write=True):
            self.write_error(403, "No access to this channel.")
            return
        existing = conn.execute("SELECT 1 FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?", (message_id, user["id"], emoji)).fetchone()
        if existing:
            conn.execute("DELETE FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?", (message_id, user["id"], emoji))
            action = "reaction.removed"
        else:
            conn.execute("INSERT INTO reactions (message_id, user_id, emoji, created_at) VALUES (?, ?, ?, ?)", (message_id, user["id"], emoji, utc_now()))
            action = "reaction.added"
        write_audit(conn, user["id"], action, "message", message_id, {"emoji": emoji}, self.client_ip(), self.headers.get("User-Agent", ""))
        LIVE_BROKER.broadcast("reaction:updated", {"channelId": message["channel_id"], "messageId": message_id})
        self.write_json(200, {"ok": True})

    def edit_message(self, conn: PostgresConnection, user: dict, message_id: str) -> None:
        body = self.read_json()
        new_body = str(body.get("body", "")).strip()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not row:
            self.write_error(404, "Message not found.")
            return
        if row["deleted_at"]:
            self.write_error(409, "Deleted messages cannot be edited.")
            return
        if not new_body:
            self.write_error(400, "Edited message cannot be empty.")
            return
        # Only the author can edit their own message — no role override, matching the same
        # author-only rule as delete_message.
        if row["author_id"] != user["id"]:
            self.write_error(403, "Only the message's author can edit it.")
            return
        history = json_loads(row["edit_history"], [])
        edited_at = utc_now()
        history.append({"body": row["body"], "editedAt": edited_at, "editorId": user["id"]})
        conn.execute("UPDATE messages SET body = ?, edited_at = ?, edit_history = ? WHERE id = ?", (new_body, edited_at, json_dumps(history), message_id))
        write_audit(conn, user["id"], "message.edited", "message", message_id, {"historyCount": len(history)}, self.client_ip(), self.headers.get("User-Agent", ""))
        LIVE_BROKER.broadcast("message:edited", {"channelId": row["channel_id"], "messageId": message_id})
        self.write_json(200, {"ok": True})

    def delete_message(self, conn: PostgresConnection, user: dict, message_id: str) -> None:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not row:
            self.write_error(404, "Message not found.")
            return
        # Only the author can delete their own message — no role, including super_admin, may
        # delete someone else's. (Admins can still edit_message to redact content, and channel
        # deletion/retention policy still apply for bulk moderation.)
        if row["author_id"] != user["id"]:
            self.write_error(403, "Only the message's author can delete it.")
            return
        conn.execute("UPDATE messages SET deleted_at = ? WHERE id = ?", (utc_now(), message_id))
        write_audit(conn, user["id"], "message.deleted", "message", message_id, {}, self.client_ip(), self.headers.get("User-Agent", ""))
        LIVE_BROKER.broadcast("message:deleted", {"channelId": row["channel_id"], "messageId": message_id})
        self.write_json(200, {"ok": True})

    def create_file_upload(self, conn: PostgresConnection, user: dict) -> None:
        fields, uploaded_files = self.read_multipart()
        uploaded = uploaded_files.get("file")
        if not uploaded:
            raise ValueError("Attach a file to upload.")
        channel_id = fields.get("channelId", "")
        if not can_access_channel(conn, user, channel_id, write=True):
            self.write_error(403, "No access to this channel.")
            return
        content = uploaded["content"]
        body = {
            "channelId": channel_id,
            "originalName": fields.get("originalName") or uploaded["filename"],
            "mime": fields.get("mime") or uploaded["contentType"],
            "sizeBytes": len(content),
            "kind": "file",
            "extractedText": fields.get("extractedText") or uploaded["filename"],
            "messageBody": fields.get("messageBody") or "",
        }
        try:
            metadata = validate_upload_metadata(body)
        except RequestTooLarge as error:
            write_audit(conn, user["id"], "file.upload.rejected", "file", str(body.get("originalName", "unknown")), {"reason": "request_too_large", "message": str(error)}, self.client_ip(), self.headers.get("User-Agent", ""))
            raise
        except ValueError as error:
            write_audit(conn, user["id"], "file.upload.rejected", "file", str(body.get("originalName", "unknown")), {"reason": "policy", "message": str(error)}, self.client_ip(), self.headers.get("User-Agent", ""))
            raise

        file_id = uid("file")
        original_name = metadata["originalName"]
        storage_path = upload_storage_path(channel_id, file_id, original_name)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(content)
        version = conn.execute("SELECT COUNT(*) + 1 AS version FROM files WHERE channel_id = ? AND original_name = ?", (channel_id, original_name)).fetchone()["version"]
        checksum = hashlib.sha256(content).hexdigest()
        storage_key = f"{channel_id}/{file_id}/{original_name}"
        attach_only = str(fields.get("attachOnly", "")).lower() in ("1", "true") or str(fields.get("createMessage", "")).lower() in ("0", "false")

        # The bytes are already safely on disk by this point (worst case on a crash: an orphan
        # file nothing points to). The file/message/attachment rows below must land together,
        # or the UI would show a message with a broken attachment link.
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO files (
                  id, channel_id, uploader_id, original_name, mime, size_bytes, status, checksum,
                  version, storage_key, kind, duration, waveform, preview_data_url, extracted_text, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, 'file', 0, '[]', '', ?, ?)
                """,
                (
                    file_id,
                    channel_id,
                    user["id"],
                    original_name,
                    metadata["mime"],
                    len(content),
                    checksum,
                    version,
                    storage_key,
                    body.get("extractedText", original_name),
                    utc_now(),
                ),
            )
            if not attach_only:
                message_id = uid("msg")
                body_text = body.get("messageBody") or f"Shared file **{original_name}**"
                conn.execute("INSERT INTO messages (id, channel_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)", (message_id, channel_id, user["id"], body_text, utc_now()))
                conn.execute("INSERT INTO message_attachments (message_id, file_id) VALUES (?, ?)", (message_id, file_id))
                write_audit(conn, user["id"], "file.upload.completed", "file", file_id, {"protocol": "multipart", "storage": "local", "messageId": message_id, "checksum": checksum}, self.client_ip(), self.headers.get("User-Agent", ""))
            else:
                message_id = None
                write_audit(conn, user["id"], "file.upload.staged", "file", file_id, {"protocol": "multipart", "storage": "local", "checksum": checksum}, self.client_ip(), self.headers.get("User-Agent", ""))

        if not attach_only:
            LIVE_BROKER.broadcast("file:uploaded", {"channelId": channel_id, "fileId": file_id, "messageId": message_id})
        self.write_json(201, {"ok": True, "fileId": file_id, "messageId": message_id, "staged": attach_only})

    def create_file(self, conn: PostgresConnection, user: dict) -> None:
        body = self.read_json()
        channel_id = body.get("channelId")
        if not can_access_channel(conn, user, channel_id, write=True):
            self.write_error(403, "No access to this channel.")
            return
        try:
            metadata = validate_upload_metadata(body)
        except RequestTooLarge as error:
            write_audit(conn, user["id"], "file.upload.rejected", "file", str(body.get("originalName", "unknown")), {"reason": "request_too_large", "message": str(error)}, self.client_ip(), self.headers.get("User-Agent", ""))
            raise
        except ValueError as error:
            write_audit(conn, user["id"], "file.upload.rejected", "file", str(body.get("originalName", "unknown")), {"reason": "policy", "message": str(error)}, self.client_ip(), self.headers.get("User-Agent", ""))
            raise
        file_id = uid("file")
        original_name = metadata["originalName"]
        version = conn.execute("SELECT COUNT(*) + 1 AS version FROM files WHERE channel_id = ? AND original_name = ?", (channel_id, original_name)).fetchone()["version"]
        kind = metadata["kind"]
        try:
            duration = max(0, min(int(body.get("duration", 0) or 0), 3600))
        except (TypeError, ValueError):
            raise ValueError("Duration must be a number of seconds.")
        raw_waveform = body.get("waveform", [])
        waveform = [max(0, min(int(level), 100)) for level in raw_waveform[:64] if isinstance(level, (int, float))] if isinstance(raw_waveform, list) else []
        preview_data_url = ""
        audio_bytes: bytes | None = None
        if kind == "voice":
            preview_data_url = str(body.get("previewDataUrl", "")).strip() or fallback_voice_data_url(duration)
            # Decode the base64 audio payload so the file can be downloaded later
            if preview_data_url.startswith("data:") and ";base64," in preview_data_url:
                try:
                    audio_bytes = base64.b64decode(preview_data_url.split(";base64,", 1)[1])
                except Exception:
                    audio_bytes = None
        storage_path = upload_storage_path(channel_id, file_id, original_name)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        if audio_bytes is not None:
            storage_path.write_bytes(audio_bytes)
            size = len(audio_bytes)
            checksum = hashlib.sha256(audio_bytes).hexdigest()
        else:
            size = metadata["sizeBytes"]
            checksum = hashlib.sha256(f"{file_id}:{original_name}".encode()).hexdigest()
        # The bytes are already on disk by this point; the file/message/attachment rows below
        # must land together or the UI would show a message with a broken attachment link.
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO files (
                  id, channel_id, uploader_id, original_name, mime, size_bytes, status, checksum,
                  version, storage_key, kind, duration, waveform, preview_data_url, extracted_text, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    channel_id,
                    user["id"],
                    original_name,
                    metadata["mime"],
                    size,
                    checksum,
                    version,
                    f"{channel_id}/{file_id}/{original_name}",
                    kind,
                    duration,
                    json_dumps(waveform),
                    preview_data_url,
                    body.get("extractedText", original_name),
                    utc_now(),
                ),
            )
            message_id = uid("msg")
            body_text = body.get("messageBody") or ("Voice message" if kind == "voice" else f"Shared file **{original_name}**")
            conn.execute("INSERT INTO messages (id, channel_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)", (message_id, channel_id, user["id"], body_text, utc_now()))
            conn.execute("INSERT INTO message_attachments (message_id, file_id) VALUES (?, ?)", (message_id, file_id))
            write_audit(conn, user["id"], "file.upload.completed", "file", file_id, {"protocol": "local", "storage": "local", "messageId": message_id}, self.client_ip(), self.headers.get("User-Agent", ""))
        LIVE_BROKER.broadcast("file:uploaded", {"channelId": channel_id, "fileId": file_id, "messageId": message_id})
        self.write_json(201, {"ok": True, "fileId": file_id, "messageId": message_id})

    def get_file_info(self, conn: PostgresConnection, user: dict, file_id: str) -> None:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            self.write_error(404, "File not found.")
            return
        if not can_access_channel(conn, user, row["channel_id"]):
            self.write_error(403, "No access to this file.")
            return
        self.write_json(200, {"ok": True, "file": serialize_file(row)})

    def scan_file(self, conn: PostgresConnection, user: dict, file_id: str) -> None:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            self.write_error(404, "File not found.")
            return
        if not can_access_channel(conn, user, row["channel_id"], write=True):
            self.write_error(403, "No access to this file.")
            return
        # This is the extension/MIME allowlist re-check only. No antivirus engine is wired in
        # (ClamAV is still a roadmap item), and the audit record says so rather than claiming one.
        extension = Path(row["original_name"]).suffix.lower()
        risky = extension in BLOCKED_FILE_EXTENSIONS or (row["kind"] != "voice" and extension not in ALLOWED_FILE_EXTENSIONS)
        status = "quarantined" if risky else "available"
        conn.execute("UPDATE files SET status = ? WHERE id = ?", (status, file_id))
        write_audit(conn, user["id"], "file.scan.quarantined" if risky else "file.scan.clean", "file", file_id, {"scanner": "extension-policy", "status": status}, self.client_ip(), self.headers.get("User-Agent", ""))
        LIVE_BROKER.broadcast("file:updated", {"fileId": file_id, "channelId": row["channel_id"], "status": status})
        self.write_json(200, {"ok": True, "status": status})

    def download_file(self, conn: PostgresConnection, user: dict, file_id: str) -> None:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            self.write_error(404, "File not found.")
            return
        if not can_access_channel(conn, user, row["channel_id"]):
            self.write_error(403, "No access to this file.")
            return
        if row["status"] != "available":
            self.write_error(409, "File is not available.")
            return
        storage_path = upload_storage_path(row["channel_id"], row["id"], row["original_name"])
        # Recover legacy voice messages whose bytes were only stored as base64 in preview_data_url
        if not storage_path.exists() and row["kind"] == "voice":
            preview = row["preview_data_url"] or ""
            if preview.startswith("data:") and ";base64," in preview:
                try:
                    audio_bytes = base64.b64decode(preview.split(";base64,", 1)[1])
                    storage_path.parent.mkdir(parents=True, exist_ok=True)
                    storage_path.write_bytes(audio_bytes)
                    conn.execute(
                        "UPDATE files SET size_bytes = ?, checksum = ? WHERE id = ?",
                        (len(audio_bytes), hashlib.sha256(audio_bytes).hexdigest(), file_id),
                    )
                except Exception:
                    pass
        if not storage_path.exists():
            self.write_error(410, "File data is not available on this server. Please re-upload the file.")
            return
        mime = normalized_mime(row["mime"])
        size = storage_path.stat().st_size
        write_audit(conn, user["id"], "file.download.completed", "file", file_id, {"storage": "local", "sizeBytes": size}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", content_disposition(row["original_name"]))
        self.end_headers()
        with storage_path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def preview_file(self, conn: PostgresConnection, user: dict, file_id: str) -> None:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            self.write_error(404, "File not found.")
            return
        if not can_access_channel(conn, user, row["channel_id"]):
            self.write_error(403, "No access to this file.")
            return
        if row["status"] != "available":
            self.write_error(409, "File is not available.")
            return
        mime = normalized_mime(row["mime"])
        filename = row["original_name"]
        ext = Path(filename).suffix.lower()

        if mime == "application/octet-stream" or not mime:
            guessed, _ = mimetypes.guess_type(filename)
            if guessed:
                mime = guessed
            elif ext in (".md", ".txt", ".log", ".sql", ".py", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml", ".csv"):
                mime = "text/plain"

        is_previewable = (
            mime.startswith(("image/", "audio/", "video/", "text/"))
            or mime in ("application/pdf", "application/json", "application/javascript", "application/xml", "application/x-yaml")
            or ext in (".pdf", ".txt", ".md", ".json", ".csv", ".js", ".ts", ".py", ".html", ".css", ".xml", ".yaml", ".yml", ".sql", ".log")
        )
        if not is_previewable:
            self.write_error(415, "Preview is not available for this file type.")
            return
        storage_path = upload_storage_path(row["channel_id"], row["id"], row["original_name"])
        if not storage_path.exists():
            self.write_error(410, "File data is not available on this server. Please re-upload the file.")
            return
        size = storage_path.stat().st_size
        content_type = mime
        if mime.startswith("text/") or ext in (".txt", ".md", ".json", ".csv", ".js", ".ts", ".py", ".html", ".css", ".xml", ".yaml", ".yml", ".sql", ".log"):
            if "charset" not in content_type:
                content_type += "; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{urllib.parse.quote(row['original_name'])}")
        self.end_headers()
        with storage_path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def delete_file(self, conn: PostgresConnection, user: dict, file_id: str) -> None:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            self.write_error(404, "File not found.")
            return

        is_admin = user["role"] in ADMIN_ROLES
        is_uploader = row["uploader_id"] == user["id"]
        is_chan_owner = is_channel_owner(conn, user, row["channel_id"])
        if not (is_admin or is_uploader or is_chan_owner):
            self.write_error(403, "Only the uploader, channel owner, or an Admin can delete this file.")
            return

        file_name = row["original_name"]
        channel_id = row["channel_id"]

        # Check attached messages
        with conn.transaction():
            attached_msgs = conn.execute(
                "SELECT message_id FROM message_attachments WHERE file_id = ?",
                (file_id,),
            ).fetchall()

            conn.execute("DELETE FROM message_attachments WHERE file_id = ?", (file_id,))

            for msg_row in attached_msgs:
                msg_id = msg_row["message_id"]
                remaining = conn.execute(
                    "SELECT COUNT(*) AS c FROM message_attachments WHERE message_id = ?",
                    (msg_id,),
                ).fetchone()["c"]
                msg = conn.execute("SELECT body FROM messages WHERE id = ?", (msg_id,)).fetchone()
                if remaining == 0 and msg and msg["body"].startswith("Shared file"):
                    conn.execute("UPDATE messages SET deleted_at = ? WHERE id = ?", (utc_now(), msg_id))

            conn.execute("DELETE FROM files WHERE id = ?", (file_id,))

            write_audit(
                conn,
                user["id"],
                "file.deleted",
                "file",
                file_id,
                {"originalName": file_name, "channelId": channel_id},
                self.client_ip(),
                self.headers.get("User-Agent", ""),
            )
        # Bytes go only after the rows are committed, so a failed delete never leaves a record
        # pointing at missing data.
        remove_upload_dir(channel_id, file_id)
        LIVE_BROKER.broadcast("file:deleted", {"channelId": channel_id, "fileId": file_id})
        self.write_json(200, {"ok": True, "deletedFileId": file_id, "name": file_name})

    def create_event(self, conn: PostgresConnection, user: dict) -> None:
        body = self.read_json()
        event_id = uid("event")
        title = str(body.get("title") or "").strip()[:200] or "Untitled event"
        starts = str(body.get("startsAt") or "").strip()
        starts_dt = parse_utc_datetime(starts)
        if not starts_dt:
            raise ValueError("Event start time (startsAt) must be an ISO-8601 date/time.")
        ends = str(body.get("endsAt") or "").strip()
        if ends:
            ends_dt = parse_utc_datetime(ends)
            if not ends_dt or ends_dt < starts_dt:
                raise ValueError("Event end time must be a valid date/time after the start.")
        else:
            ends = (datetime.fromisoformat(starts.replace("Z", "+00:00")) + timedelta(hours=1)).isoformat(timespec="minutes")
        client_name = str(body.get("clientName", "")).strip()[:200]
        event_type = str(body.get("eventType", "meeting")).strip()[:40] or "meeting"
        try:
            advance_notice_days = int(body.get("advanceNoticeDays") if body.get("advanceNoticeDays") is not None else 4)
        except (TypeError, ValueError):
            raise ValueError("advanceNoticeDays must be a whole number.")
        advance_notice_days = max(0, min(advance_notice_days, 60))
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO events (id, title, description, starts_at, ends_at, location, livekit_room, created_by, target_group_id, client_name, event_type, advance_notice_days, last_alerted_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
                """,
                (event_id, title, str(body.get("description") or "")[:4000], starts, ends, str(body.get("location") or "")[:200], f"event-{event_id}", user["id"], body.get("targetGroupId"), client_name, event_type, advance_notice_days, utc_now()),
            )
            conn.execute("INSERT INTO event_attendees (event_id, user_id, response) VALUES (?, ?, 'accepted')", (event_id, user["id"]))
            write_audit(conn, user["id"], "calendar.event.created", "event", event_id, {"title": title, "clientName": client_name}, self.client_ip(), self.headers.get("User-Agent", ""))
        check_advance_calendar_reminders(conn)
        self.write_json(201, {"ok": True, "eventId": event_id})

    def delete_event(self, conn: PostgresConnection, user: dict, event_id: str) -> None:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            self.write_error(404, "Event not found.")
            return
        if row["created_by"] != user["id"] and user["role"] not in ADMIN_ROLES:
            self.write_error(403, "Only event creator or admin can delete.")
            return
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        write_audit(conn, user["id"], "calendar.event.deleted", "event", event_id, {}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def ai_report_job_status(self, user: dict) -> None:
        if not self.require_report_admin(user):
            return
        self.write_json(200, {"ok": True, **WEEKLY_REPORT_JOB})

    def delete_ai_report_endpoint(self, conn: PostgresConnection, user: dict, report_id: str) -> None:
        if not self.require_report_admin(user):
            return
        row = conn.execute("SELECT id, user_id, title FROM weekly_activity_reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            self.write_error(404, "Report not found.")
            return
        conn.execute("DELETE FROM weekly_activity_reports WHERE id = ?", (report_id,))
        write_audit(conn, user["id"], "ai_report.deleted", "report", report_id, {"title": row["title"], "target_user_id": row["user_id"]}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True, "message": "Report deleted successfully."})

    def clear_ai_reports_endpoint(self, conn: PostgresConnection, user: dict) -> None:
        if not self.require_report_admin(user):
            return
        count = conn.execute("SELECT COUNT(*) as c FROM weekly_activity_reports").fetchone()["c"]
        conn.execute("DELETE FROM weekly_activity_reports")
        write_audit(conn, user["id"], "ai_reports.cleared", "report", "all", {"cleared_count": count}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True, "message": f"Successfully cleared {count} reports from archive."})

    def generate_ai_reports_endpoint(self, conn: PostgresConnection, user: dict) -> None:
        if not self.require_report_admin(user):
            return
        body = self.read_json()
        week_end_dt = datetime.now(timezone.utc)
        week_start_dt = week_end_dt - timedelta(days=7)

        if body.get("all"):
            # One Bedrock call per user, so run in the background and let the client poll the status.
            if WEEKLY_REPORT_JOB["running"]:
                self.write_error(409, "A team report is already being generated.")
                return
            threading.Thread(
                target=run_combined_report_job,
                args=(week_start_dt, week_end_dt, "manual", user["id"]),
                daemon=True,
                name="ManualTeamReport",
            ).start()
            write_audit(conn, user["id"], "ai_report.generate_all", "report", "combined", {}, self.client_ip(), self.headers.get("User-Agent", ""))
            self.write_json(202, {"ok": True, "status": "running"})
            return

        target_user_id = body.get("userId")
        if not target_user_id:
            self.write_error(400, "Choose an employee, or request the combined report for all users.")
            return
        try:
            report = generate_weekly_user_report(conn, target_user_id, week_start_dt, week_end_dt, generated_by=user["id"])
        except ValueError as exc:
            self.write_error(404, str(exc))
            return
        write_audit(conn, user["id"], "ai_report.generate_user", "report", report["id"], {"target_user_id": target_user_id}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True, "report": report})

    def generate_blockchain_briefing_endpoint(self, user: dict) -> None:
        if not self.require_admin(user):
            return

        global BLOCKCHAIN_BRIEFING_STATE
        if BLOCKCHAIN_BRIEFING_STATE.get("running"):
            self.write_json(409, {
                "ok": False,
                "error": "A blockchain briefing generation is already in progress.",
                "status": "running",
            })
            return

        def worker():
            execute_blockchain_briefing_sync()

        t = threading.Thread(target=worker, daemon=True, name="ManualBlockchainBriefing")
        t.start()

        self.write_json(200, {
            "ok": True,
            "message": "Blockchain intelligence briefing generation initiated via Amazon Bedrock Qwen.",
            "status": "running",
        })

    def get_blockchain_briefing_status(self, user: dict) -> None:
        global BLOCKCHAIN_BRIEFING_STATE
        html_path = WEB_ROOT / "blockchain-briefing-live.html"
        last_modified = None
        if html_path.exists():
            last_modified = datetime.fromtimestamp(html_path.stat().st_mtime, tz=timezone.utc).isoformat()

        self.write_json(200, {
            "running": bool(BLOCKCHAIN_BRIEFING_STATE.get("running")),
            "lastRun": BLOCKCHAIN_BRIEFING_STATE.get("last_run") or last_modified,
            "lastError": BLOCKCHAIN_BRIEFING_STATE.get("last_error"),
            "url": "/blockchain-briefing-live.html",
            "isAdmin": user.get("role") in ADMIN_ROLES,
        })

    def event_response(self, conn: PostgresConnection, user: dict, event_id: str) -> None:
        response = str(self.read_json().get("response") or "accepted").strip().lower()
        if response not in ("accepted", "declined", "tentative", "pending"):
            raise ValueError("Event response must be accepted, declined, tentative, or pending.")
        if not conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone():
            self.write_error(404, "Event not found.")
            return
        conn.execute("INSERT OR REPLACE INTO event_attendees (event_id, user_id, response) VALUES (?, ?, ?)", (event_id, user["id"], response))
        write_audit(conn, user["id"], "calendar.event.response", "event", event_id, {"response": response}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def create_announcement(self, conn: PostgresConnection, user: dict) -> None:
        if not self.require_admin(user):
            return
        body = self.read_json()
        announcement_id = uid("ann")
        pinned_until = body.get("pinnedUntil") or (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
        with conn.transaction():
            conn.execute(
                "INSERT INTO announcements (id, title, body, author_id, target_group_id, pinned_until, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (announcement_id, body.get("title", ""), body.get("body", ""), user["id"], body.get("targetGroupId"), pinned_until, utc_now()),
            )
            channel = conn.execute("SELECT id FROM channels WHERE slug = 'announcements'").fetchone()
            if channel:
                message_id = uid("msg")
                conn.execute("INSERT INTO messages (id, channel_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)", (message_id, channel["id"], user["id"], f"**{body.get('title', '')}**\n\n{body.get('body', '')}", utc_now()))
            write_audit(conn, user["id"], "announcement.created", "announcement", announcement_id, {"title": body.get("title", "")}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(201, {"ok": True, "announcementId": announcement_id})

    def create_user_admin_endpoint(self, conn: PostgresConnection, user: dict) -> None:
        if not self.require_admin(user):
            return

        body = self.read_json()
        email = str(body.get("email", "")).strip().lower()
        display_name = str(body.get("displayName", "")).strip()
        password = str(body.get("password", "")).strip()
        role = str(body.get("role", "member")).strip().lower()
        title = str(body.get("title", "")).strip()
        department = str(body.get("department", "")).strip()
        custom_username = str(body.get("username", "")).strip() or None

        if not email or "@" not in email:
            self.write_error(400, "A valid company email address is required.")
            return
        if not display_name:
            self.write_error(400, "Full name (display name) is required.")
            return
        validate_new_password(password)

        if role not in ALL_ROLES:
            role = "member"

        if not can_grant_role(user["role"], role):
            self.write_error(403, f"Your role cannot create accounts with the '{role}' role.")
            return

        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            self.write_error(409, f"A user with email '{email}' already exists.")
            return

        try:
            new_user = create_user(
                conn,
                email=email,
                display_name=display_name,
                password=password,
                provider="local",
                provider_subject="",
                role=role,
                title=title,
                department=department,
                username=custom_username,
            )
            write_audit(conn, user["id"], "user.created_by_admin", "user", new_user["id"], {
                "created_by": user["id"],
                "role": role,
                "email": email,
            }, self.client_ip(), self.headers.get("User-Agent", ""))
            
            self.write_json(201, {
                "ok": True,
                "message": f"User '{display_name}' created successfully with role '{role}'.",
                "user": row_to_user(new_user),
            })
        except Exception as exc:
            self.write_error(400, str(exc))

    def update_user(self, conn: PostgresConnection, user: dict, user_id: str) -> None:
        body = self.read_json()
        target_id = user_id
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()
        if not target:
            self.write_error(404, "User not found.")
            return
        is_self = target_id == user["id"]
        # Every check happens before any write, and each failure returns immediately, so a
        # request is either applied in full or rejected with exactly one response.
        if not is_self:
            if user["role"] not in INTRANET_ADMIN_ROLES:
                self.write_error(403, "Admin or moderator role required.")
                return
            if not can_manage_user(user, target["role"]):
                self.write_error(403, "You cannot modify an account whose role is equal to or higher than yours.")
                return

        new_role = None
        if "role" in body:
            new_role = str(body["role"] or "").strip().lower()
            if new_role not in ALL_ROLES:
                raise ValueError("Invalid role.")
            if is_self and new_role != target["role"]:
                self.write_error(403, "Ask another administrator to change your own role.")
                return
            if not can_grant_role(user["role"], new_role):
                self.write_error(403, f"Your role cannot grant the '{new_role}' role.")
                return

        new_active = None
        if "isActive" in body:
            new_active = bool(body["isActive"])
            if is_self and not new_active:
                raise ValueError("You cannot suspend your own account.")

        new_password = str(body.get("newPassword") or "").strip()
        if new_password:
            validate_new_password(new_password)
            if is_self and target["password_hash"]:
                current = str(body.get("currentPassword") or "")
                if not current:
                    raise ValueError("Enter your current password to set a new one.")
                if not verify_password(current, target["password_hash"], target["password_salt"]):
                    write_audit(conn, user["id"], "user.password.change_failed", "user", target_id, {}, self.client_ip(), self.headers.get("User-Agent", ""))
                    self.write_error(403, "Current password is incorrect.")
                    return

        # Presence is not settable here — it's derived automatically from activity (see
        # is_user_online), never chosen by the user.
        profile_limits = {"statusText": ("status_text", 140), "title": ("title", 120), "department": ("department", 120), "displayName": ("display_name", 80)}
        fields = []
        params = []
        for api_key, (db_key, limit) in profile_limits.items():
            if api_key in body:
                value = str(body[api_key] or "").strip()[:limit]
                if api_key == "displayName" and not value:
                    raise ValueError("Display name cannot be empty.")
                fields.append(f"{db_key} = ?")
                params.append(value)

        ip, agent = self.client_ip(), self.headers.get("User-Agent", "")
        with conn.transaction():
            if new_role is not None and new_role != target["role"]:
                conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, target_id))
                write_audit(conn, user["id"], "user.role.changed", "user", target_id, {"role": new_role, "previousRole": target["role"]}, ip, agent)
            if new_active is not None:
                # Deliberately not removing channel_members here: reactivating someone later (the
                # common case — a temporary suspension) should restore them to exactly the private
                # groups they were already in. Login itself requires is_active = 1, and sessions
                # of a suspended account are revoked below.
                conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if new_active else 0, target_id))
                if not new_active:
                    conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
                write_audit(conn, user["id"], "user.active.changed", "user", target_id, {"isActive": new_active}, ip, agent)
            if new_password:
                p_hash, p_salt = hash_password(new_password)
                conn.execute("UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?", (p_hash, p_salt, target_id))
                # A password change ends every other session for that account (for a self-service
                # change, the caller's own session survives).
                conn.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?", (target_id, self.session_token() if is_self else ""))
                write_audit(conn, user["id"], "user.password.changed" if is_self else "user.password.reset", "user", target_id, {}, ip, agent)
            if fields:
                conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", [*params, target_id])
                write_audit(conn, user["id"], "user.profile.updated", "user", target_id, {key: body[key] for key in profile_limits if key in body}, ip, agent)
        LIVE_BROKER.broadcast("user:updated", {"userId": target_id})
        self.write_json(200, {"ok": True})

    def delete_user_endpoint(self, conn: PostgresConnection, user: dict, target_id: str) -> None:
        if user["role"] != "super_admin":
            self.write_error(403, "Only a Super Admin can delete or remove a user from the workspace.")
            return
        if target_id == user["id"]:
            self.write_error(400, "You cannot delete your own active administrator account.")
            return
        target_user = conn.execute("SELECT id, display_name, email, role FROM users WHERE id = ?", (target_id,)).fetchone()
        if not target_user:
            self.write_error(404, "User not found.")
            return

        uploaded = conn.execute("SELECT id, channel_id FROM files WHERE uploader_id = ?", (target_id,)).fetchall()
        # All-or-nothing: a crash halfway would otherwise leave a user with half their data gone.
        with conn.transaction():
            # Safely nullify or reassign foreign keys on tables with NO ACTION
            conn.execute("UPDATE asset_assignments SET assigned_by = NULL WHERE assigned_by = ?", (target_id,))
            conn.execute("UPDATE attendance_days SET corrected_by = NULL WHERE corrected_by = ?", (target_id,))
            conn.execute("UPDATE leave_requests SET approver_id = NULL WHERE approver_id = ?", (target_id,))
            conn.execute("UPDATE hr_documents SET uploaded_by = NULL WHERE uploaded_by = ?", (target_id,))
            conn.execute("UPDATE workflow_tasks SET completed_by = NULL WHERE completed_by = ?", (target_id,))
            conn.execute("UPDATE announcements SET author_id = ? WHERE author_id = ?", (user["id"], target_id))
            conn.execute("UPDATE events SET created_by = ? WHERE created_by = ?", (user["id"], target_id))

            # Clear author messages, reactions and files
            conn.execute("DELETE FROM reactions WHERE message_id IN (SELECT id FROM messages WHERE author_id = ?)", (target_id,))
            conn.execute("DELETE FROM message_attachments WHERE message_id IN (SELECT id FROM messages WHERE author_id = ?)", (target_id,))
            conn.execute("DELETE FROM messages WHERE parent_id IN (SELECT id FROM messages WHERE author_id = ?)", (target_id,))
            conn.execute("DELETE FROM messages WHERE author_id = ?", (target_id,))
            conn.execute("DELETE FROM files WHERE uploader_id = ?", (target_id,))

            # Explicitly clean memberships and sessions
            conn.execute("DELETE FROM channel_members WHERE user_id = ?", (target_id,))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))

            # Delete user record (Postgres will cascade all other ON DELETE CASCADE foreign keys)
            conn.execute("DELETE FROM users WHERE id = ?", (target_id,))
            write_audit(conn, user["id"], "user.deleted", "user", target_id, {
                "email": target_user["email"],
                "displayName": target_user["display_name"],
                "role": target_user["role"],
            }, self.client_ip(), self.headers.get("User-Agent", ""))
        for item in uploaded:
            remove_upload_dir(item["channel_id"], item["id"])
        self.write_json(200, {"ok": True, "message": f"User '{target_user['display_name']}' was permanently removed."})

    def mark_channel_read(self, conn: PostgresConnection, user: dict, channel_id: str) -> None:
        if not can_access_channel(conn, user, channel_id):
            self.write_error(403, "No access to this channel.")
            return
        body = self.read_json()
        message_id = str(body.get("messageId", "")).strip()
        if message_id:
            row = conn.execute("SELECT id FROM messages WHERE id = ? AND channel_id = ?", (message_id, channel_id)).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM messages WHERE channel_id = ? AND parent_id IS NULL ORDER BY created_at DESC LIMIT 1",
                (channel_id,),
            ).fetchone()
        target_message_id = row["id"] if row else ""
        now = utc_now()
        conn.execute(
            "INSERT INTO channel_read_states (channel_id, user_id, last_read_message_id, updated_at) VALUES (?, ?, ?, ?)",
            (channel_id, user["id"], target_message_id, now),
        )
        if row:
            conn.execute(
                "UPDATE channel_members SET last_read_message_id = ? WHERE channel_id = ? AND user_id = ?",
                (row["id"], channel_id, user["id"]),
            )
        conn.execute(
            """
            UPDATE notifications
            SET read_at = ?
            WHERE user_id = ? AND read_at IS NULL AND type != 'urgent' AND payload LIKE ?
            """,
            (now, user["id"], f'%"{channel_id}"%'),
        )
        LIVE_BROKER.broadcast("channel:read", {"channelId": channel_id, "userId": user["id"]})
        self.write_json(200, {"ok": True, "lastReadMessageId": target_message_id})

    def update_typing(self, conn: PostgresConnection, user: dict, channel_id: str) -> None:
        if not can_access_channel(conn, user, channel_id):
            self.write_error(403, "No access to this channel.")
            return
        body = self.read_json()
        typing = bool(body.get("typing", True))
        typing_until = (datetime.now(timezone.utc) + timedelta(seconds=6)).isoformat(timespec="seconds") if typing else ""
        conn.execute(
            "UPDATE channel_members SET typing_until = ? WHERE channel_id = ? AND user_id = ?",
            (typing_until, channel_id, user["id"]),
        )
        LIVE_BROKER.broadcast("channel:typing", {"channelId": channel_id, "userId": user["id"], "displayName": user.get("display_name", ""), "typing": typing})
        self.write_json(200, {"ok": True, "typingUntil": typing_until})

    def update_retention(self, conn: PostgresConnection, user: dict) -> None:
        if not self.require_admin(user):
            return
        body = self.read_json()
        if "globalDays" in body:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('retention_global_days', ?)", (str(int(body["globalDays"])),))
        if body.get("channelId"):
            conn.execute("UPDATE channels SET retention_days = ?, legal_hold = ? WHERE id = ?", (body.get("channelDays"), 1 if body.get("legalHold") else 0, body["channelId"]))
        write_audit(conn, user["id"], "retention.updated", "retention", body.get("channelId", "global"), body, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def update_feature_flag(self, conn: PostgresConnection, user: dict) -> None:
        if not self.require_admin(user):
            return
        body = self.read_json()
        key = str(body.get("key") or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key):
            raise ValueError("Feature flag key is required (letters, digits, underscore).")
        conn.execute("INSERT OR REPLACE INTO feature_flags (key, value) VALUES (?, ?)", (key, 1 if body.get("value") else 0))
        write_audit(conn, user["id"], "feature_flag.updated", "feature_flag", key, {"enabled": bool(body.get("value"))}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def toggle_channel_lock(self, conn: PostgresConnection, user: dict, channel_id: str) -> None:
        if not self.require_admin(user):
            return
        row = conn.execute("SELECT locked FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if not row:
            self.write_error(404, "Channel not found.")
            return
        locked = 0 if row["locked"] else 1
        conn.execute("UPDATE channels SET locked = ? WHERE id = ?", (locked, channel_id))
        write_audit(conn, user["id"], "channel.lock.toggled", "channel", channel_id, {"locked": bool(locked)}, self.client_ip(), self.headers.get("User-Agent", ""))
        self.write_json(200, {"ok": True})

    def handle_google_callback(self, conn: PostgresConnection, query: dict) -> None:
        def fail(reason: str) -> None:
            write_audit(conn, None, "auth.login.fail", "user", "google", {"provider": "google", "reason": reason}, self.client_ip(), self.headers.get("User-Agent", ""))
            self.send_response(302)
            self.send_header("Location", "/?auth=google_error")
            self.end_headers()

        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        # The state is single-use and expires, so a leaked callback URL cannot be replayed.
        state_row = conn.execute("DELETE FROM settings WHERE key = ? RETURNING value", (f"oauth_state:{state}",)).fetchone() if state else None
        conn.execute("DELETE FROM settings WHERE key LIKE 'oauth_state:%' AND value < ?", ((datetime.now(timezone.utc) - timedelta(minutes=OAUTH_STATE_TTL_MINUTES)).isoformat(timespec="seconds"),))
        issued = parse_utc_datetime(state_row["value"]) if state_row else None
        if not code or not issued or datetime.now(timezone.utc) - issued > timedelta(minutes=OAUTH_STATE_TTL_MINUTES):
            fail("invalid_state")
            return
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
        redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", f"http://127.0.0.1:{PORT}/api/auth/google/callback")
        token_body = urllib.parse.urlencode(
            {
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        ).encode("utf-8")
        try:
            token_req = urllib.request.Request("https://oauth2.googleapis.com/token", data=token_body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(token_req, timeout=10) as response:
                token_payload = json.loads(response.read().decode("utf-8"))
            user_req = urllib.request.Request("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {token_payload['access_token']}"})
            with urllib.request.urlopen(user_req, timeout=10) as response:
                profile = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as error:
            print(f"[Google OAuth] Token/userinfo exchange failed: {error}", file=sys.stderr, flush=True)
            fail("exchange_failed")
            return
        email = str(profile.get("email") or "").strip().lower()
        # Accounts are matched by email, so only a Google-verified address may sign in; otherwise
        # anyone could claim an existing colleague's (or the admin's) email.
        if not email or profile.get("email_verified") is not True:
            fail("email_not_verified")
            return
        if GOOGLE_ALLOWED_DOMAINS and email.rsplit("@", 1)[-1] not in GOOGLE_ALLOWED_DOMAINS:
            fail("domain_not_allowed")
            return
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and not user["is_active"]:
            fail("user_disabled")
            return
        if not user:
            user = create_user(conn, email, profile.get("name", email), None, "google", profile.get("sub", ""))
        token = create_session(conn, user["id"])
        write_audit(conn, user["id"], "auth.login.success", "user", user["id"], {"provider": "google"}, self.client_ip(), self.headers.get("User-Agent", ""))
        # The session cookie is httpOnly, so the token never needs to travel in the URL
        # (URLs end up in browser history, server logs, and Referer headers).
        self.send_response(302)
        self.send_header("Set-Cookie", self.session_headers(token)["Set-Cookie"])
        self.send_header("Location", "/")
        self.end_headers()

def main() -> None:
    init_db()
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    server = ThreadingHTTPServer((HOST, PORT), PortalHandler)
    if SSL_CERT_FILE and SSL_KEY_FILE:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(SSL_CERT_FILE, SSL_KEY_FILE)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"Beenco Connect listening on {HOST}:{PORT}", flush=True)
    if URL_SCHEME == "https":
        print("HTTPS enabled for browser camera/microphone secure-origin access.", flush=True)
    else:
        print("HTTPS not enabled. LAN browsers may block camera/microphone on http:// addresses.", flush=True)
    for url in local_access_urls():
        print(f"Portal access: {url}", flush=True)
    print("PostgreSQL database: configured", flush=True)
    start_daily_briefing_scheduler()
    print("Daily Blockchain Briefing Scheduler: active (running @ 09:00 AM daily)", flush=True)
    start_weekly_report_scheduler()
    print(f"Weekly Team Report Scheduler: active (Mondays @ {WEEKLY_REPORT_HOUR:02d}:00 {WEEKLY_REPORT_TZ})", flush=True)
    start_twitter_mentions_scheduler()
    if TWITTERAPI_IO_KEY:
        cycle_minutes = math.ceil((len(TWITTER_MENTION_ACCOUNTS) * TWITTER_MENTIONS_ACCOUNT_INTERVAL_SECONDS) / 60)
        print(
            f"Twitter Mentions Monitor: active (1 account every {TWITTER_MENTIONS_ACCOUNT_INTERVAL_SECONDS}s; full cycle about {cycle_minutes} minutes)",
            flush=True,
        )
    server.serve_forever()


if __name__ == "__main__":
    main()
