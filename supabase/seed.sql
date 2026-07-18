-- HousePadi Seed Data
-- Development and Testing Sample Data

-- ============================================
-- SEED PROFILES
-- ============================================

-- Sample Admin User
INSERT INTO profiles (id, email, first_name, last_name, phone_number, role, avatar_url)
VALUES (
    '00000000-0000-0000-0000-000000000001'::uuid,
    'admin@housepadi.app',
    'Admin',
    'User',
    '+234-800-0000-001',
    'admin',
    'https://api.example.com/avatars/admin.jpg'
) ON CONFLICT (email) DO NOTHING;

-- Sample Landlord Users
INSERT INTO profiles (id, email, first_name, last_name, phone_number, role, avatar_url)
VALUES 
(
    '00000000-0000-0000-0000-000000000002'::uuid,
    'landlord1@housepadi.app',
    'John',
    'Adeyemi',
    '+234-803-123-4567',
    'landlord',
    'https://api.example.com/avatars/landlord1.jpg'
),
(
    '00000000-0000-0000-0000-000000000003'::uuid,
    'landlord2@housepadi.app',
    'Sarah',
    'Okonkwo',
    '+234-705-234-5678',
    'landlord',
    'https://api.example.com/avatars/landlord2.jpg'
)
ON CONFLICT (email) DO NOTHING;

-- Sample Renter Users
INSERT INTO profiles (id, email, first_name, last_name, phone_number, role, avatar_url)
VALUES 
(
    '00000000-0000-0000-0000-000000000010'::uuid,
    'renter1@housepadi.app',
    'Chukwu',
    'Okoro',
    '+234-906-789-0123',
    'renter',
    'https://api.example.com/avatars/renter1.jpg'
),
(
    '00000000-0000-0000-0000-000000000011'::uuid,
    'renter2@housepadi.app',
    'Amara',
    'Ejiro',
    '+234-802-456-7890',
    'renter',
    'https://api.example.com/avatars/renter2.jpg'
),
(
    '00000000-0000-0000-0000-000000000012'::uuid,
    'renter3@housepadi.app',
    'Ade',
    'Ibraheem',
    '+234-907-123-4567',
    'renter',
    'https://api.example.com/avatars/renter3.jpg'
)
ON CONFLICT (email) DO NOTHING;

-- ============================================
-- SEED PROPERTIES
-- ============================================

