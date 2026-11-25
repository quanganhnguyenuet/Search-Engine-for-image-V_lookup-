"""P6 end to end: the served pipeline on eval/descriptive-v1, visual fusion on and off.

scripts/tune_fusion.py chose the fusion settings offline, on cached text candidates. This
runs the real SearchPipeline (scripts/evaluate_category.build_pipeline, as the API builds
it): Qdrant candidates with the category boost, the gate that sends only queries without
entity, brand or tier to fusion, and the in-memory payloads. Scored with the same human
judgments and functions as tune_fusion.py: nDCG@10 with gain 2^grade - 1, unjudged images
count as 0, judged@10 shows how much of the top 10 was judged.

  python scripts/evaluate_descriptive.py                 # dev
  python scripts/evaluate_descriptive.py --split test    # confirm the chosen settings once
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from evaluate_category import build_pipeline, load_env  # noqa: E402
from tune_fusion import judged_at_10, load_eval, ndcg_at_10, paired_bootstrap  # noqa: E402

from visualseek_retrieval.encoder import TextEncoder  # noqa: E402
from visualseek_retrieval.pipeline import Settings  # noqa: E402
from visualseek_retrieval.qdrant_store import connect  # noqa: E402

K = 10
CONFIGS = {"text": {"visual_fusion": False}, "fusion": {"visual_fusion": True}}


def summarise(rows: list[dict], runs: dict[str, dict], used: dict[str, dict]) -> dict:
    out: dict = {"n": len(rows), "systems": {}}
    per = {}
    for name, run in runs.items():
        nd = [ndcg_at_10(run[q["qid"]], q["grades"]) for q in rows]
        per[name] = nd
        system = {
            "ndcg@10": round(float(np.mean(nd)), 4),
            "p@10_grade2": round(
                float(
                    np.mean(
                        [
                            sum(q["grades"].get(a, 0) == 2 for a in run[q["qid"]][:K]) / K
                            for q in rows
                        ]
                    )
                ),
                4,
            ),
            "judged@10": round(
                float(np.mean([judged_at_10(run[q["qid"]], q["grades"]) for q in rows])), 4
            ),
            "fusion_used": sum(used[name][q["qid"]] for q in rows),
        }
        for kind in sorted({q["kind"] for q in rows}):
            values = [v for v, q in zip(nd, rows, strict=True) if q["kind"] == kind]
            system[f"ndcg@10_{kind}"] = round(float(np.mean(values)), 4)
        out["systems"][name] = system
    diffs = [a - b for a, b in zip(per["fusion"], per["text"], strict=True)]
    out["fusion_minus_text"] = {
        "delta": round(float(np.mean(diffs)), 4),
        "ci95": paired_bootstrap(diffs),
        "wins": sum(d > 1e-9 for d in diffs),
        "losses": sum(d < -1e-9 for d in diffs),
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    load_env()
    queries, _ = load_eval()
    rows = [q for q in queries if q["split"] == args.split]
    encoder = TextEncoder(device=args.device)
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    runs: dict[str, dict] = {}
    used: dict[str, dict] = {}
    timings: dict[str, list[float]] = {}
    for name, change in CONFIGS.items():
        settings = dataclasses.replace(Settings(), **change)
        pipeline = build_pipeline(settings, encoder, client, device=args.device)
        runs[name], used[name], timings[name] = {}, {}, []
        for q in rows:
            started = time.perf_counter()
            response = pipeline.search(q["query"])
            timings[name].append((time.perf_counter() - started) * 1000)
            runs[name][q["qid"]] = [hit["asset_id"] for hit in response["results"]]
            used[name][q["qid"]] = response["trace"]["channels"]["img"] == "ready"
    report = summarise(rows, runs, used)
    report["split"] = args.split
    report["ms_p50"] = {n: round(float(np.percentile(t, 50)), 1) for n, t in timings.items()}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
