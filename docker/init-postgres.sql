-- Runs once on the first boot of the Postgres container, before any data exists.
-- Postgres image executes /docker-entrypoint-initdb.d/*.sql as the POSTGRES_USER
-- (which we set to `odoo18` via env) on the default `postgres` DB.
--
-- The compose env already creates role `odoo18` (POSTGRES_USER) and DB `postgres`
-- (POSTGRES_DB). We add the supporting roles the live system relies on so the
-- restore from the prod dump doesn't fail on missing role grants.

-- Read-only role used by the reports webapp on the live server.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'report_user') THEN
        CREATE ROLE report_user LOGIN PASSWORD 'report_user';
    END IF;
END
$$;

-- Empty target DB for the restore. pg_restore --create would recreate it,
-- but we pre-create so the bootstrap script can use --clean --if-exists safely.
SELECT 'CREATE DATABASE odoo18 OWNER odoo18'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'odoo18')\gexec

-- Make sure odoo18 has the privileges Odoo expects.
ALTER ROLE odoo18 WITH SUPERUSER CREATEDB CREATEROLE;
