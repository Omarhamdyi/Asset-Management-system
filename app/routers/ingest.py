import uuid
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List

from app.database import get_db
from app.models import Assets, Organizations  
from app.schemas import AssetIngestSchema

router = APIRouter(prefix="/ingest", tags=["Data Ingestion"])

NAMESPACE_BUGUARD = uuid.UUID('12345678-1234-5678-1234-567812345678')

@router.post("/bulk", status_code=status.HTTP_201_CREATED)
def bulk_import(assets: List[AssetIngestSchema], db: Session = Depends(get_db)):
    inserted_count = 0
    updated_count = 0
    
    DEFAULT_ORG_ID = uuid.UUID('00000000-0000-0000-0000-000000000000')
    org = db.query(Organizations).filter(Organizations.id == DEFAULT_ORG_ID).first()
    if not org:
        org = Organizations(id=DEFAULT_ORG_ID, slug="buguard-org", name="Buguard Organization")
        db.add(org)
        db.commit()

    for asset_data in assets:
        try:
            try:
                asset_uuid = uuid.UUID(asset_data.id)
            except ValueError:
                asset_uuid = uuid.uuid5(NAMESPACE_BUGUARD, asset_data.id)
            
            norm_value = asset_data.value.strip().lower()
            asset_type_clean = asset_data.type.strip().lower()
            
            status_clean = asset_data.status if asset_data.status in ['active', 'stale', 'archived'] else 'active'
            source_clean = asset_data.source if asset_data.source in ['import', 'scan', 'manual'] else 'import'

            existing_asset = db.query(Assets).filter(
                Assets.organization_id == DEFAULT_ORG_ID,
                Assets.asset_type == asset_type_clean,
                Assets.normalized_value == norm_value
            ).first()
            
            if existing_asset:
                existing_asset.last_seen = datetime.utcnow()
                if existing_asset.status == "stale":
                    existing_asset.status = "active"
                
                existing_asset.tags = list(set(existing_asset.tags + asset_data.tags))
                existing_asset.metadata_ = {**existing_asset.metadata_, **asset_data.metadata}
                existing_asset.updated_at = datetime.utcnow()
                updated_count += 1
            else:
                new_asset = Assets(
                    id=asset_uuid,
                    organization_id=DEFAULT_ORG_ID,
                    asset_type=asset_type_clean,
                    value=asset_data.value.strip(),
                    normalized_value=norm_value,
                    status=status_clean,
                    source=source_clean,
                    tags=asset_data.tags,
                    metadata_={**asset_data.metadata},
                    first_seen=datetime.utcnow(),
                    last_seen=datetime.utcnow(),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.add(new_asset)
                inserted_count += 1
                
        except Exception as e:
            db.rollback()
            print(f"Error importing asset {asset_data.id}: {str(e)}")
            continue
            
    db.commit()
    
    return {
        "message": "Bulk import completed successfully",
        "inserted": inserted_count,
        "updated": updated_count
    }