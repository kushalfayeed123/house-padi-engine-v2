-- HousePadi Database Schema Migration
-- Aligned with Live Database Inspection Schema

-- ============================================
-- EXTENSIONS
-- ============================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ============================================
-- ENUM TYPES
-- ============================================

CREATE TYPE public.profiles_kyc_status_enum AS ENUM ('pending', 'verified', 'rejected');
CREATE TYPE public.properties_status_enum AS ENUM ('draft', 'published', 'rented', 'delisted');
CREATE TYPE public.transactions_type_enum AS ENUM ('rent_payment', 'deposit', 'refund', 'platform_fee');
CREATE TYPE public.kyc_verifications_status_enum AS ENUM ('pending', 'verified', 'rejected');
CREATE TYPE public.ledger_entry_type_enum AS ENUM ('debit', 'credit');
CREATE TYPE public.ledger_entry_category_enum AS ENUM ('rent', 'deposit', 'withdrawal', 'fee');

-- ============================================
-- PROFILES TABLE
-- ============================================

CREATE TABLE public.profiles (
    id UUID NOT NULL,
    kyc_status public.profiles_kyc_status_enum NOT NULL DEFAULT 'pending'::public.profiles_kyc_status_enum,
    email VARCHAR NOT NULL UNIQUE,
    first_name VARCHAR,
    last_name VARCHAR,
    avatar_url VARCHAR,
    role VARCHAR NOT NULL DEFAULT 'user'::VARCHAR,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    phone_number VARCHAR,
    CONSTRAINT profiles_pkey PRIMARY KEY (id)
);

-- ============================================
-- PROPERTIES TABLE
-- ============================================

CREATE TABLE public.properties (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL,
    description TEXT,
    price NUMERIC NOT NULL,
    currency VARCHAR NOT NULL DEFAULT 'USD'::VARCHAR,
    images TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
    features JSONB NOT NULL DEFAULT '{}'::JSONB,
    status public.properties_status_enum NOT NULL DEFAULT 'draft'::public.properties_status_enum,
    metadata JSONB,
    is_featured BOOLEAN NOT NULL DEFAULT FALSE,
    coords JSONB,
    lease_duration_months INTEGER NOT NULL DEFAULT 12,
    agreement_content TEXT,
    title VARCHAR NOT NULL,
    address_full VARCHAR NOT NULL,
    location VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    embedding vector(1536),
    "aiSummary" TEXT,
    "deletedAt" TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT properties_pkey PRIMARY KEY (id),
    CONSTRAINT FK_797b76e2d11a5bf755127d1aa67 FOREIGN KEY (owner_id) REFERENCES public.profiles(id)
);

-- ============================================
-- TOURS TABLE
-- ============================================

CREATE TABLE public.tours (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL,
    visitor_id UUID NOT NULL,
    visitor_name TEXT,
    visitor_contact TEXT,
    visitor_email TEXT,
    tour_date TIMESTAMP WITH TIME ZONE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_approval'::TEXT,
    directions_link TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT tours_pkey PRIMARY KEY (id),
    CONSTRAINT tours_property_id_fkey FOREIGN KEY (property_id) REFERENCES public.properties(id),
    CONSTRAINT tours_visitor_id_fkey FOREIGN KEY (visitor_id) REFERENCES auth.users(id)
);

-- ============================================
-- APPLICATIONS TABLE
-- ============================================

CREATE TABLE public.applications (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL,
    renter_id UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted'::TEXT,
    ai_match_score INTEGER,
    screening_summary TEXT,
    applied_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    lease_id VARCHAR,
    contract_url VARCHAR,
    CONSTRAINT applications_pkey PRIMARY KEY (id),
    CONSTRAINT FK_782e944003aa91e3f934089e01e FOREIGN KEY (property_id) REFERENCES public.properties(id),
    CONSTRAINT FK_63f747ac503d0c0ee55a3dc8404 FOREIGN KEY (renter_id) REFERENCES public.profiles(id)
);

-- ============================================
-- LEASES TABLE
-- ============================================

CREATE TABLE public.leases (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    renter_id UUID NOT NULL,
    start_date DATE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    contract_url VARCHAR,
    rent NUMERIC NOT NULL,
    CONSTRAINT leases_pkey PRIMARY KEY (id),
    CONSTRAINT FK_ee853e23faf915f2c7da39a96f6 FOREIGN KEY (property_id) REFERENCES public.properties(id),
    CONSTRAINT FK_5a1a743f1482261d88fd33ea66f FOREIGN KEY (renter_id) REFERENCES public.profiles(id)
);

