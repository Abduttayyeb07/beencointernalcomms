"""
Main Orchestration Pipeline for Daily Blockchain Intelligence Briefing.

Executes:
1. Multi-source parallel intelligence fetch (Exa + Tavily + DefiLlama)
2. Synthesis via Amazon Bedrock Qwen (19 sections, FOMO filter, Trend Confidence stages)
3. Artifact injection into blockchain-briefing-live.html
4. Archive backup to data/history/
5. Outputs strict notification message to stdout.
"""

import argparse
import datetime
import json
import logging
import os
import sys

from artifact_updater import update_briefing_artifact
from bedrock_client import BedrockQwenClient
import config
from data_fetcher import fetch_all_intelligence

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)]  # Keep stdout clean for the required final message
)
logger = logging.getLogger("run_briefing")


def save_historical_archive(briefing_data: dict) -> str:
    """Save a timestamped copy of the synthesized briefing for historical audit."""
    archive_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "history")
    os.makedirs(archive_dir, exist_ok=True)
    
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    date_str = briefing_data.get("meta", {}).get("date", now_utc.strftime("%Y-%m-%d"))
    timestamp = now_utc.strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(archive_dir, f"briefing_{date_str}_{timestamp}.json")
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(briefing_data, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Archived historical snapshot to: {filepath}")
    return filepath


def run_pipeline(dry_run: bool = False) -> dict:
    """Run the complete end-to-end intelligence pipeline."""
    logger.info("=================================================================")
    logger.info("🚀 ABDUTTAYYEB BLOCKCHAIN INTELLIGENCE PIPELINE INITIATING")
    logger.info("=================================================================")

    # Step 1: Ingest Multi-Source Intelligence
    logger.info("[1/4] Ingesting intelligence concurrently (Exa + Tavily + DefiLlama)...")
    research_payload = fetch_all_intelligence()
    logger.info(
        f"Ingestion complete: {len(research_payload.get('tavily', []))} Tavily items, "
        f"{len(research_payload.get('exa', []))} Exa items, "
        f"and DefiLlama on-chain metrics compiled."
    )

    # Step 2: Bedrock Qwen Synthesis
    logger.info("[2/4] Synthesizing 19-section intelligence briefing via Amazon Bedrock Qwen...")
    client = BedrockQwenClient()
    briefing_data = client.synthesize_briefing(research_payload)
    
    section_count = len(briefing_data.get("sections", {}))
    total_items = sum(len(sec.get("items", [])) for sec in briefing_data.get("sections", {}).values())
    logger.info(f"Synthesis successful: {section_count} sections and {total_items} items populated.")

    if dry_run:
        logger.info("[DRY RUN] Skipping file updates and notifications.")
        return briefing_data

    # Step 3: Inject into Interactive Artifact
    logger.info("[3/4] Injecting synthesized intelligence into blockchain-briefing-live.html...")
    updated_html_path = update_briefing_artifact(briefing_data)
    logger.info(f"Artifact updated: {updated_html_path}")

    # Step 4: Archive snapshot
    logger.info("[4/4] Archiving historical briefing snapshot...")
    save_historical_archive(briefing_data)

    logger.info("Pipeline execution completed successfully.")
    
    # Strictly required completion message to stdout
    notification = "⛓️ Your daily blockchain intelligence briefing is ready. Click to open Claude, then tap blockchain-briefing-live in the sidebar to explore today's trends, on-chain signals and emerging opportunities."
    try:
        print(notification)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(notification.encode("utf-8") + b"\n")
        sys.stdout.flush()
    
    return briefing_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automated Daily Blockchain Intelligence Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Perform fetch and synthesis without writing to artifact")
    args = parser.parse_args()

    try:
        run_pipeline(dry_run=args.dry_run)
    except Exception as exc:
        logger.critical(f"Pipeline failure: {exc}", exc_info=True)
        sys.exit(1)
