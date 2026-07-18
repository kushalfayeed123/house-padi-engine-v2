-- HousePadi Database Schema Migration
-- Created: 2026-07-15
-- Description: Initial schema for HousePadi real estate platform

-- ============================================
-- ENUM TYPES
-- ============================================

CREATE TYPE kyc_status AS ENUM ('pending', 'verified', 'rejected');
CREATE TYPE property_status AS ENUM ('draft', 'published', 'rented', 'delisted');
CREATE TYPE transaction_type AS ENUM ('rent_payment', 'deposit', 'refund', 'platform_fee');
CREATE TYPE application_status AS ENUM ('submitted', 'reviewing', 'approved', 'rejected');
CREATE TYPE user_role AS ENUM ('user', 'landlord', 'renter', 'admin');
CREATE TYPE transaction_status AS ENUM ('pending', 'completed', 'failed', 'cancelled');


-- ============================================
-- PROFILES TABLE (Users)
-- ============================================

CREATE TABLE profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    phone_number VARCHAR(20),
    role user_role DEFAULT 'user',
    avatar_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    deleted_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_profiles_email ON profiles(email);
CREATE INDEX idx_profiles_role ON profiles(role);
CREATE INDEX idx_profiles_created_at ON profiles(created_at);


-- ============================================
-- PROPERTIES TABLE
-- ============================================

