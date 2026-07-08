"""Summary statistics derived from an engine Result — mirrors the Pine Summary Table
(win rate, net P&L, profit factor, expectancy, exit-reason counts, worst day)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .orb_engine import Result


@dataclass
class Summary:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss_abs: float = 0.0
    avg_win: float = 0.0
    avg_loss_abs: float = 0.0
    profit_factor: float | None = None
    expectancy: float = 0.0
    biggest_win: float | None = None
    biggest_loss: float | None = None
    worst_day: float | None = None
    best_day: float | None = None
    trade_days: int = 0
    no_trade_days: int = 0
    total_days: int = 0
    trade_day_pct: float = 0.0
    reversal_entries: int = 0
    # success / failure (BE Stop @ ~$0 and any pnl<=0 counts as FAILURE)
    successes: int = 0
    failures: int = 0
    be_stop_failures: int = 0
    # exit-reason counts
    tp_exits: int = 0
    base_sl_exits: int = 0
    be_trail_exits: int = 0
    be_stop_exits: int = 0
    be_saves: int = 0
    partial_exits: int = 0
    vwap_trail_exits: int = 0
    eod_exits: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize(result: Result) -> Summary:
    s = Summary()
    s.total_days = len(result.days)
    s.trade_days = len(result.trade_days())
    s.no_trade_days = len(result.no_trade_days())
    s.trade_day_pct = (s.trade_days * 100.0 / s.total_days) if s.total_days else 0.0

    for t in result.trades:
        s.trades += 1
        # A trade is a SUCCESS only if it netted a real profit. BE Stop exits at ~$0
        # (and any non-positive leg) are FAILURES — you cannot exit exactly at break-even.
        if t.pnl_total > 0:
            s.wins += 1
            s.successes += 1
            s.gross_profit += t.pnl_total
        else:
            s.losses += 1
            s.failures += 1
            s.gross_loss_abs += abs(t.pnl_total)
        s.net_pnl += t.pnl_total
        s.biggest_win = t.pnl_total if s.biggest_win is None else max(s.biggest_win, t.pnl_total)
        s.biggest_loss = t.pnl_total if s.biggest_loss is None else min(s.biggest_loss, t.pnl_total)
        if t.is_reversal:
            s.reversal_entries += 1

        r = t.reason
        if "TP" in r:
            s.tp_exits += 1
        elif "Base SL" in r:
            s.base_sl_exits += 1
        elif "BE Stop" in r:
            s.be_stop_exits += 1
            # a BE-stop EXIT can still be a net win if a partial was banked first;
            # only count it as a failure when the trade actually lost money.
            if t.pnl_total <= 0:
                s.be_stop_failures += 1
            s.be_saves += 1
        elif "BE Trail" in r:
            s.be_trail_exits += 1
            s.be_saves += 1
        elif "VWAP" in r:
            s.vwap_trail_exits += 1
        elif "EOD" in r:
            s.eod_exits += 1

    s.partial_exits = sum(1 for e in result.events if e.type == "partial_exit")

    s.win_rate = (s.wins * 100.0 / s.trades) if s.trades else 0.0
    s.avg_win = (s.gross_profit / s.wins) if s.wins else 0.0
    s.avg_loss_abs = (s.gross_loss_abs / s.losses) if s.losses else 0.0
    s.profit_factor = (s.gross_profit / s.gross_loss_abs) if s.gross_loss_abs > 0 else None
    s.expectancy = (s.net_pnl / s.trades) if s.trades else 0.0

    day_nets = [d.day_net for d in result.trade_days()]
    if day_nets:
        s.worst_day = min(day_nets)
        s.best_day = max(day_nets)
    return s
