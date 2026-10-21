-- Apply before enabling spend logs. No request/response or free-form metadata
-- may reach storage, even if an administrator changes LiteLLM's log settings.
BEGIN;
CREATE TABLE IF NOT EXISTS homecompute_token_usage_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    tracking_since timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION homecompute_metadata_only_usage()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.messages := '{}'::jsonb;
    NEW.response := '{}'::jsonb;
    NEW.proxy_server_request := '{}'::jsonb;
    NEW.metadata := '{}'::jsonb;
    NEW.request_tags := '[]'::jsonb;
    NEW.cache_key := '';
    NEW.api_base := '';
    -- Retain only a hashed key identifier, never a credential or client label.
    NEW.api_key := CASE WHEN NEW.api_key ~ '^[0-9a-f]{64}$' THEN NEW.api_key ELSE '' END;
    NEW."user" := '';
    NEW.end_user := NULL;
    NEW.requester_ip_address := NULL;
    NEW.session_id := NULL;
    NEW.team_id := NULL;
    NEW.organization_id := NULL;
    NEW.agent_id := NULL;
    NEW.mcp_namespaced_tool_name := NULL;
    NEW.status := CASE WHEN NEW.status IN ('success', 'failure') THEN NEW.status ELSE NULL END;
    RETURN NEW;
END;
$$;

-- A fresh database gets this trigger after LiteLLM creates its own schema;
-- existing databases get it before the updated proxy starts.
DO $$
BEGIN
    IF to_regclass('public."LiteLLM_SpendLogs"') IS NOT NULL THEN
        CREATE OR REPLACE TRIGGER homecompute_metadata_only_usage
            BEFORE INSERT OR UPDATE ON "LiteLLM_SpendLogs"
            FOR EACH ROW EXECUTE FUNCTION homecompute_metadata_only_usage();
        INSERT INTO homecompute_token_usage_state (singleton) VALUES (true)
            ON CONFLICT DO NOTHING;
    END IF;
END;
$$;
COMMIT;
