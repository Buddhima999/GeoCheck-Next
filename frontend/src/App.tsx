import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import MapView, { type MapMarker } from "./MapView";
import {
  Activity, AlertTriangle, ArrowDownToLine, Check, CheckCircle2, ChevronLeft,
  ChevronRight, CircleHelp, Clock3, Database, FileCheck2, FileSpreadsheet,
  KeyRound, LoaderCircle, Map, MapPinned, Network, RefreshCw, Search,
  ShieldCheck, Upload, Users, XCircle,
} from "lucide-react";

type HealthState = "checking" | "connected" | "unavailable";
type RunStatus = "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED";
type Tab = "overview" | "upload" | "results" | "map" | "hierarchy" | "issues";
type ValidationRun = {
  run_id: string;
  status: RunStatus;
  stage?: string;
  message?: string;
  original_filename?: string;
  file_size_bytes?: number;
  created_at?: string;
  processing_started_at?: string;
  completed_at?: string;
  failed_at?: string;
  source_rows_seen?: number;
  rows_processed?: number;
  blank_rows_skipped?: number;
  batches_processed?: number;
  rows_with_findings?: number;
  rows_with_hard_errors?: number;
  summary?: Record<string, number>;
};
type RunPage = { items: ValidationRun[]; total: number; limit: number; skip: number };
type CsvPreview = { headers: string[]; detected_mapping: Record<string, string>; supported_fields: string[]; required_fields: string[]; file_name: string };
type RunSummary = {
  run_id: string;
  status: RunStatus;
  source_rows_seen: number;
  blank_rows_skipped: number;
  customer_rows_stored: number;
  rows_with_findings: number;
  rows_with_hard_errors: number;
  eligible_for_dp_assessment: number;
  dp_count: number;
  dp_coordinate_consistent: number;
  dp_coordinate_conflict: number;
  dp_coordinate_unverified: number;
  issue_counts: Record<string, number>;
  note: string;
};
type ReferenceFileStatus = { configured: boolean; feature_or_mapping_count: number; sha256: string | null; status: "LOADED" | "MISSING" };
type ReferenceStatus = { directory_configured: boolean; country_boundary: ReferenceFileStatus; administrative_areas: ReferenceFileStatus; lea_areas: ReferenceFileStatus; provider_regions: ReferenceFileStatus; warning: string };
type CustomerResult = {
  row_number: number;
  customer_id?: string | null;
  dp_id?: string | null;
  source_values?: Record<string, string>;
  customer_location?: { lat: number; lon: number } | null;
  dp_location_observed?: { lat: number; lon: number } | null;
  issue_codes?: string[];
  findings?: Array<{ code: string; severity: string; message: string }>;
  has_hard_error?: boolean;
  eligible_for_dp_assessment?: boolean;
};
type DPResult = {
  dp_id: string;
  dp_location_observed?: { lat: number; lon: number } | null;
  coordinate_consistency: string;
  observed_row_count: number;
  valid_coordinate_observation_count: number;
  coordinate_conflict_observation_count: number;
  eligible_customer_row_count: number;
  eligible_distance_fail_row_count: number;
  issue_codes?: string[];
};
type Page<T> = { items: T[]; total: number; limit: number; skip: number };
type HierarchyItem = {
  province: string;
  district: string;
  region: string;
  customer_rows: number;
  rows_with_findings: number;
  dp_count: number;
};
const PAGE_SIZE = 25;
const tabs: Array<{ id: Tab; label: string; icon: typeof Activity }> = [
  { id: "overview", label: "Overview", icon: Activity },
  { id: "upload", label: "Upload & runs", icon: FileSpreadsheet },
  { id: "results", label: "Customer & DP results", icon: Database },
  { id: "map", label: "Map explorer", icon: MapPinned },
  { id: "hierarchy", label: "Geographic hierarchy", icon: Map },
  { id: "issues", label: "Issue explorer", icon: AlertTriangle },
];

async function readJson<T>(response: Response): Promise<T> {
  let body: unknown;
  try { body = await response.json(); } catch { body = undefined; }
  if (!response.ok) {
    const detail = typeof body === "object" && body !== null && "detail" in body
      ? String((body as { detail: unknown }).detail)
      : `Request failed (HTTP ${response.status}).`;
    throw new Error(detail);
  }
  return body as T;
}
function formatDate(value?: string) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
function formatNumber(value?: number) {
  return typeof value === "number" ? value.toLocaleString() : "—";
}
function statusClass(status?: string) {
  return (status || "unknown").toLowerCase();
}
function StatusBadge({ status }: { status: string }) {
  const Icon = status === "COMPLETED" ? CheckCircle2 :
    status === "FAILED" ? XCircle :
    status === "PROCESSING" ? LoaderCircle : Clock3;
  return <span className={`status-badge ${statusClass(status)}`}><Icon size={13} />{status}</span>;
}
function MetricCard({ label, value, note, icon: Icon }: {
  label: string; value: string; note: string; icon: typeof Users;
}) {
  return <article className="metric-card">
    <div className="metric-top"><span className="metric-icon"><Icon size={18} /></span><span className="metric-note">{note}</span></div>
    <p className="metric-label">{label}</p><p className="metric-value">{value}</p>
  </article>;
}

