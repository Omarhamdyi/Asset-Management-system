from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Any

from app.database import get_db
from app.models import Assets
from app.services.ai_service import translate_nl_query_to_filters
from datetime import datetime, timedelta

router = APIRouter(prefix="/analyze", tags=["AI Analysis"])

class QueryRequest(BaseModel):
    query: str

@router.post("/query")
def natural_language_query(payload: QueryRequest, db: Session = Depends(get_db)):
    try:
        filters = translate_nl_query_to_filters(payload.query)
        
        query_builder = db.query(Assets)
        
        if filters.asset_type and filters.asset_type.lower() != "all":
            query_builder = query_builder.filter(Assets.asset_type == filters.asset_type.lower())
            
        if filters.status:
            query_builder = query_builder.filter(Assets.status == filters.status.lower())
            
        if filters.source:
            query_builder = query_builder.filter(Assets.source == filters.source.lower())
            
        if filters.environment:
            env_keyword = filters.environment.lower()
            query_builder = query_builder.filter(
                (Assets.normalized_value.like(f"%{env_keyword}%")) | 
                (Assets.tags.any(env_keyword))
            )
            
        if filters.tag:
            query_builder = query_builder.filter(Assets.tags.any(filters.tag.lower()))

        if filters.is_expired is True:
            current_time = datetime.utcnow()
            query_builder = query_builder.filter(
                Assets.asset_type == "certificate",
                Assets.certificate_expires_at < current_time
            )

        if filters.first_seen_within_days:
            time_threshold = datetime.utcnow() - timedelta(days=filters.first_seen_within_days)
            query_builder = query_builder.filter(Assets.first_seen >= time_threshold)

        if filters.last_seen_within_days:
            time_threshold = datetime.utcnow() - timedelta(days=filters.last_seen_within_days)
            query_builder = query_builder.filter(Assets.last_seen >= time_threshold)

        if filters.metadata_port:
            query_builder = query_builder.filter(Assets.metadata_['port'].as_integer() == filters.metadata_port)
            
        if filters.metadata_banner_contains:
            query_builder = query_builder.filter(Assets.metadata_['banner'].as_string().ilike(f"%{filters.metadata_banner_contains}%"))

        if filters.order_by:
            column = getattr(Assets, filters.order_by, None)
            if column:
                if filters.order_dir and filters.order_dir.lower() == "desc":
                    query_builder = query_builder.order_by(column.desc())
                else:
                    query_builder = query_builder.order_by(column.asc())

        result_limit = filters.limit if filters.limit is not None else 50
        query_builder = query_builder.limit(result_limit)
        
        results = query_builder.all()
        
        return {
            "user_query": payload.query,
            "interpreted_filters": filters.dict(exclude_none=True),
            "count": len(results),
            "results": results
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI Layer Error: {str(e)}"
        )