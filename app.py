"""Interactive Data for Decision dashboard."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from src.data_processing import regression_summary


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
BOUNDARY_FILES = {
    "country": DATA_DIR / "kenya_country.geojson",
    "county": DATA_DIR / "kenya_counties.geojson",
    "subcounty": DATA_DIR / "kenya_subcounties.geojson",
}

st.set_page_config(page_title="Data for Decision", page_icon="📊", layout="wide")

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.4rem; padding-bottom: 2rem; }
    [data-testid="stMetric"] { background: #F4F8FB; border: 1px solid #D6E4ED; border-radius: 10px; padding: 0.75rem 0.9rem; }
    [data-testid="stMetricLabel"] { color: #52606D; }
    .filter-chip { display: inline-block; background: #EAF4FB; color: #12304A; border: 1px solid #B9D9EC; border-radius: 999px; padding: 0.28rem 0.65rem; margin: 0 0.35rem 0.35rem 0; font-size: 0.82rem; }
    .source-note { color: #667085; font-size: 0.82rem; }
    [data-baseweb="select"] > div { border-color: #D6E4ED !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

COLORS = {
    "navy": "#12304A",
    "blue": "#1769AA",
    "light_blue": "#DCECF7",
    "green": "#2E7D57",
    "gold": "#D99A00",
    "orange": "#C96A1B",
    "red": "#B33A3A",
    "purple": "#6B5B95",
    "grey": "#667085",
}

DATASETS = {
    "Presidential — Kenya": {
        "path": DATA_DIR / "D4D_Kenya_Presidential.xlsx",
        "history_sheet": "Poll_History",
        "snapshot_sheet": "Latest_Snapshot",
        "master_sheet": "County_Master",
        "summary_sheet": "National_Tracking_Poll",
        "hierarchy": ["county", "sub_county"],
        "probability": "support_probability",
        "current_metrics": ["support_probability", "zone", "trend_label", "trend_slope_per_cycle", "trend_slope_ci_low", "trend_slope_ci_high", "trend_pvalue"],
        "historical_metrics": ["sample_size", "supporters", "true_regime"],
        "label": "Presidential support tracking",
    },
    "Governor — Kitui": {
        "path": DATA_DIR / "D4D_Kitui_Governor.xlsx",
        "history_sheet": "Poll_History",
        "snapshot_sheet": "Latest_Snapshot",
        "master_sheet": "Ward_Master",
        "summary_sheet": "county_tracking_poll",
        "hierarchy": ["county", "sub_county", "ward"],
        "probability": "win_probability",
        "current_metrics": ["win_probability", "zone", "trend_label", "trend_slope_per_cycle", "trend_slope_ci_low", "trend_slope_ci_high", "trend_pvalue"],
        "historical_metrics": ["sample_size", "supporters", "true_regime"],
        "label": "Kitui governor tracking",
    },
}

DISPLAY_NAMES = {
    "sample_size": "Sample size",
    "supporters": "Supporters",
    "true_regime": "True regime",
    "support_probability": "Support probability",
    "win_probability": "Win probability",
    "zone": "Zone",
    "trend_label": "Trend label",
    "trend_slope_per_cycle": "Trend slope per cycle",
    "trend_pvalue": "Trend p-value",
    "trend_slope_ci_low": "Slope 95% CI lower",
    "trend_slope_ci_high": "Slope 95% CI upper",
    "trend_n_cycles": "Trend cycles",
    "registered_voters": "Registered voters",
    "cycle_date": "Cycle date",
}


def key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


@st.cache_data(show_spinner=False)
def load_geojson(level: str) -> dict:
    with BOUNDARY_FILES[level].open(encoding="utf-8") as handle:
        return json.load(handle)


@st.cache_data(show_spinner=False)
def load_dataset(dataset_name: str) -> dict[str, pd.DataFrame | list[str]]:
    cfg = DATASETS[dataset_name]
    history = pd.read_excel(cfg["path"], sheet_name=cfg["history_sheet"])
    snapshot = pd.read_excel(cfg["path"], sheet_name=cfg["snapshot_sheet"])
    master = pd.read_excel(cfg["path"], sheet_name=cfg["master_sheet"])
    summary = pd.read_excel(cfg["path"], sheet_name=cfg["summary_sheet"])
    for frame in (history, snapshot, master, summary):
        frame.columns = [str(col).strip() for col in frame.columns]
    for frame in (history, snapshot, summary):
        if "cycle_id" in frame:
            frame["cycle_id"] = pd.to_numeric(frame["cycle_id"], errors="coerce").astype("Int64")
        if "cycle_date" in frame:
            frame["cycle_date"] = pd.to_datetime(frame["cycle_date"], errors="coerce")
    for frame in (history, snapshot, master):
        for col in ["county", "sub_county", "ward"]:
            if col in frame:
                frame[f"{col}_key"] = frame[col].map(key)
    for col in ["sample_size", "supporters", "registered_voters", cfg["probability"], "ci_low", "ci_high"]:
        for frame in (history, snapshot):
            if col in frame:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
    for col in ["trend_slope_per_cycle", "trend_slope_ci_low", "trend_slope_ci_high", "trend_pvalue", "trend_n_cycles"]:
        if col in snapshot:
            snapshot[col] = pd.to_numeric(snapshot[col], errors="coerce")
    if "registered_voters" in master:
        master["registered_voters"] = pd.to_numeric(master["registered_voters"], errors="coerce")
    required = set(cfg["hierarchy"] + ["cycle_id", "cycle_date", "sample_size", "supporters"])
    missing = sorted(required.difference(history.columns))
    if missing:
        raise ValueError(f"Missing required columns in {dataset_name}: {', '.join(missing)}")
    return {"history": history, "snapshot": snapshot, "master": master, "summary": summary, "missing": missing}


def boundaries_for(level: str, frame: pd.DataFrame) -> tuple[dict, str, str]:
    geo = load_geojson(level)
    if level == "county":
        return geo, "properties.county_key", "county_key"
    return geo, "properties.subcounty_geo_key", "geo_key"


def add_geo_lines(fig: go.Figure, geo: dict, color: str, width: float, name: str) -> None:
    """Add polygon outlines, including MultiPolygon holes/parts, as one map layer."""
    lons: list[float | None] = []
    lats: list[float | None] = []
    for feature in geo.get("features", []):
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates", [])
        polygons = coords if geometry.get("type") == "MultiPolygon" else [coords]
        for polygon in polygons:
            for ring in polygon:
                for lon, lat in ring:
                    lons.append(lon)
                    lats.append(lat)
                lons.append(None)
                lats.append(None)
    fig.add_trace(go.Scattergeo(lon=lons, lat=lats, mode="lines", line={"color": color, "width": width}, name=name, hoverinfo="skip", showlegend=False))


def current_cycle(history: pd.DataFrame) -> int:
    return int(history["cycle_id"].dropna().max())


def cycle_label(history: pd.DataFrame, cycle: object) -> str:
    dates = pd.to_datetime(history.loc[history["cycle_id"].astype(str).eq(str(cycle)), "cycle_date"], errors="coerce").dropna()
    suffix = dates.min().strftime("%d %b %Y") if not dates.empty else "date unavailable"
    return f"Cycle {cycle} — {suffix}"


def metric_options(history: pd.DataFrame, cfg: dict, cycle: int) -> list[str]:
    cols = cfg["current_metrics"] if cycle == current_cycle(history) else cfg["historical_metrics"]
    return [col for col in cols if col in history.columns]


def format_metric(value: object, metric: str) -> str:
    if pd.isna(value):
        return "—"
    if "probability" in metric:
        return f"{float(value) * 100:.1f}%"
    if metric in {"sample_size", "supporters", "registered_voters"}:
        return f"{int(value):,}"
    if metric == "trend_pvalue":
        return f"{float(value):.3f}"
    if metric in {"trend_slope_per_cycle", "trend_slope_ci_low", "trend_slope_ci_high"}:
        return f"{float(value):+.4f}"
    return str(value)


def prepare_geo_frame(history: pd.DataFrame, level: str) -> pd.DataFrame:
    group_cols = ["county"] if level == "county" else ["county", "sub_county"]
    out = history.groupby(group_cols, as_index=False).agg(
        sample_size=("sample_size", "sum"),
        supporters=("supporters", "sum"),
        cycle_date=("cycle_date", "max"),
    )
    out["county_key"] = out["county"].map(key)
    if level == "subcounty":
        out["subcounty_key"] = out["sub_county"].map(key)
        out["geo_key"] = out["county_key"] + "__" + out["subcounty_key"]
    return out


def build_map(frame: pd.DataFrame, level: str, metric: str, title: str, selected_county: str | None = None, fallback_points: pd.DataFrame | None = None) -> go.Figure:
    geo, feature_key, frame_key = boundaries_for(level, frame)
    plot = frame.copy()
    if level == "county":
        plot["county_key"] = plot["county"].map(key)
    plot["map_value"] = plot[metric].map(key) if plot[metric].dtype == "object" else pd.to_numeric(plot[metric], errors="coerce")
    categories = None
    if plot["map_value"].dtype == "object":
        categories = list(dict.fromkeys(plot[metric].dropna().astype(str)))
        plot["map_value"] = plot[metric].map({v: i + 1 for i, v in enumerate(categories)})
        color_scale = [[i / max(len(categories) - 1, 1), px.colors.qualitative.Safe[i % len(px.colors.qualitative.Safe)]] for i in range(len(categories))]
        colorbar = {"title": DISPLAY_NAMES.get(metric, metric)}
    else:
        color_scale = "Blues"
        colorbar = {"title": DISPLAY_NAMES.get(metric, metric)}
    plot["hover_metric"] = plot[metric].map(lambda v: format_metric(v, metric))
    fig = px.choropleth(
        plot,
        geojson=geo,
        locations=frame_key,
        featureidkey=feature_key,
        color="map_value",
        color_continuous_scale=color_scale,
        hover_name="county" if level == "county" else "sub_county",
        hover_data={"map_value": False, "hover_metric": True, "sample_size": True, "supporters": True},
        labels={"hover_metric": DISPLAY_NAMES.get(metric, metric), "sample_size": "Sample size", "supporters": "Supporters"},
    )
    fig.update_traces(marker_line_color="#FFFFFF", marker_line_width=0.7, colorbar=colorbar, selector={"type": "choropleth"})
    fig.update_coloraxes(colorbar_title=DISPLAY_NAMES.get(metric, metric.replace("_", " ").title()))
    if categories:
        fig.update_coloraxes(colorbar_tickvals=list(range(1, len(categories) + 1)), colorbar_ticktext=categories)
    if level == "county" and selected_county:
        selected = plot[plot["county"].eq(selected_county)]
        if not selected.empty:
            fig.add_trace(go.Choropleth(geojson=geo, locations=selected["county_key"], z=[1], featureidkey=feature_key, colorscale=[[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)" ]], marker_line_color=COLORS["gold"], marker_line_width=4, showscale=False, hoverinfo="skip", showlegend=False))
    if fallback_points is not None and not fallback_points.empty:
        fig.add_trace(go.Scattergeo(lon=fallback_points["lon"], lat=fallback_points["lat"], mode="markers", marker={"size": 8, "color": COLORS["orange"], "symbol": "diamond"}, text=fallback_points["sub_county"], customdata=fallback_points[["county", "sub_county"]], hovertemplate="%{customdata[1]}<br>%{customdata[0]}<br>No matched polygon; coordinate fallback<extra></extra>", name="Coordinate fallback", showlegend=True))
    add_geo_lines(fig, load_geojson("country"), COLORS["navy"], 2.8, "Kenya boundary")
    fig.update_geos(fitbounds="locations", visible=False, showcountries=False, showland=False)
    fig.update_layout(title=title, height=620, margin={"l": 0, "r": 0, "t": 45, "b": 0}, paper_bgcolor="white", geo_bgcolor="white", legend={"orientation": "h"})
    return fig


def trend_chart(history: pd.DataFrame, cfg: dict, title: str) -> go.Figure:
    group = history.copy()
    group["cycle_date"] = pd.to_datetime(group["cycle_date"], errors="coerce")
    prob = cfg["probability"]
    aggregations = {"sample_size": ("sample_size", "sum"), "supporters": ("supporters", "sum")}
    if prob in group:
        group[prob] = pd.to_numeric(group[prob], errors="coerce")
        group["weighted_probability"] = group[prob] * group["sample_size"]
        aggregations["weighted_probability"] = ("weighted_probability", "sum")
    has_ci = {"ci_low", "ci_high"}.issubset(group.columns)
    if has_ci:
        group["ci_low"] = pd.to_numeric(group["ci_low"], errors="coerce")
        group["ci_high"] = pd.to_numeric(group["ci_high"], errors="coerce")
        group["weighted_ci_low"] = group["ci_low"] * group["sample_size"]
        group["weighted_ci_high"] = group["ci_high"] * group["sample_size"]
        aggregations["weighted_ci_low"] = ("weighted_ci_low", "sum")
        aggregations["weighted_ci_high"] = ("weighted_ci_high", "sum")
    group = group.groupby(["cycle_id", "cycle_date"], as_index=False).agg(**aggregations)
    if "weighted_probability" in group:
        group[prob] = group["weighted_probability"] / group["sample_size"].replace(0, pd.NA)
    if has_ci:
        group["ci_low"] = group["weighted_ci_low"] / group["sample_size"].replace(0, pd.NA)
        group["ci_high"] = group["weighted_ci_high"] / group["sample_size"].replace(0, pd.NA)
    group = group.assign(entity="Selected geography")
    if prob in group:
        regression = regression_summary(group, prob)
        fig = go.Figure()
        if has_ci and group[["ci_low", "ci_high"]].notna().all(axis=None):
            fig.add_trace(go.Scatter(x=group["cycle_date"], y=group["ci_high"], mode="lines", line={"width": 0}, hoverinfo="skip", showlegend=False, name="95% CI"))
            fig.add_trace(go.Scatter(x=group["cycle_date"], y=group["ci_low"], mode="lines", line={"width": 0}, fill="tonexty", fillcolor="rgba(23, 105, 170, 0.18)", hoverinfo="skip", name="95% CI"))
        fig.add_trace(go.Scatter(x=group["cycle_date"], y=group[prob], mode="lines+markers", line={"color": COLORS["blue"], "width": 3}, marker={"size": 7}, name=DISPLAY_NAMES[prob], customdata=group[["cycle_id", "sample_size", "supporters"]], hovertemplate="Estimate: %{y:.1%}<br>Cycle: %{customdata[0]}<br>Sample size: %{customdata[1]:,}<br>Supporters: %{customdata[2]:,}<extra></extra>"))
        if not pd.isna(regression["trend_slope_per_cycle"]):
            fitted = regression["trend_intercept"] + regression["trend_slope_per_cycle"] * group["cycle_id"].astype(float)
            fig.add_trace(go.Scatter(x=group["cycle_date"], y=fitted, mode="lines", line={"color": COLORS["orange"], "width": 2, "dash": "dash"}, name="Regression line", hoverinfo="skip"))
            slope_text = f"Slope: {regression['trend_slope_per_cycle']:+.4f}/cycle · 95% CI [{regression['trend_slope_ci_low']:+.4f}, {regression['trend_slope_ci_high']:+.4f}] · p={regression['trend_pvalue']:.3f}"
        else:
            slope_text = "Slope unavailable: fewer than three cycles with valid estimates"
        fig.update_layout(title=f"{title}<br><sup>{slope_text}</sup>", xaxis_title="Cycle date", yaxis_title=DISPLAY_NAMES[prob], legend_title_text="", hovermode="x unified")
        y_columns = [prob]
        if has_ci:
            y_columns.extend(["ci_low", "ci_high"])
        y_values = pd.concat([group[column] for column in y_columns], ignore_index=True).dropna()
        if not y_values.empty:
            y_min = float(y_values.min())
            y_max = float(y_values.max())
            y_span = max(y_max - y_min, 0.05)
            padding = max(0.03, y_span * 0.20)
            lower = max(0.0, y_min - padding)
            upper = min(1.0, y_max + padding)
            if upper - lower < 0.10:
                midpoint = (y_min + y_max) / 2
                lower = max(0.0, midpoint - 0.05)
                upper = min(1.0, midpoint + 0.05)
            fig.update_yaxes(tickformat=".0%", range=[lower, upper])
    else:
        fig = px.line(group, x="cycle_id", y="sample_size", color="entity", markers=True, labels={"sample_size": "Sample size", "cycle_id": "Cycle"}, title=title)
    fig.update_layout(height=390, margin={"l": 0, "r": 10, "t": 45, "b": 0}, legend_title_text="")
    return fig


def dataframe_download(frame: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="filtered_data")
    return output.getvalue()


st.markdown(f"<h1 style='color:{COLORS['navy']}; margin-bottom:0'>Data for Decision</h1><p style='color:{COLORS['grey']}; margin-top:0'>Interactive polling results by cycle and geography</p><p class='source-note'>Kenya polling dashboard · Source workbooks and administrative boundaries are shown in the data notes below.</p>", unsafe_allow_html=True)

with st.sidebar:
    st.header("Explore")
    dataset_name = st.selectbox("Dataset / election", list(DATASETS))
    cfg = DATASETS[dataset_name]
    bundle = load_dataset(dataset_name)
    history = bundle["history"].copy()
    snapshot = bundle["snapshot"].copy()
    cycle_values = sorted(history["cycle_id"].dropna().astype(int).unique())
    view_mode = st.selectbox("Display mode", ["Cycle view", "Latest assessment"])
    level_options = ["National", "County", "Sub-county"] + (["Ward"] if "ward" in cfg["hierarchy"] else [])
    level = st.selectbox("Geography level", level_options)
    selected_county = st.selectbox("County", ["All"] + sorted(history["county"].dropna().unique().tolist()))
    selected_subcounty = "All"
    if selected_county != "All" and "sub_county" in history:
        choices = sorted(history.loc[history.county.eq(selected_county), "sub_county"].dropna().unique().tolist())
        selected_subcounty = st.selectbox("Sub-county", ["All"] + choices)
    st.caption("Future-dated cycles are retained as supplied and are not filtered against the server date.")

stored_cycle = st.session_state.get("cycle_timeline", max(cycle_values))
if stored_cycle not in cycle_values:
    stored_cycle = max(cycle_values)
is_latest = view_mode == "Latest assessment"
selected_cycle = current_cycle(history) if is_latest else int(stored_cycle)

cycle_history = history[history["cycle_id"].eq(selected_cycle)].copy()
filtered = history.copy()
if selected_county != "All":
    filtered = filtered[filtered["county"].eq(selected_county)]
if selected_subcounty != "All":
    filtered = filtered[filtered["sub_county"].eq(selected_subcounty)]
filtered_snapshot = snapshot.copy()
if selected_county != "All":
    filtered_snapshot = filtered_snapshot[filtered_snapshot["county"].eq(selected_county)]
if selected_subcounty != "All":
    filtered_snapshot = filtered_snapshot[filtered_snapshot["sub_county"].eq(selected_subcounty)]
current = filtered_snapshot.copy() if is_latest else filtered[filtered["cycle_id"].eq(selected_cycle)].copy()
if is_latest:
    available_metrics = [column for column in cfg["current_metrics"] + ["trend_slope_per_cycle", "trend_pvalue"] if column in snapshot.columns]
else:
    available_metrics = metric_options(history, cfg, selected_cycle)
metric = st.selectbox("Map metric", available_metrics, format_func=lambda x: DISPLAY_NAMES.get(x, x.replace("_", " ").title()))

scope_label = selected_subcounty if selected_subcounty != "All" else selected_county if selected_county != "All" else "Kenya"
if is_latest:
    st.info("Latest assessment indicators are derived from the historical cycles in the selected workbook. They are not cycle-specific measures.")
else:
    st.info("Estimates are based on the selected polling cycle and source sample sizes. They are not certified election results.")

chips = [f"Dataset: {dataset_name}", f"View: {'Latest assessment' if is_latest else 'Cycle view'}", f"Cycle: {selected_cycle if not is_latest else 'Latest snapshot'}", f"Level: {level}", f"Metric: {DISPLAY_NAMES.get(metric, metric)}"]
if selected_county != "All":
    chips.append(f"County: {selected_county}")
if selected_subcounty != "All":
    chips.append(f"Sub-county: {selected_subcounty}")
st.markdown("".join(f"<span class='filter-chip'>{chip}</span>" for chip in chips), unsafe_allow_html=True)

metric_cols = st.columns(4)
summary_row = current
prob = cfg["probability"]
if is_latest:
    zone_summary = summary_row["zone"].mode().iat[0] if "zone" in summary_row and not summary_row["zone"].dropna().empty else None
    trend_summary = summary_row["trend_label"].mode().iat[0] if "trend_label" in summary_row and not summary_row["trend_label"].dropna().empty else None
    cards = [
        (DISPLAY_NAMES.get(prob, prob), format_metric(summary_row[prob].mean() if prob in summary_row else None, prob)),
        ("Zone", format_metric(zone_summary, "zone")),
        ("Trend label", format_metric(trend_summary, "trend_label")),
        ("Geographies", f"{summary_row[cfg['hierarchy'][-1]].nunique():,}" if not summary_row.empty else "0"),
    ]
elif selected_cycle == current_cycle(history):
    cards = [
        (DISPLAY_NAMES.get(prob, prob), format_metric(summary_row[prob].mean() if prob in summary_row else None, prob)),
        ("Sample size", format_metric(summary_row["sample_size"].sum() if not summary_row.empty else None, "sample_size")),
        ("Supporters", format_metric(summary_row["supporters"].sum() if not summary_row.empty else None, "supporters")),
        ("Geographies", f"{summary_row[cfg['hierarchy'][-1]].nunique():,}" if not summary_row.empty else "0"),
    ]
else:
    regime_summary = summary_row["true_regime"].mode().iat[0] if "true_regime" in summary_row and not summary_row["true_regime"].dropna().empty else None
    cards = [
        ("Sample size", format_metric(summary_row["sample_size"].sum() if not summary_row.empty else None, "sample_size")),
        ("Supporters", format_metric(summary_row["supporters"].sum() if not summary_row.empty else None, "supporters")),
        ("True regime", format_metric(regime_summary, "true_regime")),
        ("Geographies", f"{summary_row[cfg['hierarchy'][-1]].nunique():,}" if not summary_row.empty else "0"),
    ]
for col, (label, value) in zip(metric_cols, cards):
    col.metric(label, value)

if is_latest:
    st.caption(f"Current cycle: {selected_cycle}. Leadership view includes support probability, zone, and trend label where supplied.")
elif selected_cycle == current_cycle(history):
    st.caption(f"Cycle view: {selected_cycle}. Probability is shown from the selected cycle; derived trend indicators are available under Latest assessment.")
else:
    st.caption(f"Historical cycle: {selected_cycle}. Historical view includes sample size, supporters, and true regime where supplied.")

analysis_tab, source_tab, quality_tab, help_tab = st.tabs(["Overview, Trend & Geography", "Source detail", "Data quality", "Help"])

with analysis_tab:
    st.subheader("Cycle timeline")
    st.slider(
        "Cycle timeline — Latest assessment" if is_latest else f"Cycle timeline — {cycle_label(history, selected_cycle)}",
        min_value=min(cycle_values),
        max_value=max(cycle_values),
        value=selected_cycle,
        step=1,
        format="Cycle %d",
        key="cycle_timeline_latest" if is_latest else "cycle_timeline",
        disabled=is_latest,
        help="Move across cycles to update the map and all linked panels.",
    )
    endpoint_left, endpoint_right = st.columns(2)
    endpoint_left.caption(cycle_label(history, min(cycle_values)))
    endpoint_right.caption("Latest derived assessment" if is_latest else cycle_label(history, max(cycle_values)))
    if is_latest:
        st.caption("The cycle timeline is disabled in Latest assessment mode because zone and trend indicators summarize the full historical period.")
    st.subheader("Overview")
    map_source = snapshot if is_latest else cycle_history
    if level == "National":
        map_level = "county"
        # Aggregate numeric measures and retain the first supplied status label per county.
        agg = {"sample_size": "sum", "supporters": "sum", "cycle_date": "max", metric: "first"}
        map_frame = map_source.groupby("county", as_index=False).agg(agg)
        map_frame["county_key"] = map_frame["county"].map(key)
        st.plotly_chart(build_map(map_frame, map_level, metric, f"Kenya by county — {DISPLAY_NAMES.get(metric, metric)}", selected_county if selected_county != "All" else None), width="stretch")
    elif level == "County":
        map_level = "subcounty"
        map_frame = map_source.copy()
        if selected_county != "All":
            map_frame = map_frame[map_frame["county"].eq(selected_county)]
        map_frame = map_frame.groupby(["county", "sub_county"], as_index=False).agg({metric: "first", "sample_size": "sum", "supporters": "sum", "cycle_date": "max"})
        map_frame["county_key"] = map_frame["county"].map(key)
        map_frame["subcounty_key"] = map_frame["sub_county"].map(key)
        map_frame["geo_key"] = map_frame["county_key"] + "__" + map_frame["subcounty_key"]
        matched = set(map_frame["geo_key"]) & {f["properties"].get("subcounty_geo_key") for f in load_geojson("subcounty").get("features", [])}
        fallback_points = map_frame.loc[~map_frame["geo_key"].isin(matched)].copy()
        master = bundle["master"]
        if not fallback_points.empty and set(["lat", "lon"]).issubset(master.columns):
            fallback_points = fallback_points[["county", "sub_county"]].merge(master[["county", "sub_county", "lat", "lon"]], on=["county", "sub_county"], how="left")
            fallback_points["lat"] = pd.to_numeric(fallback_points["lat"], errors="coerce")
            fallback_points["lon"] = pd.to_numeric(fallback_points["lon"], errors="coerce")
            fallback_points = fallback_points.dropna(subset=["lat", "lon"])
        if len(matched) < len(map_frame):
            st.warning(f"{len(map_frame) - len(matched)} selected sub-counties use coordinate fallback markers because the CDC reference layer has no one-to-one polygon for their source name. They remain visible on the map and in the source table.")
        st.plotly_chart(build_map(map_frame, map_level, metric, f"Sub-counties in {selected_county if selected_county != 'All' else 'Kenya'} — {DISPLAY_NAMES.get(metric, metric)}", selected_county if selected_county != "All" else None, fallback_points), width="stretch")
        if not fallback_points.empty:
            with st.expander(f"Review {len(fallback_points)} coordinate fallback entries"):
                fallback_view = fallback_points[["county", "sub_county", "lat", "lon"]].copy()
                fallback_view = fallback_view.rename(columns={"county": "County", "sub_county": "Sub-county", "lat": "Latitude", "lon": "Longitude"})
                fallback_view["Map status"] = "Coordinate fallback — no one-to-one polygon match"
                st.dataframe(fallback_view, hide_index=True, width="stretch")
    else:
        point_frame = current.copy()
        if selected_county != "All":
            point_frame = point_frame[point_frame.county.eq(selected_county)]
        if selected_subcounty != "All":
            point_frame = point_frame[point_frame.sub_county.eq(selected_subcounty)]
        if "lat" not in point_frame or "lon" not in point_frame:
            master = bundle["master"]
            join_cols = cfg["hierarchy"]
            point_frame = point_frame.merge(master[join_cols + ["lat", "lon"]], on=join_cols, how="left")
        if "lat" not in point_frame:
            st.warning("No point coordinates are available for this geography level in the source data.")
        else:
            point_frame["lat"] = pd.to_numeric(point_frame["lat"], errors="coerce")
            point_frame["lon"] = pd.to_numeric(point_frame["lon"], errors="coerce")
            point_frame = point_frame.dropna(subset=["lat", "lon"])
            fig = px.scatter_geo(point_frame, lat="lat", lon="lon", color=metric if metric in point_frame else None, hover_name=cfg["hierarchy"][-1], hover_data=["cycle_id", "sample_size", "supporters"], scope="africa", title=f"{level} locations — {DISPLAY_NAMES.get(metric, metric)}")
            add_geo_lines(fig, load_geojson("country"), COLORS["navy"], 2.8, "Kenya boundary")
            fig.update_geos(fitbounds="locations", visible=False, projection_scale=1.15)
            fig.update_layout(height=620, margin={"l": 0, "r": 0, "t": 45, "b": 0})
            st.plotly_chart(fig, width="stretch")

    st.divider()
    st.subheader(f"Trend — {scope_label}")
    st.caption("The trend is aggregated for the selected geography and uses respondent-weighted probability. The shaded ribbon shows the supplied 95% confidence interval; for aggregated geographies, it is the sample-size-weighted aggregation of source intervals.")
    st.plotly_chart(trend_chart(filtered, cfg, f"Cycle trend — {scope_label}"), width="stretch", key="analysis_trend_chart")

    st.subheader(f"Geography detail — {scope_label}")
    st.caption("Status distributions reflect the selected cycle and active geography filters.")
    if not current.empty:
        status_cols = [c for c in ["zone", "trend_label", "true_regime"] if c in current]
        if status_cols:
            status_columns = st.columns(len(status_cols))
            for column, col in zip(status_columns, status_cols):
                counts = current[col].value_counts().rename_axis(DISPLAY_NAMES[col]).reset_index(name="Count")
                column.dataframe(counts, hide_index=True, width="stretch")
        else:
            st.info("No status labels are supplied for this dataset and cycle.")
    else:
        st.info("No observations match the current filters.")

table = current.copy()
visible = [c for c in cfg["hierarchy"] + ["cycle_id", "cycle_date"] + cfg["historical_metrics"] + cfg["current_metrics"] if c in table.columns]
table = table[visible].drop_duplicates().sort_values(cfg["hierarchy"])
validation_table = table.copy()
table["cycle_date"] = pd.to_datetime(table["cycle_date"], errors="coerce").dt.strftime("%d %b %Y")
table = table.rename(columns={column: DISPLAY_NAMES.get(column, column.replace("_", " ").title()) for column in table.columns})

with source_tab:
    st.subheader(f"Source detail — {scope_label}")
    st.caption("The table reflects the selected cycle and sidebar filters. Use the downloads for further analysis.")
    st.dataframe(table, hide_index=True, width="stretch")
    q1, q2 = st.columns(2)
    with q1:
        csv = table.to_csv(index=False).encode("utf-8")
        st.download_button("Download filtered CSV", csv, file_name=f"{key(dataset_name)}_cycle_{selected_cycle}.csv", mime="text/csv")
    with q2:
        st.download_button("Download filtered Excel", dataframe_download(table), file_name=f"{key(dataset_name)}_cycle_{selected_cycle}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with quality_tab:
    st.subheader("Data quality and interpretation")
    shape_keys = {feature["properties"].get("subcounty_geo_key") for feature in load_geojson("subcounty").get("features", [])}
    quality_source = map_source[["county", "sub_county"]].drop_duplicates() if level == "County" and "sub_county" in map_source.columns else pd.DataFrame(columns=["county", "sub_county"])
    quality_source["geo_key"] = quality_source["county"].map(key) + "__" + quality_source["sub_county"].map(key)
    quality_fallbacks = quality_source.loc[~quality_source["geo_key"].isin(shape_keys), ["county", "sub_county"]].copy()
    checks = {
        "Rows in selected cycle": len(validation_table),
        "Duplicate geography/cycle rows": int(validation_table.duplicated(subset=cfg["hierarchy"] + ["cycle_id"]).sum()),
        "Missing sample size": int(validation_table["sample_size"].isna().sum()) if "sample_size" in validation_table else 0,
        "Missing current probability": int(validation_table[prob].isna().sum()) if prob in validation_table else 0,
        "Boundary assets available": all(path.exists() for path in BOUNDARY_FILES.values()),
        "Coordinate fallback sub-counties in selected layer": len(quality_fallbacks),
    }
    st.json(checks)
    if not quality_fallbacks.empty:
        st.caption("Fallback entries remain visible as orange coordinate markers because the reference shapefile has no one-to-one polygon match for the source name.")
        st.dataframe(quality_fallbacks.rename(columns={"county": "County", "sub_county": "Sub-county"}), hide_index=True, width="stretch")
    st.write("The current-cycle view is determined by the maximum valid cycle in the selected workbook. Future-dated source cycles remain visible.")

with help_tab:
    st.subheader("How to use this dashboard")
    st.caption("Use the sidebar filters to move from a national overview to a county, sub-county, or ward-level review.")

    st.markdown("**1. Choose the dataset and display mode**")
    st.write("Select the election dataset, then choose Cycle view for a specific polling cycle or Latest assessment for indicators derived from the full historical series.")

    st.markdown("**2. Select the geography**")
    st.write("Choose National, County, Sub-county, or Ward where available. County and sub-county selections filter the map, trend, geography summary, and source detail together.")

    st.markdown("**3. Move through cycles**")
    st.write("In Cycle view, use the Cycle timeline above the map to review the selected cycle longitudinally. Latest assessment disables the timeline because its zone and trend indicators summarize historical data rather than one cycle.")

    st.markdown("**4. Read the map**")
    st.write("Use the Map metric selector and hover over areas for the underlying values. The dark outline is the Kenya boundary. Orange diamonds are coordinate fallback markers for source geographies without a one-to-one reference polygon; these records remain available in Source detail.")

    st.markdown("**5. Read the trend**")
    st.write("The blue series is the respondent-weighted probability by cycle. The shaded band is the supplied 95% confidence interval. The orange dashed line is the fitted regression line. The title reports the slope per cycle, its 95% confidence interval, and p-value.")

    st.markdown("**6. Interpret leadership indicators**")
    st.write("Zone and trend label are derived indicators for the Latest assessment view. A slope confidence interval containing zero means the direction is uncertain. These indicators describe polling patterns and are not certified election results.")

    st.markdown("**7. Review and download data**")
    st.write("Use Source detail to inspect the filtered records and download CSV or Excel. Use Data quality to review missing values, duplicate geography-cycle rows, boundary matches, and coordinate fallbacks.")

    st.info("For a new workbook, run the release validation pipeline before promotion. It recomputes derived indicators and creates an audit comparing the supplied and computed Latest_Snapshot values.")

st.caption("Sources: Data for Decision workbooks in data/; Kenya country, county, and sub-county boundaries adapted from the cdc-kenya-footprint reference repository.")
