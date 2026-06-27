import os
import json
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Capability 1 — Natural-language asset query
# ---------------------------------------------------------------------------

class AssetFilters(BaseModel):
    reasoning: str = Field(
        ...,
        description=(
            "Brief explanation of how you interpreted the user's intent and which filters apply. "
            "If the query is ambiguous or out of scope, explain why here."
        )
    )
    is_out_of_scope: bool = Field(
        False,
        description=(
            "Set to True if the query has nothing to do with assets, security, or the platform "
            "(e.g. 'what is the weather?'). When True, leave all other fields as None."
        )
    )

    # --- Core asset fields ---
    asset_type: Optional[str] = Field(
        None,
        description=(
            "One of: domain, subdomain, ip_address, service, certificate, technology. "
            "Leave None if the user does not specify a type or says 'all assets'."
        )
    )
    status: Optional[str] = Field(
        None,
        description=(
            "Lifecycle status: active, stale, or archived. "
            "e.g. 'stale assets' → stale, 'inactive' → stale, 'decommissioned' → archived."
        )
    )
    source: Optional[str] = Field(
        None,
        description="Discovery source: import, scan, or manual."
    )
    environment: Optional[str] = Field(
        None,
        description=(
            "Normalize to one of: prod, staging, dev. "
            "e.g. 'production' → prod, 'development' → dev. "
            "Matched against asset tags and normalized_value."
        )
    )

    # --- Certificate lifecycle ---
    is_expired: Optional[bool] = Field(
        None,
        description=(
            "Set to True ONLY when the user explicitly asks for certificates that have already expired. "
            "Do NOT set this for 'expiring soon' — use expiring_within_days instead."
        )
    )
    expiring_within_days: Optional[int] = Field(
        None,
        description=(
            "Set when the user asks for certificates expiring soon or within a time window. "
            "e.g. 'expiring in 30 days' → 30, 'expiring this week' → 7, 'expiring this month' → 30. "
            "Do NOT combine with is_expired — they are mutually exclusive."
        )
    )

    # --- Time-based filters ---
    first_seen_within_days: Optional[int] = Field(
        None,
        description=(
            "Assets first discovered within this many days ago. "
            "e.g. 'new assets' → 7, 'discovered last week' → 7, 'found in the last month' → 30."
        )
    )
    last_seen_within_days: Optional[int] = Field(
        None,
        description=(
            "Assets last observed within this many days ago. "
            "e.g. 'recently seen' → 7, 'seen in the last 3 days' → 3."
        )
    )

    # --- Content and search filters ---
    value_contains: Optional[str] = Field(
        None,
        description=(
            "Substring to match against asset value (domain name, IP, service string). "
            "e.g. 'assets with admin in the name' → admin, 'subdomains containing api' → api."
        )
    )
    tag: Optional[str] = Field(
        None,
        description=(
            "A specific free-form tag the asset must have. "
            "e.g. 'tagged as critical' → critical, 'external assets' → external."
        )
    )

    # --- Service / metadata filters ---
    metadata_port: Optional[int] = Field(
        None,
        description=(
            "Port number for filtering services. "
            "e.g. 'port 443' → 443, 'SSH services' → 22, 'RDP' → 3389, 'MySQL' → 3306, 'PostgreSQL' → 5432."
        )
    )
    metadata_protocol: Optional[str] = Field(
        None,
        description="Protocol for filtering services: tcp or udp."
    )
    metadata_banner_contains: Optional[str] = Field(
        None,
        description=(
            "Substring to search inside service banners. "
            "e.g. 'nginx servers' → nginx, 'apache services' → apache, 'openssh' → openssh."
        )
    )
    metadata_tech_version: Optional[str] = Field(
        None,
        description=(
            "Specific technology version string. "
            "e.g. 'nginx 1.24' → 1.24, 'PHP 7' → 7."
        )
    )
    metadata_cert_issuer: Optional[str] = Field(
        None,
        description=(
            "Certificate issuer name. "
            "e.g. \"Let's Encrypt certificates\" → Let's Encrypt, 'DigiCert' → DigiCert."
        )
    )

    # --- Pagination and sorting ---
    limit: Optional[int] = Field(
        50,
        description=(
            "Max number of results. Default 50. "
            "e.g. 'first 5' → 5, 'top 10' → 10, 'all' → 1000."
        )
    )
    order_by: Optional[str] = Field(
        None,
        description=(
            "Field to sort by. One of: last_seen, first_seen, value, certificate_expires_at. "
            "e.g. 'most recently seen' → last_seen, 'oldest first' → first_seen, "
            "'alphabetically' → value, 'soonest expiring' → certificate_expires_at."
        )
    )
    order_dir: Optional[str] = Field(
        None,
        description=(
            "Sort direction: asc or desc. "
            "e.g. 'oldest first' → asc, 'most recent' → desc, 'soonest expiring' → asc."
        )
    )
    logical_operator: Optional[str] = "AND" 


