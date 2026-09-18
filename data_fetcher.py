"""
Hybrid Retrieval Engine for Blockchain Intelligence Briefing.
Concurrently queries Exa.ai, Tavily, and DefiLlama to produce verified ground truth
and emerging alpha discoveries.
"""

import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional
import requests
from exa_py import Exa
from tavily import TavilyClient

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("data_fetcher")


def fetch_single_exa_query(exa_client: Exa, query: str) -> List[Dict[str, Any]]:
    """Execute a single Exa neural search with full page content & highlights."""
    results = []
    try:
        res = exa_client.search(
            query,
            num_results=4,
            contents={"text": {"max_characters": 800}, "highlights": True},
        )
        for r in getattr(res, "results", []):
            highlights_list = getattr(r, "highlights", []) or []
            highlights_text = " ".join(highlights_list)
            body_text = getattr(r, "text", "") or ""
            snippet = highlights_text if highlights_text else body_text[:600]
            results.append({
                "source": "Exa (Alpha/Research)",
                "query": query,
                "title": getattr(r, "title", "Untitled Technical Writeup"),
                "url": getattr(r, "url", ""),
                "published_date": getattr(r, "published_date", None),
                "snippet": snippet.strip(),
            })
    except Exception as e:
        logger.warning(f"Exa search failed for query '{query[:40]}...': {e}")
    return results


def fetch_single_tavily_query(tavily_client: TavilyClient, query: str) -> List[Dict[str, Any]]:
    """Execute a single Tavily news & fact verification query."""
    results = []
    try:
        res = tavily_client.search(
            query=query,
            search_depth="advanced",
            time_range="d",
            max_results=4,
        )
        for item in res.get("results", []):
            results.append({
                "source": "Tavily (Verified News)",
                "query": query,
                "title": item.get("title", "Untitled News"),
                "url": item.get("url", ""),
                "snippet": item.get("content", "").strip()[:800],
            })
    except Exception as e:
        logger.warning(f"Tavily search failed for query '{query[:40]}...': {e}")
    return results


def _fetch_chain_history(chain_name: str) -> Optional[Dict[str, Any]]:
    """Helper to fetch 1d and 7d historical TVL change for a specific chain."""
    try:
        url = f"https://api.llama.fi/v2/historicalChainTvl/{chain_name}"
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            data = r.json()
            if len(data) >= 8:
                current_tvl = data[-1]["tvl"]
                prev_1d = data[-2]["tvl"]
                prev_7d = data[-8]["tvl"]
                ch_1d = ((current_tvl - prev_1d) / prev_1d * 100) if prev_1d else 0
                ch_7d = ((current_tvl - prev_7d) / prev_7d * 100) if prev_7d else 0
                return {
                    "name": chain_name,
                    "tvl": round(current_tvl),
                    "change_1d": round(ch_1d, 2),
                    "change_7d": round(ch_7d, 2),
                }
    except Exception:
        pass
    return None


def fetch_defillama_ground_truth() -> Dict[str, Any]:
    """
    Fetch deterministic ground truth from DefiLlama:
    1. Top chains by TVL and 1d/7d % growth
    2. Stablecoin inflows/outflows across top ecosystems
    """
    ground_truth = {
        "top_tvl_chains": [],
        "stablecoin_flows": [],
        "summary_text": "",
    }

    # 1. Chains TVL & Historical Growth
    try:
        resp = requests.get(config.DEFILLAMA_CHAINS_URL, timeout=config.API_TIMEOUT_SECONDS)
        if resp.status_code == 200:
            chains_data = resp.json()
            # Sort top 10 chains by TVL
            sorted_chains = sorted(chains_data, key=lambda x: x.get("tvl", 0), reverse=True)[:8]
            
            # Concurrently fetch history for top chains
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(_fetch_chain_history, c.get("name")): c for c in sorted_chains}
                for f in as_completed(futures):
                    res = f.result()
                    if res:
                        ground_truth["top_tvl_chains"].append(res)
            
            # Sort by TVL
            ground_truth["top_tvl_chains"].sort(key=lambda x: x["tvl"], reverse=True)
    except Exception as e:
        logger.warning(f"DefiLlama chains request failed: {e}")

    # 2. Stablecoin circulation across ecosystems
    try:
        resp_stables = requests.get(config.DEFILLAMA_STABLECOINS_URL, timeout=config.API_TIMEOUT_SECONDS)
        if resp_stables.status_code == 200:
            stables_data = resp_stables.json()
            top_stables = sorted(
                stables_data,
                key=lambda x: (x.get("totalCirculatingUSD") or {}).get("peggedUSD", 0),
                reverse=True
            )[:8]
            for s in top_stables:
                circ = s.get("totalCirculatingUSD", {}).get("peggedUSD", 0)
                ground_truth["stablecoin_flows"].append({
                    "name": s.get("name"),
                    "circulating_usd": round(circ),
                })
    except Exception as e:
        logger.warning(f"DefiLlama stablecoins request failed: {e}")

    # Build concise text summary
    summary_lines = ["### DefiLlama On-Chain Ground Truth:"]
    if ground_truth["top_tvl_chains"]:
        chain_items = [f"- **{c['name']}**: TVL ${c['tvl']:,} (1D: {'+' if c['change_1d']>=0 else ''}{c['change_1d']}%, 7D: {'+' if c['change_7d']>=0 else ''}{c['change_7d']}%)" for c in ground_truth["top_tvl_chains"]]
        summary_lines.extend(chain_items)
    if ground_truth["stablecoin_flows"]:
        stables_str = ", ".join([f"{s['name']}: ${s['circulating_usd']:,}" for s in ground_truth["stablecoin_flows"][:6]])
        summary_lines.append(f"- **Top Stablecoin Ecosystems:** {stables_str}")

    ground_truth["summary_text"] = "\n".join(summary_lines)
    return ground_truth


