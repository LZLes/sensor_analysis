// Cyclic Voltammetry mode — self-contained (doesn't use trace_mode.js):
// runs are keyed by scan rate, and peak detection plays the role the
// calibration windows play in the other two modes.

const CV_API = "/api/cyclic_voltammetry";

let cvState = null;
let cvChannelsDraft = [];
let cvColumns = [];

const cvSig = (v) => (typeof v === "number" ? Number(v.toPrecision(4)) : v);

async function cvRefresh(newState) {
  cvState = newState || (await apiCall(`${CV_API}/state`));
  document.getElementById("cv-volt-unit").value = cvState.volt_unit;
  document.getElementById("cv-cur-unit").value = cvState.cur_unit;
  document.getElementById("cv-sr-unit").value = cvState.sr_unit;
  document.querySelectorAll("#mode-cyclic_voltammetry .needs-data").forEach((el) => {
    el.classList.toggle("is-empty", cvState.runs.length === 0);
  });
  document.getElementById("cv-clear-btn").hidden = cvState.runs.length === 0;

  cvRenderRunsTable();
  cvRenderChannelRunSelect();
  await cvRenderChannelAssignment();
  renderChecklist("cv-sr-checklist", cvState.scan_rate_labels, cvRenderPlot);
  renderChecklist("cv-ch-checklist", cvState.channel_names, cvRenderPlot);
  renderChecklist("cv-peak-channels", cvState.channel_names, () => {});
  renderChecklist("cv-sranalysis-channels", cvState.channel_names, cvRenderScanRateAnalysis);
  await cvRenderPlot();
  cvRenderPeaksTable((await apiCall(`${CV_API}/peaks`)).peaks);
}

function cvRenderRunsTable() {
  const table = document.getElementById("cv-runs-table");
  table.innerHTML = "";
  if (!cvState.runs.length) return;
  const head = document.createElement("tr");
  ["Scan rate", "File", "Rows", "Channels", "Peaks", ""].forEach((c) => {
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
    srInp.step = "any";
    srInp.min = "0";
    srInp.value = r.scan_rate;
    srInp.title = `Scan rate (${cvState.sr_unit}) — guessed from the file name; edit if wrong`;
    srInp.addEventListener("change", async () => {
      try {
        await cvRefresh(await apiPostJson(`${CV_API}/runs/${ri}/scan-rate`, { scan_rate: parseFloat(srInp.value) }));
      } catch (err) {
        toast(err.message, "error");
        srInp.value = r.scan_rate;
      }
    });
    srTd.append(srInp, document.createTextNode(" " + cvState.sr_unit));
    tr.appendChild(srTd);
    [r.filename, r.n_rows, r.channels.map((c) => c.name).join(", "), r.n_peaks].forEach((v) => {
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    });
    const rmTd = document.createElement("td");
    const rm = document.createElement("button");
    rm.className = "icon-btn";
    rm.title = "Remove this run";
    rm.textContent = "×";
    rm.addEventListener("click", async () => {
      try { await cvRefresh(await apiDelete(`${CV_API}/runs/${ri}`)); } catch (err) { toast(err.message, "error"); }
    });
    rmTd.appendChild(rm);
    tr.appendChild(rmTd);
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
    opt.textContent = `${r.label} — ${r.filename}`;
    sel.appendChild(opt);
  });
  if (prev !== "" && Number(prev) < cvState.runs.length) sel.value = prev;
  sel.onchange = () => cvRenderChannelAssignment().catch((err) => toast(err.message, "error"));
}

function cvSelectedRunIndex() {
  const v = document.getElementById("cv-channel-run-select").value;
  return v === "" ? -1 : parseInt(v, 10);
}

