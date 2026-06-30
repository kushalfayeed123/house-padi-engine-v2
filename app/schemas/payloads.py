from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal
from uuid import UUID
from datetime import datetime


class PropertyPayload(BaseModel):
    address: str = Field(..., min_length=6, description="Full real estate location address")
    base_price: float = Field(..., gt=0, description="Monthly rental subscription baseline")
    specs: Dict[str, Any] = Field(default_factory=dict, description="Structural and amenities descriptors")


class TourPayload(BaseModel):
    property_id: UUID
    visitor_name: str = Field(..., min_length=2)
    visitor_contact: str
    tour_date: datetime


class LeasePayload(BaseModel):
    property_id: UUID
    renter_id: UUID
    lease_terms: Dict[str, Any]