CREATE TABLE properties (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    address_full TEXT NOT NULL,
    location VARCHAR(100) NOT NULL,
    latitude DECIMAL(10, 8),
    longitude DECIMAL(11, 8),
    price NUMERIC(12, 2) NOT NULL CHECK (price > 0),
    currency VARCHAR(3) DEFAULT 'USD',
    description TEXT,
    images TEXT[] DEFAULT ARRAY[]::TEXT[],
    features JSONB DEFAULT '{}'::JSONB,
    lease_duration_months INTEGER DEFAULT 12,
    agreement_content TEXT,
    metadata JSONB DEFAULT '{}'::JSONB,
    is_featured BOOLEAN DEFAULT FALSE,
    status property_status DEFAULT 'draft',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    deleted_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_properties_owner_id ON properties(owner_id);
CREATE INDEX idx_properties_location ON properties(location);
CREATE INDEX idx_properties_status ON properties(status);
CREATE INDEX idx_properties_is_featured ON properties(is_featured);
CREATE INDEX idx_properties_price ON properties(price);
CREATE INDEX idx_properties_created_at ON properties(created_at);
CREATE INDEX idx_properties_coordinates ON properties(latitude, longitude);


-- ============================================
-- TOURS TABLE
-- ============================================

CREATE TABLE tours (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    visitor_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
    visitor_name VARCHAR(255) NOT NULL,
    visitor_contact VARCHAR(255) NOT NULL,
    tour_date TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(50) DEFAULT 'scheduled',
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_tours_property_id ON tours(property_id);
CREATE INDEX idx_tours_visitor_id ON tours(visitor_id);
CREATE INDEX idx_tours_tour_date ON tours(tour_date);
CREATE INDEX idx_tours_status ON tours(status);


-- ============================================
-- APPLICATIONS TABLE
-- ============================================

CREATE TABLE applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    renter_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    status application_status DEFAULT 'submitted',
    screening_summary TEXT,
    ai_match_score INTEGER CHECK (ai_match_score >= 0 AND ai_match_score <= 100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_applications_property_id ON applications(property_id);
CREATE INDEX idx_applications_renter_id ON applications(renter_id);
CREATE INDEX idx_applications_status ON applications(status);
CREATE INDEX idx_applications_created_at ON applications(created_at);


-- ============================================
-- LEASES TABLE
-- ============================================

CREATE TABLE leases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    renter_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    owner_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    start_date DATE NOT NULL,
    end_date DATE,
    rent NUMERIC(12, 2) NOT NULL CHECK (rent > 0),
    currency VARCHAR(3) DEFAULT 'USD',
    contract_url TEXT,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_leases_property_id ON leases(property_id);
CREATE INDEX idx_leases_renter_id ON leases(renter_id);
CREATE INDEX idx_leases_owner_id ON leases(owner_id);
CREATE INDEX idx_leases_status ON leases(status);
CREATE INDEX idx_leases_start_date ON leases(start_date);


-- ============================================
-- TRANSACTIONS TABLE
-- ============================================

CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id UUID NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    payer_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    platform_fee NUMERIC(12, 2) DEFAULT 0 CHECK (platform_fee >= 0),
    type transaction_type DEFAULT 'rent_payment',
    currency VARCHAR(3) DEFAULT 'USD',
    payment_gateway_ref VARCHAR(255) NOT NULL UNIQUE,
    status transaction_status DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_transactions_lease_id ON transactions(lease_id);
CREATE INDEX idx_transactions_payer_id ON transactions(payer_id);
CREATE INDEX idx_transactions_status ON transactions(status);
CREATE INDEX idx_transactions_type ON transactions(type);
CREATE INDEX idx_transactions_created_at ON transactions(created_at);
CREATE INDEX idx_transactions_payment_gateway_ref ON transactions(payment_gateway_ref);


-- ============================================
-- KYC VERIFICATION TABLE
-- ============================================

CREATE TABLE kyc_verifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    id_type VARCHAR(50) NOT NULL,
    id_number VARCHAR(100) NOT NULL,
    id_image_url TEXT NOT NULL,
    status kyc_status DEFAULT 'pending',
    verification_date TIMESTAMP WITH TIME ZONE,
    verified_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
    rejection_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_kyc_verifications_user_id ON kyc_verifications(user_id);
CREATE INDEX idx_kyc_verifications_status ON kyc_verifications(status);
CREATE INDEX idx_kyc_verifications_created_at ON kyc_verifications(created_at);
CREATE UNIQUE INDEX idx_kyc_verifications_user_latest ON kyc_verifications(user_id DESC, created_at DESC);


-- ============================================
-- WALLETS TABLE
-- ============================================

CREATE TABLE wallets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES profiles(id) ON DELETE CASCADE,
    balance NUMERIC(15, 2) DEFAULT 0 CHECK (balance >= 0),
    currency VARCHAR(3) DEFAULT 'USD',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_wallets_user_id ON wallets(user_id);


-- ============================================
-- LEDGER ENTRIES TABLE
-- ============================================

CREATE TABLE ledger_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_id UUID NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
    amount NUMERIC(15, 2) NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('debit', 'credit')),
    category VARCHAR(100) NOT NULL,
    reference_id UUID,
    description TEXT,
    balance_after NUMERIC(15, 2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_ledger_entries_wallet_id ON ledger_entries(wallet_id);
CREATE INDEX idx_ledger_entries_type ON ledger_entries(type);
CREATE INDEX idx_ledger_entries_category ON ledger_entries(category);
CREATE INDEX idx_ledger_entries_created_at ON ledger_entries(created_at);


-- ============================================
-- CHAT THREADS TABLE
-- ============================================

CREATE TABLE chat_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    renter_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
    owner_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
    property_id UUID REFERENCES properties(id) ON DELETE SET NULL,
    subject VARCHAR(255),
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_chat_threads_renter_id ON chat_threads(renter_id);
CREATE INDEX idx_chat_threads_owner_id ON chat_threads(owner_id);
CREATE INDEX idx_chat_threads_property_id ON chat_threads(property_id);
CREATE INDEX idx_chat_threads_status ON chat_threads(status);
CREATE INDEX idx_chat_threads_created_at ON chat_threads(created_at);


-- ============================================
-- MESSAGES TABLE
-- ============================================

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id UUID NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    sender_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    is_ai_response BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_messages_thread_id ON messages(thread_id);
CREATE INDEX idx_messages_sender_id ON messages(sender_id);
CREATE INDEX idx_messages_is_ai_response ON messages(is_ai_response);
CREATE INDEX idx_messages_created_at ON messages(created_at);
CREATE INDEX idx_messages_thread_created ON messages(thread_id, created_at);


-- ============================================
-- TRIGGERS FOR updated_at TIMESTAMPS
-- ============================================

-- Function to update updated_at column
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers for all tables with updated_at
CREATE TRIGGER update_profiles_updated_at BEFORE UPDATE ON profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_properties_updated_at BEFORE UPDATE ON properties
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_tours_updated_at BEFORE UPDATE ON tours
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_applications_updated_at BEFORE UPDATE ON applications
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_leases_updated_at BEFORE UPDATE ON leases
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_transactions_updated_at BEFORE UPDATE ON transactions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_kyc_verifications_updated_at BEFORE UPDATE ON kyc_verifications
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_wallets_updated_at BEFORE UPDATE ON wallets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_chat_threads_updated_at BEFORE UPDATE ON chat_threads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_messages_updated_at BEFORE UPDATE ON messages
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- ============================================
-- ROW LEVEL SECURITY POLICIES (Optional)
-- ============================================

-- Enable RLS on all tables
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE properties ENABLE ROW LEVEL SECURITY;
ALTER TABLE tours ENABLE ROW LEVEL SECURITY;
ALTER TABLE applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_verifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;

-- Profile: Users can only view their own profile (relaxed for now - apply restrictions as needed)
-- Property: Anyone can view published properties, only owners can modify
-- Wallet: Only the owner can view/modify their wallet
-- Messages: Participants of the thread can view messages

-- ============================================
-- MATERIALIZED VIEWS (Optional - for analytics)
-- ============================================

-- Active leases view
CREATE MATERIALIZED VIEW active_leases_view AS
SELECT 
    l.id,
    l.property_id,
    p.title as property_title,
    l.renter_id,
    r.email as renter_email,
    l.owner_id,
    o.email as owner_email,
    l.rent,
    l.start_date,
    l.end_date,
    l.status,
    l.created_at
FROM leases l
JOIN properties p ON l.property_id = p.id
JOIN profiles r ON l.renter_id = r.id
JOIN profiles o ON l.owner_id = o.id
WHERE l.status = 'active';

CREATE INDEX idx_active_leases_view_property_id ON active_leases_view(property_id);
CREATE INDEX idx_active_leases_view_renter_id ON active_leases_view(renter_id);
CREATE INDEX idx_active_leases_view_owner_id ON active_leases_view(owner_id);


-- Property statistics view
CREATE MATERIALIZED VIEW property_statistics_view AS
SELECT 
    p.id,
    p.title,
    p.location,
    p.price,
    COUNT(DISTINCT t.id) as tour_count,
    COUNT(DISTINCT a.id) as application_count,
    COUNT(DISTINCT l.id) as active_lease_count
FROM properties p
LEFT JOIN tours t ON p.id = t.property_id AND t.status = 'scheduled'
LEFT JOIN applications a ON p.id = a.property_id AND a.status IN ('submitted', 'reviewing')
LEFT JOIN leases l ON p.id = l.property_id AND l.status = 'active'
WHERE p.deleted_at IS NULL
GROUP BY p.id, p.title, p.location, p.price;

CREATE INDEX idx_property_statistics_view_location ON property_statistics_view(location);
