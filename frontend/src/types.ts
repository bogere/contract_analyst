export type Issue = {
  title: string;
  severity: "red" | "yellow" | "green";
  clause: string;
  quote: string;
  explanation: string;
  suggestion: string;
  redline?: string;
};

export type Protection = {
  title: string;
  clause: string;
  explanation: string;
};

export type StatuteCheck = {
  rule_id: string;
  title: string;
  basis: string;
  status: "violation" | "ok" | "unknown";
  detail: string;
  quote: string;
};

export type AnalysisResult = {
  contract_type: string;
  summary: string;
  parties: string[];
  key_terms: string[];
  red_flags: Issue[];
  warnings: Issue[];
  good_clauses: Protection[];
  missing_protections: string[];
  statute_checks: StatuteCheck[];
  fairness_score: number;
  fairness_grade: string;
};

export type SessionUser = {
  id: string;
  email: string;
  is_admin: boolean;
};

export type BillingPlan = {
  key: "free" | "pro" | "business";
  name: string;
  price: string;
  monthly_limit: number;
  description: string;
  available: boolean;
};

export type BillingInfo = {
  current_plan_key: BillingPlan["key"];
  current_plan_name: string;
  subscription_status: string;
  pending_plan_key: "pro" | "business" | null;
  monthly_usage: number;
  monthly_limit: number;
  next_billing_time: string | null;
  paypal_enabled: boolean;
  plans: BillingPlan[];
};

export type AdminUserSummary = SessionUser & {
  created_at: string;
};

export type AdminSettings = {
  model: string | null;
  effective_model: string;
  base_url: string | null;
  has_saved_api_key: boolean;
  has_environment_api_key: boolean;
  language: "en" | "zh";
  jurisdiction: "auto" | "cn" | "us-ca";
};

export type ReportSummary = {
  id: string;
  filename: string;
  contract_type: string;
  fairness_score: number;
  fairness_grade: string;
  created_at: string;
};

export type SavedReport = {
  id: string;
  filename: string;
  result: AnalysisResult;
  created_at: string;
};

export type ResultTab = "overview" | "red-flags" | "warnings" | "protections" | "statutes";
