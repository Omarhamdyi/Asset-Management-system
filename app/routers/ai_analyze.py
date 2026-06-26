from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Any

from app.database import get_db
from app.models import Assets
from app.services.ai_service import translate_nl_query_to_filters
from datetime import datetime

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
            
        if filters.is_expired is True:
            current_time = datetime.now()
            query_builder = query_builder.filter(Assets.asset_type == "certificate")
            
            if hasattr(Assets, 'certificate_expires_at'):
                query_builder = query_builder.filter(Assets.certificate_expires_at < current_time)
            else:
                current_date_str = current_time.strftime("%Y-%m-%d")
                query_builder = query_builder.filter(
                    Assets.metadata_['expiry_date'].as_string() < current_date_str
                )
        results = query_builder.limit(50).all()
        
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