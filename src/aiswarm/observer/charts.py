"""Headless charts: trends across runs (a dashed line marks a harness change before a run) and the private-message network."""

import math
from collections.abc import Mapping
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


def network_chart(path: Path, title: str, nodes: dict[str, str], edges: Mapping[tuple[str, str], int]) -> Path:
    """Who wrote privately to whom: agents on a circle, one arrow per direction, wider for more messages."""
    fig, ax = plt.subplots(figsize=(6, 6))
    n = len(nodes)
    pos = {agent: (math.cos(math.pi / 2 - 2 * math.pi * i / n), math.sin(math.pi / 2 - 2 * math.pi * i / n))
           for i, agent in enumerate(nodes)}
    top = max(edges.values(), default=1)
    for (sender, recipient), count in sorted(edges.items()):
        (x1, y1), (x2, y2) = pos[sender], pos[recipient]
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(
            arrowstyle="-|>", color="tab:blue", lw=1 + 5 * count / top, shrinkA=20, shrinkB=20,
            connectionstyle="arc3,rad=0.15"))  # curved, so a→b and b→a do not overlap
        ax.text((x1 + x2) / 2 + 0.08 * (y2 - y1), (y1 + y2) / 2 - 0.08 * (x2 - x1), str(count), fontsize=9,
                ha="center", va="center", color="tab:blue")
    for agent, (x, y) in pos.items():
        ax.scatter([x], [y], s=600, color="white", edgecolors="black", zorder=3)
        ax.text(x, y - 0.2, nodes[agent], ha="center", va="top", fontsize=9)
    ax.set_xlim(-1.5, 1.5)
    ax.set_ylim(-1.5, 1.5)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(title)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
