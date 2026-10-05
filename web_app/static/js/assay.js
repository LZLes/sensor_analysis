// Assay (96-well microplate) mode: ① Import → ② Standards → ③ Standard
// Curve → ④ Results & Export, matching the Streamlit app's Assay tabs.

const ASSAY_API = "/api/assay";
const ASSAY_ROWS = "ABCDEFGH".split("");

let assayState = null;
let assayStdDraft = [];
let assaySampleDraft = [];
let assayLayoutDirty = false;
let assayLastWellInput = null;   // the well cell a plate-map click fills in

// Unapplied edits on ② Standards survive refreshes caused by other actions
// (units change, new plate…); only Apply/Discard/session import reset them.
async function assayRefresh(newState, opts = {}) {
  assayState = newState || (await apiCall(`${ASSAY_API}/state`));
  document.getElementById("assay-sig-unit").value = assayState.sig_unit;
  document.getElementById("assay-conc-unit").value = assayState.conc_unit;
  const hasPlate = assayState.plate !== null;
  document.querySelectorAll("#mode-assay .needs-data").forEach((el) => el.classList.toggle("is-empty", !hasPlate));
  document.getElementById("assay-clear-btn").hidden = !hasPlate;
  setStatus("assay-plate-status", hasPlate ? `${assayState.n_wells} wells with data.` : "");

  assayRenderGrid();
  drawPlot("assay-plate-map", assayState.plate_figure, { displayModeBar: false });
  drawPlot("assay-layout-map", assayState.plate_figure, { displayModeBar: false });
  assayBindPlateClick();
  if (opts.resetLayout || !assayLayoutDirty) {
    assayStdDraft = assayState.std_rows.map((r) => ({ ...r }));
    assaySampleDraft = assayState.sample_rows.map((r) => ({ ...r }));
    assaySetDirty(false);
  }
  assayRenderStdTable();
  assayRenderSampleTable();
  assayRenderProblems();

  const fitSel = document.getElementById("assay-fit-type");
  if (!fitSel.options.length) {
    assayState.fit_types.forEach((f) => fitSel.add(new Option(f, f)));
  }
  if (assayState.fit_label) fitSel.value = assayState.fit_label;
  if (assayState.has_result) await assayShowCurve();
  else assayClearCurve();
}

// -- ① Import ------------------------------------------------------------------------
function assayRenderGrid() {
  const table = document.getElementById("assay-grid");
  table.innerHTML = "";
  const head = document.createElement("tr");
  head.appendChild(document.createElement("th"));
  for (let c = 1; c <= 12; c++) {
    const th = document.createElement("th");
    th.textContent = c;
    head.appendChild(th);
  }
  table.appendChild(head);
  ASSAY_ROWS.forEach((row, ri) => {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = row;
    tr.appendChild(th);
    for (let ci = 0; ci < 12; ci++) {
      const td = document.createElement("td");
      const inp = document.createElement("input");
      inp.type = "text";
      inp.inputMode = "decimal";
      inp.dataset.r = ri;
      inp.dataset.c = ci;
      const v = assayState.plate ? assayState.plate[ri][ci] : null;
      inp.value = v === null ? "" : v;
      inp.addEventListener("input", () => { document.getElementById("assay-grid-apply-btn").disabled = false; });
      td.appendChild(inp);
      tr.appendChild(td);
    }
    table.appendChild(tr);
  });
  document.getElementById("assay-grid-apply-btn").disabled = true;
}

onClick("assay-grid-apply-btn", async () => {
  const grid = ASSAY_ROWS.map(() => Array(12).fill(null));
  const bad = [];
  document.querySelectorAll("#assay-grid input").forEach((inp) => {
    const t = inp.value.trim().replace(",", ".");
    if (t === "") return;
    const n = Number(t);
    if (Number.isFinite(n)) grid[inp.dataset.r][inp.dataset.c] = n;
    else bad.push(`${ASSAY_ROWS[inp.dataset.r]}${Number(inp.dataset.c) + 1}`);
  });
  if (bad.length) throw new Error(`Not a number in well(s): ${bad.join(", ")}`);
  await assayRefresh(await apiPostJson(`${ASSAY_API}/plate`, { grid }));
  toast("Plate values updated.", "success");
});

onClick("assay-sample-btn", async () => {
  await assayRefresh(await apiCall(`${ASSAY_API}/plate/sample`, { method: "POST" }), { resetLayout: true });
  toast("Sample plate loaded with a matching standards layout — continue in ③ Standard Curve.", "success");
});

onClick("assay-clear-btn", async () => {
  if (!confirm("Clear the plate data (the standards layout is kept)?")) return;
  await assayRefresh(await apiDelete(`${ASSAY_API}/plate`));
});

async function assayUploadPlate(fileList) {
  const form = new FormData();
  form.append("file", fileList[0]);
  try {
    await assayRefresh(await apiCall(`${ASSAY_API}/plate/upload`, { method: "POST", body: form }));
    toast(`Loaded ${fileList[0].name || "plate"}.`, "success");
  } catch (err) {
    toast(err.message, "error");
  }
}
setupFileInput("assay-file-input", assayUploadPlate);
setupDropzone("assay-dropzone", assayUploadPlate);

