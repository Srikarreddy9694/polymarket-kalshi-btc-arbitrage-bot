#!/usr/bin/env python3
"""
Paper Trading Analyzer — reads paper trading results from the database and generates a report.

Usage:
    python scripts/analyze_paper.py              # Analyze all data
    python scripts/analyze_paper.py --days 7     # Last 7 days only

Output:
    - Console summary with key metrics
    - Recommendation (GO / NO-GO) based on configurable thresholds
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from storage.database import Database

logger = logging.getLogger("analyze_paper")

# Go/No-Go thresholds
MIN_PROFITABLE_HIT_RATE = 5.0       # At least 5% of scans find profit
MIN_AVG_MARGIN_USD = 0.005           # Average margin > $0.005
MIN_OPPORTUNITIES_PER_DAY = 10       # At least 10 opps per day
MAX_ACCEPTABLE_ERROR_RATE = 10.0     # Less than 10% scan errors


def analyze(db: Database, days: int = 0) -> dict:
    """
    Analyze paper trading results from the database.

    Returns a structured report dict.
    """
    # Fetch all paper_opportunity events
    events = db.get_events(event_type="paper_opportunity", days=days)

    if not events:
        return {
            "status": "NO DATA",
            "message": "No paper trading data found in database.",
            "recommendation": "NO-GO",
        }

    # Parse the opportunities
    margins = []
    strategies = {}
    for event in events:
        try:
            details = json.loads(event.get("details", "{}"))
            net_margin = details.get("net_margin", 0.0)
            strategy = details.get("strategy", "unknown")

            if net_margin > 0:
                margins.append(net_margin)
            strategies[strategy] = strategies.get(strategy, 0) + 1
        except (json.JSONDecodeError, AttributeError):
            continue

    total_events = len(events)
    profitable = len(margins)
    hit_rate = (profitable / total_events) * 100 if total_events > 0 else 0.0
    avg_margin = sum(margins) / len(margins) if margins else 0.0
    total_pnl = sum(margins)

    # Time span
    if events:
        first_ts = events[0].get("created_at", "")
        last_ts = events[-1].get("created_at", "")
    else:
        first_ts = last_ts = "unknown"

    # Go/No-Go decision
    passes = []
    passes.append(("hit_rate", hit_rate >= MIN_PROFITABLE_HIT_RATE, f"{hit_rate:.1f}% >= {MIN_PROFITABLE_HIT_RATE}%"))
    passes.append(("avg_margin", avg_margin >= MIN_AVG_MARGIN_USD, f"${avg_margin:.4f} >= ${MIN_AVG_MARGIN_USD:.4f}"))

    all_pass = all(p[1] for p in passes)
    recommendation = "GO ✅" if all_pass else "NO-GO ❌"

    report = {
        "period": {
            "from": first_ts,
            "to": last_ts,
            "days_filter": days if days > 0 else "all",
        },
        "scans": {
            "total": total_events,
            "profitable": profitable,
            "hit_rate_pct": round(hit_rate, 2),
        },
        "pnl": {
            "simulated_total_usd": round(total_pnl, 4),
            "avg_margin_usd": round(avg_margin, 4),
            "max_margin_usd": round(max(margins), 4) if margins else 0.0,
            "min_margin_usd": round(min(margins), 4) if margins else 0.0,
        },
        "strategies": strategies,
        "go_no_go": {
            "checks": [{"gate": p[0], "passed": p[1], "detail": p[2]} for p in passes],
            "recommendation": recommendation,
        },
    }

    return report


def print_report(report: dict) -> None:
    """Pretty-print the analysis report."""
    print("\n" + "=" * 50)
    print("  📊 PAPER TRADING ANALYSIS REPORT")
    print("=" * 50)

    if report.get("status") == "NO DATA":
        print(f"\n  {report['message']}")
        print(f"  Recommendation: {report['recommendation']}")
        return

    period = report["period"]
    print(f"\n  Period: {period['from']} → {period['to']}")

    scans = report["scans"]
    print(f"\n  📈 Scans")
    print(f"     Total: {scans['total']}")
    print(f"     Profitable: {scans['profitable']}")
    print(f"     Hit Rate: {scans['hit_rate_pct']:.1f}%")

    pnl = report["pnl"]
    print(f"\n  💰 P&L")
    print(f"     Simulated Total: ${pnl['simulated_total_usd']:+.4f}")
    print(f"     Avg Margin: ${pnl['avg_margin_usd']:.4f}")
    print(f"     Max Margin: ${pnl['max_margin_usd']:.4f}")
    print(f"     Min Margin: ${pnl['min_margin_usd']:.4f}")

    strategies = report.get("strategies", {})
    if strategies:
        print(f"\n  🎯 Strategies")
        for strat, count in strategies.items():
            print(f"     {strat}: {count}")

    gng = report["go_no_go"]
    print(f"\n  🚦 Go/No-Go Decision")
    for check in gng["checks"]:
        status = "✅" if check["passed"] else "❌"
        print(f"     {status} {check['gate']}: {check['detail']}")
    print(f"\n  ➡️  Recommendation: {gng['recommendation']}")
    print("=" * 50 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Analyze paper trading results")
    parser.add_argument("--days", type=int, default=0, help="Analyze last N days (0=all)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    settings = Settings()
    db = Database(db_path=settings.DB_PATH)

    report = analyze(db, days=args.days)
    print_report(report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