def fetch_all_intelligence() -> Dict[str, Any]:
    """
    Run concurrent retrieval across Exa, Tavily, and DefiLlama.
    Returns structured results and formatted synthesis prompt text.
    """
    logger.info("Initializing concurrent data retrieval (Exa, Tavily, DefiLlama)...")
    exa_client = Exa(api_key=config.EXA_API_KEY) if config.EXA_API_KEY else None
    tavily_client = TavilyClient(api_key=config.TAVILY_API_KEY) if config.TAVILY_API_KEY else None

    exa_results: List[Dict[str, Any]] = []
    tavily_results: List[Dict[str, Any]] = []
    defillama_data: Dict[str, Any] = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_type = {}

        future_to_type[executor.submit(fetch_defillama_ground_truth)] = ("defillama", "DefiLlama Ground Truth")

        if exa_client:
            for q in config.EXA_QUERIES:
                future_to_type[executor.submit(fetch_single_exa_query, exa_client, q)] = ("exa", q)

        if tavily_client:
            for q in config.TAVILY_QUERIES:
                future_to_type[executor.submit(fetch_single_tavily_query, tavily_client, q)] = ("tavily", q)

        for future in as_completed(future_to_type):
            q_type, label = future_to_type[future]
            try:
                result = future.result()
                if q_type == "defillama":
                    defillama_data = result
                    logger.info("Successfully fetched DefiLlama on-chain metrics.")
                elif q_type == "exa":
                    exa_results.extend(result)
                    logger.info(f"Exa query complete [{len(result)} items]: {label[:45]}...")
                elif q_type == "tavily":
                    tavily_results.extend(result)
                    logger.info(f"Tavily query complete [{len(result)} items]: {label[:45]}...")
            except Exception as e:
                logger.error(f"Error executing retrieval task ({q_type}, {label}): {e}")

    logger.info(f"Retrieval summary: {len(exa_results)} Exa items, {len(tavily_results)} Tavily items, DefiLlama ready.")

    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    compiled_parts = [
        f"RESEARCH DATA COLLECTED AT: {today_str}\n",
        "=== SECTION A: DETERMINISTIC GROUND TRUTH (DefiLlama) ===",
        defillama_data.get("summary_text", "No DefiLlama data available."),
        "\n=== SECTION B: FACTUAL VERIFICATION & INSTITUTIONAL NEWS (Tavily) ===",
    ]

    for idx, item in enumerate(tavily_results, 1):
        compiled_parts.append(
            f"[{idx}] {item['title']}\n"
            f"    URL: {item['url']}\n"
            f"    Category: {item['query']}\n"
            f"    Content: {item['snippet']}\n"
        )

    compiled_parts.append("=== SECTION C: ALPHA & NARRATIVE DISCOVERY (Exa Neural Search) ===")
    for idx, item in enumerate(exa_results, 1):
        compiled_parts.append(
            f"[{idx}] {item['title']}\n"
            f"    URL: {item['url']}\n"
            f"    Query Focus: {item['query']}\n"
            f"    Published: {item.get('published_date') or 'Recent'}\n"
            f"    Snippet: {item['snippet']}\n"
        )

    compiled_context = "\n".join(compiled_parts)

    return {
        "timestamp": today_str,
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
        "defillama": defillama_data,
        "tavily": tavily_results,
        "exa": exa_results,
        "compiled_context": compiled_context,
    }


if __name__ == "__main__":
    data = fetch_all_intelligence()
    print("\n--- SAMPLE COMPILED CONTEXT PREVIEW ---")
    print(data["compiled_context"][:1200])
