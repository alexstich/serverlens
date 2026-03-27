-- ═══════════════════════════════════════════════════════════════════════
-- ServerLens — Шаблон SQL для ручного создания read-only пользователя (setup_db_users.sql)
--
-- Описание:
--   Справочный SQL-файл (шаблон) для ручного создания read-only пользователя
--   PostgreSQL. Используйте этот файл, если хотите настроить доступ вручную
--   вместо интерактивного скрипта setup_db.sh.
--
--   Что делает шаблон:
--     1. Создаёт пользователя serverlens_readonly с паролем
--     2. Устанавливает default_transaction_read_only = on (запрет записи)
--     3. Устанавливает statement_timeout = 30s (защита от тяжёлых запросов)
--     4. Даёт примеры GRANT для конкретных таблиц
--
-- Запуск (только первый блок — создание пользователя):
--   sudo -u postgres psql -f scripts/setup_db_users.sql
--
-- ВНИМАНИЕ:
--   - Замените 'CHANGE_ME_TO_SECURE_PASSWORD' на реальный пароль!
--   - Команды GRANT (ниже) закомментированы — раскомментируйте нужные
--   - Для автоматической настройки используйте: sudo bash scripts/setup_db.sh
--
-- Безопасность:
--   - Пользователь создаётся ТОЛЬКО с правом чтения (read-only)
--   - statement_timeout защищает от случайно тяжёлых запросов
--   - Рекомендуется давать SELECT только на конкретные таблицы, а не ALL TABLES
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
