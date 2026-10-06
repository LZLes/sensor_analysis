// Assay (96-well microplate) mode: ① Import → ② Plate Layout → ③ Standard
// Curve → ④ Results → ⑤ Normalise & Export. ①–④ match the Streamlit app's
// Assay tabs; the selectable plate, subject/timepoint labelling, excluded
// wells, several plates, templates, per-sample summary and normalisation are
// web-only (see web_app/api/assay.py and core/analysis/assay_layout.py).

const ASSAY_API = "/api/assay";
const ASSAY_ROWS = "ABCDEFGH".split("");

let assayState = null;
let assayStdDraft = [];
let assaySampleDraft = [];
let assayLayoutDirty = false;

// Plate selection (② Plate Layout)
const assaySel = new Set();
let assayAnchor = null;
let assayDrag = null;          // {base: Set} while the mouse button is down
let assayPreview = new Map();  // well -> {role, text, sub, subject} (unapplied assignment)
let assayAssignMode = "samples";

// Unapplied table edits on ② survive refreshes caused by other actions
// (units change, new plate…); only Apply/Discard/session import reset them.
async function assayRefresh(newState, opts = {}) {
  assayState = newState || (await apiCall(`${ASSAY_API}/state`));
  document.getElementById("assay-sig-unit").value = assayState.sig_unit;
  document.getElementById("assay-conc-unit").value = assayState.conc_unit;
  assayShowReadout(assayState.readout);
  const hasPlate = assayState.plate !== null;
  document.querySelectorAll("#mode-assay .needs-data").forEach((el) => el.classList.toggle("is-empty", !hasPlate));
  document.getElementById("assay-clear-btn").hidden = !hasPlate;
  setStatus("assay-plate-status", hasPlate ? `${assayState.n_wells} wells with data.` : "");

  assayRenderGrid();
  drawPlot("assay-plate-map", assayState.plate_figure, { displayModeBar: false });
  if (opts.resetLayout || !assayLayoutDirty) {
    assayStdDraft = assayState.std_rows.map((r) => ({ ...r }));
    assaySampleDraft = assayState.sample_rows.map((r) => ({ ...r }));
    assaySetDirty(false);
  }
  if (opts.resetLayout) assaySetNormDirty(false);  // the samples (rows) may have changed
  assayRenderStdTable();
  assayRenderSampleTable();
  assayRenderProblems();
  assayPaintCanvas();

  const fitSel = document.getElementById("assay-fit-type");
  if (!fitSel.options.length) {
    assayState.fit_types.forEach((f) => fitSel.add(new Option(f, f)));
  }
  if (assayState.fit_label) fitSel.value = assayState.fit_label;
  assayRenderPlates();
  assayRenderCurveSource();
  if (assayState.has_curve) await assayShowCurve();
  else assayClearCurve();
  const tab = assayActiveTab();
  if (tab === "results") await assayRenderResults();
  if (tab === "normalise" && opts.resetLayout) await assayRenderNormalise();
}

// -- Plates ------------------------------------------------------------------------------
function assayRenderPlates() {
  const host = document.getElementById("assay-plate-chips");
  host.innerHTML = "";
  const multi = assayState.plates.length > 1;
  assayState.plates.forEach((p) => {
    const chip = document.createElement("span");
    chip.className = `plate-chip${p.index === assayState.active ? " active" : ""}`;
    const name = document.createElement("button");
    name.className = "plate-chip-name";
    name.textContent = p.name;
    name.title = (p.has_data ? "" : "No data yet. ") + (p.source_name ? `Uses ${p.source_name}'s standard curve. ` : "")
      + "Click to select, double-click to rename.";
    if (!p.has_data) name.classList.add("no-data");
    name.addEventListener("click", () => assayPlateAction(() => apiPostJson(`${ASSAY_API}/plates/${p.index}/activate`, {})));
    name.addEventListener("dblclick", () => {
      const n = prompt("Rename plate", p.name);
      if (n && n.trim() && n.trim() !== p.name) {
        assayPlateAction(() => apiPostJson(`${ASSAY_API}/plates/${p.index}/rename`, { name: n.trim() }));
      }
    });
    chip.appendChild(name);
    if (multi) {
      const rm = document.createElement("button");
      rm.className = "icon-btn";
      rm.textContent = "×";
      rm.title = `Remove ${p.name}`;
      rm.addEventListener("click", () => {
        if (confirm(`Remove ${p.name} and its data? (Undo brings it back.)`)) {
          assayPlateAction(() => apiDelete(`${ASSAY_API}/plates/${p.index}`));
        }
      });
      chip.appendChild(rm);
    }
    host.appendChild(chip);
  });
  document.querySelector("#assay-plates-bar .plates-hint").hidden = !multi;
}

async function assayPlateAction(call) {
  try {
    if (assayLayoutDirty && !confirm("Discard your unsaved table edits on ② Plate Layout?")) return;
    assaySel.clear();
    assayPreview = new Map();
    await assayRefresh(await call(), { resetLayout: true });
  } catch (err) {
    toast(err.message, "error");
  }
}

document.getElementById("assay-plate-add-toggle").addEventListener("click", () => {
  document.getElementById("assay-plate-add-form").hidden = false;
  document.getElementById("assay-plate-add-toggle").hidden = true;
  const inp = document.getElementById("assay-plate-add-name");
  inp.value = `Plate ${assayState.plates.length + 1}`;
  inp.select();
});

function assayHideAddPlate() {
  document.getElementById("assay-plate-add-form").hidden = true;
  document.getElementById("assay-plate-add-toggle").hidden = false;
}

document.getElementById("assay-plate-add-cancel").addEventListener("click", assayHideAddPlate);

