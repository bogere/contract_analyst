import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import type { AdminSettings, AdminUserSummary, AnalysisResult, Issue, ReportSummary, ResultTab, SavedReport, SessionUser } from "./types";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const MAX_FILE_BYTES = 20 * 1024 * 1024;
const IDLE_TIMEOUT_MS = 15 * 60 * 1000;
const ACCEPTED = ".pdf,.docx,.txt,.md,.rtf,.bmp,.jpg,.jpeg,.png,.tif,.tiff,.webp";

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
  const uploadPanelRef = useRef<HTMLElement>(null);
  const [user, setUser] = useState<SessionUser | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [sessionError, setSessionError] = useState("");
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [activeReportId, setActiveReportId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<"dashboard" | "all-contracts" | "settings" | "users">("dashboard");
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
  const [adminSettingsLoading, setAdminSettingsLoading] = useState(false);
  const [adminSettingsSaving, setAdminSettingsSaving] = useState(false);
  const [adminSettingsError, setAdminSettingsError] = useState("");
  const [adminSettingsSaved, setAdminSettingsSaved] = useState("");
  const [adminHasSavedApiKey, setAdminHasSavedApiKey] = useState(false);
  const [adminHasEnvironmentApiKey, setAdminHasEnvironmentApiKey] = useState(false);
  const [adminEffectiveModel, setAdminEffectiveModel] = useState("");
  const [clearAdminApiKey, setClearAdminApiKey] = useState(false);
  const [adminUsers, setAdminUsers] = useState<AdminUserSummary[]>([]);
  const [adminUsersLoading, setAdminUsersLoading] = useState(false);
  const [adminUsersError, setAdminUsersError] = useState("");
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [creatingUser, setCreatingUser] = useState(false);

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
    let active = true;
    setReportsLoading(true);
    fetch(`${API_URL}/api/reports`, { credentials: "include" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Unable to load saved reports.");
        const nextReports = await response.json() as ReportSummary[];
        if (active) {
          setReports(nextReports);
          setHistoryError("");
        }
      })
      .catch((cause: unknown) => {
        if (active) setHistoryError(cause instanceof Error ? cause.message : "Unable to load saved reports.");
      })
      .finally(() => {
        if (active) setReportsLoading(false);
      });
    return () => { active = false; };
  }, [user]);

  useEffect(() => {
    if (!user?.is_admin) {
      setAdminUsers([]);
      return;
    }
    let active = true;
    setAdminSettingsLoading(true);
    setAdminUsersLoading(true);
    Promise.all([
      fetch(`${API_URL}/api/admin/settings`, { credentials: "include" }).then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "Unable to load workspace settings.");
        return body as AdminSettings;
      }),
      fetch(`${API_URL}/api/admin/users`, { credentials: "include" }).then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "Unable to load workspace users.");
        return body as AdminUserSummary[];
      }),
    ])
      .then(([settings, users]) => {
        if (!active) return;
        setModel(settings.model ?? "");
        setBaseUrl(settings.base_url ?? "");
        setLanguage(settings.language);
        setJurisdiction(settings.jurisdiction);
        setAdminHasSavedApiKey(settings.has_saved_api_key);
        setAdminHasEnvironmentApiKey(settings.has_environment_api_key);
        setAdminEffectiveModel(settings.effective_model);
        setAdminUsers(users);
        setAdminSettingsError("");
        setAdminUsersError("");
      })
      .catch((cause: unknown) => {
        if (!active) return;
        const message = cause instanceof Error ? cause.message : "Unable to load administrator workspace.";
        setAdminSettingsError(message);
        setAdminUsersError(message);
      })
      .finally(() => {
        if (active) {
          setAdminSettingsLoading(false);
          setAdminUsersLoading(false);
        }
      });
    return () => { active = false; };
  }, [user]);

  function selectFile(nextFile?: File) {
    if (!nextFile) return;
    const extension = nextFile.name.split(".").pop()?.toLowerCase();
    if (!extension || !["pdf", "docx", "txt", "md", "rtf", "bmp", "jpg", "jpeg", "png", "tif", "tiff", "webp"].includes(extension)) {
      setError("Please choose a PDF, DOCX, TXT, Markdown, RTF, BMP, JPEG, PNG, TIFF, or WebP document.");
      return;
    }
    if (nextFile.size > MAX_FILE_BYTES) {
      setError("This file is larger than the 20 MiB upload limit.");
      return;
    }
    setError("");
    setResult(null);
    setActiveReportId(null);
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
    setActiveReportId(null);
    const payload = new FormData();
    payload.append("file", file);

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
      if (reportsResponse.ok) {
        const nextReports = await reportsResponse.json() as ReportSummary[];
        setReports(nextReports);
        setActiveReportId(nextReports[0]?.id ?? null);
      }
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
      setActiveReportId(report.id);
      setActiveView("dashboard");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to open this report.");
    }
  }

  async function saveAdminSettings() {
    setAdminSettingsSaving(true);
    setAdminSettingsError("");
    setAdminSettingsSaved("");
    try {
      const response = await fetch(`${API_URL}/api/admin/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          model: model.trim() || null,
          base_url: baseUrl.trim() || null,
          api_key: apiKey.trim() || null,
          clear_api_key: clearAdminApiKey,
          language,
          jurisdiction,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Unable to save workspace settings.");
      const settings = body as AdminSettings;
      setModel(settings.model ?? "");
      setBaseUrl(settings.base_url ?? "");
      setAdminHasSavedApiKey(settings.has_saved_api_key);
      setAdminHasEnvironmentApiKey(settings.has_environment_api_key);
      setAdminEffectiveModel(settings.effective_model);
      setApiKey("");
      setClearAdminApiKey(false);
      setAdminSettingsSaved("Workspace analysis settings saved.");
    } catch (cause) {
      setAdminSettingsError(cause instanceof Error ? cause.message : "Unable to save workspace settings.");
    } finally {
      setAdminSettingsSaving(false);
    }
  }

  async function createWorkspaceUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreatingUser(true);
    setAdminUsersError("");
    try {
      const response = await fetch(`${API_URL}/api/admin/users`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: newUserEmail, password: newUserPassword }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Unable to create user.");
      const createdUser = body as AdminUserSummary;
      setAdminUsers((current) => [createdUser, ...current.filter((item) => item.id !== createdUser.id)]);
      setNewUserEmail("");
      setNewUserPassword("");
    } catch (cause) {
      setAdminUsersError(cause instanceof Error ? cause.message : "Unable to create user.");
    } finally {
      setCreatingUser(false);
    }
  }

  const signOut = useCallback(async (automatic = false) => {
    try {
      const response = await fetch(`${API_URL}/api/auth/logout`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) throw new Error("Unable to sign out. Please try again.");
    } catch (cause) {
      if (!automatic) {
        setError(cause instanceof Error ? cause.message : "Unable to sign out.");
        return;
      }
    }
    setUser(null);
    setResult(null);
    setFile(null);
    setApiKey("");
    setBaseUrl("");
    setClearAdminApiKey(false);
    setAdminSettingsSaved("");
    setAdminUsers([]);
    setSessionError(automatic ? "You were signed out after 15 minutes of inactivity." : "");
  }, []);

  useEffect(() => {
    if (!user) return;
    let timeoutId = 0;
    const resetTimeout = () => {
      window.clearTimeout(timeoutId);
      timeoutId = window.setTimeout(() => {
        void signOut(true);
      }, IDLE_TIMEOUT_MS);
    };
    const activityEvents: Array<keyof DocumentEventMap> = [
      "pointerdown",
      "keydown",
      "scroll",
      "touchstart",
    ];
    activityEvents.forEach((eventName) => {
      document.addEventListener(eventName, resetTimeout, { passive: true });
    });
    resetTimeout();
    return () => {
      window.clearTimeout(timeoutId);
      activityEvents.forEach((eventName) => {
        document.removeEventListener(eventName, resetTimeout);
      });
    };
  }, [user, signOut]);

  function startNewAnalysis() {
    setActiveView("dashboard");
    setActiveReportId(null);
    setResult(null);
    setResultFilename("");
    setFile(null);
    setError("");
    uploadPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
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
        <div className="dashboard-layout">
          <aside className="dashboard-sidebar" aria-label="Analysis dashboard">
            <div className="sidebar-heading">
              <div>
                <p className="eyebrow">WORKSPACE</p>
                <h2>My analyses</h2>
              </div>
              <span className="report-count">{reports.length}</span>
            </div>
            <button className="new-analysis-button" type="button" onClick={startNewAnalysis}>
              <span aria-hidden="true">+</span> New analysis
            </button>
            <button
              className={`sidebar-nav-button${activeView === "all-contracts" ? " active" : ""}`}
              type="button"
              onClick={() => setActiveView("all-contracts")}
              aria-current={activeView === "all-contracts" ? "page" : undefined}
            >
              <DocumentIcon /> All contracts
            </button>
            <div className="sidebar-section-heading">
              <span>RECENT CONTRACTS</span>
              {reports.length > 0 && <span>{Math.min(reports.length, 3)}</span>}
            </div>
            {historyError && <p className="error-message sidebar-error" role="alert">{historyError}</p>}
            <div className="sidebar-report-list" aria-label="Recent contract analyses">
              {reportsLoading ? (
                <p className="sidebar-status"><span className="spinner sidebar-spinner" /> Loading analyses…</p>
              ) : reports.length ? (
                reports.slice(0, 3).map((report) => (
                  <button
                    className={`sidebar-report${activeReportId === report.id ? " active" : ""}`}
                    type="button"
                    key={report.id}
                    onClick={() => void openReport(report)}
                    aria-current={activeReportId === report.id ? "true" : undefined}
                  >
                    <span className="sidebar-report-icon"><DocumentIcon /></span>
                    <span className="sidebar-report-copy">
                      <strong>{report.filename}</strong>
                      <small>{new Date(report.created_at).toLocaleDateString()} · {report.contract_type.replace(/_/g, " ")}</small>
                    </span>
                    <span className={`sidebar-grade${report.fairness_score < 50 ? " low" : ""}`}>{report.fairness_grade}</span>
                  </button>
                ))
              ) : !historyError ? (
                <div className="sidebar-empty">
                  <span className="sidebar-empty-icon"><DocumentIcon /></span>
                  <strong>No analyses yet</strong>
                  <span>Your saved contract reviews will appear here.</span>
                </div>
              ) : null}
            </div>
            {user.is_admin && (
              <>
                <div className="sidebar-section-heading admin-sidebar-heading"><span>ADMIN WORKSPACE</span></div>
                <button
                  className={`sidebar-nav-button${activeView === "users" ? " active" : ""}`}
                  type="button"
                  onClick={() => setActiveView("users")}
                  aria-current={activeView === "users" ? "page" : undefined}
                >
                  <UsersIcon /> Manage users
                </button>
                <button
                  className={`sidebar-nav-button${activeView === "settings" ? " active" : ""}`}
                  type="button"
                  onClick={() => setActiveView("settings")}
                  aria-current={activeView === "settings" ? "page" : undefined}
                >
                  <SettingsIcon /> AI settings
                </button>
              </>
            )}
            <div className="sidebar-footnote"><ShieldIcon /><span>Only you can access your saved analysis reports.</span></div>
          </aside>

          <div className="dashboard-content">
            {activeView === "all-contracts" ? (
              <section className="all-contracts-page">
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">CONTRACT LIBRARY</p>
                    <h1>All contracts</h1>
                    <p className="intro">Browse and reopen your saved contract analyses.</p>
                  </div>
                </div>
                <div className="panel contracts-panel">
                  <div className="contracts-panel-heading">
                    <div>
                      <h2>Contract analyses</h2>
                      <p>{reports.length} saved {reports.length === 1 ? "analysis" : "analyses"}</p>
                    </div>
                  </div>
                  {reportsLoading ? (
                    <p className="contracts-status"><span className="spinner sidebar-spinner" /> Loading analyses…</p>
                  ) : historyError ? (
                    <p className="error-message" role="alert">{historyError}</p>
                  ) : reports.length ? (
                    <div className="contracts-table-wrap">
                      <table className="contracts-table">
                        <thead>
                          <tr>
                            <th scope="col">Contract</th>
                            <th scope="col">Type</th>
                            <th scope="col">Date analyzed</th>
                            <th scope="col">Fairness</th>
                            <th scope="col"><span className="visually-hidden">Actions</span></th>
                          </tr>
                        </thead>
                        <tbody>
                          {reports.map((report) => (
                            <tr key={report.id}>
                              <td className="contracts-filename">{report.filename}</td>
                              <td className="contracts-type">{report.contract_type.replace(/_/g, " ")}</td>
                              <td><time dateTime={report.created_at}>{new Date(report.created_at).toLocaleDateString()}</time></td>
                              <td>
                                <span className={`contracts-grade${report.fairness_score < 50 ? " low" : ""}`}>
                                  {report.fairness_grade}
                                </span>
                                <span className="contracts-score">{report.fairness_score}/100</span>
                              </td>
                              <td>
                                <button className="contracts-open-button" type="button" onClick={() => void openReport(report)}>
                                  Open
                                  <span className="visually-hidden"> {report.filename}</span>
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="contracts-empty">
                      <span>No contract analyses yet.</span>
                      <button className="contracts-open-button" type="button" onClick={startNewAnalysis}>Start an analysis</button>
                    </div>
                  )}
                </div>
              </section>
            ) : activeView === "users" && user.is_admin ? (
              <section className="admin-page">
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">ADMIN WORKSPACE</p>
                    <h1>Manage users</h1>
                    <p className="intro">Create accounts for people who need access to contract analysis.</p>
                  </div>
                </div>
                <div className="panel admin-panel">
                  <div className="settings-panel-heading">
                    <span className="settings-panel-icon"><UsersIcon /></span>
                    <div>
                      <h2>Add a workspace user</h2>
                      <p>New users can sign in with the email and password you provide.</p>
                    </div>
                  </div>
                  <form className="admin-user-form" onSubmit={(event) => void createWorkspaceUser(event)}>
                    <div>
                      <label className="field-label" htmlFor="new-user-email">Email</label>
                      <input
                        id="new-user-email"
                        className="text-input"
                        type="email"
                        autoComplete="email"
                        required
                        maxLength={320}
                        value={newUserEmail}
                        onChange={(event) => setNewUserEmail(event.target.value)}
                      />
                    </div>
                    <div>
                      <label className="field-label" htmlFor="new-user-password">Temporary password</label>
                      <input
                        id="new-user-password"
                        className="text-input"
                        type="password"
                        autoComplete="new-password"
                        required
                        minLength={12}
                        maxLength={128}
                        value={newUserPassword}
                        onChange={(event) => setNewUserPassword(event.target.value)}
                      />
                    </div>
                    {adminUsersError && <div className="error-message" role="alert">{adminUsersError}</div>}
                    <button className="analyze-button" type="submit" disabled={creatingUser}>
                      {creatingUser ? <><span className="spinner" /> Creating user…</> : "Create user"}
                    </button>
                  </form>
                </div>
                <div className="panel users-panel">
                  <div className="users-panel-heading">
                    <div><h2>Workspace users</h2><p>{adminUsers.length} account{adminUsers.length === 1 ? "" : "s"}</p></div>
                  </div>
                  {adminUsersLoading ? <p className="admin-empty">Loading users…</p> : adminUsers.length ? (
                    <ul className="admin-user-list">
                      {adminUsers.map((workspaceUser) => (
                        <li key={workspaceUser.id}>
                          <span className="admin-user-avatar">{workspaceUser.email.slice(0, 1).toUpperCase()}</span>
                          <span className="admin-user-info"><strong>{workspaceUser.email}</strong><small>Added {new Date(workspaceUser.created_at).toLocaleDateString()}</small></span>
                          {workspaceUser.is_admin && <span className="admin-role-badge">Admin</span>}
                        </li>
                      ))}
                    </ul>
                  ) : <p className="admin-empty">No user accounts found.</p>}
                </div>
              </section>
            ) : activeView === "settings" && user.is_admin ? (
              <section className="settings-page">
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">ADMIN WORKSPACE</p>
                    <h1>AI analysis settings</h1>
                    <p className="intro">Configure shared provider settings for everyone in this workspace.</p>
                  </div>
                </div>
                <div className="panel settings-panel">
                  <div className="settings-panel-heading">
                    <span className="settings-panel-icon"><SettingsIcon /></span>
                    <div>
                      <h2>Shared AI configuration</h2>
                      <p>These settings are used for analyses across all user accounts.</p>
                    </div>
                  </div>
                  {adminSettingsLoading ? <p className="admin-empty">Loading current settings…</p> : (
                    <>
                      {adminSettingsError && <div className="error-message" role="alert">{adminSettingsError}</div>}
                      <label className="field-label" htmlFor="model">AI model override <span className="optional">Optional</span></label>
                      <input
                        id="model"
                        className="text-input"
                        value={model}
                        onChange={(event) => { setModel(event.target.value); setAdminSettingsSaved(""); }}
                        placeholder={`Uses .env default (${adminEffectiveModel || "gpt-4o-mini"})`}
                      />
                      <label className="field-label" htmlFor="base-url">OpenAI-compatible API URL <span className="optional">Optional</span></label>
                      <input
                        id="base-url"
                        className="text-input"
                        type="url"
                        autoComplete="url"
                        placeholder="Uses OPENAI_BASE_URL from .env"
                        value={baseUrl}
                        onChange={(event) => { setBaseUrl(event.target.value); setAdminSettingsSaved(""); }}
                      />
                      <label className="field-label" htmlFor="api-key">Provider API key <span className="optional">Optional</span></label>
                      <input
                        id="api-key"
                        className="text-input"
                        type="password"
                        autoComplete="new-password"
                        placeholder={adminHasSavedApiKey ? "Saved key configured; leave blank to keep it" : "Uses OPENAI_API_KEY / OPENROUTER_API_KEY from .env"}
                        value={apiKey}
                        onChange={(event) => {
                          setApiKey(event.target.value);
                          if (event.target.value) setClearAdminApiKey(false);
                          setAdminSettingsSaved("");
                        }}
                      />
                      <p className="settings-help">
                        {adminHasSavedApiKey
                          ? "A saved workspace key will be used for all users. Enter a replacement or choose clear to fall back to .env."
                          : adminHasEnvironmentApiKey
                            ? "No database key is saved. The provider key configured in .env will be used."
                            : "No provider key is configured. Add a key here or set one in .env."}
                      </p>
                      {adminHasSavedApiKey && (
                        <label className="clear-key-option">
                          <input
                            type="checkbox"
                            checked={clearAdminApiKey}
                            onChange={(event) => {
                              setClearAdminApiKey(event.target.checked);
                              if (event.target.checked) setApiKey("");
                            }}
                          />
                          Clear saved key and use .env instead
                        </label>
                      )}
                      <div className="field-row">
                        <div>
                          <label className="field-label" htmlFor="language">Report language</label>
                          <select id="language" className="text-input" value={language} onChange={(event) => { setLanguage(event.target.value as "en" | "zh"); setAdminSettingsSaved(""); }}>
                            <option value="en">English</option>
                            <option value="zh">中文</option>
                          </select>
                        </div>
                        <div>
                          <label className="field-label" htmlFor="jurisdiction">Legal jurisdiction</label>
                          <select id="jurisdiction" className="text-input" value={jurisdiction} onChange={(event) => { setJurisdiction(event.target.value as "auto" | "cn" | "us-ca"); setAdminSettingsSaved(""); }}>
                            <option value="auto">Auto-detect</option>
                            <option value="cn">China</option>
                            <option value="us-ca">California, US</option>
                          </select>
                        </div>
                      </div>
                      {adminSettingsSaved && <p className="success-message" role="status">{adminSettingsSaved}</p>}
                      <button className="analyze-button settings-done-button" type="button" onClick={() => void saveAdminSettings()} disabled={adminSettingsSaving}>
                        {adminSettingsSaving ? <><span className="spinner" /> Saving settings…</> : "Save shared settings"}
                      </button>
                    </>
                  )}
                </div>
              </section>
            ) : activeReportId ? (
              result && (
                <section className="results-column selected-report" aria-live="polite">
                  <AnalysisView
                    result={result}
                    activeTab={activeTab}
                    setActiveTab={setActiveTab}
                    filename={resultFilename || "Contract"}
                  />
                </section>
              )
            ) : (
              <>
                <section className="page-heading">
                  <div>
                    <p className="eyebrow">CONTRACT INTELLIGENCE</p>
                    <h1>Your contract dashboard.</h1>
                    <p className="intro">Review your saved analyses or start a new contract review.</p>
                  </div>
                  <div className="secure-badge"><ShieldIcon /> Temporary file handling</div>
                </section>

                <section className="dashboard-metrics" aria-label="Analysis summary">
                  <article className="dashboard-metric">
                    <span className="dashboard-metric-icon" aria-hidden="true">▤</span>
                    <span><small>SAVED ANALYSES</small><strong>{reports.length}</strong></span>
                  </article>
                  <article className="dashboard-metric">
                    <span className="dashboard-metric-icon score-icon" aria-hidden="true">◎</span>
                    <span>
                      <small>AVERAGE FAIRNESS</small>
                      <strong>
                        {reports.length ? (
                          <>
                            {Math.round(reports.reduce((total, report) => total + report.fairness_score, 0) / reports.length)}
                            <em>/100</em>
                          </>
                        ) : "—"}
                      </strong>
                    </span>
                  </article>
                  <article className="dashboard-metric">
                    <span className="dashboard-metric-icon date-icon" aria-hidden="true">◷</span>
                    <span>
                      <small>LAST REVIEW</small>
                      <strong className="last-review">{reports[0] ? new Date(reports[0].created_at).toLocaleDateString() : "—"}</strong>
                    </span>
                  </article>
                </section>

                <div className="main-grid">
          <aside className="panel upload-panel" ref={uploadPanelRef}>
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
                    onClick={(event) => {
                      event.stopPropagation();
                      setFile(null);
                      setResult(null);
                      setResultFilename("");
                      setActiveReportId(null);
                    }}
                  >Remove document</button>
                </>
              ) : (
                <>
                  <strong>Drop your file here</strong>
                  <span>or <span className="browse-link">browse files</span></span>
                  <small>PDF, DOCX, TXT, MD, RTF or image · Up to 20 MB</small>
                </>
              )}
            </div>

            {error && <div className="error-message" role="alert">{error}</div>}
            <button className="analyze-button" type="button" disabled={!file || loading} onClick={analyze}>
              {loading ? <><span className="spinner" /> Analyzing contract…</> : <>Analyze contract <ArrowIcon /></>}
            </button>
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
              </>
            )}
            <footer className="footer"><span>CONTRACTGUARD AI</span><span>Understand the fine print. Make your next move with confidence.</span></footer>
          </div>
        </div>
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
function SettingsIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9.5 3h5l.7 2.2c.6.2 1.2.5 1.7 1l2.2-.7 2.5 4.3-1.6 1.7v1.9l1.6 1.7-2.5 4.3-2.2-.7c-.5.4-1.1.8-1.7 1L14.5 22h-5l-.7-2.3c-.6-.2-1.2-.5-1.7-1l-2.2.7-2.5-4.3L4 13.4v-1.9l-1.6-1.7 2.5-4.3 2.2.7c.5-.4 1.1-.8 1.7-1z" /><circle cx="12" cy="12.5" r="3" /></svg>;
}
function UsersIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20M10 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM16 4.2a3.5 3.5 0 0 1 0 6.8M17 15h.5a3.5 3.5 0 0 1 3.5 3.5V20" /></svg>;
}

export default App;
