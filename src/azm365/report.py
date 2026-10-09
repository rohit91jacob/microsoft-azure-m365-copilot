"""Static results site: ``azm365 report --out site`` -> ``site/index.html`` + ``summary.json``.

Reads the gold marts from the DuckDB warehouse, the bronze manifests and the latest DQ run, and
renders one self-contained HTML page (inline SVG charts, no JavaScript, light/dark aware). Only
aggregates and short top-N tables are published - never bulk copies of the source data (see
DATA_LICENSE.md). Roadmap features are shown by ID with a link to the official roadmap.
"""

from __future__ import annotations

import html
import json
import logging
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from azm365 import __version__, bronze
from azm365.lake import Lake, open_lake
from azm365.settings import Settings

log = logging.getLogger(__name__)

ROADMAP_URL = "https://www.microsoft.com/microsoft-365/roadmap?id={}"
SERIES_1 = "var(--series-1)"
SERIES_2 = "var(--series-2)"


# --------------------------------------------------------------------------- formatting


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def fmt_num(v: Any, digits: int = 0) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and digits:
        return f"{v:,.{digits}f}"
    return f"{round(v):,}" if isinstance(v, int | float) else esc(v)


def fmt_pct(v: float | None, digits: int = 1) -> str:
    return "-" if v is None else f"{v * 100:+.{digits}f}%"


def fmt_share(v: float | None, digits: int = 1) -> str:
    return "-" if v is None else f"{v * 100:.{digits}f}%"


def fmt_price(v: float | None) -> str:
    if v is None:
        return "-"
    return f"${v:,.4f}" if v < 10 else f"${v:,.2f}"


def fmt_day(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, datetime):
        return v.astimezone(UTC).strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    return esc(v)


def fmt_month(v: Any) -> str:
    return v.strftime("%b %Y") if isinstance(v, date) else "-"


def table(headers: list[str], rows: list[list[str]], *, numeric: set[int] | None = None) -> str:
    numeric = numeric or set()
    head = "".join(
        f"<th{' class=num' if i in numeric else ''}>{esc(h)}</th>" for i, h in enumerate(headers)
    )
    body = "".join(
        "<tr>"
        + "".join(f"<td{' class=num' if i in numeric else ''}>{c}</td>" for i, c in enumerate(r))
        + "</tr>"
        for r in rows
    )
    if not rows:
        body = f'<tr><td colspan="{len(headers)}" class="muted">No rows yet.</td></tr>'
    return (
        f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    )


# --------------------------------------------------------------------------- SVG charts


def hbar_chart(rows: list[tuple[str, float, str]], *, title: str, ref: float | None = None) -> str:
    """Horizontal bars: (label, value, tooltip). One series, so no legend; ``ref`` draws a rule."""
    if not rows:
        return ""
    w, label_w, bar_h, gap = 720, 230, 16, 6
    h = len(rows) * (bar_h + gap) + 24
    vmax = max(*(v for _, v, _ in rows), ref or 0) or 1
    scale = (w - label_w - 70) / vmax
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}" class="chart">']
    for i, (label, v, tip) in enumerate(rows):
        y = i * (bar_h + gap) + 4
        bw = max(v * scale, 1)
        parts.append(
            f'<g class="mark"><title>{esc(tip)}</title>'
            f'<rect x="0" y="{y - 2}" width="{w}" height="{bar_h + 4}" class="hit"/>'
            f'<text x="{label_w - 8}" y="{y + bar_h - 4}" text-anchor="end" class="lbl">{esc(label)}</text>'
            f'<rect x="{label_w}" y="{y}" width="{bw:.1f}" height="{bar_h}" rx="3" fill="{SERIES_1}"/>'
            f'<text x="{label_w + bw + 6:.1f}" y="{y + bar_h - 4}" class="val">{esc(tip.split(": ")[-1])}</text></g>'
        )
    if ref is not None:
        x = label_w + ref * scale
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{h - 18}" class="ref"/>')
        parts.append(f'<text x="{x + 4:.1f}" y="{h - 6}" class="axis">reference = {ref:g}</text>')
    parts.append("</svg>")
    return "".join(parts)


