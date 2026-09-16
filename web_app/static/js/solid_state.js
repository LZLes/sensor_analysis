// Solid-State mode — ports macos_app/ui/modes/solid_state_view.py's tab
// flow (Import -> Time Series & Windows -> Calibration Results -> Export)
// to plain fetch()/DOM calls against /api/solid_state/*.

const SS_API = "/api/solid_state";
const SS_CPDF_COLUMNS = ["Label", "Concentration", "t_start", "t_end", "avg_duration", "Reading_mV"];

let ssState = null;
let ssActiveFilename = null;
let ssChannelsDraft = [];
let ssCpdfDraft = [];

function ssActiveIndex() {
  if (!ssState) return -1;
  return ssState.files.findIndex((f) => f.filename === ssActiveFilename);
}

function ssActiveFile() {
  const idx = ssActiveIndex();
  return idx >= 0 ? ssState.files[idx] : null;
}

async function ssRefresh(newState) {
  ssState = newState || (await apiCall(`${SS_API}/state`));
  if (!ssState.files.find((f) => f.filename === ssActiveFilename)) {
    ssActiveFilename = ssState.files.length ? ssState.files[0].filename : null;
  }
  document.getElementById("ss-conc-unit").value = ssState.conc_unit;
  document.getElementById("ss-signal-unit").value = ssState.signal_unit;
  document.getElementById("ss-smooth-method").value = ssState.smooth_method;
  document.getElementById("ss-smooth-window").value = ssState.smooth_window;
  document.getElementById("ss-smooth-polyorder").value = ssState.smooth_polyorder;

  ssRenderFilesList();
  ssRenderDatasetSelect();
  ssRenderChannelChecklist();
  ssRenderChannelAssignment();
  ssRenderCalTable();
  ssRenderAutodetectChannelSelect();
  await ssRenderTimeseries();
}

function ssRenderFilesList() {
  const ul = document.getElementById("ss-files-list");
  ul.innerHTML = "";
  ssState.files.forEach((f) => {
    const li = document.createElement("li");
    li.textContent = `${f.filename} — ${f.channels.length} channel${f.channels.length !== 1 ? "s" : ""}`;
    ul.appendChild(li);
  });
}

function ssRenderDatasetSelect() {
  const sel = document.getElementById("ss-dataset-select");
  sel.innerHTML = "";
  ssState.files.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f.filename;
    opt.textContent = f.filename;
    if (f.filename === ssActiveFilename) opt.selected = true;
    sel.appendChild(opt);
  });
  sel.onchange = () => {
    ssActiveFilename = sel.value;
    ssRenderChannelAssignment();
    ssRenderCalTable();
    ssRenderAutodetectChannelSelect();
  };
}

function ssRenderChannelChecklist() {
  const div = document.getElementById("ss-channel-checklist");
  const previouslyChecked = new Set(
    Array.from(div.querySelectorAll("input:checked")).map((cb) => cb.value)
  );
  const isFirstRender = div.childElementCount === 0;
  div.innerHTML = "";
  ssState.channel_labels.forEach((label) => {
    const wrap = document.createElement("label");
    wrap.className = "checklist-item";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = label;
    cb.checked = isFirstRender ? true : previouslyChecked.has(label);
    cb.addEventListener("change", ssRenderTimeseries);
    wrap.appendChild(cb);
    wrap.appendChild(document.createTextNode(" " + label));
    div.appendChild(wrap);
  });
}

function ssVisibleChannels() {
  return Array.from(document.querySelectorAll("#ss-channel-checklist input:checked")).map((cb) => cb.value);
}

function ssRenderChannelAssignment() {
  const container = document.getElementById("ss-channel-assignment");
  container.innerHTML = "";
  const frec = ssActiveFile();
  if (!frec) return;
  ssChannelsDraft = frec.channels.map((c) => ({ ...c }));
  ssChannelsDraft.forEach((ch, ci) => {
    const row = document.createElement("div");
    row.className = "channel-row";
    const nameInp = document.createElement("input");
    nameInp.type = "text";
    nameInp.value = ch.name;
    nameInp.addEventListener("change", () => { ssChannelsDraft[ci].name = nameInp.value; });
    const tcSel = ssColumnSelect(frec.columns, ch.tc, (v) => { ssChannelsDraft[ci].tc = v; });
    const icSel = ssColumnSelect(frec.columns, ch.ic, (v) => { ssChannelsDraft[ci].ic = v; });
    row.append(labeled("Name", nameInp), labeled("Time col", tcSel), labeled("Potential col", icSel));
    container.appendChild(row);
  });
}

