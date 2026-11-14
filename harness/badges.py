"""Embeddable SVG badges (shields-style flat) + copy-paste markdown snippets."""
from __future__ import annotations

from xml.sax.saxutils import escape

COLORS = {"pass": "#2ea043", "partial": "#d29922", "fail": "#cf222e", "untested": "#6e7681"}

CHAR_W = 6.9  # ~11px Verdana bold average glyph width
PAD = 7


def _w(text: str) -> float:
    return round(PAD * 2 + len(text) * CHAR_W, 1)


def badge_svg(status: str, tool_count: int | None, call_s: float | None, stage: str | None) -> str:
    label = "mcp-health"
    if status == "pass":
        ms = f"{int((call_s or 0) * 1000)}ms" if call_s is not None else "ok"
        value = f"PASS · {tool_count if tool_count is not None else '?'} tools · {ms}"
    elif status == "partial":
        value = f"PARTIAL · {stage or 'degraded'}"
    elif status == "fail":
        value = f"FAIL · {stage or 'broken'}"
    else:
        value = "UNTESTED"
    color = COLORS.get(status, "#6e7681")
    lw, rw = _w(escape(label)), _w(escape(value))
    total = lw + rw
    mid = lw
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{escape(label)}: {escape(value)}">
  <title>{escape(label)}: {escape(value)}</title>
  <linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
  <clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{total}" height="20" fill="#555"/>
    <rect x="{mid}" width="{rw}" height="20" fill="{color}"/>
    <rect width="{total}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11" font-weight="bold">
    <text x="{mid / 2:.1f}" y="14" fill="#010101" fill-opacity=".3">{escape(label)}</text>
    <text x="{mid / 2:.1f}" y="13">{escape(label)}</text>
    <text x="{mid + rw / 2:.1f}" y="14" fill="#010101" fill-opacity=".3">{escape(value)}</text>
    <text x="{mid + rw / 2:.1f}" y="13">{escape(value)}</text>
  </g>
</svg>'''


def badge_markdown(base_url: str, slug: str) -> str:
    url = base_url.rstrip("/")
    return (f"[![MCP Health: PASS](<{url}/badges/{slug}.svg>)]"
            f"(<{url}/server/{slug}.html>)")
