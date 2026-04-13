#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def _load(df_path: Path) -> pd.DataFrame:
    df = pd.read_csv(df_path)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def _equity_curve(df: pd.DataFrame, out: Path) -> None:
    plt.figure(figsize=(11, 4.6))
    plt.plot(df["Date"], df["Day End Equity"], linewidth=2.0)
    plt.title("G1-412837 Equity Curve (Day End Equity)")
    plt.xlabel("Date")
    plt.ylabel("Equity ($)")
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(out, dpi=180)
    plt.close()


def _drawdown_curve(df: pd.DataFrame, out: Path) -> None:
    plt.figure(figsize=(11, 4.6))
    plt.plot(df["Date"], df["Drawdown %"], color="#c1121f", linewidth=1.9)
    plt.title("G1-412837 Drawdown Curve")
    plt.xlabel("Date")
    plt.ylabel("Drawdown (%)")
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(out, dpi=180)
    plt.close()


def _monthly_heatmap(df: pd.DataFrame, out: Path) -> None:
    d = df.copy()
    d["Year"] = d["Date"].dt.year
    d["Month"] = d["Date"].dt.month
    monthly = d.groupby(["Year", "Month"], as_index=False).agg(
        month_start=("Day Start Equity", "first"),
        month_end=("Day End Equity", "last"),
    )
    monthly["return_pct"] = (monthly["month_end"] / monthly["month_start"] - 1.0) * 100.0
    pivot = monthly.pivot(index="Year", columns="Month", values="return_pct").sort_index()

    fig, ax = plt.subplots(figsize=(11, 4.8))
    im = ax.imshow(pivot.fillna(0).values, aspect="auto", cmap="RdYlGn")
    ax.set_title("G1-412837 Monthly Return Heatmap (%)")
    ax.set_xlabel("Month")
    ax.set_ylabel("Year")
    ax.set_xticks(range(12), labels=[str(i) for i in range(1, 13)])
    ax.set_yticks(range(len(pivot.index)), labels=[str(y) for y in pivot.index])
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Return %")

    # annotate cells that exist in pivot
    for r, year in enumerate(pivot.index):
        for c in range(12):
            v = pivot.iloc[r, c]
            if pd.notna(v):
                ax.text(c, r, f"{v:.1f}", ha="center", va="center", fontsize=8, color="black")
    plt.tight_layout()
    plt.savefig(out, dpi=180)
    plt.close()


def _worst_day_week(df: pd.DataFrame, out: Path) -> None:
    d = df.copy()
    d["DateOnly"] = d["Date"].dt.date
    daily = d[["DateOnly", "PnL ($)"]].copy()
    daily["DateOnly"] = pd.to_datetime(daily["DateOnly"])
    daily = daily.sort_values("DateOnly")

    worst_days = daily.nsmallest(10, "PnL ($)").copy()
    weekly = daily.set_index("DateOnly").resample("W-FRI").sum(numeric_only=True).reset_index()
    worst_weeks = weekly.nsmallest(10, "PnL ($)").copy()

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))

    axes[0].barh(worst_days["DateOnly"].dt.strftime("%Y-%m-%d"), worst_days["PnL ($)"], color="#c1121f")
    axes[0].set_title("Worst 10 Days by PnL ($)")
    axes[0].set_xlabel("PnL ($)")
    axes[0].invert_yaxis()

    axes[1].barh(worst_weeks["DateOnly"].dt.strftime("%Y-%m-%d"), worst_weeks["PnL ($)"], color="#9d0208")
    axes[1].set_title("Worst 10 Weeks (Fri close week sum)")
    axes[1].set_xlabel("PnL ($)")
    axes[1].invert_yaxis()

    for ax in axes:
        ax.grid(axis="x", alpha=0.25)

    plt.tight_layout()
    plt.savefig(out, dpi=180)
    plt.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate chart assets for G1 dossier.")
    ap.add_argument("--daybyday-csv", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    csv_path = Path(args.daybyday_csv).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    df = _load(csv_path)
    _equity_curve(df, out_dir / "g1_equity_curve.png")
    _drawdown_curve(df, out_dir / "g1_drawdown_curve.png")
    _monthly_heatmap(df, out_dir / "g1_monthly_heatmap.png")
    _worst_day_week(df, out_dir / "g1_worst_day_week.png")

    print(str(out_dir / "g1_equity_curve.png"))
    print(str(out_dir / "g1_drawdown_curve.png"))
    print(str(out_dir / "g1_monthly_heatmap.png"))
    print(str(out_dir / "g1_worst_day_week.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

