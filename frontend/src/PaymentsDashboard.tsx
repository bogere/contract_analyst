import { useEffect, useMemo, useState } from "react";
import {
  createAiHarness,
} from "ag-studio";
import type {
  AgStudioApi,
  AgAiHarnessSetup,
  AgAiPromptStarter,
  AgDataSourcesDefinition,
  AgReportState,
} from "ag-studio";
import { AgStudioAiModule } from "ag-studio";
import { AgStudio, AgStudioProvider } from "ag-studio-react";
import { createPaymentsAiAdapter } from "./paymentsAiAdapter";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const STUDIO_LICENSE_KEY = import.meta.env.VITE_AG_STUDIO_LICENSE_KEY?.trim();

type PaymentsAnalytics = {
  snapshot_at: string;
  accounts: number;
  paid_active_subscribers: number;
  estimated_mrr: number;
  upcoming_renewal_count: number;
  plans: Array<{
    plan_name: string;
    subscriber_count: number;
    active_paid_subscribers: number;
    monthly_price: number;
    estimated_mrr: number;
  }>;
  upcoming_renewals: Array<{
    renewal_date: string;
    plan_name: string;
    renewal_count: number;
  }>;
};

function isPaymentsAnalytics(value: unknown): value is PaymentsAnalytics {
  if (typeof value !== "object" || value === null) return false;
  const analytics = value as Record<string, unknown>;
  return typeof analytics.snapshot_at === "string"
    && typeof analytics.accounts === "number"
    && typeof analytics.paid_active_subscribers === "number"
    && typeof analytics.estimated_mrr === "number"
    && typeof analytics.upcoming_renewal_count === "number"
    && Array.isArray(analytics.plans)
    && analytics.plans.every((plan: unknown) =>
      typeof plan === "object" && plan !== null
      && "plan_name" in plan && typeof plan.plan_name === "string"
      && "subscriber_count" in plan && typeof plan.subscriber_count === "number"
      && "active_paid_subscribers" in plan && typeof plan.active_paid_subscribers === "number"
      && "monthly_price" in plan && typeof plan.monthly_price === "number"
      && "estimated_mrr" in plan && typeof plan.estimated_mrr === "number")
    && Array.isArray(analytics.upcoming_renewals)
    && analytics.upcoming_renewals.every((renewal: unknown) =>
      typeof renewal === "object" && renewal !== null
      && "renewal_date" in renewal && typeof renewal.renewal_date === "string"
      && "plan_name" in renewal && typeof renewal.plan_name === "string"
      && "renewal_count" in renewal && typeof renewal.renewal_count === "number");
}

const promptStarters: AgAiPromptStarter[] = [
  {
    label: "Subscribers by plan",
    prompt: "Show active subscribers by plan and explain the current mix.",
  },
  {
    label: "Estimate MRR",
    prompt: "Estimate monthly recurring revenue by plan. Make clear this is not collected cash.",
  },
  {
    label: "Upcoming renewals",
    prompt: "Chart scheduled renewals by plan over the next 30 days.",
  },
];

