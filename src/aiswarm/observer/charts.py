"""Headless trend charts. A dashed line marks a harness change before a run."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def marker_positions(markers: list[int]) -> list[float]:
    return [m - 0.5 for m in markers]


def trend_chart(path: Path, title: str, labels: list[str], series: dict[str, list[float | None]],
                markers: list[int], ylabel: str = "") -> Path:
    fig, ax = plt.subplots(figsize=(8, 4))
    xs = list(range(len(labels)))
    plotted = False
    for name, values in sorted(series.items()):
        points = [(x, v) for x, v in zip(xs, values) if v is not None]
        if points:
            px, py = zip(*points)
            ax.plot(px, py, marker="o", label=name)
            plotted = True
    for x in marker_positions(markers):
        ax.axvline(x, color="grey", linestyle="--", linewidth=1)
    ax.set_xticks(xs, labels, rotation=30, ha="right")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    if plotted:
        ax.legend(fontsize="small")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
