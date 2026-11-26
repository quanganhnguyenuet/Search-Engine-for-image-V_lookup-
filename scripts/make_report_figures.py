"""Slide-ready figures (16:9 PNG) and one data.json for the 2026-11-25 measurement report.

Reads the result files in data/processed/products-v1/eval/runs/checkpoint-2026-11-25/ so the
numbers in the figures are the numbers in the report. Values that were measured outside those
files (the same-day REST -> gRPC latency work and the 15/22 flow checks) are listed in STATIC with
where they come from.

Colour: the reference categorical palette of the dataviz skill, slots 1-3 (blue, orange, aqua),
validated for light mode; text in ink tokens, never in the series colour.

  python scripts/make_report_figures.py [--out docs/figures/2026-11-25]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data/processed/products-v1/eval/runs"
CK = RUNS / "checkpoint-2026-11-25"

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"

# Measured earlier on 2026-11-25 in this working session, not stored in the result files.
STATIC = {
    # median / worst client latency of the same 6-query think-time script, API :8000
    "optimisation": {
        "median_client_ms": {"before": 844, "after": 319},
        "worst_client_ms": {"before": 3901, "after": 561},
        # Qdrant REST vs gRPC, rank + trace in parallel, real pipeline request, median ms
        "rank_plus_trace_ms": {"before": 791, "after": 170},
    },
    # official scripts/evaluate_category.py runs on eval/category-v1 (63 queries) and multi (30)
    "category": {
        "single": {
            "off": {"p10": 0.786, "category": 0.815, "tier": 0.35},
            "on": {"p10": 0.875, "category": 0.895, "tier": 0.575},
        },
        "multi": {
            "off": {"all_intents": 0.533, "balanced": 0.53},
            "on": {"all_intents": 0.80, "balanced": 0.837},
        },
    },
    # silver-v1.1 test, scripts/evaluate_v2.py run on 2026-11-24 night (descriptive test)
    "descriptive_test_24_11": {"text": 0.579, "fusion": 0.788},
}

FLOW_LABEL = {
    "silver:single_named": "Theo tên sản phẩm",
    "silver:single_brand": "Theo hãng",
    "silver:multi_intent": "Nhiều ý (A và B)",
    "silver:multi_hop": "Quan hệ: sản phẩm khác của hãng",
    "silver:no_answer": "Ngoài phạm vi (từ chối)",
    "category": "Theo loại hàng",
    "descriptive": "Mô tả thị giác (fusion ảnh)",
    "place": "Theo địa danh",
}


def vn(text: str) -> str:
    """Vietnamese decimal comma."""
    return text.replace(".", ",")


def setup() -> None:
    names = {f.name for f in font_manager.fontManager.ttflist}
    family = next((n for n in ("Segoe UI", "Arial", "DejaVu Sans") if n in names), "DejaVu Sans")
    plt.rcParams.update(
        {
            "font.family": family,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "text.color": INK,
            "axes.labelcolor": INK2,
            "xtick.color": MUTED,
            "ytick.color": INK2,
            "axes.edgecolor": GRID,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "axes.spines.bottom": False,
            "font.size": 13,
        }
    )


def new_figure(title: str, subtitle: str = "", panels: int = 1):
    fig, axes = plt.subplots(1, panels, figsize=(11.2, 6.3), dpi=160)
    axes = [axes] if panels == 1 else list(axes)
    fig.text(0.04, 0.955, title, fontsize=19, fontweight="bold", va="top", ha="left", color=INK)
    if subtitle:
        fig.text(0.04, 0.895, subtitle, fontsize=12, va="top", ha="left", color=INK2)
    fig.subplots_adjust(
        left=0.30 if panels == 1 else 0.17, right=0.97, top=0.80, bottom=0.16, wspace=0.62
    )
    return fig, axes


def style_x(ax, xmax: float, fmt: str = "{:.1f}") -> None:
    ax.set_xlim(0, xmax)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: vn(f"{v:g}")))
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0)
    ax.tick_params(axis="x", labelsize=11)


def legend_below(ax, ncol: int) -> None:
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(0.0, -0.07),
        ncol=ncol,
        frameon=False,
        fontsize=11.5,
        labelcolor=INK2,
        handlelength=1.6,
        columnspacing=2.0,
    )


def grouped_hbar(ax, labels, series, colors, xmax, fmt="{:.3f}", legend=True) -> None:
    """Thin horizontal bars, one group per label, value printed at the bar end."""
    n = len(series)
    height = 0.7 / n
    for s, (name, values) in enumerate(series.items()):
        ys = [i + (s - (n - 1) / 2) * height for i in range(len(labels))]
        ax.barh(
            ys,
            values,
            height=height * 0.9,
            color=colors[s],
            label=name,
            edgecolor=SURFACE,
            linewidth=1.2,
        )
        for y, v in zip(ys, values, strict=True):
            if v is None:
                continue
            ax.text(
                v + xmax * 0.01,
                y,
                vn(fmt.format(v)),
                va="center",
                ha="left",
                fontsize=11,
                color=INK,
            )
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=12.5)
    ax.invert_yaxis()
    style_x(ax, xmax)
    if n > 1 and legend:
        legend_below(ax, n)


def save(fig, out: Path, name: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / name)
    plt.close(fig)
    print("wrote", out / name)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def hops_scores(set_name: str) -> dict:
    text = (CK / f"hops_{set_name}.json").read_text(encoding="utf-8")
    data = json.loads(text.split("\n", 1)[1])
    return {
        kind: {m: v["current"]["mean"] for m, v in metrics.items() if isinstance(v, dict)}
        for kind, metrics in data.items()
    }


def hops_recall(set_name: str) -> dict:
    text = (CK / f"recall_{set_name}_vs_pool.txt").read_text(encoding="utf-8")
    out, kind = {}, None
    for line in text.splitlines():
        head = re.match(r"== (\w+): Recall@10 on (\d+) of (\d+)", line)
        if head:
            kind = head.group(1)
            out[kind] = {"n": int(head.group(2)), "of": int(head.group(3))}
        cur = re.match(r"\s+current\s+([\d.]+)\s+CI95 of the mean \[([\d.]+), ([\d.]+)\]", line)
        if cur and kind:
            out[kind].update(
                recall=float(cur.group(1)), lo=float(cur.group(2)), hi=float(cur.group(3))
            )
    return out


def fig_silver(out: Path, data: dict) -> None:
    dev, test = read_json(CK / "silver_dev.json"), read_json(CK / "silver_test.json")
    kinds = ["single_named", "single_brand", "multi_intent", "multi_hop"]
    labels = [
        "Theo tên sản phẩm",
        "Theo hãng",
        "Nhiều ý (A và B)",
        "Quan hệ: sản phẩm khác\ncủa hãng",
        "Từ chối câu ngoài\nphạm vi (đúng)",
    ]

    def row(split):
        q = split["quality"]
        return [q[k]["all"]["recall@10"] for k in kinds] + [q["no_answer"]["rejection_rate"]]

    series = {"Dev": row(dev), "Test (chạy một lần)": row(test)}
    data["silver"] = {
        "labels": labels,
        "dev": series["Dev"],
        "test": series["Test (chạy một lần)"],
        "n_dev": dev["n"],
        "n_test": test["n"],
    }
    fig, (ax,) = new_figure(
        "Câu đã biết đáp án: Recall@10 từ 0,94 đến 1,00",
        f"Silver-v1.1, chạy qua API thật. Dev {dev['n']} câu, test {test['n']} câu. "
        "Nhãn silver dựng từ catalogue nên con số gần như là cận trên.",
    )
    grouped_hbar(ax, labels, series, [BLUE, ORANGE], 1.12)
    save(fig, out, "01_silver_recall.png")


def fig_hops(out: Path, data: dict) -> None:
    dev, hold = hops_scores("hops-v1"), hops_scores("hops-v2-holdout")
    rdev, rhold = hops_recall("hops-v1"), hops_recall("hops-v2-holdout")
    fig, (a1, a2) = new_figure(
        "Chấm tay: bộ chưa từng thấy (holdout) ngang bộ dev",
        "20 câu mỗi loại nên khoảng tin cậy rộng (khoảng ±0,1). P@10 dev multi-hop là chặn dưới: 6 sản phẩm của 1 câu chưa chấm",
        panels=2,
    )
    mh = ["P@10", "Recall@10"]
    a1.set_title("Quan hệ (multi-hop)", loc="left", fontsize=14, color=INK, pad=10)
    grouped_hbar(
        a1,
        mh,
        {
            "Dev (hops-v1)": [dev["multi_hop"]["p10"], rdev["multi_hop"]["recall"]],
            "Holdout (hops-v2)": [hold["multi_hop"]["p10"], rhold["multi_hop"]["recall"]],
        },
        [BLUE, ORANGE],
        1.15,
    )
    mi = ["P@10", "Đủ mọi vế\n(all_parts@10)", "Cân bằng\ncác vế", "Recall@10"]
    a2.set_title("Nhiều ý (multi-intent)", loc="left", fontsize=14, color=INK, pad=10)
    grouped_hbar(
        a2,
        mi,
        {
            "Dev (hops-v1)": [
                dev["multi_intent"]["p10"],
                dev["multi_intent"]["all_parts10"],
                dev["multi_intent"]["balanced10"],
                rdev["multi_intent"]["recall"],
            ],
            "Holdout (hops-v2)": [
                hold["multi_intent"]["p10"],
                hold["multi_intent"]["all_parts10"],
                hold["multi_intent"]["balanced10"],
                rhold["multi_intent"]["recall"],
            ],
        },
        [BLUE, ORANGE],
        1.15,
        legend=False,
    )
    data["hops"] = {"dev": dev, "holdout": hold, "recall_dev": rdev, "recall_holdout": rhold}
    save(fig, out, "02_hops_dev_vs_holdout.png")


def fig_places(out: Path, data: dict) -> None:
    places = read_json(CK / "places.json")
    rows = places["rows"]
    labels = [f"{r['place']} ({r['entities']} sản phẩm)" for r in rows]
    series = {
        "Đường graph (ORIGIN)": [r["graph"]["recall10"] for r in rows],
        "Tìm theo nội dung (trước)": [r["content"]["recall10"] for r in rows],
    }
    data["places"] = {"rows": rows, "summary": places["summary"]}
    fig, (ax,) = new_figure(
        "“Đặc sản + địa danh”: Recall@10 từ 0,15 lên 0,98",
        "6 địa danh có từ 3 sản phẩm thực phẩm. Đáp án lấy từ chính các cạnh ORIGIN của graph, "
        "nên đây là kiểm tra nhất quán, chưa phải chấm tay.",
    )
    grouped_hbar(ax, labels, series, [BLUE, ORANGE], 1.15, fmt="{:.2f}")
    save(fig, out, "03_place_flow.png")


def fig_category(out: Path, data: dict) -> None:
    c = STATIC["category"]
    fig, (a1, a2) = new_figure(
        "Hiểu loại hàng: P@10 từ 0,79 lên 0,88; câu nhiều vế từ 0,53 lên 0,80",
        "eval/category-v1: 63 câu một loại hàng và 30 câu “A và B” (dựng từ taxonomy nên hơi lạc quan)",
        panels=2,
    )
    a1.set_title("Câu một loại hàng", loc="left", fontsize=14, color=INK, pad=10)
    grouped_hbar(
        a1,
        ["P@10 chung", "P@10 loại hàng", "P@10 hạng thời trang"],
        {
            "Tắt hiểu loại hàng": [c["single"]["off"][k] for k in ("p10", "category", "tier")],
            "Bật": [c["single"]["on"][k] for k in ("p10", "category", "tier")],
        },
        [ORANGE, BLUE],
        1.15,
        fmt="{:.2f}",
    )
    a2.set_title("Câu nhiều vế (A và B)", loc="left", fontsize=14, color=INK, pad=10)
    grouped_hbar(
        a2,
        ["Có ảnh cho mọi vế", "Chia đều các vế"],
        {
            "Tắt hiểu loại hàng": [c["multi"]["off"][k] for k in ("all_intents", "balanced")],
            "Bật (tách theo vế)": [c["multi"]["on"][k] for k in ("all_intents", "balanced")],
        },
        [ORANGE, BLUE],
        1.15,
        fmt="{:.2f}",
        legend=False,
    )
    data["category"] = c
    save(fig, out, "04_category.png")


def fig_descriptive(out: Path, data: dict) -> None:
    d = read_json(CK / "descriptive_dev.json")["systems"]
    keys = [
        ("ndcg@10", "Tất cả (56 câu)"),
        ("ndcg@10_attribute", "Có thuộc tính (màu, chất liệu)"),
        ("ndcg@10_category", "Theo loại hàng"),
        ("ndcg@10_visual", "Mô tả thị giác"),
        ("ndcg@10_ambiguous", "Mơ hồ"),
    ]
    series = {
        "Chỉ văn bản": [d["text"][k] for k, _ in keys],
        "Văn bản + vector ảnh (fusion)": [d["fusion"][k] for k, _ in keys],
    }
    data["descriptive_dev"] = {
        "labels": [x for _, x in keys],
        **series,
        "test_24_11": STATIC["descriptive_test_24_11"],
    }
    fig, (ax,) = new_figure(
        "Câu mô tả thị giác: vector ảnh nâng nDCG@10 từ 0,58 lên 0,79",
        "descriptive-v1 dev, điểm chấm tay. Test (đo tối 24/11, một lần): 0,579 lên 0,788",
    )
    grouped_hbar(ax, [x for _, x in keys], series, [ORANGE, BLUE], 1.15, fmt="{:.2f}")
    save(fig, out, "05_descriptive.png")


def fig_latency(out: Path, data: dict) -> None:
    lat = read_json(CK / "latency.json")
    by = lat["flows"]["by_flow"]
    order = [f for f in FLOW_LABEL if f in by]
    labels = [FLOW_LABEL[f] for f in order]
    series = {
        "p50 (nửa số câu nhanh hơn)": [by[f]["client"]["p50"] for f in order],
        "p95": [by[f]["client"]["p95"] for f in order],
    }
    data["latency_flows"] = {
        "labels": labels,
        **series,
        "all": lat["flows"]["all"],
        "queries_per_flow": lat["queries_per_flow"],
        "api": lat["api"],
    }
    top = max(series["p95"]) * 1.18
    fig, (ax,) = new_figure(
        f"Độ trễ theo luồng: p50 {lat['flows']['all']['p50']:.0f} ms, p95 {lat['flows']['all']['p95']:.0f} ms",
        "Từng câu một, đo ở phía người dùng (ms), có gồm đường mạng tới Qdrant Cloud ở Seoul",
    )
    grouped_hbar(ax, labels, series, [BLUE, ORANGE], top, fmt="{:.0f}")
    save(fig, out, "06_latency_by_flow.png")

    stage = {}
    for f in order:
        s = by[f]["stage_median_ms"]
        enc = s.get("embed_query", 0) + s.get("embed_image", 0) + s.get("similarity", 0)
        qd = s.get("qdrant", 0)
        total = s.get("total", 0)
        stage[f] = (enc, qd, max(total - enc - qd, 0))
    data["latency_stages"] = {
        FLOW_LABEL[f]: dict(zip(("encode", "qdrant", "rest"), v, strict=True))
        for f, v in stage.items()
    }
    fig, (ax,) = new_figure(
        "Thời gian đi đâu: gọi Qdrant từ xa chiếm phần lớn",
        "Trung vị từng giai đoạn (ms). Mã hóa câu chạy GPU; Qdrant Cloud ở Seoul gọi qua gRPC",
    )
    left = [0.0] * len(order)
    names = ["Mã hóa câu (bge-m3, SigLIP2)", "Gọi Qdrant (xếp hạng + trace)", "Còn lại (S1, S2)"]
    for i, (name, color) in enumerate(zip(names, (BLUE, ORANGE, AQUA), strict=True)):
        vals = [stage[f][i] for f in order]
        ax.barh(
            range(len(order)),
            vals,
            left=left,
            height=0.5,
            color=color,
            label=name,
            edgecolor=SURFACE,
            linewidth=1.5,
        )
        for y, (v, l0) in enumerate(zip(vals, left, strict=True)):
            if v >= 40:
                ax.text(
                    l0 + v / 2,
                    y,
                    f"{v:.0f}",
                    ha="center",
                    va="center",
                    color="#ffffff",
                    fontsize=11,
                    fontweight="bold",
                )
        left = [a + b for a, b in zip(left, vals, strict=True)]
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(labels, fontsize=12.5)
    ax.invert_yaxis()
    style_x(ax, max(left) * 1.12)
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(0.0, -0.07),
        ncol=3,
        frameon=False,
        fontsize=11,
        labelcolor=INK2,
        handlelength=1.6,
        columnspacing=1.6,
    )
    save(fig, out, "07_latency_stages.png")


def fig_load(out: Path, data: dict) -> None:
    load = read_json(CK / "latency.json")["load"]
    data["load"] = load
    fig, (a1, a2) = new_figure(
        "Dưới tải: API xử lý từng câu một, thông lượng chạm trần ~2–3 câu/giây",
        "Mỗi mức chạy 30 giây, các client gửi liên tục. API xếp hàng các câu, nên p95 tăng theo số client",
        panels=2,
    )
    xs = [r["clients"] for r in load]
    for ax, key, title, fmt in (
        (a1, "qps", "Thông lượng (câu/giây)", "{:.1f}"),
        (a2, "p95", "Độ trễ p95 (ms)", "{:.0f}"),
    ):
        ys = [r[key] for r in load]
        ax.plot(
            xs,
            ys,
            color=BLUE,
            linewidth=2.2,
            marker="o",
            markersize=8,
            markeredgecolor=SURFACE,
            markeredgewidth=2,
        )
        for x, y in zip(xs, ys, strict=True):
            ax.text(x, y + max(ys) * 0.05, vn(fmt.format(y)), ha="center", fontsize=11, color=INK)
        ax.set_title(title, loc="left", fontsize=14, color=INK, pad=10)
        ax.set_xticks(xs)
        ax.set_xlabel("Số client đồng thời", fontsize=11)
        ax.set_ylim(0, max(ys) * 1.25)
        ax.yaxis.grid(True, color=GRID, linewidth=0.8)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: vn(f"{v:g}")))
        ax.set_axisbelow(True)
        ax.tick_params(length=0, labelsize=11)
        ax.tick_params(axis="y", colors=MUTED)
    save(fig, out, "08_load.png")


def fig_optimisation(out: Path, data: dict) -> None:
    s = STATIC["optimisation"]
    labels = [
        "Trung vị phía người dùng",
        "Chậm nhất trong 6 câu thử",
        "Xếp hạng + trace gọi Qdrant\n(trung vị, song song)",
    ]
    series = {
        "Trước (REST, kết nối đóng sau 5 giây)": [
            s["median_client_ms"]["before"],
            s["worst_client_ms"]["before"],
            s["rank_plus_trace_ms"]["before"],
        ],
        "Sau (gRPC, giữ kết nối, payload gọn)": [
            s["median_client_ms"]["after"],
            s["worst_client_ms"]["after"],
            s["rank_plus_trace_ms"]["after"],
        ],
    }
    data["optimisation"] = {"labels": labels, **series}
    fig, (ax,) = new_figure(
        "Tối ưu trong ngày: độ trễ trung vị từ 844 ms xuống 319 ms",
        "Cùng kịch bản người dùng (dừng 10 giây giữa các câu), cùng ngày 25/11. Câu chậm nhất: 3,9 giây xuống 0,56 giây",
    )
    grouped_hbar(ax, labels, series, [ORANGE, BLUE], 4600, fmt="{:.0f}")
    save(fig, out, "09_optimisation.png")


def fig_network(out: Path, data: dict) -> None:
    runs = {
        "24/11 tối (dev)": read_json(RUNS / "dev-checkpoint-2026-11-24.json"),
        "25/11 (dev)": read_json(CK / "silver_dev.json"),
        "25/11 (test)": read_json(CK / "silver_test.json"),
    }
    import numpy as np

    def q(run):
        v = [
            r["server"]["qdrant"]
            for r in run["records"].values()
            if "server" in r and r["server"].get("qdrant", 0) > 0
        ]
        return float(np.percentile(v, 50)), float(np.percentile(v, 95))

    vals = {k: q(v) for k, v in runs.items()}
    labels = list(vals)
    series = {"p50": [vals[k][0] for k in labels], "p95": [vals[k][1] for k in labels]}
    data["qdrant_stage"] = {k: {"p50": a, "p95": b} for k, (a, b) in vals.items()}
    fig, (ax,) = new_figure(
        "Gọi Qdrant chậm hơn tối qua dù đã dùng gRPC: phần lớn do đường mạng",
        "Giai đoạn gọi Qdrant (ms) trong 3 lần chạy silver. Đo RTT hôm nay 130–195 ms, đuôi tới 2,4 giây",
    )
    grouped_hbar(ax, labels, series, [BLUE, ORANGE], max(series["p95"]) * 1.2, fmt="{:.0f}")
    save(fig, out, "10_qdrant_stage_by_run.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs/figures/2026-11-25")
    args = parser.parse_args()
    setup()
    data: dict = {"static": STATIC}
    for fn in (
        fig_silver,
        fig_hops,
        fig_places,
        fig_category,
        fig_descriptive,
        fig_latency,
        fig_load,
        fig_optimisation,
        fig_network,
    ):
        fn(args.out, data)
    (args.out / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("wrote", args.out / "data.json")


if __name__ == "__main__":
    main()
