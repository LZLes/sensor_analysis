"""Matplotlib publication-style presets shared by every mode's PNG export
(render_ts_png, render_cal_png, render_solid_cal_png, render_cv_png,
render_assay_curve, and CV's inline plot builder)."""


_ORIGIN_RC = {
    "font.family":        "sans-serif",
    "font.size":          10,
    "axes.labelsize":     11,
    "axes.titlesize":     12,
    "xtick.labelsize":    10,
    "ytick.labelsize":    10,
    "legend.fontsize":    9,
    "legend.frameon":     True,
    "legend.framealpha":  1.0,
    "legend.edgecolor":   "black",
    "legend.fancybox":    False,
    "lines.linewidth":    1.5,
    "axes.linewidth":     1.0,
    "axes.edgecolor":     "black",
    "xtick.major.width":  1.0,
    "ytick.major.width":  1.0,
    "xtick.major.size":   5,
    "ytick.major.size":   5,
    "xtick.minor.width":  0.8,
    "ytick.minor.width":  0.8,
    "xtick.minor.size":   3,
    "ytick.minor.size":   3,
    "xtick.direction":    "in",
    "ytick.direction":    "in",
    "xtick.top":          True,
    "ytick.right":        True,
    "axes.grid":          False,
    "figure.facecolor":   "white",
    "axes.facecolor":     "white",
    "savefig.facecolor":  "white",
}

_MINIMAL_RC = {
    "font.family":       "sans-serif",
    "font.size":         8,
    "axes.labelsize":    9,
    "axes.titlesize":    10,
    "xtick.labelsize":   8,
    "ytick.labelsize":   8,
    "legend.fontsize":   7,
    "legend.framealpha": 0.9,
    "lines.linewidth":   1.2,
    "axes.linewidth":    0.8,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "xtick.major.size":  3,
    "ytick.major.size":  3,
    "figure.facecolor":  "white",
    "axes.facecolor":    "white",
    "savefig.facecolor": "white",
}


def _apply_spine_style(ax, style: str) -> None:
    """Apply spine / tick style based on export style name."""
    if style == "origin":
        ax.minorticks_on()
        # all four spines already visible by default; ensure they're black
        for spine in ax.spines.values():
            spine.set_linewidth(1.0)
            spine.set_color("black")
    else:
        ax.spines[["top", "right"]].set_visible(False)
