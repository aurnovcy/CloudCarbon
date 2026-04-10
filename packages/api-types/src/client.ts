import type { components } from "./index.js";

// ---------------------------------------------------------------------------
// Re-export canonical types for consumers
// ---------------------------------------------------------------------------

export type AuthToken = components["schemas"]["AuthToken"];
export type User = components["schemas"]["User"];
export type CloudAccount = components["schemas"]["CloudAccount"];
export type IngestionResult = components["schemas"]["IngestionResult"];
export type Recommendation = components["schemas"]["Recommendation"];
export type RecommendationSummary = components["schemas"]["RecommendationSummary"];
export type AgentConfig = components["schemas"]["AgentConfig"];
export type AgentRun = components["schemas"]["AgentRun"];
export type OverviewReport = components["schemas"]["OverviewReport"];
export type CarbonReport = components["schemas"]["CarbonReport"];
export type WaterReport = components["schemas"]["WaterReport"];
export type ForecastResult = components["schemas"]["ForecastResult"];
export type NLQueryResult = components["schemas"]["NLQueryResult"];
export type PolicyRule = components["schemas"]["PolicyRule"];
export type CloudCarbonRecord = components["schemas"]["CloudCarbonRecord"];

// ---------------------------------------------------------------------------
// Client configuration and error types
// ---------------------------------------------------------------------------

