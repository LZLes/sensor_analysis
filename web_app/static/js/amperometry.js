// Amperometry mode — same shape as solid_state.js, plus baseline
// subtraction (Baseline checkbox column), segmented-linear fit settings,
// channel-average trace, and the effective-concentration dilution
// calculator.

const AMP_API = "/api/amperometry";
const AMP_CPDF_COLUMNS = ["Label", "Concentration", "Spike Vol", "Stock Conc", "t_start", "t_end", "avg_duration", "Baseline"];

let ampState = null;
let ampActiveFilename = null;
let ampChannelsDraft = [];
let ampCpdfDraft = [];

function ampActiveIndex() {
  if (!ampState) return -1;
  return ampState.files.findIndex((f) => f.filename === ampActiveFilename);
}

function ampActiveFile() {
  const idx = ampActiveIndex();
  return idx >= 0 ? ampState.files[idx] : null;
}

async function ampRefresh(newState) {
  ampState = newState || (await apiCall(`${AMP_API}/state`));
  if (!ampState.files.find((f) => f.filename === ampActiveFilename)) {
    ampActiveFilename = ampState.files.length ? ampState.files[0].filename : null;
  }
  document.getElementById("amp-conc-unit").value = ampState.conc_unit;
  document.getElementById("amp-signal-unit").value = ampState.signal_unit;
  document.getElementById("amp-smooth-method").value = ampState.smooth_method;
  document.getElementById("amp-smooth-window").value = ampState.smooth_window;
  document.getElementById("amp-smooth-polyorder").value = ampState.smooth_polyorder;
  document.getElementById("amp-initial-volume").value = ampState.initial_volume;
  document.getElementById("amp-vol-unit").value = ampState.vol_unit;

  ampRenderFilesList();
  ampRenderDatasetSelect();
  ampRenderChannelChecklist();
  ampRenderChannelAssignment();
  ampRenderCalTable();
  ampRenderAutodetectChannelSelect();
  await ampRenderTimeseries();
}

function ampRenderFilesList() {
  const ul = document.getElementById("amp-files-list");
  ul.innerHTML = "";
  ampState.files.forEach((f) => {
    const li = document.createElement("li");
    li.textContent = `${f.filename} — ${f.channels.length} channel${f.channels.length !== 1 ? "s" : ""}`;
    ul.appendChild(li);
  });
}

function ampRenderDatasetSelect() {
  const sel = document.getElementById("amp-dataset-select");
  sel.innerHTML = "";
  ampState.files.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f.filename;
    opt.textContent = f.filename;
    if (f.filename === ampActiveFilename) opt.selected = true;
    sel.appendChild(opt);
  });
  sel.onchange = () => {
    ampActiveFilename = sel.value;
    ampRenderChannelAssignment();
    ampRenderCalTable();
    ampRenderAutodetectChannelSelect();
  };
}

function ampRenderChannelChecklist() {
  const div = document.getElementById("amp-channel-checklist");
  const previouslyChecked = new Set(
    Array.from(div.querySelectorAll("input:checked")).map((cb) => cb.value)
  );
  const isFirstRender = div.childElementCount === 0;
  div.innerHTML = "";
  ampState.channel_labels.forEach((label) => {
    const wrap = document.createElement("label");
    wrap.className = "checklist-item";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = label;
    cb.checked = isFirstRender ? true : previouslyChecked.has(label);
    cb.addEventListener("change", ampRenderTimeseries);
    wrap.appendChild(cb);
    wrap.appendChild(document.createTextNode(" " + label));
    div.appendChild(wrap);
  });
}

function ampVisibleChannels() {
  return Array.from(document.querySelectorAll("#amp-channel-checklist input:checked")).map((cb) => cb.value);
}

