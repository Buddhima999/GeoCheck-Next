import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, ArrowDownToLine, ArrowLeftRight, Building2, Check,
  CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, Clock3,
  Database, FileCheck2, FileSpreadsheet, Filter, HardDriveUpload, KeyRound,
  LoaderCircle, MapPinned, Menu, Network, RefreshCw, Search, ShieldCheck,
  Trash2, Users, X,
} from "lucide-react";
import MapView, { type MapLine, type MapMarker } from "./MapView";

type Health = "checking" | "connected" | "unavailable";
type View = "overview" | "customers" | "fdps" | "geography" | "uploads";
type Status = "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED" | "DELETING";
type ValidationRun = {
  run_id: string;
  status: Status;
  stage?: string;
  message?: string;
  original_filename?: string;
  created_at?: string;
  completed_at?: string;
  source_rows_seen?: number;
  rows_processed?: number;
  rows_with_findings?: number;
  summary?: Record<string, number>;
};
type Page<T> = { items: T[]; total: number; limit: number; skip: number };
type CsvPreview = {
  headers: string[];
  detected_mapping: Record<string, string>;
  supported_fields: string[];
  required_fields: string[];
  file_name: string;
};
type Check = { status?: string; distance_m?: number; message?: string; method?: string; area_code?: string; area_name?: string };
type Customer = {
  row_number: number;
  customer_id?: string | null;
  dp_id?: string | null;
  source_values?: Record<string, string>;
  customer_location?: { lat: number; lon: number } | null;
  dp_location_observed?: { lat: number; lon: number } | null;
  calculated_customer_dp_distance_m?: number | null;
  overhead_distance_m?: number | null;
  overall_status?: string;
  issue_codes?: string[];
  findings?: Array<{ code: string; message: string; severity: string }>;
  checks?: Record<string, Check>;
  eligible_for_dp_assessment?: boolean;
  customer_inside_sri_lanka?: string;
};
type FDP = {
  dp_id: string;
  dp_location_observed?: { lat: number; lon: number } | null;
  coordinate_consistency: string;
  country_boundary_status?: string;
  dp_assessment_status?: string;
  observed_row_count: number;
  valid_coordinate_observation_count: number;
  coordinate_conflict_observation_count: number;
  eligible_customer_row_count: number;
  eligible_distance_fail_row_count: number;
  eligible_distance_fail_percentage?: number | null;
  assigned_customer_count?: number;
  available_port_count?: number;
  port_capacity?: number;
  port_capacity_status?: string;
  port_capacity_exceeded_by?: number;
  issue_codes?: string[];
  geography_customer_count?: number;
  geography_customer_rows?: number;
};
type FDPCustomerMapData = {
  dp_id: string;
  assigned_rows: number;
  mapped_rows: number;
  returned_points: number;
  truncated: boolean;
  customers: Array<{ customer_id?: string | null; row_number: number; latitude: number; longitude: number; address?: string | null; status?: string }>;
};
type Summary = {
  run_id: string;
  status: Status;
  source_rows_seen: number;
  blank_rows_skipped: number;
  customer_rows_stored: number;
  rows_with_findings: number;
  rows_with_hard_errors: number;
  faulty_customers: number;
  unverified_customers: number;
  passed_customers: number;
  eligible_for_dp_assessment: number;
  dp_count: number;
  dp_coordinate_consistent: number;
  dp_coordinate_conflict: number;
  dp_coordinate_unverified: number;
  dp_port_capacity_exceeded: number;
  dp_port_capacity_limit: number;
  correct_csv_rows: number;
  correct_csv_rows_percentage: number | null;
  correct_customer_locations: number;
  correct_customer_locations_percentage: number | null;
  correct_dp_locations: number;
  correct_dp_locations_percentage: number | null;
  dp_assessment_fail: number;
  dp_assessment_unverified: number;
  customer_dp_limit_m: number;
  issue_counts: Record<string, number>;
  note: string;
};
type GeographyOptions = { provinces: string[]; districts: string[]; regions: string[] };
type GeographySummary = Page<{ province: string; district: string; region: string; customer_rows: number; rows_with_findings: number; dp_count: number }> & {
  area_customer_rows: number;
  area_rows_with_findings: number;
};
type ReferenceFile = { status: "LOADED" | "MISSING"; feature_or_mapping_count: number };
type ReferenceStatus = {
  country_boundary: ReferenceFile;
  administrative_areas: ReferenceFile;
  lea_areas: ReferenceFile;
  lea_area_circles: ReferenceFile;
  provider_regions: ReferenceFile;
};

const PAGE_SIZE = 50;
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").trim().replace(/\/$/, "");
const GOOGLE_MAPS_KEY = (import.meta.env.VITE_GOOGLE_MAPS_API_KEY || "").trim();
const OSM_TILES = import.meta.env.VITE_MAP_TILE_URL || "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

const navigation: Array<{ id: View; label: string; icon: typeof Activity; group: string }> = [
  { id: "overview", label: "Overview", icon: Activity, group: "WORKSPACE" },
  { id: "customers", label: "Customer analysis", icon: Users, group: "ANALYSIS" },
  { id: "fdps", label: "FDP analysis", icon: Network, group: "ANALYSIS" },
  { id: "geography", label: "Service areas", icon: MapPinned, group: "ANALYSIS" },
  { id: "uploads", label: "Uploads & runs", icon: FileSpreadsheet, group: "DATA" },
];

