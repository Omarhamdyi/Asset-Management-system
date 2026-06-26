-- Migration: 001_add_subdomain_to_ip_address_relationship
-- Adds the missing subdomain_to_ip_address value to the asset_relationship_type
-- enum and updates the shape CHECK constraint to allow it.
-- Required for the bidirectional ip_address ↔ subdomain relationship.
--
-- Run once against your database:
--   psql -U <user> -d <dbname> -f migrations/001_add_subdomain_to_ip_address_relationship.sql

BEGIN;

-- Step 1: Add the missing enum value.
-- ALTER TYPE ... ADD VALUE cannot run inside a transaction in older Postgres
-- versions, but is safe inside a transaction in Postgres 12+.
ALTER TYPE asset_relationship_type ADD VALUE IF NOT EXISTS 'subdomain_to_ip_address';

-- Step 2: Commit the enum change before altering the CHECK constraint,
-- because the new enum value must be visible before the constraint references it.
COMMIT;

BEGIN;

-- Step 3: Drop the existing shape CHECK constraint.
ALTER TABLE asset_relationships
    DROP CONSTRAINT asset_relationships_shape;

-- Step 4: Re-add it with the subdomain_to_ip_address case included.
ALTER TABLE asset_relationships
    ADD CONSTRAINT asset_relationships_shape CHECK (
        (relationship_type = 'subdomain_to_domain'        AND source_asset_type = 'subdomain'    AND target_asset_type = 'domain')      OR
        (relationship_type = 'subdomain_to_ip_address'    AND source_asset_type = 'subdomain'    AND target_asset_type = 'ip_address')  OR
        (relationship_type = 'service_to_ip_address'      AND source_asset_type = 'service'      AND target_asset_type = 'ip_address')  OR
        (relationship_type = 'ip_address_to_subdomain'    AND source_asset_type = 'ip_address'   AND target_asset_type = 'subdomain')   OR
        (relationship_type = 'certificate_to_domain'      AND source_asset_type = 'certificate'  AND target_asset_type = 'domain')      OR
        (relationship_type = 'certificate_to_subdomain'   AND source_asset_type = 'certificate'  AND target_asset_type = 'subdomain')   OR
        (relationship_type = 'technology_to_subdomain'    AND source_asset_type = 'technology'   AND target_asset_type = 'subdomain')   OR
        (relationship_type = 'technology_to_service'      AND source_asset_type = 'technology'   AND target_asset_type = 'service')
    );

COMMIT;