function ampRenderChannelAssignment() {
  const container = document.getElementById("amp-channel-assignment");
  container.innerHTML = "";
  const frec = ampActiveFile();
  if (!frec) return;
  ampChannelsDraft = frec.channels.map((c) => ({ ...c }));
  ampChannelsDraft.forEach((ch, ci) => {
    const row = document.createElement("div");
    row.className = "channel-row";
    const nameInp = document.createElement("input");
    nameInp.type = "text";
    nameInp.value = ch.name;
    nameInp.addEventListener("change", () => { ampChannelsDraft[ci].name = nameInp.value; });
    const tcSel = ampColumnSelect(frec.columns, ch.tc, (v) => { ampChannelsDraft[ci].tc = v; });
    const icSel = ampColumnSelect(frec.columns, ch.ic, (v) => { ampChannelsDraft[ci].ic = v; });
    row.append(labeled("Name", nameInp), labeled("Time col", tcSel), labeled("Current col", icSel));
    container.appendChild(row);
  });
}

function ampColumnSelect(columns, current, onChange) {
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

document.getElementById("amp-apply-channels-btn").addEventListener("click", async () => {
  const idx = ampActiveIndex();
  if (idx < 0) return;
  const keepFilename = ampActiveFilename;
  const newState = await apiPostJson(`${AMP_API}/files/${idx}/channels`, { channels: ampChannelsDraft });
  ampActiveFilename = keepFilename;
  await ampRefresh(newState);
});

function ampRenderCalTable() {
  const container = document.getElementById("amp-cal-table");
  container.innerHTML = "";
  const frec = ampActiveFile();
  ampCpdfDraft = frec ? frec.cpdf.map((r) => ({ ...r })) : [];
  const table = document.createElement("table");
  table.className = "editable-table";
  const thead = document.createElement("tr");
  AMP_CPDF_COLUMNS.forEach((c) => { const th = document.createElement("th"); th.textContent = c; thead.appendChild(th); });
  thead.appendChild(document.createElement("th"));
  table.appendChild(thead);

  ampCpdfDraft.forEach((row, ri) => {
    const tr = document.createElement("tr");
    AMP_CPDF_COLUMNS.forEach((c) => {
      const td = document.createElement("td");
      if (c === "Baseline") {
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = !!row[c];
        cb.addEventListener("change", () => {
          ampCpdfDraft[ri][c] = cb.checked;
          ampCommitCalTable();
        });
        td.appendChild(cb);
      } else {
        const inp = document.createElement("input");
        inp.type = "text";
        inp.value = row[c] === null || row[c] === undefined ? "" : row[c];
        inp.addEventListener("change", () => {
          ampCpdfDraft[ri][c] = ampCoerceCell(c, inp.value);
          ampCommitCalTable();
        });
        td.appendChild(inp);
      }
      tr.appendChild(td);
    });
    const rmTd = document.createElement("td");
    const rmBtn = document.createElement("button");
    rmBtn.textContent = "−";
    rmBtn.addEventListener("click", () => {
      ampCpdfDraft.splice(ri, 1);
      ampCommitCalTable();
    });
    rmTd.appendChild(rmBtn);
    tr.appendChild(rmTd);
    table.appendChild(tr);
  });
  container.appendChild(table);

  const addBtn = document.createElement("button");
  addBtn.textContent = "+ Row";
  addBtn.addEventListener("click", () => {
    ampCpdfDraft.push({
      Label: "New", Concentration: 0.0, "Spike Vol": null, "Stock Conc": null,
      t_start: 0.0, t_end: 60.0, avg_duration: null, Baseline: false,
    });
    ampCommitCalTable();
  });
  container.appendChild(addBtn);
}

function ampCoerceCell(column, text) {
  if (column === "Label") return text;
  if (text === "") return null;
  const num = parseFloat(text);
  return Number.isNaN(num) ? text : num;
}

async function ampCommitCalTable() {
  const idx = ampActiveIndex();
  if (idx < 0) return;
  const keepFilename = ampActiveFilename;
  const newState = await apiPostJson(`${AMP_API}/files/${idx}/table`, { rows: ampCpdfDraft });
  ampActiveFilename = keepFilename;
  await ampRefresh(newState);
}

function ampRenderAutodetectChannelSelect() {
  const sel = document.getElementById("amp-autodetect-channel");
  sel.innerHTML = "";
  const frec = ampActiveFile();
  (frec ? frec.channels : []).forEach((ch) => {
    const opt = document.createElement("option");
    opt.value = ch.name;
    opt.textContent = ch.name;
    sel.appendChild(opt);
  });
}

document.getElementById("amp-autodetect-btn").addEventListener("click", async () => {
  const idx = ampActiveIndex();
  if (idx < 0) return;
  try {
    const result = await apiPostJson(`${AMP_API}/files/${idx}/autodetect`, {
      channel: document.getElementById("amp-autodetect-channel").value,
      sensitivity: parseFloat(document.getElementById("amp-autodetect-sensitivity").value),
      min_gap: parseFloat(document.getElementById("amp-autodetect-mingap").value),
      max_steps: parseInt(document.getElementById("amp-autodetect-maxsteps").value, 10) || 0,
    });
    const resultEl = document.getElementById("amp-autodetect-result");
    resultEl.textContent = result.edges.length
      ? `Detected times (s): ${result.edges.map((e) => e.toFixed(4)).join(", ")}`
      : "No clear step transitions found — try lowering sensitivity.";
    await ampRenderTimeseries();
  } catch (e) {
    document.getElementById("amp-autodetect-result").textContent = "Error: " + e.message;
  }
});

document.getElementById("amp-autodetect-apply-btn").addEventListener("click", async () => {
  const idx = ampActiveIndex();
  if (idx < 0) return;
  const keepFilename = ampActiveFilename;
  try {
    const newState = await apiPostJson(`${AMP_API}/files/${idx}/autodetect/apply`, {
      channel: document.getElementById("amp-autodetect-channel").value,
      include_baseline: document.getElementById("amp-autodetect-baseline").checked,
    });
    ampActiveFilename = keepFilename;
    await ampRefresh(newState);
  } catch (e) {
    document.getElementById("amp-autodetect-result").textContent = "Error: " + e.message;
  }
});

async function ampRenderTimeseries() {
  if (!ampState || !ampState.files.length) {
    document.getElementById("amp-plot").innerHTML = "";
    return;
  }
  const fig = await apiPostJson(`${AMP_API}/timeseries`, {
    visible: ampVisibleChannels(),
    smooth_method: document.getElementById("amp-smooth-method").value,
    smooth_window: parseInt(document.getElementById("amp-smooth-window").value, 10),
    smooth_polyorder: parseInt(document.getElementById("amp-smooth-polyorder").value, 10),
    y_auto: true,
  });
  Plotly.newPlot("amp-plot", fig.data, fig.layout, { responsive: true });
}

["amp-smooth-method", "amp-smooth-window", "amp-smooth-polyorder"].forEach((id) => {
  document.getElementById(id).addEventListener("change", ampRenderTimeseries);
});

document.getElementById("amp-fit-type").addEventListener("change", (e) => {
  document.getElementById("amp-n-seg").disabled = e.target.value !== "Segmented Linear";
});

document.getElementById("amp-effconc-btn").addEventListener("click", async () => {
  const idx = ampActiveIndex();
  if (idx < 0) return;
  const keepFilename = ampActiveFilename;
  const newState = await apiPostJson(`${AMP_API}/files/${idx}/effective-concentration`, {
    initial_volume: parseFloat(document.getElementById("amp-initial-volume").value),
    vol_unit: document.getElementById("amp-vol-unit").value,
  });
  ampActiveFilename = keepFilename;
  await ampRefresh(newState);
});

// -- Import: sample / browse / drag-and-drop / units ---------------------------
document.getElementById("amp-sample-btn").addEventListener("click", async () => {
  try {
    const newState = await apiCall(`${AMP_API}/files/sample`, { method: "POST" });
    document.getElementById("amp-status").textContent = "Sample data loaded.";
    await ampRefresh(newState);
  } catch (e) {
    document.getElementById("amp-status").textContent = "Error: " + e.message;
  }
});

async function ampUploadFiles(fileList) {
  const form = new FormData();
  Array.from(fileList).forEach((f) => form.append("files", f));
  try {
    const newState = await apiCall(`${AMP_API}/files`, { method: "POST", body: form });
    document.getElementById("amp-status").textContent = `${fileList.length} file(s) loaded.`;
    await ampRefresh(newState);
  } catch (e) {
    document.getElementById("amp-status").textContent = "Error: " + e.message;
  }
}

document.getElementById("amp-file-input").addEventListener("change", (e) => {
  if (e.target.files.length) ampUploadFiles(e.target.files);
  e.target.value = "";
});

const ampDropzone = document.getElementById("amp-dropzone");
["dragenter", "dragover"].forEach((evt) => ampDropzone.addEventListener(evt, (e) => {
  e.preventDefault();
  ampDropzone.classList.add("drag-active");
}));
["dragleave", "drop"].forEach((evt) => ampDropzone.addEventListener(evt, (e) => {
  e.preventDefault();
  ampDropzone.classList.remove("drag-active");
}));
ampDropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) ampUploadFiles(e.dataTransfer.files);
});

