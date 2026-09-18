"""
Configuration module for the Automated Daily Blockchain Intelligence Briefing Pipeline.
Loads environment variables, defines search queries, API endpoints, and section schemas.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# API Keys & Credentials
EXA_API_KEY = os.getenv("EXA_API_KEY") or os.getenv("EXA_AI_KEY") or ""
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY") or ""

# AWS Bedrock Configuration
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID") or ""
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY") or ""
AWS_REGION = os.getenv("AWS_BEDROCK_REGION") or os.getenv("AWS_REGION") or "eu-central-1"
BEDROCK_MODEL_ID = os.getenv("AWS_BEDROCK_MODEL") or os.getenv("BEDROCK_MODEL_ID") or "qwen.qwen3-32b-v1:0"

# Target Artifact File Path
ARTIFACT_HTML_PATH = BASE_DIR / "blockchain-briefing-live.html"

# Network Timeouts
API_TIMEOUT_SECONDS = 15

# DefiLlama Endpoints
DEFILLAMA_CHAINS_URL = "https://api.llama.fi/v2/chains"
DEFILLAMA_STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoinchains"

# Exa.ai Neural Search Queries
EXA_QUERIES = [
    "Novel DeFi liquidity primitives, restaking mechanisms, or intent solvers technical writeup",
    "Emerging crypto ecosystems, new testnets, or developer migrations governance proposal",
    "New crypto narratives, DePIN, AI x Crypto agent frameworks github mirror substack",
]

# Tavily Factual & News Search Queries
TAVILY_QUERIES = [
    "Crypto VC funding rounds and venture investments announced this week",
    "Crypto protocol exploit, hack, or smart contract vulnerability post-mortem",
    "SEC, CFTC, EU MiCA regulatory policy crypto developments",
    "Major blockchain institutional adoption, tokenized treasury, and RWA announcements",
]

# Trend Confidence Framework Stages
TREND_STAGES = ["EARLY", "ACCELERATING", "MAINSTREAM", "COOLING", "SPECULATIVE"]

# Allowed Severity Levels
SEVERITY_LEVELS = ["critical", "high", "medium", "info"]

# 19 Intelligence Briefing Sections
BRIEFING_SECTIONS = [
    {"id": "macro_market_pulse", "name": "Macro & Market Pulse", "icon": "🌐", "description": "Macroeconomic backdrop, market-wide liquidity, and systemic asset flows."},
    {"id": "defi_primitives", "name": "DeFi Primitives & Liquidity", "icon": "⚡", "description": "Novel liquidity architectures, AMM paradigms, and yield mechanisms."},
    {"id": "restaking_infra", "name": "Restaking & Shared Security", "icon": "🛡️", "description": "EigenLayer, Symbiotic, Karak, AVS validation, and slashing dynamics."},
    {"id": "intent_solvers", "name": "Intents & Solver Networks", "icon": "🎯", "description": "Cross-chain intent execution, orderflow auctions, and MEV extraction."},
    {"id": "layer1_ecosystems", "name": "L1 Ecosystems & Alternate VMs", "icon": "⛓️", "description": "MoveVM (Sui/Aptos), Solana, Monad, and parallelized EVM developments."},
    {"id": "layer2_rollups", "name": "L2 / L3 Rollups & Data Availability", "icon": "📦", "description": "ZK-rollups, Optimistic stacks, Celestia/EigenDA, and settlement layers."},
    {"id": "chain_tvl_anomalies", "name": "On-Chain TVL Anomalies", "icon": "📈", "description": "Top-growing chains by 1-day and 7-day TVL momentum via DefiLlama."},
    {"id": "stablecoin_dynamics", "name": "Stablecoin Inflows & Liquidity Shifts", "icon": "💵", "description": "Net stablecoin issuance, bridge movements, and capital rotations."},
    {"id": "ai_crypto_agents", "name": "AI x Crypto & Autonomous Agents", "icon": "🤖", "description": "On-chain agent frameworks, inference primitives, and compute tokenization."},
    {"id": "depin_hardware", "name": "DePIN & Decentralized Hardware", "icon": "📡", "description": "Decentralized physical infrastructure, wireless, sensors, and GPU clusters."},
    {"id": "rwa_tokenization", "name": "RWA & Tokenized Treasuries", "icon": "🏛️", "description": "Private credit, tokenized US Treasuries, commodity vaults, and real estate."},
    {"id": "institutional_flows", "name": "Institutional Adoption & ETFs", "icon": "💼", "description": "Spot ETF inflows, custodian partnerships, and traditional finance entry."},
    {"id": "venture_funding", "name": "Venture Capital & Deals", "icon": "💰", "description": "Seed, Series A, and strategic venture investment rounds this week."},
    {"id": "governance_proposals", "name": "Governance & Migrations", "icon": "🗳️", "description": "DAO votes, tokenomics revamps, and cross-chain protocol migrations."},
    {"id": "exploits_security", "name": "Security, Exploits & Hacks", "icon": "🚨", "description": "Smart contract vulnerabilities, bridge exploits, and post-mortem analysis."},
    {"id": "regulatory_policy", "name": "Global Regulatory & Legal", "icon": "⚖️", "description": "SEC, CFTC, EU MiCA, APAC frameworks, and legal enforcement actions."},
    {"id": "developer_testnets", "name": "Developer Migrations & Testnets", "icon": "🛠️", "description": "Active incentivized testnets, SDK releases, and developer migration trends."},
    {"id": "fomo_filter", "name": "FOMO Filter (Signal vs. Noise)", "icon": "🔍", "description": "Critical deconstruction of speculative hype, pump schemes, and ephemeral meta."},
    {"id": "alpha_radar", "name": "Emerging Alpha Radar", "icon": "📡", "description": "Early-stage asymmetric opportunities, under-the-radar github repos & protocol launches."},
]
