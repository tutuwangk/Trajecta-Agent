export type AgentV3CoverageEntry = {
  obligation_id: string;
  mention: string;
  role: "visit" | "meal" | "lodging" | "shopping" | "photo" | "airport" | string;
  priority: "required" | "preferred" | "optional";
  status: "unhandled" | "scheduled" | "pending_confirmation" | "not_scheduled" | "excluded";
  stop_id?: string | null;
  reason_code?: string | null;
  rationale?: string | null;
};

export type AgentV3GroundingDecision = {
  target_id: string;
  obligation_ids: string[];
  mentions: string[];
  status: "selected" | "needs_confirmation" | "no_match" | string;
  selected_candidate_id?: string | null;
  selected_name?: string | null;
  selected_address?: string | null;
  rationale: string;
  confidence: number;
  options: Array<{ candidate_id: string; name: string; address?: string | null }>;
  clarification_question?: string | null;
};

export type AgentV3TimelineStop = {
  stop_id: string;
  candidate_id: string;
  obligation_ids: string[];
  name: string;
  kind: "lodging" | "airport" | "visit" | "meal" | "shopping" | "photo" | string;
  arrival_at: string;
  departure_at: string;
  stay_duration_min: number;
  location?: { longitude: number; latitude: number } | null;
  address?: string | null;
  coordinate_system?: "gcj02";
};

export type AgentV3TimelineLeg = {
  leg_id: string;
  from_stop_id: string;
  to_stop_id: string;
  departure_at: string;
  arrival_at: string;
  duration_min: number;
  mode: string;
  fact_id: string;
  fact_source: string;
  fact_status: "verified" | "estimated" | string;
};

export type AgentV3DeliveryIssue = {
  code: string;
  severity: "review" | "blocking";
  message: string;
  recommendation: string;
  day_numbers: number[];
  obligation_ids: string[];
  place_names: string[];
};

export type AgentV3Candidate = {
  candidate_snapshot_id: string;
  producing_run_id: string;
  fact_status: "verified" | "degraded" | "failed";
  experience_status: "good" | "needs_adjustment" | "conflict";
  coverage: {
    explicit_place_count: number;
    disposed_place_count: number;
    coverage_ratio: number;
    open_obligation_ids: string[];
    entries: AgentV3CoverageEntry[];
  };
  grounding_decisions: AgentV3GroundingDecision[];
  unresolved_places: Array<{
    target_id: string;
    obligation_ids: string[];
    mention: string;
    reason: string;
    clarification_question?: string | null;
  }>;
  timeline: {
    snapshot_id: string;
    days: Array<{
      day_number: number;
      calendar_date: string;
      title: string;
      stops: AgentV3TimelineStop[];
      legs: AgentV3TimelineLeg[];
    }>;
  };
  fact_gap_report: {
    fact_need_plan_id: string;
    gaps: Array<{
      need_id: string;
      kind: string;
      day_number: number;
      stop_ids: string[];
      place_names: string[];
      failure_code: string;
      failure_message: string;
      still_scheduled: boolean;
      impact: string;
    }>;
  };
  operational_facts: Array<{
    fact_id: string;
    candidate_id: string;
    stop_id: string;
    visit_at: string;
    visit_compatible: true;
    claims: Array<{
      field: "opening_hours" | "closure" | "last_entry" | "reservation";
      value: string;
      source_ids: string[];
      confidence: number;
    }>;
    sources: Array<{
      source_id: string;
      provider: string;
      uri: string;
      title: string;
      excerpt: string;
      retrieved_at: string;
    }>;
  }>;
};

export type AgentV3Delivery = {
  run_id: string;
  run_status: "created" | "active" | "waiting_user" | "needs_resume" | "succeeded" | "failed" | "cancelled";
  delivery_state: "working" | "not_delivered" | "publishable" | "review_required" | "blocked";
  candidate: AgentV3Candidate | null;
  assessment: {
    candidate_snapshot_id: string;
    producing_run_id: string;
    state: "publishable" | "review_required" | "blocked";
    may_publish: boolean;
    issues: AgentV3DeliveryIssue[];
  } | null;
  release: {
    release_id: string;
    producing_run_id: string;
    candidate_snapshot_id: string;
    status: "published";
    immutable: true;
    narrative: {
      overview: string;
      days: Array<{
        day_number: number;
        title: string;
        summary: string;
      }>;
    };
    published_at: string;
  } | null;
};

export type AgentV3Run = {
  run_id: string;
  workspace_id: string;
  goal_revision_id: string;
  status: AgentV3Delivery["run_status"];
};

export type AgentV3Workspace = {
  workspace_id: string;
  version: number;
  goal: {
    goal_revision_id: string;
    destination: string;
    start_date: string;
    days: number;
  };
  sources: Array<{
    source_id: string;
    kind: "user_request" | "raw_material" | "user_revision";
    content: string;
  }>;
};

export type AgentV3Event = {
  event_id: number;
  created_at: string;
  type: string;
  [key: string]: unknown;
};