onClick("assay-plate-add-btn", async () => {
  await assayPlateAction(() => apiPostJson(`${ASSAY_API}/plates`, {
    name: document.getElementById("assay-plate-add-name").value,
    copy_layout: document.getElementById("assay-plate-add-copy").checked,
  }));
  assayHideAddPlate();
  showTab(document.getElementById("mode-assay"), "import");
  toast("Plate added — paste or load its data in ① Import.", "success");
});

function assayRenderCurveSource() {
  const row = document.getElementById("assay-curve-source-row");
  const others = assayState.plates.filter((p) => p.index !== assayState.active && !p.std_source);
  row.hidden = !others.length;
  const sel = document.getElementById("assay-curve-source");
  sel.innerHTML = "";
  sel.add(new Option("Its own standards", ""));
  others.forEach((p) => sel.add(new Option(`From ${p.name}`, p.id)));
  const me = assayState.plates[assayState.active];
  sel.value = (me && me.std_source) || "";
  document.getElementById("assay-compute-btn").disabled = !!(me && me.std_source);
}

document.getElementById("assay-curve-source").addEventListener("change", async (e) => {
  try {
    await assayRefresh(await apiPostJson(`${ASSAY_API}/plates/curve-source`, { source: e.target.value || null }));
  } catch (err) {
    toast(err.message, "error");
  }
});

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
  toast("Sample plate loaded with standards and 5 subjects × 4 timepoints labelled — see ② Plate Layout.", "success");
});

onClick("assay-clear-btn", async () => {
  if (!confirm("Clear the plate data (the layout is kept)?")) return;
  await assayRefresh(await apiDelete(`${ASSAY_API}/plate`));
});

async function assayUploadPlate(fileList) {
  const form = new FormData();
  form.append("file", fileList[0]);
  try {
    await assayRefresh(await apiCall(`${ASSAY_API}/plate/upload`, { method: "POST", body: form }));
    toast(`Loaded ${fileList[0].name || "plate"}. Next: label the wells in ② Plate Layout.`, "success");
  } catch (err) {
    toast(err.message, "error");
  }
}
setupFileInput("assay-file-input", assayUploadPlate);
setupDropzone("assay-dropzone", assayUploadPlate);

// A block copied from Excel / plate-reader software: rows of tab-separated
// values, with or without A–H row labels and a 1–12 header.
async function assayLoadPastedPlate(text) {
  text = text.trim();
  if (!text) throw new Error("Paste the plate values into the box first.");
  let lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (!/^\s*[A-Ha-h][\t,; ]/.test(lines[0]) && !/^\s*[A-Ha-h][\t,; ]/.test(lines[1] || "")) {
    // No row letters: drop a numeric 1..12 header if present, then label rows A–H.
    if (/^\s*1[\t,; ]+2[\t,; ]+3/.test(lines[0])) lines = lines.slice(1);
    lines = lines.slice(0, 8).map((l, i) => `${ASSAY_ROWS[i]}\t${l}`);
  }
  const blob = new File([lines.join("\n")], "pasted_plate.txt", { type: "text/plain" });
  await assayUploadPlate([blob]);
}

onClick("assay-paste-btn", async () => {
  await assayLoadPastedPlate(document.getElementById("assay-paste").value);
  document.getElementById("assay-paste").value = "";
});

// ⌘V anywhere on ① loads the clipboard as plate data; on ② it drops a
// pasted block into the layout-paste box for review (never applied blind).
function assayActiveTab() {
  const page = document.getElementById("mode-assay");
  if (!page.classList.contains("active")) return null;
  const btn = page.querySelector(".tab-btn.active");
  return btn ? btn.dataset.tab : null;
}

