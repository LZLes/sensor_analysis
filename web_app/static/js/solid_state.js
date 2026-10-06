// Solid-State (potentiometric / Nernstian) mode — the shared trace-mode UI
// (trace_mode.js) with a Nernstian E vs log(concentration) fit. A Reading
// column lets a row use a typed-in potential instead of a trace window.

const ssMode = createTraceMode({
  mode: "solid_state",
  prefix: "ss",
  api: "/api/solid_state",
  columns: ["Label", "Concentration", "t_start", "t_end", "avg_duration", "Reading_mV"],
  columnTitles: { t_start: "t start (s)", t_end: "t end (s)", avg_duration: "avg last (s)", Reading_mV: "Manual reading" },
  signalColLabel: "Potential",
  newRow: (last) => {
    const t0 = last && last.t_end != null ? last.t_end : 0;
    return { Label: "New", Concentration: null, t_start: t0, t_end: t0 + 60, avg_duration: null, Reading_mV: null };
  },
  computeBody: () => ({
    ion_charge: parseInt(document.getElementById("ss-ion-charge").value, 10) || 1,
    lab_temp: parseFloat(document.getElementById("ss-lab-temp").value),
  }),
});
const ssRefresh = ssMode.refresh;
