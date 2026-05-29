export type Holding = {
  fund_code: string;
  fund_name: string;
  holding_amount: number;
  return_percent: number;
  daily_profit?: number | null;
  user_note?: string | null;
};

export type InvestorProfile = {
  style: string;
  horizon: string;
  max_drawdown_percent: number;
  concentration_limit_percent: number;
  prefer_dca: boolean;
  avoid_chasing: boolean;
};

export type RiskAlert = {
  code: string;
  severity: "low" | "medium" | "high";
  message: string;
  evidence: string;
};

export type Report = {
  id: string;
  created_at: string;
  title: string;
  risk: {
    level: "low" | "medium" | "high";
    suggested_action: "watch" | "pause_add" | "staggered_add" | "risk_review";
    weighted_return_percent: number;
    alerts: RiskAlert[];
  };
  holdings: Holding[];
  snapshots: Array<{
    fund_code: string;
    fund_name: string;
    latest_nav?: number | null;
    nav_date?: string | null;
    source: string;
    note?: string | null;
  }>;
  summary: string;
  recommendations: string[];
  caveats: string[];
  provider: string;
};

export type OcrResponse = {
  raw_text: string;
  upload_path: string | null;
  holdings: Holding[];
  error?: string;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function parseOcr(formData: FormData): Promise<OcrResponse> {
  const response = await fetch(`${API_BASE}/api/ocr`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function analyzeHoldings(
  holdings: Holding[],
  profile: InvestorProfile,
  ocrText?: string,
): Promise<Report> {
  const response = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ holdings, profile, ocr_text: ocrText }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function listReports(): Promise<Report[]> {
  const response = await fetch(`${API_BASE}/api/reports`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}
