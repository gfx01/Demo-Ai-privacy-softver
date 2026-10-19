-- privacy hub schema (database "privacyhub").
-- Tenant separation: every table carries organisation_id.
-- The audit log never stores prompt/response text, entity values or mappings.

CREATE TABLE organisations (
    id          text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,62}$'),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE org_settings (
    organisation_id   text PRIMARY KEY REFERENCES organisations(id) ON DELETE CASCADE,
    mode              text NOT NULL DEFAULT 'mask' CHECK (mode IN ('mask', 'strict')),
    strategy          text NOT NULL DEFAULT 'bracket' CHECK (strategy IN ('bracket', 'pseudonym')),
    language          text NOT NULL DEFAULT 'hu' CHECK (language IN ('hu', 'en')),
    disabled_entities text[] NOT NULL DEFAULT '{}',
    updated_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        text
);

CREATE TABLE audit_events (
    id              bigserial PRIMARY KEY,
    organisation_id text NOT NULL,
    request_id      text NOT NULL,
    event           text NOT NULL CHECK (event IN ('request', 'response')),
    ts              timestamptz NOT NULL DEFAULT now(),
    outcome         text NOT NULL,
    user_id         text,
    team_id         text,
    key_alias       text,
    end_user_id     text,
    model           text,
    mode            text,
    strategy        text,
    language        text,
    entity_total    integer NOT NULL DEFAULT 0,
    entity_counts   jsonb NOT NULL DEFAULT '{}'::jsonb,
    restored_total  integer,
    leftover_tokens integer,
    latency_ms      integer,
    error_class     text
);
CREATE INDEX audit_events_org_ts ON audit_events (organisation_id, ts DESC);
CREATE INDEX audit_events_request ON audit_events (request_id);

-- Gateway: append-only audit, read settings.
GRANT SELECT ON organisations, org_settings TO ph_gateway;
GRANT INSERT ON audit_events TO ph_gateway;
GRANT USAGE ON SEQUENCE audit_events_id_seq TO ph_gateway;

-- Portal (admin UI): manage organisations/settings, read audit.
GRANT SELECT, INSERT, UPDATE ON organisations, org_settings TO ph_portal;
GRANT SELECT ON audit_events TO ph_portal;

-- Nobody but the database owner can UPDATE or DELETE audit rows.

INSERT INTO organisations (id, name) VALUES ('default', 'Default organisation');
INSERT INTO org_settings (organisation_id, mode, strategy, language) VALUES ('default', 'mask', 'bracket', 'hu');
