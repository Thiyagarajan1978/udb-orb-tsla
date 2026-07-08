"""UDB-ORB-TSLA — Streamlit dashboard (B Square style, port 8080).

Reads everything from the SQLite ledger written by the backtest/live runners. Pick a run
in the sidebar; the pages show the summary, equity curve, trade log, daily review,
no-trade reasons, and the live event stream.

Run:  streamlit run ui/app.py --server.port 8080
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from udb_orb.config import db_path, load_config  # noqa: E402
from udb_orb.db.database import Database  # noqa: E402

st.set_page_config(page_title="UDB-ORB TSLA", page_icon="📈", layout="wide")

# ---- B Square palette ----
ACCENT = "#1e88e5"
GREEN = "#07aa4b"
RED = "#e53935"

st.markdown(
    f"""
    <style>
      .block-container {{ padding-top: 1.2rem; }}
      div[data-testid="stMetricValue"] {{ font-size: 1.4rem; }}
      h1, h2, h3 {{ color: {ACCENT}; }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=30)
def _load(dbfile: str):
    with Database(dbfile) as db:
        runs = db.list_runs()
    return runs


def _fmt(x, sign=False):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return (f"{x:+.2f}" if sign else f"{x:.2f}")


def main():
    cfg = load_config()
    dbfile = str(db_path(cfg))
    if not Path(dbfile).exists():
        st.warning("No database yet. Run a backtest first: `python cli.py backtest --start ... --end ...`")
        st.stop()

    runs = _load(dbfile)
    if runs.empty:
        st.warning("Database has no runs yet. Run a backtest or start the live loop.")
        st.stop()

    st.sidebar.title("📈 UDB-ORB TSLA")
    st.sidebar.caption("Adaptive TP + Reversal · 5-minute ORB")
    runs["label"] = runs.apply(
        lambda r: f"#{r['id']} · {r['kind']} · {r['start_date'] or ''}→{r['end_date'] or 'live'}", axis=1)
    sel = st.sidebar.selectbox("Run", runs["label"].tolist())
    run_id = int(runs.loc[runs["label"] == sel, "id"].iloc[0])
    run_row = runs.loc[runs["id"] == run_id].iloc[0]

    with Database(dbfile) as db:
        trades = db.trades_df(run_id)
        events = db.events_df(run_id)
        days = db.days_df(run_id)

    st.title(f"TSLA ORB — {run_row['profile']}")
    st.caption(f"Run #{run_id} · {run_row['kind']} · created {run_row['created_utc']}")

    # ---- summary metrics from stored trades ----
    from udb_orb.engine.metrics import Summary
    s = _summary_from_frames(trades, days)

    c = st.columns(6)
    c[0].metric("Net P&L", _fmt(s.net_pnl, True))
    c[1].metric("Trades", f"{s.trades}")
    c[2].metric("Win rate", f"{s.win_rate:.1f}%")
    c[3].metric("Profit factor", _fmt(s.profit_factor))
    c[4].metric("Worst day", _fmt(s.worst_day, True))
    c[5].metric("Reversals", f"{s.reversal_entries}")

    tabs = st.tabs(["Equity", "Trade log", "Daily review", "No-trade days", "Events / alerts"])

    with tabs[0]:
        _equity_tab(trades)
    with tabs[1]:
        _trade_log_tab(trades)
    with tabs[2]:
        _daily_tab(days)
    with tabs[3]:
        _no_trade_tab(days)
    with tabs[4]:
        _events_tab(events)


def _summary_from_frames(trades: pd.DataFrame, days: pd.DataFrame):
    from types import SimpleNamespace
    s = SimpleNamespace(net_pnl=0.0, trades=0, wins=0, losses=0, win_rate=0.0,
                        profit_factor=None, worst_day=None, reversal_entries=0)
    if not trades.empty:
        s.trades = len(trades)
        s.wins = int((trades["pnl_total"] >= 0).sum())
        s.losses = int((trades["pnl_total"] < 0).sum())
        s.net_pnl = float(trades["pnl_total"].sum())
        s.win_rate = s.wins * 100.0 / s.trades if s.trades else 0.0
        gp = float(trades.loc[trades["pnl_total"] >= 0, "pnl_total"].sum())
        gl = float(-trades.loc[trades["pnl_total"] < 0, "pnl_total"].sum())
        s.profit_factor = (gp / gl) if gl > 0 else None
        s.reversal_entries = int(trades["is_reversal"].sum())
    if not days.empty:
        td = days.loc[days["has_trades"] == 1, "day_net"]
        if not td.empty:
            s.worst_day = float(td.min())
    return s


def _equity_tab(trades: pd.DataFrame):
    if trades.empty:
        st.info("No trades.")
        return
    t = trades.copy()
    t["exit_ts"] = pd.to_datetime(t["exit_ts"])
    t = t.sort_values("exit_ts")
    t["equity"] = t["pnl_total"].cumsum()
    st.line_chart(t.set_index("exit_ts")["equity"], height=340)
    peak = t["equity"].cummax()
    dd = (t["equity"] - peak)
    st.caption(f"Max drawdown: {dd.min():+.2f}  ·  Final equity: {t['equity'].iloc[-1]:+.2f}")


def _trade_log_tab(trades: pd.DataFrame):
    if trades.empty:
        st.info("No trades.")
        return
    show = trades[["day", "direction", "entry_ts", "entry_price", "exit_ts", "exit_price",
                   "qty", "part1_pnl", "pnl_total", "reason", "duration_bars"]].copy()
    show = show.sort_values("exit_ts", ascending=False)
    st.dataframe(show, use_container_width=True, height=520)
    st.download_button("Download CSV", show.to_csv(index=False), "trades.csv", "text/csv")


def _daily_tab(days: pd.DataFrame):
    td = days[days["has_trades"] == 1].copy()
    if td.empty:
        st.info("No trade days.")
        return
    td = td[["date", "day_name", "t1", "entry_price", "exit_price", "exit_reason",
             "duration_bars", "day_net", "or_width"]].sort_values("date", ascending=False)
    st.dataframe(td, use_container_width=True, height=520)


def _no_trade_tab(days: pd.DataFrame):
    nt = days[days["has_trades"] == 0].copy()
    if nt.empty:
        st.info("Every session had a trade.")
        return
    counts = nt["no_trade_reason"].value_counts().rename_axis("reason").reset_index(name="days")
    st.bar_chart(counts.set_index("reason")["days"])
    st.dataframe(nt[["date", "day_name", "no_trade_reason", "or_width"]].sort_values("date", ascending=False),
                 use_container_width=True, height=360)


def _events_tab(events: pd.DataFrame):
    if events.empty:
        st.info("No events.")
        return
    ev = events[["ts", "type", "direction", "price", "qty", "pnl", "reason", "note", "alerted"]].copy()
    ev = ev.sort_values("ts", ascending=False)
    st.dataframe(ev, use_container_width=True, height=520)


if __name__ == "__main__":
    main()
