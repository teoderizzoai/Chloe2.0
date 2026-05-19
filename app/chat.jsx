/* Chat drawer — connects to the real Chloe WebSocket. */

function ChatDrawer({ userName, open, onClose }) {
  const [messages, setMessages] = React.useState([]);
  const [text, setText] = React.useState("");
  const [thinking, setThinking] = React.useState(false);
  const [connected, setConnected] = React.useState(false);
  const logRef = React.useRef(null);
  const wsRef  = React.useRef(null);

  // Connect to real chat WebSocket when drawer opens.
  // Resolves person_id by name first so Teo and Zuza get separate histories.
  React.useEffect(() => {
    if (!open) return;
    const apiBase = window.__CHLOE_API_BASE__ || '';
    const wsBase  = window.__CHLOE_WS_BASE__  || '';
    let cancelled = false;

    fetch(`${apiBase}/v1/persons/resolve?name=${encodeURIComponent(userName || 'Teo')}`)
      .then(r => r.json())
      .catch(() => ({ person_id: 1 }))
      .then(({ person_id }) => {
        if (cancelled) return;
        let ws;
        try {
          ws = new WebSocket(`${wsBase}/v1/mobile/ws?person_id=${person_id}`);
        } catch (e) {
          return;
        }
        wsRef.current = ws;
        let accumulated = '';

        ws.onopen = () => setConnected(true);

        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === 'history') {
              setMessages((msg.messages || []).map(m => ({ from: m.from, text: m.text, at: '' })));
              return;
            }
            if (msg.type === 'chunk') {
              accumulated += msg.text || '';
              setMessages(m => {
                const last = m[m.length - 1];
                if (last && last.from === 'chloe' && last._streaming) {
                  return [...m.slice(0, -1), { ...last, text: accumulated }];
                }
                return [...m, { from: 'chloe', text: accumulated, at: nowTime(), _streaming: true }];
              });
            } else if (msg.type === 'done') {
              setThinking(false);
              accumulated = '';
              setMessages(m => {
                const last = m[m.length - 1];
                if (last && last._streaming) {
                  const { _streaming, ...clean } = last;
                  return [...m.slice(0, -1), clean];
                }
                return m;
              });
            } else if (msg.type === 'error') {
              setThinking(false);
              accumulated = '';
              setMessages(m => [...m, { from: 'system', text: 'Something went wrong.', at: nowTime() }]);
            }
          } catch (_) {}
        };

        ws.onerror = () => {
          setThinking(false);
          setConnected(false);
        };

        ws.onclose = () => {
          wsRef.current = null;
          setConnected(false);
        };
      });

    return () => {
      cancelled = true;
      try { if (wsRef.current) wsRef.current.close(); } catch (_) {}
      wsRef.current = null;
      setConnected(false);
    };
  }, [open]);

  React.useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [messages, thinking]);

  function send(e) {
    e && e.preventDefault();
    const t = text.trim();
    if (!t) return;
    setMessages(m => [...m, { from: "me", text: t, at: nowTime() }]);
    setText("");

    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      setThinking(true);
      ws.send(JSON.stringify({ type: 'message', text: t }));
    } else {
      setMessages(m => [...m, { from: 'system', text: 'Not connected to Chloe.', at: nowTime() }]);
    }
  }

  if (!open) return null;

  return (
    <div className="chat-drawer" role="dialog" aria-label="Chat with Chloe">
      <div className="hd">
        <Avatar size="xs" />
        <div className="who">
          <div className="n">Chloe</div>
          <div className="s">{connected ? 'connected' : 'connecting…'}</div>
        </div>
        <button className="ghost x" onClick={onClose} aria-label="Close chat">✕</button>
      </div>
      <div className="chat-log" ref={logRef}>
        {messages.length === 0 && !thinking && (
          <div className="msg system">Say something.</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={"msg " + (m.from === "me" ? "mine" : m.from === "system" ? "system" : "her")}>
            {m.from !== "system" && (
              <div className="who">{m.from === "me" ? (userName || "you") : "chloe"}{m.at ? ' · ' + m.at : ''}</div>
            )}
            <div>{m.text}</div>
          </div>
        ))}
        {thinking && (
          <div className="msg her thinking">
            <div className="who">chloe · {nowTime()}</div>
            <div></div>
          </div>
        )}
      </div>
      <form className="chat-form" onSubmit={send}>
        <input
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="say something to her…"
          autoFocus
        />
        <button className="primary" type="submit" disabled={!text.trim()}>send</button>
      </form>
    </div>
  );
}

function nowTime() {
  const d = new Date();
  return d.getHours().toString().padStart(2, "0") + ":" + d.getMinutes().toString().padStart(2, "0");
}

window.ChatDrawer = ChatDrawer;