async function json<T>(response: Response): Promise<T> {
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

function number(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString() : "—";
}
function percent(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)}%` : "—";
}
function date(value?: string) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
function statusName(value?: string) { return (value || "UNVERIFIED").replaceAll("_", " "); }
function statusTone(value?: string) {
  const status = (value || "unverified").toLowerCase();
  if (["pass", "completed", "consistent", "connected"].includes(status)) return "good";
  if (["fail", "failed", "conflict"].includes(status)) return "bad";
  if (["processing", "queued", "deleting"].includes(status)) return "working";
  return "neutral";
}
function runTitle(item: ValidationRun) { return item.original_filename || item.run_id; }

function Badge({ value }: { value?: string }) {
  return <span className={`badge badge-${statusTone(value)}`}><span className="badge-dot" />{statusName(value)}</span>;
}

function Progress({ value }: { value: number }) {
  const bounded = Math.max(0, Math.min(100, value));
  return <span className="progress-track"><i style={{ width: `${bounded}%` }} /></span>;
}

export default function App() {
  const [view, setView] = useState<View>("overview");
  const [health, setHealth] = useState<Health>("checking");
  const [apiKey, setApiKey] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [runs, setRuns] = useState<ValidationRun[]>([]);
  const [runTotal, setRunTotal] = useState(0);
  const [deletingRunId, setDeletingRunId] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [run, setRun] = useState<ValidationRun | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [referenceStatus, setReferenceStatus] = useState<ReferenceStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingPage, setLoadingPage] = useState(false);
  const [loadingAreas, setLoadingAreas] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [uploading, setUploading] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  const [customerPage, setCustomerPage] = useState<Page<Customer> | null>(null);
  const [customerSearchDraft, setCustomerSearchDraft] = useState("");
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerIssue, setCustomerIssue] = useState("");
  const [customerSkip, setCustomerSkip] = useState(0);
  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null);

  const [fdpPage, setFdpPage] = useState<Page<FDP> | null>(null);
  const [fdpSearchDraft, setFdpSearchDraft] = useState("");
  const [fdpQuery, setFdpQuery] = useState("");
  const [fdpSkip, setFdpSkip] = useState(0);
  const [selectedFdp, setSelectedFdp] = useState<FDP | null>(null);

  const [geographyOptions, setGeographyOptions] = useState<GeographyOptions>({ provinces: [], districts: [], regions: [] });
  const [province, setProvince] = useState("");
  const [district, setDistrict] = useState("");
  const [region, setRegion] = useState("");
  const [geographySummary, setGeographySummary] = useState<GeographySummary | null>(null);
  const [geographyKind, setGeographyKind] = useState<"customers" | "fdps">("customers");
  const [geographyCustomers, setGeographyCustomers] = useState<Page<Customer> | null>(null);
  const [geographyFdps, setGeographyFdps] = useState<Page<FDP> | null>(null);
  const [geographySkip, setGeographySkip] = useState(0);

  const apiFetch = useCallback((path: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    if (apiKey.trim()) headers.set("X-API-Key", apiKey.trim());
    return fetch(apiUrl(path), { ...init, headers });
  }, [apiKey]);

  const loadRuns = useCallback(async () => {
    if (!apiKey.trim()) return;
    setBusy(true);
    try {
      const data = await json<Page<ValidationRun>>(await apiFetch("/api/validation-runs?limit=50"));
      setRuns(data.items);
      setRunTotal(data.total);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load uploads.");
    } finally { setBusy(false); }
  }, [apiFetch, apiKey]);

  const loadRun = useCallback(async (runId: string) => {
    if (!apiKey.trim() || !runId) return;
    try {
      const current = await json<ValidationRun>(await apiFetch(`/api/validation-runs/${encodeURIComponent(runId)}`));
      setRun(current);
      if (current.status === "COMPLETED") {
        setSummary(await json<Summary>(await apiFetch(`/api/validation-runs/${encodeURIComponent(runId)}/summary`)));
      } else setSummary(null);
      return current;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load this upload.");
      return undefined;
    }
  }, [apiFetch, apiKey]);

  const selectRun = useCallback((runId: string) => {
    setSelectedRunId(runId);
    setCustomerPage(null);
    setFdpPage(null);
    setCustomerSkip(0);
    setFdpSkip(0);
    setCustomerQuery("");
    setCustomerSearchDraft("");
    setCustomerIssue("");
    setFdpQuery("");
    setFdpSearchDraft("");
    setProvince("");
    setDistrict("");
    setRegion("");
    setGeographyOptions({ provinces: [], districts: [], regions: [] });
    setGeographySummary(null);
    setGeographyCustomers(null);
    setGeographyFdps(null);
    setSelectedCustomer(null);
    setSelectedFdp(null);
    const picked = runs.find((item) => item.run_id === runId);
    if (picked) setRun(picked);
    void loadRun(runId);
  }, [loadRun, runs]);

  const deleteRun = useCallback(async (item: ValidationRun) => {
    if (!window.confirm(`Delete “${runTitle(item)}” and permanently remove its run history, customer rows, and FDP results? This cannot be undone.`)) return;
    setDeletingRunId(item.run_id);
    setError("");
    try {
      await json<{ run_id: string; deleted: boolean }>(await apiFetch(
        `/api/validation-runs/${encodeURIComponent(item.run_id)}`,
        { method: "DELETE" },
      ));
      const remaining = runs.filter((candidate) => candidate.run_id !== item.run_id);
      setRuns(remaining);
      setRunTotal((total) => Math.max(0, total - 1));
      setNotice(`${runTitle(item)} and its saved analysis data were deleted.`);
      if (selectedRunId === item.run_id) {
        if (remaining.length) selectRun(remaining[0].run_id);
        else {
          setSelectedRunId("");
          setRun(null);
          setSummary(null);
          setCustomerPage(null);
          setFdpPage(null);
        }
      }
      void loadRuns();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete this upload.");
    } finally {
      setDeletingRunId("");
    }
  }, [apiFetch, loadRuns, runs, selectedRunId, selectRun]);

  const loadCustomers = useCallback(async (skip = customerSkip, query = customerQuery, issue = customerIssue, area?: { province: string; district: string; region: string }) => {
    if (!apiKey.trim() || !selectedRunId) return;
    setLoadingPage(true);
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), skip: String(skip) });
      if (query.trim()) params.set("q", query.trim());
      if (issue) params.set("issue_code", issue);
      if (area?.province) params.set("province", area.province);
      if (area?.district) params.set("district", area.district);
      if (area?.region) params.set("region", area.region);
      const page = await json<Page<Customer>>(await apiFetch(`/api/validation-runs/${encodeURIComponent(selectedRunId)}/customers?${params}`));
      if (area) setGeographyCustomers(page); else setCustomerPage(page);
      if (!area) setCustomerSkip(skip);
      else setGeographySkip(skip);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load customer rows.");
    } finally { setLoadingPage(false); }
  }, [apiFetch, apiKey, customerIssue, customerQuery, customerSkip, selectedRunId]);

  const loadFdps = useCallback(async (skip = fdpSkip, query = fdpQuery, area?: { province: string; district: string; region: string }) => {
    if (!apiKey.trim() || !selectedRunId) return;
    setLoadingPage(true);
    try {
      let url: string;
      if (area) {
        const params = new URLSearchParams({ limit: String(PAGE_SIZE), skip: String(skip) });
        if (area.province) params.set("province", area.province);
        if (area.district) params.set("district", area.district);
        if (area.region) params.set("region", area.region);
        url = `/api/validation-runs/${encodeURIComponent(selectedRunId)}/hierarchy/dps?${params}`;
      } else {
        const params = new URLSearchParams({ limit: String(PAGE_SIZE), skip: String(skip) });
        if (query.trim()) params.set("q", query.trim());
        url = `/api/validation-runs/${encodeURIComponent(selectedRunId)}/dps?${params}`;
      }
      const page = await json<Page<FDP>>(await apiFetch(url));
      if (area) setGeographyFdps(page); else setFdpPage(page);
      if (!area) setFdpSkip(skip); else setGeographySkip(skip);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load FDP records.");
    } finally { setLoadingPage(false); }
  }, [apiFetch, apiKey, fdpQuery, fdpSkip, selectedRunId]);

  const currentArea = useMemo(() => ({ province, district, region }), [province, district, region]);

  const loadGeography = useCallback(async () => {
    if (!apiKey.trim() || !selectedRunId) return;
    setLoadingAreas(true);
    try {
      const params = new URLSearchParams();
      if (province) params.set("province", province);
      if (district) params.set("district", district);
      if (region) params.set("region", region);
      params.set("limit", "1");
      const result = await json<GeographySummary>(await apiFetch(`/api/validation-runs/${encodeURIComponent(selectedRunId)}/hierarchy?${params}`));
      setGeographySummary(result);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not summarize this service area.");
    } finally { setLoadingAreas(false); }
  }, [apiFetch, apiKey, district, province, region, selectedRunId]);

  const loadGeographyOptions = useCallback(async () => {
    if (!apiKey.trim() || !selectedRunId) return;
    try {
      const params = new URLSearchParams();
      if (province) params.set("province", province);
      if (district) params.set("district", district);
      const options = await json<GeographyOptions>(await apiFetch(`/api/validation-runs/${encodeURIComponent(selectedRunId)}/hierarchy/options?${params}`));
      setGeographyOptions(options);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load area selectors."); }
  }, [apiFetch, apiKey, district, province, selectedRunId]);

  const previewFile = useCallback(async (selectedFile: File | null) => {
    if (!selectedFile || !apiKey.trim()) return;
    setPreviewing(true);
    setError("");
    const form = new FormData();
    form.append("file", selectedFile);
    try {
      const result = await json<CsvPreview>(await apiFetch("/api/validation-runs/preview", { method: "POST", body: form }));
      setPreview(result);
      setMapping(result.detected_mapping);
    } catch (cause) {
      setPreview(null);
      setMapping({});
      setError(cause instanceof Error ? cause.message : "Could not read the CSV header.");
    } finally { setPreviewing(false); }
  }, [apiFetch, apiKey]);

  const connect = async (event: FormEvent) => {
    event.preventDefault();
    const candidate = keyDraft.trim();
    if (!candidate) { setError("Enter the workspace key from backend/.env."); return; }
    setApiKey(candidate);
    setError("");
  };

  const submitUpload = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    if (!file || !preview) { setError("Choose and preview a CSV file before uploading."); return; }
    const missing = preview.required_fields.filter((field) => !mapping[field]);
    if (missing.length) { setError(`Map the required columns: ${missing.join(", ")}.`); return; }
    setUploading(true);
    const form = new FormData();
    form.append("file", file);
    form.append("column_mapping", JSON.stringify(mapping));
    try {
      const created = await json<ValidationRun>(await apiFetch("/api/validation-runs", { method: "POST", body: form }));
      setRuns((previous) => [created, ...previous.filter((item) => item.run_id !== created.run_id)]);
      setRunTotal((value) => value + 1);
      setRun(created);
      setSelectedRunId(created.run_id);
      setSummary(null);
      setFile(null);
      setPreview(null);
      setMapping({});
      setNotice(`${created.original_filename || "CSV"} uploaded and queued for analysis.`);
      setView("uploads");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Upload failed."); }
    finally { setUploading(false); }
  };

  const downloadExport = async (kind: "customers" | "dps", reportType: "all" | "faults" | "unverified" = "all") => {
    if (!apiKey.trim() || !selectedRunId) return;
    try {
      const endpoint = kind === "customers" ? `customers/export.csv?report_type=${reportType}` : "dps/export.csv";
      const response = await apiFetch(`/api/validation-runs/${encodeURIComponent(selectedRunId)}/${endpoint}`);
      if (!response.ok) throw new Error("Export could not be downloaded.");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = kind === "customers" ? `geocheck-${reportType}-${selectedRunId}.csv` : `geocheck-fdps-${selectedRunId}.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice("Your CSV export is ready.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Export failed."); }
  };

  useEffect(() => {
    let live = true;
    fetch(apiUrl("/api/health")).then((response) => response.json()).then((body) => {
      if (live) setHealth(body.database === "connected" ? "connected" : "unavailable");
    }).catch(() => { if (live) setHealth("unavailable"); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (apiKey) void loadRuns();
  }, [apiKey, loadRuns]);

  useEffect(() => {
    if (!apiKey.trim()) {
      setReferenceStatus(null);
      return;
    }
    void loadReferenceInventory(apiFetch, setReferenceStatus, setError);
  }, [apiFetch, apiKey]);

  useEffect(() => {
    if (!selectedRunId || !apiKey) return;
    void loadRun(selectedRunId);
  }, [apiKey, selectedRunId, loadRun]);

  useEffect(() => {
    if (!run || !["QUEUED", "PROCESSING"].includes(run.status) || !apiKey) return;
    const timer = window.setInterval(async () => {
      const updated = await loadRun(run.run_id);
      if (updated?.status === "COMPLETED") void loadRuns();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [apiKey, loadRun, loadRuns, run]);

  useEffect(() => {
    if (!apiKey || !selectedRunId || run?.status !== "COMPLETED") return;
    if (view === "customers") void loadCustomers(0);
    if (view === "fdps") void loadFdps(0);
    if (view === "geography") {
      void loadGeographyOptions();
      void loadGeography();
    }
  }, [apiKey, selectedRunId, run?.status, view]);

  useEffect(() => {
    if (view !== "geography" || !selectedRunId || run?.status !== "COMPLETED") return;
    void loadGeographyOptions();
    void loadGeography();
    setGeographyCustomers(null);
    setGeographyFdps(null);
    setGeographySkip(0);
    if (province) {
      if (geographyKind === "customers") void loadCustomers(0, "", "", currentArea);
      else void loadFdps(0, "", currentArea);
    }
  }, [province, district, region, geographyKind]);

  useEffect(() => {
    if (file && apiKey) void previewFile(file);
  }, [file, apiKey, previewFile]);

  const issueEntries = useMemo(() => Object.entries(summary?.issue_counts || {}).sort((a, b) => b[1] - a[1]), [summary]);
  const completedRuns = useMemo(() => runs.filter((item) => item.status === "COMPLETED").length, [runs]);
  const activeLabel = navigation.find((item) => item.id === view)?.label || "Overview";

  const customerMarkers = useMemo((): MapMarker[] => {
    if (!selectedCustomer) return [];
    const markers: MapMarker[] = [];
    const customer = selectedCustomer.customer_location;
    const fdp = selectedCustomer.dp_location_observed;
    if (customer) markers.push({
      id: `customer-${selectedCustomer.row_number}`,
      latitude: customer.lat,
      longitude: customer.lon,
      title: `Customer ${selectedCustomer.customer_id || selectedCustomer.row_number}`,
      detail: selectedCustomer.source_values?.address || "Customer CSV location",
      kind: "customer",
    });
    if (fdp) markers.push({
      id: `fdp-${selectedCustomer.dp_id || selectedCustomer.row_number}`,
      latitude: fdp.lat,
      longitude: fdp.lon,
      title: `FDP ${selectedCustomer.dp_id || "unknown"}`,
      detail: `${number(selectedCustomer.checks?.customer_dp_distance?.distance_m ?? selectedCustomer.calculated_customer_dp_distance_m)} m from customer`,
      kind: "dp",
      radiusMeters: summary?.customer_dp_limit_m || 500,
    });
    return markers;
  }, [selectedCustomer, summary?.customer_dp_limit_m]);

  const customerPath = useMemo<MapLine | undefined>(() => {
    const customer = selectedCustomer?.customer_location;
    const fdp = selectedCustomer?.dp_location_observed;
    return customer && fdp ? [[customer.lat, customer.lon], [fdp.lat, fdp.lon]] : undefined;
  }, [selectedCustomer]);
  const customerMapCenter = useMemo<[number, number]>(() => {
    if (!customerMarkers.length) return [7.8731, 80.7718];
    return [
      customerMarkers.reduce((sum, marker) => sum + marker.latitude, 0) / customerMarkers.length,
      customerMarkers.reduce((sum, marker) => sum + marker.longitude, 0) / customerMarkers.length,
    ];
  }, [customerMarkers]);

  if (!apiKey) {
    return <main className="login-shell">
      <section className="login-card">
        <div className="login-mark"><MapPinned size={25} /></div>
        <p className="eyebrow">GEOCHECK · OPERATIONS</p>
        <h1>Customer and FDP<br />location quality</h1>
        <p className="login-lede">Review large CSV imports, find location issues, and trace customers to their connected FDP.</p>
        <div className={`service-health health-${health}`}><span />{health === "connected" ? "Local services are ready" : health === "checking" ? "Checking local services…" : "Backend is not available"}</div>
        <form className="login-form" onSubmit={(event) => void connect(event)}>
          <label htmlFor="workspace-key">Workspace access key</label>
          <div className="input-with-icon"><KeyRound size={17} /><input id="workspace-key" type="password" autoComplete="current-password" value={keyDraft} onChange={(event) => setKeyDraft(event.target.value)} placeholder="Enter the key from backend/.env" /></div>
          <button className="button button-primary" type="submit">Connect to workspace <ChevronRight size={16} /></button>
        </form>
        <p className="login-footnote">The key is kept in this browser tab only. Configure the Google Maps browser key in frontend/.env.local; it is never entered on this screen.</p>
        {error && <div className="inline-error" role="alert"><AlertTriangle size={15} />{error}</div>}
      </section>
    </main>;
  }

  return <div className="workspace">
    <aside className={`sidebar ${mobileNavOpen ? "sidebar-open" : ""}`}>
      <div className="brand"><span className="brand-icon"><MapPinned size={20} /></span><span><strong>GeoCheck</strong><small>LOCATION OPERATIONS</small></span><button className="mobile-close" onClick={() => setMobileNavOpen(false)} aria-label="Close menu"><X size={17} /></button></div>
      <div className="sidebar-dataset"><span>ACTIVE DATASET</span><strong title={run ? runTitle(run) : "No selected upload"}>{run ? runTitle(run) : "No upload selected"}</strong><small>{run ? `${number(run.rows_processed || run.source_rows_seen)} rows · ${statusName(run.status)}` : "Upload a CSV to begin"}</small></div>
      {Array.from(new Set(navigation.map((item) => item.group))).map((group) => <div className="nav-group" key={group}>
        <p>{group}</p>{navigation.filter((item) => item.group === group).map(({ id, label, icon: Icon }) => <button key={id} className={`nav-link ${view === id ? "active" : ""}`} onClick={() => { setView(id); setMobileNavOpen(false); setError(""); }}><Icon size={17} /><span>{label}</span>{id === "uploads" && uploading && <LoaderCircle size={14} className="spinning" />}</button>)}
      </div>)}
      <div className="sidebar-bottom"><div className={`sidebar-status health-${health}`}><span />{health === "connected" ? "Local database connected" : health === "checking" ? "Checking database" : "Database unavailable"}</div><div className="sidebar-note"><ShieldCheck size={15} /><span>Data stays in your configured workspace. Restrict access before using real customer records.</span></div></div>
    </aside>

    <main className="main-content">
      <header className="topbar">
        <div className="topbar-title"><button className="mobile-menu" onClick={() => setMobileNavOpen(true)} aria-label="Open menu"><Menu size={19} /></button><div><div className="breadcrumbs">GeoCheck <ChevronRight size={13} /> {activeLabel}</div><h1>{view === "overview" ? "Operations overview" : activeLabel}</h1></div></div>
        <div className="topbar-actions">
          <label className="dataset-select"><span>DATASET</span><select value={selectedRunId} onChange={(event) => selectRun(event.target.value)} aria-label="Select upload">
            <option value="">Select an upload</option>{runs.map((item) => <option key={item.run_id} value={item.run_id}>{runTitle(item)} · {item.status}</option>)}
          </select><ChevronDown size={14} /></label>
          <span className={`connection-state health-${health}`} title={health === "connected" ? "MongoDB connected" : "Local API / database status"}><i />{health === "connected" ? "Connected" : health === "checking" ? "Checking" : "Offline"}</span>
          <button className="button button-primary top-upload" onClick={() => setView("uploads")}><HardDriveUpload size={16} /><span>Upload CSV</span></button>
        </div>
      </header>

      {error && <div className="toast toast-error" role="alert"><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss error"><X size={15} /></button></div>}
      {notice && <div className="toast toast-success" role="status"><CheckCircle2 size={17} /><span>{notice}</span><button onClick={() => setNotice("")} aria-label="Dismiss notification"><X size={15} /></button></div>}

      {view === "overview" && <>
        <section className="page-intro"><div><p className="eyebrow">DATASET QUALITY</p><h2>{summary ? runTitle(run || { run_id: selectedRunId, status: "COMPLETED" }) : "Your network, at a glance"}</h2><p>{summary ? `${number(summary.customer_rows_stored)} customer rows · ${number(summary.dp_count)} FDPs · completed ${date(run?.completed_at)}` : "Upload a customer export to check customer locations, FDP locations, service distance, and FDP port capacity."}</p></div><button className="button button-secondary" onClick={() => void loadRuns()} disabled={busy}><RefreshCw size={15} className={busy ? "spinning" : ""} />Refresh data</button></section>
        {!summary || run?.status !== "COMPLETED" ? <section className="welcome-grid">
          <article className="welcome-card"><div className="welcome-icon"><Database size={22} /></div><p className="eyebrow">START A DATASET CHECK</p><h3>Upload the customer CSV</h3><p>GeoCheck validates customer coordinates and address evidence, checks FDP locations against Sri Lanka, and flags FDPs with more than eight distinct customers.</p><button className="button button-primary" onClick={() => setView("uploads")}><HardDriveUpload size={16} />Choose a CSV file</button><small>Files are processed by the local worker in batches. The browser does not load the full dataset.</small></article>
          <article className="panel recent-panel"><div className="panel-heading"><div><h3>Recent uploads</h3><p>Choose an existing validation run to review its results.</p></div><button className="icon-button" onClick={() => void loadRuns()} aria-label="Refresh uploads"><RefreshCw size={16} /></button></div>
            <RunList runs={runs.slice(0, 6)} selectedId={selectedRunId} onSelect={(item) => { selectRun(item.run_id); setView("overview"); }} />
            {!runs.length && <div className="empty-inline">No upload history yet.</div>}
          </article>
        </section> : <>
          <section className="stat-grid">
            <StatCard label="Rows passing validation" value={percent(summary.correct_csv_rows_percentage)} detail={`${number(summary.correct_csv_rows)} of ${number(summary.customer_rows_stored)} rows`} icon={CheckCircle2} tone="green" />
            <StatCard label="Customer locations accepted" value={percent(summary.correct_customer_locations_percentage)} detail={`${number(summary.correct_customer_locations)} customer locations`} icon={Users} tone="blue" />
            <StatCard label="FDPs verified" value={percent(summary.correct_dp_locations_percentage)} detail={`${number(summary.correct_dp_locations)} of ${number(summary.dp_count)} FDPs`} icon={Network} tone="orange" />
            <StatCard label="Rows needing review" value={percent(summary.customer_rows_stored ? (summary.rows_with_findings * 100 / summary.customer_rows_stored) : null)} detail={`${number(summary.rows_with_findings)} rows · ${number(summary.unverified_customers)} unverified`} icon={AlertTriangle} tone="red" />
          </section>
          <section className="dashboard-grid">
            <article className="panel quality-panel"><div className="panel-heading"><div><h3>CSV quality breakdown</h3><p>Every row is counted once in the overall row totals.</p></div><span className="subtle-chip">{number(summary.customer_rows_stored)} rows checked</span></div>
              <div className="quality-row"><div className="quality-label"><span className="quality-dot pass" /><span>Passed</span><strong>{number(summary.passed_customers)}</strong><b>{percent(summary.customer_rows_stored ? summary.passed_customers * 100 / summary.customer_rows_stored : null)}</b></div><Progress value={summary.customer_rows_stored ? summary.passed_customers * 100 / summary.customer_rows_stored : 0} /></div>
              <div className="quality-row"><div className="quality-label"><span className="quality-dot fail" /><span>Failed</span><strong>{number(summary.faulty_customers)}</strong><b>{percent(summary.customer_rows_stored ? summary.faulty_customers * 100 / summary.customer_rows_stored : null)}</b></div><Progress value={summary.customer_rows_stored ? summary.faulty_customers * 100 / summary.customer_rows_stored : 0} /></div>
              <div className="quality-row"><div className="quality-label"><span className="quality-dot unverified" /><span>Unverified</span><strong>{number(summary.unverified_customers)}</strong><b>{percent(summary.customer_rows_stored ? summary.unverified_customers * 100 / summary.customer_rows_stored : null)}</b></div><Progress value={summary.customer_rows_stored ? summary.unverified_customers * 100 / summary.customer_rows_stored : 0} /></div>
              <div className="quality-footer"><span><b>{number(summary.rows_with_hard_errors)}</b> rows with blocking data errors</span><span><b>{number(summary.blank_rows_skipped)}</b> blank lines skipped</span></div>
            </article>
            <article className="panel network-panel"><div className="panel-heading"><div><h3>FDP network health</h3><p>Location verification and port capacity.</p></div><span className="metric-icon icon-orange"><Network size={17} /></span></div>
              <div className="network-stat"><div><span>Coordinate conflicts</span><strong>{number(summary.dp_coordinate_conflict)}</strong></div><Badge value={summary.dp_coordinate_conflict ? "FAIL" : "PASS"} /></div>
              <div className="network-stat"><div><span>Over the {number(summary.dp_port_capacity_limit)}-customer port limit</span><strong>{number(summary.dp_port_capacity_exceeded)}</strong></div><Badge value={summary.dp_port_capacity_exceeded ? "FAIL" : "PASS"} /></div>
              <div className="network-stat"><div><span>Not fully verifiable</span><strong>{number(summary.dp_assessment_unverified)}</strong></div><Badge value={summary.dp_assessment_unverified ? "UNVERIFIED" : "PASS"} /></div>
              <button className="text-link" onClick={() => setView("fdps")}>Open FDP analysis <ChevronRight size={14} /></button>
            </article>
          </section>
          <section className="panel issue-panel"><div className="panel-heading"><div><h3>Issues to review</h3><p>Issue percentage = rows with this finding ÷ total customer rows. Select one to inspect records.</p></div><button className="button button-secondary" onClick={() => setView("customers")}><Filter size={15} />All customer rows</button></div>
            {issueEntries.length ? <div className="issue-list">{issueEntries.slice(0, 8).map(([code, count]) => {
              const share = summary.customer_rows_stored ? count * 100 / summary.customer_rows_stored : 0;
              return <button className="issue-row" key={code} onClick={() => { setCustomerIssue(code); setCustomerQuery(""); setCustomerSearchDraft(""); setView("customers"); void loadCustomers(0, "", code); }}><span className="issue-alert"><AlertTriangle size={15} /></span><span className="issue-name">{code.replaceAll("_", " ").toLowerCase()}</span><span className="issue-count">{number(count)} <small>rows</small></span><span className="issue-share"><Progress value={share} /><small>{share.toFixed(2)}% of dataset</small></span><ChevronRight size={16} className="issue-chevron" /></button>;
            })}</div> : <div className="empty-inline">No validation issues were recorded for this upload.</div>}
          </section>
          <section className="dashboard-footer-grid">
            <article className="panel reference-panel"><div className="panel-heading"><div><h3>Reference data status</h3><p>Geographic checks depend on approved reference files.</p></div><button className="icon-button" onClick={() => void loadReferenceInventory(apiFetch, setReferenceStatus, setError)} aria-label="Refresh reference status"><RefreshCw size={15} /></button></div>
              <ReferenceGrid data={referenceStatus} />
            </article>
            <article className="panel recent-panel"><div className="panel-heading"><div><h3>Recent uploads</h3><p>{number(runTotal)} saved validation run(s).</p></div><button className="text-link" onClick={() => setView("uploads")}>View all <ChevronRight size={14} /></button></div><RunList runs={runs.slice(0, 4)} selectedId={selectedRunId} onSelect={(item) => selectRun(item.run_id)} /></article>
          </section>
        </>}
      </>}

      {view === "uploads" && <section className="upload-page">
        <div className="page-intro"><div><p className="eyebrow">DATA INTAKE</p><h2>Upload and validate</h2><p>Choose a CSV. GeoCheck previews its columns, then the worker processes rows without loading the whole file into the browser.</p></div></div>
        <div className="upload-grid">
          <article className="panel upload-panel"><div className="panel-heading"><div><h3>New CSV upload</h3><p>Map the columns before starting validation.</p></div><span className="metric-icon icon-blue"><HardDriveUpload size={17} /></span></div>
            <form onSubmit={(event) => void submitUpload(event)} className="upload-form">
              <label className={`file-drop ${file ? "file-selected" : ""}`}><input type="file" accept=".csv,text/csv" onChange={(event) => { setPreview(null); setMapping({}); setFile(event.target.files?.[0] || null); }} /><span className="file-drop-icon"><FileSpreadsheet size={23} /></span><strong>{file?.name || "Choose a customer CSV"}</strong><small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · previewing headers…` : "CSV format · upload limits are configured by your administrator"}</small>{previewing && <span className="file-loading"><LoaderCircle className="spinning" size={14} /> Reading headers</span>}</label>
              {preview && <div className="mapping-section"><div className="mapping-title"><strong>Confirm column mapping</strong><span>{Object.keys(mapping).length} field(s) mapped</span></div><p>Required audit fields must be mapped. Province, district, and region are optional when approved coordinate reference data is configured.</p><div className="mapping-grid">{preview.supported_fields.map((field) => {
                const required = preview.required_fields.includes(field);
                return <label className="mapping-field" key={field}><span>{field.replaceAll("_", " ")}{required && <em>Required</em>}</span><select value={mapping[field] || ""} required={required} onChange={(event) => {
                  const selected = event.target.value;
                  setMapping((previous) => {
                    const next = { ...previous };
                    if (!selected) delete next[field];
                    else {
                      for (const [otherField, header] of Object.entries(next)) if (otherField !== field && header === selected) delete next[otherField];
                      next[field] = selected;
                    }
                    return next;
                  });
                }}><option value="">Not mapped</option>{preview.headers.map((header) => <option key={header} value={header}>{header}</option>)}</select></label>;
              })}</div></div>}
              <div className="upload-note"><CircleHelp size={15} /><span>Required: customer ID, Sri Lankan landline/service number, customer address and coordinates, reported province, FDP ID and FDP coordinates. A landline prefix and province check can flag a mismatch; they do not identify an exact house or telephone-exchange boundary.</span></div>
              <button type="submit" className="button button-primary button-wide" disabled={!file || !preview || previewing || uploading}>{uploading ? <><LoaderCircle size={16} className="spinning" />Uploading…</> : <><HardDriveUpload size={16} />Upload and start analysis</>}</button>
            </form>
          </article>
          <article className="panel run-status-panel"><div className="panel-heading"><div><h3>Selected upload</h3><p>Processing runs in the background.</p></div><button className="icon-button" onClick={() => { if (selectedRunId) void loadRun(selectedRunId); }} disabled={!selectedRunId} aria-label="Refresh status"><RefreshCw size={15} /></button></div>
            {run ? <div className="run-status-card"><div className="run-title-row"><strong>{runTitle(run)}</strong><Badge value={run.status} /></div><p className="run-id">Run ID · {run.run_id}</p><div className="run-progress"><Progress value={run.status === "COMPLETED" || run.status === "FAILED" ? 100 : run.status === "PROCESSING" ? 62 : 12} /></div><div className="run-progress-caption"><span>{statusName(run.stage || run.status)}</span><span>{number(run.rows_processed)} processed</span></div><dl className="run-meta"><dt>Rows seen</dt><dd>{number(run.source_rows_seen)}</dd><dt>Rows processed</dt><dd>{number(run.rows_processed)}</dd><dt>Rows with findings</dt><dd>{number(run.rows_with_findings)}</dd><dt>Created</dt><dd>{date(run.created_at)}</dd></dl>{run.status === "COMPLETED" && <button className="button button-secondary button-wide" onClick={() => setView("overview")}>Open analysis <ChevronRight size={15} /></button>}</div> : <div className="empty-inline">Select an upload from the top bar or start a new CSV upload.</div>}
          </article>
        </div>
        <article className="panel uploads-history"><div className="panel-heading"><div><h3>Upload history</h3><p>{number(runTotal)} saved run(s) · {completedRuns} completed in the current page</p><small className="delete-help">Deleting a finished run removes its analysis rows and any retained source CSV.</small></div><button className="button button-secondary" onClick={() => void loadRuns()} disabled={busy}><RefreshCw size={15} className={busy ? "spinning" : ""} />Refresh</button></div><RunTable runs={runs} selectedId={selectedRunId} onSelect={(item) => selectRun(item.run_id)} onDelete={(item) => void deleteRun(item)} deletingRunId={deletingRunId} /></article>
      </section>}

      {view === "customers" && <section className="analysis-page">
        <PageHeader title="Customer location analysis" description="Review address, landline-area, coordinate, and connected-FDP evidence. Select a customer to see the customer and FDP together on the map." />
        <div className="analysis-toolbar"><form className="search-box" onSubmit={(event) => { event.preventDefault(); setCustomerQuery(customerSearchDraft.trim()); setCustomerSkip(0); void loadCustomers(0, customerSearchDraft.trim(), customerIssue); }}><Search size={17} /><input value={customerSearchDraft} onChange={(event) => setCustomerSearchDraft(event.target.value)} placeholder="Search customer, landline, FDP, address" /><button type="submit" disabled={!selectedRunId || loadingPage}>Search</button></form><label className="control-label">Finding<select value={customerIssue} onChange={(event) => { setCustomerIssue(event.target.value); setCustomerSkip(0); void loadCustomers(0, customerQuery, event.target.value); }}><option value="">All rows</option>{Object.keys(summary?.issue_counts || {}).sort().map((code) => <option key={code} value={code}>{code.replaceAll("_", " ")} · {number(summary?.issue_counts[code])}</option>)}</select></label><div className="toolbar-actions"><button className="button button-secondary" onClick={() => void downloadExport("customers", "all")} disabled={!selectedRunId}><ArrowDownToLine size={15} />Export CSV</button><button className="button button-quiet" onClick={() => void downloadExport("customers", "faults")} disabled={!selectedRunId}>Faults only</button></div></div>
        <div className="panel table-panel"><div className="table-heading"><div><strong>{number(customerPage?.total)} customer row(s)</strong><span>{customerIssue ? `Filtered by ${customerIssue.replaceAll("_", " ").toLowerCase()}` : "Click a customer to open the map and validation evidence."}</span></div><button className="icon-button" onClick={() => void loadCustomers(customerSkip)} aria-label="Refresh customer rows"><RefreshCw size={15} /></button></div>
          {loadingPage ? <Loading label="Loading customer rows…" /> : <div className="data-table-scroll"><table className="data-table"><thead><tr><th>Customer</th><th>Address & landline</th><th>Connected FDP</th><th>Location checks</th><th>Distance</th><th>Record result</th></tr></thead><tbody>
            {(customerPage?.items || []).map((item) => <tr key={item.row_number} className="selectable-row" onClick={() => setSelectedCustomer(item)} tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter") setSelectedCustomer(item); }}><td><strong>{item.customer_id || "Missing ID"}</strong><small>Source row {number(item.row_number)}</small></td><td><span className="cell-primary">{item.source_values?.address || "No address"}</span><small>{item.source_values?.router_number || item.source_values?.phone_number || "No landline"}{item.checks?.router_number_format?.area_name ? ` · ${item.checks.router_number_format.area_name}` : ""}</small></td><td><strong>{item.dp_id || "Not assigned"}</strong><small>{item.dp_location_observed ? `${item.dp_location_observed.lat.toFixed(5)}, ${item.dp_location_observed.lon.toFixed(5)}` : "FDP coordinates unavailable"}</small></td><td><span className="check-stack"><span><Badge value={item.checks?.country_boundary?.status || item.customer_inside_sri_lanka} /> <small>Country</small></span><span><Badge value={item.checks?.address_zone?.status} /> <small>Address</small></span><span><Badge value={item.checks?.phone_area?.status} /> <small>Landline</small></span></span></td><td>{item.checks?.customer_dp_distance?.distance_m != null ? <><strong>{number(Math.round(item.checks.customer_dp_distance.distance_m))} m</strong><small>Customer → FDP</small></> : <Badge value="UNVERIFIED" />}</td><td><span className="result-cell"><Badge value={item.overall_status} /><small>{item.issue_codes?.length ? `${item.issue_codes.length} finding(s)` : "No findings"}</small></span></td></tr>)}
            {!customerPage?.items.length && <tr><td colSpan={6} className="empty-cell">No customer rows match the current search.</td></tr>}
          </tbody></table></div>}
          <Pager total={customerPage?.total || 0} skip={customerPage?.skip || 0} limit={customerPage?.limit || PAGE_SIZE} onChange={(skip) => void loadCustomers(skip)} />
        </div>
      </section>}

      {view === "fdps" && <section className="analysis-page">
        <PageHeader title="FDP service analysis" description="Check FDP points against the Sri Lanka boundary, review connected customer distances, and find FDPs assigned more customers than their port capacity." />
        {summary && <div className="mini-stat-row"><MiniStat label="FDPs in dataset" value={number(summary.dp_count)} /><MiniStat label="Over the port limit" value={number(summary.dp_port_capacity_exceeded)} tone="bad" /><MiniStat label="Port limit per FDP" value={number(summary.dp_port_capacity_limit)} /><MiniStat label="Distance review limit" value={`${number(summary.customer_dp_limit_m)} m`} /></div>}
        <div className="analysis-toolbar"><form className="search-box" onSubmit={(event) => { event.preventDefault(); setFdpQuery(fdpSearchDraft.trim()); setFdpSkip(0); void loadFdps(0, fdpSearchDraft.trim()); }}><Search size={17} /><input value={fdpSearchDraft} onChange={(event) => setFdpSearchDraft(event.target.value)} placeholder="Search FDP ID" /><button type="submit" disabled={!selectedRunId || loadingPage}>Search</button></form><div className="toolbar-actions"><button className="button button-secondary" onClick={() => void downloadExport("dps")} disabled={!selectedRunId}><ArrowDownToLine size={15} />Export FDP report</button></div></div>
        <div className="panel table-panel"><div className="table-heading"><div><strong>{number(fdpPage?.total)} FDP(s)</strong><span>Port usage counts distinct customer IDs from this upload.</span></div><button className="icon-button" onClick={() => void loadFdps(fdpSkip)} aria-label="Refresh FDP records"><RefreshCw size={15} /></button></div>
          {loadingPage ? <Loading label="Loading FDP records…" /> : <div className="data-table-scroll"><table className="data-table"><thead><tr><th>FDP</th><th>Coordinate & boundary</th><th>Customers / ports</th><th>Customer distance</th><th>Coordinate consistency</th><th>Overall assessment</th></tr></thead><tbody>
            {(fdpPage?.items || []).map((item) => <tr key={item.dp_id} className="selectable-row" onClick={() => setSelectedFdp(item)} tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter") setSelectedFdp(item); }}><td><strong>{item.dp_id}</strong><small>{number(item.observed_row_count)} linked CSV rows</small></td><td>{item.dp_location_observed ? <><strong>{item.dp_location_observed.lat.toFixed(5)}, {item.dp_location_observed.lon.toFixed(5)}</strong><small><Badge value={item.country_boundary_status} />Sri Lanka boundary</small></> : <Badge value="UNVERIFIED" />}</td><td><div className="capacity-cell"><strong>{number(item.assigned_customer_count)} <small>/ {number(item.port_capacity)} ports</small></strong><Progress value={item.port_capacity ? (item.assigned_customer_count || 0) * 100 / item.port_capacity : 0} /><span>{number(item.available_port_count)} free · <Badge value={item.port_capacity_status} /></span></div></td><td>{item.eligible_distance_fail_percentage != null ? <><strong>{percent(item.eligible_distance_fail_percentage)} over limit</strong><small>{number(item.eligible_distance_fail_row_count)} failed distance check(s)</small></> : <Badge value="UNVERIFIED" />}</td><td><Badge value={item.coordinate_consistency} /><small>{number(item.coordinate_conflict_observation_count)} conflict observation(s)</small></td><td><Badge value={item.dp_assessment_status} />{item.issue_codes?.length ? <small>{item.issue_codes.join(", ")}</small> : <small>No FDP findings</small>}</td></tr>)}
            {!fdpPage?.items.length && <tr><td colSpan={6} className="empty-cell">No FDP records match this search.</td></tr>}
          </tbody></table></div>}
          <Pager total={fdpPage?.total || 0} skip={fdpPage?.skip || 0} limit={fdpPage?.limit || PAGE_SIZE} onChange={(skip) => void loadFdps(skip)} />
        </div>
      </section>}

      {view === "geography" && <section className="analysis-page">
        <PageHeader title="Service-area explorer" description="Browse the network from province to district to region. Use approved area reference files for coordinate-based assignments; CSV labels are used as a fallback." />
        <div className="panel geography-panel">
          <div className="geography-selectors"><AreaSelect label="Province" value={province} options={geographyOptions.provinces} placeholder="Select a province" onChange={(value) => { setProvince(value); setDistrict(""); setRegion(""); }} /><AreaSelect label="District" value={district} options={geographyOptions.districts} placeholder="All districts" disabled={!province} onChange={(value) => { setDistrict(value); setRegion(""); }} /><AreaSelect label="Region" value={region} options={geographyOptions.regions} placeholder="All regions" disabled={!district} onChange={setRegion} /><button className="button button-secondary area-refresh" onClick={() => { void loadGeographyOptions(); void loadGeography(); }} disabled={loadingAreas}><RefreshCw size={15} className={loadingAreas ? "spinning" : ""} />Refresh</button></div>
          {province ? <><div className="area-title-row"><div><p className="eyebrow">SELECTED SERVICE AREA</p><h3>{[province, district, region].filter(Boolean).join(" / ")}</h3></div><div className="area-tabs"><button className={geographyKind === "customers" ? "active" : ""} onClick={() => setGeographyKind("customers")}><Users size={15} />Customers</button><button className={geographyKind === "fdps" ? "active" : ""} onClick={() => setGeographyKind("fdps")}><Network size={15} />FDPs</button></div></div>
            <div className="area-kpis"><MiniStat label="Customer CSV rows" value={number(geographySummary?.area_customer_rows)} /><MiniStat label="Rows with findings" value={number(geographySummary?.area_rows_with_findings)} tone={geographySummary?.area_rows_with_findings ? "bad" : "good"} /><MiniStat label="Rows without findings" value={number((geographySummary?.area_customer_rows || 0) - (geographySummary?.area_rows_with_findings || 0))} tone="good" /></div>
            <div className="area-table-head"><div><strong>{geographyKind === "customers" ? number(geographyCustomers?.total) : number(geographyFdps?.total)} {geographyKind === "customers" ? "customer rows" : "FDPs"}</strong><span>Showing a page at a time for large datasets.</span></div></div>
            {loadingPage ? <Loading label="Loading selected area…" /> : geographyKind === "customers" ? <div className="data-table-scroll"><table className="data-table"><thead><tr><th>Customer</th><th>Address</th><th>FDP</th><th>Location result</th><th>Findings</th></tr></thead><tbody>{(geographyCustomers?.items || []).map((item) => <tr key={item.row_number} className="selectable-row" onClick={() => setSelectedCustomer(item)}><td><strong>{item.customer_id || "Missing ID"}</strong><small>{item.source_values?.router_number || "No landline"}</small></td><td>{item.source_values?.address || "No address"}</td><td>{item.dp_id || "Not assigned"}</td><td><Badge value={item.overall_status} /></td><td>{item.issue_codes?.length ? item.issue_codes.join(", ") : "None"}</td></tr>)}{!geographyCustomers?.items.length && <tr><td colSpan={5} className="empty-cell">No customer rows in this area.</td></tr>}</tbody></table></div> : <div className="data-table-scroll"><table className="data-table"><thead><tr><th>FDP</th><th>Location</th><th>Customers in area</th><th>Ports used / limit</th><th>Boundary</th><th>Assessment</th></tr></thead><tbody>{(geographyFdps?.items || []).map((item) => <tr key={item.dp_id} className="selectable-row" onClick={() => setSelectedFdp(item)}><td><strong>{item.dp_id}</strong></td><td>{item.dp_location_observed ? `${item.dp_location_observed.lat.toFixed(5)}, ${item.dp_location_observed.lon.toFixed(5)}` : "No coordinate"}</td><td>{number(item.geography_customer_count)} distinct <small>{number(item.geography_customer_rows)} CSV row(s)</small></td><td>{number(item.assigned_customer_count)} / {number(item.port_capacity)}<small>{number(item.available_port_count)} free port(s)</small></td><td><Badge value={item.country_boundary_status} /></td><td><Badge value={item.dp_assessment_status} /></td></tr>)}{!geographyFdps?.items.length && <tr><td colSpan={6} className="empty-cell">No FDPs in this area.</td></tr>}</tbody></table></div>}
            <Pager total={geographyKind === "customers" ? geographyCustomers?.total || 0 : geographyFdps?.total || 0} skip={geographySkip} limit={PAGE_SIZE} onChange={(skip) => geographyKind === "customers" ? void loadCustomers(skip, "", "", currentArea) : void loadFdps(skip, "", currentArea)} />
          </> : <div className="area-empty"><div><Building2 size={22} /></div><strong>Select a province to start</strong><p>Province and district are derived from customer coordinates only when the approved admin-area polygons are installed. Otherwise GeoCheck uses area labels supplied in the CSV. Unresolved records are shown as Unreported.</p></div>}
        </div>
        <div className="reference-callout"><CircleHelp size={16} /><span>For coordinate-derived areas, configure <code>backend/reference-data/admin_areas.geojson</code> and <code>provider_regions.csv</code>. Missing reference data is shown as Unreported; the app does not invent service areas.</span></div>
      </section>}

      {view === "overview" && summary && run?.status === "COMPLETED" && <div className="secondary-actions"><button className="button button-secondary" onClick={() => void downloadExport("customers", "faults")}><ArrowDownToLine size={15} />Download flagged rows</button><button className="button button-quiet" onClick={() => setView("uploads")}>Switch upload</button></div>}
      <footer className="app-footer"><span>GeoCheck Operations</span><span>CSV analysis runs on your configured backend</span></footer>
    </main>

    {selectedCustomer && <CustomerDialog customer={selectedCustomer} markers={customerMarkers} center={customerMapCenter} line={customerPath} googleMapsKey={GOOGLE_MAPS_KEY} onClose={() => setSelectedCustomer(null)} />}
    {selectedFdp && <FdpDialog fdp={selectedFdp} runId={selectedRunId || ""} apiFetch={apiFetch} googleMapsKey={GOOGLE_MAPS_KEY} onClose={() => setSelectedFdp(null)} />}
  </div>;
}

function StatCard({ label, value, detail, icon: Icon, tone }: { label: string; value: string; detail: string; icon: typeof Users; tone: string }) {
  return <article className="stat-card"><div className={`stat-icon stat-${tone}`}><Icon size={19} /></div><p>{label}</p><strong>{value}</strong><small>{detail}</small></article>;
}

function MiniStat({ label, value, tone = "default" }: { label: string; value: string; tone?: string }) {
  return <article className={`mini-stat mini-${tone}`}><span>{label}</span><strong>{value}</strong></article>;
}

function PageHeader({ title, description }: { title: string; description: string }) {
  return <div className="page-intro"><div><p className="eyebrow">VALIDATION WORKSPACE</p><h2>{title}</h2><p>{description}</p></div></div>;
}

function AreaSelect({ label, value, options, placeholder, disabled, onChange }: { label: string; value: string; options: string[]; placeholder: string; disabled?: boolean; onChange: (value: string) => void }) {
  return <label className="area-select"><span>{label}</span><select value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}><option value="">{placeholder}</option>{options.map((option) => <option key={option} value={option}>{option}</option>)}</select><ChevronDown size={15} /></label>;
}

function RunList({ runs, selectedId, onSelect }: { runs: ValidationRun[]; selectedId: string; onSelect: (run: ValidationRun) => void }) {
  return <div className="run-list">{runs.map((item) => <button className={`run-list-item ${selectedId === item.run_id ? "selected" : ""}`} key={item.run_id} onClick={() => onSelect(item)}><span className="run-file-icon"><FileSpreadsheet size={17} /></span><span className="run-list-copy"><strong>{runTitle(item)}</strong><small>{date(item.created_at)} · {number(item.rows_processed || item.source_rows_seen)} rows</small></span><Badge value={item.status} /><ChevronRight size={15} /></button>)}</div>;
}

function RunTable({ runs, selectedId, onSelect, onDelete, deletingRunId }: { runs: ValidationRun[]; selectedId: string; onSelect: (run: ValidationRun) => void; onDelete: (run: ValidationRun) => void; deletingRunId: string }) {
  return <div className="data-table-scroll"><table className="data-table"><thead><tr><th>File</th><th>Status</th><th>Rows</th><th>Created</th><th>Actions</th></tr></thead><tbody>{runs.map((item) => {
    const terminal = item.status === "COMPLETED" || item.status === "FAILED" || item.status === "DELETING";
    const deleteTitle = item.status === "DELETING" ? "Retry cleanup for this run" : terminal ? "Delete this run and all its results" : "Wait until processing finishes before deleting";
    return <tr key={item.run_id}><td><strong>{runTitle(item)}</strong><small>{item.run_id.slice(0, 14)}…</small></td><td><Badge value={item.status} /></td><td>{number(item.rows_processed || item.source_rows_seen)}</td><td>{date(item.created_at)}</td><td><span className="run-actions"><button className="button button-secondary button-small" onClick={() => onSelect(item)}>{selectedId === item.run_id ? "Selected" : "Open"}</button><button className="button button-danger button-small" onClick={() => onDelete(item)} disabled={!terminal || deletingRunId === item.run_id} title={deleteTitle} aria-label={`Delete ${runTitle(item)}`}>{deletingRunId === item.run_id ? <LoaderCircle size={13} className="spinning" /> : <Trash2 size={13} />}<span>Delete</span></button></span></td></tr>;
  })}{!runs.length && <tr><td className="empty-cell" colSpan={5}>No uploads yet.</td></tr>}</tbody></table></div>;
}

function Pager({ total, skip, limit, onChange }: { total: number; skip: number; limit: number; onChange: (skip: number) => void }) {
  const start = total ? skip + 1 : 0;
  const end = Math.min(skip + limit, total);
  return <div className="pager"><span>Showing {number(start)}–{number(end)} of {number(total)}</span><div><button className="icon-button" onClick={() => onChange(Math.max(0, skip - limit))} disabled={skip <= 0} aria-label="Previous page"><ChevronLeft size={16} /></button><button className="icon-button" onClick={() => onChange(skip + limit)} disabled={skip + limit >= total} aria-label="Next page"><ChevronRight size={16} /></button></div></div>;
}

function Loading({ label }: { label: string }) {
  return <div className="loading-block"><LoaderCircle size={20} className="spinning" />{label}</div>;
}

function ReferenceGrid({ data }: { data: ReferenceStatus | null }) {
  const refs: Array<[string, keyof ReferenceStatus]> = [
    ["Sri Lanka boundary", "country_boundary"], ["Admin areas", "administrative_areas"],
    ["LEA geometry", "lea_areas"], ["LEA area circles", "lea_area_circles"],
    ["Provider regions", "provider_regions"],
  ];
  return <div className="reference-list">{refs.map(([label, key]) => {
    const item = data?.[key] as ReferenceFile | undefined;
    const loaded = item?.status === "LOADED";
    return <div className="reference-list-item" key={key}><span className={`reference-status ${!data ? "checking" : loaded ? "loaded" : "missing"}`} /><span>{label}</span><strong>{!data ? "Checking…" : loaded ? `${number(item.feature_or_mapping_count)} loaded` : "Not installed"}</strong></div>;
  })}</div>;
}

async function loadReferenceInventory(fetcher: (path: string, init?: RequestInit) => Promise<Response>, setStatus: (value: ReferenceStatus) => void, setError: (value: string) => void) {
  try { setStatus(await json<ReferenceStatus>(await fetcher("/api/reference-data/status"))); }
  catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load reference data status."); }
}

function CustomerDialog({ customer, markers, center, line, googleMapsKey, onClose }: { customer: Customer; markers: MapMarker[]; center: [number, number]; line?: MapLine; googleMapsKey: string; onClose: () => void }) {
  const address = customer.source_values?.address || "Address not provided";
  const phone = customer.source_values?.router_number || customer.source_values?.phone_number || "Not provided";
  useEffect(() => {
    const listener = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [onClose]);
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="detail-dialog customer-dialog" role="dialog" aria-modal="true" aria-labelledby="customer-dialog-title">
    <header className="dialog-header"><div><p className="eyebrow">CUSTOMER LOCATION RECORD</p><h2 id="customer-dialog-title">{customer.customer_id || `Source row ${customer.row_number}`}</h2><p>{address}</p></div><button className="icon-button close-dialog" onClick={onClose} aria-label="Close customer details"><X size={18} /></button></header>
    <div className="customer-dialog-layout"><div className="customer-evidence">
      <div className="dialog-status"><span>Record result</span><Badge value={customer.overall_status} /></div>
      <div className="evidence-group"><p>Customer</p><dl><dt>Landline</dt><dd>{phone}</dd><dt>Customer coordinates</dt><dd>{customer.customer_location ? `${customer.customer_location.lat.toFixed(6)}, ${customer.customer_location.lon.toFixed(6)}` : "Not available"}</dd><dt>Address check</dt><dd><Badge value={customer.checks?.address_zone?.status} /> {customer.checks?.address_zone?.distance_m != null ? `${number(Math.round(customer.checks.address_zone.distance_m))} m from address match` : ""}</dd><dt>Phone area</dt><dd><Badge value={customer.checks?.phone_area?.status} /> {customer.checks?.phone_area?.area_name || customer.checks?.phone_area?.area_code || ""}</dd><dt>Country boundary</dt><dd><Badge value={customer.checks?.country_boundary?.status || customer.customer_inside_sri_lanka} /></dd></dl></div>
      <div className="evidence-group"><p>Connected FDP</p><dl><dt>FDP ID</dt><dd>{customer.dp_id || "Not assigned"}</dd><dt>FDP coordinates</dt><dd>{customer.dp_location_observed ? `${customer.dp_location_observed.lat.toFixed(6)}, ${customer.dp_location_observed.lon.toFixed(6)}` : "Not available"}</dd><dt>Direct distance</dt><dd>{customer.checks?.customer_dp_distance?.distance_m != null ? `${number(Math.round(customer.checks.customer_dp_distance.distance_m))} m` : "Not calculated"}</dd><dt>Distance threshold</dt><dd>{customer.checks?.customer_dp_distance?.status ? <Badge value={customer.checks.customer_dp_distance.status} /> : "—"}</dd></dl></div>
      <div className="evidence-group"><p>Findings</p>{customer.findings?.length ? <ul className="finding-list">{customer.findings.map((finding, index) => <li key={`${finding.code}-${index}`}><Badge value={finding.severity === "error" ? "FAIL" : finding.severity === "info" ? "UNVERIFIED" : "REVIEW"} /><span>{finding.message}</span></li>)}</ul> : <div className="no-findings"><Check size={15} />No validation findings</div>}</div>
    </div><div className="customer-dialog-map"><div className="map-dialog-heading"><div><strong>Customer ↔ FDP</strong><span>Blue: customer · Orange: FDP · Line: direct distance</span></div></div>{markers.length ? <MapView markers={markers} center={center} zoom={markers.length > 1 ? 15 : 14} tileUrl={OSM_TILES} path={line} googleMapsKey={googleMapsKey || undefined} /> : <div className="map-empty">No usable customer or FDP coordinates are available for this row.</div>}</div></div>
    <footer className="dialog-footer"><span>Source CSV row {number(customer.row_number)}</span><button className="button button-secondary" onClick={onClose}>Close details</button></footer>
  </section></div>;
}

function FdpDialog({ fdp, runId, apiFetch, googleMapsKey, onClose }: { fdp: FDP; runId: string; apiFetch: (path: string, init?: RequestInit) => Promise<Response>; googleMapsKey: string; onClose: () => void }) {
  const [mapData, setMapData] = useState<FDPCustomerMapData | null>(null);
  const [mapLoading, setMapLoading] = useState(true);
  const [mapError, setMapError] = useState("");
  useEffect(() => {
    let active = true;
    setMapData(null);
    setMapLoading(true);
    setMapError("");
    void apiFetch(`/api/validation-runs/${encodeURIComponent(runId)}/dps/${encodeURIComponent(fdp.dp_id)}/customers?limit=1000`)
      .then((response) => json<FDPCustomerMapData>(response))
      .then((data) => { if (active) setMapData(data); })
      .catch((cause: unknown) => { if (active) setMapError(cause instanceof Error ? cause.message : "Could not load assigned customer locations."); })
      .finally(() => { if (active) setMapLoading(false); });
    return () => { active = false; };
  }, [apiFetch, fdp.dp_id, runId]);

  const point = fdp.dp_location_observed;
  const markers: MapMarker[] = [
    ...(point ? [{ id: `dp-${fdp.dp_id}`, latitude: point.lat, longitude: point.lon, title: `FDP ${fdp.dp_id}`, detail: `${number(fdp.assigned_customer_count)} connected customer(s)`, kind: "dp" as const, color: "#f2c94c" }] : []),
    ...(mapData?.customers || []).map((customer) => ({
      id: `customer-${customer.row_number}`,
      latitude: customer.latitude,
      longitude: customer.longitude,
      title: customer.customer_id || `Customer row ${customer.row_number}`,
      detail: customer.address || `Assigned to FDP ${fdp.dp_id}`,
      kind: "customer" as const,
    })),
  ];
  const locationSummary = mapLoading
    ? "Loading customer locations..."
    : mapError
      ? mapError
      : `${number(mapData?.returned_points || 0)} customer point(s) shown / ${number(mapData?.assigned_rows || 0)} linked row(s) assigned${mapData?.truncated ? " / showing at most 1,000 located rows" : ""}${mapData && mapData.assigned_rows > mapData.mapped_rows ? ` / ${number(mapData.assigned_rows - mapData.mapped_rows)} row(s) have no usable coordinates` : ""}`;

  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="detail-dialog fdp-dialog" role="dialog" aria-modal="true" aria-labelledby="fdp-dialog-title"><header className="dialog-header"><div><p className="eyebrow">FDP LOCATION RECORD</p><h2 id="fdp-dialog-title">{fdp.dp_id}</h2><p>{number(fdp.assigned_customer_count)} distinct customers assigned | {number(fdp.port_capacity)} ports available by policy</p></div><button className="icon-button close-dialog" onClick={onClose} aria-label="Close FDP details"><X size={18} /></button></header><div className="fdp-dialog-content"><div className="fdp-detail-kpis"><MiniStat label="Assigned customers" value={number(fdp.assigned_customer_count)} /><MiniStat label="Free ports" value={number(fdp.available_port_count)} /><MiniStat label="Port capacity" value={number(fdp.port_capacity)} /></div><div className="fdp-detail-checks"><span>Country check <Badge value={fdp.country_boundary_status} /></span><span>Coordinate consistency <Badge value={fdp.coordinate_consistency} /></span><span>FDP assessment <Badge value={fdp.dp_assessment_status} /></span><span>Capacity check <Badge value={fdp.port_capacity_status} /></span></div><div className="map-dialog-heading"><div><strong>FDP and connected customers</strong><span>Yellow = FDP / Blue = customers assigned to this FDP</span><small>{locationSummary}</small></div></div>{markers.length ? <MapView markers={markers} center={point ? [point.lat, point.lon] : [markers[0].latitude, markers[0].longitude]} zoom={15} tileUrl={OSM_TILES} googleMapsKey={googleMapsKey || undefined} fitToMarkers /> : <div className="map-empty">No usable FDP or assigned customer coordinates are available.</div>}</div><footer className="dialog-footer"><span>Click a blue customer dot or yellow FDP dot to see its details.</span><button className="button button-secondary" onClick={onClose}>Close details</button></footer></section></div>;
}
