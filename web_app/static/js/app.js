// The app shell: boots the modes listed by /api/app/modes (web_app/registry.py),
// switches modes and tabs, and holds the small helpers every mode script
// uses. Each mode script (js/<id>.js) is loaded after its markup
// (/modes/<id>.html) is in the page, calls registerMode(id, {refresh}), and
// self-initializes once `sessionReady` resolves.

// -- Mode registry ------------------------------------------------------------------------
const MODE_KEY = "sensor-studio-mode";
const MODES = {};          // id -> {id, label, scripts, report, undo} from the server
const MODE_HANDLERS = {};  // id -> {refresh(state?)} from registerMode
let currentMode = null;

// Called by each mode script. refresh(state) re-renders the mode, from the
// given state or by fetching it. Optional confirmDiscard() returns false to
// cancel an undo/redo that would throw away unsaved edits.
function registerMode(id, handlers) {
  MODE_HANDLERS[id] = handlers;
}

function showMode(mode) {
  const btn = document.querySelector(`.mode-btn[data-mode="${mode}"]`);
  if (!btn) return;
  currentMode = mode;
  document.querySelectorAll(".mode-btn").forEach((b) => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".mode-page").forEach((p) => p.classList.toggle("active", p.id === `mode-${mode}`));
  try { localStorage.setItem(MODE_KEY, mode); } catch (_e) { /* storage unavailable */ }
  resizePlotsIn(document.getElementById(`mode-${mode}`));
  document.getElementById("report-btn").hidden = !(MODES[mode] && MODES[mode].report);
  refreshUndoButtons();
}

function showTab(scopeEl, tab) {
  scopeEl.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  scopeEl.querySelectorAll(".tab-page").forEach((p) => p.classList.toggle("active", p.dataset.tabPage === tab));
  resizePlotsIn(scopeEl);
  scopeEl.dispatchEvent(new CustomEvent("tabshown", { detail: { tab } }));
}

// Plotly sizes a chart from its container at draw time; a chart drawn into a
// hidden tab comes out the wrong size, so re-fit visible charts on show.
function resizePlotsIn(el) {
  if (!el) return;
  requestAnimationFrame(() => {
    el.querySelectorAll(".js-plotly-plot").forEach((p) => {
      if (p.offsetParent !== null) Plotly.Plots.resize(p);
    });
  });
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error(`Could not load ${src}`));
    document.body.appendChild(el);
  });
}

// Builds the nav and one <section> per mode, then loads the scripts in
// order (shared ones once, before the first mode that needs them).
async function bootModes() {
  const { modes } = await (await fetch("/api/app/modes")).json();
  const nav = document.getElementById("mode-nav");
  const main = document.getElementById("modes");
  const fragments = await Promise.all(modes.map((m) => fetch(`/modes/${m.id}.html`).then((r) => {
    if (!r.ok) throw new Error(`Missing /modes/${m.id}.html`);
    return r.text();
  })));
  modes.forEach((m, i) => {
    MODES[m.id] = m;
    const btn = document.createElement("button");
    btn.className = "mode-btn";
    btn.dataset.mode = m.id;
    btn.textContent = m.label;
    btn.addEventListener("click", () => showMode(m.id));
    nav.appendChild(btn);
    const section = document.createElement("section");
    section.id = `mode-${m.id}`;
    section.className = "mode-page";
    section.innerHTML = fragments[i];
    section.querySelectorAll(".tab-btn").forEach((tb) => {
      tb.addEventListener("click", () => showTab(section, tb.dataset.tab));
    });
    main.appendChild(section);
  });
  let saved = null;
  try { saved = localStorage.getItem(MODE_KEY); } catch (_e) { /* storage unavailable */ }
  showMode(MODES[saved] ? saved : modes[0].id);
  const loaded = new Set();
  for (const m of modes) {
    for (const src of [...m.scripts, `${m.id}.js`]) {
      if (loaded.has(src)) continue;
      loaded.add(src);
      await loadScript(`/js/${src}`);
    }
  }
}

// -- Notifications -------------------------------------------------------------------
function toast(message, kind = "info") {
  const host = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = message;
  el.addEventListener("click", () => el.remove());
  host.appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 8000 : 3500);
}

