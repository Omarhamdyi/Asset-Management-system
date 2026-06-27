from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any

class AssetIngestSchema(BaseModel):
    id: Optional[str] = None  
    type: str 
    value: str
    status: Optional[str] = "active"
    source: Optional[str] = "import"
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    parent: Optional[str] = None
    covers: Optional[str] = None

    @field_validator('type', 'status', 'source', mode='before')
    @classmethod
    def clean_and_lower_strings(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator('value', mode='before')
    @classmethod
    def clean_value(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v