def column_chart(
    points: list[tuple[str, float, float | None]],
    *,
    title: str,
    series: tuple[str, str] = ("value", ""),
    height: int = 200,
) -> str:
    """Vertical columns. ``(label, total, part)``: when part is given it is stacked at the base
    in series-2, with a 2px gap, and a legend names both series."""
    if not points:
        return ""
    w, pad_l, pad_b = 720, 44, 34
    n = len(points)
    step = (w - pad_l) / n
    bw = max(min(step - 2, 28), 2)
    vmax = max(t for _, t, _ in points) or 1
    plot_h = height - pad_b - 8
    parts = [f'<svg viewBox="0 0 {w} {height}" role="img" aria-label="{esc(title)}" class="chart">']
    for frac in (0.5, 1.0):
        y = 8 + plot_h * (1 - frac)
        parts.append(f'<line x1="{pad_l}" x2="{w}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        parts.append(
            f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" class="axis">{fmt_num(vmax * frac)}</text>'
        )
    label_every = max(1, n // 12)
    for i, (label, total, part) in enumerate(points):
        x = pad_l + i * step + (step - bw) / 2
        th = plot_h * total / vmax
        tip = f"{label}: {fmt_num(total)} {series[0]}"
        if part is not None:
            tip += f", of which {fmt_num(part)} {series[1]}"
        parts.append(f'<g class="mark"><title>{esc(tip)}</title>')
        parts.append(
            f'<rect x="{pad_l + i * step:.1f}" y="8" width="{step:.1f}" height="{plot_h}" class="hit"/>'
        )
        if part is not None and total > 0:
            ph = plot_h * part / vmax
            parts.append(
                f'<rect x="{x:.1f}" y="{8 + plot_h - th:.1f}" width="{bw:.1f}" height="{max(th - ph - 2, 0):.1f}" rx="3" fill="{SERIES_1}"/>'
            )
            parts.append(
                f'<rect x="{x:.1f}" y="{8 + plot_h - ph:.1f}" width="{bw:.1f}" height="{ph:.1f}" fill="{SERIES_2}"/>'
            )
        else:
            parts.append(
                f'<rect x="{x:.1f}" y="{8 + plot_h - th:.1f}" width="{bw:.1f}" height="{th:.1f}" rx="3" fill="{SERIES_1}"/>'
            )
        parts.append("</g>")
        if i % label_every == 0:
            parts.append(
                f'<text x="{x + bw / 2:.1f}" y="{height - pad_b + 16}" text-anchor="middle" class="axis">{esc(label)}</text>'
            )
    parts.append(
        f'<line x1="{pad_l}" x2="{w}" y1="{8 + plot_h}" y2="{8 + plot_h}" class="base"/></svg>'
    )
    legend = ""
    if points and points[0][2] is not None:
        legend = (
            '<div class="legend">'
            f'<span><i style="background:{SERIES_2}"></i>{esc(series[1])}</span>'
            f'<span><i style="background:{SERIES_1}"></i>other {esc(series[0])}</span></div>'
        )
    return legend + "".join(parts)


def sparkline(values: list[int], *, title: str) -> str:
    if not values:
        return ""
    w, h = 260, 48
    vmax = max(values) or 1
    step = w / max(len(values) - 1, 1)
    pts = " ".join(f"{i * step:.1f},{h - 4 - (h - 8) * v / vmax:.1f}" for i, v in enumerate(values))
    return (
        f'<svg viewBox="0 0 {w} {h}" class="spark" role="img" aria-label="{esc(title)}">'
        f"<title>{esc(title)}</title>"
        f'<polyline points="{pts}" fill="none" stroke="{SERIES_1}" stroke-width="2" stroke-linejoin="round"/></svg>'
    )


# --------------------------------------------------------------------------- data access


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    rel = con.sql(sql)
    cols = rel.columns
    return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]


def _has(con: duckdb.DuckDBPyConnection, schema: str, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [schema, name],
        ).fetchone()[0]
    )


