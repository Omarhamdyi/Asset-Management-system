from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import any_,or_
from pydantic import BaseModel
from typing import Any, List, Dict, Optional
from datetime import datetime, timedelta

from app.database import get_db
from app.models import Assets
from app.services.ai_service import (
    translate_nl_query_to_filters,
    analyze_assets_risk,
    generate_markdown_report
)

router = APIRouter(prefix="/analyze", tags=["AI Analysis"])

# ---------------------------------------------------------------------------
# Valid values — used to guard against LLM hallucinating filter values
# ---------------------------------------------------------------------------
VALID_ASSET_TYPES = {'domain', 'subdomain', 'ip_address', 'service', 'certificate', 'technology'}
VALID_STATUSES    = {'active', 'stale', 'archived'}
VALID_SOURCES     = {'import', 'scan', 'manual'}
VALID_ORDER_BY    = {'last_seen', 'first_seen', 'value', 'certificate_expires_at'}
VALID_ORDER_DIRS  = {'asc', 'desc'}
VALID_ENVS        = {'prod', 'staging', 'dev'}

class QueryRequest(BaseModel):
    query: str


def _serialize_asset(asset) -> Dict:
    """Convert a SQLAlchemy Assets ORM object to a JSON-safe dict."""
    return {
        "id": str(asset.id),
        "asset_type": asset.asset_type,
        "value": asset.value,
        "normalized_value": asset.normalized_value,
        "status": asset.status,
        "source": asset.source,
        "tags": asset.tags,
        "metadata": asset.metadata_,
        "first_seen": asset.first_seen.isoformat() if asset.first_seen else None,
        "last_seen": asset.last_seen.isoformat() if asset.last_seen else None,
        "certificate_expires_at": asset.certificate_expires_at.isoformat() if asset.certificate_expires_at else None,
    }


def _validate_filters(filters):
    """
    Guard against the LLM hallucinating invalid filter values.
    Raises HTTPException 400 with a clear message if any value is outside
    the known enum set — this satisfies the spec requirement:
    'guard against the model inventing assets that aren't in the data.'
    """
    if filters.asset_type and filters.asset_type.lower() not in VALID_ASSET_TYPES:
        raise HTTPException(status_code=400, detail=f"AI returned unrecognized asset_type: '{filters.asset_type}'. Valid types: {sorted(VALID_ASSET_TYPES)}")
    if filters.status and filters.status.lower() not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"AI returned unrecognized status: '{filters.status}'. Valid values: {sorted(VALID_STATUSES)}")
    if filters.source and filters.source.lower() not in VALID_SOURCES:
        raise HTTPException(status_code=400, detail=f"AI returned unrecognized source: '{filters.source}'. Valid values: {sorted(VALID_SOURCES)}")
    if filters.order_by and filters.order_by.lower() not in VALID_ORDER_BY:
        raise HTTPException(status_code=400, detail=f"AI returned unrecognized order_by: '{filters.order_by}'. Valid values: {sorted(VALID_ORDER_BY)}")
    if filters.order_dir and filters.order_dir.lower() not in VALID_ORDER_DIRS:
        raise HTTPException(status_code=400, detail=f"AI returned unrecognized order_dir: '{filters.order_dir}'. Must be 'asc' or 'desc'.")
    if filters.environment and filters.environment.lower() not in VALID_ENVS:
        raise HTTPException(status_code=400, detail=f"AI returned unrecognized environment: '{filters.environment}'. Valid values: {sorted(VALID_ENVS)}")


def _execute_filtered_asset_query(db: Session, user_query: str):
    filters = translate_nl_query_to_filters(user_query)

    if filters.is_out_of_scope:
        raise HTTPException(
            status_code=400,
            detail=f"Query is out of scope for this platform. Reason: {filters.reasoning}"
        )

    _validate_filters(filters)

    query_builder = db.query(Assets)
    current_time = datetime.utcnow()

    # 🛠️ الفلاتر العامة (اللي دايماً بتطبق AND زي الـ asset_type والـ status والـ limit)
    if filters.asset_type and filters.asset_type.lower() != "all":
        query_builder = query_builder.filter(Assets.asset_type == filters.asset_type.lower())

    if filters.status:
        query_builder = query_builder.filter(Assets.status == filters.status.lower())

    if filters.source:
        query_builder = query_builder.filter(Assets.source == filters.source.lower())

    local_conditions = []

    if filters.environment:
        env_keyword = filters.environment.lower()
        local_conditions.append(
            (Assets.normalized_value.ilike(f"%{env_keyword}%")) |
            (env_keyword == any_(Assets.tags))
        )

    if filters.tag:
        local_conditions.append(filters.tag.lower() == any_(Assets.tags))

    if filters.value_contains:
        local_conditions.append(Assets.normalized_value.ilike(f"%{filters.value_contains}%"))

    if local_conditions:
        if filters.logical_operator == "OR":
            query_builder = query_builder.filter(or_(*local_conditions))
        else:
            for cond in local_conditions:
                query_builder = query_builder.filter(cond)

    if filters.is_expired is True:
        query_builder = query_builder.filter(
            Assets.asset_type == "certificate",
            Assets.certificate_expires_at < current_time
        )

    if filters.expiring_within_days:
        future_threshold = current_time + timedelta(days=filters.expiring_within_days)
        query_builder = query_builder.filter(
            Assets.asset_type == "certificate",
            Assets.certificate_expires_at >= current_time,
            Assets.certificate_expires_at <= future_threshold
        )

    if filters.first_seen_within_days:
        time_threshold = current_time - timedelta(days=filters.first_seen_within_days)
        query_builder = query_builder.filter(Assets.first_seen >= time_threshold)

    if filters.last_seen_within_days:
        time_threshold = current_time - timedelta(days=filters.last_seen_within_days)
        query_builder = query_builder.filter(Assets.last_seen >= time_threshold)

    if filters.metadata_port:
        query_builder = query_builder.filter(Assets.metadata_['port'].as_integer() == filters.metadata_port)

    if filters.metadata_protocol:
        query_builder = query_builder.filter(Assets.metadata_['protocol'].as_string() == filters.metadata_protocol.lower())

    if filters.metadata_banner_contains:
        query_builder = query_builder.filter(Assets.metadata_['banner'].as_string().ilike(f"%{filters.metadata_banner_contains}%"))

    if filters.metadata_tech_version:
        query_builder = query_builder.filter(Assets.metadata_['version'].as_string() == filters.metadata_tech_version)

    if filters.metadata_cert_issuer:
        query_builder = query_builder.filter(Assets.metadata_['issuer'].as_string().ilike(f"%{filters.metadata_cert_issuer}%"))

    if filters.order_by:
        column = getattr(Assets, filters.order_by, None)
        if column:
            if filters.order_dir and filters.order_dir.lower() == "desc":
                query_builder = query_builder.order_by(column.desc())
            else:
                query_builder = query_builder.order_by(column.asc())

    result_limit = filters.limit if filters.limit is not None else 50
    query_builder = query_builder.limit(result_limit)

    return filters, query_builder.all()


