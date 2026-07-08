from pydantic import BaseModel, Field
from typing import Dict, Any
from uuid import UUID
from datetime import datetime


class PropertyPayload(BaseModel):
    address: str = Field(..., min_length=6, description="Full real estate location address")
    base_price: float = Field(..., gt=0, description="Monthly rental subscription baseline")
    specs: Dict[str, Any] = Field(default_factory=dict, description="Structural and amenities descriptors")
    location: str = Field(..., description="The city or district (e.g., 'Abuja')")


class TourPayload(BaseModel):
    property_id: str
    visitor_name: str = Field(..., min_length=2)
    visitor_contact: str
    tour_date: str


class LeasePayload(BaseModel):
    property_id: UUID
    renter_id: UUID
    lease_terms: Dict[str, Any]