async function cvRenderChannelAssignment(draft) {
  const container = document.getElementById("cv-channel-assignment");
  container.innerHTML = "";
  const ri = cvSelectedRunIndex();
  if (ri < 0) return;
  if (!draft) {
    const { columns, channels } = await apiCall(`${CV_API}/runs/${ri}/columns`);
    cvColumns = columns;
    draft = channels.map((c) => ({ name: c.name, vc: c.vc, ic_cols: [...c.ic_cols] }));
  }
  cvChannelsDraft = draft;
  cvChannelsDraft.forEach((ch, ci) => {
    const row = document.createElement("div");
    row.className = "channel-row";
    const nameInp = document.createElement("input");
    nameInp.type = "text";
    nameInp.value = ch.name;
    nameInp.addEventListener("change", () => { cvChannelsDraft[ci].name = nameInp.value; });
    const vcSel = columnSelect(cvColumns, ch.vc, (v) => { cvChannelsDraft[ci].vc = v; });
    const icSel = document.createElement("select");
    icSel.multiple = true;
    icSel.size = Math.min(4, cvColumns.length);
    icSel.title = "Select several columns (⌘/Ctrl-click) to average them into one trace";
    cvColumns.forEach((col) => {
      const opt = document.createElement("option");
      opt.value = col;
      opt.textContent = col;
      opt.selected = ch.ic_cols.includes(col);
      icSel.appendChild(opt);
    });
    icSel.addEventListener("change", () => {
      cvChannelsDraft[ci].ic_cols = Array.from(icSel.selectedOptions).map((o) => o.value);
    });
    const rm = document.createElement("button");
    rm.className = "icon-btn";
    rm.textContent = "×";
    rm.title = "Remove channel";
    rm.addEventListener("click", () => { cvChannelsDraft.splice(ci, 1); cvRenderChannelAssignment(cvChannelsDraft); });
    row.append(labeled("Name", nameInp), labeled("Potential", vcSel), labeled("Current(s)", icSel), rm);
    container.appendChild(row);
  });
  const add = document.createElement("button");
  add.textContent = "+ Channel";
  add.addEventListener("click", () => {
    cvChannelsDraft.push({ name: `CH${cvChannelsDraft.length + 1}`, vc: cvColumns[0], ic_cols: cvColumns.slice(1, 2) });
    cvRenderChannelAssignment(cvChannelsDraft);
  });
  container.appendChild(add);
}

onClick("cv-apply-channels-btn", async () => {
  const ri = cvSelectedRunIndex();
  if (ri < 0) return;
  await cvRefresh(await apiPostJson(`${CV_API}/runs/${ri}/channels`, { channels: cvChannelsDraft }));
  toast("Channel assignment applied.", "success");
});

let cvPlotSeq = 0;
async function cvRenderPlot() {
  if (!cvState || !cvState.runs.length) {
    drawPlot("cv-plot", null);
    return;
  }
  const seq = ++cvPlotSeq;
  try {
    const fig = await apiPostJson(`${CV_API}/plot`, {
      visible_srs: checkedValues("cv-sr-checklist"),
      visible_chs: checkedValues("cv-ch-checklist"),
    });
    if (seq === cvPlotSeq) drawPlot("cv-plot", fig);
  } catch (err) {
    toast("Plot failed: " + err.message, "error");
  }
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
    if (newState.errors) toast(`Some files could not be loaded: ${newState.errors.join("; ")}`, "error");
    else toast(`${fileList.length} file(s) loaded — check the scan rates in the table.`, "success");
    await cvRefresh(newState);
  } catch (err) {
    toast(err.message, "error");
  }
}
setupFileInput("cv-file-input", cvUploadFiles);
setupDropzone("cv-dropzone", cvUploadFiles);

onClick("cv-clear-btn", async () => {
  if (!confirm("Remove all loaded CV runs?")) return;
  await cvRefresh(await apiDelete(`${CV_API}/runs`));
});

["cv-volt-unit", "cv-cur-unit", "cv-sr-unit"].forEach((id) => {
  document.getElementById(id).addEventListener("change", async () => {
    try {
      await cvRefresh(await apiPostJson(`${CV_API}/units`, {
        volt_unit: document.getElementById("cv-volt-unit").value,
        cur_unit: document.getElementById("cv-cur-unit").value,
        sr_unit: document.getElementById("cv-sr-unit").value,
      }));
    } catch (err) {
      toast(err.message, "error");
    }
  });
});

