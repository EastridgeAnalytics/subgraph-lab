// The REST contract (lab/contracts/api.py) as TypeScript. Only the director changes it, with a decision on 08.
// Keep field names identical to the Python models.

export type Route = "relate" | "bolt";
export type StepStatus = "pending" | "running" | "done" | "failed" | "skipped";
export type Disposition = "confirmed" | "false_positive" | "needs_more";

export interface Health { ok: boolean; version: string; relate: string }

export interface ConnectTest { uri?: string; username?: string; password?: string; database?: string }
export interface ConnectResult {
  ok: boolean; neo4j_version?: string | null; edition?: string | null; query_uri?: string | null;
  pull_route?: Route | null; message: string;
}

export interface PullSpec {
  mode: "schema" | "cypher"; labels?: string[]; types?: string[]; since?: string | null; until?: string | null;
  query?: string | null; params?: Record<string, unknown>;
}
export interface SearchSpec { text?: string | null; account?: string | null; k?: number | null; hops?: number | null }
export interface IdentifierLink { rel_type: string; label: string; key: string; kind: "device" | "email" | "address" | "phone" | "other" }
export interface GraphMapping {
  account_label: string; account_key: string; account_opened?: string | null; payment_type: string; payment_key: string;
  payment_amount: string; payment_time: string; identifiers: IdentifierLink[];
}
export interface Recipe {
  name: string; graph?: GraphMapping; pull: PullSpec; search?: SearchSpec | null; models: string[];
  summaries: boolean; write_back?: Record<string, boolean>;
}
export interface SliceRequest { run_id?: string | null; recipe?: Recipe; pull?: PullSpec | null; search?: SearchSpec | null }
export interface Hit { key: string; rrf: number; found_by: ("vector" | "fulltext")[] }
export interface SliceEstimate {
  mode: "schema" | "cypher" | "search"; hits: Hit[]; hops?: number | null; nodes: number; relationships: number;
  memory_bytes: number; over_cap: boolean; cap: number; note?: string | null;
}

export interface Step {
  name: string; status: StepStatus; started_at?: string | null; finished_at?: string | null;
  counts: Record<string, number>; note?: string | null;
}
export interface Fallback { step: string; route: Route; reason: string; at: string }
export interface PushEntity {
  entity: string; kind: string; total: number; created: number; updated: number; skipped: number;
  batches: number; landed: boolean; error?: string | null;
}
export interface RunManifest {
  run_id: string; recipe_hash: string; created_at: string; finished_at?: string | null; status: StepStatus;
  routes: Record<string, Route>; fallbacks: Fallback[]; steps: Step[]; push: PushEntity[];
  counts: Record<string, number>; model_hash?: string | null; factor_version?: string | null;
}
export interface RunStarted { run_id: string }
export interface RunsList { runs: RunManifest[] }

export interface Factor {
  id: string; name: string; kind: "cypher" | "python" | "model"; query?: string | null; function?: string | null;
  source?: string | null; threshold?: number | null; weight: number; description?: string | null;
}
export interface ModelInfo { name: string; kind: "model" | "factor"; description: string; code_path: string; factor?: Factor | null }
export interface ModelsList { models: ModelInfo[]; factor_version: string; policy: Record<string, unknown> }

export interface AccountRow {
  key: string; risk_score: number; risk_score_model?: number | null; risk_score_rules?: number | null;
  factors: { id: string; score: number; weight: number }[]; community_key?: string | null;
  alert_key?: string | null; disposition?: Disposition | null;
}
export interface AccountsPage { total: number; rows: AccountRow[] }
export interface CommunityRow {
  key: string; size: number; score: number; summary?: string | null; summary_source?: string | null;
  disposition?: Disposition | null;
}
export interface CommunitiesPage { total: number; rows: CommunityRow[] }

export interface SharedIdentifier { kind: string; key: string; members_sharing: number }
export interface Evidence {
  subject: "community" | "alert"; key: string; run_id: string; member_count: number; member_keys: string[];
  score: number; score_parts: Record<string, number>; shared_identifiers: SharedIdentifier[];
  money: { inflow: number; outflow: number; internal: number; cycles: number; first_at?: string | null;
           last_at?: string | null; span_hours?: number | null };
  factors: { factor_id: string; name: string; accounts: number; evidence_ids: string[] }[];
  top_members: { key: string; score: number }[];
  comparison: { communities_in_slice: number; score_rank: number; median_size: number; median_score: number };
}
export interface Summary {
  key: string; text: string; source: "api" | "agent" | "template"; model?: string | null; evidence_hash: string;
  checks: Record<string, boolean>; failed_reason?: string | null;
}
export interface GraphNode { id: string; label: string; props: Record<string, unknown> }
export interface GraphEdge { source: string; target: string; type: string; props: Record<string, unknown> }
export interface CommunityDetail {
  row: CommunityRow; score_parts: Record<string, number>; evidence: Evidence; summary?: Summary | null;
  nodes: GraphNode[]; edges: GraphEdge[]; similar: CommunityRow[];
}

export interface Delta {
  new_alerts: number; new_communities: number; accounts_scored: number; score_changes: Record<string, number>;
  missing_remote_keys: number; sampled: number; vectors: number; dispositions: number; bytes_estimate: number;
}
export interface Review { run_id: string; plan: PushEntity[]; plan_text: string; delta: Delta }
export interface PushResult { run_id: string; pushed: boolean; entities: PushEntity[]; resumed: boolean; message: string }
export interface DispositionRequest { kind: "account" | "alert" | "community"; key: string; disposition: Disposition; by: string }
export interface DispositionResult { ok: boolean; staged: boolean }
export interface TimelinePoint { at: string; accounts: number; payments: number; amount: number }
export interface Timeline { start: string; end: string; as_of?: string | null; points: TimelinePoint[] }
export interface ShowCode { recipe_yaml: string; python: string }