document.addEventListener("paste", (e) => {
  const tab = assayActiveTab();
  if (!tab || e.target.closest("input, textarea, select")) return;
  const text = (e.clipboardData || window.clipboardData).getData("text");
  if (!text || !/[\t\n]/.test(text.trim())) return;
  e.preventDefault();
  if (tab === "import") {
    assayLoadPastedPlate(text).catch((err) => toast(err.message, "error"));
  } else if (tab === "layout") {
    document.getElementById("assay-layout-paste").value = text;
    document.getElementById("assay-layout-paste").closest("details").open = true;
    toast("Layout pasted into the box. Check the separator, then click Apply pasted layout.");
  }
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

function assayShowReadout(r) {
  document.getElementById("assay-readout-type").value = r.type;
  document.getElementById("assay-readout-wl").value = r.wavelength || "";
  document.getElementById("assay-readout-ex").value = r.ex || "";
  document.getElementById("assay-readout-em").value = r.em || "";
  document.querySelectorAll("#mode-assay [data-readout]").forEach((el) => { el.hidden = el.dataset.readout !== r.type; });
}

["assay-readout-type", "assay-readout-wl", "assay-readout-ex", "assay-readout-em"].forEach((id) => {
  document.getElementById(id).addEventListener("change", async () => {
    try {
      await assayRefresh(await apiPostJson(`${ASSAY_API}/readout`, {
        type: document.getElementById("assay-readout-type").value,
        wavelength: document.getElementById("assay-readout-wl").value,
        ex: document.getElementById("assay-readout-ex").value,
        em: document.getElementById("assay-readout-em").value,
      }));
    } catch (err) {
      toast(err.message, "error");
    }
  });
});

// -- ② Plate Layout: selectable plate ------------------------------------------------------
const assayWellName = (r, c) => `${ASSAY_ROWS[r]}${c + 1}`;
const assayWellRC = (w) => [ASSAY_ROWS.indexOf(w[0]), Number(w.slice(1)) - 1];

function assayFmt(v) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "";
  const n = Number(v);
  return Math.abs(n) >= 1000 || (n !== 0 && Math.abs(n) < 0.01) ? n.toExponential(1) : String(Number(n.toPrecision(3)));
}

// Pastel colour per subject, by its position in the layout's subject order
// (subjects only in the unapplied preview go after). Hues skip the blank's
// orange and the standards' blue.
const ASSAY_SUBJECT_HUES = [150, 285, 345, 60, 175, 315, 100, 5, 255, 125, 75, 330];

function assaySubjectOrder() {
  const order = [...assayState.subjects];
  for (const p of assayPreview.values()) if (p.subject && !order.includes(p.subject)) order.push(p.subject);
  return order;
}

function assaySubjectColor(subject, order) {
  const idx = order.indexOf(subject);
  if (idx < 0) return "hsl(270, 30%, 86%)";
  const hue = ASSAY_SUBJECT_HUES[idx % ASSAY_SUBJECT_HUES.length];
  const light = 84 - 6 * (Math.floor(idx / ASSAY_SUBJECT_HUES.length) % 2);
  return `hsl(${hue}, 60%, ${light}%)`;
}

function assayBuildCanvas() {
  const canvas = document.getElementById("assay-plate-canvas");
  canvas.innerHTML = "";
  const corner = document.createElement("div");
  corner.className = "plate-head corner";
  corner.title = "Select all wells";
  corner.dataset.all = "1";
  canvas.appendChild(corner);
  for (let c = 0; c < 12; c++) {
    const h = document.createElement("div");
    h.className = "plate-head";
    h.textContent = c + 1;
    h.dataset.col = c;
    canvas.appendChild(h);
  }
  ASSAY_ROWS.forEach((row, r) => {
    const h = document.createElement("div");
    h.className = "plate-head";
    h.textContent = row;
    h.dataset.row = r;
    canvas.appendChild(h);
    for (let c = 0; c < 12; c++) {
      const w = document.createElement("div");
      w.className = "well";
      w.dataset.well = assayWellName(r, c);
      w.innerHTML = '<span class="w-main"></span><span class="w-sub"></span>';
      canvas.appendChild(w);
    }
  });
}

function assayPaintCanvas() {
  const canvas = document.getElementById("assay-plate-canvas");
  if (!canvas.children.length) assayBuildCanvas();
  const cu = assayState.conc_unit;
  const order = assaySubjectOrder();
  assayState.wells.forEach((cell) => {
    const el = canvas.querySelector(`[data-well="${cell.well}"]`);
    const prev = assayPreview.get(cell.well);
    const role = prev ? prev.role : cell.role;
    el.className = `well role-${role}${assaySel.has(cell.well) ? " selected" : ""}${prev ? " preview" : ""}`
      + `${cell.excluded ? " excluded" : ""}`;
    let main = "", sub = "";
    const sig = cell.value === null ? "no data" : `${assayFmt(cell.value)} ${assayState.sig_unit}`;
    let title = `${cell.well}: ${sig}`;
    if (prev) {
      main = prev.text;
      sub = prev.sub || "";
      title += `\nWill become: ${[prev.text, prev.sub].filter(Boolean).join(" · ")}`;
    } else if (role === "blank") {
      main = "Blank";
      sub = `S${cell.set}`;
      title += "\nBlank";
    } else if (role === "std") {
      main = assayFmt(cell.conc);
      sub = `S${cell.set}`;
      title += `\nStandard ${cell.label}: ${cell.conc} ${cu} (set ${cell.set})`;
    } else if (role === "sample") {
      main = cell.subject || cell.label;
      sub = cell.timepoint || "";
      title += `\nSample ${cell.label}`;
    } else if (role === "data") {
      main = assayFmt(cell.value);
    }
    el.querySelector(".w-main").textContent = main;
    el.querySelector(".w-sub").textContent = sub;
    if (cell.excluded) title += "\nExcluded from calculations";
    el.title = title;
    const subject = prev ? prev.subject : cell.subject;
    el.style.background = role === "sample" ? assaySubjectColor(subject, order) : "";
  });
  assayRenderSelInfo();
}

function assayRenderSelInfo() {
  const wells = assaySortedSel();
  let text = "No wells selected.";
  if (wells.length) {
    const rs = wells.map((w) => assayWellRC(w)[0]);
    const cs = wells.map((w) => assayWellRC(w)[1]);
    const [r0, r1, c0, c1] = [Math.min(...rs), Math.max(...rs), Math.min(...cs), Math.max(...cs)];
    const isRect = wells.length === (r1 - r0 + 1) * (c1 - c0 + 1);
    const where = wells.length === 1 ? wells[0]
      : isRect ? `${assayWellName(r0, c0)}:${assayWellName(r1, c1)}`
      : wells.length <= 6 ? wells.join(", ") : "";
    text = `${wells.length} well${wells.length === 1 ? "" : "s"} selected${where ? ` (${where})` : ""}.`;
  }
  document.getElementById("assay-sel-info").textContent = text;
}

function assaySortedSel() {
  return Array.from(assaySel).sort((a, b) => {
    const [ra, ca] = assayWellRC(a);
    const [rb, cb] = assayWellRC(b);
    return ra - rb || ca - cb;
  });
}

function assayRect(a, b) {
  const [r0, c0] = assayWellRC(a);
  const [r1, c1] = assayWellRC(b);
  const out = [];
  for (let r = Math.min(r0, r1); r <= Math.max(r0, r1); r++) {
    for (let c = Math.min(c0, c1); c <= Math.max(c0, c1); c++) out.push(assayWellName(r, c));
  }
  return out;
}

function assaySetSelection(wells) {
  assaySel.clear();
  wells.forEach((w) => assaySel.add(w));
  assaySelectionChanged();
}

function assaySelectionChanged() {
  assayPaintCanvas();
  assaySchedulePreview();
}

(function bindCanvas() {
  const canvas = document.getElementById("assay-plate-canvas");
  canvas.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const head = e.target.closest(".plate-head");
    const add = e.metaKey || e.ctrlKey || e.shiftKey;
    if (head) {
      e.preventDefault();
      let wells = [];
      if (head.dataset.all) wells = assayRect("A1", "H12");
      else if (head.dataset.row !== undefined) wells = assayRect(assayWellName(+head.dataset.row, 0), assayWellName(+head.dataset.row, 11));
      else if (head.dataset.col !== undefined) wells = assayRect(assayWellName(0, +head.dataset.col), assayWellName(7, +head.dataset.col));
      assaySetSelection(add ? [...assaySel, ...wells] : wells);
      return;
    }
    const el = e.target.closest(".well");
    if (!el) return;
    e.preventDefault();
    const w = el.dataset.well;
    if (e.shiftKey && assayAnchor) {
      assayDrag = { base: new Set(assaySel), start: assayAnchor };
      assaySetSelection([...assayDrag.base, ...assayRect(assayAnchor, w)]);
      return;
    }
    if (e.metaKey || e.ctrlKey) {
      assayDrag = { base: new Set(assaySel), start: w };
      if (assaySel.has(w)) { assayDrag.base.delete(w); assayDrag.removing = true; }
    } else {
      assayDrag = { base: new Set(), start: w };
    }
    assayAnchor = w;
    assayDragTo(w);
  });
  canvas.addEventListener("mouseover", (e) => {
    if (!assayDrag) return;
    const el = e.target.closest(".well");
    if (el) assayDragTo(el.dataset.well);
  });
  document.addEventListener("mouseup", () => { assayDrag = null; });
})();

