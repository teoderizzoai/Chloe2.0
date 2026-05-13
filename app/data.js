/* data.js — no static mock data. Live data loads from /v1/dashboard/state. */

window.__CHLOE_API_BASE__ = location.protocol === 'file:' ? 'http://localhost:8000' : '';
window.__CHLOE_WS_BASE__  = location.protocol === 'file:'
  ? 'ws://localhost:8000'
  : (location.protocol === 'https:' ? 'wss' : 'ws') + '://' + location.host;

// Empty baseline — shown only if the server is unreachable.
window.CHLOE = {
  meta:             { location: "", local_time: "", weather: "", since: "", day_started_at: "" },
  affect:           { valence: 0, arousal: 0, social_pull: 0, openness: 0, label: "", sublabel: "" },
  vitals:           {
    energy:         { value: 0, label: "" },
    rest_debt:      { value: 0, label: "", invert: true },
    social_battery: { value: 0, label: "" },
    curiosity:      { value: 0, label: "" },
  },
  arc:              { name: "", started: "", summary: "" },
  current_activity: { line: "", since: "", artifact: "" },
  garden:           [],
  goals:            [],
  memories:         [],
  persons:          [],
  audit:            [],
  confirmations:    [],
  identity:         { traits_core: [], traits_emerging: [], traits_archived: [], beliefs: [], contradictions: [], next_week_intention: "" },
  settings:         { quiet_hours: { start: "23:00", end: "08:00" }, away_mode: false, focus_mode: false, auth_ceiling: "kinetic", spending: { cap_usd_day: 1.50, spent_usd_today: 0.0 }, dont_touch: { gmail_labels: [], notes_folders: [], spotify_playlists: [] } },
  inner_state:      {
    world_beliefs: [], questions: [], tensions: [], wants: [], fears: [],
    anticipations: [], aversions: [], ideas: [], narrative_timeline: [], character_addenda: [],
    reflect: { last_run_at: "", emotions: [], biased_summary: "", recurring_loops: [] },
    kv: { novelty_deficit: 0, teo_read: "", aesthetic_orientation: "" },
  },
};

// Kick off the live-data fetch immediately.
window.__CHLOE_LOAD__ = fetch(window.__CHLOE_API_BASE__ + '/v1/dashboard/state')
  .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
  .then(live => { window.CHLOE = live; return live; })
  .catch(err => {
    console.warn('[chloe] live data unavailable', err);
    return window.CHLOE;
  });