// Paste a block copied from Excel / the plate reader software: rows of
// tab-separated values, with or without A–H row labels and a 1–12 header.
onClick("assay-paste-btn", async () => {
  const text = document.getElementById("assay-paste").value.trim();
  if (!text) throw new Error("Paste the plate values into the box first.");
  let lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (!/^\s*[A-Ha-h][\t,; ]/.test(lines[0]) && !/^\s*[A-Ha-h][\t,; ]/.test(lines[1] || "")) {
    // No row letters: drop a numeric 1..12 header if present, then label rows A–H.
    if (/^\s*1[\t,; ]+2[\t,; ]+3/.test(lines[0])) lines = lines.slice(1);
    lines = lines.slice(0, 8).map((l, i) => `${ASSAY_ROWS[i]}\t${l}`);
  }
  const blob = new File([lines.join("\n")], "pasted_plate.txt", { type: "text/plain" });
  await assayUploadPlate([blob]);
  document.getElementById("assay-paste").value = "";
});

["assay-sig-unit", "assay-conc-unit"].forEach((id) => {
  document.getElementById(id).addEventListener("change", async () => {
    try {
      await assayRefresh(await apiPostJson(`${ASSAY_API}/units`, {
        sig_unit: document.getElementById("assay-sig-unit").value,
        conc_unit: document.getElementById("assay-conc-unit").value,
      }));
    } catch (err) {
      toast(err.message, "error");
    }
  });
});

// -- ② Standards ----------------------------------------------------------------------
function assaySetDirty(dirty) {
  assayLayoutDirty = dirty;
  document.getElementById("assay-layout-dirty").hidden = !dirty;
}

function assayEditableTable(containerId, rows, columns, onChange) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  const table = document.createElement("table");
  table.className = "editable-table";
  const head = document.createElement("tr");
  columns.forEach(([, title]) => { const th = document.createElement("th"); th.textContent = title; head.appendChild(th); });
  head.appendChild(document.createElement("th"));
  table.appendChild(head);
  rows.forEach((row, ri) => {
    const tr = document.createElement("tr");
    if (containerId === "assay-std-table" && ri === 0) tr.className = "row-blank";
    columns.forEach(([key, , isWell]) => {
      const td = document.createElement("td");
      const inp = document.createElement("input");
      inp.type = "text";
      inp.value = row[key] === null || row[key] === undefined ? "" : row[key];
      if (isWell) {
        inp.classList.add("well-input");
        inp.placeholder = "e.g. A1";
        inp.addEventListener("focus", () => { assayLastWellInput = { input: inp, rows, ri, key }; });
      }
      inp.addEventListener("input", () => { row[key] = inp.value; assaySetDirty(true); });
      td.appendChild(inp);
      tr.appendChild(td);
    });
    const rmTd = document.createElement("td");
    const rm = document.createElement("button");
    rm.className = "icon-btn";
    rm.textContent = "×";
    rm.title = "Delete row";
    rm.addEventListener("click", () => { rows.splice(ri, 1); assaySetDirty(true); onChange(); });
    rmTd.appendChild(rm);
    tr.appendChild(rmTd);
    table.appendChild(tr);
  });
  container.appendChild(table);
}

function assayRenderStdTable() {
  const cu = assayState ? assayState.conc_unit : "";
  assayEditableTable("assay-std-table", assayStdDraft, [
    ["Label", "Label"], ["Conc", `Conc (${cu})`], ["S1", "Set 1 well", true], ["S2", "Set 2 well", true], ["S3", "Set 3 well", true],
  ], assayRenderStdTable);
}

function assayRenderSampleTable() {
  assayEditableTable("assay-sample-table", assaySampleDraft, [["Well", "Well", true], ["Label", "Label"]], assayRenderSampleTable);
}

function assayRenderProblems() {
  const ul = document.getElementById("assay-layout-problems");
  ul.innerHTML = "";
  (assayState.layout_problems || []).forEach((p) => {
    const li = document.createElement("li");
    li.textContent = p;
    ul.appendChild(li);
  });
}

document.getElementById("assay-std-add-btn").addEventListener("click", () => {
  const n = assayStdDraft.length + 1;
  assayStdDraft.push({ Label: `Std ${n}`, Conc: null, S1: "", S2: "", S3: "" });
  assaySetDirty(true);
  assayRenderStdTable();
});

document.getElementById("assay-sample-add-btn").addEventListener("click", () => {
  assaySampleDraft.push({ Well: "", Label: "" });
  assaySetDirty(true);
  assayRenderSampleTable();
});

onClick("assay-layout-apply-btn", async () => {
  const std = assayStdDraft.map((r) => {
    const t = String(r.Conc ?? "").trim().replace(",", ".");
    if (t !== "" && !Number.isFinite(Number(t))) throw new Error(`Concentration "${r.Conc}" (${r.Label}) isn't a number.`);
    return { ...r, Conc: t === "" ? null : Number(t) };
  });
  await assayRefresh(await apiPostJson(`${ASSAY_API}/layout`, { std_rows: std, sample_rows: assaySampleDraft }),
    { resetLayout: true });
  toast(assayState.layout_problems.length ? "Layout saved — but see the warnings below." : "Layout saved.",
    assayState.layout_problems.length ? "error" : "success");
});