function assayDragTo(w) {
  const rect = assayRect(assayDrag.start, w);
  if (assayDrag.removing) {
    assaySetSelection([...assayDrag.base].filter((x) => !rect.includes(x)));
  } else {
    assaySetSelection([...assayDrag.base, ...rect]);
  }
}

document.addEventListener("keydown", (e) => {
  if (assayActiveTab() !== "layout" || e.target.closest("input, textarea, select")) return;
  if (e.key === "Escape") assaySetSelection([]);
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a") {
    e.preventDefault();
    assaySetSelection(assayRect("A1", "H12"));
  }
});

document.getElementById("assay-sel-clear-btn").addEventListener("click", () => assaySetSelection([]));

// -- ② Plate Layout: assignment ----------------------------------------------------------
document.querySelectorAll("#assay-assign-seg .seg-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    assayAssignMode = btn.dataset.assign;
    document.querySelectorAll("#assay-assign-seg .seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll("#mode-assay [data-assign-form]").forEach((f) => { f.hidden = f.dataset.assignForm !== assayAssignMode; });
    assaySchedulePreview();
  });
});

document.getElementById("assay-std-mode").addEventListener("change", (e) => {
  document.querySelectorAll("#mode-assay [data-std-mode]").forEach((el) => { el.hidden = el.dataset.stdMode !== e.target.value; });
  assaySchedulePreview();
});

function assaySamplesBody(preview) {
  return {
    wells: assaySortedSel(),
    subjects: document.getElementById("assay-subjects").value,
    timepoints: document.getElementById("assay-timepoints").value,
    replicates: Math.max(1, parseInt(document.getElementById("assay-reps").value, 10) || 1),
    order: document.getElementById("assay-fill-order").value,
    nesting: document.getElementById("assay-nesting").value,
    preview,
  };
}

function assayStandardsBody(preview) {
  const num = (id) => { const v = parseFloat(document.getElementById(id).value); return Number.isFinite(v) ? v : null; };
  return {
    wells: assaySortedSel(),
    direction: document.getElementById("assay-std-direction").value,
    mode: document.getElementById("assay-std-mode").value,
    top: num("assay-std-top"),
    factor: num("assay-std-factor"),
    include_blank: document.getElementById("assay-std-blank").checked,
    lowest_first: document.getElementById("assay-std-lowfirst").checked,
    concs_text: document.getElementById("assay-std-list").value,
    preview,
  };
}

let assayPreviewTimer = null;
let assayPreviewSeq = 0;

function assaySchedulePreview() {
  clearTimeout(assayPreviewTimer);
  assayPreviewTimer = setTimeout(assayUpdatePreview, 180);
}

async function assayUpdatePreview() {
  const seq = ++assayPreviewSeq;
  const msgEl = "assay-assign-preview";
  const n = assaySel.size;
  let preview = new Map();
  let message = "";
  let kind = "info";
  if (!n) {
    message = "Select wells on the plate to assign them.";
  } else if (assayAssignMode === "samples" || assayAssignMode === "standards") {
    const body = assayAssignMode === "samples" ? assaySamplesBody(true) : assayStandardsBody(true);
    const empty = assayAssignMode === "samples" && !body.subjects.trim() && !body.timepoints.trim();
    if (empty) {
      message = "Enter subjects (and optionally timepoints) to see a preview.";
    } else {
      try {
        const r = await apiPostJson(`${ASSAY_API}/assign/${assayAssignMode}`, body);
        if (seq !== assayPreviewSeq) return;  // a newer preview is on its way
        r.preview.forEach((p) => preview.set(p.well, p));
        message = r.message;
        kind = r.error ? "warn" : "info";
      } catch (err) {
        message = err.message;
        kind = "warn";
      }
    }
  } else if (assayAssignMode === "blank") {
    const max = assayState.max_reps;
    message = n > max ? `${n} wells selected; the blank can have at most ${max}.` : `Mark ${n} well(s) as the blank.`
      + (n === 1 ? " With one well there's no blank SD, so no LOD/LOQ." : "");
    kind = n > max ? "warn" : "info";
    if (n <= max) assaySortedSel().forEach((w, i) => preview.set(w, { role: "blank", text: "Blank", sub: `S${i + 1}` }));
  } else if (assayAssignMode === "exclude") {
    const ex = new Set(assayState.excluded);
    const k = assaySortedSel().filter((w) => ex.has(w)).length;
    message = `${n} well(s) selected${k ? `, ${k} already excluded` : ""}.`;
  } else {
    message = `Clear ${n} well(s).`;
  }
  if (seq !== assayPreviewSeq) return;
  assayPreview = preview;
  setStatus(msgEl, message, kind);
  assayPaintCanvas();
}