def collect(
    con: duckdb.DuckDBPyConnection, manifests: list[dict[str, Any]], dq: dict | None
) -> dict[str, Any]:
    d: dict[str, Any] = {}
    synthetic = set()
    for m in manifests:
        synthetic.add(bool(m.get("synthetic")))
    d["synthetic"] = True in synthetic
    lineage: dict[tuple[str, str], dict[str, Any]] = {}
    for m in manifests:
        k = (m["source"], m["scope"])
        e = lineage.setdefault(
            k,
            {
                "live": 0,
                "wayback": 0,
                "first": m["snapshot_ts"],
                "last": m["snapshot_ts"],
                "items": 0,
            },
        )
        e[m["origin"]] += 1
        e["first"] = min(e["first"], m["snapshot_ts"])
        if m["snapshot_ts"] >= e["last"]:
            e["last"], e["items"], e["complete"] = m["snapshot_ts"], m["item_count"], m["complete"]
    d["lineage"] = lineage

    d["price_summary"] = _rows(
        con,
        "SELECT * FROM marts.mart_price_change_summary ORDER BY scope, to_snapshot_ts, from_snapshot_ts",
    )
    d["price_changes_latest"] = _rows(
        con,
        """
        SELECT * FROM marts.mart_price_changes
        WHERE to_snapshot_ts = (SELECT max(to_snapshot_ts) FROM marts.mart_price_changes)
          AND price_type = 'Consumption'
          AND meter_name NOT LIKE '%Spot%' AND meter_name NOT LIKE '%Low Priority%'
        ORDER BY abs(pct_change) DESC NULLS LAST, service_name, meter_name LIMIT 15
        """,
    )
    d["price_change_totals"] = _rows(
        con,
        """
        SELECT count(*) AS changes, count(*) FILTER (WHERE direction = 'increase') AS increases,
               count(*) FILTER (WHERE direction = 'decrease') AS decreases,
               min(from_snapshot_ts) AS first_ts, max(to_snapshot_ts) AS last_ts
        FROM marts.mart_price_changes
        """,
    )[0]
    d["price_points_latest"] = (
        con.sql("SELECT count(*) FROM intermediate.int_price_latest").fetchone()[0]
        if _has(con, "intermediate", "int_price_latest")
        else None
    )
    d["vm_cheapest"] = _rows(
        con,
        """
        SELECT * FROM marts.mart_cheapest_region
        WHERE service_name = 'Virtual Machines' AND product_name NOT LIKE '%Windows%'
          AND sku_name NOT LIKE '%Spot%' AND sku_name NOT LIKE '%Low Priority%'
        ORDER BY cheapest_price
        """,
    )
    d["vm_index"] = _rows(
        con,
        "SELECT * FROM marts.mart_region_price_index WHERE service_name = 'Virtual Machines' ORDER BY price_index, arm_region_name",
    )
    d["service_index_summary"] = _rows(
        con,
        """
        SELECT service_name, count(*) AS regions,
               arg_min(arm_region_name, price_index) AS cheapest_region, min(price_index) AS cheapest_index,
               arg_max(arm_region_name, price_index) AS priciest_region, max(price_index) AS priciest_index,
               sum(meters_compared) AS meters
        FROM marts.mart_region_price_index GROUP BY service_name ORDER BY service_name
        """,
    )

    d["roadmap_status"] = (
        _rows(
            con,
            """
        WITH f AS (SELECT DISTINCT feature_id, status, mentions_copilot FROM intermediate.int_roadmap_latest)
        SELECT status, count(*) AS features, count(*) FILTER (WHERE mentions_copilot) AS copilot
        FROM f GROUP BY status ORDER BY features DESC
        """,
        )
        if _has(con, "intermediate", "int_roadmap_latest")
        else []
    )
    d["roadmap_latest"] = (
        _rows(
            con,
            """
        SELECT max(snapshot_ts) AS snapshot_ts, count(DISTINCT feature_id) AS features,
               count(DISTINCT feature_id) FILTER (WHERE mentions_copilot) AS copilot
        FROM intermediate.int_roadmap_latest
        """,
        )[0]
        if _has(con, "intermediate", "int_roadmap_latest")
        else {}
    )
    d["roadmap_products"] = _rows(
        con,
        """
        SELECT product, sum(features) AS features, sum(copilot_features) AS copilot
        FROM marts.mart_roadmap_cadence WHERE product <> '(untagged)'
        GROUP BY product ORDER BY features DESC, product LIMIT 12
        """,
    )
    d["copilot_ga"] = _rows(
        con,
        """
        SELECT ga_month, sum(copilot_features) AS copilot, sum(features) AS features
        FROM marts.mart_roadmap_cadence
        WHERE product = 'Microsoft Copilot (Microsoft 365)' AND ga_month IS NOT NULL
          AND status <> 'Launched'
        GROUP BY ga_month ORDER BY ga_month
        """,
    )
    d["monthly_additions"] = _rows(
        con, "SELECT * FROM marts.mart_roadmap_monthly_additions ORDER BY created_month"
    )
    d["slippage_pairs"] = (
        _rows(
            con,
            """
        WITH h AS (
            SELECT prev_snapshot_ts AS from_ts, snapshot_ts AS to_ts, feature_id, mentions_copilot,
                   ga_month > prev_ga_month AS slipped, ga_month < prev_ga_month AS pulled_in,
                   date_diff('month', prev_ga_month, ga_month) AS moved
            FROM intermediate.int_roadmap_feature_history
            WHERE prev_ga_month IS NOT NULL AND ga_month IS NOT NULL
        )
        SELECT from_ts, to_ts, count(*) AS compared,
               count(*) FILTER (WHERE slipped) AS slipped, count(*) FILTER (WHERE pulled_in) AS pulled_in,
               avg(moved) FILTER (WHERE slipped) AS avg_slip,
               count(*) FILTER (WHERE mentions_copilot) AS copilot_compared,
               count(*) FILTER (WHERE mentions_copilot AND slipped) AS copilot_slipped
        FROM h GROUP BY from_ts, to_ts ORDER BY to_ts, from_ts
        """,
        )
        if _has(con, "intermediate", "int_roadmap_feature_history")
        else []
    )
    d["slippage_products"] = _rows(
        con,
        """
        SELECT product, sum(features_compared) AS compared, sum(features_slipped) AS slipped,
               sum(features_pulled_in) AS pulled_in,
               sum(features_slipped)::DOUBLE / nullif(sum(features_compared), 0) AS slip_rate
        FROM marts.mart_roadmap_slippage_by_product
        GROUP BY product HAVING sum(features_compared) >= 20
        ORDER BY slip_rate DESC, product LIMIT 10
        """,
    )
    d["slips_recent"] = _rows(
        con,
        """
        SELECT feature_id, products, from_ga_label, to_ga_label, months_moved, to_status, to_snapshot_ts
        FROM marts.mart_roadmap_slippage
        WHERE mentions_copilot
        ORDER BY to_snapshot_ts DESC, months_moved DESC, feature_id LIMIT 12
        """,
    )
    d["repos"] = _rows(con, "SELECT * FROM marts.mart_repo_summary ORDER BY commits_52w DESC")
    weekly: dict[str, list[int]] = defaultdict(list)
    for r in _rows(
        con,
        "SELECT repo, week_start, commits FROM marts.mart_repo_weekly_throughput ORDER BY repo, week_start",
    ):
        weekly[r["repo"]].append(int(r["commits"]))
    d["weekly"] = dict(weekly)
    d["dq"] = dq
    return d


