export type ApiResponse<T> = {
  ok: boolean;
  data: T | null;
  error: {
    code: string;
    message: string;
    step?: string | null;
    blockers?: PlanningBlocker[];
  } | null;
  step_status: Record<string, string>;
};

export type PlanningBlocker = {
  type?: string;
  message?: string;
  action_hint?: string;
  affected_day?: number;
  affected_poi_name?: string;
};

export type UserProfile = {
  destination: string;
  days: number;
  nights: number;
  hotel_name?: string | null;
  hotel_area?: string | null;
  travelers: { count: number; type: string };
  budget_level: "low" | "medium" | "high";
  transport_preference: string[];
  route_goal?: string;
  preferences: Record<string, number>;
  constraints: {
    avoid_too_tired: boolean;
    physical_intensity?: "high" | "medium" | "low";
    must_visit: string[];
    avoid_visit: string[];
  };
};

export type PoiRow = {
  id: number;
  raw_poi: Record<string, unknown>;
  grounded_poi: GroundedPoi;
  decision: "keep" | "delete" | "must_visit" | "optional" | "arrange_nearby" | "confirm_arrange_nearby";
  system_decision: "include" | "optional" | "needs_confirmation" | "exclude";
  user_override: "must_include" | "optional" | "remove" | "rename_confirm" | "arrange_nearby" | "none";
  final_decision: "include" | "optional" | "exclude" | "unresolved";
  inferred_role: "visit" | "meal" | "hotel" | "start" | "end" | "transport" | "backup";
  decision_reason: string;
  place_pool_item: PlacePoolItem;
  manual_name?: string | null;
};

export type PlacePoolItem = {
  id: string;
  display_name: string;
  type_label: string;
  status_label: "已识别" | "需确认" | "未匹配";
  decision_label: "已纳入" | "待定" | "需确认" | "未纳入";
  primary_actions: string[];
  needs_attention: boolean;
};

export type GroundedPoi = {
  raw_name: string;
  standard_name: string;
  amap_id: string;
  address: string;
  location: { lng?: number | null; lat?: number | null };
  city: string;
  district: string;
  category_raw?: string;
  category_normalized: string;
  match_confidence: number;
  match_status: "matched" | "ambiguous" | "unmatched";
  is_chain?: boolean;
  chain_status?: "unresolved" | "resolved";
  brand_name?: string;
  selection_mode?: string;
  resolved_branch_id?: string;
  resolved_branch_name?: string;
  resolved_from_anchor_poi_id?: string;
  resolved_from_anchor_name?: string;
  resolved_by?: string;
  candidate_options?: Array<Record<string, unknown>>;
  route_branch_options?: Array<Record<string, unknown>>;
  contexts?: string[];
  experience_tags?: string[];
};

export type PoiDecisionInput = {
  poi_id: string;
  decision: string;
  manual_name?: string;
  anchor_poi_id?: string;
};

export type SessionData = {
  session_id: string;
  raw_input: string;
  notes: string;
  user_profile: UserProfile;
  pois: PoiRow[];
  itinerary_state?: ItineraryState | null;
  planning_intervention?: PlanningIntervention | null;
  latest_planning_run?: PlanningRun | null;
  revision_history: Array<Record<string, unknown>>;
};

export type PlanningRun = {
  id: string;
  status: "running" | "completed" | "failed";
  stage?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  attempt_count?: number;
  duration_ms?: number;
};

export type PlanningInterventionIssue = {
  type: string;
  domain?: string;
  message: string;
  suggestion?: string;
  evidence?: string;
  day?: number;
  poi_name?: string;
};

export type PlanningIntervention = {
  id: string;
  status: "needs_user_choice";
  domain?: string;
  question: string;
  options: Array<{
    id: string;
    label: string;
    description?: string;
  }>;
  issues?: PlanningInterventionIssue[];
  display_issues?: PlanningInterventionIssue[];
  context_summary?: Record<string, unknown>;
};

export type PlanCompletedResult = {
  status: "completed";
  runtime_pois: RuntimePoi[];
  route_matrix: Array<Record<string, unknown>>;
  itinerary: Itinerary;
  verification: ItineraryState["verification"];
};