["assay-subjects", "assay-timepoints", "assay-reps", "assay-fill-order", "assay-nesting",
 "assay-std-direction", "assay-std-top", "assay-std-factor", "assay-std-blank", "assay-std-lowfirst", "assay-std-list",
].forEach((id) => {
  const el = document.getElementById(id);
  el.addEventListener("input", assaySchedulePreview);
  el.addEventListener("change", assaySchedulePreview);
});

async function assayAssign(kind, body) {
  if (!assaySel.size) throw new Error("Select wells on the plate first.");
  // Unapplied table edits would be clobbered by the next table Apply — save them first.
  if (assayLayoutDirty) await assayApplyTables(true);
  const r = await apiPostJson(`${ASSAY_API}/assign/${kind}`, body);
  assayPreview = new Map();
  await assayRefresh(r, { resetLayout: true });
  assaySetSelection([]);
  toast(r.message, r.layout_problems.length ? "info" : "success");
}

onClick("assay-assign-samples-btn", () => assayAssign("samples", assaySamplesBody(false)));
onClick("assay-assign-standards-btn", () => assayAssign("standards", assayStandardsBody(false)));
onClick("assay-assign-blank-btn", () => assayAssign("blank", { wells: assaySortedSel() }));
onClick("assay-assign-clear-btn", () => assayAssign("clear", { wells: assaySortedSel() }));

async function assaySetExcluded(wells, excluded) {
  if (!wells.length) throw new Error("Select wells on the plate first.");
  const r = await apiPostJson(`${ASSAY_API}/exclude`, { wells, excluded });
  await assayRefresh(r);
  toast(r.message, "success");
  // Fit-quality notes belong next to the curve; only a lost fit is an error.
  const warnings = r.warnings || [];
  setStatus("assay-curve-status", warnings.join("  •  "), "warn");
  warnings.filter((w) => w.includes("can't be fitted")).forEach((w) => toast(w, "error"));
  return r;
}

onClick("assay-exclude-btn", async () => { await assaySetExcluded(assaySortedSel(), true); assaySetSelection([]); });
onClick("assay-include-btn", async () => { await assaySetExcluded(assaySortedSel(), false); assaySetSelection([]); });

onClick("assay-layout-paste-btn", async () => {
  const text = document.getElementById("assay-layout-paste").value;
  if (assayLayoutDirty) await assayApplyTables(true);
  const r = await apiPostJson(`${ASSAY_API}/layout/paste`, { text, sep: document.getElementById("assay-layout-sep").value });
  await assayRefresh(r, { resetLayout: true });
  document.getElementById("assay-layout-paste").value = "";
  toast(r.message, "success");
});

// -- ② Plate Layout: tables ------------------------------------------------------------------
function assaySetDirty(dirty) {
  assayLayoutDirty = dirty;
  document.getElementById("assay-layout-dirty").hidden = !dirty;
}

// Editable table bound to an array of row objects. columns: [key, title,
// {readOnly}]. Pasting a block copied from Excel (several lines and/or tab-
// separated cells) into a cell fills the cells below/right of it.
function assayEditableTable(containerId, rows, columns, opts) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  const table = document.createElement("table");
  table.className = "editable-table";
  const head = document.createElement("tr");
  columns.forEach(([, title]) => { const th = document.createElement("th"); th.textContent = title; head.appendChild(th); });
  if (!opts.noDelete) head.appendChild(document.createElement("th"));
  table.appendChild(head);
  const editable = columns.filter(([, , o]) => !(o && o.readOnly)).map(([k]) => k);
  const rerender = () => assayEditableTable(containerId, rows, columns, opts);
  rows.forEach((row, ri) => {
    const tr = document.createElement("tr");
    if (opts.rowClass) tr.className = opts.rowClass(row, ri) || "";
    columns.forEach(([key, , o]) => {
      const td = document.createElement("td");
      if (o && o.readOnly) {
        td.textContent = row[key] ?? "";
        td.className = "ro";
      } else {
        const inp = document.createElement("input");
        inp.type = "text";
        inp.value = row[key] === null || row[key] === undefined ? "" : row[key];
        if (o && o.placeholder) inp.placeholder = o.placeholder;
        inp.addEventListener("input", () => { row[key] = inp.value; opts.onEdit(); });
        inp.addEventListener("paste", (e) => {
          const text = (e.clipboardData || window.clipboardData).getData("text").replace(/\r/g, "").replace(/\n+$/, "");
          if (!/[\t\n]/.test(text)) return;
          e.preventDefault();
          const ci = editable.indexOf(key);
          text.split("\n").forEach((line, i) => {
            if (!rows[ri + i]) {
              if (!opts.newRow) return;
              rows.push(opts.newRow());
            }
            line.split("\t").forEach((val, j) => {
              const k = editable[ci + j];
              if (k) rows[ri + i][k] = val.trim();
            });
          });
          opts.onEdit();
          rerender();
        });
        td.appendChild(inp);
      }
      tr.appendChild(td);
    });
    if (!opts.noDelete) {
      const rmTd = document.createElement("td");
      const rm = document.createElement("button");
      rm.className = "icon-btn";
      rm.textContent = "×";
      rm.title = "Delete row";
      rm.addEventListener("click", () => { rows.splice(ri, 1); opts.onEdit(); rerender(); });
      rmTd.appendChild(rm);
      tr.appendChild(rmTd);
    }
    table.appendChild(tr);
  });
  container.appendChild(table);
}