INSERT INTO properties (
    id, owner_id, title, address_full, location, latitude, longitude,
    price, currency, description, images, features, lease_duration_months,
    is_featured, status
)
VALUES 
(
    '10000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000002'::uuid,
    '2-Bedroom Luxury Apartment in Victoria Island',
    '23 Ligali Ayorinde Street, Victoria Island, Lagos',
    'Lagos',
    6.4243,
    3.4267,
    1500000,
    'NGN',
    'Beautiful 2-bedroom apartment with modern furnishings, AC, generator, and security.',
    ARRAY['https://api.example.com/properties/prop1-img1.jpg', 'https://api.example.com/properties/prop1-img2.jpg'],
    '{"bedrooms": 2, "bathrooms": 2, "amenities": ["wifi", "ac", "generator", "security"], "furnished": true}'::jsonb,
    12,
    true,
    'published'
),
(
    '10000000-0000-0000-0000-000000000002'::uuid,
    '00000000-0000-0000-0000-000000000002'::uuid,
    '3-Bedroom Duplex in Ikoyi',
    '45 Bourdillon Road, Ikoyi, Lagos',
    'Lagos',
    6.4612,
    3.4497,
    2500000,
    'NGN',
    'Premium 3-bedroom duplex with modern kitchen, garden, and dedicated parking.',
    ARRAY['https://api.example.com/properties/prop2-img1.jpg'],
    '{"bedrooms": 3, "bathrooms": 3, "amenities": ["wifi", "ac", "generator", "security", "garden", "parking"], "furnished": true}'::jsonb,
    12,
    true,
    'published'
),
(
    '10000000-0000-0000-0000-000000000003'::uuid,
    '00000000-0000-0000-0000-000000000003'::uuid,
    '1-Bedroom Studio in Lekki',
    '12 Banana Island Road, Lekki, Lagos',
    'Lagos',
    6.4521,
    3.5515,
    800000,
    'NGN',
    'Cozy 1-bedroom studio perfect for individuals or couples.',
    ARRAY['https://api.example.com/properties/prop3-img1.jpg'],
    '{"bedrooms": 1, "bathrooms": 1, "amenities": ["wifi", "ac", "security"], "furnished": false}'::jsonb,
    12,
    false,
    'published'
),
(
    '10000000-0000-0000-0000-000000000004'::uuid,
    '00000000-0000-0000-0000-000000000003'::uuid,
    '4-Bedroom House in Abuja',
    '123 Admiralty Way, Lekki Phase 1, Lagos',
    'Abuja',
    9.0765,
    7.3986,
    3000000,
    'NGN',
    'Spacious 4-bedroom detached house with pool and gym.',
    ARRAY['https://api.example.com/properties/prop4-img1.jpg', 'https://api.example.com/properties/prop4-img2.jpg'],
    '{"bedrooms": 4, "bathrooms": 4, "amenities": ["wifi", "ac", "generator", "security", "pool", "gym"], "furnished": true}'::jsonb,
    12,
    true,
    'published'
)
ON CONFLICT DO NOTHING;

-- ============================================
-- SEED TOURS
-- ============================================

INSERT INTO tours (id, property_id, visitor_id, visitor_name, visitor_contact, tour_date, status)
VALUES 
(
    '20000000-0000-0000-0000-000000000001'::uuid,
    '10000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000010'::uuid,
    'Chukwu Okoro',
    '+234-906-789-0123',
    NOW() + INTERVAL '7 days',
    'scheduled'
),
(
    '20000000-0000-0000-0000-000000000002'::uuid,
    '10000000-0000-0000-0000-000000000002'::uuid,
    '00000000-0000-0000-0000-000000000011'::uuid,
    'Amara Ejiro',
    '+234-802-456-7890',
    NOW() + INTERVAL '14 days',
    'scheduled'
)
ON CONFLICT DO NOTHING;

-- ============================================
-- SEED KYC VERIFICATIONS
-- ============================================

INSERT INTO kyc_verifications (
    id, user_id, id_type, id_number, id_image_url, status, verification_date, verified_by
)
VALUES 
(
    '30000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000010'::uuid,
    'passport',
    'A12345678',
    'https://api.example.com/kyc/renter1-passport.jpg',
    'verified',
    NOW(),
    '00000000-0000-0000-0000-000000000001'::uuid
),
(
    '30000000-0000-0000-0000-000000000002'::uuid,
    '00000000-0000-0000-0000-000000000011'::uuid,
    'national_id',
    'N123456789012',
    'https://api.example.com/kyc/renter2-nid.jpg',
    'verified',
    NOW(),
    '00000000-0000-0000-0000-000000000001'::uuid
),
(
    '30000000-0000-0000-0000-000000000003'::uuid,
    '00000000-0000-0000-0000-000000000012'::uuid,
    'drivers_license',
    'D87654321',
    'https://api.example.com/kyc/renter3-license.jpg',
    'pending',
    NULL,
    NULL
)
ON CONFLICT DO NOTHING;

-- ============================================
-- SEED WALLETS
-- ============================================

