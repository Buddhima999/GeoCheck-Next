import { useEffect, useState } from "react";
import {
  Activity, AlertTriangle, ChevronRight, CircleHelp, Database, FileCheck2,
  FileSpreadsheet, Map, MapPinned, Network, Search, ShieldCheck, Users,
} from "lucide-react";

type HealthState = "checking" | "connected" | "unavailable";

const hierarchy = [
  { label: "Province", detail: "National overview and province-level quality", icon: Map },
  { label: "District", detail: "Compare districts and find concentrated issues", icon: MapPinned },
  { label: "Region", detail: "Provider-defined operational region", icon: Network },
  { label: "DP explorer", detail: "Coordinate consistency and customer associations", icon: Database },
  { label: "Customer records", detail: "Individual checks, evidence and map inspection", icon: Users },
];

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
  const [query, setQuery] = useState("");

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

  const healthText = health === "connected" ? "MongoDB connected" :
    health === "checking" ? "Checking database…" : "API / MongoDB not connected";

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><MapPinned size={22} /></div><div><strong>GeoCheck</strong><span> NEXT</span></div></div>
      <div className="workspace-label">WORKSPACE</div>
      <button className="nav-item active"><Activity size={17} /> Overview</button>
      <button className="nav-item"><Map size={17} /> Geographic hierarchy</button>
      <button className="nav-item"><AlertTriangle size={17} /> What's wrong</button>
      <button className="nav-item"><FileSpreadsheet size={17} /> Validation runs</button>
      <div className="sidebar-bottom"><div className="privacy-note"><ShieldCheck size={16} /><span>Restricted customer-data access is required before production use.</span></div><span className="muted tiny">Foundation v0.1.0</span></div>
    </aside>
    <main className="main-area">
      <header className="topbar"><div><p className="eyebrow">LOCATION QUALITY PLATFORM</p><h1>National overview</h1><p className="subtitle">Investigate customer and distribution-point data quality, from national summaries to individual records.</p></div><div className={`connection-pill ${health}`}><span className="connection-dot" />{healthText}</div></header>
      <section className="hero-panel"><div className="hero-copy"><span className="hero-tag"><ShieldCheck size={14} /> VALIDATION WORKSPACE</span><h2>Trust the evidence.<br />Find the records that need attention.</h2><p>The dashboard will use verified rules and reference data. No production percentages appear until a validation run is completed and reconciled.</p></div><div className="hero-art" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="hero-pin"><MapPinned size={32} /></div><span className="hero-point point-a" /><span className="hero-point point-b" /><span className="hero-point point-c" /></div></section>
      <section className="section-heading"><div><h2>Dataset overview</h2><p>Expected operating scale — not yet validated data.</p></div><span className="status-chip"><Activity size={14} /> Awaiting data contract</span></section>
      <section className="metrics-grid">
        <MetricCard label="Expected customers" value="600,000" note="Planning figure" icon={Users} />
        <MetricCard label="Expected DPs" value="150,000" note="Planning figure" icon={Network} />
        <MetricCard label="Validation runs" value="0" note="No real run yet" icon={FileCheck2} />
        <MetricCard label="Confirmed errors" value="—" note="Not calculated" icon={AlertTriangle} />
      </section>
      <section className="content-grid">
        <article className="panel"><div className="panel-heading"><div><h2>Explore by geography</h2><p>Move down the hierarchy to inspect locations and records.</p></div><span className="icon-bubble"><Map size={18} /></span></div><div className="hierarchy-list">{hierarchy.map(({ label, detail, icon: Icon }, index) => <div className="hierarchy-row" key={label}><div className="hierarchy-icon"><Icon size={17} /></div><div className="hierarchy-copy"><strong>{label}</strong><span>{detail}</span></div><ChevronRight size={17} className="chevron muted" />{index !== hierarchy.length - 1 && <div className="hierarchy-connector" />}</div>)}</div><div className="panel-footnote"><CircleHelp size={15} /> Region and LEA mappings must come from the provider; they will not be guessed.</div></article>
        <article className="panel"><div className="panel-heading"><div><h2>What's wrong</h2><p>Issue categories will become selectable filters.</p></div><span className="icon-bubble warning"><AlertTriangle size={18} /></span></div><div className="empty-state"><div className="empty-icon"><FileCheck2 size={24} /></div><strong>No validation results yet</strong><span>Counts and percentages remain blank until rules are confirmed and data is validated.</span></div><div className="legend-row"><span className="legend-dot pass" /> Pass <span className="legend-dot fail" /> Fail <span className="legend-dot unknown" /> Unverified</div></article>
      </section>
      <section className="panel search-panel"><div className="panel-heading"><div><h2>Find a customer or DP</h2><p>Search will query indexed MongoDB records after the API is implemented.</p></div></div><form className="search-form" onSubmit={(event) => event.preventDefault()}><Search size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Customer ID, DP ID, phone or address" aria-label="Search customer or DP" /><button type="submit" disabled={!query.trim()}>Search</button></form><p className="small-note">Search is a visual placeholder in this foundation; it does not query records yet.</p></section>
      <footer className="footer"><span>GeoCheck Next · Foundation v0.1.0</span><span><ShieldCheck size={13} /> Built for auditable validation</span></footer>
    </main>
  </div>;
}
