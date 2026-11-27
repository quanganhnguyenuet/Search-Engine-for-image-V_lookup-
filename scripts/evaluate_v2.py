"""Run an eval split against the live API and score it (PLAN_PRODUCTS_V1.md 5.2 and 5.3).

  python scripts/evaluate_v2.py run --split dev --out runs/dev-b3.json
  python scripts/evaluate_v2.py compare runs/a.json runs/b.json

Protocol: warmup file first (not scored, first request = cold_start_ms), then every query of
the split exactly once, sequentially, shuffled with a fixed seed; quality and latency come
from that same pass. Any application cache hit marks the run invalid; errors and timeouts
(2 s) are counted and make the run incomplete.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import random
import time
from collections import defaultdict
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "data/processed/products-v1/eval/silver-v1"
BOOTSTRAP = 10_000
SEED = 7
K = 10


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---- metrics -------------------------------------------------------------------------------
def recall_at_10(row: dict, r10: list[str]) -> float:
    got = set(r10)
    intents = row["rel_by_intent"]
    if row["type"] == "multi_intent":
        s = K // row["k"]
        return float(np.mean([min(len(got & set(rel)), s) / min(len(rel), s) for rel in intents]))
    rel = set(intents[0])
    return len(got & rel) / min(len(rel), K)


def ndcg_at_10(row: dict, r10: list[str]) -> float:
    rel = {a for intent in row["rel_by_intent"] for a in intent}
    dcg = sum(1 / math.log2(i + 2) for i, a in enumerate(r10) if a in rel)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(rel), K)))
    return dcg / ideal if ideal else 0.0


def bootstrap_ci(values: list[float], seed: int = SEED) -> list[float]:
    if not values:
        return [float("nan"), float("nan")]
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = data[rng.integers(0, len(data), (BOOTSTRAP, len(data)))].mean(axis=1)
    return [round(float(x), 4) for x in np.percentile(means, [2.5, 97.5])]


def percentiles(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    data = np.asarray(values, dtype=float)
    p50, p95, p99 = np.percentile(data, [50, 95, 99], method="linear")
    return {
        "n": len(data),
        "p50": round(float(p50), 1),
        "p95": round(float(p95), 1),
        "p99": round(float(p99), 1),
        "max": round(float(data.max()), 1),
    }


def score(rows: list[dict], records: dict[str, dict]) -> dict:
    by_type: dict[str, list] = defaultdict(list)
    for row in rows:
        by_type[row["type"]].append(row)
    report: dict = {}
    for kind, items in sorted(by_type.items()):
        section: dict = {"n": len(items)}
        if kind == "no_answer":
            rejected = [records[r["query_id"]]["rejected"] for r in items]
            section["rejection_rate"] = round(float(np.mean(rejected)), 4)
            section["rejection_ci95"] = bootstrap_ci([float(x) for x in rejected])
            report[kind] = section
            continue
        for label, subset in (
            ("all", items),
            ("priority", [r for r in items if r["priority"]]),
            ("other", [r for r in items if not r["priority"]]),
        ):
            if not subset:
                continue
            recalls = [records[r["query_id"]]["recall"] for r in subset]
            part = {
                "n": len(subset),
                "recall@10": round(float(np.mean(recalls)), 4),
                "recall_ci95": bootstrap_ci(recalls),
                "hit@10": round(float(np.mean([records[r["query_id"]]["hit"] for r in subset])), 4),
                "ndcg@10": round(
                    float(np.mean([records[r["query_id"]]["ndcg"] for r in subset])), 4
                ),
            }
            if label == "all":
                part["false_rejection"] = round(
                    float(np.mean([records[r["query_id"]]["rejected"] for r in subset])), 4
                )
                part["clarification_rate"] = round(
                    float(np.mean([records[r["query_id"]]["clarified"] for r in subset])), 4
                )
                extra = [records[r["query_id"]].get("extra", {}) for r in subset]
                for key in ("all_covered", "entity_coverage", "anchor_leak"):
                    vals = [e[key] for e in extra if key in e]
                    if vals:
                        part[key] = round(float(np.mean(vals)), 4)
            section[label] = part
        report[kind] = section
    return report


def extras(row: dict, results: list[dict]) -> dict:
    r10 = results[:K]
    ids = [hit["asset_id"] for hit in r10]
    if row["type"] == "multi_intent":
        return {"all_covered": float(all(set(ids) & set(rel) for rel in row["rel_by_intent"]))}
    if row["type"] == "multi_hop":
        targets, anchors = set(row["target_entities"]), set(row["anchor_entities"])
        found = {e for hit in r10 for e in hit.get("entity_ids") or [] if e in targets}
        leak = [any(e in anchors for e in hit.get("entity_ids") or []) for hit in r10]
        return {
            "entity_coverage": len(found) / min(len(targets), K),
            "anchor_leak": float(np.mean(leak)) if leak else 0.0,
        }
    return {}


# ---- run -----------------------------------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    rows = load(args.eval / f"{args.split}.jsonl")
    warmup = load(args.eval / "warmup.jsonl")
    manifest = json.loads((args.eval / "manifest.json").read_text(encoding="utf-8"))
    order = list(range(len(rows)))
    random.Random(SEED).shuffle(order)
    url = args.api.rstrip("/") + "/api/v1/search/text"
    client = httpx.Client(timeout=args.timeout)

    info = client.get(args.api.rstrip("/") + "/api/v1/search/info")
    if info.status_code == 401:
        raise SystemExit("The API requires login. Restart it with REQUIRE_LOGIN=false.")
    health = info.json()
    cold_start_ms = None
    for index, item in enumerate(warmup):
        tick = time.perf_counter()
        client.post(url, json={"query": item["query"]})
        if index == 0:
            cold_start_ms = round((time.perf_counter() - tick) * 1000, 1)

    records: dict[str, dict] = {}
    errors = cache_hits = 0
    for index in order:
        row = rows[index]
        tick = time.perf_counter()
        try:
            response = client.post(url, json={"query": row["query"]})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            errors += 1
            records[row["query_id"]] = {
                "error": str(exc),
                "recall": 0.0,
                "hit": 0.0,
                "ndcg": 0.0,
                "rejected": False,
                "clarified": False,
            }
            continue
        client_ms = (time.perf_counter() - tick) * 1000
        cache_hits += any(body.get("cache", {}).values())
        results = body["results"]
        r10 = list(dict.fromkeys(hit["asset_id"] for hit in results))[:K]
        rejected = bool(body["out_of_scope"])
        clarified = body.get("clarification") is not None
        record = {
            "query": row["query"],
            "type": row["type"],
            "client_ms": round(client_ms, 2),
            "server": body["timings"],
            "r10": r10,
            "rejected": rejected,
            "clarified": clarified,
            "plan": body.get("plan"),
        }
        if row["type"] != "no_answer":
            record["recall"] = recall_at_10(row, r10)
            record["hit"] = float(bool(set(r10) & {a for i in row["rel_by_intent"] for a in i}))
            record["ndcg"] = ndcg_at_10(row, r10)
            record["extra"] = extras(row, results)
        records[row["query_id"]] = record

    ok = [r for r in records.values() if "client_ms" in r]
    latency = {
        "client_ms": percentiles([r["client_ms"] for r in ok]),
        "server_total": percentiles([r["server"]["total"] for r in ok]),
    }
    for stage in ("embed_query", "s1_spotting", "s2_expand", "qdrant"):
        latency[stage] = percentiles([r["server"][stage] for r in ok if stage in r["server"]])
    by_type: dict[str, list] = defaultdict(list)
    for r in ok:
        by_type[r["type"]].append(r["client_ms"])
    latency["client_ms_by_type"] = {k: percentiles(v) for k, v in sorted(by_type.items())}

    out = {
        "split": args.split,
        "label": args.label,
        "n": len(rows),
        "eval_manifest": manifest,
        "api_info": health,
        "complete": errors == 0,
        "valid": cache_hits == 0,
        "error_count": errors,
        "cache_hits": cache_hits,
        "cold_start_ms": cold_start_ms,
        "environment": {
            "platform": platform.platform(),
            "api": args.api,
            "timeout_s": args.timeout,
        },
        "quality": score(rows, records),
        "latency": latency,
        "records": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {
                k: out[k]
                for k in (
                    "split",
                    "label",
                    "complete",
                    "valid",
                    "error_count",
                    "cold_start_ms",
                    "quality",
                )
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    print(
        json.dumps(
            {
                "client_ms": latency["client_ms"],
                "embed": latency["embed_query"],
                "qdrant": latency["qdrant"],
            },
            indent=1,
        )
    )


def compare(args: argparse.Namespace) -> None:
    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))
    by_type: dict[str, list] = defaultdict(list)
    for qid, rec in a["records"].items():
        other = b["records"].get(qid)
        if other is not None and "recall" in rec and "recall" in other:
            by_type[rec.get("type", qid.split("-")[0])].append(other["recall"] - rec["recall"])
    result = {}
    for kind, deltas in sorted(by_type.items()):
        result[kind] = {
            "n": len(deltas),
            "delta": round(float(np.mean(deltas)), 4),
            "ci95": bootstrap_ci(deltas),
        }
    print(
        json.dumps(
            {"a": a.get("label"), "b": b.get("label"), "paired_delta_recall": result},
            indent=1,
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--split", choices=["dev", "test"], default="dev")
    p_run.add_argument("--eval", type=Path, default=EVAL, help="eval set directory")
    p_run.add_argument("--api", default="http://127.0.0.1:8000")
    p_run.add_argument("--timeout", type=float, default=2.0)
    p_run.add_argument("--label", default="")
    p_run.add_argument("--out", type=Path, required=True)
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("a")
    p_cmp.add_argument("b")
    args = parser.parse_args()
    run(args) if args.cmd == "run" else compare(args)


if __name__ == "__main__":
    main()
