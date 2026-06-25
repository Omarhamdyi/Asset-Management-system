from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class AssetIngestSchema(BaseModel):
    id: str
    type: str 
    value: str
    status: Optional[str] = "active"
    source: Optional[str] = "import"
    tags: Optional[List[str]] = []
    metadata: Optional[Dict[str, Any]] = {}
    parent: Optional[str] = None
    covers: Optional[str] = None