// Replicate columns: S1–S3 always, plus any S4… in use (or added with + Replicate).
function assayRepCols() {
  const n = Math.max(3, ...assayStdDraft.flatMap((r) => Object.keys(r).filter((k) => /^S\d+$/.test(k)).map((k) => +k.slice(1))));
  return Array.from({ length: n }, (_x, i) => `S${i + 1}`);
}

function assayRenderStdTable() {
  const cu = assayState ? assayState.conc_unit : "";
  const reps = assayRepCols();
  assayEditableTable("assay-std-table", assayStdDraft, [
    ["Label", "Label"], ["Conc", `Conc (${cu})`],
    ...reps.map((k, i) => [k, `Set ${i + 1} well`, i === 0 ? { placeholder: "e.g. A1" } : undefined]),
  ], { onEdit: () => assaySetDirty(true), rowClass: (_r, ri) => (ri === 0 ? "row-blank" : ""),
       newRow: () => ({ Label: "", Conc: null, ...Object.fromEntries(reps.map((k) => [k, ""])) }) });
  document.getElementById("assay-std-addrep-btn").disabled = reps.length >= (assayState ? assayState.max_reps : 12);
}

document.getElementById("assay-std-addrep-btn").addEventListener("click", () => {
  const next = `S${assayRepCols().length + 1}`;
  if (!assayStdDraft.length) assayStdDraft.push({ Label: "Blank", Conc: 0 });
  assayStdDraft.forEach((r) => { r[next] = r[next] || ""; });
  assaySetDirty(true);
  assayRenderStdTable();
});

function assayRenderSampleTable() {
  assayEditableTable("assay-sample-table", assaySampleDraft, [
    ["Well", "Well", { placeholder: "e.g. D1" }], ["Subject", "Subject"], ["Timepoint", "Timepoint"],
    ["Label", "Label", { placeholder: "auto" }],
  ], { onEdit: () => assaySetDirty(true), newRow: () => ({ Well: "", Subject: "", Timepoint: "", Label: "" }) });
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
  assayStdDraft.push({ Label: `Std ${assayStdDraft.length + 1}`, Conc: null,
    ...Object.fromEntries(assayRepCols().map((k) => [k, ""])) });
  assaySetDirty(true);
  assayRenderStdTable();
});

document.getElementById("assay-sample-add-btn").addEventListener("click", () => {
  assaySampleDraft.push({ Well: "", Subject: "", Timepoint: "", Label: "" });
  assaySetDirty(true);
  assayRenderSampleTable();
});

async function assayApplyTables(quiet = false) {
  const std = assayStdDraft.map((r) => {
    const t = String(r.Conc ?? "").trim().replace(",", ".");
    if (t !== "" && !Number.isFinite(Number(t))) throw new Error(`Concentration "${r.Conc}" (${r.Label}) isn't a number.`);
    return { ...r, Conc: t === "" ? null : Number(t) };
  });
  // A label left over from the old subject/timepoint would go stale; let the server recompose it.
  const samples = assaySampleDraft.map((r, i) => {
    const orig = assayState.sample_rows[i];
    const autoLabel = orig && orig.Label === [orig.Subject, orig.Timepoint].filter(Boolean).join(" · ");
    const changed = orig && (orig.Subject !== r.Subject || orig.Timepoint !== r.Timepoint);
    return autoLabel && changed && r.Label === orig.Label ? { ...r, Label: "" } : r;
  });
  await assayRefresh(await apiPostJson(`${ASSAY_API}/layout`, { std_rows: std, sample_rows: samples }),
    { resetLayout: true });
  if (!quiet) {
    toast(assayState.layout_problems.length ? "Layout saved — but see the warnings above the plate." : "Layout saved.",
      assayState.layout_problems.length ? "error" : "success");
  }
}

onClick("assay-layout-apply-btn", () => assayApplyTables());
onClick("assay-layout-reset-btn", async () => assayRefresh(undefined, { resetLayout: true }));

// -- ② Plate Layout: templates ------------------------------------------------------------
async function assayLoadTemplates(payload) {
  const r = payload || (await apiCall(`${ASSAY_API}/templates`));
  const sel = document.getElementById("assay-template-select");
  const keep = sel.value;
  sel.innerHTML = "";
  if (!r.templates.length) sel.add(new Option("No templates saved yet", ""));
  r.templates.forEach((t) => sel.add(new Option(`${t.name} — ${t.n_levels} levels, ${t.n_samples} samples (${t.saved_at})`, t.name)));
  if (r.templates.some((t) => t.name === keep)) sel.value = keep;
  ["assay-template-apply-btn", "assay-template-download-btn", "assay-template-delete-btn"].forEach((id) => {
    document.getElementById(id).disabled = !r.templates.length;
  });
}

document.getElementById("assay-templates-details").addEventListener("toggle", (e) => {
  if (e.target.open) assayLoadTemplates().catch((err) => toast(err.message, "error"));
});

onClick("assay-template-save-btn", async () => {
  const name = document.getElementById("assay-template-name").value.trim();
  if (!name) throw new Error("Give the template a name.");
  if (assayLayoutDirty) await assayApplyTables(true);
  const r = await apiPostJson(`${ASSAY_API}/templates`, { name });
  await assayLoadTemplates(r);
  document.getElementById("assay-template-select").value = name;
  document.getElementById("assay-template-name").value = "";
  toast(r.message, "success");
});

