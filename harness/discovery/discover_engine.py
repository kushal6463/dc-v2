"""Phase 1 — relationship discovery engine.

Generates causal HYPOTHESES (not proof) over the admissible candidate edges:
  1. deseasonalize each series (STL) -> residuals; flag stationarity (ADF)
  2. per edge: lagged cross-correlation, Granger F-test, mutual information
  3. Benjamini-Hochberg FDR across the whole batch
  4. stability selection: keep only edges that survive a majority of subwindows
  5. conditioning (poor-man's PCMCI): re-test each survivor's partial correlation
     CONDITIONED on the target's other parents + its own past — an edge that
     collapses once common drivers are removed was a confound, and is dropped.
     (Not full PCMCI+; a single conditioning pass on the discovered parent set.)

Two modes:
  --mode synthetic   self-test on planted causal structure (proves the engine
                     recovers true lagged edges and FDR-rejects trend/season
                     spurious pairs). Runs with NO API/data dependency.
  --mode api         ingest real series via the product API (data plane) for the
                     candidate edges and discover. Requires the API serving values.

    python tools/discover_engine.py --mode synthetic
    python tools/discover_engine.py --mode api --tenant rare_seeds
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import re
import urllib.request

import numpy as np
import pandas as pd
from sklearn.feature_selection import mutual_info_regression
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.stattools import adfuller, grangercausalitytests

from harness.kg.config import REPO_ROOT

MAX_LAG = 14
GRANGER_LAG = 7

# All engine inputs/outputs live under the repo's data dir (tenant-scoped), so a
# run from any working directory writes the same canonical files and never
# clobbers a different tenant's CSV.
DATA_DIR = REPO_ROOT / "data"


def _data_path(name: str) -> str:
    """Absolute path under ``REPO_ROOT/data`` (created on demand) for ``name``."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return str(DATA_DIR / name)


def _series_cache_path(tenant: str) -> str:
    """Absolute path to the tenant's series cache (``data/series/<tenant>.jsonl``)."""
    (DATA_DIR / "series").mkdir(parents=True, exist_ok=True)
    return str(DATA_DIR / "series" / f"{tenant}.jsonl")


# ---------------- preprocessing ----------------
def deseasonalize(s: pd.Series, period: int = 7) -> pd.Series:
    s = pd.Series(s).astype(float).interpolate().dropna()
    if len(s) < 2 * period + 12:
        return s - s.mean()
    try:                                             # keep the index: edges align on dates
        return pd.Series(STL(s.values, period=period, robust=True).fit().resid, index=s.index)
    except Exception:
        return s - s.rolling(period, min_periods=1).mean()


def adf_stationary(x: np.ndarray) -> bool:
    try:
        return adfuller(x, autolag="AIC")[1] < 0.05
    except Exception:
        return False


# ---------------- discovery methods (on residuals) ----------------
def best_lag_corr(x: pd.Series, y: pd.Series, max_lag: int = MAX_LAG):
    best_k, best_c = 0, 0.0
    for k in range(1, max_lag + 1):
        df = pd.concat([x.shift(k), y], axis=1).dropna()
        if len(df) < 30:
            continue
        c = df.iloc[:, 0].corr(df.iloc[:, 1])
        if pd.notna(c) and abs(c) > abs(best_c):
            best_k, best_c = k, c
    return best_k, best_c


def granger_min_p(x: pd.Series, y: pd.Series, maxlag: int = GRANGER_LAG) -> float:
    df = pd.concat([y, x], axis=1).dropna()      # does x granger-cause y?
    if len(df) < 3 * maxlag + 20:
        return 1.0
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            res = grangercausalitytests(df.values, maxlag=maxlag)
        return min(res[l][0]["ssr_ftest"][1] for l in res)
    except Exception:
        return 1.0


def mutual_info(x: pd.Series, y: pd.Series, lag: int) -> float:
    df = pd.concat([x.shift(max(lag, 1)), y], axis=1).dropna()
    if len(df) < 30:
        return 0.0
    return float(mutual_info_regression(df.iloc[:, [0]].values, df.iloc[:, 1].values,
                                        random_state=0)[0])


def bh_fdr(pvals: list[float], q: float = 0.05) -> np.ndarray:
    p = np.asarray(pvals)
    n = len(p)
    order = np.argsort(p)
    keep = np.zeros(n, bool)
    kmax = 0
    for i, idx in enumerate(order, 1):
        if p[idx] <= i / n * q:
            kmax = i
    keep[order[:kmax]] = True
    return keep


def test_edge(rx: pd.Series, ry: pd.Series) -> dict:
    k, c = best_lag_corr(rx, ry)
    gp = granger_min_p(rx, ry)
    mi = mutual_info(rx, ry, k)
    disc = 0.5 * abs(c) + 0.4 * (1 - gp) + 0.1 * min(mi, 1.0)
    return {"lag": k, "corr": round(c, 3), "granger_p": round(gp, 4),
            "mi": round(mi, 3), "discovery_score": round(disc, 3)}


