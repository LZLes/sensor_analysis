// Shared UI for the two trace-based, time-windowed calibration modes
// (Amperometry and Solid-State) — the web counterpart of core/shared_tabs.py.
// Each mode calls createTraceMode() with its own API prefix, calibration-
// table columns and mode-specific hooks; everything else lives here once.

function createTraceMode(cfg) {
  const P = cfg.prefix;            // DOM id prefix: "amp" / "ss"
  const API = cfg.api;
  const $ = (suffix) => document.getElementById(`${P}-${suffix}`);

  let state = null;
  let activeFilename = null;
  let channelsDraft = [];
  let cpdfDraft = [];

  const activeIndex = () => (state ? state.files.findIndex((f) => f.filename === activeFilename) : -1);
  const activeFile = () => { const i = activeIndex(); return i >= 0 ? state.files[i] : null; };
  const visibleChannels = () => checkedValues(`${P}-channel-checklist`);

  async function refresh(newState) {
    state = newState || (await apiCall(`${API}/state`));
    if (!state.files.find((f) => f.filename === activeFilename)) {
      activeFilename = state.files.length ? state.files[0].filename : null;
    }
    $("conc-unit").value = state.conc_unit;
    $("signal-unit").value = state.signal_unit;
    $("smooth-method").value = state.smooth_method;
    $("smooth-window").value = state.smooth_window;
    $("smooth-polyorder").value = state.smooth_polyorder;
    if (cfg.onState) cfg.onState(state);

    renderFilesList(`${P}-files-list`,
      state.files.map((f) => `${f.filename} — ${f.n_rows} rows, ${f.channels.length} channel${f.channels.length !== 1 ? "s" : ""}`),
      async (i) => refresh(await apiDelete(`${API}/files/${i}`)));
    $("clear-btn").hidden = state.files.length === 0;
    document.querySelectorAll(`#mode-${cfg.mode} .needs-data`).forEach((el) => {
      el.classList.toggle("is-empty", state.files.length === 0);
    });
    renderDatasetSelect();
    renderChecklist(`${P}-channel-checklist`, state.channel_labels, renderTimeseries);
    renderPerFile();
    await renderTimeseries();
  }

  function renderPerFile() {
    renderChannelAssignment();
    renderCalTable();
    renderAutodetectChannelSelect();
  }

  function renderDatasetSelect() {
    const sel = $("dataset-select");
    sel.innerHTML = "";
    state.files.forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f.filename;
      opt.textContent = f.filename;
      sel.appendChild(opt);
    });
    sel.value = activeFilename || "";
    sel.onchange = () => { activeFilename = sel.value; renderPerFile(); };
  }

  // -- Channel assignment ---------------------------------------------------------------
  // Renders from `draft` when given (after +/− edits), else from the server copy.
  function renderChannelAssignment(draft) {
    const container = $("channel-assignment");
    container.innerHTML = "";
    const frec = activeFile();
    if (!frec) return;
    channelsDraft = draft || frec.channels.map((c) => ({ ...c }));
    channelsDraft.forEach((ch, ci) => {
      const row = document.createElement("div");
      row.className = "channel-row";
      const nameInp = document.createElement("input");
      nameInp.type = "text";
      nameInp.value = ch.name;
      nameInp.addEventListener("change", () => { channelsDraft[ci].name = nameInp.value; });
      const rm = document.createElement("button");
      rm.className = "icon-btn";
      rm.title = "Remove channel";
      rm.textContent = "×";
      rm.addEventListener("click", () => { channelsDraft.splice(ci, 1); renderChannelAssignment(channelsDraft); });
      row.append(
        labeled("Name", nameInp),
        labeled("Time", columnSelect(frec.columns, ch.tc, (v) => { channelsDraft[ci].tc = v; })),
        labeled(cfg.signalColLabel, columnSelect(frec.columns, ch.ic, (v) => { channelsDraft[ci].ic = v; })),
        rm,
      );
      container.appendChild(row);
    });
    const add = document.createElement("button");
    add.textContent = "+ Channel";
    add.addEventListener("click", () => {
      const cols = frec.columns;
      channelsDraft.push({ name: `Channel ${channelsDraft.length + 1}`, tc: cols[0], ic: cols[Math.min(1, cols.length - 1)] });
      renderChannelAssignment(channelsDraft);
    });
    container.appendChild(add);
  }

  onClick(`${P}-apply-channels-btn`, async () => {
    const idx = activeIndex();
    if (idx < 0) return;
    await refresh(await apiPostJson(`${API}/files/${idx}/channels`, { channels: channelsDraft }));
    toast("Channel assignment applied.", "success");
  });

  // -- Calibration table --------------------------------------------------------------------
  function renderCalTable() {
    const container = $("cal-table");
    container.innerHTML = "";
    const frec = activeFile();
    if (!frec) return;
    cpdfDraft = frec.cpdf.map((r) => ({ ...r }));
    const table = document.createElement("table");
    table.className = "editable-table";
    const thead = document.createElement("tr");
    cfg.columns.forEach((c) => {
      const th = document.createElement("th");
      th.textContent = cfg.columnTitles?.[c] || c;
      thead.appendChild(th);
    });
    thead.appendChild(document.createElement("th"));
    table.appendChild(thead);

    cpdfDraft.forEach((row, ri) => {
      const tr = document.createElement("tr");
      cfg.columns.forEach((c) => {
        const td = document.createElement("td");
        const inp = document.createElement("input");
        if ((cfg.boolColumns || []).includes(c)) {
          inp.type = "checkbox";
          inp.checked = !!row[c];
          inp.addEventListener("change", () => { cpdfDraft[ri][c] = inp.checked; commitCalTable(false); });
        } else {
          inp.type = "text";
          inp.value = row[c] === null || row[c] === undefined ? "" : row[c];
          if (c !== "Label") inp.inputMode = "decimal";
          inp.addEventListener("change", () => {
            const v = coerceCell(c, inp.value);
            inp.classList.toggle("invalid", v === undefined);
            if (v === undefined) { toast(`"${inp.value}" isn't a number.`, "error"); return; }
            cpdfDraft[ri][c] = v;
            commitCalTable(false);
          });
        }
        td.appendChild(inp);
        tr.appendChild(td);
      });
      const rmTd = document.createElement("td");
      const rmBtn = document.createElement("button");
      rmBtn.className = "icon-btn";
      rmBtn.title = "Delete row";
      rmBtn.textContent = "×";
      rmBtn.addEventListener("click", () => { cpdfDraft.splice(ri, 1); commitCalTable(true); });
      rmTd.appendChild(rmBtn);
      tr.appendChild(rmTd);
      table.appendChild(tr);
    });
    container.appendChild(table);

    const addBtn = document.createElement("button");
    addBtn.textContent = "+ Row";
    addBtn.addEventListener("click", () => {
      const last = cpdfDraft[cpdfDraft.length - 1];
      cpdfDraft.push(cfg.newRow(last));
      commitCalTable(true);
    });
    container.appendChild(addBtn);
  }

  // undefined = invalid; null = blank
  function coerceCell(column, text) {
    if (column === "Label") return text;
    const t = text.trim();
    if (t === "") return null;
    const num = Number(t.replace(",", "."));
    return Number.isFinite(num) ? num : undefined;
  }

  // Cell edits don't rebuild the table (so Tab-ing between cells keeps
  // focus); adding/removing rows does.
  async function commitCalTable(rebuild) {
    const idx = activeIndex();
    if (idx < 0) return;
    try {
      state = await apiPostJson(`${API}/files/${idx}/table`, { rows: cpdfDraft });
      if (rebuild) renderCalTable();
      else cpdfDraft = state.files[idx].cpdf.map((r) => ({ ...r }));
      await renderTimeseries();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  // -- Autodetect ------------------------------------------------------------------------
  function renderAutodetectChannelSelect() {
    const sel = $("autodetect-channel");
    sel.innerHTML = "";
    const frec = activeFile();
    (frec ? frec.channels : []).forEach((ch) => {
      const opt = document.createElement("option");
      opt.value = ch.name;
      opt.textContent = ch.name;
      sel.appendChild(opt);
    });
    $("autodetect-result").textContent = "";
    $("autodetect-apply-btn").disabled = true;
  }

  onClick(`${P}-autodetect-btn`, async () => {
    const idx = activeIndex();
    if (idx < 0) return;
    const result = await apiPostJson(`${API}/files/${idx}/autodetect`, {
      channel: $("autodetect-channel").value,
      sensitivity: parseFloat($("autodetect-sensitivity").value),
      min_gap: parseFloat($("autodetect-mingap").value),
      max_steps: parseInt($("autodetect-maxsteps").value, 10) || 0,
    });
    $("autodetect-result").textContent = result.edges.length
      ? `${result.edges.length} step(s) at t = ${result.edges.map((e) => e.toFixed(1)).join(", ")} s — shown as pink lines on the plot.`
      : "No clear step transitions found — try lowering sensitivity.";
    $("autodetect-apply-btn").disabled = !result.edges.length;
    await renderTimeseries();
  });

  onClick(`${P}-autodetect-apply-btn`, async () => {
    const idx = activeIndex();
    if (idx < 0) return;
    const body = { channel: $("autodetect-channel").value, ...(cfg.autodetectApplyExtra ? cfg.autodetectApplyExtra() : {}) };
    await refresh(await apiPostJson(`${API}/files/${idx}/autodetect/apply`, body));
    toast("Calibration windows replaced from detected steps — fill in the concentrations.", "success");
  });

  // -- Time series -------------------------------------------------------------------------
  let tsSeq = 0;
  async function renderTimeseries() {
    if (!state || !state.files.length) {
      drawPlot(`${P}-plot`, null);
      return;
    }
    const seq = ++tsSeq;
    try {
      const fig = await apiPostJson(`${API}/timeseries`, {
        visible: visibleChannels(),
        smooth_method: $("smooth-method").value,
        smooth_window: parseInt($("smooth-window").value, 10) || 11,
        smooth_polyorder: parseInt($("smooth-polyorder").value, 10) || 2,
        y_auto: true,
      });
      if (seq === tsSeq) drawPlot(`${P}-plot`, fig);  // drop out-of-order responses
    } catch (err) {
      toast("Plot failed: " + err.message, "error");
    }
  }

  ["smooth-method", "smooth-window", "smooth-polyorder"].forEach((s) => $(s).addEventListener("change", renderTimeseries));

  // -- Import ---------------------------------------------------------------------------------
  onClick(`${P}-sample-btn`, async () => {
    await refresh(await apiCall(`${API}/files/sample`, { method: "POST" }));
    toast("Sample data loaded — continue in ② Time Series & Windows.", "success");
  });

  onClick(`${P}-clear-btn`, async () => {
    if (!confirm("Remove all loaded files and their calibration tables?")) return;
    await refresh(await apiDelete(`${API}/files`));
  });

  async function uploadFiles(fileList) {
    const form = new FormData();
    Array.from(fileList).forEach((f) => form.append("files", f));
    try {
      await refresh(await apiCall(`${API}/files`, { method: "POST", body: form }));
      toast(`${fileList.length} file(s) loaded.`, "success");
    } catch (err) {
      toast(err.message, "error");
    }
  }
  setupFileInput(`${P}-file-input`, uploadFiles);
  setupDropzone(`${P}-dropzone`, uploadFiles);

  ["conc-unit", "signal-unit"].forEach((s) => {
    $(s).addEventListener("change", async () => {
      try {
        state = await apiPostJson(`${API}/units`, { conc_unit: $("conc-unit").value, signal_unit: $("signal-unit").value });
        await renderTimeseries();
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });

  // -- Calibration results ------------------------------------------------------------------------
  onClick(`${P}-compute-btn`, async () => {
    const selected = visibleChannels();
    if (!selected.length) throw new Error("Tick at least one channel above the time-series plot.");
    const result = await apiPostJson(`${API}/compute`, { selected, ...cfg.computeBody() });
    setStatus(`${P}-cal-status`, result.warnings.join("  •  "), "warn");
    drawPlot(`${P}-cal-plot`, result.figure);
    renderTable(`${P}-stats-table`, result.stats);
    if (result.figure) toast("Calibration computed.", "success");
    else toast("No channel produced a calibration — see the notes above the plot.", "error");
  });

  // -- Export ---------------------------------------------------------------------------------------
  const curveBody = () => exportOptions(P);
  const tsBody = () => ({ ...exportOptions(P), visible: visibleChannels() });
  $("export-curve-btn").addEventListener("click", () => download(`${API}/export/curve`, curveBody()));
  $("export-ts-btn").addEventListener("click", () => download(`${API}/export/timeseries`, tsBody()));
  $("export-csv-btn").addEventListener("click", () => download(`${API}/export/csv`));
  $("preview-curve-btn").addEventListener("click", () => previewExport(`${P}-preview-img`, `${API}/export/curve`, curveBody()));
  $("preview-ts-btn").addEventListener("click", () => previewExport(`${P}-preview-img`, `${API}/export/timeseries`, tsBody()));

  // -- Compare files ----------------------------------------------------------------------------------
  async function renderComparison() {
    const result = await apiCall(`${API}/comparison`);
    drawPlot(`${P}-compare-plot`, result.figure);
    renderTable(`${P}-compare-table`, result.stats);
    setStatus(`${P}-compare-status`, result.figure ? "" : "Load files and fill in their calibration tables to compare them.");
  }
  onClick(`${P}-compare-btn`, renderComparison);
  document.getElementById(`mode-${cfg.mode}`).addEventListener("tabshown", (e) => {
    if (e.detail.tab === "compare" && state && state.files.length) renderComparison().catch((err) => toast(err.message, "error"));
  });

  registerMode(cfg.mode, { refresh });
  sessionReady.then(() => refresh()).catch((err) => toast(err.message, "error"));
  return { refresh };
}