const initialState: AgReportState = {
  pages: [{
    id: "payments-overview",
    widgets: {
      "estimated-mrr": {
        type: "value",
        dataMapping: { value: [{ id: "plans.estimated_mrr", aggregation: "sum" }] },
        format: {
          style: {
            label: { enabled: true, text: "Estimated monthly recurring revenue" },
          },
        },
      },
      "paid-subscribers": {
        type: "value",
        dataMapping: { value: [{ id: "plans.active_paid_subscribers", aggregation: "sum" }] },
        format: {
          style: {
            label: { enabled: true, text: "Active paid subscribers" },
          },
        },
      },
      "upcoming-renewals": {
        type: "value",
        dataMapping: { value: [{ id: "renewals.renewal_count", aggregation: "sum" }] },
        format: {
          style: {
            label: { enabled: true, text: "Scheduled renewals · next 30 days" },
          },
        },
      },
      "mrr-by-plan": {
        type: "bar-chart-grouped",
        dataMapping: {
          categoryKey: [{ id: "plans.plan_name" }],
          valueKey: [{ id: "plans.estimated_mrr", aggregation: "sum" }],
        },
        format: { title: { enabled: true, text: "Estimated MRR by plan" } },
      },
      "subscribers-by-plan": {
        type: "bar-chart-grouped",
        dataMapping: {
          categoryKey: [{ id: "plans.plan_name" }],
          valueKey: [{ id: "plans.subscriber_count", aggregation: "sum" }],
        },
        format: { title: { enabled: true, text: "Subscriber accounts by plan" } },
      },
      "renewals-grid": {
        type: "grid",
        dataMapping: {
          cols: [
            { id: "renewals.renewal_date" },
            { id: "renewals.plan_name" },
            { id: "renewals.renewal_count", aggregation: "sum" },
          ],
        },
        format: { title: { enabled: true, text: "Scheduled renewals · next 30 days" } },
      },
    },
    widgetLayout: {
      "estimated-mrr": { xTrack: 0, yTrack: 0, xSpan: 8, ySpan: 6 },
      "paid-subscribers": { xTrack: 8, yTrack: 0, xSpan: 8, ySpan: 6 },
      "upcoming-renewals": { xTrack: 16, yTrack: 0, xSpan: 8, ySpan: 6 },
      "mrr-by-plan": { xTrack: 0, yTrack: 6, xSpan: 12, ySpan: 16 },
      "subscribers-by-plan": { xTrack: 12, yTrack: 6, xSpan: 12, ySpan: 16 },
      "renewals-grid": { xTrack: 0, yTrack: 22, xSpan: 24, ySpan: 16 },
    },
  }],
  selectedPageId: "payments-overview",
};

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