def _residualize(target: np.ndarray, controls: np.ndarray) -> np.ndarray:
    """Residual of `target` after linearly regressing out `controls` (OLS + intercept)."""
    if controls is None or controls.shape[1] == 0:
        return target - target.mean()
    A = np.column_stack([np.ones(len(target)), controls])
    coef, *_ = np.linalg.lstsq(A, target, rcond=None)
    return target - A @ coef


def partial_corr(x: np.ndarray, y: np.ndarray, Z: np.ndarray) -> float:
    """Correlation of x and y AFTER removing everything explained by Z — the
    conditioning step that separates a real link from a common-cause confound."""
    rx, ry = _residualize(x, Z), _residualize(y, Z)
    if np.std(rx) < 1e-9 or np.std(ry) < 1e-9:
        return 0.0
    c = np.corrcoef(rx, ry)[0, 1]
    return 0.0 if np.isnan(c) else float(c)


def conditional_edge(resid: dict, grains: dict, src: str, dst: str, lag: int,
                     parents: list, min_len: int = 30):
    """Partial correlation of src(t-lag) -> dst(t), CONDITIONED on dst's other parents
    (each at its own lag) + dst's own past. Returns the conditioned correlation, or None
    if there isn't enough joint history. A confound collapses this toward 0."""
    cols = {"y": resid[dst], "x": resid[src].shift(max(lag, 1))}
    for i, (p, plag) in enumerate(parents):
        if p in (src, dst):
            continue
        cols[f"z{i}"] = resid[p].shift(max(int(plag or 1), 1))
    cols["yauto"] = resid[dst].shift(1)                  # control autocorrelation
    df = pd.concat(cols, axis=1).dropna()
    if len(df) < min_len:
        return None
    Z = df[[c for c in df.columns if c not in ("y", "x")]].to_numpy()
    return round(partial_corr(df["x"].to_numpy(), df["y"].to_numpy(), Z), 3)