function setStatus(id, message, kind = "info") {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message || "";
  el.dataset.kind = kind;
}

// -- API helpers ------------------------------------------------------------------------
function formatDetail(detail, fallback) {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  // FastAPI 422 validation errors: [{loc: [...], msg: "..."}, ...]
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc || []).filter((x) => x !== "body").join(".")}: ${d.msg}`).join("; ");
  }
  return JSON.stringify(detail);
}

async function apiCall(path, opts) {
  const res = await fetch(path, opts);
  noteUndoHeader(res);
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = formatDetail(body.detail, detail);
    } catch (_e) { /* no JSON body */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  const contentType = res.headers.get("content-type") || "";
  return contentType.includes("application/json") ? res.json() : res;
}

function apiPostJson(path, body) {
  return apiCall(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function apiDelete(path) {
  return apiCall(path, { method: "DELETE" });
}

// Wrap an async click handler: disables the button while it runs and turns
// any thrown error into a visible toast instead of a silent console error.
function onClick(id, handler) {
  const el = document.getElementById(id);
  el.addEventListener("click", async (e) => {
    if (el.disabled) return;
    el.disabled = true;
    try {
      await handler(e);
    } catch (err) {
      toast(err.message, "error");
    } finally {
      el.disabled = false;
    }
  });
}

// Downloads go through fetch (not a plain <a href>) so POST bodies and
// backend error messages both work.
async function download(path, body) {
  try {
    const opts = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    };
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = formatDetail((await res.json()).detail, detail); } catch (_e) { /* not JSON */ }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const disposition = res.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : "export";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`Saved ${filename}`, "success");
  } catch (err) {
    toast("Export failed: " + err.message, "error");
  }
}

// -- Plot helpers --------------------------------------------------------------------------
const PLOT_CONFIG = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ["select2d", "lasso2d"],
  toImageButtonOptions: { format: "png", scale: 2 },
};

function drawPlot(id, fig, extraConfig) {
  const el = document.getElementById(id);
  if (!fig) {
    Plotly.purge(el);
    el.innerHTML = "";
    el.classList.add("empty");
    return;
  }
  el.classList.remove("empty");
  Plotly.react(el, fig.data, fig.layout, { ...PLOT_CONFIG, ...(extraConfig || {}) });
}

// -- Checklists ----------------------------------------------------------------------------
// Remembers what the user has unchecked; any label never seen before (a newly
// loaded file/channel/run) starts checked, so adding data never blanks a plot.
function renderChecklist(containerId, labels, onChange) {
  const div = document.getElementById(containerId);
  const known = new Set(JSON.parse(div.dataset.known || "[]"));
  const checked = new Set(Array.from(div.querySelectorAll("input:checked")).map((cb) => cb.value));
  div.innerHTML = "";
  if (labels.length > 1) {
    const bar = document.createElement("span");
    bar.className = "checklist-actions";
    [["All", true], ["None", false]].forEach(([text, value]) => {
      const a = document.createElement("button");
      a.type = "button";
      a.className = "link-btn";
      a.textContent = text;
      a.addEventListener("click", () => {
        div.querySelectorAll("input[type=checkbox]").forEach((cb) => { cb.checked = value; });
        onChange();
      });
      bar.appendChild(a);
    });
    div.appendChild(bar);
  }
  labels.forEach((label) => {
    const wrap = document.createElement("label");
    wrap.className = "checklist-item";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = label;
    cb.checked = known.has(label) ? checked.has(label) : true;
    cb.addEventListener("change", onChange);
    wrap.append(cb, document.createTextNode(" " + label));
    div.appendChild(wrap);
  });
  div.dataset.known = JSON.stringify(labels);
}

function checkedValues(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} input[type=checkbox]:checked`)).map((cb) => cb.value);
}

// -- Small DOM helpers ------------------------------------------------------------------------
function labeled(text, el) {
  const wrap = document.createElement("label");
  wrap.appendChild(document.createTextNode(text + " "));
  wrap.appendChild(el);
  return wrap;
}

