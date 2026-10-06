"""Printable HTML report for a mode, built from its ModeSpec.report sections.

One self-contained page (images inlined as data: URIs) that the browser can
print or save as PDF. Section kinds are listed in web_app/modespec.py."""

from __future__ import annotations

import base64
import html
import time

from web_app.modespec import ReportSection

_CSS = """
body { font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
       color: #1d1d1f; max-width: 960px; margin: 24px auto; padding: 0 20px; }
h1 { font-size: 22px; margin: 0 0 2px; }
h2 { font-size: 15px; margin: 26px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #ddd; }
.meta { color: #666; margin-bottom: 18px; }
table { border-collapse: collapse; width: 100%; font-size: 12px; margin: 4px 0 8px; }
th, td { border: 1px solid #ddd; padding: 3px 6px; text-align: left; vertical-align: top; }
th { background: #f4f5f7; font-weight: 600; }
table.kv th { width: 30%; }
img { max-width: 100%; }
.note { color: #555; font-style: italic; }
.toolbar { position: sticky; top: 0; background: #fff; padding: 8px 0; text-align: right; }
.toolbar button { font: inherit; padding: 6px 14px; border-radius: 6px; border: 1px solid #bbb; background: #f7f7f7;
                  cursor: pointer; }
section { break-inside: avoid; }
@media print { .toolbar { display: none; } body { margin: 0; } h2 { break-after: avoid; } }
"""


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="note">No rows.</p>'
    cols = list(rows[0].keys())
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_esc(r.get(c))}</td>" for c in cols) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _section(kind: str, title: str, value) -> str:
    if kind == "kv":
        body = "<table class='kv'>" + "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>"
                                              for k, v in value.items()) + "</table>"
    elif kind == "table":
        body = _table(value)
    elif kind == "image":
        body = f'<img alt="{_esc(title)}" src="data:image/png;base64,{base64.b64encode(value).decode()}">'
    elif kind == "note":
        body = f'<p class="note">{_esc(value)}</p>'
    else:
        body = f"<p>{_esc(value)}</p>"
    return f"<section><h2>{_esc(title)}</h2>{body}</section>"


def render(mode_label: str, sections: list[ReportSection]) -> str:
    when = time.strftime("%d %b %Y, %H:%M")
    parts = "".join(_section(*s) for s in sections) or '<p class="note">Nothing to report yet.</p>'
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{_esc(mode_label)} report</title>"
            f"<style>{_CSS}</style></head><body>"
            "<div class='toolbar'><button onclick='window.print()'>Print / Save as PDF</button></div>"
            f"<h1>{_esc(mode_label)} report</h1><div class='meta'>Sensor Calibration Studio · {when}</div>"
            f"{parts}</body></html>")
