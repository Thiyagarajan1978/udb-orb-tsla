#!/usr/bin/env python
"""Email a trade-detail report for a backtest/live run via Resend.

Usage:
  python scripts/email_report.py --run 4
  python scripts/email_report.py --latest
  python scripts/email_report.py --run 4 --to someone@example.com
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from udb_orb.config import db_path, get_env, load_config  # noqa: E402
from udb_orb.db.database import Database  # noqa: E402


def _fmt_ts(ts):
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return "—"
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")


def build_report(run_row, trades: pd.DataFrame, days: pd.DataFrame):
    net = float(trades["pnl_total"].sum()) if not trades.empty else 0.0
    wins = int((trades["pnl_total"] >= 0).sum()) if not trades.empty else 0
    n = len(trades)
    wr = (wins * 100.0 / n) if n else 0.0
    revs = int(trades["is_reversal"].sum()) if not trades.empty else 0
    rng = f"{run_row['start_date']} &rarr; {run_row['end_date']}"
    rng_text = f"{run_row['start_date']} -> {run_row['end_date']}"

    # ---- plain text (ASCII-safe for Windows consoles) ----
    lines = [
        f"UDB-ORB TSLA - Trade Report (run #{run_row['id']})",
        f"Profile : {run_row['profile']}",
        f"Range   : {rng_text}",
        f"Result  : {n} trades | {wr:.1f}% WR | Net {net:+.2f} | {revs} reversal(s)",
        "",
        f"{'Day':<11}{'Dir':<9}{'Entry':<20}{'@':<9}{'Exit':<20}{'@':<9}{'Qty':<5}{'PnL':<9}{'Reason':<10}",
        "-" * 110,
    ]
    for _, t in trades.iterrows():
        lines.append(
            f"{t['day']:<11}{t['direction']:<9}{_fmt_ts(t['entry_ts']):<20}{t['entry_price']:<9.2f}"
            f"{_fmt_ts(t['exit_ts']):<20}{t['exit_price']:<9.2f}{t['qty']:<5.2f}"
            f"{t['pnl_total']:<+9.2f}{t['reason']:<10}"
        )
    text = "\n".join(lines)

    # ---- HTML ----
    rows = ""
    for _, t in trades.iterrows():
        color = "#07aa4b" if t["pnl_total"] >= 0 else "#e53935"
        rev = " 🔁" if t["is_reversal"] else ""
        rows += (
            f"<tr>"
            f"<td>{t['day']}</td>"
            f"<td>{t['direction']}{rev}</td>"
            f"<td>{_fmt_ts(t['entry_ts'])}</td><td style='text-align:right'>{t['entry_price']:.2f}</td>"
            f"<td>{_fmt_ts(t['exit_ts'])}</td><td style='text-align:right'>{t['exit_price']:.2f}</td>"
            f"<td style='text-align:right'>{t['qty']:.2f}</td>"
            f"<td style='text-align:right;color:{color};font-weight:600'>{t['pnl_total']:+.2f}</td>"
            f"<td>{t['reason']}</td>"
            f"</tr>"
        )
    net_color = "#07aa4b" if net >= 0 else "#e53935"
    html = f"""\
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:820px">
  <h2 style="color:#1e88e5;margin-bottom:2px">UDB-ORB TSLA — Trade Report</h2>
  <div style="color:#555">Run #{run_row['id']} · {run_row['profile']}</div>
  <div style="color:#555;margin-bottom:10px">Range: {rng}</div>
  <div style="font-size:15px;margin:12px 0">
    <b>{n}</b> trades ·
    <b>{wr:.1f}%</b> win rate ·
    Net <b style="color:{net_color}">{net:+.2f}</b> ·
    <b>{revs}</b> reversal(s)
  </div>
  <table cellpadding="6" cellspacing="0"
         style="border-collapse:collapse;width:100%;font-size:13px;border:1px solid #ddd">
    <thead>
      <tr style="background:#1e88e5;color:#fff;text-align:left">
        <th>Day</th><th>Dir</th><th>Entry</th><th>@</th><th>Exit</th><th>@</th>
        <th>Qty</th><th>PnL</th><th>Reason</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="color:#888;font-size:12px;margin-top:14px">
    Alerts-only research system · Adaptive TP + Reversal · TSLA 5-minute ORB ·
    P&L is gross per unit, 5-minute intrabar fills are approximate.
  </p>
</div>"""
    subject = f"UDB-ORB TSLA report | {rng_text} | {n} trades | net {net:+.2f}"
    return subject, text, html


def send_resend(subject: str, text: str, html: str, to: str) -> tuple[bool, str]:
    import requests
    key = get_env("RESEND_API_KEY")
    frm = get_env("ALERT_FROM") or "onboarding@resend.dev"
    if not key:
        return False, "RESEND_API_KEY missing"
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"from": frm, "to": [to], "subject": subject, "text": text, "html": html},
        timeout=30,
    )
    ok = r.status_code < 300
    return ok, (r.text[:300] if not ok else r.json().get("id", "sent"))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", type=int)
    g.add_argument("--latest", action="store_true")
    ap.add_argument("--to", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print report, do not send")
    args = ap.parse_args()

    cfg = load_config()
    to = args.to or get_env("ALERT_TO")
    with Database(db_path(cfg)) as db:
        runs = db.list_runs()
        if args.latest:
            run_id = int(runs["id"].iloc[0])
        else:
            run_id = args.run
        run_row = runs.loc[runs["id"] == run_id].iloc[0]
        trades = db.trades_df(run_id)

    subject, text, html = build_report(run_row, trades, None)
    print(text)
    print()
    if args.dry_run:
        print("[dry-run] not sent")
        return
    if not to:
        print("No recipient (set ALERT_TO in .env or pass --to)")
        return
    ok, info = send_resend(subject, text, html, to)
    print(f"Email to {to}: {'SENT ('+info+')' if ok else 'FAILED — '+info}")


if __name__ == "__main__":
    main()