# --------------------------------------------------------------------------- page


CSS = """
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f3f2ef;--text-primary:#0b0b0b;
--text-secondary:#52514e;--text-muted:#77756f;--rule:#dcdad4;--series-1:#2a78d6;--series-2:#eb6834;
--good:#008300;--critical:#c62f2e;--banner:#fff4d6}
@media (prefers-color-scheme:dark){:root .viz-root{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#242422;
--text-primary:#fff;--text-secondary:#c3c2b7;--text-muted:#9b9a92;--rule:#3a3a37;--series-1:#3987e5;
--series-2:#d95926;--good:#3fae3f;--critical:#e66767;--banner:#3b3220}}
body{margin:0;background:var(--surface-1)}
.viz-root{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text-primary);
background:var(--surface-1);max-width:1080px;margin:0 auto;padding:24px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:36px 0 8px;border-bottom:1px solid var(--rule);padding-bottom:4px}
h3{font-size:16px;margin:22px 0 6px}p,li{color:var(--text-secondary)}a{color:var(--series-1)}
.muted{color:var(--text-muted)}.banner{background:var(--banner);padding:10px 14px;border-radius:6px;margin:12px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0}
.tile{background:var(--surface-2);border-radius:8px;padding:12px 14px}.tile b{display:block;font-size:26px}
.tile span{color:var(--text-secondary);font-size:13px}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0 10px}
th,td{padding:5px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
th{color:var(--text-secondary);font-weight:600}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.chart{width:100%;height:auto;margin:6px 0}.spark{width:260px;height:48px}
.chart text{font-size:12px;fill:var(--text-secondary)}.chart .val{fill:var(--text-primary)}
.chart .axis{fill:var(--text-muted);font-size:11px}.grid{stroke:var(--rule);stroke-width:1}
.base{stroke:var(--text-muted);stroke-width:1}.ref{stroke:var(--text-muted);stroke-dasharray:4 3}
.hit{fill:transparent}.mark:hover .hit{fill:var(--surface-2)}
.legend{display:flex;gap:16px;font-size:13px;color:var(--text-secondary)}
.legend i{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.pass{color:var(--good)}.fail{color:var(--critical)}
footer{margin-top:40px;font-size:13px;color:var(--text-muted);border-top:1px solid var(--rule);padding-top:12px}
"""


