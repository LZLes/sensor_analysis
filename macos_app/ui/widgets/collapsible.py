"""Tiny shared helper for collapsible QGroupBox sections — used across the
Amperometry/Solid-State/Cyclic Voltammetry tabs to keep secondary controls
(channel assignment, autodetect, dilution calculator, column mapping, ...)
out of the way until the user actually needs them, instead of every group
being permanently expanded and stacked one under another."""

from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QWidget


def make_collapsible(box: QGroupBox, content: QWidget, expanded: bool = False) -> None:
    """box must already contain `content` in its own layout. Toggling the
    group's checkbox shows/hides `content`; box itself stays visible as a
    slim titled header when collapsed."""
    box.setCheckable(True)
    box.setChecked(expanded)
    content.setVisible(expanded)
    box.toggled.connect(content.setVisible)