function renderTable(tableId, rows, opts = {}) {
  const table = document.getElementById(tableId);
  table.innerHTML = "";
  if (!rows || !rows.length) return;
  const columns = Object.keys(rows[0]);
  const thead = document.createElement("tr");
  columns.forEach((c) => { const th = document.createElement("th"); th.textContent = c; thead.appendChild(th); });
  table.appendChild(thead);
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    if (opts.rowClass) tr.className = opts.rowClass(row) || "";
    columns.forEach((c) => {
      const td = document.createElement("td");
      td.textContent = row[c] === null || row[c] === undefined ? "—" : row[c];
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });
}

function columnSelect(columns, current, onChange) {
  const sel = document.createElement("select");
  columns.forEach((col) => {
    const opt = document.createElement("option");
    opt.value = col;
    opt.textContent = col;
    if (col === current) opt.selected = true;
    sel.appendChild(opt);
  });
  sel.addEventListener("change", () => onChange(sel.value));
  return sel;
}

// A loaded-files list with a remove (×) button per entry.
function renderFilesList(ulId, items, onRemove) {
  const ul = document.getElementById(ulId);
  ul.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "hint";
    li.textContent = "No files loaded yet.";
    ul.appendChild(li);
    return;
  }
  items.forEach((text, i) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.textContent = text;
    const rm = document.createElement("button");
    rm.className = "icon-btn";
    rm.title = "Remove this file";
    rm.textContent = "×";
    rm.addEventListener("click", async () => {
      try { await onRemove(i); } catch (err) { toast(err.message, "error"); }
    });
    li.append(span, rm);
    ul.appendChild(li);
  });
}

function setupDropzone(id, onFiles) {
  const zone = document.getElementById(id);
  ["dragenter", "dragover"].forEach((evt) => zone.addEventListener(evt, (e) => {
    e.preventDefault();
    zone.classList.add("drag-active");
  }));
  ["dragleave", "drop"].forEach((evt) => zone.addEventListener(evt, (e) => {
    e.preventDefault();
    zone.classList.remove("drag-active");
  }));
  zone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files);
  });
}

function setupFileInput(id, onFiles) {
  document.getElementById(id).addEventListener("change", (e) => {
    if (e.target.files.length) onFiles(Array.from(e.target.files));
    e.target.value = "";
  });
}

// Export options (format/DPI/style/size) — every mode's Export tab uses the
// same control ids with its own prefix ("ss", "amp", "cv", "assay").
function exportOptions(prefix) {
  const width = parseFloat(document.getElementById(`${prefix}-export-width`).value);
  const height = parseFloat(document.getElementById(`${prefix}-export-height`).value);
  return {
    fmt: document.getElementById(`${prefix}-export-fmt`).value,
    dpi: parseInt(document.getElementById(`${prefix}-export-dpi`).value, 10),
    style: document.getElementById(`${prefix}-export-style`).value,
    figsize: (width > 0 && height > 0) ? [width, height] : null,
  };
}

// Live preview of a static export: same endpoint, forced to a low-DPI PNG.
async function previewExport(imgId, path, body) {
  const img = document.getElementById(imgId);
  try {
    const res = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, fmt: "png", dpi: 90 }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = formatDetail((await res.json()).detail, detail); } catch (_e) { /* not JSON */ }
      throw new Error(detail);
    }
    if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
    img.src = URL.createObjectURL(await res.blob());
    img.parentElement.classList.remove("empty");
    img.parentElement.dataset.message = "";
  } catch (err) {
    img.removeAttribute("src");
    img.parentElement.classList.add("empty");
    img.parentElement.dataset.message = err.message;
  }
}

// -- Undo / redo -------------------------------------------------------------------------------
// Every mode response carries X-Undo: "<undo>,<redo>" for that mode
// (web_app/main.py), so the buttons stay current without extra requests.
const undoCounts = {};

function noteUndoHeader(res) {
  const mode = res.headers.get("X-Undo-Mode");
  const counts = res.headers.get("X-Undo");
  if (!mode || !counts) return;
  const [undo, redo] = counts.split(",").map(Number);
  undoCounts[mode] = { undo, redo };
  if (mode === currentMode) refreshUndoButtons();
}

function refreshUndoButtons() {
  const c = undoCounts[currentMode] || { undo: 0, redo: 0 };
  const supported = !!(MODES[currentMode] && MODES[currentMode].undo);
  document.getElementById("undo-btn").disabled = !supported || !c.undo;
  document.getElementById("redo-btn").disabled = !supported || !c.redo;
}