def stability_ratio(rx: pd.Series, ry: pd.Series, k: int = 4, min_len: int = 35):
    """Fraction of K rolling sub-windows in which the edge stays significant. A
    one-period fluke fails this; a stable mechanism survives most windows. Returns
    None when the series is too short to window meaningfully."""
    n = len(rx)
    if n < 2 * min_len:
        return None
    win = max(min_len, n // k)
    hits = tested = 0
    for i in range(0, n - min_len + 1, win):
        df = pd.concat([rx.iloc[i:i + win], ry.iloc[i:i + win]], axis=1).dropna()
        if len(df) < min_len:
            continue
        tested += 1
        r = test_edge(df.iloc[:, 0], df.iloc[:, 1])
        if r["granger_p"] < 0.1 and r["discovery_score"] > 0.3:
            hits += 1
    return round(hits / tested, 2) if tested else None


# ---------------- synthetic self-test ----------------
def synthetic_panel(n: int = 900) -> tuple[pd.DataFrame, list]:
    rng = np.random.default_rng()
    t = np.arange(n)
    season = 1.2 * np.sin(2 * np.pi * t / 7) + 0.6 * np.sin(2 * np.pi * t / 365)
    trend = 0.01 * t

    def base():
        return trend + season

    nz = lambda s=1.0: rng.normal(0, s, n)
    # planted on RESIDUAL component: spend -> sessions (lag2) -> orders (lag1)
    spend_r = nz(2)
    sessions_r = 0.8 * np.roll(spend_r, 2) + nz(1)
    orders_r = 0.7 * np.roll(sessions_r, 1) + nz(1)
    unrelated_r = nz(1)            # shares trend/season only — a spurious trap
    df = pd.DataFrame({
        "spend":      base() + spend_r,
        "sessions":   base() + sessions_r,
        "orders":     base() + orders_r,
        "unrelated":  base() + unrelated_r,
    })
    planted = [("spend", "sessions", 2), ("sessions", "orders", 1)]
    return df, planted


def run_synthetic() -> None:
    df, planted = synthetic_panel()
    print("synthetic panel: 4 series x 900 days; planted spend->sessions(lag2), sessions->orders(lag1)")
    print("(all share weekly+annual seasonality + trend — the spurious trap)\n")

    # raw-level correlation (shows why we MUST deseasonalize)
    print("RAW-level correlation (unrelated looks linked due to shared trend/season):")
    print(f"   spend~unrelated raw corr = {df['spend'].corr(df['unrelated']):+.2f}   "
          f"sessions~unrelated raw corr = {df['sessions'].corr(df['unrelated']):+.2f}\n")

    resid = {c: pd.Series(deseasonalize(df[c])) for c in df}
    print("stationarity after STL (ADF p<0.05):",
          {c: adf_stationary(resid[c].dropna().values) for c in resid})

    names = list(df.columns)
    rows, pvals = [], []
    for s in names:
        for d in names:
            if s == d:
                continue
            r = test_edge(resid[s], resid[d])
            r["src"], r["dst"] = s, d
            rows.append(r); pvals.append(r["granger_p"])
    keep = bh_fdr(pvals, q=0.05)
    for r, k in zip(rows, keep):
        r["fdr_pass"] = bool(k)

    print("\nedge tests on RESIDUALS (FDR-controlled):")
    print(f"  {'edge':<22}{'lag':>4}{'corr':>8}{'granger_p':>11}{'mi':>7}{'score':>7}  FDR")
    for r in sorted(rows, key=lambda r: -r["discovery_score"]):
        mark = "KEEP" if r["fdr_pass"] and r["discovery_score"] > 0.3 else "drop"
        print(f"  {r['src']+' -> '+r['dst']:<22}{r['lag']:>4}{r['corr']:>8}"
              f"{r['granger_p']:>11}{r['mi']:>7}{r['discovery_score']:>7}  {mark}")

    found = {(r["src"], r["dst"]) for r in rows
             if r["fdr_pass"] and r["discovery_score"] > 0.3}
    hits = [(s, d) for s, d, _ in planted if (s, d) in found]
    spurious = [(r["src"], r["dst"]) for r in rows
                if r["dst"] == "unrelated" and r["fdr_pass"] and r["discovery_score"] > 0.3]
    print(f"\nSELF-TEST: planted edges recovered {len(hits)}/{len(planted)} "
          f"{hits} ; spurious-into-unrelated kept = {len(spurious)} {spurious}")
    print("PASS" if len(hits) == len(planted) and not spurious else "CHECK")


# ---------------- API ingestion (data plane) ----------------
def api_login(base: str, email: str, pw: str, tenant: str):
    import http.cookiejar
    import urllib.parse
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    body = json.dumps({"email": email, "password": pw}).encode()
    op.open(urllib.request.Request(f"{base}/api/v1/auth/login", body,
            {"Content-Type": "application/json"}), timeout=25)
    # super_admin selects the tenant via the tw_active_tenant cookie
    host = urllib.parse.urlparse(base).hostname
    cj.set_cookie(http.cookiejar.Cookie(
        0, "tw_active_tenant", tenant, None, False, host, True, False,
        "/", True, True, None, True, None, None, {}))
    return op


def _pick_col(cols: list[str], base: str) -> str | None:
    skip = {"date", "day", "week", "month", "period", "id", "name", "label"}
    cand = [c for c in cols if c.lower() not in skip]
    bn = base.replace("_", "").lower()
    for c in cand:                                   # exact
        if c.replace("_", "").lower() == bn:
            return c
    for c in cand:                                   # substring either way
        cn = c.replace("_", "").lower()
        if bn and len(bn) >= 4 and (bn in cn or cn in bn):
            return c
    return None                                      # STRICT: only a real match


# ---- shape-agnostic, grain-aware series extraction (every dashboard payload) ----
DATEKEYS = ("date", "day", "week", "month", "period", "ds")
GRAIN_GRAN = {"daily": "daily", "weekly": "weekly", "monthly": "monthly",
              "quarterly": "monthly", "annual": "monthly"}
GRAIN_MINPTS = {"daily": 60, "weekly": 40, "monthly": 18, "quarterly": 10, "annual": 8}
GRAIN_FREQ = {"daily": "D", "weekly": "W-MON", "monthly": "MS", "quarterly": "QS", "annual": "YS"}
GRAIN_PERIOD = {"daily": 7, "weekly": 52, "monthly": 12, "quarterly": 4, "annual": 1}
GRAIN_ORDER = {"daily": 0, "weekly": 1, "monthly": 2, "quarterly": 3, "annual": 4}


def _pts_from_list(lst) -> list:
    """[{date-ish, value}] -> [(date10, float)] ; ignores non-date / non-numeric rows."""
    out = []
    for p in lst or []:
        if not isinstance(p, dict):
            continue
        dk = next((k for k in p if k.lower() in DATEKEYS), None)
        v = p.get("value")
        if dk is None or v is None:
            continue
        try:
            out.append((str(p[dk])[:10], float(v)))
        except (TypeError, ValueError):
            continue
    return out


NON_ADDITIVE_UNITS = {"percent", "ratio", "index", "days"}


def is_additive(r: dict) -> bool:
    """Can per-segment series be SUMMED into a total? True for counts/currency totals;
    False for rates/ratios/averages/percents (summing channel ROAS is meaningless)."""
    if (r.get("aggregation") or "") in ("rate", "ratio", "avg", "median"):
        return False
    if r.get("is_derived") == "yes":
        return False
    return r.get("unit", "") not in NON_ADDITIVE_UNITS


def _series_points(d, additive: bool = True) -> list:
    """Pull ONE dated series from ANY metric/chart payload shape. A multi-series
    breakdown is summed by date ONLY when the metric is additive — summing the
    segments of a rate/ratio/average would invent a meaningless series, so we skip it."""
    if not isinstance(d, dict):
        return []
    sp = _pts_from_list(d.get("sparkline"))
    if sp:
        return sp
    data = d.get("data")
    if isinstance(data, list) and data:
        flat = _pts_from_list(data)
        if flat:
            return flat
        if isinstance(data[0], dict) and isinstance(data[0].get("data"), list):
            if not additive:
                return []                            # can't sum a rate/ratio breakdown
            from collections import defaultdict
            acc, seen = defaultdict(float), False
            for s in data:                           # sum the per-series daily values
                for dt, v in _pts_from_list(s.get("data")):
                    acc[dt] += v; seen = True
            if seen:
                return sorted(acc.items())
    return []


def _pts_to_series(pts, minp):
    if len(pts) < minp:
        return None
    idx = pd.to_datetime([dt for dt, _ in pts], errors="coerce")
    s = pd.Series([v for _, v in pts], index=idx).astype(float)
    s = s[s.index.notna()]
    s = s[~s.index.duplicated()].sort_index()
    return s if len(s) >= minp else None


def fetch_series(op, base: str, path: str, grain: str = "daily", additive: bool = True):
    """ONE ingestion path for every node: requests the node's NATIVE granularity,
    extracts a clean dated series from any payload shape, and gates on a
    grain-appropriate minimum (not a blanket 60-daily floor)."""
    grain = (grain or "daily").strip() or "daily"
    url = f"{base}{path}?date_from=2023-06-01&date_to=2025-12-31&granularity={GRAIN_GRAN.get(grain, 'daily')}"
    try:
        d = json.loads(op.open(url, timeout=40).read())
    except Exception:
        return None
    return _pts_to_series(_series_points(d, additive), GRAIN_MINPTS.get(grain, 60))


def root_metric_series(op, base, slug, metric_id, grain, cache, additive=True):
    """Recover a metric whose granular /metrics/{id} route 404s or returns a scalar:
    the dashboard ROOT assembly carries the same metric WITH its full sparkline.
    One root fetch per (slug, grain), cached, feeds every such metric on it."""
    key = (slug, grain)
    if key not in cache:
        url = (f"{base}/api/v1/{slug}/?date_from=2023-06-01&date_to=2025-12-31"
               f"&granularity={GRAIN_GRAN.get(grain, 'daily')}")
        try:
            cache[key] = json.loads(op.open(url, timeout=60).read())
        except Exception:
            cache[key] = {}
    mc = cache[key].get("metrics") if isinstance(cache[key], dict) else None
    m = None
    if isinstance(mc, dict):
        m = mc.get(metric_id)
    elif isinstance(mc, list):
        m = next((x for x in mc if isinstance(x, dict) and x.get("metric_id") == metric_id), None)
    if not isinstance(m, dict):
        return None
    return _pts_to_series(_series_points(m, additive), GRAIN_MINPTS.get(grain, 60))


def root_chart_series(op, base, slug, metric_base, grain, cache):
    """A metric whose endpoint serves only a scalar often has its daily series in a
    `<metric>_trend` chart on the dashboard ROOT (e.g. ceo-pulse/revenue_trend, 618 pts).
    Match the metric_base to such a chart and pull the flat date/value series."""
    key = (slug, grain)
    if key not in cache:
        url = (f"{base}/api/v1/{slug}/?date_from=2023-06-01&date_to=2025-12-31"
               f"&granularity={GRAIN_GRAN.get(grain, 'daily')}")
        try:
            cache[key] = json.loads(op.open(url, timeout=60).read())
        except Exception:  # noqa: BLE001
            cache[key] = {}
    charts = cache[key].get("charts") if isinstance(cache[key], dict) else None
    if not isinstance(charts, dict):
        return None
    mb = metric_base.lower()
    for cid, cv in charts.items():
        cl = cid.lower().replace("_trend", "").replace("-", "_")
        if cl == mb or (len(mb) >= 4 and (mb in cl or cl in mb)):
            s = _pts_to_series(_pts_from_list(cv.get("data") if isinstance(cv, dict) else None),
                               GRAIN_MINPTS.get(grain, 60))
            if s is not None:
                return s
    return None


def fetch_node_series(op, base, r, grain, root_cache):
    """Full ingestion for one node: granular metric/chart endpoint first, then the
    dashboard-root metric assembly, then a `<metric>_trend` chart on the root."""
    ce, se = r.get("card_endpoint"), r.get("series_endpoint")
    add = is_additive(r)                              # only sum breakdowns for additive metrics
    for ep in (ce, se):
        if ep:
            s = fetch_series(op, base, ep, grain, add)
            if s is not None:
                return s
    if ce:                                            # .../<slug>/metrics/<id> -> root metric
        m = re.search(r"/([a-z0-9][a-z0-9_-]*)/metrics/([a-z0-9_-]+)/?$", ce, re.I)
        if m:
            s = root_metric_series(op, base, m.group(1), m.group(2), grain, root_cache, add)
            if s is not None:
                return s
    for dash in [d for d in (r.get("source_dashboards") or "").split("|") if d]:
        s = root_chart_series(op, base, dash.replace("_", "-"), r.get("metric_base", ""), grain, root_cache)
        if s is not None:
            return s
    return None


def align_pair(rx, ry, gx: str, gy: str):
    """Bring two residual series onto a common grid (the coarser grain) and inner-join
    on dates — so a daily node and a weekly node can still be compared."""
    coarse = gx if GRAIN_ORDER.get(gx, 0) >= GRAIN_ORDER.get(gy, 0) else gy
    freq = GRAIN_FREQ.get(coarse, "D")

    def conform(s, g):
        s = s[~s.index.duplicated()].sort_index()    # fallback frames can have dup dates
        return s.resample(freq).mean() if g != coarse else s

    df = pd.concat([conform(rx, gx), conform(ry, gy)], axis=1).dropna()
    return df.iloc[:, 0], df.iloc[:, 1]


def fetch_table(op, base: str, path: str) -> pd.DataFrame | None:
    """Fetch a chart ONCE and return a date-indexed frame of ALL numeric columns.
    One call feeds every node whose series lives on this chart."""
    url = f"{base}{path}?date_from=2023-06-01&date_to=2025-12-31&granularity=daily"
    try:
        d = json.loads(op.open(url, timeout=40).read())
    except Exception:
        return None
    rows = d.get("data")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    datekey = next((k for k in rows[0] if k.lower() in
                    ("date", "day", "week", "month", "period")), None)
    if not datekey:
        return None                                  # breakdown/bundle, not a time series
    recs = []
    for r in rows:
        rec = {"_date": str(r.get(datekey))[:10]}
        for k, v in r.items():
            if k == datekey:
                continue
            try:
                rec[k] = float(v)
            except (TypeError, ValueError):
                pass
        if len(rec) > 1:
            recs.append(rec)
    if len(recs) < 60:
        return None
    df = pd.DataFrame(recs).set_index("_date")
    df.index = pd.to_datetime(df.index, errors="coerce")  # non-date axis -> NaT, dropped
    df = df[df.index.notna()]
    df = df[~df.index.duplicated()]
    if len(df) < 60:
        return None                                  # not a real daily time series
    return df.sort_index()


def fetch_sparkline(op, base: str, path: str):
    """PRIMARY ingest: a metric endpoint's daily sparkline -> a clean date series.
    Metric endpoints reliably return value+sparkline; charts often don't."""
    url = f"{base}{path}?date_from=2023-06-01&date_to=2025-12-31&granularity=daily"
    try:
        d = json.loads(op.open(url, timeout=40).read())
    except Exception:
        return None
    sp = d.get("sparkline")
    if not isinstance(sp, list) or len(sp) < 60:
        return None
    recs = [(str(p.get("date"))[:10], p.get("value")) for p in sp
            if isinstance(p, dict) and p.get("value") is not None]
    if len(recs) < 60:
        return None
    idx = pd.to_datetime([dt for dt, _ in recs], errors="coerce")  # weekday/hour axis -> NaT
    s = pd.Series([v for _, v in recs], index=idx)
    s = s[s.index.notna()]
    if len(s) < 60:
        return None                                  # not a real daily time series, skip
    return s[~s.index.duplicated()].sort_index()


def run_api(tenant: str, base: str, email: str, pw: str) -> None:
    reg = {r["node_id"]: r for r in csv.DictReader(
        open(_data_path(f"metric_registry.{tenant}.csv"), encoding="utf-8"))}
    edges = list(csv.DictReader(open(_data_path(f"candidate_edges.{tenant}.csv"), encoding="utf-8")))
    try:
        op = api_login(base, email, pw, tenant)
    except Exception as e:  # noqa: BLE001
        print(f"login failed ({e}) — check creds / API up? aborting"); return

    from collections import defaultdict
    need = {e["src"] for e in edges} | {e["dst"] for e in edges}
    node_dash = {nid: [d for d in reg.get(nid, {}).get("source_dashboards", "").split("|") if d]
                 for nid in need}
    dashes = sorted({d for ds in node_dash.values() for d in ds})

    table_cache: dict[str, pd.DataFrame | None] = {}

    def get_table(path):
        if path not in table_cache:
            table_cache[path] = fetch_table(op, base, path)
        return table_cache[path]

    def list_charts(slug):                           # QA's LIVE chart ids per dashboard
        try:
            d = json.loads(op.open(f"{base}/api/v1/{slug}/charts/", timeout=25).read())
        except Exception:
            return []
        return [c.get("id") for c in d.get("charts", []) if isinstance(c, dict) and c.get("id")]

    # PRIMARY ingest: grain-aware, any payload shape, granular-then-root per node.
    print(f"ingesting series for {len(need)} candidate nodes (grain-aware + root) ...")
    series: dict = {}
    grains: dict = {}
    root_cache: dict = {}
    for nid in need:
        r = reg.get(nid, {})
        grain = (r.get("grain") or "daily").strip() or "daily"
        s = fetch_node_series(op, base, r, grain, root_cache)
        if s is not None:
            series[nid] = s; grains[nid] = grain

    # FALLBACK: chart-column pool, only for dashboards of still-missing nodes.
    missing = [nid for nid in need if nid not in series]
    if missing:
        pool: dict[str, dict] = defaultdict(dict)
        for d in sorted({d for nid in missing for d in node_dash[nid]}):
            slug = d.replace("_", "-")
            for cid in list_charts(slug):
                df = get_table(f"/api/v1/{slug}/charts/{cid}")
                if df is None:
                    continue
                for col in df.columns:
                    if col not in pool[d] and len(df[col].dropna()) >= 30:
                        pool[d][col] = df[col].dropna()
        for nid in missing:
            mbase = reg.get(nid, {}).get("metric_base", "")
            for d in node_dash[nid]:
                col = _pick_col(list(pool[d].keys()), mbase)
                if col:
                    series[nid] = pool[d][col]
                    grains[nid] = (reg.get(nid, {}).get("grain") or "daily").strip() or "daily"
                    break

    resid = {nid: deseasonalize(s, GRAIN_PERIOD.get(grains.get(nid, "daily"), 7))
             for nid, s in series.items()}
    print(f"  mapped {len(series)}/{len(need)} candidate nodes to a time series")
    if len(series) < 2:
        print("not enough series returned. aborting."); return

    rows, pvals = [], []
    for e in edges:
        if e["src"] in resid and e["dst"] in resid:
            rx, ry = align_pair(resid[e["src"]], resid[e["dst"]],
                                grains.get(e["src"], "daily"), grains.get(e["dst"], "daily"))
            if len(rx) < 30:
                continue                    # too little overlap after grain alignment
            r = test_edge(rx, ry)
            r["stability"] = stability_ratio(rx, ry)
            r.update(src=e["src"], dst=e["dst"]); rows.append(r); pvals.append(r["granger_p"])
    if not rows:
        print("no testable edges (need both endpoints with data)."); return
    for r, k in zip(rows, bh_fdr(pvals)):
        # FDR + stability: when windowing is possible, require survival in a
        # majority of sub-windows (a one-period fluke is rejected).
        st = r.get("stability")
        r["fdr_pass"] = bool(k) and (st is None or st >= 0.5)

    # ---- conditioning pass (poor-man's PCMCI): condition each surviving edge on the
    #      target's OTHER parents + its own past. A link that collapses once common
    #      drivers are removed was a confound, not a cause. ----
    survivors = [r for r in rows if r["fdr_pass"] and r["discovery_score"] > 0.3]
    parents = defaultdict(list)
    for r in survivors:
        parents[r["dst"]].append((r["src"], r["lag"], r["discovery_score"]))
    COND_MIN = 0.08
    n_confounded = 0
    for r in rows:
        r["cond_corr"] = ""
        if not (r["fdr_pass"] and r["discovery_score"] > 0.3):
            continue
        others = sorted([p for p in parents[r["dst"]] if p[0] != r["src"]],
                        key=lambda p: -p[2])[:5]               # condition on top-5 co-parents
        pc = conditional_edge(resid, grains, r["src"], r["dst"], int(r["lag"]),
                              [(p, lg) for p, lg, _ in others])
        if pc is not None:
            r["cond_corr"] = pc
            if abs(pc) < COND_MIN:                              # confounded -> drop
                r["fdr_pass"] = False; n_confounded += 1

    for r in rows:
        r["method"] = "granger+stability+conditioning"
    out = _data_path(f"discovered_edges.{tenant}.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["src", "dst", "lag", "corr", "granger_p",
                                          "mi", "discovery_score", "stability", "cond_corr", "method", "fdr_pass"])
        w.writeheader(); w.writerows(rows)
    kept = [r for r in rows if r["fdr_pass"] and r["discovery_score"] > 0.3]
    drop_unstable = sum(1 for r in rows if r.get("stability") is not None and r["stability"] < 0.5)
    print(f"tested {len(rows)} edges; {len(kept)} pass FDR + stability + conditioning "
          f"({drop_unstable} unstable, {n_confounded} confounded-out) -> {out}")
    for r in sorted(kept, key=lambda r: -r["discovery_score"])[:15]:
        print(f"   {r['src']} -> {r['dst']}  lag={r['lag']} corr={r['corr']} score={r['discovery_score']}")


