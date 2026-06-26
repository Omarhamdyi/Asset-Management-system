import uuid
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List

from app.database import get_db
from app.models import Assets, Organizations, AssetRelationships  
from app.schemas import AssetIngestSchema
from datetime import datetime

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

    processed_assets = []

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
                
                if existing_asset.asset_type == "certificate" and asset_data.metadata:
                    expiry_date_str = asset_data.metadata.get("expiry_date")
                    if expiry_date_str:
                        try:
                            existing_asset.certificate_expires_at = datetime.strptime(expiry_date_str, "%Y-%m-%d")
                        except ValueError:
                            pass
                
            
                existing_asset.updated_at = datetime.utcnow()
                updated_count += 1
                processed_assets.append(existing_asset)
            else:
                cert_expiry = None
                if asset_type_clean == "certificate" and asset_data.metadata:
                    expiry_date_str = asset_data.metadata.get("expiry_date")
                    if expiry_date_str:
                        try:
                            cert_expiry = datetime.strptime(expiry_date_str, "%Y-%m-%d")
                        except ValueError:
                            pass 

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
                    certificate_expires_at=cert_expiry, 
                    first_seen=datetime.utcnow(),
                    last_seen=datetime.utcnow(),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.add(new_asset)
                inserted_count += 1
                processed_assets.append(new_asset)
                
        except Exception as e:
            db.rollback()
            print(f"Error importing asset {asset_data.id}: {str(e)}")
            continue
            
    db.commit()


    for asset in processed_assets:
        try:
            if asset.asset_type == 'subdomain':
                parts = asset.normalized_value.split('.')
                if len(parts) > 2:
                    parent_domain_value = ".".join(parts[-2:]) 
                    
                    target_domain = db.query(Assets).filter(
                        Assets.organization_id == DEFAULT_ORG_ID,
                        Assets.asset_type == 'domain',
                        Assets.normalized_value == parent_domain_value
                    ).first()
                    
                    if target_domain:
                        _create_relationship(db, DEFAULT_ORG_ID, asset, target_domain, 'subdomain_to_domain')

            elif asset.asset_type == 'certificate':
                target_value = asset.normalized_value.replace("cn=", "").strip()
                
                target_asset = db.query(Assets).filter(
                    Assets.organization_id == DEFAULT_ORG_ID,
                    Assets.normalized_value == target_value,
                    Assets.asset_type.in_(['domain', 'subdomain'])
                ).first()
                
                if target_asset:
                    rel_type = 'certificate_to_domain' if target_asset.asset_type == 'domain' else 'certificate_to_subdomain'
                    _create_relationship(db, DEFAULT_ORG_ID, asset, target_asset, rel_type)

        except Exception as rel_error:
            db.rollback()
            print(f"Error establishing relationship for asset {asset.id}: {str(rel_error)}")
            continue

    db.commit()
    
    return {
        "message": "Bulk import and relationships graphing completed successfully",
        "inserted_assets": inserted_count,
        "updated_assets": updated_count
    }

def _create_relationship(db: Session, org_id: uuid.UUID, source_asset: Assets, target_asset: Assets, rel_type: str):
    existing_rel = db.query(AssetRelationships).filter(
        AssetRelationships.organization_id == org_id,
        AssetRelationships.source_asset_id == source_asset.id,
        AssetRelationships.target_asset_id == target_asset.id,
        AssetRelationships.relationship_type == rel_type
    ).first()
    
    if not existing_rel:
        new_rel = AssetRelationships(
            id=uuid.uuid4(), 
            organization_id=org_id,
            source_asset_id=source_asset.id,
            source_asset_type=source_asset.asset_type,
            target_asset_id=target_asset.id,
            target_asset_type=target_asset.asset_type,
            relationship_type=rel_type,
            metadata_={},
            first_seen=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(new_rel)