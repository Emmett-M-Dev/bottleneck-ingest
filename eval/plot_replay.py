"""Dissertation figures from the longitudinal replay log.

    python -m eval.plot_replay --profile foyle

Reads outputs/replay_<profile>.jsonl (eval/replay.py) and writes three PNGs
to outputs/ — one per longitudinal claim:

    replay_detection_<p>.png  per-type F1 tracking the moving ground truth,
                              with the truth's own growth underneath
    replay_learning_<p>.png   learned-fix retrieval climbing as the corpus
                              grows — the learning loop made visible
    replay_gate_<p>.png       simulated Gate-2 decisions per tick — the
                              safety valve catching detector wobble

Static light-mode figures for the Word write-up (print target). Colors are
the validated reference categorical palette (dataviz skill); the sub-3:1
aqua/yellow slots carry direct line-end labels as the relief rule requires.
No heavy imports — matplotlib only, safe in any process.
"""

from __future__ import annotations

import argparse
import json
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config

TYPES = ("delay", "repetition", "rework")

# Validated reference palette, light mode (dataviz skill references/palette.md).
SERIES = {"delay": "#2a78d6", "repetition": "#1baf7a", "rework": "#eda100"}
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
GOOD, CRITICAL = "#0ca30c", "#d03b3b"

plt.rcParams.update({
    "font.family": ["Segoe UI", "sans-serif"],
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK_2,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True, "legend.frameon": False,
})


def _load(profile: str) -> list[dict]:
    path = config.replay_log_path(profile)
    if not path.exists():
        raise SystemExit(f"{path.name} missing — run eval.replay --profile {profile} first")
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in records if r.get("record") == "tick"]


def _endlabels(ax, x: float, items: list[tuple[float, str]]) -> None:
    """Direct labels at the line ends, dodged apart when values coincide
    (the relief rule for the sub-3:1 slots — labels, not color alone)."""
    lo, hi = ax.get_ylim()
    gap = 0.055 * (hi - lo)
    placed: list[float] = []
    for y, text in sorted(items, key=lambda p: -p[0]):
        pos = y if not placed else min(y, placed[-1] - gap)
        placed.append(pos)
        ax.annotate(text, (x, pos), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=INK_2)


def fig_detection(ticks: list[dict], profile: str) -> plt.Figure:
    x = [t["tick"] for t in ticks]
    nan = float("nan")
    fig, (top, bot) = plt.subplots(2, 1, sharex=True, figsize=(7.2, 5.2),
                                   height_ratios=[3, 2], constrained_layout=True)
    ends = []
    for k in TYPES:
        # Mask ticks where the pattern exists in neither truth nor detection —
        # a vacuous F1 of 1.0 there would paint a fake perfect start.
        y = [nan if not (d["tp"] + d["fp"] + d["fn"]) else d["f1"]
             for d in (t["detection"][k] for t in ticks)]
        top.plot(x, y, color=SERIES[k], linewidth=2, marker="o", markersize=5)
        ends.append((y[-1], k))
    macro = [t["detection"]["macro_f1"] for t in ticks]
    top.plot(x, macro, color=INK, linewidth=2.6)
    ends.append((macro[-1], "macro"))
    top.set_ylim(-0.05, 1.08)
    _endlabels(top, x[-1], ends)
    top.set_ylabel("F1 vs tick ground truth")
    top.set_title(f"Detection tracks the moving truth — {profile}",
                  color=INK, loc="left", fontsize=11)
    top.legend(["delay", "repetition", "rework", "macro F1"],
               loc="lower right", fontsize=8.5)

    ends = []
    for k in TYPES:  # truth size = tp + fn, derived from the scored sets
        gt = [t["detection"][k]["tp"] + t["detection"][k]["fn"] for t in ticks]
        bot.plot(x, gt, color=SERIES[k], linewidth=2, marker="o", markersize=5)
        ends.append((gt[-1], k))
    bot.set_ylim(bottom=-0.2)
    _endlabels(bot, x[-1], ends)
    bot.set_ylabel("cases in ground truth")
    bot.set_xlabel("week (tick)")
    bot.set_xticks(x)
    bot.set_title("The truth itself grows as injected patterns land",
                  color=INK_2, loc="left", fontsize=9.5)
    return fig


