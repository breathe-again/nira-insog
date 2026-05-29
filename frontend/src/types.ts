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

export interface CashFlowCategoryPointOut {
  date: string;
  categories: Record<string, string | number>;
}

export interface CashFlowMetaOut {
  anomaly_dates: string[];
  category_palette: [string, string][]; // tuples of (name, hex color)
}

export interface CategoryDetailRowOut {
  vendor_name: string | null;
  description_sample: string;
  txn_count: number;
  total: string | number;
}

export interface CategoryDetailOut {
  category: string;
  color: string;
  total: string | number;
  txn_count: number;
  contributors: CategoryDetailRowOut[];
}

export interface InvestmentSchemeOut {
  scheme: string;
  invested: string | number;
  redeemed: string | number;
  net: string | number;
  txn_count: number;
}

export interface InvestmentActivityOut {
  window_start: string;
  window_end: string;
  invested_total: string | number;
  redeemed_total: string | number;
  net_invested: string | number;
  txn_count_in: number;
  txn_count_out: number;
  by_scheme: InvestmentSchemeOut[];
}

// ---------- Duplicates ----------

export interface DuplicateDocOut {
  id: string;
  original_filename: string;
  document_type: string;
  status: string;
  file_size_bytes: number;
  txn_count: number;
  total_debit: string | number;
  total_credit: string | number;
  min_date: string | null;
  max_date: string | null;
  uploaded_at: string;
  has_hash: boolean;
}

export interface DuplicateClusterOut {
  cluster_id: string;
  cluster_type: "exact" | "fuzzy";
  signature: string;
  docs: DuplicateDocOut[];
}

export interface DuplicateClustersOut {
  clusters: DuplicateClusterOut[];
  total_clusters: number;
  total_duplicate_docs: number;
}

export interface DeleteDuplicateOut {
  document_id: string;
  txns_deleted: number;
  invoices_unlinked: number;
  receipts_unlinked: number;
}

export interface BackfillHashesOut {
  processed: number;
  updated: number;
  skipped: number;
  errors: number;
}

// ---------- Tax ----------

export interface CounterpartyGSTINOut {
  id: string;
  role: "vendor" | "client";
  name: string;
  gstin_raw: string | null;
  is_valid: boolean;
  reason: string | null;
  state_code: string | null;
  state_name: string | null;
  pan: string | null;
}

export interface GSTINHealthOut {
  counterparties: CounterpartyGSTINOut[];
  total: number;
  valid: number;
  invalid: number;
  missing: number;
  compliance_pct: number;
}

export interface TaxInstallmentOut {
  label: string;
  due_date: string;
  cumulative_pct: number;
  cumulative_amount: string | number;
  this_installment: string | number;
  status: "upcoming" | "due_soon" | "overdue" | "complete";
  days_until_due: number;
}

export interface AdvanceTaxOut {
  fy_label: string;
  fy_start: string;
  fy_end: string;
  days_elapsed: number;
  days_remaining: number;
  revenue_ytd: string | number;
  expense_ytd: string | number;
  net_profit_ytd: string | number;
  projected_annual_profit: string | number;
  entity_type: string;
  estimated_tax_rate: number;
  estimated_annual_tax: string | number;
  installments: TaxInstallmentOut[];
  next_due: TaxInstallmentOut | null;
  total_overdue: string | number;
}

export interface VendorTDSRowOut {
  vendor_id: string;
  vendor_name: string;
  pan: string | null;
  section_code: string;
  section_label: string;
  fy_payments_total: string | number;
  threshold: string | number;
  has_crossed_threshold: boolean;
  applicable_rate: number;
  tds_amount_estimated: string | number;
  net_payable_after_tds: string | number;
  form_quarterly: string;
  deduction_status: string;
  notes: string | null;
}

export interface TDSDraftOut {
  fy_label: string;
  rows: VendorTDSRowOut[];
  total_vendors: number;
  vendors_crossed_threshold: number;
  total_tds_estimated: string | number;
}

// ---------- Cash forecast ----------

export interface CashForecastPointOut {
  date: string;            // YYYY-MM-DD
  days_from_now: number;
  pessimistic: string;     // INR, stored as string to preserve Decimal precision
  likely: string;
  optimistic: string;
  inflow: string;          // inflow on this day (likely scenario)
  outflow: string;
  actual: string | null;   // back-filled once date passes
}

export interface CashForecastOut {
  run_id: string;
  as_of_date: string;
  horizon_days: number;
  starting_cash_inr: string;
  ending_cash_likely_inr: string;
  ending_cash_pessimistic_inr: string;
  ending_cash_optimistic_inr: string;
  runway_zero_date: string | null;
  drivers_count: number;
  inflows_total_inr: string;
  outflows_total_inr: string;
  created_at: string;
  points: CashForecastPointOut[];
}

export type ForecastDriverKind =
  | "recurring_inflow"
  | "recurring_outflow"
  | "open_receivable"
  | "open_payable"
  | "scheduled_tax"
  | "opening_balance"
  | "one_off";

export interface ForecastDriverOut {
  id: string;
  kind: ForecastDriverKind;
  label: string;
  direction: "inflow" | "outflow";
  expected_date: string | null;
  expected_amount_inr: string;
  confidence: string;
  source_kind: string;
  vendor_id: string | null;
  client_id: string | null;
  supporting_data: Record<string, unknown> | null;
}

// ---------- Sessions + Team ----------

export interface SessionInfoOut {
  id: string;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  is_current: boolean;
}

export interface SessionListOut {
  sessions: SessionInfoOut[];
  total: number;
}

export interface MemberOut {
  id: string;
  email: string;
  role: string;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface InviteOut {
  id: string;
  email: string;
  role: string;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  invite_url: string;
  token: string;
}

export interface TeamOverviewOut {
  members: MemberOut[];
  pending_invites: InviteOut[];
}

export interface InviteCheckOut {
  org_name: string;
  email: string;
  role: string;
  expires_at: string;
  already_accepted: boolean;
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
  embedding_coverage: EmbeddingCoverageOut;
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

// ---------- Embeddings + semantic search ----------

export interface SearchHitOut {
  id: string;
  source: "bank_txn" | "invoice" | "receipt";
  txn_date: string | null;
  amount: string | null;
  direction: string | null;
  description: string;
  matched_vendor_id: string | null;
  category: string | null;
  distance: number | null;
  document_id: string | null;
  vendor_name: string | null;
  invoice_number: string | null;
}

export interface SearchOut {
  query: string;
  enabled: boolean;
  count: number;
  hits: SearchHitOut[];
}

export interface EmbeddingCoverageOut {
  enabled: boolean;
  total: number;
  embedded: number;
  coverage_pct: number;
}

export interface BackfillEmbeddingsOut {
  enabled: boolean;
  embedded: number;
  total: number;
  skipped_reason?: string | null;
}

// ---------- Q&A ----------

export interface AskOut {
  question: string;
  sql: string | null;
  row_count: number;
  sample: Record<string, unknown>[];
  answer: string;
}

export interface DashboardSummaryOut {
  cash_position: KpiOut;
  receivables: KpiOut;
  payables: KpiOut;
  net_flow_mtd: KpiOut;
  cash_flow: CashFlowPointOut[];
  cash_flow_by_category?: CashFlowCategoryPointOut[];
  cash_flow_meta?: CashFlowMetaOut;
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
