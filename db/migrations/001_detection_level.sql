-- Detection level per organisation ("maximum" also masks unknown words).
-- New installations get this column from db/schema/privacyhub.sql; for an
-- existing database run once as the database owner:
--   docker compose exec -T postgres psql -U "$POSTGRES_USER" -d privacyhub < db/migrations/001_detection_level.sql
-- The gateway reads the column; without it every request is blocked
-- (settings unavailable -> fail closed).
ALTER TABLE org_settings
    ADD COLUMN IF NOT EXISTS detection text NOT NULL DEFAULT 'standard'
    CHECK (detection IN ('standard', 'maximum'));