async function historyStep(direction) {
  const mode = currentMode;
  if (!mode || !MODE_HANDLERS[mode]) return;
  const c = undoCounts[mode] || {};
  if (!c[direction]) return;
  const h = MODE_HANDLERS[mode];
  if (h.confirmDiscard && !h.confirmDiscard()) return;
  try {
    const state = await apiPostJson(`/api/history/${mode}/${direction}`, {});
    await MODE_HANDLERS[mode].refresh(state);
    toast(direction === "undo" ? "Undone." : "Redone.");
  } catch (err) {
    toast(err.message, "error");
  }
}

document.getElementById("undo-btn").addEventListener("click", () => historyStep("undo"));
document.getElementById("redo-btn").addEventListener("click", () => historyStep("redo"));

// ⌘Z / ⇧⌘Z (Ctrl on other systems), except while typing in a field, where
// the browser's own text undo applies.
document.addEventListener("keydown", (e) => {
  if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "z" || e.altKey) return;
  const t = e.target;
  if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
  e.preventDefault();
  historyStep(e.shiftKey ? "redo" : "undo");
});

// -- Report ----------------------------------------------------------------------------------
document.getElementById("report-btn").addEventListener("click", () => {
  if (currentMode) window.open(`/api/report/${currentMode}`, "_blank");
});

// -- Session bootstrap + Tier 2 persistence (Export/Import Session JSON) ---------------------
// Ping once before any mode fires its parallel /state requests, so they all
// share one session cookie.
const sessionReady = fetch("/api/session/ping").catch(() => null);

async function refreshAllModes() {
  await Promise.all(Object.values(MODE_HANDLERS).map((h) => h.refresh()));
}

function loadedText(loaded) {
  return loaded && loaded.length ? loaded.join(", ") : "no data";
}

document.getElementById("session-export-btn").addEventListener("click", () => {
  download("/api/session/export");
});

document.getElementById("session-import-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const r = await apiCall("/api/session/import", { method: "POST", body: form });
    toast(`Session imported: ${loadedText(r.loaded)}.`, "success");
    document.getElementById("restore-banner").hidden = true;
    await refreshAllModes();
  } catch (err) {
    toast("Import failed: " + err.message, "error");
  }
});

// -- Autosave restore -------------------------------------------------------------------------
// The server autosaves the session (web_app/autosave.py); after a restart a
// fresh page is offered the previous run's work.
async function offerRestore() {
  let info;
  try { info = await apiCall("/api/autosave"); } catch (_e) { return; }
  if (!info || !info.available) return;
  document.getElementById("restore-text").textContent =
    `Restore your previous session from ${info.saved_at} (${loadedText(info.summary)})?`;
  document.getElementById("restore-banner").hidden = false;
}

onClick("restore-btn", async () => {
  const r = await apiPostJson("/api/autosave/restore", {});
  document.getElementById("restore-banner").hidden = true;
  await refreshAllModes();
  toast(`Restored: ${loadedText(r.loaded)}.`, "success");
});

onClick("restore-discard-btn", async () => {
  await apiPostJson("/api/autosave/discard", {});
  document.getElementById("restore-banner").hidden = true;
});

// -- App lifecycle: heartbeat + Quit -------------------------------------------------------
// The server can be started without a terminal (the macOS app), so the page
// is how it gets stopped: an explicit Quit, or an idle shutdown once no tab
// has sent a heartbeat for a while.
setInterval(() => fetch("/api/app/heartbeat", { method: "POST" }).catch(() => {}), 60000);
fetch("/api/app/heartbeat", { method: "POST" }).catch(() => {});

document.getElementById("app-quit-btn").addEventListener("click", async () => {
  if (!confirm("Quit Sensor Calibration Studio?\n\nYour work is autosaved and offered back next time; "
    + "use Export session to keep a copy you can share.")) return;
  try { await fetch("/api/app/quit", { method: "POST" }); } catch (_e) { /* already gone */ }
  document.body.innerHTML = '<div class="quit-screen"><h2>Sensor Calibration Studio has stopped.</h2>'
    + "<p>You can close this tab. Open the app again from your Applications folder.</p></div>";
});

bootModes()
  .then(() => sessionReady)
  .then(offerRestore)
  .catch((err) => toast(err.message, "error"));
