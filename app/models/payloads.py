from pydantic import BaseModel, Field, EmailStr
from typing import Dict, Any, List, Optional
from uuid import UUID
from datetime import datetime, date
from enum import Enum


# ============================================
# ENUM DEFINITIONS (matching schema)
# ============================================

class KYCStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class PropertyStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RENTED = "rented"
    DELISTED = "delisted"


class TransactionType(str, Enum):
    RENT_PAYMENT = "rent_payment"
    DEPOSIT = "deposit"
    REFUND = "refund"
    PLATFORM_FEE = "platform_fee"


class KYCVerificationStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class ApplicationStatus(str, Enum):
    SUBMITTED = "submitted"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    REJECTED = "rejected"


# ============================================
# PROFILE SCHEMA
# ============================================

class ProfilePayload(BaseModel):
    """User profile creation/update payload"""
    email: EmailStr = Field(..., description="User email address")
    first_name: str = Field(..., description="User first name")
    last_name: str = Field(..., description="User last name")
    phone_number: Optional[str] = Field(None, description="User phone number")
    role: str = Field("user", description="User role: 'landlord', 'renter', or 'admin'")
    avatar_url: Optional[str] = Field(None, description="URL to user avatar image")


# ============================================
# PROPERTY SCHEMA (UPDATED)
# ============================================

class PropertyCoordinates(BaseModel):
    """Geographic coordinates for property"""
    latitude: float = Field(..., description="Property latitude")
    longitude: float = Field(..., description="Property longitude")


class PropertyPayload(BaseModel):
    """Property creation/update payload - ALIGNED WITH NEW SCHEMA"""
    title: str = Field(..., description="Property title/name")
    address_full: str = Field(..., description="Full street address")
    location: str = Field(..., description="City or region (e.g., 'Abuja')")
    price: float = Field(..., gt=0, description="Monthly rent price")
    currency: str = Field("USD", description="Currency code")
    description: Optional[str] = Field(None, description="Property description")
    
    # Geographic coordinates (required for directions/maps)
    coords: Optional[PropertyCoordinates] = Field(None, description="Latitude/Longitude")
    
    # Images and features
    images: List[str] = Field(default_factory=list, description="Array of image URLs")
    features: Dict[str, Any] = Field(default_factory=dict, description="Amenities and features")
    
    # Lease terms
    lease_duration_months: int = Field(12, description="Default lease duration in months")
    
    # Agreement
    agreement_content: Optional[str] = Field(None, description="Lease agreement text/template")
    
    # Additional metadata
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional property metadata")
    is_featured: bool = Field(False, description="Whether property is featured")
    status: PropertyStatus = Field(PropertyStatus.DRAFT, description="Property status")


# ============================================
# TOUR SCHEMA (UPDATED)
# ============================================

class TourPayload(BaseModel):
    """Tour scheduling payload"""
    property_id: str = Field(..., description="UUID of the property")
    tour_date: str = Field(..., description="ISO timestamp format (YYYY-MM-DD HH:MM:SS)")
    visitor_name: str = Field(..., min_length=2, description="Name of person touring")
    visitor_contact: str = Field(..., description="Contact phone or email")


# ============================================
# LEASE SCHEMA (UPDATED)
# ============================================

class LeasePayload(BaseModel):
    """Lease agreement payload"""
    property_id: UUID = Field(..., description="Property UUID")
    renter_id: UUID = Field(..., description="Renter UUID")
    owner_id: UUID = Field(..., description="Owner/Landlord UUID")
    start_date: date = Field(..., description="Lease start date")
    rent: float = Field(..., gt=0, description="Monthly rent amount")
    contract_url: Optional[str] = Field(None, description="URL to signed lease PDF")


# ============================================
# APPLICATION SCHEMA (NEW)
# ============================================

class ApplicationPayload(BaseModel):
    """Rental application payload"""
    property_id: UUID = Field(..., description="Property UUID")
    renter_id: UUID = Field(..., description="Renter UUID")
    status: ApplicationStatus = Field(ApplicationStatus.SUBMITTED, description="Application status")
    screening_summary: Optional[str] = Field(None, description="AI screening summary")
    ai_match_score: Optional[int] = Field(None, ge=0, le=100, description="AI matching score 0-100")


# ============================================
# TRANSACTION SCHEMA (NEW)
# ============================================

class TransactionPayload(BaseModel):
    """Payment transaction payload"""
    lease_id: UUID = Field(..., description="Associated lease UUID")
    payer_id: UUID = Field(..., description="UUID of person making payment")
    amount: float = Field(..., gt=0, description="Payment amount")
    platform_fee: float = Field(0, ge=0, description="Platform fee amount")
    type: TransactionType = Field(TransactionType.RENT_PAYMENT, description="Transaction type")
    currency: str = Field("USD", description="Currency code")
    payment_gateway_ref: str = Field(..., description="Payment gateway reference ID")
    status: str = Field("pending", description="Transaction status")


# ============================================
# KYC VERIFICATION SCHEMA (NEW)
# ============================================

class KYCVerificationPayload(BaseModel):
    """KYC verification payload"""
    user_id: UUID = Field(..., description="User UUID")
    id_type: str = Field(..., description="Type of ID (passport, license, etc)")
    id_number: str = Field(..., description="ID number")
    id_image_url: str = Field(..., description="URL to ID image")
    status: KYCVerificationStatus = Field(KYCVerificationStatus.PENDING, description="Verification status")


# ============================================
# WALLET/LEDGER SCHEMA (NEW)
# ============================================

class WalletPayload(BaseModel):
    """User wallet payload"""
    userId: UUID = Field(..., description="User UUID")
    balance: float = Field(0, description="Wallet balance")


class LedgerEntryPayload(BaseModel):
    """Ledger entry payload"""
    walletId: str = Field(..., description="Wallet ID")
    amount: float = Field(..., description="Transaction amount")
    type: str = Field(..., description="Entry type (debit/credit)")
    category: str = Field(..., description="Transaction category")
    referenceId: Optional[str] = Field(None, description="Reference ID")


# ============================================
# CHAT SCHEMA (NEW)
# ============================================

class ChatThreadPayload(BaseModel):
    """Chat thread creation payload"""
    renter_id: Optional[UUID] = Field(None, description="Renter UUID")
    owner_id: Optional[UUID] = Field(None, description="Owner UUID")
    property_id: Optional[UUID] = Field(None, description="Property UUID if property-specific")


class MessagePayload(BaseModel):
    """Message payload"""
    thread_id: UUID = Field(..., description="Chat thread UUID")
    sender_id: UUID = Field(..., description="Sender UUID")
    content: str = Field(..., description="Message content")
    is_ai_response: bool = Field(False, description="Whether this is an AI response")


# ============================================
# BANK DETAILS SCHEMA (NEW)
# ============================================

class BankDetailsPayload(BaseModel):
    """Bank account details for payments"""
    user_id: UUID = Field(..., description="User UUID")
    bank_name: str = Field(..., description="Bank name")
    bank_code: str = Field(..., description="Bank code")
    account_number: str = Field(..., description="Account number")
    account_name: str = Field(..., description="Account holder name")
    recipient_code: Optional[str] = Field(None, description="Payment provider recipient code")

