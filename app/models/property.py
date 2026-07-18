from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class PropertySchema(BaseModel):
    title: str
    address_full: str
    location: str
    price: float
    description: Optional[str] = None
    images: Optional[List[str]] = None
    features: Optional[Dict[str, Any]] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