onClick("assay-template-apply-btn", async () => {
  const name = document.getElementById("assay-template-select").value;
  if (!name) return;
  if (assayLayoutDirty && !confirm("Discard your unsaved table edits on ② Plate Layout?")) return;
  const r = await apiPostJson(`${ASSAY_API}/templates/apply`, { name });
  await assayRefresh(r, { resetLayout: true });
  if (r.fit_type) document.getElementById("assay-fit-type").value = r.fit_type;
  toast(r.message, "success");
});

onClick("assay-template-delete-btn", async () => {
  const name = document.getElementById("assay-template-select").value;
  if (!name || !confirm(`Delete the template "${name}"?`)) return;
  await assayLoadTemplates(await apiDelete(`${ASSAY_API}/templates/${encodeURIComponent(name)}`));
});

document.getElementById("assay-template-download-btn").addEventListener("click", () => {
  const name = document.getElementById("assay-template-select").value;
  if (name) download(`${ASSAY_API}/templates/${encodeURIComponent(name)}/download`);
});

setupFileInput("assay-template-upload", async (files) => {
  const form = new FormData();
  form.append("file", files[0]);
  try {
    const r = await apiCall(`${ASSAY_API}/templates/upload`, { method: "POST", body: form });
    await assayLoadTemplates(r);
    toast(r.message, "success");
  } catch (err) {
    toast(err.message, "error");
  }
});

// -- ③ Standard Curve ------------------------------------------------------------------
function assayClearCurve() {
  drawPlot("assay-curve-plot", null);
  renderTable("assay-standards-table", []);
  setStatus("assay-equation", "");
  setStatus("assay-curve-status", "");
  document.getElementById("assay-qc").hidden = true;
}

function assayShowCurvePayload(res) {
  drawPlot("assay-curve-plot", res.figure);
  renderTable("assay-standards-table", res.standards, {
    rowClass: (r) => (String(r.Label).endsWith("(blank)") ? "row-blank" : String(r["Recovery (%)"]).includes("⚠") ? "row-flag" : ""),
  });
  setStatus("assay-equation", res.figure ? `${res.equation}    •    blank mean = ${Number(res.blank_mean).toPrecision(4)} ${assayState.sig_unit}` : "");
  const qc = document.getElementById("assay-qc");
  qc.hidden = !res.qc;
  if (res.qc) {
    qc.innerHTML = "";
    [["LOD (3 SD)", res.qc.lod], ["LOQ (10 SD)", res.qc.loq], ["Blank SD", `${res.qc.blank_sd} (n = ${res.qc.n_blank})`],
     ["Recovery", res.qc.recovery_range]].forEach(([k, v]) => {
      const item = document.createElement("span");
      item.innerHTML = "<b></b> ";
      item.firstChild.textContent = k;
      item.appendChild(document.createTextNode(v));
      qc.appendChild(item);
    });
    if (res.qc.note) {
      const n = document.createElement("span");
      n.className = "hint";
      n.textContent = res.qc.note;
      qc.appendChild(n);
    }
  }
  if (res.source) setStatus("assay-curve-status", `Using the standard curve from ${res.source}.`);
}

async function assayShowCurve() {
  const res = await apiCall(`${ASSAY_API}/curve?show_reps=${document.getElementById("assay-show-reps").checked}`);
  if (res.figure) assayShowCurvePayload(res);
  else assayClearCurve();
}

