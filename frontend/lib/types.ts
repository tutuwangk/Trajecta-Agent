export type ApiResponse<T> = {
  ok: boolean;
  data: T | null;
  error: { code: string; message: string; step?: string | null } | null;
  step_status: Record<string, string>;
};

export type UserProfile = {
  destination: string;
  days: number;
  nights: number;
  hotel_area?: string | null;
  travelers: { count: number; type: string };
  budget_level: "low" | "medium" | "high";
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
  decision: "keep" | "delete" | "must_visit" | "optional";
  manual_name?: string | null;
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
  contexts?: string[];
  experience_tags?: string[];
};

export type SessionData = {
  session_id: string;
  raw_input: string;
  notes: string;
  user_profile: UserProfile;
  pois: PoiRow[];
  itinerary_state?: ItineraryState | null;
  revision_history: Array<Record<string, unknown>>;
};

export type ItineraryState = {
  runtime_pois: RuntimePoi[];
  route_matrix: Array<Record<string, unknown>>;
  itinerary: Itinerary;
  verification: { passed: boolean; issues: Array<{ type: string; severity: string; message: string; suggestion?: string }> };
};

export type RuntimePoi = {
  poi_id: string;
  standard_name: string;
  location: { lng?: number | null; lat?: number | null };
  match_status: string;
};

export type Itinerary = {
  destination: string;
  days: DayRoute[];
  global_risks: string[];
  uncertain_pois: Array<Record<string, unknown>>;
  revision_notes: string[];
};

export type DayRoute = {
  day: number;
  theme: string;
  summary: string;
  total_outing_min?: number;
  total_outing_minutes?: number;
  outing_duration_min?: number;
  outing_duration_minutes?: number;
  hotel_departure_transport_min?: number;
  hotel_return_transport_min?: number;
  hotel_to_first_transport_min?: number;
  last_to_hotel_transport_min?: number;
  meal_breaks?: Array<{ duration_min?: number; duration_minutes?: number }>;
  items: ItineraryItem[];
  removed_pois?: Array<{ name: string; reason: string }>;
  alternatives?: Array<Record<string, unknown> | string>;
};

export type ItineraryItem = {
  time_block: string;
  poi_id: string;
  name: string;
  arrival_time?: string;
  duration_min: number;
  reason: string;
  transport_to_next?: { mode: string; duration_min?: number; distance_m?: number; amap_navigation_link?: string };
  risk_notes?: string[];
  amap_link?: string;
};