onClick("assay-layout-reset-btn", async () => assayRefresh(undefined, { resetLayout: true }));

// Clicking a well on the layout preview fills the last-focused well cell.
function assayBindPlateClick() {
  const el = document.getElementById("assay-layout-map");
  if (!assayState.plate) { delete el.dataset.bound; return; }  // purged: handlers are gone
  if (el.dataset.bound || !el.on) return;
  el.dataset.bound = "1";
  el.on("plotly_click", (ev) => {
    const pt = ev.points && ev.points[0];
    if (!pt || pt.curveNumber !== 0 || !assayLastWellInput) {
      if (!assayLastWellInput) toast("Click a well cell in the tables first, then click a well on the map.");
      return;
    }
    const well = `${ASSAY_ROWS[7 - pt.y]}${pt.x}`;
    const { input, rows, ri, key } = assayLastWellInput;
    input.value = well;
    rows[ri][key] = well;
    assaySetDirty(true);
    // Move focus to the next well cell so consecutive clicks fill a row.
    const wells = Array.from(document.querySelectorAll("#assay-std-table .well-input, #assay-sample-table .well-input"));
    const next = wells[wells.indexOf(input) + 1];
    if (next) next.focus();
  });
}

// -- ③ Standard Curve ------------------------------------------------------------------
function assayClearCurve() {
  drawPlot("assay-curve-plot", null);
  renderTable("assay-standards-table", []);
  setStatus("assay-equation", "");
  setStatus("assay-curve-status", "");
}

function assayShowCurvePayload(res) {
  drawPlot("assay-curve-plot", res.figure);
  renderTable("assay-standards-table", res.standards, { rowClass: (r) => (String(r.Label).endsWith("(blank)") ? "row-blank" : "") });
  setStatus("assay-equation", res.figure ? `${res.equation}    •    blank mean = ${Number(res.blank_mean).toPrecision(4)} ${assayState.sig_unit}` : "");
}

async function assayShowCurve() {
  const res = await apiCall(`${ASSAY_API}/curve?show_reps=${document.getElementById("assay-show-reps").checked}`);
  if (res.figure) assayShowCurvePayload(res);
  else assayClearCurve();
}

onClick("assay-compute-btn", async () => {
  if (assayLayoutDirty) throw new Error("You have unsaved changes on ② Standards — click Apply layout first.");
  const res = await apiPostJson(`${ASSAY_API}/compute`, {
    fit_type: document.getElementById("assay-fit-type").value,
    show_reps: document.getElementById("assay-show-reps").checked,
  });
  assayState.has_result = true;
  assayShowCurvePayload(res);
  setStatus("assay-curve-status", res.warnings.join("  •  "), "warn");
  toast("Standard curve computed — sample concentrations are in ④ Results.", "success");
});

document.getElementById("assay-show-reps").addEventListener("change", () => {
  if (assayState && assayState.has_result) assayShowCurve().catch((err) => toast(err.message, "error"));
});

// -- ④ Results & Export -------------------------------------------------------------------
async function assayRenderResults() {
  if (!assayState || !assayState.has_result) {
    setStatus("assay-results-status", "Compute the standard curve in ③ first.");
    renderTable("assay-results-table", []);
    drawPlot("assay-results-map", null);
    return;
  }
  const r = await apiCall(`${ASSAY_API}/results`);
  setStatus("assay-results-status", r.summary);
  const rows = r.rows.map((row) => ({
    Well: row.Well, Label: row.Label,
    [`Signal (${r.sig_unit})`]: row.Signal, [`ΔSignal (${r.sig_unit})`]: row["ΔSignal"],
    [`Conc (${r.conc_unit})`]: row.Conc, Flag: row.Flag,
  }));
  renderTable("assay-results-table", rows, { rowClass: (row) => (row.Flag ? "row-flag" : "") });
  drawPlot("assay-results-map", r.figure, { displayModeBar: false });
}

document.getElementById("mode-assay").addEventListener("tabshown", (e) => {
  if (e.detail.tab === "results") assayRenderResults().catch((err) => toast(err.message, "error"));
});

const assayCurveBody = () => ({ ...exportOptions("assay"), show_reps: document.getElementById("assay-show-reps").checked });
document.getElementById("assay-export-results-btn").addEventListener("click", () => download(`${ASSAY_API}/export/results-csv`));
document.getElementById("assay-export-standards-btn").addEventListener("click", () => download(`${ASSAY_API}/export/standards-csv`));
document.getElementById("assay-export-curve-btn").addEventListener("click", () => download(`${ASSAY_API}/export/curve`, assayCurveBody()));
document.getElementById("assay-preview-curve-btn").addEventListener("click", () => previewExport("assay-preview-img", `${ASSAY_API}/export/curve`, assayCurveBody()));

sessionReady.then(() => assayRefresh()).catch((err) => toast(err.message, "error"));