["amp-conc-unit", "amp-signal-unit"].forEach((id) => {
  document.getElementById(id).addEventListener("change", async () => {
    const newState = await apiPostJson(`${AMP_API}/units`, {
      conc_unit: document.getElementById("amp-conc-unit").value,
      signal_unit: document.getElementById("amp-signal-unit").value,
    });
    ampState = newState;
  });
});

// -- Calibration Results ----------------------------------------------------------
document.getElementById("amp-compute-btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("amp-cal-status");
  try {
    const result = await apiPostJson(`${AMP_API}/compute`, {
      selected: ampVisibleChannels(),
      fit_type: document.getElementById("amp-fit-type").value,
      n_seg: parseInt(document.getElementById("amp-n-seg").value, 10),
      show_avg: document.getElementById("amp-show-avg").checked,
    });
    statusEl.textContent = result.warnings.length ? result.warnings.join(" | ") : "Calibration computed — results below.";
    if (result.figure) {
      Plotly.newPlot("amp-cal-plot", result.figure.data, result.figure.layout, { responsive: true });
    }
    ampRenderStatsTable(result.stats);
  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
  }
});

function ampRenderStatsTable(rows) {
  const table = document.getElementById("amp-stats-table");
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
function ampExportOptions() {
  const width = parseFloat(document.getElementById("amp-export-width").value);
  const height = parseFloat(document.getElementById("amp-export-height").value);
  return {
    fmt: document.getElementById("amp-export-fmt").value,
    dpi: parseInt(document.getElementById("amp-export-dpi").value, 10),
    style: document.getElementById("amp-export-style").value,
    figsize: (width > 0 && height > 0) ? [width, height] : null,
  };
}

document.getElementById("amp-export-curve-btn").addEventListener("click", () => {
  downloadFromResponse(apiCall(`${AMP_API}/export/curve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(ampExportOptions()),
  }));
});

document.getElementById("amp-export-ts-btn").addEventListener("click", () => {
  downloadFromResponse(apiCall(`${AMP_API}/export/timeseries`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...ampExportOptions(), visible: ampVisibleChannels() }),
  }));
});

document.getElementById("amp-export-csv-btn").addEventListener("click", () => {
  downloadFromResponse(apiCall(`${AMP_API}/export/csv`));
});

// -- Compare Files ----------------------------------------------------------------
document.getElementById("amp-compare-btn").addEventListener("click", async () => {
  const result = await apiCall(`${AMP_API}/comparison`);
  if (result.figure) {
    Plotly.newPlot("amp-compare-plot", result.figure.data, result.figure.layout, { responsive: true });
  } else {
    document.getElementById("amp-compare-plot").innerHTML = "";
  }
  ampRenderComparisonTable(result.stats);
});

function ampRenderComparisonTable(rows) {
  const table = document.getElementById("amp-compare-table");
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

ampRefresh();