INSERT INTO wallets (id, user_id, balance, currency)
VALUES 
(
    '40000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000002'::uuid,
    5000000,
    'NGN'
),
(
    '40000000-0000-0000-0000-000000000002'::uuid,
    '00000000-0000-0000-0000-000000000003'::uuid,
    3500000,
    'NGN'
),
(
    '40000000-0000-0000-0000-000000000010'::uuid,
    '00000000-0000-0000-0000-000000000010'::uuid,
    2000000,
    'NGN'
),
(
    '40000000-0000-0000-0000-000000000011'::uuid,
    '00000000-0000-0000-0000-000000000011'::uuid,
    1500000,
    'NGN'
),
(
    '40000000-0000-0000-0000-000000000012'::uuid,
    '00000000-0000-0000-0000-000000000012'::uuid,
    1000000,
    'NGN'
)
ON CONFLICT DO NOTHING;

-- ============================================
-- SEED APPLICATIONS
-- ============================================

INSERT INTO applications (
    id, property_id, renter_id, status, screening_summary, ai_match_score
)
VALUES 
(
    '50000000-0000-0000-0000-000000000001'::uuid,
    '10000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000010'::uuid,
    'approved',
    'Excellent credit history. Verified employment. No previous evictions.',
    92
),
(
    '50000000-0000-0000-0000-000000000002'::uuid,
    '10000000-0000-0000-0000-000000000002'::uuid,
    '00000000-0000-0000-0000-000000000011'::uuid,
    'reviewing',
    'Good credit history. Employment verification in progress.',
    78
),
(
    '50000000-0000-0000-0000-000000000003'::uuid,
    '10000000-0000-0000-0000-000000000003'::uuid,
    '00000000-0000-0000-0000-000000000012'::uuid,
    'submitted',
    NULL,
    NULL
)
ON CONFLICT DO NOTHING;

-- ============================================
-- SEED LEASES
-- ============================================

INSERT INTO leases (
    id, property_id, renter_id, owner_id, start_date, end_date,
    rent, currency, contract_url, status
)
VALUES 
(
    '60000000-0000-0000-0000-000000000001'::uuid,
    '10000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000010'::uuid,
    '00000000-0000-0000-0000-000000000002'::uuid,
    '2026-07-01'::date,
    '2027-06-30'::date,
    1500000,
    'NGN',
    'https://api.example.com/contracts/lease-001.pdf',
    'active'
)
ON CONFLICT DO NOTHING;

-- ============================================
-- SEED TRANSACTIONS
-- ============================================

INSERT INTO transactions (
    id, lease_id, payer_id, amount, platform_fee, type, currency,
    payment_gateway_ref, status
)
VALUES 
(
    '70000000-0000-0000-0000-000000000001'::uuid,
    '60000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000010'::uuid,
    1500000,
    75000,
    'rent_payment',
    'NGN',
    'PAYSTACK_TXN_001',
    'completed'
),
(
    '70000000-0000-0000-0000-000000000002'::uuid,
    '60000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000010'::uuid,
    300000,
    0,
    'deposit',
    'NGN',
    'PAYSTACK_TXN_002',
    'completed'
)
ON CONFLICT DO NOTHING;

-- ============================================
-- SEED LEDGER ENTRIES
-- ============================================

INSERT INTO ledger_entries (
    id, wallet_id, amount, type, category, reference_id, description, balance_after
)
VALUES 
(
    '80000000-0000-0000-0000-000000000001'::uuid,
    '40000000-0000-0000-0000-000000000001'::uuid,
    1500000,
    'credit',
    'rent_payment',
    '70000000-0000-0000-0000-000000000001'::uuid,
    'Rent payment received from Chukwu Okoro for Victoria Island apartment',
    6500000
),
(
    '80000000-0000-0000-0000-000000000002'::uuid,
    '40000000-0000-0000-0000-000000000001'::uuid,
    75000,
    'debit',
    'platform_fee',
    '70000000-0000-0000-0000-000000000001'::uuid,
    'Platform fee for rent payment processing',
    6425000
),
(
    '80000000-0000-0000-0000-000000000010'::uuid,
    '40000000-0000-0000-0000-000000000010'::uuid,
    1500000,
    'debit',
    'rent_payment',
    '70000000-0000-0000-0000-000000000001'::uuid,
    'Rent payment for Victoria Island apartment',
    500000
),
(
    '80000000-0000-0000-0000-000000000011'::uuid,
    '40000000-0000-0000-0000-000000000010'::uuid,
    300000,
    'debit',
    'deposit',
    '70000000-0000-0000-0000-000000000002'::uuid,
    'Security deposit for Victoria Island apartment',
    200000
)
ON CONFLICT DO NOTHING;