export type PlanNeedsChoiceResult = {
  status: "needs_user_choice";
  planning_intervention: PlanningIntervention;
};

export type PlanResult = PlanCompletedResult | PlanNeedsChoiceResult;

export type ItineraryState = {
  runtime_pois: RuntimePoi[];
  route_matrix: Array<Record<string, unknown>>;
  itinerary: Itinerary;
  verification: { passed: boolean; issues: Array<{ type: string; severity: string; message: string; suggestion?: string; day?: number; poi_name?: string }> };
};

export type RuntimePoi = {
  poi_id: string;
  standard_name: string;
  location: { lng?: number | null; lat?: number | null };
  match_status: string;
  final_decision?: string;
};

export type Itinerary = {
  destination: string;
  route_summary?: {
    main_message?: string;
    scheduled_places_count?: number;
    unscheduled_places_count?: number;
    attention_required_count?: number;
  };
  days: DayRoute[];
  global_risks: string[];
  uncertain_pois: Array<Record<string, unknown>>;
  unscheduled_places?: Array<{ name: string; reason: string }>;
  attention_places?: Array<{ name: string; reason: string }>;
  revision_notes: string[];
};

export type DayRoute = {
  day: number;
  theme: string;
  summary: string;
  total_outing_min?: number;
  intensity_outing_min?: number;
  total_transfer_min?: number;
  total_outing_minutes?: number;
  outing_duration_min?: number;
  outing_duration_minutes?: number;
  hotel_departure_transport_min?: number;
  hotel_return_transport_min?: number;
  hotel_to_first_transport_min?: number;
  last_to_hotel_transport_min?: number;
  meal_slots?: Array<{
    slot: "breakfast" | "lunch" | "dinner";
    requirement?: "required" | "optional";
    source: "poi" | "inside_poi" | "fallback_nearby";
    poi_id?: string;
    within_poi_id?: string;
  }>;
  meal_breaks?: Array<{
    label?: string;
    slot?: "breakfast" | "lunch" | "dinner";
    start_time?: string;
    duration_min?: number;
    duration_minutes?: number;
    within_poi_id?: string;
    included_in_item_duration?: boolean;
    source?: "inside_poi" | "fallback_nearby";
  }>;
  segments?: DaySegment[];
  hotel_rest_breaks?: HotelRestBreak[];
  items: ItineraryItem[];
  removed_pois?: Array<{ name: string; reason: string }>;
  alternatives?: Array<Record<string, unknown> | string>;
};

export type DaySegment =
  | {
      kind: "outing";
      segment_time?: "morning" | "midday" | "afternoon" | "evening" | "night" | string;
      poi_ids: string[];
    }
  | {
      kind: "hotel_rest";
      duration_min?: number;
      reason?: string;
    };

export type HotelRestBreak = {
  after_poi_id: string;
  before_poi_id: string;
  duration_min?: number;
  reason?: string;
  return_to_hotel_transport_min?: number;
  depart_from_hotel_transport_min?: number;
  hotel_arrival_time?: string;
  rest_end_time?: string;
  next_departure_time?: string;
};

export type ItineraryItem = {
  time_block: string;
  poi_id: string;
  name: string;
  selected_branch_id?: string;
  scheduled_role?: "quick_stop" | "meal_stop" | "anchor_visit" | "filler_visit" | "nightlife_stop" | string;
  burden_role?: "protected_basic" | "light_detour" | "normal_load" | "heavy_load" | string;
  trim_priority?: "must_keep" | "never_trim_before_meal" | "keep_if_low_detour" | "trim_first" | string;
  arrival_time?: string;
  duration_min: number;
  reason: string;
  meal_roles?: Array<"breakfast" | "lunch" | "dinner">;
  transport_to_next?: { mode: "walking" | "taxi" | "public_transport" | "driving" | "transit" | "unknown" | string; duration_min?: number; distance_m?: number; amap_navigation_link?: string };
  risk_notes?: string[];
  amap_link?: string;
};