export default function PaymentsDashboard() {
  const [analytics, setAnalytics] = useState<PaymentsAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshVersion, setRefreshVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function loadAnalytics() {
      setLoading(true);
      setError("");
      try {
        const response = await fetch(`${API_URL}/api/admin/payments/analytics`, {
          credentials: "include",
          signal: controller.signal,
        });
        const body: unknown = await response.json();
        if (!response.ok) {
          const detail = typeof body === "object" && body !== null && "detail" in body
            && typeof body.detail === "string"
            ? body.detail
            : "Unable to load payment analytics.";
          throw new Error(detail);
        }
        if (!isPaymentsAnalytics(body)) throw new Error("The payments analytics response was invalid.");
        setAnalytics(body);
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "Unable to load payment analytics.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void loadAnalytics();
    return () => controller.abort();
  }, [refreshVersion]);

  const data = useMemo<AgDataSourcesDefinition | undefined>(() => {
    if (!analytics) return undefined;
    return {
      description:
        "Read-only subscription aggregates for Contract Analyst. `plans` has current account counts by plan, active paid subscriber counts, and estimated monthly recurring revenue based on active paid subscriptions at configured monthly prices. `renewals` has counts of stored next-billing dates in the 30 days after the snapshot. This is not a transaction ledger: collected cash, payment failures, refunds, and historical churn are not available. The data contains no account emails or subscription identifiers.",
      sources: [
        {
          id: "plans",
          name: "Plan summary",
          description: "Account counts by current plan, active paid subscribers by plan, and estimated MRR (active paid subscribers multiplied by monthly plan price).",
          data: analytics.plans,
          fields: [
            { id: "plan_name", name: "Plan", format: "textFormat" },
            { id: "subscriber_count", name: "Subscriber accounts", format: "integerFormat" },
            { id: "active_paid_subscribers", name: "Active paid subscribers", format: "integerFormat" },
            { id: "monthly_price", name: "Monthly price", format: "currencyFormat" },
            { id: "estimated_mrr", name: "Estimated MRR", format: "currencyFormat" },
          ],
        },
        {
          id: "renewals",
          name: "Upcoming renewals",
          description: "Aggregated scheduled renewal counts by date and plan for the 30 days after the snapshot.",
          data: analytics.upcoming_renewals,
          fields: [
            { id: "renewal_date", name: "Renewal date", format: "dateFormat" },
            { id: "plan_name", name: "Plan", format: "textFormat" },
            { id: "renewal_count", name: "Scheduled renewals", format: "integerFormat" },
          ],
        },
      ],
    };
  }, [analytics]);

  const ai = useMemo<AgAiHarnessSetup | undefined>(() => {
    if (!analytics) return undefined;
    const adapter = createPaymentsAiAdapter(API_URL, analytics);
    // AG Studio 3.1 types this callback with the core API, but createAiHarness requires its full UI API.
    return ({ api }) => createAiHarness(api as AgStudioApi, { adapter, promptStarters });
  }, [analytics]);

  return (
    <section className="payments-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">ADMIN WORKSPACE</p>
          <h1>Payments dashboard</h1>
          <p className="intro">Explore subscription health and estimated recurring revenue.</p>
        </div>
        <div className="payments-heading-actions">
          {analytics && (
            <div className="payments-snapshot">
              Snapshot refreshed {new Date(analytics.snapshot_at).toLocaleString()}
            </div>
          )}
          <button
            className="payments-refresh-button"
            type="button"
            onClick={() => setRefreshVersion((version) => version + 1)}
            disabled={loading}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && <div className="error-message payments-error" role="alert">{error}</div>}
      {loading ? (
        <div className="panel payments-loading"><span className="spinner sidebar-spinner" /> Loading subscription analytics…</div>
      ) : analytics && data ? (
        <>
          <div className="payments-metrics" aria-label="Subscription summary">
            <article className="panel payments-metric">
              <span className="payments-metric-icon" aria-hidden="true">$</span>
              <span><small>ESTIMATED MONTHLY RECURRING REVENUE</small><strong>{formatCurrency(analytics.estimated_mrr)}</strong></span>
            </article>
            <article className="panel payments-metric">
              <span className="payments-metric-icon" aria-hidden="true">↗</span>
              <span><small>ACTIVE PAID SUBSCRIBERS</small><strong>{analytics.paid_active_subscribers}</strong></span>
            </article>
            <article className="panel payments-metric">
              <span className="payments-metric-icon" aria-hidden="true">◷</span>
              <span><small>SCHEDULED RENEWALS · NEXT 30 DAYS</small><strong>{analytics.upcoming_renewal_count}</strong></span>
            </article>
            <article className="panel payments-metric">
              <span className="payments-metric-icon" aria-hidden="true">◎</span>
              <span><small>WORKSPACE ACCOUNTS</small><strong>{analytics.accounts}</strong></span>
            </article>
          </div>

          <div className="payments-disclaimer" role="note">
            MRR is an estimate based on active paid plans and configured monthly prices—not collected cash.
            This app does not retain transaction, refund, payment-failure, or historical churn data.
          </div>

          <div className="panel payments-studio-panel">
            <div className="payments-studio-heading">
              <div>
                <h2>Explore with AG Studio</h2>
                <p>Ask a question in the assistant or edit the starter widgets to build your own view.</p>
              </div>
              <span className="payments-readonly-badge">Aggregated · read-only</span>
            </div>
            <div className="payments-studio">
              <AgStudioProvider
                modules={[AgStudioAiModule]}
                licenseKey={STUDIO_LICENSE_KEY || undefined}
              >
                <AgStudio
                  className="payments-ag-studio"
                  style={{ height: "100%", width: "100%" }}
                  data={data}
                  initialState={initialState}
                  mode="edit"
                  ai={ai}
                />
              </AgStudioProvider>
            </div>
          </div>
          <p className="payments-data-note">
            The assistant uses the workspace AI provider configured by the administrator. Only aggregate
            subscription metrics are shared; account emails and PayPal subscription identifiers are excluded.
          </p>
        </>
      ) : (
        <div className="panel payments-empty">
          <p>Subscription analytics are unavailable right now.</p>
          <button className="analyze-button" type="button" onClick={() => setRefreshVersion((version) => version + 1)}>
            Retry
          </button>
        </div>
      )}
    </section>
  );
}
