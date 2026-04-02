-- ═══════════════════════════════════════════════════════════════════════
-- ServerLens — SQL template for manually creating a read-only user (setup_db_users.sql)
--
-- Description:
--   Reference SQL file (template) for manually creating a read-only PostgreSQL
--   user. Use this if you want to configure access manually instead of the
--   interactive setup_db.sh script.
--
--   What the template does:
--     1. Creates user serverlens_readonly with a password
--     2. Sets default_transaction_read_only = on (no writes)
--     3. Sets statement_timeout = 30s (limits heavy queries)
--     4. Shows example GRANTs for specific tables
--
-- Run (first block only — create user):
--   sudo -u postgres psql -f scripts/setup_db_users.sql
--
-- IMPORTANT:
--   - Replace 'CHANGE_ME_TO_SECURE_PASSWORD' with a real password!
--   - GRANT commands below are commented — uncomment what you need
--   - For automated setup use: sudo bash scripts/setup_db.sh
--
-- Security:
--   - User is created with read-only intent only
--   - statement_timeout mitigates accidentally heavy queries
--   - Prefer GRANT SELECT on specific tables, not ALL TABLES
-- ═══════════════════════════════════════════════════════════════════════

-- Create the read-only user
CREATE USER serverlens_readonly WITH PASSWORD 'CHANGE_ME_TO_SECURE_PASSWORD';

-- Force read-only transactions
ALTER USER serverlens_readonly SET default_transaction_read_only = on;

-- Set a statement timeout for safety (30 seconds)
ALTER USER serverlens_readonly SET statement_timeout = '30s';

-- ═══════════════════════════════════════════
-- Grant access per database
-- ═══════════════════════════════════════════

-- For each database, connect and run the GRANT statements.
-- Example for a database called "app_production":

-- \c app_production

-- GRANT CONNECT ON DATABASE app_production TO serverlens_readonly;
-- GRANT USAGE ON SCHEMA public TO serverlens_readonly;

-- Grant SELECT only on specific tables:
-- GRANT SELECT ON users, api_requests TO serverlens_readonly;

-- Or grant SELECT on all existing tables (less secure):
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO serverlens_readonly;

-- ═══════════════════════════════════════════
-- Verification
-- ═══════════════════════════════════════════

-- Test the user can only read:
-- \c app_production serverlens_readonly
-- SELECT 1;                          -- should work
-- CREATE TABLE test (id int);        -- should FAIL
-- INSERT INTO users (id) VALUES (1); -- should FAIL
