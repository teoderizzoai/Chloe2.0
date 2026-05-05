from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from chloe.actions import audit

admin_router = APIRouter()

AUDIT_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Chloe — Audit Feed</title>
  <meta charset="utf-8">
  <style>
    body { font-family: monospace; background: #111; color: #eee; padding: 20px; }
    h1 { color: #9f9; }
    table { border-collapse: collapse; width: 100%; }
    th { text-align: left; background: #222; padding: 8px; border-bottom: 1px solid #444; }
    td { padding: 6px 8px; border-bottom: 1px solid #333; font-size: 12px; }
    .state-executed { color: #9f9; }
    .state-self_aborted, .state-suppressed_by_leash { color: #fa0; }
    .state-denied, .state-failed { color: #f66; }
    .state-awaiting_confirmation { color: #99f; }
    .state-reverted { color: #aaa; }
    #ts { color: #888; font-size: 11px; margin-bottom: 10px; }
  </style>
  <script>
    async function refresh() {
      const resp = await fetch('/admin/audit?limit=200');
      const data = await resp.json();
      const tbody = document.getElementById('tbody');
      tbody.innerHTML = data.actions.map(a => `
        <tr>
          <td>${new Date(a.proposed_at).toLocaleTimeString()}</td>
          <td>${a.tool}</td>
          <td>${a.verb}</td>
          <td class="state-${a.state}">${a.state}</td>
          <td>${a.intent.substring(0, 80)}</td>
          <td>$${a.cost_usd.toFixed(4)}</td>
        </tr>
      `).join('');
      document.getElementById('ts').textContent = 'Updated: ' + new Date().toLocaleTimeString();
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</head>
<body>
  <h1>Audit Feed</h1>
  <div id="ts"></div>
  <table>
    <thead>
      <tr>
        <th>Time</th><th>Tool</th><th>Verb</th><th>State</th><th>Intent</th><th>Cost</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</body>
</html>
"""


@admin_router.get("/audit")
async def get_audit(limit: int = Query(200, le=500)) -> dict:
    actions = await audit.recent(n=limit)
    return {
        "count": len(actions),
        "actions": [
            {
                "id": a.id,
                "tool": a.tool,
                "verb": a.verb,
                "intent": a.intent,
                "preview": a.preview,
                "state": a.state,
                "authorization": a.authorization,
                "proposed_at": a.proposed_at.isoformat(),
                "cost_usd": a.cost_estimate.usd,
                "error": a.error,
            }
            for a in actions
        ],
    }


@admin_router.get("/audit/ui", response_class=HTMLResponse)
async def audit_ui():
    return AUDIT_HTML
