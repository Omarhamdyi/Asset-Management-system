import json
import uuid
from sqlalchemy.orm import Session
from app.models import Assets
from app.services.ai_service import classify_and_enrich_asset_ai
from app.database import get_db

def run_automated_enrichment_pipeline(asset_id: str):
    """
    Automated enrichment pipeline for a newly imported asset.
    1. Opens an isolated DB session for the background task.
    2. Fetches the raw asset from DB.
    3. Calls AI to classify environment, category, and criticality.
    4. Updates the asset in the database.
    """
    db_gen = get_db()
    db: Session = next(db_gen)
    
    try:
        asset = db.query(Assets).filter(Assets.id == asset_id).first()
        if not asset:
            print(f"[-] Asset {asset_id} not found for enrichment.")
            return

        asset_data = {
            "asset_type": asset.asset_type,
            "value": asset.value,
            "metadata": asset.metadata_ or {}
        }

        ai_analysis = classify_and_enrich_asset_ai(asset_data)

        if ai_analysis.get("environment"):
            asset.environment = ai_analysis["environment"].lower()
            
        new_tags = []
        if ai_analysis.get("category"):
            new_tags.append(f"category:{ai_analysis['category'].lower()}")
        if ai_analysis.get("criticality"):
            new_tags.append(f"criticality:{ai_analysis['criticality'].lower()}")
            if ai_analysis["criticality"].lower() == "critical":
                new_tags.append("critical")

        existing_tags = asset.tags if asset.tags else []
        asset.tags = list(set(existing_tags + new_tags))

        existing_metadata = asset.metadata_ if asset.metadata_ else {}
        
        if ai_analysis.get("inferred_tech_stack"):
            existing_metadata["ai_detected_tech"] = ai_analysis["inferred_tech_stack"]
        elif ai_analysis.get("enriched_metadata"): 
            existing_metadata["ai_detected_tech"] = ai_analysis["enriched_metadata"]
            
        asset.metadata_ = existing_metadata

        db.commit()
        print(f"[+] Asset {asset.value} successfully enriched by AI! 🚀")

    except Exception as e:
        db.rollback()
        print(f"[-] Error in automated enrichment pipeline for asset {asset_id}: {str(e)}")
        
    finally:
        db.close()