// -- Peak detection -------------------------------------------------------------------
onClick("cv-find-peaks-btn", async () => {
  const channels = checkedValues("cv-peak-channels");
  if (!channels.length) throw new Error("Select at least one channel to analyse.");
  const width = document.getElementById("cv-width").value;
  const height = document.getElementById("cv-height").value;
  const result = await apiPostJson(`${CV_API}/peaks/detect`, {
    channels,
    prominence: parseFloat(document.getElementById("cv-prom").value),
    distance: parseInt(document.getElementById("cv-dist").value, 10) || 1,
    width: width === "" ? null : parseInt(width, 10),
    height: height === "" ? null : parseFloat(height),
    visible_srs: checkedValues("cv-sr-checklist"),
    visible_chs: checkedValues("cv-ch-checklist"),
  });
  setStatus("cv-peak-status", `${result.peaks.length} peak(s) found across ${result.state.runs.length} run(s).`);
  drawPlot("cv-plot", result.figure);
  cvRenderPeaksTable(result.peaks);
  cvState = result.state;
  cvRenderRunsTable();
});

function cvRenderPeaksTable(rows) {
  renderTable("cv-peaks-table", rows.map((r) => ({ ...r, Ep: cvSig(r.Ep), Ip: cvSig(r.Ip) })));
}

// -- Scan Rate Analysis ----------------------------------------------------------------
async function cvRenderScanRateAnalysis() {
  const channels = checkedValues("cv-sranalysis-channels");
  const ids = { ip_nu: "cv-plot-ip-nu", ip_sqrt_nu: "cv-plot-ip-sqrt", ep_nu: "cv-plot-ep-nu", delta_ep: "cv-plot-dep-nu" };
  const result = channels.length ? await apiPostJson(`${CV_API}/scan-rate`, { channels }) : { figures: {}, stats: [] };
  const anyPeaks = cvState && cvState.runs.some((r) => r.n_peaks > 0);
  setStatus("cv-sr-status", anyPeaks ? "" : "Run peak detection in ② Plot & Peaks first.");
  Object.entries(ids).forEach(([key, id]) => drawPlot(id, anyPeaks ? result.figures[key] : null));
  renderTable("cv-sr-stats-table", result.stats);
}

document.getElementById("mode-cyclic_voltammetry").addEventListener("tabshown", (e) => {
  if (e.detail.tab === "scanrate") cvRenderScanRateAnalysis().catch((err) => toast(err.message, "error"));
});

// -- Export -------------------------------------------------------------------------
const cvPlotBody = () => ({ ...exportOptions("cv"), visible_srs: checkedValues("cv-sr-checklist"), visible_chs: checkedValues("cv-ch-checklist") });
const cvSrBody = (kind) => ({ ...exportOptions("cv"), kind, channels: checkedValues("cv-sranalysis-channels") });

document.getElementById("cv-export-plot-btn").addEventListener("click", () => download(`${CV_API}/export/plot`, cvPlotBody()));
document.getElementById("cv-preview-plot-btn").addEventListener("click", () => previewExport("cv-preview-img", `${CV_API}/export/plot`, cvPlotBody()));
[["ipnu", "ip_nu"], ["ipsqrt", "ip_sqrt_nu"], ["epnu", "ep_nu"], ["depnu", "delta_ep"]].forEach(([btn, kind]) => {
  document.getElementById(`cv-export-${btn}-btn`).addEventListener("click", () => download(`${CV_API}/export/scan-rate-plot`, cvSrBody(kind)));
});
document.getElementById("cv-preview-sr-btn").addEventListener("click", () => {
  previewExport("cv-preview-img", `${CV_API}/export/scan-rate-plot`, cvSrBody(document.getElementById("cv-preview-sr-kind").value));
});
document.getElementById("cv-export-peaks-csv-btn").addEventListener("click", () => download(`${CV_API}/export/peaks-csv`));
document.getElementById("cv-export-sr-csv-btn").addEventListener("click", () => {
  download(`${CV_API}/export/scan-rate-csv`, { channels: checkedValues("cv-sranalysis-channels") });
});
document.getElementById("cv-export-raw-btn").addEventListener("click", () => download(`${CV_API}/export/raw`));

registerMode("cyclic_voltammetry", { refresh: cvRefresh });
sessionReady.then(() => cvRefresh()).catch((err) => toast(err.message, "error"));
