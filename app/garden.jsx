/* Interest Garden — three viz modes.
   Botanical: flowers in a row, stem height = intensity.
   Constellation: stars at deterministic positions, size = intensity, with faint lines.
   Bubbles: floating organic blobs, size = intensity.
   All three share the same data and a hover-selects-detail panel below. */

function Garden({ garden }) {
  const [mode, setMode] = React.useState("constellation");
  const [hover, setHover] = React.useState(null);
  const active = hover || (garden.length > 0 ? garden[0] : null);

  if (!garden.length) {
    return (
      <div>
        <div className="hd"><span>Interest Garden</span></div>
        <div className="empty" style={{padding: "24px 0"}}>nothing has caught yet.</div>
      </div>
    );
  }

  return (
    <div>
      <div className="hd">
        <span>Interest Garden</span>
        <span className="stretch"></span>
        <div className="garden-mode-tabs">
          <button aria-selected={mode === "botanical"}     onClick={() => setMode("botanical")}>botanical</button>
          <button aria-selected={mode === "constellation"} onClick={() => setMode("constellation")}>constellation</button>
          <button aria-selected={mode === "bubbles"}       onClick={() => setMode("bubbles")}>bubbles</button>
        </div>
      </div>

      <div className="garden-stage">
        {mode === "botanical"     && <GardenBotanical     garden={garden} onHover={setHover} active={active} />}
        {mode === "constellation" && <GardenConstellation garden={garden} onHover={setHover} active={active} />}
        {mode === "bubbles"       && <GardenBubbles       garden={garden} onHover={setHover} active={active} />}
      </div>

      {active && (
        <div className="garden-detail">
          <div className="name">{active.label}</div>
          <div>{active.why} <span className="mono" style={{color:"var(--ink-mute)", marginLeft: 8, fontSize:11}}>· last touched {active.last_engaged}</span></div>
          <div className="arts">
            {(active.artifacts || []).map((a, i) => (
              <span className="art" key={i}>
                <span className="k">{a.kind.replace(/_/g, ' ')}</span>{a.title}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ───────── Botanical ───────── */
function GardenBotanical({ garden, onHover, active }) {
  return (
    <div className="bot">
      {garden.map(g => {
        const h = 60 + g.intensity * 200; // 60..260 px stem
        const dim = g.intensity < 0.35;
        return (
          <div
            key={g.id}
            className={"plant" + (dim ? " dim" : "")}
            onMouseEnter={() => onHover(g)}
            onMouseLeave={() => onHover(null)}
            onClick={() => onHover(g)}
            style={{opacity: active && active.id === g.id ? 1 : 0.92}}
          >
            <div className="bloom" style={{width: 16 + g.intensity*16, height: 16 + g.intensity*16}}></div>
            <div className="stem" style={{height: h}}>
              <span className="leaf-mid" style={{top: `${40 + (1-g.intensity)*25}%`}}></span>
            </div>
            <div className="lbl">{g.label}</div>
          </div>
        );
      })}
    </div>
  );
}

/* ───────── Constellation ───────── */
function GardenConstellation({ garden, onHover, active }) {
  // Deterministic-ish positions, hand-tuned to look like a small sky.
  const positions = [
    { x: 18, y: 32 }, { x: 36, y: 52 }, { x: 52, y: 28 }, { x: 68, y: 46 },
    { x: 82, y: 30 }, { x: 28, y: 72 }, { x: 58, y: 68 }, { x: 78, y: 70 },
  ];
  // Build edges between adjacent stars in the list (simple "constellation")
  const edges = [
    [0,1],[1,2],[2,3],[3,4],[1,5],[3,6],[6,7],[5,6],
  ];
  return (
    <div className="con">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none">
        {edges.map(([a,b], i) => {
          const pa = positions[a], pb = positions[b];
          return <line key={i} x1={pa.x} y1={pa.y} x2={pb.x} y2={pb.y}
            stroke="var(--rule)" strokeWidth="0.15" strokeDasharray="0.5 0.5" />;
        })}
      </svg>
      {garden.map((g, i) => {
        const p = positions[i] || { x: 50, y: 50 };
        const size = 6 + g.intensity * 22;
        const dim = g.intensity < 0.35;
        const isActive = active && active.id === g.id;
        return (
          <div
            key={g.id}
            className={"star" + (dim ? " dim" : "")}
            style={{
              left: `${p.x}%`, top: `${p.y}%`,
              width: size, height: size,
              opacity: isActive ? 1 : 0.95,
              transform: isActive ? "translate(-50%,-50%) scale(1.18)" : "translate(-50%,-50%)",
              transition: "transform .25s",
            }}
            onMouseEnter={() => onHover(g)}
            onMouseLeave={() => onHover(null)}
            onClick={() => onHover(g)}
          >
            {isActive && <span className="lab">{g.label}</span>}
          </div>
        );
      })}
    </div>
  );
}

/* ───────── Bubbles ───────── */
function GardenBubbles({ garden, onHover, active }) {
  // Hand-placed positions so they don't overlap badly.
  const positions = [
    { x: 25, y: 38 }, { x: 55, y: 30 }, { x: 80, y: 42 }, { x: 38, y: 66 },
    { x: 66, y: 62 }, { x: 88, y: 72 }, { x: 14, y: 70 }, { x: 50, y: 86 },
  ];
  return (
    <div className="bub">
      {garden.map((g, i) => {
        const p = positions[i] || { x: 50, y: 50 };
        const size = 60 + g.intensity * 100;
        const dim = g.intensity < 0.35;
        const isActive = active && active.id === g.id;
        return (
          <div
            key={g.id}
            className={"blob" + (dim ? " dim" : "")}
            style={{
              left: `${p.x}%`, top: `${p.y}%`,
              width: size, height: size,
              transform: isActive ? "translate(-50%,-50%) scale(1.06)" : "translate(-50%,-50%)",
              zIndex: isActive ? 2 : 1,
            }}
            onMouseEnter={() => onHover(g)}
            onMouseLeave={() => onHover(null)}
            onClick={() => onHover(g)}
          >
            <div className="lab">{g.label}</div>
          </div>
        );
      })}
    </div>
  );
}

window.Garden = Garden;
