from __future__ import annotations

from datetime import datetime

from flask import Blueprint, Response, abort, jsonify, request

from app.analysis import run_analysis
from app.meal_logging import log_meal
from app.png_icon import solid_png
from app.state import get_state

bp = Blueprint("webapp", __name__)

ICON_RGB = (37, 99, 235)


def _authed_state() -> dict:
    state = get_state()
    config = state["config"]
    token = request.args.get("token") or request.headers.get("X-App-Token")
    if token != config.webapp_token:
        abort(403)
    return state


@bp.get("/app")
def index():
    _authed_state()
    return Response(INDEX_HTML, mimetype="text/html")


@bp.get("/app/manifest.json")
def manifest():
    _authed_state()
    token = request.args.get("token", "")
    return jsonify(
        {
            "name": "Meal Stress Log",
            "short_name": "MealLog",
            "start_url": f"/app?token={token}",
            "scope": "/app",
            "display": "standalone",
            "background_color": "#0b1120",
            "theme_color": "#0b1120",
            "icons": [
                {"src": "/app/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/app/icon-512.png", "sizes": "512x512", "type": "image/png"},
            ],
        }
    )


@bp.get("/app/sw.js")
def service_worker():
    return Response(SW_JS, mimetype="application/javascript")


@bp.get("/app/icon-192.png")
def icon_192():
    return Response(solid_png(192, ICON_RGB), mimetype="image/png")


@bp.get("/app/icon-512.png")
def icon_512():
    return Response(solid_png(512, ICON_RGB), mimetype="image/png")


@bp.get("/app/api/meals")
def api_list_meals():
    state = _authed_state()
    meals = state["db"].get_meals(limit=20)
    return jsonify(
        [
            {
                "id": m.id,
                "meal_time": m.meal_time.isoformat(),
                "raw_text": m.raw_text,
                "foods": m.foods,
            }
            for m in meals
        ]
    )


@bp.post("/app/api/meals")
def api_log_meal():
    state = _authed_state()
    config = state["config"]
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "leeg bericht"}), 400

    result = log_meal(
        state["db"],
        config,
        text=text,
        message_time=datetime.now(config.timezone),
        raw_update={"source": "webapp", "text": text},
    )
    return jsonify(
        {
            "id": result.meal_id,
            "meal_time": result.meal_time.isoformat(),
            "time_note": result.time_note,
            "foods": result.foods,
        }
    )


@bp.delete("/app/api/meals/<meal_id>")
def api_delete_meal(meal_id: str):
    state = _authed_state()
    ok = state["db"].delete_meal(meal_id)
    return jsonify({"deleted": ok})


@bp.post("/app/api/analyse")
def api_analyse():
    state = _authed_state()
    config = state["config"]
    report = run_analysis(state["db"], state["garmin"], config.timezone)
    return jsonify({"report": report})


SW_JS = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
"""

INDEX_HTML = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Meal Stress Log</title>
<meta name="theme-color" content="#0b1120">
<link rel="manifest" id="manifest-link" href="">
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px; padding-bottom: 32px;
    background: #0b1120; color: #e5e7eb;
    font: 16px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  h1 { font-size: 18px; margin: 0 0 16px; color: #93c5fd; }
  textarea {
    width: 100%; min-height: 72px; padding: 12px; border-radius: 10px;
    border: 1px solid #334155; background: #111827; color: #e5e7eb;
    font-size: 16px; resize: vertical;
  }
  button {
    font-size: 16px; padding: 12px 16px; border-radius: 10px; border: none;
    background: #2563eb; color: white; font-weight: 600; cursor: pointer;
  }
  button:active { background: #1d4ed8; }
  button.secondary { background: #374151; }
  button.danger { background: #7f1d1d; padding: 6px 10px; font-size: 13px; }
  .row { display: flex; gap: 8px; margin-top: 8px; }
  .row > button { flex: 1; }
  ul { list-style: none; padding: 0; margin: 20px 0 0; }
  li {
    display: flex; justify-content: space-between; align-items: center;
    gap: 8px; padding: 10px 0; border-bottom: 1px solid #1f2937;
  }
  .meal-text { flex: 1; min-width: 0; }
  .meal-time { color: #9ca3af; font-size: 13px; }
  .meal-foods { color: #93c5fd; font-size: 13px; }
  #status { margin-top: 8px; font-size: 14px; color: #9ca3af; min-height: 20px; }
  #report {
    white-space: pre-wrap; background: #111827; border-radius: 10px;
    padding: 12px; margin-top: 12px; font-size: 14px; display: none;
  }
  h2 { font-size: 14px; color: #9ca3af; margin: 24px 0 4px; text-transform: uppercase; }
</style>
</head>
<body>
<h1>Wat heb je gegeten?</h1>
<textarea id="text" placeholder="bv. om 18:30 pizza margherita gegeten" autofocus></textarea>
<div class="row">
  <button id="log-btn">Loggen</button>
  <button id="analyse-btn" class="secondary">Analyseer</button>
</div>
<div id="status"></div>
<div id="report"></div>

<h2>Laatste maaltijden</h2>
<ul id="meals"></ul>

<script>
const params = new URLSearchParams(location.search);
const token = params.get('token') || '';
document.getElementById('manifest-link').href = '/app/manifest.json?token=' + encodeURIComponent(token);

function api(path, options) {
  const url = path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
  return fetch(url, options).then(r => r.json());
}

function setStatus(msg) {
  document.getElementById('status').textContent = msg;
}

function renderMeals(meals) {
  const ul = document.getElementById('meals');
  ul.innerHTML = '';
  for (const m of meals) {
    const li = document.createElement('li');
    const dt = new Date(m.meal_time);
    const timeStr = dt.toLocaleString('nl-NL', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    li.innerHTML = `
      <div class="meal-text">
        <div>${escapeHtml(m.raw_text)}</div>
        <div class="meal-time">${timeStr}${m.foods.length ? ' &middot; <span class="meal-foods">' + m.foods.map(escapeHtml).join(', ') + '</span>' : ''}</div>
      </div>
      <button class="danger" data-id="${m.id}">wis</button>
    `;
    li.querySelector('button').addEventListener('click', () => deleteMeal(m.id));
    ul.appendChild(li);
  }
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function loadMeals() {
  const meals = await api('/app/api/meals');
  renderMeals(meals);
}

async function logMeal() {
  const textEl = document.getElementById('text');
  const text = textEl.value.trim();
  if (!text) return;
  setStatus('Loggen...');
  const result = await api('/app/api/meals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (result.error) {
    setStatus('Fout: ' + result.error);
    return;
  }
  textEl.value = '';
  const dt = new Date(result.meal_time);
  setStatus(`Gelogd om ${dt.toLocaleTimeString('nl-NL', {hour:'2-digit', minute:'2-digit'})} (${result.time_note}). Tags: ${result.foods.join(', ') || '(geen herkend)'}`);
  loadMeals();
}

async function deleteMeal(id) {
  await api('/app/api/meals/' + encodeURIComponent(id), { method: 'DELETE' });
  loadMeals();
}

async function analyse() {
  setStatus('Bezig met analyseren, kan even duren...');
  const reportEl = document.getElementById('report');
  reportEl.style.display = 'none';
  const result = await api('/app/api/analyse', { method: 'POST' });
  setStatus('');
  reportEl.textContent = result.report;
  reportEl.style.display = 'block';
}

document.getElementById('log-btn').addEventListener('click', logMeal);
document.getElementById('analyse-btn').addEventListener('click', analyse);

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/app/sw.js');
}

loadMeals();
</script>
</body>
</html>
"""
