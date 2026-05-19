/* App orchestrator — login, tabs, header, chat fab. */

const TABS = [
  { id:"now",           label:"Now" },
  { id:"memory",        label:"Memory" },
  { id:"relationships", label:"Relationships" },
  { id:"goals",         label:"Goals" },
  { id:"inner",         label:"Inner" },
  { id:"audit",         label:"Audit" },
  { id:"confirmations", label:"Confirmations" },
  { id:"identity",      label:"Identity" },
  { id:"settings",      label:"Settings" },
  { id:"debug",         label:"Debug" },
  { id:"admin",         label:"Admin" },
];

function App() {
  // ── Tweaks (theme) ─────────────────────────────────────────────
  const [t, setTweak] = useTweaks(window.TWEAK_DEFAULTS);
  React.useEffect(() => {
    document.documentElement.setAttribute("data-theme", t.theme || "light");
  }, [t.theme]);

  // ── Login state ────────────────────────────────────────────────
  const [userName, setUserName] = React.useState(() => {
    try { return localStorage.getItem("chloe_user") || ""; } catch { return ""; }
  });
  const onLogin = (name) => {
    try { localStorage.setItem("chloe_user", name); } catch {}
    setUserName(name);
  };
  const onLogout = () => {
    try { localStorage.removeItem("chloe_user"); } catch {}
    setUserName("");
  };

  // ── Tab routing (persisted via URL hash) ───────────────────────
  const [tab, setTab] = React.useState(() => {
    const h = (window.location.hash || "").replace("#", "");
    return TABS.find(t => t.id === h) ? h : "now";
  });
  React.useEffect(() => {
    window.location.hash = tab;
  }, [tab]);

  // ── Chat ───────────────────────────────────────────────────────
  const [chatOpen, setChatOpen] = React.useState(false);

  // ── Local mutable copy of state (for confirms / settings toggles) ──
  const [data, setData] = React.useState(() => structuredClone(window.CHLOE));

  // ── Load live data from the backend ───────────────────────────────────────
  React.useEffect(() => {
    if (window.__CHLOE_LOAD__) {
      window.__CHLOE_LOAD__.then(live => {
        if (live) setData(structuredClone(live));
      });
    }
  }, []);

  // ── Live affect via WebSocket ─────────────────────────────────────────────
  React.useEffect(() => {
    const base = window.__CHLOE_WS_BASE__ || '';
    let ws;
    try {
      ws = new WebSocket(base + '/v1/dashboard/ws');
    } catch (e) {
      return;
    }
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        const affectSnap = (msg.type === 'snapshot' ? msg.data?.affect?.[0] : msg.affect?.[0]);
        if (affectSnap) {
          setData(d => ({
            ...d,
            affect: {
              ...d.affect,
              valence:     affectSnap.valence     ?? d.affect.valence,
              arousal:     affectSnap.arousal     ?? d.affect.arousal,
              social_pull: affectSnap.social_pull ?? d.affect.social_pull,
              openness:    affectSnap.openness    ?? d.affect.openness,
              label:       affectSnap.label       || d.affect.label,
            },
          }));
        }
      } catch (_) {}
    };
    ws.onerror = () => {};
    return () => { try { ws.close(); } catch (_) {} };
  }, []);

  const resolveTicket = (id, action) => {
    setData(d => {
      const t = d.confirmations.find(x => x.id === id);
      if (!t) return d;
      const newConf = d.confirmations.filter(x => x.id !== id);
      const auditAt = nowTime();
      const newAudit = [...d.audit, {
        at: auditAt, tool: t.tool, verb: t.verb, auth: t.auth,
        state: action === "confirm" ? "executed" : "denied",
        intent: t.intent, cost: 0.0015,
      }];
      return { ...d, confirmations: newConf, audit: newAudit };
    });
    const base = window.__CHLOE_API_BASE__ || '';
    fetch(`${base}/v1/confirmations/${id}/${action === "confirm" ? "confirm" : "deny"}`, {
      method: 'POST',
    }).catch(err => console.warn('[chloe] confirm/deny failed', err));
  };

  const togglePref = (key, value) => {
    setData(d => ({
      ...d,
      settings: { ...d.settings, [key]: value }
    }));
  };

  if (!userName) {
    return <LoginScreen onLogin={onLogin} />;
  }

  const pendingCount = data.confirmations.length;

  return (
    <div className="app">
      <Letterhead data={data} userName={userName} onLogout={onLogout} />
      <nav className="tabs" role="tablist">
        {TABS.map(tt => (
          <button
            key={tt.id}
            className="tab"
            role="tab"
            aria-selected={tab === tt.id}
            onClick={() => setTab(tt.id)}
          >
            {tt.label}
            {tt.id === "confirmations" && pendingCount > 0 && <span className="dot" title={pendingCount + " pending"}></span>}
          </button>
        ))}
      </nav>

      {tab === "now"           && <NowTab data={data} />}
      {tab === "memory"        && <MemoryTab data={data} />}
      {tab === "relationships" && <RelationshipsTab data={data} />}
      {tab === "goals"         && <GoalsTab data={data} />}
      {tab === "inner"         && <InnerStateTab data={data} />}
      {tab === "audit"         && <AuditTab data={data} />}
      {tab === "confirmations" && <ConfirmationsTab data={data} onResolve={resolveTicket} />}
      {tab === "identity"      && <IdentityTab data={data} />}
      {tab === "settings"      && <SettingsTab data={data} onTogglePref={togglePref} />}
      {tab === "debug"         && <DebugTab />}
      {tab === "admin"         && <AdminTab />}

      <button className="chat-fab" onClick={() => setChatOpen(o => !o)} aria-label="Chat with Chloe">
        {chatOpen ? "✕" : "✍"}
        {!chatOpen && pendingCount > 0 && <span className="nb">{pendingCount}</span>}
      </button>

      <ChatDrawer
        userName={userName}
        open={chatOpen}
        onClose={() => setChatOpen(false)}
      />

      <TweaksPanel title="Tweaks">
        <TweakSection label="Theme" />
        <TweakRadio
          label="Paper / Sepia / Ink"
          value={t.theme}
          onChange={v => setTweak("theme", v)}
          options={[
            { value:"light", label:"Paper" },
            { value:"sepia", label:"Sepia" },
            { value:"dark",  label:"Ink"   },
          ]}
        />
      </TweaksPanel>
    </div>
  );
}