-- ============================================
-- SEED CHAT THREADS
-- ============================================

INSERT INTO chat_threads (
    id, renter_id, owner_id, property_id, subject, status
)
VALUES 
(
    '90000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000010'::uuid,
    '00000000-0000-0000-0000-000000000002'::uuid,
    '10000000-0000-0000-0000-000000000001'::uuid,
    'Questions about Victoria Island Apartment',
    'active'
),
(
    '90000000-0000-0000-0000-000000000002'::uuid,
    '00000000-0000-0000-0000-000000000011'::uuid,
    '00000000-0000-0000-0000-000000000002'::uuid,
    '10000000-0000-0000-0000-000000000002'::uuid,
    'Ikoyi Duplex Inquiry',
    'active'
)
ON CONFLICT DO NOTHING;

-- ============================================
-- SEED MESSAGES
-- ============================================

INSERT INTO messages (id, thread_id, sender_id, content, is_ai_response)
VALUES 
(
    'a0000000-0000-0000-0000-000000000001'::uuid,
    '90000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000010'::uuid,
    'Hi, I am interested in this apartment. Can you tell me more about the lease terms?',
    false
),
(
    'a0000000-0000-0000-0000-000000000002'::uuid,
    '90000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000002'::uuid,
    'Hello Chukwu! The lease is for 12 months starting from July 1st, 2026. Monthly rent is ₦1.5M with utilities included.',
    false
),
(
    'a0000000-0000-0000-0000-000000000003'::uuid,
    '90000000-0000-0000-0000-000000000001'::uuid,
    '00000000-0000-0000-0000-000000000010'::uuid,
    'That sounds great! Is the apartment available for viewing this week?',
    false
),
(
    'a0000000-0000-0000-0000-000000000004'::uuid,
    '90000000-0000-0000-0000-000000000002'::uuid,
    '00000000-0000-0000-0000-000000000011'::uuid,
    'Can I schedule a tour for the Ikoyi property?',
    false
),
(
    'a0000000-0000-0000-0000-000000000005'::uuid,
    '90000000-0000-0000-0000-000000000002'::uuid,
    '00000000-0000-0000-0000-000000000002'::uuid,
    'Yes, Amara! I have availability on weekends. Which date works best for you?',
    false
)
ON CONFLICT DO NOTHING;

-- ============================================
-- VERIFY DATA
-- ============================================

SELECT '✓ Profiles seeded' as status, COUNT(*) as count FROM profiles;
SELECT '✓ Properties seeded' as status, COUNT(*) as count FROM properties;
SELECT '✓ Tours seeded' as status, COUNT(*) as count FROM tours;
SELECT '✓ KYC Verifications seeded' as status, COUNT(*) as count FROM kyc_verifications;
SELECT '✓ Wallets seeded' as status, COUNT(*) as count FROM wallets;
SELECT '✓ Applications seeded' as status, COUNT(*) as count FROM applications;
SELECT '✓ Leases seeded' as status, COUNT(*) as count FROM leases;
SELECT '✓ Transactions seeded' as status, COUNT(*) as count FROM transactions;
SELECT '✓ Ledger Entries seeded' as status, COUNT(*) as count FROM ledger_entries;
SELECT '✓ Chat Threads seeded' as status, COUNT(*) as count FROM chat_threads;
SELECT '✓ Messages seeded' as status, COUNT(*) as count FROM messages;