export interface ClientConfig {
  baseUrl: string;
  getToken: () => string | null;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

// ---------------------------------------------------------------------------
// Core request helper
// ---------------------------------------------------------------------------

async function request<T>(
  config: ClientConfig,
  method: string,
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
  body?: unknown
): Promise<T> {
  const url = new URL(`${config.baseUrl}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined) url.searchParams.set(k, String(v));
    });
  }
  const token = config.getToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(url.toString(), {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// API client factory
// ---------------------------------------------------------------------------

export function createClient(config: ClientConfig) {
  return {
    auth: {
      login: (email: string, password: string) =>
        request<AuthToken>(config, "POST", "/auth/token", undefined, {
          grant_type: "password",
          username: email,
          password,
        }),
      refresh: (refreshToken: string) =>
        request<AuthToken>(config, "POST", "/auth/refresh", undefined, {
          refresh_token: refreshToken,
        }),
      logout: () => request<void>(config, "POST", "/auth/logout"),
      me: () => request<User>(config, "GET", "/auth/me"),
    },

    accounts: {
      list: () =>
        request<{ accounts: CloudAccount[]; total: number }>(config, "GET", "/accounts"),
      create: (data: {
        name: string;
        provider: "aws" | "azure" | "gcp" | "alibaba";
        account_identifier: string;
        config?: Record<string, unknown>;
        credentials?: Record<string, string>;
      }) => request<CloudAccount>(config, "POST", "/accounts", undefined, data),
      delete: (accountId: string) =>
        request<void>(config, "DELETE", `/accounts/${accountId}`),
    },

    ingest: {
      upload: (file: File, accountId: string) => {
        const token = config.getToken();
        const formData = new FormData();
        formData.append("file", file);
        return fetch(`${config.baseUrl}/ingest/upload?account_id=${accountId}`, {
          method: "POST",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: formData,
        }).then((r) =>
          r.ok
            ? (r.json() as Promise<IngestionResult>)
            : r.text().then((t) => Promise.reject(new ApiError(r.status, t)))
        );
      },
      sync: (accountId: string) =>
        request<{ status: string; account_id: string; job_id: string }>(
          config, "POST", `/ingest/sync/${accountId}`
        ),
      syncStatus: (jobId: string) =>
        request<{
          job_id: string;
          account_id?: string | null;
          status: "pending" | "running" | "completed" | "failed";
          started_at?: string | null;
          completed_at?: string | null;
          result?: IngestionResult | null;
          error?: string | null;
        }>(config, "GET", `/ingest/sync/${jobId}/status`),
    },

    enrichment: {
      run: (data: {
        tenant_id: string;
        mode?: "incremental" | "full";
        enrichment_version?: string;
        limit?: number;
      }) =>
        request<{
          job_id: string;
          tenant_id: string;
          mode: string;
          status: string;
          queued_at: string;
        }>(config, "POST", "/enrichment/run", undefined, data),
      status: (jobId: string) =>
        request<{
          job_id: string;
          status: "queued" | "running" | "completed" | "failed";
          processed: number;
          total: number;
          pct: number;
          failed: number;
          errors: string[];
          started_at?: string | null;
          completed_at?: string | null;
          duration_seconds?: number | null;
        }>(config, "GET", `/enrichment/status/${jobId}`),
      summary: () =>
        request<{
          tenant_id: string;
          total_records_enriched: number;
          total_focus_records: number;
          coverage_pct: number;
          total_co2e_kg: number;
          total_scope3_co2e_kg: number;
          scope3_pct_of_total: number;
          total_water_litres: number;
          last_enriched_at?: string | null;
          enrichment_version?: string | null;
        }>(config, "GET", "/enrichment/summary"),
    },

    recommendations: {
      list: (params?: {
        status?: "open" | "implemented" | "dismissed" | "snoozed";
        type?: string;
        provider?: string;
        region?: string;
        min_cost_impact?: number;
        min_co2e_impact?: number;
        sort_by?: "impact_score" | "cost" | "co2e" | "water";
        mode?: "cost" | "sustainability";
        limit?: number;
        offset?: number;
      }) =>
        request<{ items: Recommendation[]; total: number; page: number; page_size: number }>(
          config,
          "GET",
          "/recommendations",
          params as Record<string, string | number | boolean | undefined>
        ),
      update: (
        id: string,
        data: { status: "implemented" | "dismissed" | "snoozed" | "open"; snooze_until?: string }
      ) => request<Recommendation>(config, "PATCH", `/recommendations/${id}`, undefined, data),
      run: (weights?: { cost_weight: number; carbon_weight: number; water_weight: number }) =>
        request<{ job_id: string; tenant_id: string; status: string; queued_at: string }>(
          config, "POST", "/recommendations/run", undefined, weights
        ),
      summary: () => request<RecommendationSummary>(config, "GET", "/recommendations/summary"),
    },

    agents: {
      list: () => request<AgentConfig[]>(config, "GET", "/agents"),
      update: (
        agentType: string,
        data: Partial<Pick<AgentConfig, "enabled" | "dry_run" | "schedule_cron" | "config">>
      ) => request<AgentConfig>(config, "PATCH", `/agents/${agentType}`, undefined, data),
      run: (agentType: string, dryRun = true, configOverride?: Record<string, unknown>) =>
        request<{
          run_id: string;
          agent_type: string;
          status: string;
          dry_run: boolean;
          started_at: string;
        }>(config, "POST", `/agents/${agentType}/run`, undefined, {
          dry_run: dryRun,
          config_override: configOverride,
        }),
      runs: (params?: {
        agent_type?: string;
        status?: string;
        page?: number;
        page_size?: number;
      }) =>
        request<{ items: AgentRun[]; total: number; limit: number; offset: number }>(
          config,
          "GET",
          "/agents/runs",
          params as Record<string, string | number | boolean | undefined>
        ),
      runDetail: (runId: string) =>
        request<AgentRun>(config, "GET", `/agents/runs/${runId}`),
      approve: (runId: string, actionId: string, notes?: string) =>
        request<{
          action_id: string;
          status: string;
          approved_by: string;
          approved_at: string;
          result?: Record<string, unknown> | null;
        }>(config, "POST", `/agents/runs/${runId}/approve`, undefined, {
          action_id: actionId,
          notes,
        }),
    },

    reports: {
      overview: (params?: {
        start_date?: string;
        end_date?: string;
        provider?: string;
        mode?: "cost" | "sustainability";
      }) =>
        request<OverviewReport>(
          config, "GET", "/reports/overview",
          params as Record<string, string | number | boolean | undefined>
        ),
      carbon: (params?: {
        start_date?: string;
        end_date?: string;
        provider?: string;
        granularity?: "daily" | "weekly" | "monthly";
      }) =>
        request<CarbonReport>(
          config, "GET", "/reports/carbon",
          params as Record<string, string | number | boolean | undefined>
        ),
      water: (params?: { start_date?: string; end_date?: string; provider?: string }) =>
        request<WaterReport>(
          config, "GET", "/reports/water",
          params as Record<string, string | number | boolean | undefined>
        ),
      forecast: (params?: { metrics?: string; horizon_days?: number }) =>
        request<Record<string, ForecastResult>>(
          config, "GET", "/reports/forecast",
          params as Record<string, string | number | boolean | undefined>
        ),
      costCarbon: (params?: {
        start_date?: string;
        end_date?: string;
        provider?: string;
        granularity?: "daily" | "weekly" | "monthly";
      }) =>
        request<{
          period: { start: string; end: string };
          by_service: {
            service_name: string;
            service_category: string;
            cost_usd: number;
            co2e_kg: number;
            carbon_efficiency: number;
          }[];
          by_region: { region: string; provider: string; cost_usd: number; co2e_kg: number }[];
          time_series: { date: string; cost_usd: number; co2e_kg: number }[];
        }>(
          config, "GET", "/reports/cost-carbon",
          params as Record<string, string | number | boolean | undefined>
        ),
    },

    query: {
      natural: (question: string) =>
        request<NLQueryResult>(config, "POST", "/query/natural-language", undefined, { question }),
    },

    policies: {
      list: () => request<PolicyRule[]>(config, "GET", "/policies"),
      create: (data: {
        name: string;
        description?: string;
        rule_type:
          | "exclude_region"
          | "min_co2e_threshold"
          | "require_approval"
          | "water_stress_block";
        condition: Record<string, unknown>;
        active?: boolean;
      }) => request<PolicyRule>(config, "POST", "/policies", undefined, data),
    },
  };
}
