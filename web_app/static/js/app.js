// Phase 1 feasibility check: fetch a figure built server-side from
// modes/solid_state.py's sample-data loader and render it with Plotly.js.
// Per-mode logic (amperometry.js/solid_state.js/cyclic_voltammetry.js)
// lands in later phases.
fetch("/api/_test/figure")
  .then((res) => res.json())
  .then((fig) => {
    Plotly.newPlot("test-plot", fig.data, fig.layout, { responsive: true });
  });
