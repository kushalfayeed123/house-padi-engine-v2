INTENT_PROTOTYPES = {
    "TRIGGER_PROPERTY_UI": ["create a new property", "list my apartment", "publish a listing", "add a new house"],
    "TRIGGER_DASHBOARD_UI": ["view my tours", "see my applications", "view my properties", "my dashboard", "check my listings", "my bookings", "view tours"],
    "property-specialist": ["search for houses", "find an apartment", "show me listings"],
    "tour-specialist": ["book a tour", "schedule a visit", "see the place"],
    "lease-specialist": ["sign lease agreement", "view contract terms", "update lease terms", "sign my lease", "view my active lease"],
    "payment-specialist": ["pay rent", "transfer money", "check wallet balance"],
    "kyc-specialist": ["verify identity", "upload id", "kyc status"],
    "chat-specialist": ["message landlord", "contact owner", "start a chat"],
    "supervisor": [
        "how does this work",
        "can you explain how a lease works",
        "what are the rules here",
        "tell me about the platform",
        "hello",
        "what can you do",
    ],
}

INTENT_UI_MAP = {
    "TRIGGER_PROPERTY_UI": "/dashboard/landlord/create-property",
    "TRIGGER_DASHBOARD_UI": "/dashboard",
    "TRIGGER_PAYMENT_UI": "/payments",
    "TRIGGER_KYC_UI": "/profile/verify",
}

INTENT_CONTENT_MAP = {
    "TRIGGER_PROPERTY_UI": "Opening the property listing form for you...",
    "TRIGGER_DASHBOARD_UI": "Opening your dashboard...",
    "TRIGGER_PAYMENT_UI": "Taking you to your payments page...",
    "TRIGGER_KYC_UI": "Opening identity verification..."
}