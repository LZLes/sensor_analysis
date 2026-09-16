// Cyclic Voltammetry mode — self-contained (matches
// macos_app/ui/modes/cyclic_voltammetry_view.py's own reasoning for not
// sharing the Import/Time-Series panels the other two modes use).

const CV_API = "/api/cv";

let cvState = null;
let cvChannelRunIndex = 0;
let cvChannelsDraft = [];

async function cvRefresh(newState) {
  cvState = newState || (await apiCall(`${CV_API}/state`));
  document.getElementById("cv-volt-unit").value = cvState.volt_unit;
  document.getElementById("cv-cur-unit").value = cvState.cur_unit;
  document.getElementById("cv-sr-unit").value = cvState.sr_unit;

  cvRenderRunsTable();
  cvRenderChannelRunSelect();
  await cvRenderChannelAssignment();
  cvRenderSrChecklist();
  cvRenderChChecklist();
  cvRenderPeakChannelsChecklist();
  cvRenderScanRateChannelsChecklist();
  await cvRenderPlot();
}

function cvRenderRunsTable() {
  const table = document.getElementById("cv-runs-table");
  table.innerHTML = "";
  const head = document.createElement("tr");
  ["Scan rate", "File", "Rows", "Channels", "Peaks"].forEach((c) => {
    const th = document.createElement("th");
    th.textContent = c;
    head.appendChild(th);
  });
  table.appendChild(head);
  cvState.runs.forEach((r, ri) => {
    const tr = document.createElement("tr");
    const srTd = document.createElement("td");
    const srInp = document.createElement("input");
    srInp.type = "number";
    srInp.step = "0.001";
    srInp.value = r.scan_rate;
    srInp.addEventListener("change", async () => {
      const newState = await apiPostJson(`${CV_API}/runs/${ri}/scan-rate`, { scan_rate: parseFloat(srInp.value) });
      await cvRefresh(newState);
    });
    srTd.appendChild(srInp);
    tr.appendChild(srTd);
    [r.filename, r.n_rows, r.channels.map((c) => c.name).join(", "), r.n_peaks].forEach((v) => {
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });
}

function cvRenderChannelRunSelect() {
  const sel = document.getElementById("cv-channel-run-select");
  const prev = sel.value;
  sel.innerHTML = "";
  cvState.runs.forEach((r, ri) => {
    const opt = document.createElement("option");
    opt.value = ri;
    opt.textContent = r.filename;
    sel.appendChild(opt);
  });
  if (prev !== "" && Number(prev) < cvState.runs.length) sel.value = prev;
  cvChannelRunIndex = sel.value === "" ? 0 : parseInt(sel.value, 10);
  sel.onchange = async () => {
    cvChannelRunIndex = parseInt(sel.value, 10);
    await cvRenderChannelAssignment();
  };
}

async function cvRenderChannelAssignment() {
  const container = document.getElementById("cv-channel-assignment");
  container.innerHTML = "";
  if (!cvState.runs.length) return;
  const { columns, channels } = await apiCall(`${CV_API}/runs/${cvChannelRunIndex}/columns`);
  cvChannelsDraft = channels.map((c) => ({ name: c.name, vc: c.vc, ic_cols: c.is_avg ? [] : [c.ic] }));
  cvChannelsDraft.forEach((ch, ci) => {
    const row = document.createElement("div");
    row.className = "channel-row";
    const nameInp = document.createElement("input");
    nameInp.type = "text";
    nameInp.value = ch.name;
    nameInp.addEventListener("change", () => { cvChannelsDraft[ci].name = nameInp.value; });
    const vcSel = document.createElement("select");
    columns.forEach((col) => {
      const opt = document.createElement("option");
      opt.value = col;
      opt.textContent = col;
      if (col === ch.vc) opt.selected = true;
      vcSel.appendChild(opt);
    });
    vcSel.addEventListener("change", () => { cvChannelsDraft[ci].vc = vcSel.value; });
    const icSel = document.createElement("select");
    icSel.multiple = true;
    icSel.size = Math.min(4, columns.length);
    columns.forEach((col) => {
      const opt = document.createElement("option");
      opt.value = col;
      opt.textContent = col;
      if (ch.ic_cols.includes(col)) opt.selected = true;
      icSel.appendChild(opt);
    });
    icSel.addEventListener("change", () => {
      cvChannelsDraft[ci].ic_cols = Array.from(icSel.selectedOptions).map((o) => o.value);
    });
    row.append(labeled("Name", nameInp), labeled("Voltage col", vcSel), labeled("Current col(s)", icSel));
    container.appendChild(row);
  });
}

document.getElementById("cv-apply-channels-btn").addEventListener("click", async () => {
  const newState = await apiPostJson(`${CV_API}/runs/${cvChannelRunIndex}/channels`, { channels: cvChannelsDraft });
  await cvRefresh(newState);
});

function cvChecklistLabels(containerId, labels, onChange) {
  const div = document.getElementById(containerId);
  const previouslyChecked = new Set(Array.from(div.querySelectorAll("input:checked")).map((cb) => cb.value));
  const isFirstRender = div.childElementCount === 0;
  div.innerHTML = "";
  labels.forEach((label) => {
    const wrap = document.createElement("label");
    wrap.className = "checklist-item";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = label;
    cb.checked = isFirstRender ? true : previouslyChecked.has(label);
    cb.addEventListener("change", onChange);
    wrap.appendChild(cb);
    wrap.appendChild(document.createTextNode(" " + label));
    div.appendChild(wrap);
  });
}

function cvChecked(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} input:checked`)).map((cb) => cb.value);
}

function cvRenderSrChecklist() {
  cvChecklistLabels("cv-sr-checklist", cvState.scan_rate_labels, cvRenderPlot);
}

function cvRenderChChecklist() {
  cvChecklistLabels("cv-ch-checklist", cvState.channel_names, cvRenderPlot);
}

function cvRenderPeakChannelsChecklist() {
  cvChecklistLabels("cv-peak-channels", cvState.channel_names, () => {});
}

function cvRenderScanRateChannelsChecklist() {
  cvChecklistLabels("cv-sranalysis-channels", cvState.channel_names, cvRenderScanRateAnalysis);
}

async function cvRenderPlot() {
  if (!cvState || !cvState.runs.length) {
    document.getElementById("cv-plot").innerHTML = "";
    return;
  }
  const fig = await apiPostJson(`${CV_API}/plot`, {
    visible_srs: cvChecked("cv-sr-checklist"),
    visible_chs: cvChecked("cv-ch-checklist"),
  });
  Plotly.newPlot("cv-plot", fig.data, fig.layout, { responsive: true });
}

// -- Import -------------------------------------------------------------------------
async function cvUploadFiles(fileList) {
  const form = new FormData();
  Array.from(fileList).forEach((f) => form.append("files", f));
  form.append("fmt", document.getElementById("cv-fmt").value);
  form.append("delimiter", document.getElementById("cv-delim").value);
  form.append("skip_rows", document.getElementById("cv-skip-rows").value || "0");
  try {
    const newState = await apiCall(`${CV_API}/files`, { method: "POST", body: form });
    document.getElementById("cv-status").textContent = newState.errors
      ? `Loaded with errors: ${newState.errors.join("; ")}`
      : `${fileList.length} file(s) loaded.`;
    await cvRefresh(newState);
  } catch (e) {
    document.getElementById("cv-status").textContent = "Error: " + e.message;
  }
}

document.getElementById("cv-file-input").addEventListener("change", (e) => {
  if (e.target.files.length) cvUploadFiles(e.target.files);
  e.target.value = "";
});

const cvDropzone = document.getElementById("cv-dropzone");
["dragenter", "dragover"].forEach((evt) => cvDropzone.addEventListener(evt, (e) => {
  e.preventDefault();
  cvDropzone.classList.add("drag-active");
}));
["dragleave", "drop"].forEach((evt) => cvDropzone.addEventListener(evt, (e) => {
  e.preventDefault();
  cvDropzone.classList.remove("drag-active");
}));
cvDropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) cvUploadFiles(e.dataTransfer.files);
});

["cv-volt-unit", "cv-cur-unit", "cv-sr-unit"].forEach((id) => {
  document.getElementById(id).addEventListener("change", async () => {
    cvState = await apiPostJson(`${CV_API}/units`, {
      volt_unit: document.getElementById("cv-volt-unit").value,
      cur_unit: document.getElementById("cv-cur-unit").value,
      sr_unit: document.getElementById("cv-sr-unit").value,
    });
  });
});

// -- Peak detection -------------------------------------------------------------------
document.getElementById("cv-find-peaks-btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("cv-peak-status");
  const channels = cvChecked("cv-peak-channels");
  if (!channels.length) {
    statusEl.textContent = "Select at least one channel.";
    return;
  }
  const width = document.getElementById("cv-width").value;
  const height = document.getElementById("cv-height").value;
  try {
    const result = await apiPostJson(`${CV_API}/peaks/detect`, {
      channels,
      prominence: parseFloat(document.getElementById("cv-prom").value),
      distance: parseInt(document.getElementById("cv-dist").value, 10),
      width: width === "" ? null : parseInt(width, 10),
      height: height === "" ? null : parseFloat(height),
      visible_srs: cvChecked("cv-sr-checklist"),
      visible_chs: cvChecked("cv-ch-checklist"),
    });
    statusEl.textContent = `Peaks found in ${result.state.runs.length} run(s).`;
    Plotly.newPlot("cv-plot", result.figure.data, result.figure.layout, { responsive: true });
    cvRenderPeaksTable(result.peaks);
    cvState = result.state;
    cvRenderRunsTable();
    cvRenderScanRateChannelsChecklist();
  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
  }
});

function cvRenderPeaksTable(rows) {
  const table = document.getElementById("cv-peaks-table");
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

// -- Scan Rate Analysis ----------------------------------------------------------------
async function cvRenderScanRateAnalysis() {
  const channels = cvChecked("cv-sranalysis-channels");
  if (!channels.length) return;
  const result = await apiPostJson(`${CV_API}/scan-rate`, { channels });
  const figs = result.figures;
  if (figs.ip_nu) Plotly.newPlot("cv-plot-ip-nu", figs.ip_nu.data, figs.ip_nu.layout, { responsive: true });
  if (figs.ip_sqrt_nu) Plotly.newPlot("cv-plot-ip-sqrt", figs.ip_sqrt_nu.data, figs.ip_sqrt_nu.layout, { responsive: true });
  if (figs.ep_nu) Plotly.newPlot("cv-plot-ep-nu", figs.ep_nu.data, figs.ep_nu.layout, { responsive: true });
  if (figs.delta_ep) Plotly.newPlot("cv-plot-dep-nu", figs.delta_ep.data, figs.delta_ep.layout, { responsive: true });
  cvRenderStatsTable("cv-sr-stats-table", result.stats);
}

function cvRenderStatsTable(tableId, rows) {
  const table = document.getElementById(tableId);
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

document.querySelector('#mode-cyclic_voltammetry .tab-btn[data-tab="scanrate"]').addEventListener("click", cvRenderScanRateAnalysis);

// -- Export -------------------------------------------------------------------------
function cvExportOptions() {
  const width = parseFloat(document.getElementById("cv-export-width").value);
  const height = parseFloat(document.getElementById("cv-export-height").value);
  return {
    fmt: document.getElementById("cv-export-fmt").value,
    dpi: parseInt(document.getElementById("cv-export-dpi").value, 10),
    style: document.getElementById("cv-export-style").value,
    figsize: (width > 0 && height > 0) ? [width, height] : null,
  };
}

document.getElementById("cv-export-plot-btn").addEventListener("click", () => {
  downloadFromResponse(apiCall(`${CV_API}/export/plot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...cvExportOptions(), visible_srs: cvChecked("cv-sr-checklist"), visible_chs: cvChecked("cv-ch-checklist") }),
  }));
});

