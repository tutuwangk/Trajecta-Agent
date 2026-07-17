CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  raw_input TEXT NOT NULL,
  notes TEXT NOT NULL,
  user_profile JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pois (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  raw_poi JSONB NOT NULL,
  grounded_poi JSONB NOT NULL,
  decision TEXT NOT NULL DEFAULT 'keep',
  system_decision TEXT NOT NULL DEFAULT 'include',
  user_override TEXT NOT NULL DEFAULT 'none',
  final_decision TEXT NOT NULL DEFAULT 'include',
  inferred_role TEXT NOT NULL DEFAULT 'visit',
  decision_reason TEXT NOT NULL DEFAULT '',
  manual_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS itineraries (
  session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  runtime_pois JSONB NOT NULL,
  route_matrix JSONB NOT NULL,
  itinerary JSONB NOT NULL,
  verification JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS revision_history (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  instruction TEXT NOT NULL,
  itinerary JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS planning_interventions (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  choice_id TEXT,
  choice_label TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS planning_runs (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  checkpoint TEXT NOT NULL DEFAULT 'understanding',
  result_status TEXT NOT NULL DEFAULT '',
  error_code TEXT,
  error_message TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  input_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  fact_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  blueprint JSONB NOT NULL DEFAULT '{}'::jsonb,
  validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
  release_decision JSONB NOT NULL DEFAULT '{}'::jsonb,
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  result_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key TEXT NOT NULL DEFAULT '',
  debug JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS route_cache (
  cache_key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