def _make_ci(test: str):
    """Conditional-independence test for PCMCI+. parcorr = linear (fast);
    cmiknn = nonparametric nonlinear (slow, captures nonlinear dependence)."""
    if test == "cmiknn-gpu":
        from harness.discovery.cmi_gpu import make_cmiknn_gpu  # torch/CUDA CMI, CPU-validated
        return make_cmiknn_gpu(batched=True)       # batched null = saturates the GPU
    if test == "cmiknn":
        from tigramite.independence_tests.cmiknn import CMIknn
        # sig_blocklength=1 avoids the auto block-length estimator, which calls
        # np.corrcoef(ddof=...) — a kwarg recent numpy removed (tigramite bug).
        return CMIknn(significance="shuffle_test", knn=0.1, shuffle_neighbors=5,
                      transform="ranks", sig_blocklength=1, workers=-1)
    if test == "gpdc":
        from tigramite.independence_tests.gpdc import GPDC
        return GPDC(significance="analytic")
    from tigramite.independence_tests.parcorr import ParCorr
    return ParCorr(significance="analytic")


def _pcmci_grid(nodes, resid, tau_max, ci_test, cand_set, pc_alpha=0.05):
    """Run PCMCI+ on one consistent time grid; return admissible directed links."""
    from tigramite import data_processing as pp
    from tigramite.pcmci import PCMCI
    df = pd.concat({n: pd.Series(resid[n]) for n in nodes}, axis=1).interpolate(limit=3).dropna()
    var = list(df.columns)
    if len(var) < 3 or len(df) < tau_max * 6 + 20:
        return [], (len(var), len(df), 0)
    # verbosity=1 -> PCMCI prints per-variable progress (~one line per variable in the
    # PC phase), so a slow CMIknn run shows how far along it is.
    pcmci = PCMCI(dataframe=pp.DataFrame(df.to_numpy().astype(float), var_names=var),
                  cond_ind_test=ci_test, verbosity=1)
    print(f"    running PCMCI+ over {len(var)} variables (progress below) ...", flush=True)
    res = pcmci.run_pcmciplus(tau_min=1, tau_max=tau_max, pc_alpha=pc_alpha)
    graph, val, pmat = res["graph"], res["val_matrix"], res["p_matrix"]
    rows, inadm = [], 0
    for i, s in enumerate(var):
        for j, d in enumerate(var):
            for tau in range(1, tau_max + 1):
                if graph[i, j, tau] != "-->":
                    continue
                if (s, d) not in cand_set:
                    inadm += 1; continue
                v = float(val[i, j, tau])
                rows.append({"src": s, "dst": d, "lag": tau, "corr": round(v, 3),
                             "granger_p": round(float(pmat[i, j, tau]), 4), "mi": "",
                             "discovery_score": round(abs(v), 3), "stability": "",
                             "cond_corr": "PCMCI+", "method": "pcmci+", "fdr_pass": True})
    return rows, (len(var), len(df), inadm)


