import os
from typing import Optional, List, Any
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv

load_dotenv()

class AssetFilters(BaseModel):
    reasoning: str = Field(
        ..., 
        description="A brief explanation of how you interpreted the user's intent and which filters apply."
    )

    # Core Asset Fields
    asset_type: Optional[str] = Field(
        None,
        description="One of: domain, subdomain, ip_address, service, certificate, technology, or None if not specified",
    )
    status: Optional[str] = Field(
        None, description="One of: active, stale, archived"
    )
    source: Optional[str] = Field(
        None, description="One of: import, scan, manual"
    )
    environment: Optional[str] = Field(
        None,
        description="Environment tag like prod, staging, dev — extracted from value, tags or metadata",
    )

    # Certificate lifecycle
    is_expired: Optional[bool] = Field(
        None, description="True if user asks for expired certificates"
    )
    expiring_within_days: Optional[int] = Field(
        None,
        description="Number of days within which certificates are expiring. e.g. 'expiring in 30 days' → 30",
    )

    # Time-based filters
    first_seen_within_days: Optional[int] = Field(
        None,
        description="Assets first discovered within this many days ago. e.g. 'discovered last week' → 7",
    )
    last_seen_within_days: Optional[int] = Field(
        None, description="Assets last seen within this many days ago. e.g. 'seen in the last 3 days' → 3"
    )

    # Content & Search filters
    value_contains: Optional[str] = Field(
        None,
        description="Substring to search in asset value. e.g. 'api' → assets with api in their name",
    )
    tag: Optional[str] = Field(
        None, description="A specific free-form tag the asset must have (e.g. 'critical', 'external')"
    )

    metadata_port: Optional[int] = Field(
        None, description="Specific port number if filtering services or ports, e.g., 443, 80, 22"
    )
    metadata_protocol: Optional[str] = Field(
        None, description="Protocol type if filtering services, e.g., 'tcp', 'udp'"
    )
    metadata_banner_contains: Optional[str] = Field(
        None, description="Substring to look for inside service banners, e.g., 'nginx', 'apache', 'openssh'"
    )
    metadata_tech_version: Optional[str] = Field(
        None, description="Specific technology version if requested, e.g., '1.24', '8.0'"
    )
    metadata_cert_issuer: Optional[str] = Field(
        None, description="Certificate issuer name if requested, e.g., 'Let's Encrypt', 'DigiCert'"
    )

    # Pagination
    limit: Optional[int] = Field(
        50,
        description="Maximum number of results to return. Default 50. e.g. 'first 5' → 5, 'top 10' → 10",
    )

    # Sorting
    order_by: Optional[str] = Field(
        None,
        description="Field to sort by: last_seen, first_seen, value, certificate_expires_at",
    )
    order_dir: Optional[str] = Field(
        None, description="Sort direction: asc or desc"
    )


def translate_nl_query_to_filters(user_query: str) -> AssetFilters:
    llm = init_chat_model("command-a-plus-05-2026", temperature=0.0)
    
    system_prompt = (
        "You are an expert AI Assistant for DarkAtlas, an Attack Surface Monitoring (ASM) platform.\n"
        "Your sole task is to translate a user's natural language query into highly structured database filters.\n"
        "First, populate the `reasoning` field with your thought process.\n"
        "Then, extract fields strictly based on the provided schema. Do not invent filters outside the schema.\n"
        "If a concept like 'expired' is mentioned, set `is_expired` to True.\n"
        "If the user asks for ports, specific technologies, banners, or issuers, map them to the corresponding `metadata_*` flat fields."
    )
    
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{query}")
    ])
    
    structured_llm = llm.with_structured_output(AssetFilters)
    
    chain = prompt_template | structured_llm
    return chain.invoke({"query": user_query})