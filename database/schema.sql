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

CREATE TABLE IF NOT EXISTS route_cache (
  cache_key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