export default function App() {
  const [health, setHealth] = useState<HealthState>("checking");
  const [tab, setTab] = useState<Tab>("overview");
  const [apiKey, setApiKey] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [runs, setRuns] = useState<ValidationRun[]>([]);
  const [runTotal, setRunTotal] = useState(0);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [run, setRun] = useState<ValidationRun | null>(null);
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [referenceStatus, setReferenceStatus] = useState<ReferenceStatus | null>(null);
  const [loadingReferences, setLoadingReferences] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [csvPreview, setCsvPreview] = useState<CsvPreview | null>(null);
  const [columnMapping, setColumnMapping] = useState<Record<string, string>>({});
  const [previewing, setPreviewing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [issueFilter, setIssueFilter] = useState("");
  const [customerPage, setCustomerPage] = useState<Page<CustomerResult> | null>(null);
  const [dpPage, setDpPage] = useState<Page<DPResult> | null>(null);
  const [hierarchy, setHierarchy] = useState<Page<HierarchyItem> | null>(null);
  const [resultKind, setResultKind] = useState<"customers" | "dps">("customers");
  const [resultSkip, setResultSkip] = useState(0);
  const [hierarchySkip, setHierarchySkip] = useState(0);
  const [loadingResults, setLoadingResults] = useState(false);
  const [loadingHierarchy, setLoadingHierarchy] = useState(false);

  const apiFetch = useCallback(async (path: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    if (apiKey.trim()) headers.set("X-API-Key", apiKey.trim());
    return fetch(path, { ...init, headers });
  }, [apiKey]);

  const previewFile = useCallback(async (selectedFile: File | null) => {
    if (!selectedFile || !apiKey.trim() || !selectedFile.name.toLowerCase().endsWith(".csv")) {
      setCsvPreview(null);
      setColumnMapping({});
      return;
    }
    setPreviewing(true);
    setError("");
    const form = new FormData();
    form.append("file", selectedFile);
    try {
      const response = await apiFetch("/api/validation-runs/preview", { method: "POST", body: form });
      const preview = await readJson<CsvPreview>(response);
      setCsvPreview(preview);
      setColumnMapping(preview.detected_mapping);
      setNotice("Detected " + preview.headers.length + " CSV columns. Review required mappings before uploading.");
    } catch (cause) {
      setCsvPreview(null);
      setColumnMapping({});
      setError(cause instanceof Error ? cause.message : "Could not preview CSV headers.");
    } finally {
      setPreviewing(false);
    }
  }, [apiFetch, apiKey]);

  const loadRuns = useCallback(async (showSpinner = true) => {
    if (!apiKey.trim()) {
      setError("Enter your GEOCHECK_API_KEY to load validation runs.");
      return;
    }
    if (showSpinner) setLoadingRuns(true);
    try {
      const response = await apiFetch("/api/validation-runs?limit=50");
      const data = await readJson<RunPage>(response);
      setRuns(data.items);
      setRunTotal(data.total);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load validation runs.");
    } finally {
      if (showSpinner) setLoadingRuns(false);
    }
  }, [apiFetch, apiKey]);

  const loadRun = useCallback(async (runId: string) => {
    if (!apiKey.trim() || !runId) return;
    try {
      const response = await apiFetch(`/api/validation-runs/${encodeURIComponent(runId)}`);
      const data = await readJson<ValidationRun>(response);
      setRun(data);
      if (data.status === "COMPLETED") {
        const summaryResponse = await apiFetch(`/api/validation-runs/${encodeURIComponent(runId)}/summary`);
        setSummary(await readJson<RunSummary>(summaryResponse));
      } else {
        setSummary(null);
      }
      await loadRuns(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load the selected run.");
    }
  }, [apiFetch, apiKey, loadRuns]);

  const loadResults = useCallback(async (kind = resultKind, skip = resultSkip) => {
    if (!apiKey.trim() || !selectedRunId) return;
    setLoadingResults(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), skip: String(skip) });
      if (searchQuery.trim()) params.set("q", searchQuery.trim());
      if (kind === "customers" && issueFilter) params.set("issue_code", issueFilter);
      if (kind === "dps" && issueFilter === "DP_COORD_CONFLICT") params.set("consistency", "CONFLICT");
      const endpoint = kind === "customers" ? "customers" : "dps";
      const response = await apiFetch(`/api/validation-runs/${encodeURIComponent(selectedRunId)}/${endpoint}?${params}`);
      if (kind === "customers") setCustomerPage(await readJson<Page<CustomerResult>>(response));
      else setDpPage(await readJson<Page<DPResult>>(response));
      setResultKind(kind);
      setResultSkip(skip);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load results.");
    } finally {
      setLoadingResults(false);
    }
  }, [apiFetch, apiKey, issueFilter, resultKind, resultSkip, searchQuery, selectedRunId]);

  const loadHierarchy = useCallback(async (skip = hierarchySkip) => {
    if (!apiKey.trim() || !selectedRunId) return;
    setLoadingHierarchy(true);
    try {
      const response = await apiFetch("/api/validation-runs/" + encodeURIComponent(selectedRunId) + "/hierarchy?limit=100&skip=" + skip);
      setHierarchy(await readJson<Page<HierarchyItem>>(response));
      setHierarchySkip(skip);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load geographic hierarchy.");
    } finally {
      setLoadingHierarchy(false);
    }
  }, [apiFetch, apiKey, hierarchySkip, selectedRunId]);

  const loadReferenceStatus = useCallback(async () => {
    if (!apiKey.trim()) return;
    setLoadingReferences(true);
    try {
      const response = await apiFetch("/api/reference-data/status");
      setReferenceStatus(await readJson<ReferenceStatus>(response));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load reference-data inventory.");
    } finally {
      setLoadingReferences(false);
    }
  }, [apiFetch, apiKey]);

  useEffect(() => {
    let alive = true;
    fetch("/api/health").then((response) => {
      if (!response.ok) throw new Error("API unavailable");
      return response.json();
    }).then((data) => {
      if (alive) setHealth(data.database === "connected" ? "connected" : "unavailable");
    }).catch(() => { if (alive) setHealth("unavailable"); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!apiKey.trim()) return;
    void loadRuns();
  }, [apiKey, loadRuns]);

  useEffect(() => {
    if (file && apiKey.trim()) void previewFile(file);
  }, [file, apiKey, previewFile]);

  useEffect(() => {
    if (!selectedRunId || !apiKey.trim()) return;
    void loadRun(selectedRunId);
  }, [selectedRunId, apiKey, loadRun]);

  useEffect(() => {
    if (!apiKey.trim()) {
      setReferenceStatus(null);
      return;
    }
    void loadReferenceStatus();
  }, [apiKey, loadReferenceStatus]);

  useEffect(() => {
    if (!run || (run.status !== "QUEUED" && run.status !== "PROCESSING") || !apiKey.trim()) return;
    const timer = window.setInterval(() => { void loadRun(run.run_id); }, 2500);
    return () => window.clearInterval(timer);
  }, [apiKey, loadRun, run]);

  useEffect(() => {
    if ((tab === "results" || tab === "map") && selectedRunId && run?.status === "COMPLETED") void loadResults(resultKind, resultSkip);
  }, [tab, selectedRunId, run?.status]); // Search and paging run only on explicit user actions.

  useEffect(() => {
    if (tab === "hierarchy" && selectedRunId && run?.status === "COMPLETED") void loadHierarchy(0);
  }, [tab, selectedRunId, run?.status]); // Load the first page on entry.

  const healthText = health === "connected" ? "MongoDB connected" :
    health === "checking" ? "Checking API…" : "API / MongoDB unavailable";
  const completedRuns = useMemo(() => runs.filter((item) => item.status === "COMPLETED").length, [runs]);
  const issueEntries = useMemo(
    () => Object.entries(summary?.issue_counts || {}).sort((a, b) => b[1] - a[1]),
    [summary],
  );

  const mapMarkers = useMemo<MapMarker[]>(() => {
    if (resultKind === "customers") {
      return (customerPage?.items || []).flatMap((item) => {
        const point = item.customer_location;
        if (!point || !Number.isFinite(point.lat) || !Number.isFinite(point.lon)) return [];
        return [{
          id: "customer-" + String(item.row_number),
          latitude: point.lat,
          longitude: point.lon,
          title: "Customer " + (item.customer_id || String(item.row_number)),
          detail: "DP: " + (item.dp_id || "Missing") + " · Findings: " + String(item.issue_codes?.length || 0),
          kind: "customer" as const,
        }];
      });
    }
    return (dpPage?.items || []).flatMap((item) => {
      const point = item.dp_location_observed;
      if (!point || !Number.isFinite(point.lat) || !Number.isFinite(point.lon)) return [];
      return [{
        id: "dp-" + item.dp_id,
        latitude: point.lat,
        longitude: point.lon,
        title: "DP " + item.dp_id,
        detail: "Coordinate state: " + item.coordinate_consistency + " · Customer rows: " + String(item.observed_row_count),
        kind: "dp" as const,
      }];
    });
  }, [customerPage, dpPage, resultKind]);

  const mapCenter = useMemo<[number, number]>(() => {
    if (!mapMarkers.length) return [7.8731, 80.7718];
    return [
      mapMarkers.reduce((sum, marker) => sum + marker.latitude, 0) / mapMarkers.length,
      mapMarkers.reduce((sum, marker) => sum + marker.longitude, 0) / mapMarkers.length,
    ];
  }, [mapMarkers]);

  const mapTileUrl = import.meta.env.VITE_MAP_TILE_URL ||
    "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

  async function submitUpload(event: FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (!apiKey.trim()) { setError("Enter your GEOCHECK_API_KEY first."); return; }
    if (!file) { setError("Choose a CSV file first."); return; }
    if (!file.name.toLowerCase().endsWith(".csv")) { setError("Please choose a .csv file."); return; }
    if (!csvPreview) { setError("Wait until the CSV header preview is ready."); return; }
    const missingRequired = csvPreview.required_fields.filter((field) => !columnMapping[field]);
    if (missingRequired.length) { setError("Map these required fields: " + missingRequired.join(", ")); return; }
    const form = new FormData();
    form.append("file", file);
    form.append("column_mapping", JSON.stringify(columnMapping));
    setUploading(true);
    try {
      const response = await apiFetch("/api/validation-runs", { method: "POST", body: form });
      const data = await readJson<ValidationRun & { poll_url: string }>(response);
      setSelectedRunId(data.run_id);
      setRun(data);
      setSummary(null);
      setFile(null);
      setCsvPreview(null);
      setColumnMapping({});
      setTab("upload");
      setNotice(`Accepted ${data.original_filename || "CSV"} into the validation queue.`);
      await loadRuns(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "CSV upload failed.");
    } finally {
      setUploading(false);
    }
  }

  function selectRun(runItem: ValidationRun) {
    setSelectedRunId(runItem.run_id);
    setRun(runItem);
    setSummary(null);
    setCustomerPage(null);
    setDpPage(null);
    setHierarchy(null);
    setIssueFilter("");
    setSearchDraft("");
    setSearchQuery("");
    setResultSkip(0);
  }

  function searchResults(event: FormEvent) {
    event.preventDefault();
    setSearchQuery(searchDraft.trim());
    setResultSkip(0);
    // Call explicitly with the just-submitted query in the URL so no stale state is read.
    if (!selectedRunId || !apiKey.trim()) return;
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), skip: "0" });
    if (searchDraft.trim()) params.set("q", searchDraft.trim());
    if (resultKind === "customers" && issueFilter) params.set("issue_code", issueFilter);
    if (resultKind === "dps" && issueFilter === "DP_COORD_CONFLICT") params.set("consistency", "CONFLICT");
    const endpoint = resultKind === "customers" ? "customers" : "dps";
    setLoadingResults(true);
    apiFetch(`/api/validation-runs/${encodeURIComponent(selectedRunId)}/${endpoint}?${params}`)
      .then(readJson<Page<CustomerResult | DPResult>>)
      .then((page) => {
        if (resultKind === "customers") setCustomerPage(page as Page<CustomerResult>);
        else setDpPage(page as Page<DPResult>);
        setError("");
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Search failed."))
      .finally(() => setLoadingResults(false));
  }

  function changeIssueFilter(value: string) {
    setIssueFilter(value);
    setResultSkip(0);
    if (!selectedRunId || !apiKey.trim()) return;
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), skip: "0" });
    if (searchQuery.trim()) params.set("q", searchQuery.trim());
    if (resultKind === "customers" && value) params.set("issue_code", value);
    if (resultKind === "dps" && value === "DP_COORD_CONFLICT") params.set("consistency", "CONFLICT");
    const endpoint = resultKind === "customers" ? "customers" : "dps";
    setLoadingResults(true);
    apiFetch(`/api/validation-runs/${encodeURIComponent(selectedRunId)}/${endpoint}?${params}`)
      .then(readJson<Page<CustomerResult | DPResult>>)
      .then((page) => {
        if (resultKind === "customers") setCustomerPage(page as Page<CustomerResult>);
        else setDpPage(page as Page<DPResult>);
        setError("");
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Filter failed."))
      .finally(() => setLoadingResults(false));
  }

  async function downloadExport(kind: "customers" | "dps") {
    if (!selectedRunId || !apiKey.trim()) return;
    setError("");
    try {
      const endpoint = kind === "customers" ? "customers/export.csv" : "dps/export.csv";
      const response = await apiFetch(`/api/validation-runs/${encodeURIComponent(selectedRunId)}/${endpoint}`);
      if (!response.ok) throw new Error("Export failed. Check the API key and selected run.");
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `geocheck-${kind}-${selectedRunId}.csv`;
      anchor.click();
      URL.revokeObjectURL(objectUrl);
      setNotice(`Started ${kind} CSV export.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Export failed.");
    }
  }

  const currentPage = resultKind === "customers" ? customerPage : dpPage;
  const currentItems = currentPage?.items || [];
  const currentTotal = currentPage?.total || 0;
  const currentSkip = currentPage?.skip || 0;
  const canReadResults = Boolean(selectedRunId && run?.status === "COMPLETED");

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><MapPinned size={22} /></div><div><strong>GeoCheck</strong><span> NEXT</span></div></div>
      <div className="workspace-label">WORKSPACE</div>
      <nav aria-label="Main navigation">
        {tabs.map(({ id, label, icon: Icon }) => <button key={id} className={`nav-item ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
          <Icon size={17} />{label}
        </button>)}
      </nav>
      <div className="sidebar-bottom"><div className="privacy-note"><ShieldCheck size={16} /><span>Use synthetic data until identity access, storage security and retention controls are reviewed.</span></div><span className="muted tiny">Validation workspace v0.2</span></div>
    </aside>

    <main className="main-area">
      <header className="topbar">
        <div><p className="eyebrow">LOCATION QUALITY PLATFORM</p><h1>{tabs.find((item) => item.id === tab)?.label || "Overview"}</h1><p className="subtitle">Validate customer-to-DP relationships, investigate data-quality findings, and trace results through the reported geographic hierarchy.</p></div>
        <div className={`connection-pill ${health}`}><span className="connection-dot" />{healthText}</div>
      </header>

      <section className="auth-strip">
        <KeyRound size={18} />
        <div className="auth-copy"><strong>API access key</strong><span>Set GEOCHECK_API_KEY in backend/.env. The key is kept in this page's memory only.</span></div>
        <form onSubmit={(event) => { event.preventDefault(); setApiKey(keyDraft.trim()); setNotice(""); setError(""); }} className="key-form">
          <input value={keyDraft} onChange={(event) => setKeyDraft(event.target.value)} type="password" autoComplete="off" placeholder="Enter API key" aria-label="API key" />
          <button type="submit" className="secondary-button">{apiKey ? "Update key" : "Connect"}</button>
          {apiKey && <button type="button" className="quiet-button" onClick={() => { setApiKey(""); setRuns([]); setRun(null); setSummary(null); setSelectedRunId(""); setNotice(""); }}>Disconnect</button>}
        </form>
      </section>
      {error && <div className="alert error-alert" role="alert"><AlertTriangle size={17} /><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError("")}><XCircle size={15} /></button></div>}
      {notice && <div className="alert success-alert" role="status"><CheckCircle2 size={17} /><span>{notice}</span><button aria-label="Dismiss message" onClick={() => setNotice("")}><XCircle size={15} /></button></div>}

      {tab === "overview" && <>
        <section className="hero-panel"><div className="hero-copy"><span className="hero-tag"><ShieldCheck size={14} /> VALIDATION WORKSPACE</span><h2>Trust the evidence.<br />Find the records that need attention.</h2><p>Runs are processed in the background. Reference-dependent rules remain unverified until authoritative datasets are configured.</p><button className="hero-action" onClick={() => setTab("upload")}><Upload size={16} /> Upload a CSV <ChevronRight size={15} /></button></div><div className="hero-art" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="hero-pin"><MapPinned size={32} /></div><span className="hero-point point-a" /><span className="hero-point point-b" /><span className="hero-point point-c" /></div></section>
        <section className="section-heading"><div><h2>Latest dataset</h2><p>{summary && run?.status === "COMPLETED" ? `Selected run: ${run.original_filename || run.run_id}` : "Planning figures are not validation results."}</p></div><button className="secondary-button" onClick={() => { void loadRuns(); }} disabled={loadingRuns || !apiKey}><RefreshCw size={14} className={loadingRuns ? "spinning" : ""} /> Refresh runs</button></section>
        <section className="metrics-grid">
          <MetricCard label="Customers processed" value={formatNumber(summary?.customer_rows_stored)} note="Selected run" icon={Users} />
          <MetricCard label="Distinct DPs observed" value={formatNumber(summary?.dp_count)} note="Selected run" icon={Network} />
          <MetricCard label="Rows with findings" value={formatNumber(summary?.rows_with_findings)} note="Issue codes present" icon={AlertTriangle} />
          <MetricCard label="Hard-error rows" value={formatNumber(summary?.rows_with_hard_errors)} note="Requires attention" icon={FileCheck2} />
        </section>
        <section className="panel reference-panel"><div className="panel-heading"><div><h2>Trusted reference data</h2><p>Checks stay UNVERIFIED when approved boundary or mapping files are missing.</p></div><button className="secondary-button" onClick={() => void loadReferenceStatus()} disabled={!apiKey || loadingReferences}><RefreshCw size={14} className={loadingReferences ? "spinning" : ""} /> Refresh inventory</button></div>
          <div className="reference-grid">
            <ReferenceFileCard label="Country boundary" item={referenceStatus?.country_boundary} />
            <ReferenceFileCard label="Administrative areas" item={referenceStatus?.administrative_areas} />
            <ReferenceFileCard label="LEA areas" item={referenceStatus?.lea_areas} />
            <ReferenceFileCard label="Provider regions" item={referenceStatus?.provider_regions} />
          </div>
          <div className="panel-footnote"><CircleHelp size={15} /> Add provider-approved files under backend/reference-data and restart the worker. Their SHA-256 digests are stored with each run.</div>
        </section>
        <section className="content-grid">
          <article className="panel"><div className="panel-heading"><div><h2>Latest validation runs</h2><p>Select a run to view details and results.</p></div><span className="icon-bubble"><Activity size={18} /></span></div>
            {runs.length ? <div className="run-list">{runs.slice(0, 5).map((item) => <button key={item.run_id} className={`run-row ${selectedRunId === item.run_id ? "selected" : ""}`} onClick={() => { selectRun(item); setTab("upload"); }}>
              <div className="run-row-icon"><FileSpreadsheet size={17} /></div><div className="run-row-copy"><strong>{item.original_filename || item.run_id}</strong><span>{formatDate(item.created_at)} · {formatNumber(item.rows_processed)} rows</span></div><StatusBadge status={item.status} /><ChevronRight size={15} className="muted" />
            </button>)}</div> : <div className="empty-state"><div className="empty-icon"><FileCheck2 size={24} /></div><strong>{apiKey ? "No runs found" : "Connect to load runs"}</strong><span>Upload a synthetic CSV to create the first validation run.</span></div>}
            <div className="panel-footnote"><CircleHelp size={15} /> {completedRuns} completed runs in the latest {runs.length} records; {formatNumber(runTotal)} run(s) total.</div>
          </article>
          <article className="panel"><div className="panel-heading"><div><h2>Data-quality safeguards</h2><p>Rules and references should be auditable.</p></div><span className="icon-bubble warning"><ShieldCheck size={18} /></span></div><div className="safeguard-list">
            <div><Check size={15} /><span><strong>Bounded-batch processing</strong><small>Rows are processed in batches, not loaded all at once.</small></span></div>
            <div><Check size={15} /><span><strong>Reference checks stay honest</strong><small>Missing official references produce UNVERIFIED results.</small></span></div>
            <div><AlertTriangle size={15} /><span><strong>Not production-ready</strong><small>Identity controls, official reference data and scale tests are still required.</small></span></div>
          </div></article>
        </section>
      </>}

      {tab === "upload" && <section className="upload-layout">
        <article className="panel"><div className="panel-heading"><div><h2>Upload customer data CSV</h2><p>The server checks the header and queues the file for the worker.</p></div><span className="icon-bubble"><Upload size={18} /></span></div>
          <form onSubmit={submitUpload} className="upload-form">
            <label className="file-drop"><input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] || null)} /><span className="file-drop-icon"><Upload size={23} /></span><strong>{file?.name || "Choose a CSV file"}</strong><small>{file ? `${(file.size / (1024 * 1024)).toFixed(2)} MiB selected` : "Click to browse. Max upload size: 512 MiB by default."}</small></label>
            <div className="notice-note"><CircleHelp size={16} /><span>CSV must contain customer and DP latitude/longitude columns. The API detects known header aliases or accepts a custom mapping through its API. Blank data rows are counted separately.</span></div>
            <button type="submit" className="primary-button" disabled={!file || !apiKey.trim() || uploading}>{uploading ? <><LoaderCircle size={16} className="spinning" /> Uploading…</> : <><Upload size={16} /> Upload and queue validation</>}</button>
          </form>
        </article>
        <article className="panel"><div className="panel-heading"><div><h2>Selected run</h2><p>Status updates automatically while the run is active.</p></div><button className="icon-button" title="Refresh selected run" onClick={() => run && void loadRun(run.run_id)} disabled={!run || !apiKey}><RefreshCw size={16} /></button></div>
          {run ? <div className="selected-run-detail">
            <div className="run-detail-title"><strong>{run.original_filename || run.run_id}</strong><StatusBadge status={run.status} /></div>
            <p className="muted tiny-copy">Run ID: <code>{run.run_id}</code></p>
            <div className="run-progress"><div className="run-progress-bar" style={{ width: run.status === "COMPLETED" ? "100%" : run.status === "FAILED" ? "100%" : run.status === "PROCESSING" ? "55%" : "12%" }} /></div>
            <div className="progress-labels"><span>{run.stage || run.status}</span><span>{formatNumber(run.rows_processed)} processed</span></div>
            <dl className="detail-grid"><dt>Created</dt><dd>{formatDate(run.created_at)}</dd><dt>Rows seen</dt><dd>{formatNumber(run.source_rows_seen)}</dd><dt>Blank rows</dt><dd>{formatNumber(run.blank_rows_skipped)}</dd><dt>Rows with findings</dt><dd>{formatNumber(run.rows_with_findings)}</dd><dt>Hard errors</dt><dd>{formatNumber(run.rows_with_hard_errors)}</dd></dl>
            {run.status === "COMPLETED" && <button className="primary-button full-width" onClick={() => { setTab("results"); setResultKind("customers"); void loadResults("customers", 0); }}>Explore results <ChevronRight size={16} /></button>}
            {run.status === "FAILED" && <p className="failure-note">This run failed. Automatic resume is not supported yet. Review the run status/API logs and submit the source CSV as a new run.</p>}
          </div> : <div className="empty-state"><div className="empty-icon"><Clock3 size={24} /></div><strong>No run selected</strong><span>Upload a CSV or select a recent run from the overview.</span></div>}
        </article>
        <article className="panel runs-table-panel"><div className="panel-heading"><div><h2>Validation run history</h2><p>{formatNumber(runTotal)} run(s) found.</p></div><button className="secondary-button" onClick={() => void loadRuns()} disabled={!apiKey || loadingRuns}><RefreshCw size={14} className={loadingRuns ? "spinning" : ""} /> Refresh</button></div>
          <div className="table-scroll"><table><thead><tr><th>File / run</th><th>Status</th><th>Rows</th><th>Created</th><th></th></tr></thead><tbody>
            {runs.map((item) => <tr key={item.run_id}><td><strong>{item.original_filename || item.run_id}</strong><small>{item.run_id.slice(0, 12)}…</small></td><td><StatusBadge status={item.status} /></td><td>{formatNumber(item.rows_processed)}</td><td>{formatDate(item.created_at)}</td><td><button className="table-action" onClick={() => selectRun(item)}>Open</button></td></tr>)}
            {!runs.length && <tr><td colSpan={5} className="empty-cell">{apiKey ? "No runs recorded yet." : "Enter the API key to load runs."}</td></tr>}
          </tbody></table></div>
        </article>
      </section>}

      {tab === "results" && <section className="panel results-panel">
        <div className="panel-heading"><div><h2>Inspect validation results</h2><p>Search by customer ID, DP ID, phone or address. Results are paginated.</p></div><div className="header-actions"><button className="secondary-button" onClick={() => void downloadExport(resultKind)} disabled={!canReadResults || !apiKey}><ArrowDownToLine size={15} /> Export CSV</button></div></div>
        <div className="toolbar">
          <label className="field-label">Validation run<select value={selectedRunId} onChange={(event) => { const selected = runs.find((item) => item.run_id === event.target.value); if (selected) selectRun(selected); }}><option value="">Choose a run</option>{runs.map((item) => <option key={item.run_id} value={item.run_id}>{item.original_filename || item.run_id} — {item.status}</option>)}</select></label>
          <div className="toggle-group"><button className={resultKind === "customers" ? "selected" : ""} onClick={() => { setIssueFilter(""); setSearchDraft(""); setSearchQuery(""); void loadResults("customers", 0); }}>Customers</button><button className={resultKind === "dps" ? "selected" : ""} onClick={() => { setIssueFilter(""); setSearchDraft(""); setSearchQuery(""); void loadResults("dps", 0); }}>DPs</button></div>
        </div>
        <form className="results-search" onSubmit={searchResults}><Search size={17} /><input value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="Customer ID, DP ID, phone or address" aria-label="Search validation results" /><button type="submit" disabled={!canReadResults || loadingResults}>Search</button></form>
        {resultKind === "customers" && <label className="field-label issue-select">Filter issue<select value={issueFilter} onChange={(event) => changeIssueFilter(event.target.value)}><option value="">All rows</option>{Object.keys(summary?.issue_counts || {}).sort().map((code) => <option key={code} value={code}>{code} ({formatNumber(summary?.issue_counts[code])})</option>)}</select></label>}
        {resultKind === "dps" && <label className="field-label issue-select">DP coordinate state<select value={issueFilter} onChange={(event) => changeIssueFilter(event.target.value)}><option value="">All DPs</option><option value="DP_COORD_CONFLICT">Coordinate conflict</option></select></label>}
        {!canReadResults ? <div className="empty-state"><div className="empty-icon"><Database size={24} /></div><strong>{run?.status === "PROCESSING" || run?.status === "QUEUED" ? "Validation is still running" : "Select a completed run"}</strong><span>Results are available after the selected validation run completes.</span></div> :
          loadingResults ? <div className="loading-state"><LoaderCircle size={19} className="spinning" /> Loading records…</div> :
          resultKind === "customers" ? <>
            <div className="table-scroll"><table><thead><tr><th>Source row</th><th>Customer</th><th>DP</th><th>Reported area</th><th>Distance check</th><th>Findings</th><th>DP assessment</th></tr></thead><tbody>
              {(customerPage?.items || []).map((item) => <tr key={item.row_number}><td>{item.row_number}</td><td><strong>{item.customer_id || "Missing ID"}</strong><small>{item.source_values?.phone_number || "No phone value"}</small></td><td>{item.dp_id || "—"}</td><td><strong>{item.source_values?.reported_district || item.source_values?.reported_province || "Unreported"}</strong><small>{[item.source_values?.reported_province, item.source_values?.reported_region].filter(Boolean).join(" · ")}</small></td><td><StatusBadge status={item.issue_codes?.includes("CUSTOMER_DP_DISTANCE") ? "FAIL" : item.customer_location && item.dp_location_observed ? "PASS" : "UNVERIFIED"} /></td><td><div className="issue-tags">{item.issue_codes?.length ? item.issue_codes.map((code) => <span key={code} className="issue-tag">{code}</span>) : <span className="muted">No row findings</span>}</div></td><td><StatusBadge status={item.eligible_for_dp_assessment ? "ELIGIBLE" : "UNVERIFIED"} /></td></tr>)}
              {!customerPage?.items.length && <tr><td colSpan={7} className="empty-cell">No customer rows match these filters.</td></tr>}
            </tbody></table></div>
            <Pagination total={customerPage?.total || 0} skip={customerPage?.skip || 0} limit={customerPage?.limit || PAGE_SIZE} onChange={(skip) => void loadResults("customers", skip)} />
          </> : <>
            <div className="table-scroll"><table><thead><tr><th>DP ID</th><th>Coordinate state</th><th>Observed rows</th><th>Valid coordinates</th><th>Eligible customers</th><th>Distance failures</th><th>Conflict observations</th></tr></thead><tbody>
              {(dpPage?.items || []).map((item) => <tr key={item.dp_id}><td><strong>{item.dp_id}</strong><small>{item.dp_location_observed ? `${item.dp_location_observed.lat.toFixed(5)}, ${item.dp_location_observed.lon.toFixed(5)}` : "No valid point"}</small></td><td><StatusBadge status={item.coordinate_consistency} /></td><td>{formatNumber(item.observed_row_count)}</td><td>{formatNumber(item.valid_coordinate_observation_count)}</td><td>{formatNumber(item.eligible_customer_row_count)}</td><td>{formatNumber(item.eligible_distance_fail_row_count)}</td><td>{formatNumber(item.coordinate_conflict_observation_count)}</td></tr>)}
              {!dpPage?.items.length && <tr><td colSpan={7} className="empty-cell">No DP records match these filters.</td></tr>}
            </tbody></table></div>
            <Pagination total={dpPage?.total || 0} skip={dpPage?.skip || 0} limit={dpPage?.limit || PAGE_SIZE} onChange={(skip) => void loadResults("dps", skip)} />
          </>}
      </section>}

      {tab === "map" && <section className="panel results-panel">
        <div className="panel-heading"><div><h2>Customer and DP map</h2><p>Only points from the current results page are drawn; map markers are not a national density estimate.</p></div><span className="icon-bubble"><MapPinned size={18} /></span></div>
        <div className="toolbar"><label className="field-label">Validation run<select value={selectedRunId} onChange={(event) => { const selected = runs.find((item) => item.run_id === event.target.value); if (selected) selectRun(selected); }}><option value="">Choose a run</option>{runs.map((item) => <option key={item.run_id} value={item.run_id}>{item.original_filename || item.run_id} — {item.status}</option>)}</select></label>
          <div className="toggle-group"><button className={resultKind === "customers" ? "selected" : ""} onClick={() => void loadResults("customers", 0)}>Customers</button><button className={resultKind === "dps" ? "selected" : ""} onClick={() => void loadResults("dps", 0)}>DPs</button></div>
          <button className="secondary-button" onClick={() => void loadResults(resultKind, resultSkip)} disabled={!canReadResults || loadingResults}><RefreshCw size={14} /> Refresh map points</button>
        </div>
        {!canReadResults ? <div className="empty-state"><div className="empty-icon"><MapPinned size={24} /></div><strong>Select a completed run</strong><span>Map points become available after validation completes.</span></div> :
          loadingResults ? <div className="loading-state"><LoaderCircle size={19} className="spinning" /> Loading map points…</div> :
          <><div className="map-legend"><span><i className="map-dot customer-dot" /> Customer points</span><span><i className="map-dot dp-dot" /> DP points</span><span>{mapMarkers.length} mapped point(s) on this page</span></div>
            {mapMarkers.length ? <MapView key={selectedRunId + "-" + resultKind + "-" + resultSkip} markers={mapMarkers} center={mapCenter} tileUrl={mapTileUrl} /> : <div className="empty-state"><strong>No usable coordinates on this page</strong><span>Change the result page or inspect the coordinate findings.</span></div>}
            <Pagination total={currentTotal} skip={currentSkip} limit={currentPage?.limit || PAGE_SIZE} onChange={(skip) => void loadResults(resultKind, skip)} />
          </>}
        <div className="panel-footnote"><ShieldCheck size={15} /> The default OpenStreetMap tile service receives map viewport requests. Use synthetic data here unless your provider approves that external map service, or configure a company-approved tile server with VITE_MAP_TILE_URL.</div>
      </section>}

      {tab === "hierarchy" && <section className="panel results-panel">
        <div className="panel-heading"><div><h2>Reported geographic hierarchy</h2><p>Grouped from the labels in the uploaded CSV. These are not authoritative province, district or provider-region assignments.</p></div><span className="icon-bubble"><MapPinned size={18} /></span></div>
        <div className="toolbar"><label className="field-label">Validation run<select value={selectedRunId} onChange={(event) => { const selected = runs.find((item) => item.run_id === event.target.value); if (selected) selectRun(selected); }}><option value="">Choose a run</option>{runs.map((item) => <option key={item.run_id} value={item.run_id}>{item.original_filename || item.run_id} — {item.status}</option>)}</select></label><button className="secondary-button" onClick={() => void loadHierarchy(0)} disabled={!canReadResults || loadingHierarchy}><RefreshCw size={14} /> Refresh hierarchy</button></div>
        {!canReadResults ? <div className="empty-state"><strong>Select a completed run</strong><span>Hierarchy summaries are calculated after validation finishes.</span></div> : loadingHierarchy ? <div className="loading-state"><LoaderCircle size={19} className="spinning" /> Grouping reported geography…</div> :
          <><div className="table-scroll"><table><thead><tr><th>Province (reported)</th><th>District (reported)</th><th>Region (reported)</th><th>Customer rows</th><th>Rows with findings</th><th>DP IDs</th></tr></thead><tbody>{(hierarchy?.items || []).map((item, index) => <tr key={`${item.province}|${item.district}|${item.region}|${index}`}><td>{item.province}</td><td>{item.district}</td><td>{item.region}</td><td>{formatNumber(item.customer_rows)}</td><td>{formatNumber(item.rows_with_findings)}</td><td>{formatNumber(item.dp_count)}</td></tr>)}{!hierarchy?.items.length && <tr><td colSpan={6} className="empty-cell">No grouped geography found.</td></tr>}</tbody></table></div><Pagination total={hierarchy?.total || 0} skip={hierarchy?.skip || 0} limit={hierarchy?.limit || 100} onChange={(skip) => void loadHierarchy(skip)} /></>}
        <div className="panel-footnote"><CircleHelp size={15} /> Configure approved geographic boundaries and provider mapping files before interpreting these group labels as official hierarchy.</div>
      </section>}

      {tab === "issues" && <section className="results-panel">
        <div className="panel-heading standalone"><div><h2>Issue explorer</h2><p>Browse issue counts from a completed run and jump into matching customer rows.</p></div></div>
        <div className="toolbar panel"><label className="field-label">Validation run<select value={selectedRunId} onChange={(event) => { const selected = runs.find((item) => item.run_id === event.target.value); if (selected) selectRun(selected); }}><option value="">Choose a run</option>{runs.map((item) => <option key={item.run_id} value={item.run_id}>{item.original_filename || item.run_id} — {item.status}</option>)}</select></label><button className="secondary-button" onClick={() => run && void loadRun(run.run_id)} disabled={!run || !apiKey}><RefreshCw size={14} /> Refresh summary</button></div>
        {!canReadResults || !summary ? <div className="panel empty-state"><div className="empty-icon"><AlertTriangle size={24} /></div><strong>Select a completed run</strong><span>Issue summaries are not shown before a run completes.</span></div> :
          <div className="issue-explorer-grid">{issueEntries.map(([code, count]) => <button key={code} className="issue-card" onClick={() => { setIssueFilter(code); setResultKind("customers"); setSearchDraft(""); setSearchQuery(""); setResultSkip(0); setTab("results"); void loadResults("customers", 0); }}><div><span className="issue-card-icon"><AlertTriangle size={16} /></span><strong>{code}</strong></div><b>{formatNumber(count)}</b><small>Finding(s) · Click to inspect rows <ChevronRight size={13} /></small></button>)}{!issueEntries.length && <div className="panel empty-state"><strong>No issue codes recorded</strong><span>This run did not record any findings.</span></div>}</div>}
      </section>}

      {tab === "overview" && summary && <section className="panel summary-section"><div className="panel-heading"><div><h2>Selected run quality summary</h2><p>Reference checks can remain unverified and are not counted as confirmed errors.</p></div><StatusBadge status={summary.status} /></div><div className="summary-strip"><span>DP coordinate conflicts <b>{formatNumber(summary.dp_coordinate_conflict)}</b></span><span>Consistent DPs <b>{formatNumber(summary.dp_coordinate_consistent)}</b></span><span>Unverified DPs <b>{formatNumber(summary.dp_coordinate_unverified)}</b></span><span>Eligible customer rows <b>{formatNumber(summary.eligible_for_dp_assessment)}</b></span></div><p className="small-note">{summary.note}</p></section>}

      <footer className="footer"><span>GeoCheck Next · Local validation workspace</span><span><ShieldCheck size={13} /> Keep real customer data restricted</span></footer>
    </main>
  </div>;
}

function ReferenceFileCard({ label, item }: { label: string; item?: ReferenceFileStatus }) {
  return <div className="reference-card">
    <div><strong>{label}</strong><StatusBadge status={item?.status || "MISSING"} /></div>
    <span>{formatNumber(item?.feature_or_mapping_count)} feature/mapping record(s)</span>
    <small>{item?.sha256 ? "SHA-256 " + item.sha256.slice(0, 12) + "…" : "No approved reference file loaded"}</small>
  </div>;
}

function Pagination({ total, skip, limit, onChange }: { total: number; skip: number; limit: number; onChange: (skip: number) => void }) {
  const start = total ? skip + 1 : 0;
  const end = Math.min(skip + limit, total);
  return <div className="pagination"><span>Showing {formatNumber(start)}–{formatNumber(end)} of {formatNumber(total)}</span><div><button className="icon-button" onClick={() => onChange(Math.max(0, skip - limit))} disabled={skip <= 0} aria-label="Previous page"><ChevronLeft size={16} /></button><button className="icon-button" onClick={() => onChange(skip + limit)} disabled={skip + limit >= total} aria-label="Next page"><ChevronRight size={16} /></button></div></div>;
}
