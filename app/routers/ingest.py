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

                # FIX 3: Correct status update logic.
                # Only promote stale→active when incoming is active.
                # Only downgrade active→stale/archived when explicitly told to.
                # Never silently overwrite with a lower-priority status.
                if status_clean == 'active' and existing_asset.status == 'stale':
                    existing_asset.status = 'active'
                elif status_clean in ('stale', 'archived'):
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

                # FIX 8: Never touch first_seen on an existing asset — set once, never updated.
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

    # FIX 2: Remove the redundant bare flush block that rolled back and then
    # fell through into the relationship loop with a broken session state.
    # The per-asset db.flush() inside the loop above is sufficient.

    for asset in processed_assets:
        # FIX 1: Use a savepoint (begin_nested) so that a relationship failure
        # only rolls back that one edge, not the entire batch of assets.
        savepoint = db.begin_nested()
        try:
            if asset.asset_type == 'subdomain':
                parts = asset.normalized_value.split('.')

                # FIX 4: Use parts[1:] to get the immediate parent domain,
                # not parts[-2:] which always collapses to the root domain.
                if len(parts) > 2:
                    parent_domain_value = ".".join(parts[1:])

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
                        _create_relationship(db, DEFAULT_ORG_ID, asset, target_ip, 'subdomain_to_ip_address')
                        _create_relationship(db, DEFAULT_ORG_ID, target_ip, asset, 'ip_address_to_subdomain')

            elif asset.asset_type == 'ip_address':
                linked_subdomain = (
                    asset.metadata_.get("resolved_subdomain")
                    or asset.metadata_.get("domain")
                    or asset.metadata_.get("hostname")
                )
                if linked_subdomain:
                    target_sub = db.query(Assets).filter(
                        Assets.organization_id == DEFAULT_ORG_ID,
                        Assets.asset_type == 'subdomain',
                        Assets.normalized_value == str(linked_subdomain).strip().lower()
                    ).first()
                    if target_sub:
                        _create_relationship(db, DEFAULT_ORG_ID, target_sub, asset, 'subdomain_to_ip_address')
                        _create_relationship(db, DEFAULT_ORG_ID, asset, target_sub, 'ip_address_to_subdomain')

            elif asset.asset_type == 'service':
                ip_value = (
                    asset.metadata_.get("runs_on")
                    or asset.metadata_.get("ip")
                    or asset.metadata_.get("ip_address")
                )
                if ip_value:
                    target_ip = db.query(Assets).filter(
                        Assets.organization_id == DEFAULT_ORG_ID,
                        Assets.asset_type == 'ip_address',
                        Assets.normalized_value == str(ip_value).strip().lower()
                    ).first()
                    if target_ip:
                        _create_relationship(db, DEFAULT_ORG_ID, asset, target_ip, 'service_to_ip_address')

            elif asset.asset_type == 'certificate':
                # FIX 5: Don't try to strip "cn=" from normalized_value — the CN
                # should be read from metadata directly, matching how it's stored.
                cn_value = asset.metadata_.get("common_name", "").strip().lower()
                meta_covers = asset.metadata_.get("covers")

                search_values = [v for v in [cn_value, str(meta_covers).strip().lower() if meta_covers else None] if v]

                if search_values:
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

                        # Link by explicit host reference — most reliable signal.
                        if tech_host and str(tech_host).strip().lower() == other_asset.normalized_value:
                            is_linked = True

                        # Link by banner mention — concrete technical signal.
                        elif other_asset.asset_type == 'service' and other_asset.metadata_.get("banner"):
                            if asset.normalized_value in str(other_asset.metadata_.get("banner")).lower():
                                is_linked = True

                        # FIX 6: Removed the tag-based fallback. Generic env tags like
                        # "prod" or "staging" are shared by almost every asset and produce
                        # thousands of false relationship edges. Only explicit metadata
                        # pointers or banner mentions are reliable enough to link on.

                        if is_linked:
                            rel_type = f"technology_to_{other_asset.asset_type}"
                            _create_relationship(db, DEFAULT_ORG_ID, asset, other_asset, rel_type)

            savepoint.commit()

        except Exception as rel_error:
            # FIX 1: Roll back only the savepoint, preserving all other flushed assets.
            savepoint.rollback()
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


def _create_relationship(
    db: Session,
    org_id: uuid.UUID,
    source_asset: Assets,
    target_asset: Assets,
    rel_type: str
):
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