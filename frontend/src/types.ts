/** Domain types mirroring the backend Pydantic schemas. */

export type DocumentStatus =
  | "received"
  | "extracting"
  | "extracted"
  | "understood"
  | "indexed"
  | "error";

export type FileType = "pdf" | "image" | "csv" | "xlsx";

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
  // Backend uses in_amount/out_amount as field names because `in` is a
  // Python keyword. Dashboard transforms these to {in, out} for the chart.
  in_amount: number;
  out_amount: number;
  net: number;
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
  has_any_data: boolean;
  bank_txn_count: number;
}