# ---------------------------------------------------------------------------
# Capability 1 — Natural-language asset query
# ---------------------------------------------------------------------------

@router.post("/query")
def natural_language_query(payload: QueryRequest, db: Session = Depends(get_db)):
    try:
        filters, results = _execute_filtered_asset_query(db, payload.query)
        return {
            "user_query": payload.query,
            "interpreted_filters": filters.dict(exclude_none=True),
            "count": len(results),
            "results": [_serialize_asset(a) for a in results],  # FIX: serialize ORM objects
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Layer Error (Query): {str(e)}")


# ---------------------------------------------------------------------------
# Capability 2 — Risk scoring & summarization
# ---------------------------------------------------------------------------

@router.post("/risk")
def natural_language_risk_assessment(payload: QueryRequest, db: Session = Depends(get_db)):
    try:
        filters, results = _execute_filtered_asset_query(db, payload.query)

        if not results:
            return {
                "user_query": payload.query,
                "message": "No assets found matching this query to assess.",
                "risk_assessment": {
                    "overall_risk_level": "Low",
                    "overall_risk_score": 0,
                    "summary": "No matching assets discovered in the attack surface.",
                    "findings": []
                }
            }

        assets_list = [_serialize_asset(a) for a in results]
        risk_report = analyze_assets_risk(assets_list)

        return {
            "user_query": payload.query,
            "interpreted_filters": filters.dict(exclude_none=True),
            "analyzed_assets_count": len(results),
            "risk_assessment": risk_report
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Layer Error (Risk): {str(e)}")


# ---------------------------------------------------------------------------
# Capability 4 — Natural-language report generation
# Two response modes:
#   POST /analyze/report       → JSON with report_markdown as a plain string
#   GET  /analyze/report/download → raw .md file download, renders properly
# ---------------------------------------------------------------------------

@router.post("/report")
def natural_language_report_json(payload: QueryRequest, db: Session = Depends(get_db)):
    """
    Returns the report as a JSON response with report_markdown as a plain string.
    Copy the value of report_markdown and paste into any markdown viewer.
    """
    try:
        filters, results = _execute_filtered_asset_query(db, payload.query)

        if not results:
            raise HTTPException(status_code=404, detail="No assets found within this scope to generate a report.")

        assets_list = [_serialize_asset(a) for a in results]
        markdown_content = generate_markdown_report(assets_list, payload.query)

        return {
            "user_query": payload.query,
            "interpreted_filters": filters.dict(exclude_none=True),
            "assets_included": len(results),
            # Plain string — copy and paste into any .md viewer or README
            "report_markdown": markdown_content,
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Layer Error (Report): {str(e)}")


@router.post("/report/download", response_class=PlainTextResponse)
def natural_language_report_download(payload: QueryRequest, db: Session = Depends(get_db)):
    """
    Returns the report as a raw markdown file download.
    Use this endpoint when you want a properly rendered .md file.
    """
    try:
        filters, results = _execute_filtered_asset_query(db, payload.query)

        if not results:
            raise HTTPException(status_code=404, detail="No assets found within this scope to generate a report.")

        assets_list = [_serialize_asset(a) for a in results]
        markdown_content = generate_markdown_report(assets_list, payload.query)

        if isinstance(markdown_content, list):
            text_blocks = [block.get("text", "") for block in markdown_content if isinstance(block, dict) and block.get("type") == "text"]
            if text_blocks:
                markdown_content = "".join(text_blocks)
            else:
                markdown_content = str(markdown_content)
        elif not isinstance(markdown_content, str):
            markdown_content = str(markdown_content)

        return PlainTextResponse(
            content=markdown_content,
            media_type="application/octet-stream", 
            headers={
                "Content-Disposition": "attachment; filename=\"attack_surface_report.md\"",
                "Content-Type": "text/markdown; charset=utf-8"
            }
        )
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Layer Error (Report Download): {str(e)}")