def fig_learning(ticks: list[dict], profile: str) -> plt.Figure:
    x = [t["tick"] for t in ticks]
    fig, (top, bot) = plt.subplots(2, 1, sharex=True, figsize=(7.2, 5.2),
                                   height_ratios=[3, 2], constrained_layout=True)
    nan = float("nan")
    hit = [t["retrieval"]["learned_hit_rate"] for t in ticks]
    rel = [t["retrieval"]["relevant_learned_hit_rate"] for t in ticks]
    hit = [nan if v is None else v for v in hit]
    rel = [nan if v is None else v for v in rel]
    # The two rates frequently coincide, so the second series is marker-only
    # (open circles riding the line) rather than a line hidden underneath.
    top.plot(x, hit, color=SERIES["delay"], linewidth=2, marker="o", markersize=5)
    top.plot(x, rel, color=SERIES["repetition"], linestyle="none", marker="o",
             markersize=9, markerfacecolor="none", markeredgewidth=2)
    last = max(i for i, v in enumerate(hit) if not math.isnan(v))
    top.set_ylim(-0.05, 1.08)
    _endlabels(top, x[last], [(hit[last], "any learned fix"),
                              (rel[last], "matching type")])
    top.set_ylabel("findings retrieving a learned fix")
    top.set_title(f"Approved fixes come back as evidence — {profile}",
                  color=INK, loc="left", fontsize=11)
    top.legend(["learned fix in top-3", "learned fix of matching type"],
               loc="lower right", fontsize=8.5)

    corpus = [t["retrieval"]["learned_corpus_size"] for t in ticks]
    bot.step(x, corpus, where="post", color=INK, linewidth=2)
    bot.set_ylim(bottom=-0.1)
    bot.set_ylabel("learned resolutions")
    bot.set_xlabel("week (tick)")
    bot.set_xticks(x)
    bot.set_yticks(range(0, max(corpus) + 2))
    bot.set_title("The corpus the gate curates (approvals only, deduplicated)",
                  color=INK_2, loc="left", fontsize=9.5)
    return fig


def fig_gate(ticks: list[dict], profile: str) -> plt.Figure:
    x = [t["tick"] for t in ticks]
    approved = [t["gate"]["approved"] for t in ticks]
    rejected = [t["gate"]["rejected"] for t in ticks]
    fig, ax = plt.subplots(figsize=(7.2, 3.4), constrained_layout=True)
    # 2px surface gap between stacked segments: linewidth on the surface color.
    ax.bar(x, approved, color=GOOD, edgecolor=SURFACE, linewidth=1.5, width=0.62)
    ax.bar(x, rejected, bottom=approved, color=CRITICAL,
           edgecolor=SURFACE, linewidth=1.5, width=0.62)
    for xi, a, r in zip(x, approved, rejected):
        if a:
            ax.annotate(str(a), (xi, a / 2), ha="center", va="center",
                        fontsize=8.5, color="#ffffff")
        if r:
            ax.annotate(f"{r} rejected", (xi, a + r + 0.12), ha="center",
                        va="bottom", fontsize=8.5, color=INK_2)
    ax.set_ylim(0, max(a + r for a, r in zip(approved, rejected)) + 1.2)
    ax.set_ylabel("suggested fixes")
    ax.set_xlabel("week (tick)")
    ax.set_xticks(x)
    ax.set_title(f"Simulated Gate 2 per tick — {profile} "
                 "(approved ✓ / rejected ✗)", color=INK, loc="left", fontsize=11)
    ax.legend(["approved", "rejected"], loc="upper left", fontsize=8.5)
    return fig


def main() -> None:
    ap = argparse.ArgumentParser(description="Figures from the replay log")
    ap.add_argument("--profile", required=True, choices=sorted(config.MESSY_PROFILES))
    args = ap.parse_args()

    ticks = _load(args.profile)
    for name, fn in (("detection", fig_detection), ("learning", fig_learning),
                     ("gate", fig_gate)):
        fig = fn(ticks, args.profile)
        out = config.OUTPUTS / f"replay_{name}_{args.profile}.png"
        fig.savefig(out, dpi=200)
        plt.close(fig)
        print(f"-> {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
