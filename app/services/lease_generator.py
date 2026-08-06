"""
Lease Document Generator Service
Generates standard base agreements and finalizes executed contracts.
"""

from datetime import datetime
from typing import Dict, Any

def generate_base_lease_template(property_data: Dict[str, Any], owner_name: str) -> str:
    """Generates the base lease agreement string stored on properties.agreement_content during property creation."""
    title = property_data.get("title", "Residential Unit")
    address = property_data.get("address_full", "N/A")
    rent = property_data.get("price", 0)
    duration = property_data.get("lease_duration_months", 12)
    currency = property_data.get("currency", "USD")

    template = f"""
================================================================================
                         RESIDENTIAL LEASE AGREEMENT
================================================================================

1. PARTIES
   Landlord: {owner_name}
   Tenant: {{RENTER_NAME}} (To be bound upon confirmation of payment)

2. PREMISES
   Property: {title}
   Address: {address}

3. TERM & RENT
   Lease Duration: {duration} Months
   Start Date: {{START_DATE}}
   Monthly Rent Amount: {currency} {rent:,.2f}

4. TERMS AND CONDITIONS
   - The Tenant agrees to pay rent via the HousePadi Platform.
   - The Landlord agrees to provide access and key handover upon full initial payment verification.
   - Both parties agree to abide by local residential tenancy regulations.

5. EXECUTION & SIGNATURES

   Landlord Signature:
   [ {{LANDLORD_SIGNATURE}} ]
   Date: {{LANDLORD_SIGNED_DATE}}

   Tenant Signature:
   [ {{RENTER_SIGNATURE}} ]
   Date: {{RENTER_SIGNED_DATE}}
================================================================================
"""
    return template.strip()


def finalize_executed_lease(
    base_agreement: str,
    renter_name: str,
    renter_signature: str,
    landlord_signature: str,
    start_date: str,
    landlord_signed_at: str,
    renter_signed_at: str
) -> str:
    """Fills in all placeholders in the base agreement upon payment completion."""
    final_contract = base_agreement.replace("{{RENTER_NAME}}", renter_name)
    final_contract = final_contract.replace("{{START_DATE}}", start_date)
    final_contract = final_contract.replace("{{LANDLORD_SIGNATURE}}", landlord_signature)
    final_contract = final_contract.replace("{{LANDLORD_SIGNED_DATE}}", landlord_signed_at)
    final_contract = final_contract.replace("{{RENTER_SIGNATURE}}", renter_signature)
    final_contract = final_contract.replace("{{RENTER_SIGNED_DATE}}", renter_signed_at)
    
    return final_contract