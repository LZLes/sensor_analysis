// Amperometry mode — the shared trace-mode UI (trace_mode.js) plus what
// sets it apart from Solid-State: a Baseline column (baseline subtraction),
// segmented-linear fits, the channel-average trace, and the effective-
// concentration (serial dilution) calculator.

const AMP_API = "/api/amperometry";

const ampMode = createTraceMode({
  mode: "amperometry",
  prefix: "amp",
  api: AMP_API,
  columns: ["Label", "Concentration", "Spike Vol", "Stock Conc", "t_start", "t_end", "avg_duration", "Baseline"],
  columnTitles: { t_start: "t start (s)", t_end: "t end (s)", avg_duration: "avg last (s)" },
  boolColumns: ["Baseline"],
  signalColLabel: "Current",
  newRow: (last) => {
    const t0 = last && last.t_end != null ? last.t_end : 0;
    return {
      Label: "New", Concentration: null, "Spike Vol": null, "Stock Conc": null,
      t_start: t0, t_end: t0 + 60, avg_duration: null, Baseline: false,
    };
  },
  autodetectApplyExtra: () => ({ include_baseline: document.getElementById("amp-autodetect-baseline").checked }),
  computeBody: () => ({
    fit_type: document.getElementById("amp-fit-type").value,
    n_seg: parseInt(document.getElementById("amp-n-seg").value, 10),
    show_avg: document.getElementById("amp-show-avg").checked,
  }),
  onState: (state) => {
    document.getElementById("amp-initial-volume").value = state.initial_volume;
    document.getElementById("amp-vol-unit").value = state.vol_unit;
  },
});
const ampRefresh = ampMode.refresh;

document.getElementById("amp-fit-type").addEventListener("change", (e) => {
  document.getElementById("amp-n-seg").disabled = e.target.value !== "Segmented Linear";
});

onClick("amp-effconc-btn", async () => {
  const idx = Array.from(document.getElementById("amp-dataset-select").options)
    .findIndex((o) => o.selected);
  if (idx < 0) return;
  const initialVolume = parseFloat(document.getElementById("amp-initial-volume").value);
  if (!(initialVolume > 0)) throw new Error("Initial volume must be a positive number.");
  await ampRefresh(await apiPostJson(`${AMP_API}/files/${idx}/effective-concentration`, {
    initial_volume: initialVolume,
    vol_unit: document.getElementById("amp-vol-unit").value,
  }));
  toast("Concentrations recalculated from spike volumes and stock concentrations.", "success");
});
