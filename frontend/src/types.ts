/** Domain types mirroring the backend Pydantic schemas. */

// ---------- Auth ----------

export interface AuthMeOut {
  user_id: string;
  org_id: string;
  email: string;
  role: string;
  org_name: string;
  org_plan: string;
}

export interface TokensOut {
  access_token: string;
  access_token_expires_at: string;
  refresh_token: string | null;
  user: AuthMeOut;
}

export type DocumentStatus =
  | "received"
  | "extracting"
  | "extracted"
  | "understood"
  | "indexed"
  | "error";

export type FileType = "pdf" | "image" | "csv" | "xlsx" | "html";

export type DocumentType =
  | "bank_statement"
  | "sales_invoice"
  | "purchase_invoice"
  | "receipt"
  | "unknown";

export interface DocumentOut {
  id: string;
  org_id: string;
  source: string;
  original_filename: string;
  file_type: FileType;
  document_type: DocumentType;
  status: DocumentStatus;
  file_size_bytes: number;
  error_message: string | null;
  created_at: string;
  processed_at: string | null;
}

export interface DocumentDetailOut extends DocumentOut {
  raw_extraction_json: Record<string, unknown> | null;
}

export interface DocumentListOut {
  items: DocumentOut[];
  total: number;
}

export interface ApiHealth {
  status: string;
  checks: Record<string, string>;
}

// ---------- Dashboard summary ----------

export interface KpiOut {
  value: number;
  prev_value: number;
  delta_pct: number;
}

export interface CashFlowPointOut {
  date: string;
  // The backend uses Pydantic Field(alias="in") / Field(alias="out") so the
  // wire format is `in` / `out` (NOT `in_amount` / `out_amount`).
  // Values arrive as JSON strings (Decimal) — the Dashboard adapter coerces
  // them to numbers before handing to recharts.
  in: string | number;
  out: string | number;
  net: string | number;
}

export interface CategorySliceOut {
  name: string;
  value: number;
  color: string;
}

export interface AgingBucketOut {
  bucket: string;
  amount: number;
}

export interface CounterpartyRowOut {
  name: string;
  amount: number;
  delta_pct: number;
}

export interface ForecastPointOut {
  date: string;
  forecast: number;
  lower_band: number;
  upper_band: number;
}

export interface ComplianceRowOut {
  status: "ok" | "warn" | "fail";
  label: string;
}

export type InsightSeverity = "info" | "attention" | "urgent";

export interface InsightOut {
  id: string;
  org_id: string;
  type: string;
  severity: InsightSeverity;
  title: string;
  body: string;
  supporting_data: Record<string, unknown> | null;
  created_at: string;
  dismissed_at: string | null;
}

export interface InsightListOut {
  items: InsightOut[];
  total: number;
}

export interface RecurringOutflowOut {
  label: string;
  median_amount: string | number;
  expected_day_of_month: number | null;
  observed_count: number;
  last_seen_on: string;
  status: "on_track" | "due_soon" | "overdue";
  days_until_due: number | null;
}

// ---------- Learning ----------

export interface PatternRowOut {
  label: string;
  median_amount: string | number;
  expected_day_of_month: number | null;
  cadence: string;
  observed_count: number;
  last_seen_on: string;
  days_since_last_seen: number;
  is_overdue: boolean;
}

export interface ForecastPreviewPoint {
  date: string;
  forecast: string | number;
  day_of_month: number;
  is_recurring_day: boolean;
}

export interface LearningStatusOut {
  bank_txn_count: number;
  vendor_count: number;
  insight_count: number;
  pattern_count: number;
  tagged_txn_count: number;
  auto_categorized_count: number;
  anomaly_insight_count: number;
  missed_payment_insight_count: number;
  adaptive_z_threshold: number;
  coefficient_of_variation: number;
  threshold_explanation: string;
  patterns: PatternRowOut[];
  forecast_preview: ForecastPreviewPoint[];
}

export interface RetrainOut {
  new_patterns: number;
  newly_tagged_txns: number;
  missed_payment_insights: number;
  auto_categorized: number;
  rehumanized_insights: number;
  ran_at: string;
}

export interface DashboardSummaryOut {
  cash_position: KpiOut;
  receivables: KpiOut;
  payables: KpiOut;
  net_flow_mtd: KpiOut;
  cash_flow: CashFlowPointOut[];
  expense_breakdown: CategorySliceOut[];
  receivables_aging: AgingBucketOut[];
  forecast: ForecastPointOut[];
  top_vendors: CounterpartyRowOut[];
  top_clients: CounterpartyRowOut[];
  insights: InsightOut[];
  compliance: ComplianceRowOut[];
  recurring_outflows?: RecurringOutflowOut[];
  has_any_data: boolean;
  bank_txn_count: number;
}
