// Shared shell: mode switcher + per-mode tab switching. Each mode's own
// logic lives in its own file (solid_state.js, later amperometry.js /
// cyclic_voltammetry.js) and self-initializes on DOMContentLoaded.

document.querySelectorAll(".mode-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    document.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".mode-page").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`mode-${btn.dataset.mode}`).classList.add("active");
  });
});

function initTabs(scopeEl) {
  scopeEl.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      scopeEl.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      scopeEl.querySelectorAll(".tab-page").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      scopeEl.querySelector(`.tab-page[data-tab-page="${btn.dataset.tab}"]`).classList.add("active");
    });
  });
}

document.querySelectorAll(".mode-page").forEach(initTabs);

// Generic fetch helper shared by every mode module: JSON in, JSON out,
// throws with the backend's detail message on a non-2xx response.
async function apiCall(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
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

// -- Tier 2 persistence: Export/Import Session JSON, global (spans every mode) --
document.getElementById("session-export-btn").addEventListener("click", () => {
  // Not apiCall(): that helper auto-parses an application/json response
  // body as data rather than treating it as a downloadable file, which is
  // exactly wrong here since the session bundle's payload IS JSON.
  downloadFromResponse(fetch("/api/session/export").then((res) => {
    if (!res.ok) throw new Error(res.statusText);
    return res;
  }));
});

document.getElementById("session-import-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const statusEl = document.getElementById("session-status");
  const form = new FormData();
  form.append("file", file);
  try {
    const result = await apiCall("/api/session/import", { method: "POST", body: form });
    statusEl.style.color = "#9fd9a8";
    statusEl.textContent = `Session imported: ${result.amp_files} amperometry file(s), ${result.solid_files} solid-state file(s), ${result.cv_runs} CV run(s).`;
    if (typeof ssRefresh === "function") await ssRefresh();
    if (typeof ampRefresh === "function") await ampRefresh();
    if (typeof cvRefresh === "function") await cvRefresh();
  } catch (err) {
    statusEl.style.color = "#f5a3a3";
    statusEl.textContent = "Import error: " + err.message;
  }
});

async function downloadFromResponse(promise) {
  const res = await promise;
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
  URL.revokeObjectURL(url);
}