def render(d: dict[str, Any], generated_at: datetime) -> str:
    s: list[str] = []
    s.append(
        "<h1>Azure prices, Microsoft 365 / Copilot roadmap and Microsoft OSS activity</h1>"
        f'<p class="muted">Generated {generated_at.strftime("%Y-%m-%d %H:%M UTC")} by azm365 {esc(__version__)}. '
        "Built from public, credential-free sources by a pipeline written for Azure Functions, ADLS Gen2 and dbt. "
        "This page was built by a local/CI run of that pipeline, not by an Azure deployment.</p>"
    )
    if d["synthetic"]:
        s.append(
            '<div class="banner"><b>SIMULATED DATA.</b> This page was built from the repository\'s '
            "test fixtures (deterministic fake API responses). None of the numbers below are real.</div>"
        )
    s.append(
        '<div class="banner">Microsoft 365 / Copilot <b>usage</b> telemetry is not public. Nothing here measures '
        "adoption or usage: the roadmap section counts <i>announced features</i> and how their dates move.</div>"
    )
    rl = d.get("roadmap_latest") or {}
    pt = d["price_change_totals"]
    vscode = next((r for r in d["repos"] if r["repo"] == "microsoft/vscode"), None)
    tiles = [
        (
            fmt_num(rl.get("features")),
            f"features on the M365 roadmap ({fmt_day(rl.get('snapshot_ts'))})",
        ),
        (
            fmt_share((rl.get("copilot") or 0) / rl["features"]) if rl.get("features") else "-",
            "of them mention Copilot",
        ),
        (fmt_num(d["price_points_latest"]), "Azure retail price points in the latest snapshots"),
        (fmt_num(pt["changes"]), f"price changes observed since {fmt_day(pt['first_ts'])}"),
        (
            fmt_num(vscode["commits_52w"]) if vscode else "-",
            "commits to microsoft/vscode in 52 weeks",
        ),
    ]
    s.append(
        '<div class="tiles">'
        + "".join(f'<div class="tile"><b>{v}</b><span>{esc(t)}</span></div>' for v, t in tiles)
        + "</div>"
    )

    # ---- FinOps
    s.append("<h2>Azure retail prices (FinOps)</h2>")
    s.append(
        "<p>Source: the Azure Retail Prices API (USD, pay-as-you-go list prices, no discounts). "
        "Live snapshots are daily. Older history comes from Internet Archive captures of the same API request, "
        "which hold only the first 1,000-item page of the Spain Central catalogue. Price points are compared key by key; "
        "a price point missing from a page is never counted as removed.</p>"
    )
    s.append("<h3>Changes between snapshots</h3>")
    s.append(
        table(
            ["Scope", "From", "To", "Compared", "Changed", "Up", "Down", "Median change"],
            [
                [
                    esc(r["scope"]),
                    fmt_day(r["from_snapshot_ts"])
                    + f' <span class="muted">{esc(r["from_origin"])}</span>',
                    fmt_day(r["to_snapshot_ts"])
                    + f' <span class="muted">{esc(r["to_origin"])}</span>',
                    fmt_num(r["price_points_compared"]),
                    fmt_num(r["price_points_changed"]),
                    fmt_num(r["increases"]),
                    fmt_num(r["decreases"]),
                    fmt_pct(r["median_pct_change_of_changed"]),
                ]
                for r in d["price_summary"]
            ],
            numeric={3, 4, 5, 6, 7},
        )
    )
    s.append(
        "<h3>Largest pay-as-you-go changes in the latest comparison</h3>"
        "<p>Spot and Low Priority meters (which move often) and reservations (priced per term) are left out here; "
        "they are in <code>mart_price_changes</code>.</p>"
    )
    s.append(
        table(
            ["Service", "Product / meter", "Region", "Type", "Unit", "Old", "New", "Change"],
            [
                [
                    esc(r["service_name"]),
                    f'{esc(r["product_name"])}<br><span class="muted">{esc(r["meter_name"])}</span>',
                    esc(r["arm_region_name"]),
                    esc(
                        r["price_type"]
                        + (f" {r['reservation_term']}" if r["reservation_term"] else "")
                    ),
                    esc(r["unit_of_measure"]),
                    fmt_price(r["old_price"]),
                    fmt_price(r["new_price"]),
                    fmt_pct(r["pct_change"]),
                ]
                for r in d["price_changes_latest"]
            ],
            numeric={5, 6, 7},
        )
    )
    s.append("<h3>Cheapest region for the tracked VM sizes (Linux, pay-as-you-go, per hour)</h3>")
    s.append(
        table(
            ["VM size", "Regions", "Cheapest", "Price", "Median", "Priciest", "Price", "Spread"],
            [
                [
                    esc(r["sku_name"]),
                    fmt_num(r["n_regions"]),
                    esc(r["cheapest_region"]),
                    fmt_price(r["cheapest_price"]),
                    fmt_price(r["median_price"]),
                    esc(r["priciest_region"]),
                    fmt_price(r["priciest_price"]),
                    fmt_share(r["spread_pct"], 0),
                ]
                for r in d["vm_cheapest"]
            ],
            numeric={1, 3, 4, 6, 7},
        )
    )
    vm = d["vm_index"]
    if vm:
        shown = vm[:10] + ([] if len(vm) <= 20 else vm[-10:])
        s.append(
            f"<h3>Virtual Machines region price index (East US = 1.00; {len(vm)} regions; cheapest 10 and priciest 10)</h3>"
            "<p>Geometric mean of regional price / East US price over every pay-as-you-go VM meter priced in both.</p>"
        )
        s.append(
            hbar_chart(
                [
                    (
                        f"{r['arm_region_name']}",
                        r["price_index"],
                        f"{r['arm_region_name']} ({r['meters_compared']} meters): {r['price_index']:.3f}",
                    )
                    for r in shown
                ],
                title="VM price index by region",
                ref=1.0,
            )
        )
    s.append("<h3>Cheapest and priciest region per service</h3>")
    s.append(
        table(
            ["Service", "Regions", "Meters compared", "Cheapest", "Index", "Priciest", "Index"],
            [
                [
                    esc(r["service_name"]),
                    fmt_num(r["regions"]),
                    fmt_num(r["meters"]),
                    esc(r["cheapest_region"]),
                    f"{r['cheapest_index']:.3f}",
                    esc(r["priciest_region"]),
                    f"{r['priciest_index']:.3f}",
                ]
                for r in d["service_index_summary"]
            ],
            numeric={1, 2, 4, 6},
        )
    )

    # ---- Roadmap
    s.append("<h2>Microsoft 365 roadmap: Copilot feature cadence and slippage</h2>")
    s.append(
        "<p>Source: the public Microsoft 365 roadmap feed, one snapshot per day plus Internet Archive captures. "
        "A feature is a Copilot feature when its title or a product tag mentions Copilot. Slippage is a change "
        "of the planned GA month between two consecutive snapshots in which the feature appears.</p>"
    )
    s.append(
        table(
            ["Status (latest snapshot)", "Features", "Copilot features", "Copilot share"],
            [
                [
                    esc(r["status"]),
                    fmt_num(r["features"]),
                    fmt_num(r["copilot"]),
                    fmt_share(r["copilot"] / r["features"] if r["features"] else None),
                ]
                for r in d["roadmap_status"]
            ],
            numeric={1, 2, 3},
        )
    )
    if d["roadmap_products"]:
        s.append(
            "<h3>Features per product (latest snapshot; a feature with two product tags counts under both)</h3>"
        )
        s.append(
            hbar_chart(
                [
                    (
                        r["product"],
                        float(r["features"]),
                        f"{r['product']} ({fmt_num(r['copilot'])} mention Copilot): {fmt_num(r['features'])}",
                    )
                    for r in d["roadmap_products"]
                ],
                title="Roadmap features per product",
            )
        )
    if d["monthly_additions"]:
        pts = [
            (
                r["created_month"].strftime("%y-%m"),
                float(r["features_added"]),
                float(r["copilot_features_added"]),
            )
            for r in d["monthly_additions"]
        ]
        s.append(
            "<h3>Features added per month (roadmap <code>created</code> date, every snapshot combined)</h3>"
        )
        s.append(
            column_chart(
                pts,
                title="Roadmap features added per month",
                series=("features", "Copilot features"),
            )
        )
    if d["copilot_ga"]:
        s.append("<h3>Not-yet-launched Microsoft 365 Copilot features by planned GA month</h3>")
        s.append(
            column_chart(
                [(fmt_month(r["ga_month"]), float(r["copilot"]), None) for r in d["copilot_ga"]],
                title="Copilot features by GA month",
                series=("Copilot features", ""),
            )
        )
    s.append("<h3>GA-date changes between snapshots</h3>")
    s.append(
        table(
            [
                "From",
                "To",
                "Compared",
                "Slipped",
                "Pulled in",
                "Slip rate",
                "Mean slip (months)",
                "Copilot slipped",
            ],
            [
                [
                    fmt_day(r["from_ts"]),
                    fmt_day(r["to_ts"]),
                    fmt_num(r["compared"]),
                    fmt_num(r["slipped"]),
                    fmt_num(r["pulled_in"]),
                    fmt_share(r["slipped"] / r["compared"] if r["compared"] else None),
                    fmt_num(r["avg_slip"], 1) if r["avg_slip"] is not None else "-",
                    f"{fmt_num(r['copilot_slipped'])} / {fmt_num(r['copilot_compared'])}",
                ]
                for r in d["slippage_pairs"]
            ],
            numeric={2, 3, 4, 5, 6, 7},
        )
    )
    s.append(
        "<h3>Products with the highest slip rate (summed over every snapshot pair, at least 20 comparisons)</h3>"
    )
    s.append(
        table(
            ["Product", "Compared", "Slipped", "Pulled in", "Slip rate"],
            [
                [
                    esc(r["product"]),
                    fmt_num(r["compared"]),
                    fmt_num(r["slipped"]),
                    fmt_num(r["pulled_in"]),
                    fmt_share(r["slip_rate"]),
                ]
                for r in d["slippage_products"]
            ],
            numeric={1, 2, 3, 4},
        )
    )
    s.append("<h3>Most recent Copilot GA-date changes</h3>")
    s.append(
        table(
            ["Feature", "Products", "GA before", "GA after", "Months", "Status", "Seen"],
            [
                [
                    f'<a href="{ROADMAP_URL.format(r["feature_id"])}">{r["feature_id"]}</a>',
                    esc(r["products"]),
                    esc(r["from_ga_label"]),
                    esc(r["to_ga_label"]),
                    f"{r['months_moved']:+d}",
                    esc(r["to_status"]),
                    fmt_day(r["to_snapshot_ts"]),
                ]
                for r in d["slips_recent"]
            ],
            numeric={4},
        )
    )

    # ---- Engineering
    s.append("<h2>Engineering throughput on public Microsoft repositories</h2>")
    s.append(
        "<p>Source: GitHub REST API. Commits are default-branch commits per week (GitHub participation statistics, 52 weeks). "
        "Merged PRs and time to merge come from the most recently updated closed pull requests (up to 300 per repo), "
        "so they cover the window shown, not the whole year.</p>"
    )
    s.append(
        table(
            [
                "Repository",
                "Stars",
                "Commits 52w",
                "Commits 12w",
                "Weekly commits",
                "PR window (days)",
                "Merged PRs",
                "Median hours to merge",
                "Bot share",
                "Stable releases 365d",
                "Latest release",
            ],
            [
                [
                    f'<a href="https://github.com/{esc(r["repo"])}">{esc(r["repo"])}</a>',
                    fmt_num(r["stars"]),
                    fmt_num(r["commits_52w"]),
                    fmt_num(r["commits_12w"]),
                    sparkline(
                        d["weekly"].get(r["repo"], []),
                        title=f"{r['repo']} weekly commits, 52 weeks",
                    ),
                    fmt_num(r["pr_window_days"], 1) if r["pr_window_days"] is not None else "-",
                    fmt_num(r["merged_prs_in_window"]),
                    fmt_num(r["median_hours_to_merge"], 1)
                    if r["median_hours_to_merge"] is not None
                    else "-",
                    fmt_share(r["bot_share_of_merged"], 0),
                    fmt_num(r["releases_365d"]),
                    f'{esc(r["latest_release_tag"])} <span class="muted">{fmt_day(r["latest_release_at"])}</span>',
                ]
                for r in d["repos"]
            ],
            numeric={1, 2, 3, 5, 6, 7, 8, 9},
        )
    )

    # ---- Lineage + DQ
    s.append("<h2>Snapshots and data quality</h2>")
    s.append(
        table(
            ["Source", "Scope", "Live", "Wayback", "First", "Latest", "Items (latest)", "Complete"],
            [
                [
                    esc(src),
                    esc(scope),
                    fmt_num(e["live"]),
                    fmt_num(e["wayback"]),
                    fmt_day(e["first"][:10]),
                    fmt_day(e["last"][:10]),
                    fmt_num(e["items"]),
                    "yes" if e.get("complete") else "partial",
                ]
                for (src, scope), e in sorted(d["lineage"].items())
            ],
            numeric={2, 3, 6},
        )
    )
    dq = d["dq"]
    if dq:
        counts = Counter(("pass" if r["passed"] else "fail", r["severity"]) for r in dq["results"])
        s.append(
            f"<p>Latest silver data-quality gate ({esc(dq['checked_at'])}): {counts[('pass', 'error')] + counts[('pass', 'warn')]} of "
            f"{len(dq['results'])} checks passed; {counts[('fail', 'error')]} error-level and {counts[('fail', 'warn')]} warning-level failures. "
            "Error-level failures stop the pipeline before dbt; dbt tests then gate every mart.</p>"
        )
        s.append(
            table(
                ["Check", "Table", "Severity", "Result", "Violations"],
                [
                    [
                        esc(r["name"]),
                        esc(r["table"]),
                        esc(r["severity"]),
                        '<span class="pass">pass</span>'
                        if r["passed"]
                        else '<span class="fail">FAIL</span>',
                        fmt_num(r["violations"]),
                    ]
                    for r in dq["results"]
                ],
                numeric={4},
            )
        )
    s.append(
        "<footer>Data: Azure Retail Prices API and the Microsoft 365 roadmap (Microsoft; public, used under the "
        "Microsoft Terms of Use, shown here only as aggregates and short excerpts with links); GitHub REST API; "
        "Internet Archive Wayback Machine captures. Not affiliated with or endorsed by Microsoft or GitHub. "
        'Code: MIT. <a href="https://github.com/rohit91jacob/microsoft-azure-m365-copilot">Source and methodology</a>.</footer>'
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Azure prices, M365 Copilot roadmap and Microsoft OSS activity</title>"
        f'<style>{CSS}</style></head><body><main class="viz-root">{"".join(s)}</main></body></html>'
    )


