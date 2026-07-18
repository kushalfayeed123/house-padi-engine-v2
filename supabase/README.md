# Supabase Migrations

This directory contains database migrations for the HousePadi real estate platform.

## Structure

```
supabase/
├── migrations/
│   └── 20260715000000_create_housepadi_schema.sql
├── config.toml
└── seed.sql (optional)
```

## Migrations

### `20260715000000_create_housepadi_schema.sql`
Initial schema setup including:
- **Tables**: profiles, properties, tours, applications, leases, transactions, kyc_verifications, wallets, ledger_entries, chat_threads, messages
- **Enum Types**: kyc_status, property_status, transaction_type, application_status, user_role, transaction_status
- **Indexes**: Optimized for common queries on user_id, property_id, status, created_at, etc.
- **Triggers**: Auto-updating timestamps with `update_updated_at_column()` function
- **Row Level Security**: Enabled on all tables (policies can be customized)
- **Materialized Views**: For analytics and reporting (active_leases_view, property_statistics_view)

## Running Migrations

### Using Supabase CLI

```bash
# Install Supabase CLI (if not already installed)
npm install -g supabase

# Link your project
supabase link --project-ref <project-ref>

# Push migrations to your Supabase instance
supabase db push

# Check migration status
supabase migration list
```

### Using psql directly

```bash
# Connect to your Supabase PostgreSQL database
psql postgres://<user>:<password>@<host>:5432/<database>

# Run the migration
\i supabase/migrations/20260715000000_create_housepadi_schema.sql
```

## Schema Overview

### Core Tables

#### `profiles` (Users)
- Stores user information (renters, landlords, admins)
- Fields: email, first_name, last_name, phone_number, role, avatar_url
- RLS: User can view/modify their own profile

#### `properties`
- Property listings created by landlords
- Fields: title, address_full, location, price, images, features, status
- Supports coordinates (latitude, longitude) for mapping
- Status: draft, published, rented, delisted

#### `tours`
- Tour scheduling for properties
- Links: property → tour, visitor (profile) → tour
- Tracks visitor information and tour dates

#### `applications`
- Rental applications from renters
- Includes AI matching score and screening summary
- Status: submitted, reviewing, approved, rejected

#### `leases`
- Active lease agreements between renters and owners
- Links renter, owner, and property
- Tracks rent amount, duration, and contract URL

#### `transactions`
- Payment transactions and financial records
- Tracks rent payments, deposits, refunds, platform fees
- References payment gateway for audit trail

#### `kyc_verifications`
- Identity verification for users
- Stores ID type, number, and image URL
- Status: pending, verified, rejected

#### `wallets`
- User wallet balances
- One-to-one relationship with profiles

#### `ledger_entries`
- Accounting ledger for wallet transactions
- Tracks debits and credits with categories
- Immutable audit trail

#### `chat_threads` & `messages`
- Communication system between renters and owners
- Supports property-specific conversations
- Tracks AI vs human messages

## Performance Considerations

### Indexes Created
- **Foreign keys**: Automatic indexes for referential integrity
- **Status fields**: Optimized for filtering active records
- **Timestamps**: Indexes on created_at, updated_at for sorting/filtering
- **Location**: Index on property location for geographic queries
- **Geographic coordinates**: Composite index for nearby property searches

### Materialized Views
- **active_leases_view**: Precomputed joined view for dashboard queries
- **property_statistics_view**: Aggregated metrics (tours, applications, active leases)
- Refresh these views periodically: `REFRESH MATERIALIZED VIEW active_leases_view;`

## Security Features

### Row Level Security (RLS)
All tables have RLS enabled. Customize policies based on your requirements:
- Public viewing of published properties
- Users can only access their own data (wallets, messages)
- Admins have override access

### Audit Trail
- `created_at`, `updated_at` timestamps on all tables
- Soft delete support via `deleted_at` column
- Ledger entries provide immutable financial records
- Payment gateway references for transaction verification

## Extending the Schema

To add new migrations:

1. Create a new SQL file with timestamp: `YYYYMMDDHHMMSS_description.sql`
2. Use the Supabase CLI: `supabase migration new <description>`
3. Add your SQL changes
4. Push to your project: `supabase db push`

## Troubleshooting

### Migration Failed
- Check for syntax errors in the SQL file
- Ensure all referenced tables/functions exist
- Review Supabase logs for detailed error messages

### RLS Issues
- If data access is blocked, verify RLS policies are set correctly
- Temporarily disable RLS for testing: `ALTER TABLE <table> DISABLE ROW LEVEL SECURITY;`
- Re-enable when done: `ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;`

### Performance Issues
- Review slow queries with: `EXPLAIN ANALYZE <query>;`
- Add indexes for frequently filtered columns
- Consider partitioning large tables (transactions, messages)

## Related Documentation

- [Supabase Docs](https://supabase.com/docs)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [HousePadi Schema Payloads](../../app/schemas/payloads.py)