onClick("assay-compute-btn", async () => {
  if (assayLayoutDirty) throw new Error("You have unsaved table edits on ② Plate Layout — click Apply table edits first.");
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

// -- ④ Results -------------------------------------------------------------------------------
async function assayRenderResults() {
  if (!assayState || !assayState.has_result) {
    setStatus("assay-results-status", "Compute the standard curve in ③ first.");
    ["assay-results-table", "assay-groups-table"].forEach((id) => renderTable(id, []));
    drawPlot("assay-results-map", null);
    drawPlot("assay-groups-plot", null);
    return;
  }
  const r = await apiCall(`${ASSAY_API}/results`);
  setStatus("assay-results-status", [r.summary, ...r.notes].join("  •  "), r.notes.length ? "warn" : "info");
  renderTable("assay-groups-table", r.groups, { rowClass: (row) => (row.Flag ? "row-flag" : "") });
  drawPlot("assay-groups-plot", r.group_figure);
  const rows = r.rows.map((row) => ({
    ...(r.multi_plate ? { Plate: row.Plate } : {}),
    Well: row.Well, Subject: row.Subject, Timepoint: row.Timepoint,
    Label: row.Subject || row.Timepoint ? "" : row.Label,
    [`Signal (${r.sig_unit})`]: row.Signal, [`ΔSignal (${r.sig_unit})`]: row["ΔSignal"],
    [`Conc (${r.conc_unit})`]: row.Conc, Flag: row.Flag,
  }));
  renderTable("assay-results-table", rows, {
    rowClass: (row) => (row.Flag === "excluded" ? "row-excluded" : row.Flag ? "row-flag" : ""),
  });
  // A "Use" checkbox per well on the selected plate (exclusions are per plate).
  const table = document.getElementById("assay-results-table");
  if (table.rows.length) {
    const th = document.createElement("th");
    th.textContent = "Use";
    table.rows[0].insertBefore(th, table.rows[0].firstChild);
    r.rows.forEach((row, i) => {
      const td = document.createElement("td");
      if (!r.multi_plate || row.Plate === r.active_plate) {
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = !row.Excluded;
        cb.title = row.Excluded ? "Include this well again" : "Exclude this well from the means";
        cb.addEventListener("change", () => {
          // assayRefresh (inside) redraws this tab.
          assaySetExcluded([row.Well], !cb.checked).catch((err) => toast(err.message, "error"));
        });
        td.appendChild(cb);
      }
      table.rows[i + 1].insertBefore(td, table.rows[i + 1].firstChild);
    });
  }
  drawPlot("assay-results-map", r.figure, { displayModeBar: false });
}

// -- ⑤ Normalise & Export -------------------------------------------------------------------------
let assayNormDraft = [];
let assayNormDirty = false;

function assaySetNormDirty(d) {
  assayNormDirty = d;
  document.getElementById("assay-norm-dirty").hidden = !d;
}

function assayShowNorm(p) {
  document.getElementById("assay-norm-vol-unit").value = p.vol_unit;
  document.getElementById("assay-norm-area-unit").value = p.area_unit;
  assayNormDraft = p.inputs.map((r) => ({ ...r }));
  assaySetNormDirty(false);
  assayRenderNormInputs();
  renderTable("assay-norm-table", p.table, { rowClass: (row) => (row.Flag ? "row-flag" : "") });
  drawPlot("assay-norm-plot", p.figure);
  const msg = p.n_samples ? p.notes.join("  •  ") : "Label sample wells in ② Plate Layout to normalise them.";
  setStatus("assay-norm-status", msg, p.notes.length || !p.n_samples ? "warn" : "info");
}

function assayRenderNormInputs() {
  const vu = document.getElementById("assay-norm-vol-unit").value;
  const au = document.getElementById("assay-norm-area-unit").value;
  assayEditableTable("assay-norm-inputs", assayNormDraft, [
    ["Subject", "Subject", { readOnly: true }], ["Timepoint", "Timepoint", { readOnly: true }],
    ["Dilution", "Dilution ×", { placeholder: "1" }], ["Volume", `Volume (${vu})`], ["Area", `Area (${au})`],
  ], { onEdit: () => assaySetNormDirty(true), noDelete: true });
}

async function assayRenderNormalise() {
  if (!assayState || !assayState.has_result) {
    setStatus("assay-norm-status", "Compute the standard curve in ③ first.");
    document.getElementById("assay-norm-inputs").innerHTML = "";
    renderTable("assay-norm-table", []);
    drawPlot("assay-norm-plot", null);
    return;
  }
  if (assayNormDirty) return;  // keep unsaved inputs
  assayShowNorm(await apiCall(`${ASSAY_API}/normalise`));
}

["assay-norm-vol-unit", "assay-norm-area-unit"].forEach((id) => {
  document.getElementById(id).addEventListener("input", () => { assaySetNormDirty(true); assayRenderNormInputs(); });
});

onClick("assay-norm-fill-btn", async () => {
  const col = document.getElementById("assay-norm-fill-col").value;
  const val = document.getElementById("assay-norm-fill-val").value.trim();
  assayNormDraft.forEach((r) => { r[col] = val; });
  assaySetNormDirty(true);
  assayRenderNormInputs();
});

onClick("assay-norm-apply-btn", async () => {
  const bad = [];
  const rows = assayNormDraft.map((r) => {
    const out = { Subject: r.Subject, Timepoint: r.Timepoint };
    ["Dilution", "Volume", "Area"].forEach((k) => {
      const t = String(r[k] ?? "").trim().replace(",", ".");
      if (t !== "" && !Number.isFinite(Number(t))) bad.push(`${k} "${r[k]}" (${[r.Subject, r.Timepoint].filter(Boolean).join(" ")})`);
      out[k] = t === "" ? null : Number(t);
    });
    return out;
  });
  if (bad.length) throw new Error(`Not a number: ${bad.slice(0, 4).join(", ")}${bad.length > 4 ? " …" : ""}`);
  assayShowNorm(await apiPostJson(`${ASSAY_API}/normalise`, {
    area_unit: document.getElementById("assay-norm-area-unit").value,
    vol_unit: document.getElementById("assay-norm-vol-unit").value,
    rows,
  }));
  toast("Normalisation applied.", "success");
});

document.getElementById("mode-assay").addEventListener("tabshown", (e) => {
  if (e.detail.tab === "results") assayRenderResults().catch((err) => toast(err.message, "error"));
  if (e.detail.tab === "normalise") assayRenderNormalise().catch((err) => toast(err.message, "error"));
});

const assayCurveBody = () => ({ ...exportOptions("assay"), show_reps: document.getElementById("assay-show-reps").checked });
document.getElementById("assay-export-summary-btn").addEventListener("click", () => download(`${ASSAY_API}/export/summary-csv`));
document.getElementById("assay-export-wide-btn").addEventListener("click", () => download(`${ASSAY_API}/export/summary-csv?wide=true`));
document.getElementById("assay-export-normalised-btn").addEventListener("click", () => {
  if (assayNormDirty) toast("Exporting the last applied normalisation — click Apply to include your unsaved edits.");
  download(`${ASSAY_API}/export/normalised-csv`);
});
document.getElementById("assay-export-results-btn").addEventListener("click", () => download(`${ASSAY_API}/export/results-csv`));
document.getElementById("assay-export-standards-btn").addEventListener("click", () => download(`${ASSAY_API}/export/standards-csv`));
document.getElementById("assay-export-curve-btn").addEventListener("click", () => download(`${ASSAY_API}/export/curve`, assayCurveBody()));
document.getElementById("assay-preview-curve-btn").addEventListener("click", () => previewExport("assay-preview-img", `${ASSAY_API}/export/curve`, assayCurveBody()));

registerMode("assay", { refresh: (state) => assayRefresh(state, { resetLayout: true }) });
sessionReady.then(() => assayRefresh()).catch((err) => toast(err.message, "error"));