/* ─────────────────────── Letterhead ─────────────────────── */
function Letterhead({ data, userName, onLogout }) {
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "good morning" : hour < 18 ? "good afternoon" : "good evening";
  const affectLabel = data.affect && data.affect.label ? ` — ${data.affect.label}` : "";
  const since = data.meta && data.meta.since ? data.meta.since : "";
  const location = data.meta && data.meta.location ? data.meta.location : "";
  const localTime = data.meta && data.meta.local_time ? data.meta.local_time : "";
  const weather = data.meta && data.meta.weather ? data.meta.weather : "";

  const metaParts = [
    location ? `in ${location}` : null,
    [localTime, weather].filter(Boolean).join(", ") || null,
    data.affect && data.affect.label ? data.affect.label : null,
  ].filter(Boolean);

  return (
    <header className="letterhead">
      <Avatar size="sm" />
      <div className="who">
        <h1>Chloe</h1>
        <div className="where">
          {metaParts.map((p, i) => <span key={i} className="bullet">{p}</span>)}
        </div>
      </div>
      <div className="greet">
        {greeting}, {userName}{affectLabel}.
        {since && (
          <div style={{marginTop: 6, fontSize: 11, color:"var(--ink-mute)"}} className="mono">
            known each other since {since} ·
            <button className="ghost small" style={{marginLeft: 6, padding:"0 6px", border:"none", textDecoration:"underline"}} onClick={onLogout}>not you?</button>
          </div>
        )}
        {!since && (
          <div style={{marginTop: 6, fontSize: 11, color:"var(--ink-mute)"}} className="mono">
            <button className="ghost small" style={{padding:"0 6px", border:"none", textDecoration:"underline"}} onClick={onLogout}>not you?</button>
          </div>
        )}
      </div>
    </header>
  );
}


/* ─────────────────────── Login Screen ─────────────────────── */
function LoginScreen({ onLogin }) {
  const [name, setName] = React.useState("");
  const submit = (e) => {
    e.preventDefault();
    const n = name.trim();
    if (n) onLogin(n);
  };
  return (
    <div className="scrim">
      <div className="panel">
        <Avatar size="sm" />
        <h2 style={{marginTop: 18}}>She's been waiting.</h2>
        <p>What should she call you?</p>
        <form onSubmit={submit}>
          <input
            autoFocus
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="your name"
            maxLength={40}
          />
          <button className="primary" type="submit" disabled={!name.trim()}>
            open the door
          </button>
          <div className="hint">she remembers locally · no sign-up</div>
        </form>
      </div>
    </div>
  );
}


/* ─────────────────────── Mount ─────────────────────── */
const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);

function nowTime() {
  const d = new Date();
  return d.getHours().toString().padStart(2, "0") + ":" + d.getMinutes().toString().padStart(2, "0");
}