function ssColumnSelect(columns, current, onChange) {
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

function labeled(text, el) {
  const wrap = document.createElement("label");
  wrap.appendChild(document.createTextNode(text + " "));
  wrap.appendChild(el);
  return wrap;
}

document.getElementById("ss-apply-channels-btn").addEventListener("click", async () => {
  const idx = ssActiveIndex();
  if (idx < 0) return;
  const keepFilename = ssActiveFilename;
  const newState = await apiPostJson(`${SS_API}/files/${idx}/channels`, { channels: ssChannelsDraft });
  ssActiveFilename = keepFilename;
  await ssRefresh(newState);
});

function ssRenderCalTable() {
  const container = document.getElementById("ss-cal-table");
  container.innerHTML = "";
  const frec = ssActiveFile();
  ssCpdfDraft = frec ? frec.cpdf.map((r) => ({ ...r })) : [];
  const table = document.createElement("table");
  table.className = "editable-table";
  const thead = document.createElement("tr");
  SS_CPDF_COLUMNS.forEach((c) => { const th = document.createElement("th"); th.textContent = c; thead.appendChild(th); });
  thead.appendChild(document.createElement("th"));
  table.appendChild(thead);

  ssCpdfDraft.forEach((row, ri) => {
    const tr = document.createElement("tr");
    SS_CPDF_COLUMNS.forEach((c) => {
      const td = document.createElement("td");
      const inp = document.createElement("input");
      inp.type = "text";
      inp.value = row[c] === null || row[c] === undefined ? "" : row[c];
      inp.addEventListener("change", () => {
        ssCpdfDraft[ri][c] = ssCoerceCell(c, inp.value);
        ssCommitCalTable();
      });
      td.appendChild(inp);
      tr.appendChild(td);
    });
    const rmTd = document.createElement("td");
    const rmBtn = document.createElement("button");
    rmBtn.textContent = "−";
    rmBtn.addEventListener("click", () => {
      ssCpdfDraft.splice(ri, 1);
      ssCommitCalTable();
    });
    rmTd.appendChild(rmBtn);
    tr.appendChild(rmTd);
    table.appendChild(tr);
  });
  container.appendChild(table);

  const addBtn = document.createElement("button");
  addBtn.textContent = "+ Row";
  addBtn.addEventListener("click", () => {
    ssCpdfDraft.push({ Label: "New", Concentration: 1.0, t_start: 0.0, t_end: 60.0, avg_duration: null, Reading_mV: null });
    ssCommitCalTable();
  });
  container.appendChild(addBtn);
}

function ssCoerceCell(column, text) {
  if (column === "Label") return text;
  if (text === "") return null;
  const num = parseFloat(text);
  return Number.isNaN(num) ? text : num;
}

async function ssCommitCalTable() {
  const idx = ssActiveIndex();
  if (idx < 0) return;
  const keepFilename = ssActiveFilename;
  const newState = await apiPostJson(`${SS_API}/files/${idx}/table`, { rows: ssCpdfDraft });
  ssActiveFilename = keepFilename;
  await ssRefresh(newState);
}

function ssRenderAutodetectChannelSelect() {
  const sel = document.getElementById("ss-autodetect-channel");
  sel.innerHTML = "";
  const frec = ssActiveFile();
  (frec ? frec.channels : []).forEach((ch) => {
    const opt = document.createElement("option");
    opt.value = ch.name;
    opt.textContent = ch.name;
    sel.appendChild(opt);
  });
}

document.getElementById("ss-autodetect-btn").addEventListener("click", async () => {
  const idx = ssActiveIndex();
  if (idx < 0) return;
  try {
    const result = await apiPostJson(`${SS_API}/files/${idx}/autodetect`, {
      channel: document.getElementById("ss-autodetect-channel").value,
      sensitivity: parseFloat(document.getElementById("ss-autodetect-sensitivity").value),
      min_gap: parseFloat(document.getElementById("ss-autodetect-mingap").value),
      max_steps: parseInt(document.getElementById("ss-autodetect-maxsteps").value, 10) || 0,
    });
    const resultEl = document.getElementById("ss-autodetect-result");
    resultEl.textContent = result.edges.length
      ? `Detected times (s): ${result.edges.map((e) => e.toFixed(4)).join(", ")}`
      : "No clear step transitions found — try lowering sensitivity.";
    await ssRenderTimeseries();
  } catch (e) {
    document.getElementById("ss-autodetect-result").textContent = "Error: " + e.message;
  }
});

document.getElementById("ss-autodetect-apply-btn").addEventListener("click", async () => {
  const idx = ssActiveIndex();
  if (idx < 0) return;
  const keepFilename = ssActiveFilename;
  try {
    const newState = await apiPostJson(`${SS_API}/files/${idx}/autodetect/apply`, {
      channel: document.getElementById("ss-autodetect-channel").value,
    });
    ssActiveFilename = keepFilename;
    await ssRefresh(newState);
  } catch (e) {
    document.getElementById("ss-autodetect-result").textContent = "Error: " + e.message;
  }
});

async function ssRenderTimeseries() {
  if (!ssState || !ssState.files.length) {
    document.getElementById("ss-plot").innerHTML = "";
    return;
  }
  const fig = await apiPostJson(`${SS_API}/timeseries`, {
    visible: ssVisibleChannels(),
    smooth_method: document.getElementById("ss-smooth-method").value,
    smooth_window: parseInt(document.getElementById("ss-smooth-window").value, 10),
    smooth_polyorder: parseInt(document.getElementById("ss-smooth-polyorder").value, 10),
    y_auto: true,
  });
  Plotly.newPlot("ss-plot", fig.data, fig.layout, { responsive: true });
}

["ss-smooth-method", "ss-smooth-window", "ss-smooth-polyorder"].forEach((id) => {
  document.getElementById(id).addEventListener("change", ssRenderTimeseries);
});

// -- Import: sample / browse / drag-and-drop / units ---------------------------
document.getElementById("ss-sample-btn").addEventListener("click", async () => {
  try {
    const newState = await apiCall(`${SS_API}/files/sample`, { method: "POST" });
    document.getElementById("ss-status").textContent = "Sample data loaded.";
    await ssRefresh(newState);
  } catch (e) {
    document.getElementById("ss-status").textContent = "Error: " + e.message;
  }
});

async function ssUploadFiles(fileList) {
  const form = new FormData();
  Array.from(fileList).forEach((f) => form.append("files", f));
  try {
    const newState = await apiCall(`${SS_API}/files`, { method: "POST", body: form });
    document.getElementById("ss-status").textContent = `${fileList.length} file(s) loaded.`;
    await ssRefresh(newState);
  } catch (e) {
    document.getElementById("ss-status").textContent = "Error: " + e.message;
  }
}

document.getElementById("ss-file-input").addEventListener("change", (e) => {
  if (e.target.files.length) ssUploadFiles(e.target.files);
  e.target.value = "";
});

const ssDropzone = document.getElementById("ss-dropzone");
["dragenter", "dragover"].forEach((evt) => ssDropzone.addEventListener(evt, (e) => {
  e.preventDefault();
  ssDropzone.classList.add("drag-active");
}));
["dragleave", "drop"].forEach((evt) => ssDropzone.addEventListener(evt, (e) => {
  e.preventDefault();
  ssDropzone.classList.remove("drag-active");
}));
ssDropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) ssUploadFiles(e.dataTransfer.files);
});

