import { useEffect, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import type { AnalysisResult, Issue, ReportSummary, ResultTab, SavedReport, SessionUser } from "./types";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const MAX_FILE_BYTES = 20 * 1024 * 1024;
const ACCEPTED = ".pdf,.docx,.txt,.md,.rtf";

const tabs: { id: ResultTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "red-flags", label: "Red flags" },
  { id: "warnings", label: "Warnings" },
  { id: "protections", label: "Protections" },
  { id: "statutes", label: "Statute checks" },
];

function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function App() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [user, setUser] = useState<SessionUser | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [sessionError, setSessionError] = useState("");
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [language, setLanguage] = useState("en");
  const [jurisdiction, setJurisdiction] = useState("auto");
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [resultFilename, setResultFilename] = useState("");
  const [activeTab, setActiveTab] = useState<ResultTab>("overview");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    fetch(`${API_URL}/api/auth/me`, { credentials: "include" })
      .then(async (response) => {
        if (!active) return;
        if (response.ok) setUser(await response.json() as SessionUser);
        else if (response.status !== 401) setSessionError("Unable to connect to the account service.");
      })
      .catch(() => {
        if (active) setSessionError("Unable to connect to the account service.");
      })
      .finally(() => {
        if (active) setSessionLoading(false);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!user) {
      setReports([]);
      return;
    }
    fetch(`${API_URL}/api/reports`, { credentials: "include" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Unable to load saved reports.");
        setReports(await response.json() as ReportSummary[]);
        setHistoryError("");
      })
      .catch((cause: unknown) => {
        setHistoryError(cause instanceof Error ? cause.message : "Unable to load saved reports.");
      });
  }, [user]);

  function selectFile(nextFile?: File) {
    if (!nextFile) return;
    const extension = nextFile.name.split(".").pop()?.toLowerCase();
    if (!extension || !["pdf", "docx", "txt", "md", "rtf"].includes(extension)) {
      setError("Please choose a PDF, DOCX, TXT, Markdown, or RTF document.");
      return;
    }
    if (nextFile.size > MAX_FILE_BYTES) {
      setError("This file is larger than the 20 MiB upload limit.");
      return;
    }
    setError("");
    setResult(null);
    setFile(nextFile);
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    selectFile(event.target.files?.[0]);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    selectFile(event.dataTransfer.files[0]);
  }

  async function analyze() {
    if (!file) return;
    setLoading(true);
    setError("");
    setResult(null);
    const payload = new FormData();
    payload.append("file", file);
    if (model.trim()) payload.append("model", model.trim());
    payload.append("api_key", apiKey);
    payload.append("base_url", baseUrl);
    payload.append("lang", language);
    payload.append("jurisdiction", jurisdiction);

    try {
      const response = await fetch(`${API_URL}/api/analyze`, { method: "POST", body: payload, credentials: "include" });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.detail || "The contract could not be analyzed.");
      }
      setResult(body as AnalysisResult);
      setResultFilename(file.name);
      setActiveTab("overview");
      const reportsResponse = await fetch(`${API_URL}/api/reports`, { credentials: "include" });
      if (reportsResponse.ok) setReports(await reportsResponse.json() as ReportSummary[]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to connect to ContractGuard.");
    } finally {
      setLoading(false);
    }
  }

  async function openReport(report: ReportSummary) {
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/reports/${report.id}`, { credentials: "include" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Unable to open this report.");
      const saved = body as SavedReport;
      setResult(saved.result);
      setResultFilename(saved.filename);
      setActiveTab("overview");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to open this report.");
    }
  }

  async function signOut() {
    try {
      const response = await fetch(`${API_URL}/api/auth/logout`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) throw new Error("Unable to sign out. Please try again.");
      setUser(null);
      setResult(null);
      setFile(null);
      setApiKey("");
      setBaseUrl("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to sign out.");
    }
  }

  if (sessionLoading) {
    return <main className="auth-shell"><span className="spinner auth-spinner" /></main>;
  }
  if (!user) {
    return <AuthScreen
      initialError={sessionError}
      onAuthenticated={(authenticatedUser) => {
        setSessionError("");
        setUser(authenticatedUser);
      }}
    />;
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="ContractGuard home">
          <span className="brand-mark">C</span>
          <span>contract<span className="brand-light">guard</span></span>
        </a>
        <div className="topbar-right">
          <span className="privacy-note"><span className="privacy-dot" /> Reports saved to your account</span>
          <span className="account-email">{user.email}</span>
          <button className="signout-button" type="button" onClick={() => void signOut()}>Sign out</button>
        </div>
      </header>

      <div className="workspace">
        <section className="page-heading">
          <div>
            <p className="eyebrow">CONTRACT INTELLIGENCE</p>
            <h1>Know what you’re signing.</h1>
            <p className="intro">A clearer view of the terms, risks, and protections in your contract.</p>
          </div>
          <div className="secure-badge"><ShieldIcon /> Temporary file handling</div>
        </section>

        <div className="main-grid">
          <aside className="panel upload-panel">
            <div className="panel-heading">
              <div>
                <span className="step-label">01 / DOCUMENT</span>
                <h2>Upload a contract</h2>
              </div>
              <span className="step-number">01</span>
            </div>

            <div
              className={`dropzone${dragging ? " is-dragging" : ""}${file ? " has-file" : ""}`}
              onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
              }}
              role="button"
              tabIndex={0}
            >
              <input ref={inputRef} type="file" accept={ACCEPTED} onChange={onFileChange} hidden />
              <div className="upload-icon"><UploadIcon /></div>
              {file ? (
                <>
                  <strong className="file-name">{file.name}</strong>
                  <span>{formatBytes(file.size)} · Ready to review</span>
                  <button
                    className="text-button"
                    type="button"
                    onClick={(event) => { event.stopPropagation(); setFile(null); setResult(null); }}
                  >Remove document</button>
                </>
              ) : (
                <>
                  <strong>Drop your file here</strong>
                  <span>or <span className="browse-link">browse files</span></span>
                  <small>PDF, DOCX, TXT, MD or RTF · Up to 20 MB</small>
                </>
              )}
            </div>

            <div className="settings-heading">
              <span className="step-label">02 / PREFERENCES</span>
              <h2>Analysis settings</h2>
            </div>
            <label className="field-label" htmlFor="model">AI model <span className="optional">Optional</span></label>
            <input
              id="model"
              className="text-input"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="Uses backend model setting if blank"
            />
            <label className="field-label" htmlFor="api-key">API key <span className="optional">Optional</span></label>
            <input
              id="api-key"
              className="text-input"
              type="password"
              autoComplete="off"
              placeholder="Uses server key if left blank"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
            <label className="field-label" htmlFor="base-url">OpenAI-compatible API URL <span className="optional">Optional</span></label>
            <input
              id="base-url"
              className="text-input"
              type="url"
              autoComplete="url"
              placeholder="Uses server setting if left blank"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
            />
            <div className="field-row">
              <div>
                <label className="field-label" htmlFor="language">Report language</label>
                <select id="language" className="text-input" value={language} onChange={(event) => setLanguage(event.target.value)}>
                  <option value="en">English</option>
                  <option value="zh">中文</option>
                </select>
              </div>
              <div>
                <label className="field-label" htmlFor="jurisdiction">Legal jurisdiction</label>
                <select id="jurisdiction" className="text-input" value={jurisdiction} onChange={(event) => setJurisdiction(event.target.value)}>
                  <option value="auto">Auto-detect</option>
                  <option value="cn">China</option>
                  <option value="us-ca">California, US</option>
                </select>
              </div>
            </div>

            {error && <div className="error-message" role="alert">{error}</div>}
            <button className="analyze-button" type="button" disabled={!file || loading} onClick={analyze}>
              {loading ? <><span className="spinner" /> Analyzing contract…</> : <>Analyze contract <ArrowIcon /></>}
            </button>
            <section className="saved-reports" aria-label="Saved analyses">
              <h3>Saved analyses</h3>
              {historyError && <p className="error-message" role="alert">{historyError}</p>}
              {!reports.length ? <p className="history-empty">Your completed analyses will appear here.</p> : (
                <ul>
                  {reports.map((report) => (
                    <li key={report.id}>
                      <button type="button" onClick={() => void openReport(report)}>
                        <span><strong>{report.filename}</strong><small>{new Date(report.created_at).toLocaleDateString()} · {report.contract_type.replace(/_/g, " ")}</small></span>
                        <span className="history-grade">{report.fairness_grade}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <p className="disclaimer">AI analysis is educational, not legal advice. Consult a licensed attorney for legal decisions.</p>
          </aside>

          <section className="results-column" aria-live="polite">
            {result ? (
              <AnalysisView result={result} activeTab={activeTab} setActiveTab={setActiveTab} filename={resultFilename || file?.name || "Contract"} />
            ) : (
              <EmptyResults loading={loading} />
            )}
          </section>
        </div>
        <footer className="footer"><span>CONTRACTGUARD AI</span><span>Understand the fine print. Make your next move with confidence.</span></footer>
      </div>
    </main>
  );
}

function AuthScreen({ initialError, onAuthenticated }: {
  initialError: string;
  onAuthenticated: (user: SessionUser) => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(initialError);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, password }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Unable to sign in.");
      setPassword("");
      onAuthenticated(body as SessionUser);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to connect to the account service.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <a className="brand auth-brand" href="/" aria-label="ContractGuard home">
          <span className="brand-mark">C</span>
          <span>contract<span className="brand-light">guard</span></span>
        </a>
        <p className="eyebrow">PRIVATE CONTRACT REVIEW</p>
        <h1>{mode === "login" ? "Welcome back." : "Create your account."}</h1>
        <p className="auth-intro">Sign in to review contracts and keep your analysis reports together.</p>
        <form onSubmit={(event) => void submit(event)}>
          <label className="field-label" htmlFor="auth-email">Email</label>
          <input
            className="text-input"
            id="auth-email"
            type="email"
            autoComplete="email"
            required
            maxLength={320}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <label className="field-label" htmlFor="auth-password">Password</label>
          <input
            className="text-input"
            id="auth-password"
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
            minLength={12}
            maxLength={128}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {error && <div className="error-message" role="alert">{error}</div>}
          <button className="analyze-button auth-submit" type="submit" disabled={busy}>
            {busy ? <><span className="spinner" /> Please wait…</> : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
        <p className="auth-switch">
          {mode === "login" ? "New to ContractGuard?" : "Already have an account?"}{" "}
          <button type="button" onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError("");
          }}>
            {mode === "login" ? "Create account" : "Sign in"}
          </button>
        </p>
        <p className="auth-privacy">Uploaded contracts and extracted text are deleted after analysis. Only your account and analysis report are saved.</p>
      </section>
    </main>
  );
}

function EmptyResults({ loading }: { loading: boolean }) {
  return (
    <div className={`empty-state${loading ? " analyzing" : ""}`}>
      <div className="document-illustration">
        <div className="doc-back" />
        <div className="doc-front">
          <span className="doc-line short" /><span className="doc-line" /><span className="doc-line" />
          <span className="doc-line medium" /><span className="doc-highlight" />
          <span className="doc-line" /><span className="doc-line short" />
        </div>
        <span className="sparkle sparkle-one">✦</span><span className="sparkle sparkle-two">✧</span>
      </div>
      <p className="eyebrow">{loading ? "REVIEW IN PROGRESS" : "YOUR ANALYSIS, AT A GLANCE"}</p>
      <h2>{loading ? "Reading between the lines." : "Your review starts here."}</h2>
      <p>{loading ? "ContractGuard is examining clauses and checking for potential risks." : "Upload a contract to see a plain-language summary, key risks, and the protections that matter."}</p>
      <div className="empty-features">
        <span><i className="feature-dot red-dot" /> Potential risks</span>
        <span><i className="feature-dot gold-dot" /> Terms to review</span>
        <span><i className="feature-dot green-dot" /> Protections found</span>
      </div>
    </div>
  );
}

function AnalysisView({ result, activeTab, setActiveTab, filename }: {
  result: AnalysisResult;
  activeTab: ResultTab;
  setActiveTab: (tab: ResultTab) => void;
  filename: string;
}) {
  const visibleTabs = tabs.map((tab) => ({
    ...tab,
    count: tab.id === "red-flags" ? result.red_flags.length
      : tab.id === "warnings" ? result.warnings.length
        : tab.id === "protections" ? result.good_clauses.length
          : tab.id === "statutes" ? result.statute_checks.length : undefined,
  }));

  return (
    <div className="analysis-panel">
      <div className="result-toolbar">
        <div className="document-meta"><span className="file-type-icon"><DocumentIcon /></span><span><strong>{filename}</strong><small>Analysis complete</small></span></div>
        <span className="complete-badge"><span /> COMPLETE</span>
      </div>
      <div className="score-overview">
        <div className="score-copy">
          <p className="eyebrow">CONTRACT FAIRNESS</p>
          <h2>Your contract, <span>decoded.</span></h2>
          <p>{result.contract_type.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase())} · {result.parties.length ? result.parties.join(" & ") : "Parties not identified"}</p>
        </div>
        <div className="score-card">
          <div className={`grade grade-${result.fairness_grade.replace("+", "plus")}`}>{result.fairness_grade}</div>
          <div><strong>{result.fairness_score}<small>/100</small></strong><span>Fairness score</span></div>
        </div>
      </div>
      <div className="metric-row">
        <Metric value={result.red_flags.length} label="Red flags" tone="red" />
        <Metric value={result.warnings.length} label="Review" tone="gold" />
        <Metric value={result.good_clauses.length} label="Protections" tone="green" />
        <Metric value={result.missing_protections.length} label="Missing" tone="muted" />
      </div>
      <nav className="result-tabs" aria-label="Analysis sections">
        {visibleTabs.map((tab) => (
          <button key={tab.id} className={activeTab === tab.id ? "active" : ""} onClick={() => setActiveTab(tab.id)}>
            {tab.label}{tab.count !== undefined && <span className="tab-count">{tab.count}</span>}
          </button>
        ))}
      </nav>
      <div className="result-content">
        {activeTab === "overview" && <Overview result={result} />}
        {activeTab === "red-flags" && <IssueList issues={result.red_flags} empty="No red flags were identified." />}
        {activeTab === "warnings" && <IssueList issues={result.warnings} empty="No warnings were identified." />}
        {activeTab === "protections" && <Protections result={result} />}
        {activeTab === "statutes" && <Statutes result={result} />}
      </div>
      <div className="legal-disclaimer"><ShieldIcon /><span>This AI-generated review is for informational purposes only and is not legal advice.</span></div>
    </div>
  );
}

function Metric({ value, label, tone }: { value: number; label: string; tone: string }) {
  return <div className="metric"><span className={`metric-number ${tone}`}>{value.toString().padStart(2, "0")}</span><span>{label}</span></div>;
}

function Overview({ result }: { result: AnalysisResult }) {
  return (
    <div className="overview-grid">
      <section className="overview-main">
        <div className="content-heading"><span className="heading-icon summary-icon">✳</span><div><h3>Executive summary</h3><p>The essentials, in plain language</p></div></div>
        <p className="summary-text">{result.summary}</p>
        {result.key_terms.length > 0 && <><h4 className="subheading">Key terms</h4><div className="term-list">{result.key_terms.map((term, index) => <span key={`${term}-${index}`}>{term}</span>)}</div></>}
        {result.missing_protections.length > 0 && <div className="missing-card"><h4>Protections to consider</h4><ul>{result.missing_protections.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>}
      </section>
      <aside className="overview-aside">
        <div className="content-heading"><span className="heading-icon risk-icon">!</span><div><h3>Priority review</h3><p>Items worth a closer look</p></div></div>
        {[...result.red_flags, ...result.warnings].slice(0, 3).length ? (
          [...result.red_flags, ...result.warnings].slice(0, 3).map((issue, index) => <div className="priority-item" key={`${issue.title}-${index}`}><span className={`priority-dot ${issue.severity}`} /><div><strong>{issue.title}</strong><small>{issue.clause || "Clause reference not specified"}</small></div></div>)
        ) : <p className="quiet-copy">No priority concerns were flagged.</p>}
      </aside>
    </div>
  );
}

function IssueList({ issues, empty }: { issues: Issue[]; empty: string }) {
  if (!issues.length) return <p className="empty-list">{empty}</p>;
  return <div className="issue-list">{issues.map((issue, index) => (
    <article className="issue-card" key={`${issue.title}-${index}`}>
      <div className="issue-title-row"><span className={`priority-dot ${issue.severity}`} /><h3>{issue.title}</h3><span className="clause-label">{issue.clause}</span></div>
      {issue.quote && <blockquote>{issue.quote}</blockquote>}
      <p>{issue.explanation}</p>
      {issue.suggestion && <div className="suggestion"><strong>Suggested action</strong><span>{issue.suggestion}</span></div>}
      {issue.redline && <div className="redline"><strong>Suggested wording</strong><span>{issue.redline}</span></div>}
    </article>
  ))}</div>;
}

function Protections({ result }: { result: AnalysisResult }) {
  return (
    <div className="issue-list">
      {result.good_clauses.length === 0 && <p className="empty-list">No specific protections were identified.</p>}
      {result.good_clauses.map((item, index) => (
        <article className="protection-card" key={`${item.title}-${index}`}><div className="issue-title-row"><span className="protection-check">✓</span><h3>{item.title}</h3><span className="clause-label">{item.clause}</span></div><p>{item.explanation}</p></article>
      ))}
      {result.missing_protections.length > 0 && <section className="missing-card"><h3>Missing protections</h3><ul>{result.missing_protections.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section>}
    </div>
  );
}

function Statutes({ result }: { result: AnalysisResult }) {
  if (!result.statute_checks.length) return <p className="empty-list">No statute checks were returned for this contract.</p>;
  return <div className="issue-list">{result.statute_checks.map((check) => (
    <article className="statute-card" key={check.rule_id}>
      <div className="issue-title-row"><span className={`statute-status ${check.status}`}>{check.status === "ok" ? "✓" : check.status === "violation" ? "!" : "?"}</span><h3>{check.title}</h3><span className={`status-label ${check.status}`}>{check.status}</span></div>
      <p>{check.detail}</p>{check.quote && <blockquote>{check.quote}</blockquote>}<small className="basis">Basis: {check.basis}</small>
    </article>
  ))}</div>;
}

function ShieldIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 20 6v5c0 5-3.4 8.4-8 10-4.6-1.6-8-5-8-10V6l8-3Z" /><path d="m8.5 12 2.2 2.2 4.8-5" /></svg>;
}
function UploadIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V4m0 0L8 8m4-4 4 4M5 14v5h14v-5" /></svg>;
}
function ArrowIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6" /></svg>;
}
function DocumentIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6zM14 3v5h5M9 13h6M9 17h6" /></svg>;
}

export default App;