# per-grain max lag: daily=1wk, weekly=1mo, monthly=1qtr
PCMCI_GRAINS = [("daily", 7), ("weekly", 4), ("monthly", 3)]


def run_pcmci(tenant: str, base: str, email: str, pw: str, test: str = "parcorr",
              tau_max: int = 0, max_nodes: int = 0) -> None:
    """Real PCMCI+ (tigramite) — joint multivariate causal discovery.

    PCMCI+ runs the WHOLE system together: a PC condition-selection phase finds each
    variable's parents, then the MCI test scores each lagged link CONDITIONED on those
    parents — confounding + autocorrelation handled natively. Runs once PER GRAIN
    (daily / weekly / monthly) so each grid is consistent, unions the results, and keeps
    only type-admissible directions. `test` selects the CI test (parcorr linear /
    cmiknn nonlinear). Output: data/discovered_edges_<test>.<tenant>.csv (+ canonical
    discovered_edges.<tenant>.csv for parcorr)."""
    ci_test = _make_ci(test)
    reg = {r["node_id"]: r for r in csv.DictReader(
        open(_data_path(f"metric_registry.{tenant}.csv"), encoding="utf-8"))}
    cand = list(csv.DictReader(open(_data_path(f"candidate_edges.{tenant}.csv"), encoding="utf-8")))
    cand_set = {(e["src"], e["dst"]) for e in cand}
    need = {e["src"] for e in cand} | {e["dst"] for e in cand}

    # reuse the scan's series cache when present (so a pipeline run doesn't re-fetch);
    # fetch only the still-missing nodes from the API.
    series, grains = {}, {}
    cache_path = _series_cache_path(tenant)
    if os.path.exists(cache_path):
        for line in open(cache_path, encoding="utf-8"):
            d = json.loads(line)
            if d["node_id"] in need and d.get("series"):
                idx = pd.to_datetime([x[0] for x in d["series"]], errors="coerce")
                s = pd.Series([x[1] for x in d["series"]], index=idx)
                s = s[s.index.notna()]
                series[d["node_id"]] = s[~s.index.duplicated()].sort_index()
                grains[d["node_id"]] = (reg.get(d["node_id"], {}).get("grain") or "daily").strip() or "daily"
        print(f"[{test}] loaded {len(series)} series from scan cache")
    missing = [n for n in need if n not in series]
    if missing:
        try:
            op = api_login(base, email, pw, tenant)
            root_cache = {}
            print(f"[{test}] fetching {len(missing)} uncached series ...")
            for nid in missing:
                r = reg.get(nid, {})
                grain = (r.get("grain") or "daily").strip() or "daily"
                s = fetch_node_series(op, base, r, grain, root_cache)
                if s is not None:
                    series[nid] = s; grains[nid] = grain
        except Exception as e:  # noqa: BLE001
            print(f"[{test}] API unavailable ({e}); using {len(series)} cached series")

    # optional: restrict to the most-connected nodes (PC-phase cost scales hard with N,
    # so this makes a nonparametric test like CMIknn tractable on modest hardware)
    if max_nodes:
        from collections import Counter as _C
        deg = _C()
        for e in cand:
            deg[e["src"]] += 1; deg[e["dst"]] += 1
        keep_n = {n for n, _ in deg.most_common(max_nodes)}
        series = {n: s for n, s in series.items() if n in keep_n}
        print(f"[{test}] restricted to top {len(series)} most-connected nodes")

    all_rows = []
    for grain, tau in PCMCI_GRAINS:
        nodes = [n for n in series if grains[n] == grain]
        if len(nodes) < 3:
            continue
        tau_eff = min(tau, tau_max) if tau_max else tau     # optional cap for tractable runs
        resid = {n: deseasonalize(series[n], GRAIN_PERIOD[grain]) for n in nodes}
        rows, (nv, npts, inadm) = _pcmci_grid(nodes, resid, tau_eff, ci_test, cand_set)
        print(f"  [{test}/{grain}] {nv} nodes x {npts} pts (tau_max={tau_eff}) -> "
              f"{len(rows)} admissible links ({inadm} inadmissible dropped)", flush=True)
        all_rows.extend(rows)

    suffix = f"_tau{tau_max}" if tau_max else ""
    out = _data_path(f"discovered_edges_{test}{suffix}.{tenant}.csv")
    fields = ["src", "dst", "lag", "corr", "granger_p", "mi", "discovery_score",
              "stability", "cond_corr", "method", "fdr_pass"]
    # only the default full parcorr run owns the canonical file
    write_canonical = test == "parcorr" and not tau_max
    for path in [out] + ([_data_path(f"discovered_edges.{tenant}.csv")] if write_canonical else []):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(all_rows)
    print(f"[{test}] PCMCI+ total {len(all_rows)} admissible causal links -> {out}")
    for r in sorted(all_rows, key=lambda r: -r["discovery_score"])[:12]:
        print(f"   {r['src']} -> {r['dst']}  lag={r['lag']} MCI={r['corr']} p={r['granger_p']}")