["ss-conc-unit", "ss-signal-unit"].forEach((id) => {
  document.getElementById(id).addEventListener("change", async () => {
    const newState = await apiPostJson(`${SS_API}/units`, {
      conc_unit: document.getElementById("ss-conc-unit").value,
      signal_unit: document.getElementById("ss-signal-unit").value,
    });
    ssState = newState;
  });
});

// -- Calibration Results ----------------------------------------------------------
document.getElementById("ss-compute-btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("ss-cal-status");
  try {
    const result = await apiPostJson(`${SS_API}/compute`, {
      selected: ssVisibleChannels(),
      ion_charge: parseInt(document.getElementById("ss-ion-charge").value, 10),
      lab_temp: parseFloat(document.getElementById("ss-lab-temp").value),
    });
    statusEl.textContent = result.warnings.length ? result.warnings.join(" | ") : "Calibration computed — results below.";
    if (result.figure) {
      Plotly.newPlot("ss-cal-plot", result.figure.data, result.figure.layout, { responsive: true });
    }
    ssRenderStatsTable(result.stats);
  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
  }
});

function ssRenderStatsTable(rows) {
  const table = document.getElementById("ss-stats-table");
  table.innerHTML = "";
  if (!rows.length) return;
  const columns = Object.keys(rows[0]);
  const thead = document.createElement("tr");
  columns.forEach((c) => { const th = document.createElement("th"); th.textContent = c; thead.appendChild(th); });
  table.appendChild(thead);
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    columns.forEach((c) => { const td = document.createElement("td"); td.textContent = row[c]; tr.appendChild(td); });
    table.appendChild(tr);
  });
}

// -- Export -------------------------------------------------------------------------
function ssExportOptions() {
  const width = parseFloat(document.getElementById("ss-export-width").value);
  const height = parseFloat(document.getElementById("ss-export-height").value);
  return {
    fmt: document.getElementById("ss-export-fmt").value,
    dpi: parseInt(document.getElementById("ss-export-dpi").value, 10),
    style: document.getElementById("ss-export-style").value,
    figsize: (width > 0 && height > 0) ? [width, height] : null,
  };
}

document.getElementById("ss-export-curve-btn").addEventListener("click", () => {
  downloadFromResponse(apiCall(`${SS_API}/export/curve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(ssExportOptions()),
  }));
});

document.getElementById("ss-export-ts-btn").addEventListener("click", () => {
  downloadFromResponse(apiCall(`${SS_API}/export/timeseries`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...ssExportOptions(), visible: ssVisibleChannels() }),
  }));
});

document.getElementById("ss-export-csv-btn").addEventListener("click", () => {
  downloadFromResponse(apiCall(`${SS_API}/export/csv`));
});

ssRefresh();
