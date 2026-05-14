/* All tab pages. Each is a small component that takes the data slice. */

/* ───────────────────────── NOW ───────────────────────── */
function NowTab({ data }) {
  const C = data;
  return (
    <div>
      <div className="grid now">
        {/* Left: portrait + status */}
        <div>
          <div style={{display:"flex", flexDirection:"column", alignItems:"center", gap: 18}}>
            <Avatar size="lg" monogram="C" />
            <div style={{textAlign:"center"}}>
              {C.affect.label && <div className="pill sage" style={{marginBottom: 10}}>{C.affect.label}</div>}
              {C.affect.sublabel && <div className="status-line">{C.affect.sublabel}</div>}
              <div className="status-meta">
                <span className="live">live{C.meta.location ? " · " + C.meta.location : ""}{C.meta.local_time ? " · " + C.meta.local_time : ""}</span>
              </div>
            </div>
          </div>

          <div className="card" style={{marginTop: 22}}>
            <div className="hd">Vitals<span className="stretch"></span></div>
            <div className="vitals">
              <Vital lab="energy"         v={C.vitals.energy.value}         label={C.vitals.energy.label} />
              <Vital lab="rest"           v={1 - C.vitals.rest_debt.value}  label={C.vitals.rest_debt.label} cls="sage" />
              <Vital lab="social"         v={C.vitals.social_battery.value} label={C.vitals.social_battery.label} cls="rose" />
              <Vital lab="curiosity"      v={C.vitals.curiosity.value}      label={C.vitals.curiosity.label} cls="gold" />
            </div>
          </div>
        </div>

        {/* Right: activity, affect, garden */}
        <div style={{display:"flex", flexDirection:"column", gap: 18}}>
          <div className="card">
            <div className="hd">Right now<span className="stretch"></span></div>
            <div className="lede">{C.current_activity.line}</div>
            <div style={{marginTop: 12, fontSize: 13, color:"var(--ink-mute)"}} className="mono">
              since {C.current_activity.since} · {C.current_activity.artifact}
            </div>
          </div>

          <div className="card">
            <div className="hd">Affect — dimensional<span className="stretch"></span></div>
            <div className="affect">
              <AffectRow lab="valence" v={C.affect.valence} centered />
              <AffectRow lab="arousal" v={C.affect.arousal} cls="rose" />
              <AffectRow lab="social pull" v={C.affect.social_pull} cls="sage" />
              <AffectRow lab="openness"    v={C.affect.openness} cls="gold" />
            </div>
            <hr className="rule" />
            <div style={{fontSize: 14, color:"var(--ink-soft)", fontStyle:"italic"}}>
              Arc: <span style={{color:"var(--ink)"}}>{C.arc.name}</span>. {C.arc.summary}
            </div>
          </div>

          <div className="card">
            <Garden garden={C.garden} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Vital({ lab, v, label, cls }) {
  return (
    <div className={"vital" + (cls ? " " + cls : "")}>
      <span className="lab">{lab}</span>
      <span className="bar" style={{"--v": (v*100).toFixed(0) + "%"}}></span>
      <span className="val">{label}</span>
    </div>
  );
}

function AffectRow({ lab, v, cls, centered }) {
  if (centered) {
    // Valence [-1..1] — bar grows from center
    const pct = Math.abs(v) * 50;
    const left = v < 0 ? (50 - pct) : 50;
    return (
      <div className="affect-row">
        <span className="lab">{lab}</span>
        <span className="track centered">
          <span className={"fill " + (v < 0 ? "rose" : "sage")} style={{"--w": pct + "%", "--l": left + "%"}}></span>
        </span>
        <span className="num">{v.toFixed(2)}</span>
      </div>
    );
  }
  return (
    <div className="affect-row">
      <span className="lab">{lab}</span>
      <span className="track"><span className={"fill " + (cls || "")} style={{"--v": (v*100).toFixed(0) + "%"}}></span></span>
      <span className="num">{v.toFixed(2)}</span>
    </div>
  );
}


/* ───────────────────────── MEMORY ───────────────────────── */
function MemoryTab({ data }) {
  const [kind, setKind] = React.useState("all");
  const [query, setQuery] = React.useState("");
  const [tag, setTag] = React.useState(null);
  const kinds = ["all", "episodic", "semantic", "autobiographical", "procedural"];

  // All tags used across memories, ranked by frequency.
  const tagFreq = React.useMemo(() => {
    const m = {};
    (data.memories || []).forEach(mm => (mm.tags || []).forEach(t => { m[t] = (m[t] || 0) + 1; }));
    return Object.entries(m).sort((a,b) => b[1] - a[1]);
  }, [data.memories]);

  const items = (data.memories || []).filter(m => {
    if (kind !== "all" && m.kind !== kind) return false;
    if (tag && !(m.tags || []).includes(tag)) return false;
    if (query) {
      const q = query.toLowerCase();
      const hay = (m.text + " " + (m.tags || []).join(" ") + " " + (m.anchor || "")).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  return (
    <div>
      <p className="section-intro">
        What she remembers, in four kinds: things that happened (episodic), things she knows
        (semantic), the story she tells herself about herself (autobiographical), and how to use
        her tools well (procedural). Append-only — never edited, never deleted.
      </p>

      {/* Search bar — like a margin note */}
      <div className="mem-search">
        <span className="mem-search-icon" aria-hidden="true">⌕</span>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="search her memory — words, names, places, anchors…"
          className="mem-search-input"
        />
        {query && <button className="ghost small" onClick={() => setQuery("")}>clear</button>}
      </div>

      <div className="filter-row">
        {kinds.map(k => (
          <button key={k} className={"small" + (kind === k ? " primary" : "")} onClick={() => setKind(k)}>
            {k}
          </button>
        ))}
      </div>

      {/* Tag cloud */}
      <div className="tag-cloud">
        <span className="tc-label">tags</span>
        {tagFreq.map(([t, n]) => (
          <button
            key={t}
            className={"tc-tag" + (tag === t ? " on" : "")}
            onClick={() => setTag(tag === t ? null : t)}
          >
            #{t} <span className="tc-n">{n}</span>
          </button>
        ))}
        {tag && <button className="ghost small" onClick={() => setTag(null)}>clear tag</button>}
      </div>

      <div className="mem-summary">
        {items.length} of {data.memories.length} memories
        {kind !== "all" && <> · kind <i>{kind}</i></>}
        {tag && <> · tagged <span className="mono">#{tag}</span></>}
        {query && <> · matching <i>"{query}"</i></>}
      </div>

      <div className="card">
        {items.length === 0 ? (
          <div className="empty">nothing matches that.<br/><span style={{fontSize:13}}>try a different word, or clear filters.</span></div>
        ) : (
          <div className="timeline">
            {items.map((m, i) => (
              <div className={"mem " + m.kind} key={i}>
                <div className="meta">
                  <span>{m.kind}</span>
                  {m.at !== "—" && <span>at {m.at}</span>}
                  {m.salience != null && <span>salience {m.salience.toFixed(2)}</span>}
                  {m.confidence != null && <span>confidence {m.confidence.toFixed(2)}</span>}
                  {m.anchor && <span>↳ {m.anchor}</span>}
                </div>
                <div className="text">
                  {highlight(m.text, query)}
                </div>
                {m.tags && (
                  <div className="tags">
                    {m.tags.map(t => (
                      <button
                        key={t}
                        className="t"
                        onClick={() => setTag(t)}
                        title={"filter by " + t}
                      >{t}</button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function highlight(text, query) {
  if (!query) return text;
  const q = query.trim();
  if (!q) return text;
  try {
    const re = new RegExp("(" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig");
    const parts = text.split(re);
    return parts.map((p, i) =>
      i % 2 === 1
        ? <mark key={i} className="mem-hit">{p}</mark>
        : <React.Fragment key={i}>{p}</React.Fragment>
    );
  } catch { return text; }
}


/* ─────────────────────── ONBOARDING FLOW ─────────────────────── */
const ONBOARDING_QUESTIONS = [
  { id: "intro",   q: "Tell me who you are. Name, where you're from, whatever feels like the basics." },
  { id: "work",    q: "What do you do for work? What does a typical week look like?" },
  { id: "family",  q: "Tell me about your family. Who's in it, where are they, what are they like?" },
  { id: "friends", q: "Who are your close friends? Names and what they're like, or how you know them." },
  { id: "pets",    q: "Any pets? Tell me about them." },
  { id: "tastes",  q: "What are you into? Hobbies, music, things you spend time on, whatever you actually enjoy." },
];

function OnboardingFlow({ onDone, onCancel }) {
  const [step, setStep] = React.useState(0);
  const [answers, setAnswers] = React.useState({});
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [done, setDone] = React.useState(false);
  const textRef = React.useRef(null);

  React.useEffect(() => { if (textRef.current) textRef.current.focus(); }, [step]);

  const q = ONBOARDING_QUESTIONS[step];
  const answer = answers[q.id] || "";
  const isLast = step === ONBOARDING_QUESTIONS.length - 1;

  const next = () => {
    if (isLast) {
      submit();
    } else {
      setStep(s => s + 1);
    }
  };

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const base = window.__CHLOE_API_BASE__ || '';
      const payload = ONBOARDING_QUESTIONS.map(item => ({
        question: item.q,
        answer: answers[item.id] || "",
      }));
      const resp = await fetch(base + '/admin/onboarding/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answers: payload }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      setDone(true);
      setTimeout(onDone, 2000);
    } catch (e) {
      setError(e.message);
      setSubmitting(false);
    }
  };

  if (done) {
    return (
      <div className="card" style={{marginTop: 24, padding: "32px 28px", textAlign: "center"}}>
        <div style={{fontSize: 22, marginBottom: 12}}>She's got it.</div>
        <div style={{fontSize: 15, color: "var(--ink-soft)"}}>
          Memories stored. Synthesis running. She'll know you in the next conversation.
        </div>
      </div>
    );
  }

  return (
    <div className="card" style={{marginTop: 24, padding: "28px"}}>
      <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom: 20}}>
        <div style={{fontSize: 13, color: "var(--ink-mute)"}} className="mono">
          onboarding · {step + 1} of {ONBOARDING_QUESTIONS.length}
        </div>
        <button className="ghost small" onClick={onCancel}>cancel</button>
      </div>

      <div style={{
        display: "flex", gap: 6, marginBottom: 24,
      }}>
        {ONBOARDING_QUESTIONS.map((_, i) => (
          <span key={i} style={{
            flex: 1, height: 3, borderRadius: 2,
            background: i <= step ? "var(--sage)" : "var(--rule)",
            transition: "background 0.2s",
          }} />
        ))}
      </div>

      <div style={{fontSize: 18, lineHeight: 1.5, marginBottom: 18, fontStyle: "italic", color: "var(--ink-soft)"}}>
        {q.q}
      </div>

      <textarea
        ref={textRef}
        value={answer}
        onChange={e => setAnswers(a => ({ ...a, [q.id]: e.target.value }))}
        placeholder="your answer…"
        onKeyDown={e => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && answer.trim()) next();
        }}
        style={{
          width: "100%", minHeight: 120, resize: "vertical",
          fontFamily: "inherit", fontSize: 15, lineHeight: 1.6,
          background: "var(--paper-2)", border: "1px solid var(--rule)",
          borderRadius: 6, padding: "12px 14px", color: "var(--ink)",
          outline: "none",
        }}
      />

      {error && <div style={{color:"var(--rose)", fontSize:13, marginTop:8}}>{error}</div>}

      <div style={{display:"flex", gap: 10, marginTop: 16, justifyContent: "flex-end"}}>
        {step > 0 && (
          <button className="ghost small" onClick={() => setStep(s => s - 1)}>back</button>
        )}
        <button
          className="primary"
          disabled={!answer.trim() || submitting}
          onClick={next}
        >
          {submitting ? "saving…" : isLast ? "finish" : "next"}
        </button>
      </div>

      <div style={{fontSize: 11, color: "var(--ink-mute)", marginTop: 10, textAlign: "right"}} className="mono">
        ⌘↵ to continue
      </div>
    </div>
  );
}


/* ─────────────────────── RELATIONSHIPS ─────────────────────── */
function RelationshipsTab({ data }) {
  const [selectedId, setSelectedId] = React.useState(null);
  const [showOnboarding, setShowOnboarding] = React.useState(false);
  const [onboardingDone, setOnboardingDone] = React.useState(false);
  const person = data.persons.find(p => p.id === selectedId);

  // Check onboarding status on mount
  React.useEffect(() => {
    const base = window.__CHLOE_API_BASE__ || '';
    fetch(base + '/admin/onboarding/status')
      .then(r => r.json())
      .then(d => { if (d.complete) setOnboardingDone(true); })
      .catch(() => {});
  }, []);

  if (showOnboarding) {
    return (
      <div>
        <button className="back-link" onClick={() => setShowOnboarding(false)}>← back</button>
        <OnboardingFlow
          onDone={() => { setOnboardingDone(true); setShowOnboarding(false); }}
          onCancel={() => setShowOnboarding(false)}
        />
      </div>
    );
  }

  if (person) {
    return <PersonDetail person={person} onBack={() => setSelectedId(null)} />;
  }

  const tone = (i) => ((i % 5) + 1);
  return (
    <div>
      <p className="section-intro">
        People she knows about, weighted by how present they are in her thinking. Attachment style
        emerges from accumulated interactions. Warmth here is her, not them. Open a person to see
        everything she has on them.
      </p>

      {!onboardingDone && (
        <div className="card" style={{marginBottom: 18, borderLeft: "3px solid var(--gold)", padding: "16px 20px"}}>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"center"}}>
            <div>
              <div style={{fontWeight: 600, marginBottom: 4}}>She doesn't know you yet.</div>
              <div style={{fontSize: 14, color:"var(--ink-soft)"}}>
                Six quick questions — who you are, family, friends, job, pets, interests. A starting point so she knows you.
              </div>
            </div>
            <button className="primary" style={{flexShrink: 0, marginLeft: 20}} onClick={() => setShowOnboarding(true)}>
              Start onboarding
            </button>
          </div>
        </div>
      )}

      <div className="card">
        {data.persons.map((p, i) => (
          <button
            className="person clickable"
            key={p.id}
            onClick={() => setSelectedId(p.id)}
            aria-label={"Open " + p.name}
          >
            <PersonAv name={p.name} tone={tone(i)} />
            <div className="info">
              <div className="name">
                {p.name}
                {p.aliases && p.aliases.length > 0 && (
                  <span style={{fontSize:11, color:"var(--ink-mute)", marginLeft:6, fontStyle:"italic"}}>
                    ({p.aliases.join(", ")})
                  </span>
                )}
                <span className="mono" style={{fontSize:11, color:"var(--ink-mute)", marginLeft:6}}>open ↗</span>
              </div>
              <div className="rel">{p.relation}</div>
              <div className="note">{p.one_line || p.notes}</div>
            </div>
            <div className="meta">
              <div className="last">{p.last_contact}</div>
              <div className="att"><span className="pill">{p.attachment}</span></div>
              <div style={{marginTop: 8, fontSize: 11, color:"var(--ink-mute)", fontFamily:"'JetBrains Mono', monospace"}}>
                warmth · {p.warmth.toFixed(2)}
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function PersonDetail({ person, onBack }) {
  const p = person;
  return (
    <div>
      <button className="back-link" onClick={onBack}>← back to everyone</button>

      <header className="person-hd">
        <PersonAv name={p.name} tone={(p.id % 5) + 1} />
        <div className="person-hd-info">
          <h2>{p.name}</h2>
          {p.aliases && p.aliases.length > 0 && (
            <div style={{fontSize:12, color:"var(--ink-mute)", marginBottom:2}}>
              also known as {p.aliases.join(", ")}
            </div>
          )}
          <div className="rel">{p.relation} <span style={{color:"var(--ink-mute)", margin:"0 8px"}}>·</span> known since {p.known_since}</div>
          <div className="one-line">{p.one_line}</div>
          <div className="person-stats">
            <span className="pill">{p.attachment}</span>
            <span className="pill sage">warmth {p.warmth.toFixed(2)}</span>
            <span className="pill">last · {p.last_contact}</span>
          </div>
        </div>
      </header>

      <div className="grid cols-2">
        {/* Trait profile */}
        <div className="card">
          <div className="hd">How she sees them<span className="stretch"></span></div>
          {p.trait_profile.length === 0 ? (
            <div className="empty" style={{padding:"16px 0"}}>not really a person to her, yet.</div>
          ) : p.trait_profile.map(t => (
            <div className="trait-row" key={t.name}>
              <span className="name">{t.name}</span>
              <span className="bar" style={{"--v": (t.weight*100).toFixed(0) + "%"}}></span>
              <span className="w">{t.weight.toFixed(2)}</span>
            </div>
          ))}
        </div>

        {/* Things she knows */}
        <div className="card">
          <div className="hd">What she knows<span className="stretch"></span></div>
          {p.things_she_knows.length === 0 ? (
            <div className="empty" style={{padding:"16px 0"}}>nothing yet.</div>
          ) : (
            <ul className="ink-list">
              {p.things_she_knows.map((t, i) => <li key={i}>{t}</li>)}
            </ul>
          )}
        </div>
      </div>

      <div className="card" style={{marginTop: 18}}>
        <div className="hd">Recent exchanges<span className="stretch"></span></div>
        {p.recent_exchanges.length === 0 ? (
          <div className="empty" style={{padding:"16px 0"}}>nothing recent.</div>
        ) : (
          <div className="exch">
            {p.recent_exchanges.map((e, i) => (
              <div className={"exch-row " + (e.who === "chloe" ? "her" : "them")} key={i}>
                <div className="exch-meta">{e.who} · {e.at}</div>
                <div className="exch-text">{e.text}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="grid cols-2" style={{marginTop: 18}}>
        <div className="card">
          <div className="hd">Moments<span className="stretch"></span></div>
          {p.moments.length === 0 ? (
            <div className="empty" style={{padding:"16px 0"}}>none yet.</div>
          ) : p.moments.map((m, i) => (
            <div className="moment" key={i}>
              <div className="moment-when">{m.at}</div>
              <div className="moment-text">{m.text}</div>
            </div>
          ))}
        </div>
        <div className="card">
          <div className="hd">Things she's done for / with them<span className="stretch"></span></div>
          {p.events_with.length === 0 ? (
            <div className="empty" style={{padding:"16px 0"}}>nothing logged.</div>
          ) : p.events_with.map((m, i) => (
            <div className="event-row" key={i}>
              <span className="event-when mono">{m.at}</span>
              <span className="event-text">{m.text}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="grid cols-2" style={{marginTop: 18}}>
        <div className="card">
          <div className="hd">Things she avoids<span className="stretch"></span></div>
          {p.things_she_avoids.length === 0 ? (
            <div className="empty" style={{padding:"16px 0"}}>nothing to mind.</div>
          ) : (
            <ul className="ink-list">
              {p.things_she_avoids.map((t, i) => <li key={i}>{t}</li>)}
            </ul>
          )}
        </div>
        <div className="card">
          <div className="hd">Who they talk about<span className="stretch"></span></div>
          {p.third_parties.length === 0 ? (
            <div className="empty" style={{padding:"16px 0"}}>nobody, yet.</div>
          ) : p.third_parties.map((tp, i) => (
            <div className="tp-row" key={i}>
              <div className="tp-name">{tp.name}</div>
              <div className="tp-note">{tp.note}</div>
            </div>
          ))}
        </div>
      </div>

      {p.open_threads.length > 0 && (
        <div className="card" style={{marginTop: 18, borderLeft:"2px solid var(--gold)"}}>
          <div className="hd">Open threads<span className="stretch"></span></div>
          <ul className="ink-list">
            {p.open_threads.map((t, i) => <li key={i}>{t}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}


/* ───────────────────────── GOALS ───────────────────────── */
function GoalsTab({ data }) {
  return (
    <div>
      <p className="section-intro">
        Her own persistent agenda — not tasks for you. Progress is measured by what she does in
        the world, not what she reports. A failed goal feeds a setback into traits.
      </p>
      <div className="card">
        {data.goals.map((g, i) => (
          <div className="goal" key={i}>
            <div className="row">
              <div className="name">{g.name}</div>
              <div className="pct">{(g.progress * 100).toFixed(0)}%</div>
            </div>
            <div className="pbar" style={{"--p": (g.progress * 100).toFixed(0) + "%"}}></div>
            <div className="why">{g.why}</div>
          </div>
        ))}
      </div>
      <div className="card" style={{marginTop: 18}}>
        <div className="hd">Next-week intention<span className="stretch"></span></div>
        <div className="lede">"{data.identity.next_week_intention}"</div>
      </div>
    </div>
  );
}


/* ───────────────────────── AUDIT ───────────────────────── */
function AuditTab({ data }) {
  const [filter, setFilter] = React.useState("all");
  const opts = ["all", "executed", "awaiting_confirmation", "self_aborted"];
  const items = data.audit.filter(a => filter === "all" || a.state === filter).slice().reverse();
  const totalCost = data.audit.reduce((s, a) => s + (a.cost || 0), 0);
  const heldBack  = data.audit.filter(a => a.state === "self_aborted").length;

  return (
    <div>
      <p className="section-intro">
        Everything she's done in the world today. Free actions execute immediately; kinetic
        actions go through; kinetic-sensitive ones wait for you in Confirmations. <i>Held back</i>
        is a real state — she chose not to.
      </p>
      <div style={{display:"flex", gap: 14, flexWrap:"wrap", marginBottom: 18}}>
        <div className="pill">{data.audit.length} actions today</div>
        <div className="pill sage">${totalCost.toFixed(4)} spent</div>
        <div className="pill rose">{heldBack} held back</div>
      </div>
      <div className="filter-row">
        {opts.map(o => (
          <button key={o} className={"small" + (filter === o ? " primary" : "")} onClick={() => setFilter(o)}>
            {o.replace(/_/g, " ")}
          </button>
        ))}
      </div>
      <div className="card">
        {items.map((a, i) => (
          <div className="audit-row" key={i}>
            <span className="t">{a.at}</span>
            <span className="v"><b>{a.tool}</b>.{a.verb}</span>
            <span className="intent">{a.intent}</span>
            <span className={"state " + a.state}>{a.state.replace(/_/g, " ")}</span>
            <span className="cost">${a.cost.toFixed(4)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}


/* ─────────────────────── CONFIRMATIONS ─────────────────────── */
function ConfirmationsTab({ data, onResolve }) {
  return (
    <div>
      <p className="section-intro">
        Kinetic-sensitive actions wait here. Irreversible things — sending email, writing to a
        smart home device, anything with external blast radius. Confirming late re-runs her
        deliberation; she may not want to anymore.
      </p>
      {data.confirmations.length === 0 ? (
        <div className="card empty">All clear. Nothing waiting on you.</div>
      ) : (
        data.confirmations.map(t => (
          <div className="tk" key={t.id}>
            <div className="hd">
              <span className="verb"><b>{t.tool}</b>.{t.verb}</span>
              <span className="pill gold">{t.auth}</span>
              <span className="mono" style={{marginLeft:"auto", color:"var(--ink-mute)", fontSize:11}}>
                proposed {t.proposed_at} · expires {t.expires_in}
              </span>
            </div>
            <div className="intent">{t.intent}</div>
            <pre className="preview">{t.preview}</pre>
            <div className="actions">
              <button className="primary" onClick={() => onResolve(t.id, "confirm")}>Yes, do it</button>
              <button onClick={() => onResolve(t.id, "deny")}>Not now</button>
              <button className="ghost">Show more</button>
            </div>
          </div>
        ))
      )}
    </div>
  );
}


/* ───────────────────────── IDENTITY ───────────────────────── */
function IdentityTab({ data }) {
  const I = data.identity;
  return (
    <div>
      <p className="section-intro">
        Traits emerge from accumulated experience — none of them are predefined. They can
        contradict, decay without reinforcement, and promote to "core" with sustained weight.
        Beliefs come from the weekly self-modeling pass; she holds them with confidence, not
        certainty.
      </p>
      <div className="grid cols-2">
        <div className="card">
          <div className="hd">Core traits<span className="stretch"></span></div>
          {I.traits_core.map(t => (
            <div className="trait-row" key={t.name}>
              <span className="name">{t.name}</span>
              <span className="since">since {t.since}</span>
              <span className="bar" style={{"--v": (t.weight*100).toFixed(0) + "%"}}></span>
              <span className="w">{t.weight.toFixed(2)}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <div className="hd">Emerging<span className="stretch"></span></div>
          {I.traits_emerging.map(t => (
            <div className="trait-row emerging" key={t.name}>
              <span className="name">{t.name}</span>
              <span className="since">since {t.since}</span>
              <span className="bar" style={{"--v": (t.weight*100).toFixed(0) + "%"}}></span>
              <span className="w">{t.weight.toFixed(2)}</span>
            </div>
          ))}
          {I.traits_archived.length > 0 && (
            <>
              <hr className="rule" />
              <div style={{fontSize: 12, color:"var(--ink-mute)", marginBottom: 8}} className="mono small-caps">archived</div>
              {I.traits_archived.map(t => (
                <div key={t.name} style={{fontSize: 14, color:"var(--ink-mute)", fontStyle:"italic", padding: "4px 0"}}>
                  {t.name} <span style={{fontSize: 11, color:"var(--ink-mute)"}}>· {t.reason}</span>
                </div>
              ))}
            </>
          )}
        </div>
      </div>

      <div className="card" style={{marginTop: 18}}>
        <div className="hd">Beliefs<span className="stretch"></span></div>
        {I.beliefs.map((b, i) => (
          <div className="belief" key={i}>
            <div className="q">"{b.text}"</div>
            <div className="meta">confidence {b.confidence.toFixed(2)} · {b.source}</div>
          </div>
        ))}
      </div>

      <div className="card" style={{marginTop: 18}}>
        <div className="hd">Contradictions<span className="stretch"></span></div>
        {I.contradictions.map((c, i) => (
          <div key={i} style={{fontSize: 16, lineHeight: 1.6, padding: "6px 0"}}>
            <i>{c.a}</i> ↔ <i>{c.b}</i>
            <div style={{fontSize: 13, color:"var(--ink-mute)", fontStyle:"italic", marginTop: 4}}>{c.note}</div>
          </div>
        ))}
      </div>
    </div>
  );
}


/* ───────────────────────── SETTINGS ───────────────────────── */
function SettingsTab({ data, onTogglePref }) {
  const S = data.settings || {};
  const spending = S.spending || { spent_usd_today: 0, cap_usd_day: 1.5 };
  const quietHours = S.quiet_hours || { start: "23:00", end: "08:00" };
  const dontTouch = S.dont_touch || {};
  const gmailLabels = Array.isArray(dontTouch.gmail_labels) ? dontTouch.gmail_labels : [];
  const notesFolders = Array.isArray(dontTouch.notes_folders) ? dontTouch.notes_folders : [];
  const spotifyPlaylists = Array.isArray(dontTouch.spotify_playlists) ? dontTouch.spotify_playlists : [];
  const cap = spending.cap_usd_day || 1.5;
  const spent = spending.spent_usd_today || 0;
  const pct = Math.min(100, (spent / cap) * 100);

  return (
    <div>
      <p className="section-intro">
        The leash. The gate honours these absolutely — the model has no path to bypass. A blocked
        action becomes a memory tagged "held back" and she perceives it the next time she reads
        the feed.
      </p>

      <div className="card">
        <div className="hd">Leash<span className="stretch"></span></div>
        <div className="kv-row">
          <span className="k">Away mode</span>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", gap: 12}}>
            <span style={{fontSize: 14, color:"var(--ink-soft)", fontStyle:"italic"}}>Blocks kinetic & sensitive actions while you're away.</span>
            <Toggle checked={!!S.away_mode} onChange={(v) => onTogglePref("away_mode", v)} />
          </div>
        </div>
        <div className="kv-row">
          <span className="k">Focus mode</span>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", gap: 12}}>
            <span style={{fontSize: 14, color:"var(--ink-soft)", fontStyle:"italic"}}>Suppresses outbound messages entirely.</span>
            <Toggle checked={!!S.focus_mode} onChange={(v) => onTogglePref("focus_mode", v)} />
          </div>
        </div>
        <div className="kv-row">
          <span className="k">Auth ceiling</span>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", gap: 12}}>
            <span className="mono" style={{fontSize: 13}}>{S.auth_ceiling || "kinetic"}</span>
            <span style={{fontSize: 11, color:"var(--ink-mute)"}}>highest class she may execute</span>
          </div>
        </div>
      </div>

      <div className="card" style={{marginTop: 18}}>
        <div className="hd">Quiet hours<span className="stretch"></span></div>
        <div className="kv-row">
          <span className="k">From / to</span>
          <div className="mono" style={{fontSize: 14}}>
            {quietHours.start} → {quietHours.end}
            {quietHours.timezone && <span style={{color:"var(--ink-mute)", marginLeft: 10}}>· {quietHours.timezone}</span>}
          </div>
        </div>
      </div>

      <div className="card" style={{marginTop: 18}}>
        <div className="hd">Daily spend<span className="stretch"></span></div>
        <div className="kv-row">
          <span className="k">Today</span>
          <div>
            <div style={{display:"flex", justifyContent:"space-between", fontSize: 13}}>
              <span className="mono">${spent.toFixed(4)}</span>
              <span className="mono" style={{color:"var(--ink-mute)"}}>cap ${cap.toFixed(2)}</span>
            </div>
            <div className="pbar" style={{"--p": pct.toFixed(0) + "%", height: 1, background:"var(--rule)", position:"relative", marginTop: 6}}>
              <span style={{position:"absolute", left: 0, top: -2, height: 5, width: pct + "%", background:"var(--sage)"}}></span>
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{marginTop: 18}}>
        <div className="hd">Don't touch<span className="stretch"></span></div>
        <div className="kv-row">
          <span className="k">Gmail labels</span>
          <div className="mono" style={{fontSize: 13, color:"var(--ink-soft)"}}>
            {gmailLabels.length ? gmailLabels.join(", ") : <i style={{color:"var(--ink-mute)"}}>none</i>}
          </div>
        </div>
        <div className="kv-row">
          <span className="k">Notes folders</span>
          <div className="mono" style={{fontSize: 13, color:"var(--ink-soft)"}}>
            {notesFolders.length ? notesFolders.join(", ") : <i style={{color:"var(--ink-mute)"}}>none</i>}
          </div>
        </div>
        <div className="kv-row">
          <span className="k">Spotify playlists</span>
          <div className="mono" style={{fontSize: 13, color:"var(--ink-soft)"}}>
            {spotifyPlaylists.length ? spotifyPlaylists.join(", ") : <i style={{color:"var(--ink-mute)"}}>none</i>}
          </div>
        </div>
      </div>
    </div>
  );
}

function Toggle({ checked, onChange }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} />
      <span></span>
    </label>
  );
}


/* ───────────────────────── INNER STATE ───────────────────────── */
function InnerStateTab({ data }) {
  const [liveIS, setLiveIS] = React.useState(null);
  React.useEffect(() => {
    const base = window.__CHLOE_API_BASE__ || '';
    fetch(base + '/v1/dashboard/state')
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(d => { if (d.inner_state) setLiveIS(d.inner_state); })
      .catch(() => {});
  }, []);
  const IS = liveIS || (data.inner_state) || {};
  const reflect = IS.reflect || {};
  const kv = IS.kv || {};

  function PressureBar({ value }) {
    return (
      <span className="bar" style={{"--v": ((value || 0) * 100).toFixed(0) + "%", display:"inline-block", width:60, height:6, background:"var(--rule)", position:"relative", borderRadius:3, verticalAlign:"middle", marginLeft:8}}>
        <span style={{position:"absolute", left:0, top:0, height:"100%", width:((value||0)*100).toFixed(0)+"%", background:"var(--gold)", borderRadius:3}}></span>
      </span>
    );
  }

  function InnerList({ title, items, emptyMsg, renderItem }) {
    const active = items.filter(x => !x.resolved);
    const resolved = items.filter(x => x.resolved);
    return (
      <div className="card" style={{marginTop:18}}>
        <div className="hd">{title}<span className="stretch"></span><span className="mono" style={{fontSize:11, color:"var(--ink-mute)"}}>{active.length} active</span></div>
        {active.length === 0 ? (
          <div className="empty" style={{padding:"12px 0"}}>{emptyMsg}</div>
        ) : active.map((item, i) => (
          <div key={i} style={{padding:"8px 0", borderBottom:"1px solid var(--rule)"}}>
            {renderItem(item)}
          </div>
        ))}
        {resolved.length > 0 && (
          <details style={{marginTop:8}}>
            <summary style={{fontSize:12, color:"var(--ink-mute)", cursor:"pointer"}}>+ {resolved.length} resolved</summary>
            {resolved.map((item, i) => (
              <div key={i} style={{padding:"6px 0", opacity:0.5, fontSize:13}}>
                {renderItem(item)}
              </div>
            ))}
          </details>
        )}
      </div>
    );
  }

  return (
    <div>
      <p className="section-intro">
        Her inner life — what she's holding, carrying, wondering about, dreading. This is the
        substrate that feeds into every reflect pass and every chat turn. All of it is generated
        by the system, not written by hand.
      </p>

      {/* Reflect summary */}
      <div className="card">
        <div className="hd">Last reflect<span className="stretch"></span>
          <span className="mono" style={{fontSize:11, color:"var(--ink-mute)"}}>{reflect.last_run_at || "never"}</span>
        </div>
        {reflect.emotions && reflect.emotions.length > 0 && (
          <div style={{marginBottom:10}}>
            {reflect.emotions.map((e, i) => <span key={i} className="pill" style={{marginRight:6}}>{e}</span>)}
          </div>
        )}
        {reflect.biased_summary && (
          <div style={{fontSize:15, fontStyle:"italic", color:"var(--ink-soft)", lineHeight:1.6, marginBottom:10}}>
            "{reflect.biased_summary}"
          </div>
        )}
        {reflect.recurring_loops && reflect.recurring_loops.length > 0 && (
          <>
            <div style={{fontSize:12, color:"var(--ink-mute)", marginBottom:6}} className="mono small-caps">recurring loops</div>
            <ul className="ink-list">
              {reflect.recurring_loops.map((l, i) => <li key={i}>{l}</li>)}
            </ul>
          </>
        )}
        {!reflect.biased_summary && (!reflect.emotions || !reflect.emotions.length) && (
          <div className="empty" style={{padding:"8px 0"}}>no reflect has run yet.</div>
        )}
      </div>

      {/* KV state */}
      {(kv.teo_read || kv.aesthetic_orientation || kv.novelty_deficit > 0) && (
        <div className="card" style={{marginTop:18}}>
          <div className="hd">Synthesized state<span className="stretch"></span></div>
          {kv.novelty_deficit > 0 && (
            <div className="kv-row">
              <span className="k">Novelty deficit</span>
              <div><PressureBar value={kv.novelty_deficit} /> <span className="mono" style={{fontSize:12}}>{kv.novelty_deficit.toFixed(2)}</span></div>
            </div>
          )}
          {kv.teo_read && (
            <div style={{marginTop:10}}>
              <div style={{fontSize:12, color:"var(--ink-mute)", marginBottom:4}} className="mono small-caps">standing read on Teo</div>
              <div style={{fontSize:14, fontStyle:"italic", lineHeight:1.6}}>{kv.teo_read}</div>
            </div>
          )}
          {kv.aesthetic_orientation && (
            <div style={{marginTop:10}}>
              <div style={{fontSize:12, color:"var(--ink-mute)", marginBottom:4}} className="mono small-caps">aesthetic orientation</div>
              <div style={{fontSize:14, fontStyle:"italic", lineHeight:1.6}}>{kv.aesthetic_orientation}</div>
            </div>
          )}
        </div>
      )}

      {/* World beliefs */}
      <div className="card" style={{marginTop:18}}>
        <div className="hd">World beliefs<span className="stretch"></span></div>
        {(!IS.world_beliefs || IS.world_beliefs.length === 0) ? (
          <div className="empty" style={{padding:"12px 0"}}>no beliefs formed yet.</div>
        ) : IS.world_beliefs.map((b, i) => (
          <div key={i} style={{padding:"8px 0", borderBottom:"1px solid var(--rule)"}}>
            <div style={{fontSize:14, lineHeight:1.5}}>
              <b style={{color:"var(--ink-mute)", fontSize:12}}>{b.topic}</b> — {b.belief}
            </div>
            <div style={{fontSize:11, color:"var(--ink-mute)", marginTop:3}} className="mono">
              {b.noticing ? "noticing" : (b.confidence > 0.65 ? "believes" : b.confidence > 0.4 ? "starting to think" : "might be true")}
              {" · "}{(b.confidence * 100).toFixed(0)}% confidence
              {b.ambivalent && " · ambivalent"}
              {b.updated_at && " · " + b.updated_at}
            </div>
          </div>
        ))}
      </div>

      {/* Open questions */}
      <InnerList
        title="Open questions"
        items={IS.questions || []}
        emptyMsg="nothing unresolved yet."
        renderItem={item => (
          <>
            <div style={{fontSize:14, lineHeight:1.5}}>{item.text}</div>
            <div style={{fontSize:11, color:"var(--ink-mute)", marginTop:2}} className="mono">
              {item.domain}{" · "}intensity {(item.intensity * 100).toFixed(0)}%
              {item.created_at && " · " + item.created_at}
            </div>
          </>
        )}
      />

      {/* Wants */}
      <InnerList
        title="Wants"
        items={IS.wants || []}
        emptyMsg="nothing she wants yet."
        renderItem={item => (
          <>
            <div style={{fontSize:14, lineHeight:1.5}}>{item.text}</div>
            <div style={{fontSize:11, color:"var(--ink-mute)", marginTop:2}} className="mono">
              {item.subtype && item.subtype + " · "}pressure {(item.pressure * 100).toFixed(0)}%
              <PressureBar value={item.pressure} />
            </div>
          </>
        )}
      />

      {/* Tensions */}
      <InnerList
        title="Tensions"
        items={IS.tensions || []}
        emptyMsg="no unresolved tensions."
        renderItem={item => (
          <>
            <div style={{fontSize:14, lineHeight:1.5}}>{item.text}</div>
            <div style={{fontSize:11, color:"var(--ink-mute)", marginTop:2}} className="mono">
              pressure {(item.pressure * 100).toFixed(0)}% <PressureBar value={item.pressure} />
            </div>
          </>
        )}
      />

      {/* Fears */}
      <InnerList
        title="Fears"
        items={IS.fears || []}
        emptyMsg="nothing she fears yet."
        renderItem={item => (
          <>
            <div style={{fontSize:14, lineHeight:1.5}}>{item.text}</div>
            <div style={{fontSize:11, color:"var(--ink-mute)", marginTop:2}} className="mono">
              pressure {(item.pressure * 100).toFixed(0)}% <PressureBar value={item.pressure} />
            </div>
          </>
        )}
      />

      {/* Anticipations */}
      <InnerList
        title="Anticipations"
        items={IS.anticipations || []}
        emptyMsg="nothing anticipated."
        renderItem={item => {
          const qualifier = item.valence < -0.3 ? "dreading" : item.valence > 0.3 ? "looking forward to" : "holding";
          return (
            <>
              <div style={{fontSize:14, lineHeight:1.5}}><i>{qualifier}:</i> {item.text}</div>
              <div style={{fontSize:11, color:"var(--ink-mute)", marginTop:2}} className="mono">
                intensity {(item.intensity * 100).toFixed(0)}%
                {item.target_date && " · " + item.target_date}
              </div>
            </>
          );
        }}
      />

      {/* Aversions */}
      <InnerList
        title="Aversions"
        items={IS.aversions || []}
        emptyMsg="nothing she avoids."
        renderItem={item => (
          <div style={{fontSize:14, lineHeight:1.5}}>{item.text}</div>
        )}
      />

      {/* Ideas */}
      {IS.ideas && IS.ideas.length > 0 && (
        <div className="card" style={{marginTop:18}}>
          <div className="hd">Ideas<span className="stretch"></span></div>
          {IS.ideas.map((idea, i) => (
            <div key={i} style={{padding:"8px 0", borderBottom:"1px solid var(--rule)", opacity: idea.complete ? 0.5 : 1}}>
              <div style={{fontSize:14, lineHeight:1.5}}>{idea.text}</div>
              <div style={{fontSize:11, color:"var(--ink-mute)", marginTop:2}} className="mono">
                {idea.tags && idea.tags.join(", ")}
                {idea.complete && " · done"}
                {idea.created_at && " · " + idea.created_at}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Narrative timeline */}
      {IS.narrative_timeline && IS.narrative_timeline.length > 0 && (
        <div className="card" style={{marginTop:18}}>
          <div className="hd">Narrative timeline<span className="stretch"></span></div>
          {IS.narrative_timeline.map((entry, i) => (
            <div key={i} style={{padding:"12px 0", borderBottom:"1px solid var(--rule)"}}>
              <div style={{fontSize:13, fontWeight:600, marginBottom:4}}>
                {entry.period_label || entry.week_start}
                {entry.chapter_transition && <span className="pill gold" style={{marginLeft:8, fontSize:10}}>chapter</span>}
              </div>
              {entry.what_happened && <div style={{fontSize:14, lineHeight:1.6, marginBottom:4}}>{entry.what_happened}</div>}
              {entry.what_shifted && <div style={{fontSize:13, color:"var(--ink-soft)", fontStyle:"italic", marginBottom:4}}>Shift: {entry.what_shifted}</div>}
              {entry.still_sitting_with && <div style={{fontSize:13, color:"var(--ink-mute)"}}>Still sitting with: {entry.still_sitting_with}</div>}
              {entry.felt_texture && <div style={{fontSize:12, color:"var(--ink-mute)", marginTop:4}} className="mono">{entry.felt_texture}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Character addenda */}
      {IS.character_addenda && IS.character_addenda.length > 0 && (
        <div className="card" style={{marginTop:18}}>
          <div className="hd">Character addenda<span className="stretch"></span></div>
          {IS.character_addenda.map((a, i) => (
            <div key={i} style={{padding:"10px 0", borderBottom:"1px solid var(--rule)"}}>
              <div style={{fontSize:12, color:"var(--ink-mute)", marginBottom:4}} className="mono">
                person {a.person_id} · v{a.version} · {a.created_at}
              </div>
              <div style={{fontSize:13, whiteSpace:"pre-wrap", lineHeight:1.6}}>{a.body}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


/* ───────────────────────── DEBUG ───────────────────────── */
function DebugTab() {
  const [debugData, setDebugData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [activePrompt, setActivePrompt] = React.useState(null);
  const [section, setSection] = React.useState("last_turn");

  React.useEffect(() => {
    const base = window.__CHLOE_API_BASE__ || '';
    fetch(base + '/v1/debug/prompts')
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(d => { setDebugData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, []);

  if (loading) return <div className="card" style={{marginTop:18}}><div className="empty">loading debug data…</div></div>;
  if (error)   return <div className="card" style={{marginTop:18, color:"var(--rose)"}}>{error}</div>;
  if (!debugData) return null;

  const sections = [
    { id:"last_turn",    label:"Last chat turn" },
    { id:"last_reflect", label:"Last reflect" },
    { id:"prompts",      label:"Prompt templates" },
  ];

  function Block({ title, content, maxHeight }) {
    const [expanded, setExpanded] = React.useState(false);
    const isEmpty = !content || (typeof content === "string" && !content.trim()) ||
                    (typeof content === "object" && Object.keys(content).length === 0);
    return (
      <div style={{marginBottom:18}}>
        <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:6}}>
          <span style={{fontSize:13, fontWeight:600, color:"var(--ink)"}}>{title}</span>
          {!isEmpty && <button className="ghost small" onClick={() => setExpanded(e => !e)}>{expanded ? "collapse" : "expand"}</button>}
        </div>
        {isEmpty ? (
          <div style={{fontSize:12, color:"var(--ink-mute)", fontStyle:"italic"}}>empty — hasn't run yet.</div>
        ) : (
          <pre style={{
            fontSize:11, lineHeight:1.6, whiteSpace:"pre-wrap", wordBreak:"break-word",
            background:"var(--paper)", border:"1px solid var(--rule)", borderRadius:4,
            padding:"10px 12px", overflow:"auto",
            maxHeight: expanded ? "none" : (maxHeight || 240),
            color:"var(--ink)",
          }}>
            {typeof content === "string" ? content : JSON.stringify(content, null, 2)}
          </pre>
        )}
      </div>
    );
  }

  const lt = debugData.last_turn || {};
  const lr = debugData.last_reflect || {};
  const pf = debugData.prompt_files || {};

  return (
    <div>
      <p className="section-intro">
        Every stage of the pipeline, exposed. "Last chat turn" shows the full system prompt built
        for the most recent exchange. "Last reflect" shows the exact payloads sent to Flash and
        what came back. "Prompt templates" shows every prompt file verbatim.
      </p>

      <div className="filter-row">
        {sections.map(s => (
          <button key={s.id} className={"small" + (section === s.id ? " primary" : "")}
                  onClick={() => setSection(s.id)}>{s.label}</button>
        ))}
      </div>

      {section === "last_turn" && (
        <div className="card" style={{marginTop:18}}>
          <div className="hd">Last chat turn
            <span className="stretch"></span>
            <span className="mono" style={{fontSize:11, color:"var(--ink-mute)"}}>{lt.built_at || "—"}</span>
          </div>
          <Block title="Full system prompt (char prefix + preflight + dynamic suffix)" content={lt.system_prompt} maxHeight={360} />
          <Block title="Preflight context (targeted data resolved before reply)" content={lt.preflight_context} />
          <Block title="Dynamic suffix (ambient context injected every turn)" content={lt.dynamic_suffix} />
        </div>
      )}

      {section === "last_reflect" && (
        <div className="card" style={{marginTop:18}}>
          <div className="hd">Last reflect
            <span className="stretch"></span>
            <span className="mono" style={{fontSize:11, color:"var(--ink-mute)"}}>{lr.ran_at || "—"}</span>
          </div>
          <Block title="Inner state payload (sent to reflect_inner_state.md)" content={lr.inner_payload} />
          <Block title="Inner state result (wants, tensions, emotions, loops…)" content={lr.inner_result} />
          <Block title="Signal payload (sent to reflect_signals.md)" content={lr.signal_payload} />
          <Block title="Signal result (interests, goals, beliefs, traits…)" content={lr.signal_result} />
        </div>
      )}

      {section === "prompts" && (
        <div style={{marginTop:18}}>
          <div style={{display:"flex", flexWrap:"wrap", gap:8, marginBottom:16}}>
            {Object.keys(pf).map(name => (
              <button key={name} className={"small" + (activePrompt === name ? " primary" : "")}
                      onClick={() => setActivePrompt(name === activePrompt ? null : name)}>
                {name}
              </button>
            ))}
          </div>
          {activePrompt && pf[activePrompt] && (
            <div className="card">
              <div className="hd">{activePrompt}.md<span className="stretch"></span></div>
              <pre style={{
                fontSize:11, lineHeight:1.7, whiteSpace:"pre-wrap", wordBreak:"break-word",
                color:"var(--ink)", maxHeight:600, overflow:"auto",
              }}>
                {pf[activePrompt]}
              </pre>
            </div>
          )}
          {!activePrompt && (
            <div className="card">
              <div className="empty">{Object.keys(pf).length} prompt files — click one above to read it.</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


/* ─────────────────────── ADMIN ─────────────────────── */
function AdminTab() {
  const [section, setSection] = React.useState("memories");
  const sections = [
    { id: "memories",    label: "Memories" },
    { id: "prompts",     label: "Prompts" },
    { id: "kv",          label: "KV State" },
    { id: "persons",     label: "People" },
    { id: "inner",       label: "Inner State" },
    { id: "controls",    label: "Controls" },
  ];

  return (
    <div>
      <p className="section-intro">
        Full control over Chloe's state — memories, prompts, KV, people, inner state, and manual triggers.
        Changes take effect immediately.
      </p>
      <div className="filter-row">
        {sections.map(s => (
          <button key={s.id} className={"small" + (section === s.id ? " primary" : "")}
                  onClick={() => setSection(s.id)}>{s.label}</button>
        ))}
      </div>
      {section === "memories" && <AdminMemories />}
      {section === "prompts"  && <AdminPrompts />}
      {section === "kv"       && <AdminKV />}
      {section === "persons"  && <AdminPersons />}
      {section === "inner"    && <AdminInnerState />}
      {section === "controls" && <AdminControls />}
    </div>
  );
}

function useAdminFetch(url) {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const reload = React.useCallback(() => {
    setLoading(true);
    const base = window.__CHLOE_API_BASE__ || '';
    fetch(base + url)
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [url]);
  React.useEffect(() => { reload(); }, [reload]);
  return { data, loading, error, reload };
}

function AdminApi(method, path, body) {
  const base = window.__CHLOE_API_BASE__ || '';
  return fetch(base + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }).then(r => {
    if (!r.ok) return r.text().then(t => Promise.reject(t || 'HTTP ' + r.status));
    return r.json();
  });
}


/* ── Admin: Memories ── */
function AdminMemories() {
  const { data, loading, error, reload } = useAdminFetch('/admin/memories?limit=50');
  const [addText, setAddText] = React.useState('');
  const [addKind, setAddKind] = React.useState('semantic');
  const [addSalience, setAddSalience] = React.useState('0.8');
  const [adding, setAdding] = React.useState(false);
  const [editId, setEditId] = React.useState(null);
  const [editText, setEditText] = React.useState('');
  const [editSalience, setEditSalience] = React.useState('');
  const [msg, setMsg] = React.useState(null);

  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(null), 2500); };

  const add = async () => {
    if (!addText.trim()) return;
    setAdding(true);
    try {
      await AdminApi('POST', '/admin/memories/inject', {
        text: addText.trim(), kind: addKind, salience: parseFloat(addSalience) || 0.8,
      });
      setAddText(''); flash('memory added'); reload();
    } catch (e) { flash('error: ' + e); }
    setAdding(false);
  };

  const del = async (id) => {
    if (!confirm('Delete memory ' + id + '?')) return;
    try {
      await AdminApi('DELETE', '/admin/memories/' + id);
      flash('deleted'); reload();
    } catch (e) { flash('error: ' + e); }
  };

  const startEdit = (m) => {
    setEditId(m.id); setEditText(m.text); setEditSalience(String(m.salience));
  };

  const saveEdit = async () => {
    try {
      await AdminApi('PATCH', '/admin/memories/' + editId, {
        text: editText, salience: parseFloat(editSalience),
      });
      setEditId(null); flash('saved'); reload();
    } catch (e) { flash('error: ' + e); }
  };

  if (loading) return <div className="card" style={{marginTop:18}}><div className="empty">loading…</div></div>;
  if (error) return <div className="card" style={{marginTop:18, color:"var(--rose)"}}>{error}</div>;

  const mems = data.memories || [];

  return (
    <div style={{marginTop:18}}>
      {msg && <div style={{marginBottom:12, fontSize:13, color:"var(--sage)"}} className="mono">{msg}</div>}

      <div className="card" style={{marginBottom:18}}>
        <div className="hd">Add memory<span className="stretch"></span></div>
        <textarea
          value={addText} onChange={e => setAddText(e.target.value)}
          placeholder="memory text…"
          style={{width:"100%", minHeight:80, fontFamily:"inherit", fontSize:14,
            background:"var(--paper-2)", border:"1px solid var(--rule)", borderRadius:6,
            padding:"10px 12px", color:"var(--ink)", outline:"none", resize:"vertical"}}
        />
        <div style={{display:"flex", gap:10, marginTop:10, alignItems:"center", flexWrap:"wrap"}}>
          <select value={addKind} onChange={e => setAddKind(e.target.value)}
            style={{fontFamily:"inherit", fontSize:13, background:"var(--paper-2)",
              border:"1px solid var(--rule)", borderRadius:4, padding:"4px 8px", color:"var(--ink)"}}>
            {["episodic","semantic","autobiographical","procedural"].map(k => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
          <label style={{fontSize:13, display:"flex", alignItems:"center", gap:6}}>
            salience
            <input type="number" min="0" max="1" step="0.05" value={addSalience}
              onChange={e => setAddSalience(e.target.value)}
              style={{width:60, fontFamily:"inherit", fontSize:13, background:"var(--paper-2)",
                border:"1px solid var(--rule)", borderRadius:4, padding:"3px 7px", color:"var(--ink)"}} />
          </label>
          <button className="primary" disabled={!addText.trim() || adding} onClick={add}>
            {adding ? "adding…" : "add"}
          </button>
        </div>
      </div>

      <div className="card">
        <div className="hd">All memories ({mems.length})<span className="stretch"></span></div>
        {mems.map(m => (
          <div key={m.id} style={{padding:"10px 0", borderBottom:"1px solid var(--rule)"}}>
            {editId === m.id ? (
              <div>
                <textarea value={editText} onChange={e => setEditText(e.target.value)}
                  style={{width:"100%", minHeight:70, fontFamily:"inherit", fontSize:13,
                    background:"var(--paper-2)", border:"1px solid var(--rule)", borderRadius:6,
                    padding:"8px 10px", color:"var(--ink)", outline:"none", resize:"vertical"}} />
                <div style={{display:"flex", gap:8, marginTop:8, alignItems:"center"}}>
                  <label style={{fontSize:13, display:"flex", alignItems:"center", gap:5}}>
                    salience
                    <input type="number" min="0" max="1" step="0.05" value={editSalience}
                      onChange={e => setEditSalience(e.target.value)}
                      style={{width:60, fontFamily:"inherit", fontSize:13, background:"var(--paper-2)",
                        border:"1px solid var(--rule)", borderRadius:4, padding:"3px 7px", color:"var(--ink)"}} />
                  </label>
                  <button className="primary" onClick={saveEdit}>save</button>
                  <button className="ghost small" onClick={() => setEditId(null)}>cancel</button>
                </div>
              </div>
            ) : (
              <div>
                <div style={{fontSize:13, lineHeight:1.5}}>{m.text}</div>
                <div style={{display:"flex", gap:12, marginTop:4, alignItems:"center"}}>
                  <span className="mono" style={{fontSize:11, color:"var(--ink-mute)"}}>
                    #{m.id} · {m.kind} · salience {m.salience} · {(m.created_at||'').slice(0,16)}
                  </span>
                  <button className="ghost small" onClick={() => startEdit(m)}>edit</button>
                  <button className="ghost small" style={{color:"var(--rose)"}} onClick={() => del(m.id)}>delete</button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}


/* ── Admin: Prompts ── */
function AdminPrompts() {
  const { data, loading, error, reload } = useAdminFetch('/admin/prompts');
  const [active, setActive] = React.useState(null);
  const [draft, setDraft] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const [msg, setMsg] = React.useState(null);

  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(null), 2500); };

  const openPrompt = (name) => {
    setActive(name);
    setDraft((data.prompts || {})[name] || '');
  };

  const save = async () => {
    setSaving(true);
    try {
      await AdminApi('PUT', '/admin/prompts/' + active, { content: draft });
      flash('saved'); reload();
    } catch (e) { flash('error: ' + e); }
    setSaving(false);
  };

  if (loading) return <div className="card" style={{marginTop:18}}><div className="empty">loading…</div></div>;
  if (error) return <div className="card" style={{marginTop:18, color:"var(--rose)"}}>{error}</div>;

  const names = Object.keys(data.prompts || {});

  return (
    <div style={{marginTop:18}}>
      <div style={{display:"flex", flexWrap:"wrap", gap:8, marginBottom:16}}>
        {names.map(name => (
          <button key={name} className={"small" + (active === name ? " primary" : "")}
                  onClick={() => openPrompt(name)}>{name}</button>
        ))}
      </div>

      {msg && <div style={{marginBottom:10, fontSize:13, color:"var(--sage)"}} className="mono">{msg}</div>}

      {active ? (
        <div className="card">
          <div className="hd">
            {active}.md
            <span className="stretch"></span>
            <button className="ghost small" onClick={() => setActive(null)}>close</button>
          </div>
          <textarea
            value={draft} onChange={e => setDraft(e.target.value)}
            style={{width:"100%", minHeight:400, fontFamily:"'JetBrains Mono', monospace",
              fontSize:12, lineHeight:1.7, background:"var(--paper-2)",
              border:"1px solid var(--rule)", borderRadius:6,
              padding:"12px 14px", color:"var(--ink)", outline:"none", resize:"vertical"}} />
          <div style={{display:"flex", justifyContent:"flex-end", marginTop:10}}>
            <button className="primary" disabled={saving} onClick={save}>
              {saving ? "saving…" : "save changes"}
            </button>
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="empty">{names.length} prompts — click one above to edit.</div>
        </div>
      )}
    </div>
  );
}


/* ── Admin: KV State ── */
function AdminKV() {
  const { data, loading, error, reload } = useAdminFetch('/admin/kv');
  const [editKey, setEditKey] = React.useState(null);
  const [editVal, setEditVal] = React.useState('');
  const [newKey, setNewKey] = React.useState('');
  const [newVal, setNewVal] = React.useState('');
  const [filter, setFilter] = React.useState('');
  const [msg, setMsg] = React.useState(null);

  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(null), 2500); };

  const save = async (k, v) => {
    try {
      await AdminApi('PUT', '/admin/kv/' + encodeURIComponent(k), { value: v });
      flash('saved'); setEditKey(null); reload();
    } catch (e) { flash('error: ' + e); }
  };

  const del = async (k) => {
    if (!confirm('Delete kv key: ' + k + '?')) return;
    try {
      await AdminApi('DELETE', '/admin/kv/' + encodeURIComponent(k));
      flash('deleted'); reload();
    } catch (e) { flash('error: ' + e); }
  };

  const add = async () => {
    if (!newKey.trim()) return;
    await save(newKey.trim(), newVal);
    setNewKey(''); setNewVal('');
  };

  if (loading) return <div className="card" style={{marginTop:18}}><div className="empty">loading…</div></div>;
  if (error) return <div className="card" style={{marginTop:18, color:"var(--rose)"}}>{error}</div>;

  const kv = data.kv || {};
  const keys = Object.keys(kv).filter(k => !filter || k.includes(filter));

  return (
    <div style={{marginTop:18}}>
      {msg && <div style={{marginBottom:10, fontSize:13, color:"var(--sage)"}} className="mono">{msg}</div>}

      <div className="card" style={{marginBottom:18}}>
        <div className="hd">Add / set key<span className="stretch"></span></div>
        <div style={{display:"flex", gap:8, flexWrap:"wrap"}}>
          <input value={newKey} onChange={e => setNewKey(e.target.value)}
            placeholder="key"
            style={{flex:"1 1 180px", fontFamily:"'JetBrains Mono', monospace", fontSize:13,
              background:"var(--paper-2)", border:"1px solid var(--rule)", borderRadius:4,
              padding:"5px 9px", color:"var(--ink)"}} />
          <input value={newVal} onChange={e => setNewVal(e.target.value)}
            placeholder="value"
            style={{flex:"2 1 240px", fontFamily:"'JetBrains Mono', monospace", fontSize:13,
              background:"var(--paper-2)", border:"1px solid var(--rule)", borderRadius:4,
              padding:"5px 9px", color:"var(--ink)"}} />
          <button className="primary" disabled={!newKey.trim()} onClick={add}>set</button>
        </div>
      </div>

      <div className="card">
        <div className="hd">
          KV store ({keys.length} of {Object.keys(kv).length})
          <span className="stretch"></span>
          <input value={filter} onChange={e => setFilter(e.target.value)}
            placeholder="filter…"
            style={{width:140, fontFamily:"'JetBrains Mono', monospace", fontSize:12,
              background:"var(--paper-3)", border:"1px solid var(--rule)", borderRadius:4,
              padding:"3px 8px", color:"var(--ink)"}} />
        </div>
        {keys.map(k => (
          <div key={k} style={{padding:"8px 0", borderBottom:"1px solid var(--rule)"}}>
            {editKey === k ? (
              <div>
                <div className="mono" style={{fontSize:12, color:"var(--ink-mute)", marginBottom:4}}>{k}</div>
                <textarea value={editVal} onChange={e => setEditVal(e.target.value)}
                  style={{width:"100%", minHeight:60, fontFamily:"'JetBrains Mono', monospace",
                    fontSize:12, background:"var(--paper-2)", border:"1px solid var(--rule)",
                    borderRadius:4, padding:"6px 8px", color:"var(--ink)", resize:"vertical"}} />
                <div style={{display:"flex", gap:8, marginTop:6}}>
                  <button className="primary" onClick={() => save(k, editVal)}>save</button>
                  <button className="ghost small" onClick={() => setEditKey(null)}>cancel</button>
                </div>
              </div>
            ) : (
              <div style={{display:"flex", gap:10, alignItems:"flex-start"}}>
                <div style={{flex:1, minWidth:0}}>
                  <div className="mono" style={{fontSize:12, color:"var(--ink-mute)"}}>{k}</div>
                  <div style={{fontSize:13, lineHeight:1.4, marginTop:2, wordBreak:"break-all",
                    maxHeight:60, overflow:"hidden", opacity:0.85}}>
                    {typeof kv[k] === 'string' ? kv[k].slice(0, 200) : JSON.stringify(kv[k]).slice(0,200)}
                  </div>
                </div>
                <div style={{display:"flex", gap:6, flexShrink:0}}>
                  <button className="ghost small" onClick={() => { setEditKey(k); setEditVal(typeof kv[k] === 'string' ? kv[k] : JSON.stringify(kv[k])); }}>edit</button>
                  <button className="ghost small" style={{color:"var(--rose)"}} onClick={() => del(k)}>del</button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}


/* ── Admin: Persons ── */
function AdminPersons() {
  const { data, loading, error, reload } = useAdminFetch('/v1/dashboard/state');
  const [editId, setEditId] = React.useState(null);
  const [editName, setEditName] = React.useState('');
  const [editAliases, setEditAliases] = React.useState('');
  const [editImpression, setEditImpression] = React.useState('');
  const [editAttachment, setEditAttachment] = React.useState('');
  const [msg, setMsg] = React.useState(null);

  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(null), 2500); };

  const startEdit = (p) => {
    setEditId(p.id);
    setEditName(p.name || '');
    setEditAliases((p.aliases || []).join(', '));
    setEditImpression(p.one_line || '');
    setEditAttachment(p.attachment || '');
  };

  const save = async () => {
    try {
      const aliases = editAliases.split(',').map(s => s.trim()).filter(Boolean);
      await AdminApi('PUT', '/admin/persons/' + editId, {
        name: editName.trim() || null,
        aliases,
        impression: editImpression,
        attachment_pattern: editAttachment || null,
      });
      flash('saved'); setEditId(null); reload();
    } catch (e) { flash('error: ' + e); }
  };

  if (loading) return <div className="card" style={{marginTop:18}}><div className="empty">loading…</div></div>;
  if (error) return <div className="card" style={{marginTop:18, color:"var(--rose)"}}>{error}</div>;

  const persons = data.persons || [];

  return (
    <div style={{marginTop:18}}>
      {msg && <div style={{marginBottom:10, fontSize:13, color:"var(--sage)"}} className="mono">{msg}</div>}
      <div className="card">
        {persons.map(p => (
          <div key={p.id} style={{padding:"14px 0", borderBottom:"1px solid var(--rule)"}}>
            {editId === p.id ? (
              <div>
                <label style={{display:"block", fontSize:13, color:"var(--ink-mute)", marginBottom:4}}>full name</label>
                <input value={editName} onChange={e => setEditName(e.target.value)}
                  placeholder="First Last"
                  style={{width:"100%", fontFamily:"inherit", fontSize:14,
                    background:"var(--paper-2)", border:"1px solid var(--rule)", borderRadius:4,
                    padding:"6px 10px", color:"var(--ink)", marginBottom:10}} />
                <label style={{display:"block", fontSize:13, color:"var(--ink-mute)", marginBottom:4}}>
                  nicknames / aliases <span style={{fontWeight:400}}>(comma-separated)</span>
                </label>
                <input value={editAliases} onChange={e => setEditAliases(e.target.value)}
                  placeholder="Trenki, T, …"
                  style={{width:"100%", fontFamily:"inherit", fontSize:13,
                    background:"var(--paper-2)", border:"1px solid var(--rule)", borderRadius:4,
                    padding:"6px 10px", color:"var(--ink)", marginBottom:10}} />
                <label style={{display:"block", fontSize:13, color:"var(--ink-mute)", marginBottom:4}}>impression</label>
                <textarea value={editImpression} onChange={e => setEditImpression(e.target.value)}
                  placeholder="How she reads this person…"
                  style={{width:"100%", minHeight:80, fontFamily:"inherit", fontSize:14,
                    background:"var(--paper-2)", border:"1px solid var(--rule)", borderRadius:6,
                    padding:"8px 10px", color:"var(--ink)", resize:"vertical", marginBottom:10}} />
                <label style={{display:"block", fontSize:13, color:"var(--ink-mute)", marginBottom:4}}>attachment pattern</label>
                <input value={editAttachment} onChange={e => setEditAttachment(e.target.value)}
                  placeholder="secure / anxious / avoidant…"
                  style={{width:"100%", fontFamily:"inherit", fontSize:13,
                    background:"var(--paper-2)", border:"1px solid var(--rule)", borderRadius:4,
                    padding:"6px 10px", color:"var(--ink)", marginBottom:12}} />
                <div style={{display:"flex", gap:8}}>
                  <button className="primary" onClick={save}>save</button>
                  <button className="ghost small" onClick={() => setEditId(null)}>cancel</button>
                </div>
              </div>
            ) : (
              <div style={{display:"flex", justifyContent:"space-between", alignItems:"flex-start", gap:12}}>
                <div>
                  <div style={{fontWeight:600}}>
                    {p.name} <span className="pill" style={{fontSize:11}}>{p.relation}</span>
                  </div>
                  {p.aliases && p.aliases.length > 0 && (
                    <div style={{fontSize:12, color:"var(--ink-mute)", marginTop:2}}>
                      also: {p.aliases.join(", ")}
                    </div>
                  )}
                  {p.one_line && <div style={{fontSize:14, fontStyle:"italic", color:"var(--ink-soft)", marginTop:4}}>{p.one_line}</div>}
                  {!p.one_line && <div style={{fontSize:13, color:"var(--ink-mute)", fontStyle:"italic", marginTop:4}}>no impression yet</div>}
                  <div className="mono" style={{fontSize:11, color:"var(--ink-mute)", marginTop:4}}>
                    warmth {p.warmth.toFixed(2)} · {p.attachment}
                  </div>
                </div>
                <button className="ghost small" onClick={() => startEdit(p)}>edit</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}


/* ── Admin: Inner State ── */
const INNER_KINDS = [
  { id: "interests",    label: "Interests",    url: "/admin/interests",       textKey: "label", metaKey: "why",    scaleKey: "intensity" },
  { id: "goals",        label: "Goals",        url: "/admin/inner/goals",     textKey: "text",  metaKey: "extra",  scaleKey: "progress" },
  { id: "wants",        label: "Wants",        url: "/admin/inner/wants",     textKey: "text",  metaKey: "extra",  scaleKey: "pressure" },
  { id: "fears",        label: "Fears",        url: "/admin/inner/fears",     textKey: "text",  metaKey: null,     scaleKey: "pressure" },
  { id: "tensions",     label: "Tensions",     url: "/admin/inner/tensions",  textKey: "text",  metaKey: null,     scaleKey: "pressure" },
  { id: "questions",    label: "Questions",    url: "/admin/inner/questions", textKey: "text",  metaKey: "extra",  scaleKey: "pressure" },
  { id: "anticipations",label: "Anticipations",url: "/admin/inner/anticipations", textKey: "text", metaKey: null, scaleKey: "pressure" },
  { id: "aversions",     label: "Aversions",     url: "/admin/inner/aversions",     textKey: "text", metaKey: null,    scaleKey: null },
  { id: "ideas",         label: "Ideas",         url: "/admin/inner/ideas",         textKey: "text", metaKey: null,    scaleKey: null },
  { id: "world_beliefs", label: "World Beliefs", url: "/admin/inner/world_beliefs", textKey: "text", metaKey: "extra", scaleKey: "pressure" },
];

function AdminInnerState() {
  const [kind, setKind] = React.useState("interests");
  const spec = INNER_KINDS.find(k => k.id === kind);
  return (
    <div style={{marginTop:18}}>
      <div style={{display:"flex", flexWrap:"wrap", gap:8, marginBottom:16}}>
        {INNER_KINDS.map(k => (
          <button key={k.id}
            className={"small" + (kind === k.id ? " primary" : "")}
            onClick={() => setKind(k.id)}
          >{k.label}</button>
        ))}
      </div>
      {spec && <AdminInnerList spec={spec} />}
    </div>
  );
}

function AdminInnerList({ spec }) {
  const { data, loading, error, reload } = useAdminFetch(spec.url);
  const [msg, setMsg] = React.useState(null);

  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(null), 2500); };

  const del = async (id) => {
    const deleteUrl = spec.id === "interests"
      ? `/admin/interests/${id}`
      : `/admin/inner/${spec.id}/${id}`;
    if (!confirm(`Delete this ${spec.label.toLowerCase().replace(/s$/, '')}?`)) return;
    try {
      await AdminApi('DELETE', deleteUrl);
      flash('deleted'); reload();
    } catch (e) { flash('error: ' + e); }
  };

  if (loading) return <div className="card"><div className="empty">loading…</div></div>;
  if (error)   return <div className="card" style={{color:"var(--rose)"}}>{error}</div>;

  const items = data
    ? (spec.id === "interests" ? (data.interests || []) : (data.items || []))
    : [];

  return (
    <div className="card">
      <div className="hd">
        {spec.label} ({items.length})
        <span className="stretch"></span>
        <button className="ghost small" onClick={reload}>refresh</button>
      </div>
      {msg && <div style={{fontSize:13, color:"var(--sage)", marginBottom:10}} className="mono">{msg}</div>}
      {items.length === 0 ? (
        <div className="empty" style={{padding:"12px 0"}}>nothing here.</div>
      ) : items.map(item => (
        <div key={item.id} style={{
          display:"flex", alignItems:"flex-start", gap:10,
          padding:"10px 0", borderBottom:"1px solid var(--rule)"
        }}>
          <div style={{flex:1, minWidth:0}}>
            <div style={{fontSize:14, lineHeight:1.5}}>{item[spec.textKey]}</div>
            <div style={{display:"flex", gap:10, marginTop:3, flexWrap:"wrap", alignItems:"center"}}>
              {spec.metaKey && item[spec.metaKey] && (
                <span style={{fontSize:12, color:"var(--ink-mute)", fontStyle:"italic"}}>{item[spec.metaKey]}</span>
              )}
              {spec.scaleKey && item[spec.scaleKey] != null && (
                <span className="mono" style={{fontSize:11, color:"var(--ink-mute)"}}>
                  {spec.scaleKey} {(item[spec.scaleKey] * 100).toFixed(0)}%
                </span>
              )}
              {item.resolved != null && (
                <span className="pill" style={{fontSize:10, opacity:0.7}}>
                  {item.resolved ? "resolved" : "active"}
                </span>
              )}
              {item.status && (
                <span className="pill" style={{fontSize:10, opacity:0.7}}>{item.status}</span>
              )}
              <span className="mono" style={{fontSize:11, color:"var(--ink-mute)"}}>#{item.id}</span>
            </div>
          </div>
          <button
            className="ghost small"
            style={{color:"var(--rose)", flexShrink:0}}
            onClick={() => del(item.id)}
          >delete</button>
        </div>
      ))}
    </div>
  );
}


/* ── Admin: Controls ── */
function AdminControls() {
  const [results, setResults] = React.useState({});
  const [loading, setLoading] = React.useState({});

  const run = async (id, path) => {
    setLoading(l => ({ ...l, [id]: true }));
    try {
      const r = await AdminApi('POST', path);
      setResults(res => ({ ...res, [id]: JSON.stringify(r, null, 2) }));
    } catch (e) {
      setResults(res => ({ ...res, [id]: 'error: ' + e }));
    }
    setLoading(l => ({ ...l, [id]: false }));
  };

  const controls = [
    { id: "reflect",  label: "Trigger reflect",          desc: "Force a full reflect pass (inner state + signals). Normally runs every 2h or on chat disconnect.", path: "/admin/reflect/trigger" },
    { id: "teo_read", label: "Synthesize Teo read",      desc: "Generate a standing read on Teo from recent narrative entries. Writes to identity:teo_read.", path: "/admin/teo-read/synthesize" },
    { id: "onboard",  label: "Re-run onboarding synthesis", desc: "Re-synthesize Teo's impression from onboarding memories. Does not re-ask questions.", path: "/admin/teo-read/synthesize" },
  ];

  return (
    <div style={{marginTop:18}}>
      {controls.map(c => (
        <div key={c.id} className="card" style={{marginBottom:14}}>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"flex-start", gap:16}}>
            <div>
              <div style={{fontWeight:600, marginBottom:4}}>{c.label}</div>
              <div style={{fontSize:14, color:"var(--ink-soft)"}}>{c.desc}</div>
            </div>
            <button
              className="primary"
              style={{flexShrink:0}}
              disabled={!!loading[c.id]}
              onClick={() => run(c.id, c.path)}
            >
              {loading[c.id] ? "running…" : "run"}
            </button>
          </div>
          {results[c.id] && (
            <pre style={{
              marginTop:12, fontSize:11, lineHeight:1.6, whiteSpace:"pre-wrap",
              wordBreak:"break-word", background:"var(--paper-2)",
              border:"1px solid var(--rule)", borderRadius:4, padding:"8px 10px",
              color:"var(--ink)", maxHeight:160, overflow:"auto",
            }}>{results[c.id]}</pre>
          )}
        </div>
      ))}
    </div>
  );
}


Object.assign(window, {
  NowTab, MemoryTab, RelationshipsTab, GoalsTab,
  AuditTab, ConfirmationsTab, IdentityTab, SettingsTab,
  InnerStateTab, DebugTab, AdminTab,
});