function cvExportScanRatePlot(kind) {
  downloadFromResponse(apiCall(`${CV_API}/export/scan-rate-plot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...cvExportOptions(), kind, channels: cvChecked("cv-sranalysis-channels") }),
  }));
}

document.getElementById("cv-export-ipnu-btn").addEventListener("click", () => cvExportScanRatePlot("ip_nu"));
document.getElementById("cv-export-ipsqrt-btn").addEventListener("click", () => cvExportScanRatePlot("ip_sqrt_nu"));
document.getElementById("cv-export-epnu-btn").addEventListener("click", () => cvExportScanRatePlot("ep_nu"));
document.getElementById("cv-export-depnu-btn").addEventListener("click", () => cvExportScanRatePlot("delta_ep"));

document.getElementById("cv-export-peaks-csv-btn").addEventListener("click", () => {
  downloadFromResponse(apiCall(`${CV_API}/export/peaks-csv`));
});

document.getElementById("cv-export-sr-csv-btn").addEventListener("click", () => {
  downloadFromResponse(apiCall(`${CV_API}/export/scan-rate-csv`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channels: cvChecked("cv-sranalysis-channels") }),
  }));
});

document.getElementById("cv-export-raw-btn").addEventListener("click", () => {
  downloadFromResponse(apiCall(`${CV_API}/export/raw`));
});

cvRefresh();
