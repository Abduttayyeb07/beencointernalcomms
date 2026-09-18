"""
Artifact Updater: Injects synthesized JSON intelligence into blockchain-briefing-live.html
Uses literal boundary markers:
/* ===BRIEFING_DATA_START=== */
const BRIEFING_DATA = ...;
/* ===BRIEFING_DATA_END=== */
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("artifact_updater")

DATA_REGEX = re.compile(
    r"(/\*\s*===BRIEFING_DATA_START===\s*\*/)(.*?)(/\*\s*===BRIEFING_DATA_END===\s*\*/)",
    re.DOTALL
)

DEFAULT_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blockchain-briefing-live.html")


def _atomic_write(target_path: str, content: str) -> None:
    """Writes `content` to `target_path` via a temp file + atomic replace, retrying the replace
    a few times. The portal serves this file over HTTP while this may run (a background job),
    and on some container filesystems (seen on Docker Desktop for Windows) os.replace() can
    transiently raise "Resource busy" if a GET request has the file open at that exact instant —
    the retry is what actually fixes that, not the temp file (which was already atomic)."""
    temp_path = target_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(content)
    last_error: OSError | None = None
    for attempt in range(5):
        try:
            os.replace(temp_path, target_path)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.2 * (attempt + 1))
    if os.path.exists(temp_path):
        os.remove(temp_path)
    raise last_error


def update_briefing_artifact(briefing_data: Dict[str, Any], filepath: str = DEFAULT_HTML_PATH) -> str:
    """
    Injects updated JSON briefing data into the HTML artifact between boundary markers.
    Returns the absolute path to the updated HTML file.
    """
    if not os.path.exists(filepath):
        alt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apps", "web", "blockchain-briefing-live.html")
        if os.path.exists(alt):
            filepath = alt
        else:
            logger.error(f"Artifact target file not found: {filepath}")
            raise FileNotFoundError(f"Target artifact HTML not found at: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        html_content = f.read()

    match = DATA_REGEX.search(html_content)
    if not match:
        logger.error(f"Could not find boundary markers /* ===BRIEFING_DATA_START=== */ in {filepath}")
        raise ValueError("Briefing data marker boundaries not found in HTML artifact.")

    # Prepare formatted JS constant
    json_str = json.dumps(briefing_data, indent=2, ensure_ascii=False)
    replacement_payload = f"\n    const BRIEFING_DATA = {json_str};\n    "
    
    # Replace inside the markers
    updated_html = (
        html_content[:match.start(2)] +
        replacement_payload +
        html_content[match.end(2):]
    )

    # The portal serves apps/web/blockchain-briefing-live.html, not this repo-root copy — write
    # that one FIRST so the page users actually see updates even if the secondary copy below
    # (or a retry) fails. Previously this order was reversed: a failure writing the root copy
    # (an unrelated file nothing serves) raised before the served copy was ever touched, so a
    # generation could succeed end-to-end and still leave the page showing stale content.
    web_target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apps", "web", "blockchain-briefing-live.html")
    web_updated = False
    if os.path.isdir(os.path.dirname(web_target)):
        try:
            _atomic_write(web_target, updated_html)
            logger.info(f"Synchronized live briefing artifact to web portal: {web_target}")
            web_updated = True
        except OSError as e:
            logger.warning(f"Could not update web portal artifact: {e}")

    # Nothing else reads this root-level copy (the portal only ever serves the apps/web one
    # above), so it never races a concurrent reader the way that one does — no need for
    # temp-file-plus-replace here, and simpler is also more reliable: this is what was actually
    # raising "Resource busy" even with retries, for reasons that were never fully clear, on a
    # replace the file's only consumer (a human glancing at the repo) doesn't need to be atomic.
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated_html)
        logger.info(f"Successfully injected intelligence briefing into: {filepath}")
    except OSError as e:
        logger.warning(f"Could not update repo-root artifact copy (non-fatal, nothing serves it): {e}")
        if not web_updated:
            raise

    if not web_updated and os.path.abspath(filepath) != os.path.abspath(web_target):
        logger.warning("The live web portal artifact was NOT updated; only the repo-root copy was.")

    return os.path.abspath(filepath)


if __name__ == "__main__":
    sample_data = {
        "meta": {
            "date": "2026-09-16",
            "timestamp": "2026-09-16T10:00:00Z",
            "curator": "Abduttayyeb Crypto Intelligence Terminal",
            "executiveSummary": "Test briefing updater injection verified.",
            "keyMetrics": {
                "topGrowingChain": "Sonic (+18.4% 7d)",
                "stablecoinNetInflow": "+$1.42B",
                "marketSentiment": "Selective Rotation",
                "criticalAlertsCount": 1
            }
        },
        "sections": {}
    }
    path = update_briefing_artifact(sample_data)
    print("Updated artifact at:", path)
