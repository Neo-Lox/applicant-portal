-- Magic link tokens for document uploads
CREATE TABLE IF NOT EXISTS magic_link_tokens (
    id BIGSERIAL PRIMARY KEY,
    application_id BIGINT NOT NULL,
    token_hash BYTEA NOT NULL,
    scope TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    fail_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_magic_link_tokens_hash
    ON magic_link_tokens (token_hash);

CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_app
    ON magic_link_tokens (application_id);

CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_expiry
    ON magic_link_tokens (expires_at);