def translate_nl_query_to_filters(user_query: str) -> AssetFilters:
    llm = init_chat_model("command-a-plus-05-2026", temperature=0.0)

    system_prompt = (
            "You are an expert AI Assistant for DarkAtlas, an Attack Surface Monitoring (ASM) platform.\n"
            "Your sole task is to translate a user's natural language query into structured database filters.\n\n"
            "Rules:\n"
            "1. Populate `reasoning` first — explain your interpretation before setting any filter.\n"
            "2. Only set filters that are clearly implied by the query. Do not guess or assume.\n"
            "3. If the query is unrelated to assets or security, set `is_out_of_scope` to True and leave all other fields as None.\n"
            "4. `is_expired` and `expiring_within_days` are mutually exclusive — never set both.\n"
            "5. Normalize environment values to: prod, staging, or dev.\n"
            "6. Map well-known service names to ports: SSH→22, HTTP→80, HTTPS→443, RDP→3389, MySQL→3306, PostgreSQL→5432.\n"
            "7. If the user says 'all' or gives no type, leave `asset_type` as None.\n"
            "8. For ambiguous queries (e.g. 'show me something interesting'), set `is_out_of_scope` to False "
            "but explain the ambiguity in `reasoning` and apply only the broadest safe defaults.\n"
            "9. Strictly ensure that if the user mentions a timeframe or days (e.g., next 60 days, last 7 days), you MUST explicitly populate 'expiring_within_days', "
            "'first_seen_within_days', or 'last_seen_within_days' in the final JSON output.\n"
            "10. CRITICAL TIMEFRAME RULE: Never omit timeframe integers in the final JSON schema when mentioned in prose (e.g., if 'next 60 days' is parsed, 'expiring_within_days' MUST be exactly 60).\n"
            "11. LOGICAL OPERATOR RULE: By default, `logical_operator` is 'AND'. However, if the user explicitly asks for an OR condition between environments, tags, or values (e.g., 'critical OR prod', 'staging OR dev'), you MUST set `logical_operator` to 'OR' and populate the respective fields (e.g., environment='prod', tag='critical') so the backend can join them via SQL OR logic."
        )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{query}")
    ])

    structured_llm = llm.with_structured_output(AssetFilters)
    chain = prompt | structured_llm
    return chain.invoke({"query": user_query})


# ---------------------------------------------------------------------------
# Capability 2 — Risk scoring & summarization
# ---------------------------------------------------------------------------

class RiskFinding(BaseModel):
    asset_id: str = Field(..., description="The unique ID of the vulnerable asset")
    asset_type: str = Field(..., description="The type of the asset: domain, subdomain, ip_address, service, certificate, technology")
    asset_value: str = Field(..., description="The value/name of the asset")
    risk_level: str = Field(..., description="Critical, High, Medium, or Low")
    reason: str = Field(
        ...,
        description=(
            "Specific reason this asset is risky. Be concrete: "
            "e.g. 'Certificate expired 3 days ago', 'Port 22 (SSH) exposed publicly', "
            "'End-of-life technology: PHP 5.6', 'Stale asset last seen 90 days ago with active service'."
        )
    )