-- ============================================
-- TRANSACTIONS TABLE
-- ============================================

CREATE TABLE public.transactions (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    lease_id UUID NOT NULL,
    payer_id UUID NOT NULL,
    amount NUMERIC NOT NULL,
    platform_fee NUMERIC NOT NULL,
    type public.transactions_type_enum NOT NULL DEFAULT 'rent_payment'::public.transactions_type_enum,
    currency VARCHAR NOT NULL DEFAULT 'USD'::VARCHAR,
    payment_gateway_ref VARCHAR NOT NULL UNIQUE,
    status VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT transactions_pkey PRIMARY KEY (id),
    CONSTRAINT FK_f9c5381f2459e223afe9fa68029 FOREIGN KEY (lease_id) REFERENCES public.leases(id)
);

-- ============================================
-- BANK DETAILS TABLE
-- ============================================

CREATE TABLE public.bank_details (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE,
    bank_name VARCHAR NOT NULL,
    bank_code VARCHAR NOT NULL,
    account_number VARCHAR NOT NULL,
    account_name VARCHAR NOT NULL,
    recipient_code VARCHAR,
    CONSTRAINT bank_details_pkey PRIMARY KEY (id),
    CONSTRAINT FK_8eba31ad3c2e07c029ee73e48cb FOREIGN KEY (user_id) REFERENCES public.profiles(id)
);

-- ============================================
-- WALLETS TABLE
-- ============================================

CREATE TABLE public.wallets (
    balance NUMERIC NOT NULL DEFAULT 0,
    "userId" UUID NOT NULL,
    "updatedAt" TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT wallets_pkey PRIMARY KEY ("userId")
);

-- ============================================
-- LEDGER ENTRIES TABLE
-- ============================================

CREATE TABLE public.ledger_entries (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    amount NUMERIC NOT NULL,
    type public.ledger_entry_type_enum NOT NULL,
    category public.ledger_entry_category_enum NOT NULL,
    "walletId" VARCHAR NOT NULL,
    "referenceId" VARCHAR,
    "createdAt" TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT ledger_entries_pkey PRIMARY KEY (id)
);

-- ============================================
-- KYC VERIFICATIONS TABLE
-- ============================================

CREATE TABLE public.kyc_verifications (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE,
    status public.kyc_verifications_status_enum NOT NULL DEFAULT 'pending'::public.kyc_verifications_status_enum,
    id_type VARCHAR NOT NULL,
    id_number VARCHAR NOT NULL,
    id_image_url VARCHAR NOT NULL,
    rejection_reason VARCHAR,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT kyc_verifications_pkey PRIMARY KEY (id),
    CONSTRAINT FK_1e23c7821d740b4881f773c39aa FOREIGN KEY (user_id) REFERENCES public.profiles(id)
);

-- ============================================
-- CHAT THREADS TABLE
-- ============================================

CREATE TABLE public.chat_threads (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    property_id UUID,
    renter_id UUID,
    owner_id UUID,
    last_message_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT chat_threads_pkey PRIMARY KEY (id),
    CONSTRAINT chat_threads_property_id_fkey FOREIGN KEY (property_id) REFERENCES public.properties(id),
    CONSTRAINT chat_threads_renter_id_fkey FOREIGN KEY (renter_id) REFERENCES public.profiles(id),
    CONSTRAINT chat_threads_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.profiles(id)
);

-- ============================================
-- MESSAGES TABLE
-- ============================================

CREATE TABLE public.messages (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    thread_id UUID,
    sender_id UUID,
    content TEXT NOT NULL,
    is_ai_response BOOLEAN DEFAULT FALSE,
    embedding vector(1536),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT messages_pkey PRIMARY KEY (id),
    CONSTRAINT messages_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES public.chat_threads(id),
    CONSTRAINT messages_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES public.profiles(id)
);

-- ============================================
-- MESSAGE EMBEDDINGS TABLE
-- ============================================

CREATE TABLE public.message_embeddings (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT message_embeddings_pkey PRIMARY KEY (id),
    CONSTRAINT message_embeddings_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id)
);

-- ============================================
-- CHAT MESSAGES TABLE
-- ============================================

CREATE TABLE public.chat_messages (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    "userId" UUID,
    content TEXT NOT NULL,
    role VARCHAR NOT NULL,
    "createdAt" TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    action VARCHAR DEFAULT 'NONE'::VARCHAR,
    CONSTRAINT chat_messages_pkey PRIMARY KEY (id),
    CONSTRAINT FK_43d968962b9e24e1e3517c0fbff FOREIGN KEY ("userId") REFERENCES public.profiles(id)
);