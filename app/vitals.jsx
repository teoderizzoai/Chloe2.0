/* VitalsTab — history charts for affect + energy. */

const SERIES_DEF = [
  { key: "energy",      label: "Energy",      min: 0,  max: 1,  color: "#c8903a", note: "0 = depleted · 1 = full" },
  { key: "valence",     label: "Valence",     min: -1, max: 1,  color: "#7a9070", note: "−1 = very negative · +1 = very positive", centered: true },
  { key: "arousal",     label: "Arousal",     min: 0,  max: 1,  color: "#c07070", note: "0 = calm · 1 = activated" },
  { key: "social_pull", label: "Social pull", min: 0,  max: 1,  color: "#6b8fa6", note: "0 = withdrawn · 1 = drawn in" },
  { key: "openness",    label: "Openness",    min: 0,  max: 1,  color: "#9b8ab4", note: "0 = closed · 1 = curious" },
];

const RANGES = [
  { label: "24h", hours: 24 },
  { label: "3d",  hours: 72 },
  { label: "7d",  hours: 168 },
];

function VitalsTab() {
  const [hours, setHours] = React.useState(24);
  const [pts,   setPts]   = React.useState(null);
  const [busy,  setBusy]  = React.useState(true);
  const [err,   setErr]   = React.useState(null);

  React.useEffect(() => {
    setBusy(true);
    setErr(null);
    const base = window.__CHLOE_API_BASE__ || "";
    fetch(`${base}/admin/vitals/history?hours=${hours}`)
      .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(d => { setPts(d.points || []); setBusy(false); })
      .catch(e => { setErr(e.message); setBusy(false); });
  }, [hours]);

  const rangeLabel = RANGES.find(r => r.hours === hours)?.label ?? `${hours}h`;

  return (
    <div>
      <p className="section-intro">
        Her inner weather over time — one snapshot every 10 minutes from the pressure loop.
      </p>

      <div className="filter-row" style={{ marginBottom: 20 }}>
        {RANGES.map(r => (
          <button
            key={r.hours}
            className={"small" + (hours === r.hours ? " primary" : "")}
            onClick={() => setHours(r.hours)}
          >
            {r.label}
          </button>
        ))}
      </div>

      {busy && (
        <div style={{ color: "var(--ink-mute)", padding: 40, textAlign: "center" }}>loading…</div>
      )}

      {!busy && err && (
        <div className="card" style={{ color: "var(--rose)", padding: 20 }}>
          Could not load vitals history: {err}
        </div>
      )}

      {!busy && !err && pts && pts.length === 0 && (
        <div className="card" style={{ textAlign: "center", color: "var(--ink-mute)", padding: 40 }}>
          <div style={{ fontSize: 28, marginBottom: 12 }}>○</div>
          No snapshots yet. Data accumulates every 10 minutes once the pressure loop is running.
        </div>
      )}

      {!busy && !err && pts && pts.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {SERIES_DEF.map(s => {
            const series = pts.map(p => ({ t: p.created_at, v: p[s.key] }));
            const last   = series[series.length - 1];
            const first  = series[0];
            return (
              <div key={s.key} className="card">
                <div className="hd">
                  {s.label}
                  <span className="stretch" />
                  <span style={{ fontSize: 12, color: "var(--ink-mute)", fontVariantNumeric: "tabular-nums" }}>
                    now: <strong style={{ color: s.color }}>{last ? last.v.toFixed(3) : "—"}</strong>
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "var(--ink-mute)", marginBottom: 6 }}>{s.note}</div>
                <SparkChart series={series} min={s.min} max={s.max} color={s.color} centered={s.centered} />
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: 10, color: "var(--ink-mute)" }}>
                  <span>{first  ? fmtTime(first.t)  : ""}</span>
                  <span>{last   ? fmtTime(last.t)   : ""}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SparkChart({ series, min, max, color, centered }) {
  const W = 600, H = 80;
  const pad = { top: 8, right: 8, bottom: 8, left: 8 };
  const iW = W - pad.left - pad.right;
  const iH = H - pad.top - pad.bottom;

  if (!series || series.length < 2) {
    return (
      <div style={{ height: H, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--ink-mute)", fontSize: 12 }}>
        not enough data
      </div>
    );
  }

  const times  = series.map(p => new Date(p.t).getTime());
  const tMin   = Math.min(...times);
  const tMax   = Math.max(...times);
  const tRange = tMax - tMin || 1;

  const mapX = t  => pad.left + ((new Date(t).getTime() - tMin) / tRange) * iW;
  const mapY = v  => pad.top  + (1 - (v - min) / (max - min)) * iH;

  const ptStr  = series.map(p => `${mapX(p.t).toFixed(1)},${mapY(p.v).toFixed(1)}`).join(" ");
  const baseY  = centered ? mapY(0) : H - pad.bottom;
  const fX     = mapX(series[0].t).toFixed(1);
  const lX     = mapX(series[series.length - 1].t).toFixed(1);
  const areaStr = `${fX},${baseY} ${ptStr} ${lX},${baseY}`;
  const gradId = `vg${color.replace("#", "")}`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: H, display: "block" }}>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={color} stopOpacity="0.22" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>

      {/* zero / baseline rule for valence */}
      {centered && (
        <line
          x1={pad.left} y1={mapY(0)}
          x2={W - pad.right} y2={mapY(0)}
          stroke="currentColor" strokeWidth="0.6"
          strokeDasharray="3,3" opacity="0.25"
        />
      )}

      {/* area fill */}
      <polygon points={areaStr} fill={`url(#${gradId})`} />

      {/* line */}
      <polyline
        points={ptStr}
        fill="none"
        stroke={color}
        strokeWidth="1.8"
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* tail dot */}
      <circle
        cx={mapX(series[series.length - 1].t)}
        cy={mapY(series[series.length - 1].v)}
        r="3"
        fill={color}
      />
    </svg>
  );
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts.replace(" ", "T") + (ts.includes("T") ? "" : "Z"));
  if (isNaN(d)) return ts.slice(11, 16) || ts.slice(0, 16);
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