class RiskAssessment(BaseModel):
    overall_risk_level: str = Field(..., description="Overall posture: Critical, High, Medium, or Low")
    overall_risk_score: int = Field(..., description="Score from 0 (Safe) to 100 (Extremely Vulnerable)")
    summary: str = Field(
        ...,
        description=(
            "Concise executive summary (3-5 sentences) covering: "
            "total assets analyzed, key risks found (expired certs, sensitive ports, EOL tech), "
            "and the most urgent action needed."
        )
    )
    findings: List[RiskFinding] = Field(
        default_factory=list,
        description="Individual high-risk findings, ordered from most to least severe."
    )


def analyze_assets_risk(assets_json: List[Dict[str, Any]]) -> RiskAssessment:
    llm = init_chat_model("command-a-plus-05-2026", temperature=0.0)

    system_prompt = (
        "You are an expert Cyber Security Analyst and CISO working on the DarkAtlas ASM platform.\n"
        "Analyze the provided JSON list of assets and produce a strict, grounded risk assessment.\n\n"
        "Risk scoring rules (apply in order — highest matching rule wins):\n"
        "- Critical (90-100): Expired TLS certificates on prod assets | Admin/DB ports exposed (22, 3389, 3306, 5432) on prod | EOL technology in active prod use.\n"
        "- High (70-89): Certificates expiring within 7 days | SSH/RDP/DB ports exposed on non-prod | Stale prod assets still receiving traffic.\n"
        "- Medium (40-69): HTTP (port 80) without a paired certificate | Stale assets with active linked services | Technologies with known CVEs but not EOL.\n"
        "- Low (0-39): Active domains/subdomains/IPs with no misconfigurations detected.\n\n"
        "EOL technology examples: PHP < 7.4, Python < 3.8, OpenSSL < 1.1.1, nginx < 1.18, Apache < 2.4.\n\n"
        "Important rules:\n"
        "- Only report findings that are supported by data in the provided asset list.\n"
        "- Do NOT invent assets, ports, or vulnerabilities not present in the data.\n"
        "- Use the asset's `id` field exactly as provided in `asset_id`.\n"
        "- Order findings from most to least severe."
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Analyze the risk for these assets:\n\n{assets_data}")
    ])

    structured_llm = llm.with_structured_output(RiskAssessment)
    chain = prompt | structured_llm
    return chain.invoke({"assets_data": json.dumps(assets_json, default=str)})


# ---------------------------------------------------------------------------
# Capability 4 — Natural-language report generation
# ---------------------------------------------------------------------------

def generate_markdown_report(assets_json: List[Dict[str, Any]], original_query: str) -> str:
    llm = init_chat_model("command-a-plus-05-2026", temperature=0.2)

    system_prompt = (
        "You are a Chief Information Security Officer (CISO) producing a formal report.\n"
        "Generate a professional Attack Surface Inventory & Risk Report in clean Markdown.\n\n"
        "The report MUST follow this exact structure:\n"
        "# Attack Surface Report\n"
        "## 1. Executive Summary\n"
        "## 2. Asset Inventory\n"
        "   - A markdown table: Asset Type | Count | Status Breakdown\n"
        "## 3. Critical Findings\n"
        "   - Bullet list of specific risks found in the data\n"
        "## 4. Remediation Steps\n"
        "   - Numbered, prioritized, actionable steps\n\n"
        "Rules:\n"
        "- Only reference assets that are present in the provided dataset.\n"
        "- Do NOT invent vulnerabilities or assets not in the data.\n"
        "- Be concise and technical. Target audience is a CISO and security engineers.\n"
        "- Use bold for critical items. Use markdown tables where appropriate.\n"
        "- Return ONLY the markdown report — no preamble, no explanation."
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Report scope (user's original request): {query}\n\nAsset dataset:\n{assets_data}")
    ])

    # FIX: Use plain string output instead of structured output.
    # The report is a human-readable markdown document, not a machine-parsed object.
    chain = prompt | llm
    response = chain.invoke({
        "query": original_query,
        "assets_data": json.dumps(assets_json, default=str)
    })
    return response.content