def summary(d: dict[str, Any], generated_at: datetime) -> dict[str, Any]:
    rl = d.get("roadmap_latest") or {}
    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "synthetic": d["synthetic"],
        "snapshots": {f"{k[0]}/{k[1]}": dict(v) for k, v in sorted(d["lineage"].items())},
        "roadmap": {
            "snapshot_ts": rl.get("snapshot_ts"),
            "features": rl.get("features"),
            "copilot_features": rl.get("copilot"),
            "status": {
                r["status"]: {"features": r["features"], "copilot": r["copilot"]}
                for r in d["roadmap_status"]
            },
            "slippage_pairs": d["slippage_pairs"],
        },
        "prices": {
            "price_points_latest": d["price_points_latest"],
            "changes": d["price_change_totals"],
            "change_summary": d["price_summary"],
            "vm_cheapest": [
                {
                    k: r[k]
                    for k in (
                        "sku_name",
                        "n_regions",
                        "cheapest_region",
                        "cheapest_price",
                        "median_price",
                        "priciest_region",
                        "priciest_price",
                    )
                }
                for r in d["vm_cheapest"]
            ],
            "service_index": d["service_index_summary"],
        },
        "repos": d["repos"],
        "dq": None
        if not d["dq"]
        else {
            "checked_at": d["dq"]["checked_at"],
            "passed": d["dq"]["passed"],
            "failed": [r["name"] for r in d["dq"]["results"] if not r["passed"]],
        },
    }


def build_site(settings: Settings, out: Path, *, lake: Lake | None = None) -> dict[str, Any]:
    lake = lake or open_lake(settings)
    manifests = bronze.list_manifests(lake)
    dq = (
        json.loads(lake.get("ops", "dq_results/latest.json"))
        if lake.exists("ops", "dq_results/latest.json")
        else None
    )
    if not settings.warehouse_path.is_file():
        raise FileNotFoundError(
            f"no warehouse at {settings.warehouse_path}; run `azm365 run` first"
        )
    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        con.execute("SET TimeZone = 'UTC'")
        data = collect(con, manifests, dq)
    finally:
        con.close()
    generated_at = datetime.now(UTC)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render(data, generated_at), encoding="utf-8")
    summ = summary(data, generated_at)
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    log.info("report: wrote %s", out / "index.html")
    return summ
