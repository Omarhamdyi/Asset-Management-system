import uuid
from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List

from app.database import get_db
from app.models import Assets, Organizations, AssetRelationships  
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
        try:
            db.commit()
        except Exception:
            db.rollback()
            org = db.query(Organizations).filter(Organizations.id == DEFAULT_ORG_ID).first()

    processed_assets = []

    for asset_data in assets:
        try:
            asset_type_clean = asset_data.type.strip().lower()
            if asset_type_clean not in ['domain', 'subdomain', 'ip_address', 'service', 'certificate', 'technology']:
                print(f"Skipping asset due to invalid type: {asset_data.type}")
                continue

            status_clean = asset_data.status.strip().lower() if asset_data.status else 'active'
            if status_clean not in ['active', 'stale', 'archived']:
                status_clean = 'active'

            source_clean = asset_data.source.strip().lower() if asset_data.source else 'import'
            if source_clean not in ['import', 'scan', 'manual']:
                source_clean = 'import'

            try:
                asset_uuid = uuid.UUID(asset_data.id)
            except (ValueError, TypeError):
                asset_uuid = uuid.uuid5(NAMESPACE_BUGUARD, f"{asset_type_clean}:{asset_data.value.strip().lower()}")
            
            norm_value = asset_data.value.strip().lower()

            existing_asset = db.query(Assets).filter(
                Assets.organization_id == DEFAULT_ORG_ID,
                Assets.asset_type == asset_type_clean,
                Assets.normalized_value == norm_value
            ).first()
            
            if existing_asset:
                existing_asset.last_seen = datetime.utcnow()
                if existing_asset.status == "stale" and status_clean == "active":
                    existing_asset.status = "active"
                elif status_clean != 'active':
                    existing_asset.status = status_clean
                
                existing_asset.tags = list(set(existing_asset.tags + asset_data.tags))
                existing_asset.metadata_ = {**existing_asset.metadata_, **asset_data.metadata}
                
                if existing_asset.asset_type == "certificate" and asset_data.metadata:
                    expiry_date_str = asset_data.metadata.get("expiry_date")
                    if expiry_date_str:
                        try:
                            if "T" in expiry_date_str:
                                existing_asset.certificate_expires_at = datetime.strptime(expiry_date_str.split("T")[0], "%Y-%m-%d")
                            else:
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
                            if "T" in expiry_date_str:
                                cert_expiry = datetime.strptime(expiry_date_str.split("T")[0], "%Y-%m-%d")
                            else:
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
                
            db.flush()
                
        except Exception as e:
            db.rollback()
            print(f"Error importing asset: {str(e)}")
            continue
            
    try:
        db.flush()
    except Exception as flush_err:
        db.rollback()
        print(f"Initial bulk flush failed: {str(flush_err)}")

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

                linked_ip = asset.metadata_.get("runs_on") or asset.metadata_.get("resolves_to") or asset.metadata_.get("ip")
                if linked_ip:
                    target_ip = db.query(Assets).filter(
                        Assets.organization_id == DEFAULT_ORG_ID,
                        Assets.asset_type == 'ip_address',
                        Assets.normalized_value == str(linked_ip).strip().lower()
                    ).first()
                    if target_ip:
                        _create_relationship(db, DEFAULT_ORG_ID, asset, target_ip, 'subdomain_resolves_to_ip')
                        _create_relationship(db, DEFAULT_ORG_ID, target_ip, asset, 'ip_resolves_to_subdomain')

            elif asset.asset_type == 'ip_address':
                linked_subdomain = asset.metadata_.get("resolved_subdomain") or asset.metadata_.get("domain") or asset.metadata_.get("hostname")
                if linked_subdomain:
                    target_sub = db.query(Assets).filter(
                        Assets.organization_id == DEFAULT_ORG_ID,
                        Assets.asset_type == 'subdomain',
                        Assets.normalized_value == str(linked_subdomain).strip().lower()
                    ).first()
                    if target_sub:
                        _create_relationship(db, DEFAULT_ORG_ID, target_sub, asset, 'subdomain_resolves_to_ip')
                        _create_relationship(db, DEFAULT_ORG_ID, asset, target_sub, 'ip_resolves_to_subdomain')

            elif asset.asset_type == 'service':
                ip_value = asset.metadata_.get("runs_on") or asset.metadata_.get("ip") or asset.metadata_.get("ip_address")
                if ip_value:
                    target_ip = db.query(Assets).filter(
                        Assets.organization_id == DEFAULT_ORG_ID,
                        Assets.asset_type == 'ip_address',
                        Assets.normalized_value == str(ip_value).strip().lower()
                    ).first()
                    if target_ip:
                        _create_relationship(db, DEFAULT_ORG_ID, asset, target_ip, 'service_runs_on_ip')

            elif asset.asset_type == 'certificate':
                target_value = asset.normalized_value.replace("cn=", "").strip()
                meta_covers = asset.metadata_.get("covers")
                search_values = [target_value]
                if meta_covers:
                    search_values.append(str(meta_covers).strip().lower())

                target_assets = db.query(Assets).filter(
                    Assets.organization_id == DEFAULT_ORG_ID,
                    Assets.normalized_value.in_(search_values),
                    Assets.asset_type.in_(['domain', 'subdomain'])
                ).all()
                
                for t_asset in target_assets:
                    rel_type = 'certificate_to_domain' if t_asset.asset_type == 'domain' else 'certificate_to_subdomain'
                    _create_relationship(db, DEFAULT_ORG_ID, asset, t_asset, rel_type)

            elif asset.asset_type == 'technology':
                tech_host = asset.metadata_.get("runs_on") or asset.metadata_.get("deployed_at")
                
                for other_asset in processed_assets:
                    if other_asset.asset_type in ['subdomain', 'service']:
                        is_linked = False
                        
                        if tech_host and str(tech_host).strip().lower() == other_asset.normalized_value:
                            is_linked = True
                        
                        elif other_asset.asset_type == 'service' and other_asset.metadata_.get("banner"):
                            if asset.normalized_value in str(other_asset.metadata_.get("banner")).lower():
                                is_linked = True
                                
                        if not is_linked:
                            common_tags = set(asset.tags).intersection(set(other_asset.tags))
                            if common_tags and any(tag in ['prod', 'staging', 'dev', 'api', 'internal', 'public'] for tag in common_tags):
                                if other_asset.asset_type == 'subdomain':
                                    is_linked = True
                            
                        if is_linked:
                            rel_type = f"technology_to_{other_asset.asset_type}"
                            _create_relationship(db, DEFAULT_ORG_ID, asset, other_asset, rel_type)

            db.flush()

        except Exception as rel_error:
            db.rollback()
            print(f"Error establishing relationship for asset {asset.id}: {str(rel_error)}")
            continue

    try:
        db.commit()
    except Exception as final_err:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Final database commit failed: {str(final_err)}")
    
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
            metadata_={"biographical_context": "auto_generated"},
            first_seen=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(new_rel)
    else:
        existing_rel.last_seen = datetime.utcnow()
        existing_rel.updated_at = datetime.utcnow()