def run_scan(tenant: str, base: str, email: str, pw: str) -> None:
    """Availability scan: fetch every kept node's sparkline, write availability /
    history / n_periods / data-measured grain back to the registry + persist a
    series cache. Same sparkline fetcher as discovery — one ingestion path."""
    reg_path = _data_path(f"metric_registry.{tenant}.csv")
    rows = list(csv.DictReader(open(reg_path, encoding="utf-8")))
    for c in ("availability", "history_start", "history_end", "n_periods"):
        for r in rows:
            r.setdefault(c, "")
    try:
        op = api_login(base, email, pw, tenant)
    except Exception as e:  # noqa: BLE001
        print(f"login failed ({e}); aborting scan"); return
    cache_path = _series_cache_path(tenant)
    cache = open(cache_path, "w", encoding="utf-8")
    avail = empty = 0
    targets = [r for r in rows if r["keep"] == "yes" and (r.get("card_endpoint") or r.get("series_endpoint"))]
    print(f"scanning availability for {len(targets)} kept nodes ...")
    root_cache: dict = {}
    for r in targets:
        grain = (r.get("grain") or "daily").strip() or "daily"
        s = fetch_node_series(op, base, r, grain, root_cache)
        if s is not None:
            r["availability"] = "available"
            r["history_start"] = str(s.index.min())[:10]
            r["history_end"] = str(s.index.max())[:10]
            r["n_periods"] = str(len(s))
            cache.write(json.dumps({"node_id": r["node_id"], "n": len(s),
                                    "series": [[str(i)[:10], float(v)] for i, v in s.items()]}) + "\n")
            avail += 1
        else:
            r["availability"] = "empty"; empty += 1
    cache.close()
    with open(reg_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"  available={avail} empty={empty} -> registry + {cache_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["synthetic", "api", "scan", "pcmci"], default="synthetic")
    ap.add_argument("--test", choices=["parcorr", "cmiknn", "cmiknn-gpu", "gpdc"], default="parcorr",
                    help="PCMCI+ conditional-independence test (linear vs nonlinear; -gpu = CUDA)")
    ap.add_argument("--tau-max", type=int, default=0,
                    help="cap max lag (smaller = tractable for slow nonlinear tests); writes a _tau<N> file")
    ap.add_argument("--max-nodes", type=int, default=0,
                    help="restrict to the N most-connected nodes (makes CMIknn tractable)")
    ap.add_argument("--tenant", default="rare_seeds")
    # BC_2 local default; TW_API_BASE env wins when --base is left at the default.
    ap.add_argument("--base", default=os.environ.get("TW_API_BASE", "http://localhost:8005"))
    ap.add_argument("--email", default="admin@thoughtwire.com")
    ap.add_argument("--password", default="TestPass2026!")
    args = ap.parse_args()
    email = os.environ.get("TW_API_EMAIL") or args.email       # env wins (pipeline sets it)
    pw = os.environ.get("TW_API_PASSWORD") or args.password
    if args.mode == "synthetic":
        run_synthetic()
    elif args.mode == "scan":
        run_scan(args.tenant, args.base, email, pw)
    elif args.mode == "pcmci":
        run_pcmci(args.tenant, args.base, email, pw, test=args.test,
                  tau_max=args.tau_max, max_nodes=args.max_nodes)
    else:
        run_api(args.tenant, args.base, email, pw)


if __name__ == "__main__":
    main()
