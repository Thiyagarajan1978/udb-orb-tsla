"""SQLite ledger for UDB-ORB-TSLA.

Stores the whole record: raw 5m bars, closed trade legs, the ordered event stream
(for the alert/audit trail), per-day summaries (trade + no-trade), and a derived
equity curve. Both the backtest runner and the live loop write through here, so the
Streamlit dashboard has one place to read from.

A `run` row groups a batch (a backtest or a live session) with its config snapshot,
so multiple experiments coexist in one DB and the UI can pick which to view.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,           -- 'backtest' | 'live'
    symbol        TEXT NOT NULL,
    profile       TEXT,
    start_date    TEXT,
    end_date      TEXT,
    created_utc   TEXT NOT NULL,
    config_json   TEXT,
    enhancements_json TEXT,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS bars (
    symbol   TEXT NOT NULL,
    ts       TEXT NOT NULL,               -- ISO ET
    open     REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    day          TEXT NOT NULL,
    direction    TEXT NOT NULL,
    is_reversal  INTEGER NOT NULL,
    entry_ts     TEXT, entry_price REAL,
    exit_ts      TEXT, exit_price REAL,
    qty          REAL, part1_pnl REAL,
    pnl_total    REAL, pnl_per_unit REAL,
    reason       TEXT, duration_bars INTEGER,
    outcome      TEXT,                    -- 'success' | 'failure' (BE Stop @ ~$0 = failure)
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER NOT NULL,
    symbol    TEXT NOT NULL,
    ts        TEXT NOT NULL,
    type      TEXT NOT NULL,
    direction TEXT, price REAL, qty REAL, pnl REAL,
    reason    TEXT, note TEXT,
    alerted   INTEGER DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS days (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    date          TEXT NOT NULL,
    day_name      TEXT,
    has_trades    INTEGER NOT NULL,
    t1            TEXT,
    entry_ts      TEXT, entry_price REAL, exit_price REAL,
    exit_reason   TEXT, duration_bars INTEGER,
    day_net       REAL,
    or_high REAL, or_low REAL, or_width REAL,
    no_trade_reason TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS ix_trades_run  ON trades(run_id);
CREATE INDEX IF NOT EXISTS ix_events_run  ON events(run_id);
CREATE INDEX IF NOT EXISTS ix_days_run    ON days(run_id);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a DB was first created."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "outcome" not in cols:
            self.conn.execute("ALTER TABLE trades ADD COLUMN outcome TEXT")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- runs -----------------------------------------------------------
    def create_run(self, kind: str, symbol: str, profile: str, start_date: str | None,
                   end_date: str | None, config: dict, enhancements: dict,
                   notes: str = "") -> int:
        cur = self.conn.execute(
            """INSERT INTO runs(kind, symbol, profile, start_date, end_date, created_utc,
                                config_json, enhancements_json, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (kind, symbol, profile, start_date, end_date,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             json.dumps(config, default=str), json.dumps(enhancements, default=str), notes),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest_run(self, kind: str | None = None) -> Optional[sqlite3.Row]:
        if kind:
            return self.conn.execute(
                "SELECT * FROM runs WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
        return self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

    def list_runs(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM runs ORDER BY id DESC", self.conn)

    # ---- bars -----------------------------------------------------------
    def upsert_bars(self, symbol: str, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        rows = [
            (symbol, ts.isoformat(), float(r["open"]), float(r["high"]), float(r["low"]),
             float(r["close"]), float(r.get("volume", 0) or 0))
            for ts, r in df.iterrows()
        ]
        self.conn.executemany(
            """INSERT INTO bars(symbol, ts, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(symbol, ts) DO UPDATE SET
                 open=excluded.open, high=excluded.high, low=excluded.low,
                 close=excluded.close, volume=excluded.volume""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def load_bars(self, symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        q = "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol=?"
        args: list[Any] = [symbol]
        if start:
            q += " AND ts >= ?"; args.append(start)
        if end:
            q += " AND ts <= ?"; args.append(end)
        q += " ORDER BY ts"
        df = pd.read_sql_query(q, self.conn, params=args)
        if df.empty:
            return df
        df["ts"] = pd.to_datetime(df["ts"])
        return df.set_index("ts")

    # ---- writing engine output -----------------------------------------
    def write_result(self, run_id: int, symbol: str, result) -> None:
        """Persist a full engine Result (trades, events, days) for a run."""
        self.conn.execute("DELETE FROM trades WHERE run_id=?", (run_id,))
        self.conn.execute("DELETE FROM events WHERE run_id=?", (run_id,))
        self.conn.execute("DELETE FROM days   WHERE run_id=?", (run_id,))

        self.conn.executemany(
            """INSERT INTO trades(run_id, symbol, day, direction, is_reversal, entry_ts,
                 entry_price, exit_ts, exit_price, qty, part1_pnl, pnl_total, pnl_per_unit,
                 reason, duration_bars, outcome)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(run_id, symbol, t.day, t.direction, int(t.is_reversal),
              _iso(t.entry_ts), t.entry_price, _iso(t.exit_ts), t.exit_price, t.qty,
              t.part1_pnl, t.pnl_total, t.pnl_per_unit, t.reason, t.duration_bars, t.outcome)
             for t in result.trades],
        )
        self.conn.executemany(
            """INSERT INTO events(run_id, symbol, ts, type, direction, price, qty, pnl, reason, note)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [(run_id, symbol, _iso(e.ts), e.type, e.direction, e.price, e.qty, e.pnl, e.reason, e.note)
             for e in result.events],
        )
        self.conn.executemany(
            """INSERT INTO days(run_id, symbol, date, day_name, has_trades, t1, entry_ts,
                 entry_price, exit_price, exit_reason, duration_bars, day_net,
                 or_high, or_low, or_width, no_trade_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(run_id, symbol, d.date, d.day_name, int(d.has_trades), d.t1, _iso(d.entry_ts),
              d.entry_price, d.exit_price, d.exit_reason, d.duration_bars, d.day_net,
              d.or_high, d.or_low, d.or_width, d.no_trade_reason)
             for d in result.days],
        )
        self.conn.commit()

    def append_events(self, run_id: int, symbol: str, events: Iterable) -> list[int]:
        """Append events (live loop) and return their new ids."""
        ids: list[int] = []
        for e in events:
            cur = self.conn.execute(
                """INSERT INTO events(run_id, symbol, ts, type, direction, price, qty, pnl, reason, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (run_id, symbol, _iso(e.ts), e.type, e.direction, e.price, e.qty, e.pnl, e.reason, e.note),
            )
            ids.append(int(cur.lastrowid))
        self.conn.commit()
        return ids

    def mark_alerted(self, event_ids: Iterable[int]) -> None:
        self.conn.executemany("UPDATE events SET alerted=1 WHERE id=?", [(i,) for i in event_ids])
        self.conn.commit()

    # ---- reading for UI -------------------------------------------------
    def trades_df(self, run_id: int) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM trades WHERE run_id=? ORDER BY exit_ts", self.conn, params=[run_id])

    def events_df(self, run_id: int) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM events WHERE run_id=? ORDER BY ts", self.conn, params=[run_id])

    def days_df(self, run_id: int) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM days WHERE run_id=? ORDER BY date", self.conn, params=[run_id])


def _iso(ts) -> str | None:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return None
    if isinstance(ts, str):
        return ts
    return pd.Timestamp(ts).isoformat()
