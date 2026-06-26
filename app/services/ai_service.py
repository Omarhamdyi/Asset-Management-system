import os
from typing import Optional, List
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv

load_dotenv()
class AssetFilters(BaseModel):
    asset_type: str = Field(
        ..., description="The type of the asset, must be one of: domain, subdomain, ip_address, service, certificate, or 'all' if not specified"
    )
    status: Optional[str] = Field(
        None, description="The lifecycle status, must be one of: active, stale, archived"
    )
    source: Optional[str] = Field(
        None, description="The discovery source, must be one of: import, scan, manual"
    )
    is_expired: Optional[bool] = Field(
        None, description="Set to True if the user asks for expired certificates or assets, otherwise None"
    )
    environment: Optional[str] = Field(
        None, description="Extracted environment if mentioned like 'production', 'prod', 'staging', 'dev'"
    )


def translate_nl_query_to_filters(user_query: str) -> AssetFilters:

    llm = init_chat_model("command-a-plus-05-2026", temperature=0.0)
    
    system_prompt = (
        "You are an expert AI Assistant for DarkAtlas, an Attack Surface Monitoring (ASM) platform.\n"
        "Your sole task is to translate a user's natural language query into structured database filters.\n"
        "Extract fields strictly based on the provided schema. Do not invent filters outside the schema.\n"
        "If a concept like 'expired' is mentioned, set `is_expired` to True.\n"
        "If an environment like 'production' or 'prod' is mentioned, extract it into the `environment` field."
    )
    
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{query}")
    ])
    
    structured_llm = llm.with_structured_output(AssetFilters)
    
    chain = prompt_template | structured_llm
    return chain.invoke({"query": user_query})  