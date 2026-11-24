"""B2: classify every canonical entity into the frozen taxonomy (graph/taxonomy-v1.json).

pack    evidence pack per canonical entity: names of merged members, brand, page titles,
        descriptions, OCR. Every snippet has an id so a label can cite it.
pilot   200 entities stratified over entity_dense clusters (deterministic).
run     Gemini structured output per entity: L2 category (enum of the taxonomy),
        ProductType, market_tier (fashion only), cited snippet ids, basis, confidence.
        Responses are cached by sha256(prompt version + model + pack), so a rerun costs
        nothing; token usage is written with the labels.

  python scripts/graph_classify.py pack
  python scripts/graph_classify.py pilot
  python scripts/graph_classify.py run --ids pilot      # or: --ids all
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from enum import Enum
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval.entity_review import EntityReview  # noqa: E402

CORPUS = ROOT / "data/processed/products-v1"
ARTIFACTS = CORPUS / "search"
OUT = CORPUS / "graph"
TAXONOMY = ROOT / "graph/taxonomy-v1.json"
PACKAGED_REVIEW = (
    ROOT / "services/api/src/visualseek_api/application/products-v1-identity-review.json"
)
MODEL = "gemini-3.8-flash"
PROMPT_VERSION = "b2-classify-v1"
TIER_VALUES = ("luxury", "premium", "mass", "unknown", "not_applicable")
PILOT_SIZE = 200
MAX = {"title": 8, "desc": 4, "ocr": 6}
CLIP = {"title": 200, "desc": 300, "ocr": 160}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def review_path() -> Path:
    local = ARTIFACTS / "identity_review.json"
    return local if local.exists() else PACKAGED_REVIEW


def load_review() -> tuple[dict, EntityReview]:
    graph = json.loads((ARTIFACTS / "graph.json").read_text(encoding="utf-8"))
    return graph, EntityReview.load(graph, review_path().read_bytes())


def clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ---- pack --------------------------------------------------------------------------------
def build_packs(graph: dict, review: EntityReview, assets: list[dict]) -> list[dict]:
    entities = {e["id"]: e for e in graph["entities"]}
    brands = {b["id"]: b["name"] for b in graph["brands"]}
    members: dict[str, list[str]] = defaultdict(list)
    for entity in graph["entities"]:
        members[review.canonical(entity["id"])].append(entity["id"])
    titles: dict[str, Counter] = defaultdict(Counter)
    descs: dict[str, Counter] = defaultdict(Counter)
    ocr: dict[str, Counter] = defaultdict(Counter)
    ld_brands: dict[str, Counter] = defaultdict(Counter)
    for asset in assets:
        kept = set(review.result_ids(asset["asset_id"], asset["entity_ids"]))
        text = " ".join(o["text"] for o in asset.get("ocr") or [] if o["score"] >= 0.8)
        for owner in kept:
            if len(text) >= 6:
                ocr[owner][clip(text, CLIP["ocr"])] += 1
        for source in asset["sources"]:
            owner = review.canonical(source["entity_id"])
            if owner not in kept:
                continue
            page = source.get("page") or {}
            if page.get("status") != "ok":
                continue
            if source.get("page_title"):
                titles[owner][clip(source["page_title"], CLIP["title"])] += 1
            if page.get("description"):
                descs[owner][clip(page["description"], CLIP["desc"])] += 1
            for product in page.get("products") or []:
                if product.get("brand"):
                    ld_brands[owner][product["brand"]] += 1
    packs = []
    for owner in sorted(members):
        snippets = []
        for i, eid in enumerate(members[owner], 1):
            snippets.append({"id": f"n{i}", "kind": "name", "text": entities[eid]["name"]})
        graph_brands = sorted(
            {brands[entities[e]["brand_id"]] for e in members[owner] if entities[e]["brand_id"]}
        )
        brand_names = graph_brands + [b for b, _ in ld_brands[owner].most_common(2)]
        unique = list({name.casefold(): name for name in reversed(brand_names)}.values())[::-1]
        for i, name in enumerate(unique, 1):
            snippets.append({"id": f"b{i}", "kind": "brand", "text": name})
        for kind, counter in (("title", titles), ("desc", descs), ("ocr", ocr)):
            for i, (text, _) in enumerate(counter[owner].most_common(MAX[kind]), 1):
                snippets.append({"id": f"{kind[0]}{i}", "kind": kind, "text": text})
        packs.append(
            {
                "entity_id": owner,
                "name": entities[owner]["name"],
                "members": members[owner],
                "snippets": snippets,
            }
        )
    return packs


def pack(_: argparse.Namespace) -> None:
    graph, review = load_review()
    packs = build_packs(graph, review, read_jsonl(CORPUS / "assets.jsonl"))
    OUT.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in packs)
    (OUT / "evidence.jsonl").write_text(body, encoding="utf-8", newline="\n")
    sizes = [len(p["snippets"]) for p in packs]
    print(
        json.dumps(
            {
                "entities": len(packs),
                "snippets_mean": round(float(np.mean(sizes)), 1),
                "only_name": sum(s == 1 for s in sizes),
            }
        )
    )


# ---- pilot -------------------------------------------------------------------------------
def pilot(_: argparse.Namespace) -> None:
    from sklearn.cluster import KMeans

    graph, review = load_review()
    dense = np.load(ARTIFACTS / "entity_dense.npy")
    rows = [i for i, e in enumerate(graph["entities"]) if review.canonical(e["id"]) == e["id"]]
    ids = [graph["entities"][i]["id"] for i in rows]
    labels = KMeans(n_clusters=70, n_init=4, random_state=0).fit(dense[rows]).labels_
    by_cluster: dict[int, list[str]] = defaultdict(list)
    for eid, label in zip(ids, labels.tolist(), strict=True):
        by_cluster[label].append(eid)
    for members in by_cluster.values():
        members.sort(key=lambda e: hashlib.sha256(f"pilot|{e}".encode()).hexdigest())
    picked: list[str] = []
    round_ = 0
    while len(picked) < PILOT_SIZE:  # round robin keeps every cluster represented
        for label in sorted(by_cluster, key=lambda c: -len(by_cluster[c])):
            if round_ < len(by_cluster[label]) and len(picked) < PILOT_SIZE:
                picked.append(by_cluster[label][round_])
        round_ += 1
    (OUT / "pilot_ids.json").write_text(json.dumps(sorted(picked), indent=1), encoding="utf-8")
    print(f"pilot: {len(picked)} entities from {len(by_cluster)} clusters")


# ---- run ---------------------------------------------------------------------------------
def taxonomy_prompt(taxonomy: dict) -> tuple[list[str], str]:
    ids, lines = [], []
    for l1 in taxonomy["categories"]:
        lines.append(f"## {l1['name']}")
        for c in l1["children"]:
            ids.append(c["id"])
            lines.append(f"- {c['id']}: {c['name']} — {c['description']}")
    tier = taxonomy["attributes"]["market_tier"]["values"]
    lines.append("\nmarket_tier (only for fashion.*):")
    lines += [f"- {k}: {v}" for k, v in tier.items()]
    return ids, "\n".join(lines)


SYSTEM = """You label products of a Vietnamese image search corpus.
Each entity is a product (or occasionally a place, scene or weapon) shown by several web
images. You get numbered evidence snippets: n = entity names, b = brand, t = page titles,
d = page descriptions, o = OCR text read from the images (noisy).

Return:
- category: exactly one id from the taxonomy below. Use other.unclear only when the
  evidence cannot tell what the thing is.
- product_type: the generic kind of product in Vietnamese, 1-5 words, no brand or model
  (e.g. "Má phanh ô tô", "Thuốc kháng sinh", "Nhẫn kim cương", "Lốp xe đạp").
- market_tier: luxury/premium/mass/unknown for fashion.* categories, otherwise
  not_applicable.
- evidence: ids of the snippets that support the category (at least one when basis is
  evidence).
- basis: "evidence" if the snippets show it; "knowledge" if you rely on what you know
  about the name or brand rather than the snippets.
- confidence: high, medium or low.

Taxonomy:
"""


LABEL_FIELDS = ("category", "product_type", "market_tier", "evidence", "basis", "confidence")
BATCH_NOTE = """
Several entities come in one message, each under a header such as "### e3". Return one
label per entity with its key; evidence ids refer to that entity's own snippets.
"""


def batch_schema(category_ids: list[str]):
    """{labels: [...]} with the category constrained to the taxonomy ids."""
    category = Enum("Category", {c: c for c in category_ids}, type=str)

    class EntityLabel(BaseModel):
        key: str
        category: category  # type: ignore[valid-type]
        product_type: str = Field(max_length=80)
        market_tier: Literal["luxury", "premium", "mass", "unknown", "not_applicable"]
        evidence: list[str]
        basis: Literal["evidence", "knowledge"]
        confidence: Literal["high", "medium", "low"]

    class Labels(BaseModel):
        labels: list[EntityLabel]

    return Labels


def render_pack(p: dict) -> str:
    return "\n".join(f"[{s['id']}] ({s['kind']}) {s['text']}" for s in p["snippets"])


def load_env() -> None:
    path = ROOT / ".env"
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "GEMINI_API_KEY":
            os.environ.setdefault("GEMINI_API_KEY", value.strip().strip('"'))


def agy_schema(category_ids: list[str]) -> dict:
    """Plain JSON schema (no $ref) for the agy --json-schema flag."""
    label = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "category": {"type": "string", "enum": category_ids},
            "product_type": {"type": "string"},
            "market_tier": {
                "type": "string",
                "enum": ["luxury", "premium", "mass", "unknown", "not_applicable"],
            },
            "evidence": {"type": "array", "items": {"type": "string"}},
            "basis": {"type": "string", "enum": ["evidence", "knowledge"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["key", *LABEL_FIELDS],
    }
    return {
        "type": "object",
        "properties": {"labels": {"type": "array", "items": label}},
        "required": ["labels"],
    }


class EngineError(Exception):
    def __init__(self, message: str, retry: bool):
        super().__init__(message)
        self.retry = retry


def api_engine(args, system: str, category_ids: list[str]):
    from google import genai
    from google.genai import errors, types

    client = genai.Client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=batch_schema(category_ids),
        temperature=0,
        thinking_config=types.ThinkingConfig(thinking_budget=args.thinking_budget),
    )

    def call(user: str) -> tuple[str, dict]:
        try:
            response = client.models.generate_content(model=MODEL, contents=user, config=config)
        except errors.APIError as error:
            retry = error.code == 429 or (error.code or 0) >= 500
            raise EngineError(f"HTTP {error.code}", retry) from error
        usage = response.usage_metadata
        return response.text, {
            "prompt": usage.prompt_token_count or 0,
            "output": usage.candidates_token_count or 0,
            "thoughts": usage.thoughts_token_count or 0,
        }

    return call


AGY_QUOTA = ("quota", "rate limit", "resource_exhausted", "high demand", "try again", "429", "503")


def agy_engine(args, system: str, category_ids: list[str]):
    import subprocess
    import tempfile

    schema = OUT / "agy_schema.json"
    schema.write_text(json.dumps(agy_schema(category_ids), ensure_ascii=False), encoding="utf-8")
    # An empty working directory: the labelling prompt needs no files and no tools.
    workdir = Path(tempfile.mkdtemp(prefix="agy-b2-"))
    cmd = [
        args.agy, "--input-format", "stream-json", "--output-format", "stream-json",
        "--model", args.agy_model, "--disable-slash-commands", "--json-schema", str(schema),
        "--print-timeout", f"{args.timeout}s", "--print=",
    ]  # fmt: skip

    def call(user: str) -> tuple[str, dict]:
        prompt = system + "\n\nDo not use any tool. Answer only with the JSON.\n\n" + user
        message = json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False)
        try:
            done = subprocess.run(
                cmd, input=message + "\n", capture_output=True, text=True, encoding="utf-8",
                cwd=workdir, timeout=args.timeout + 60,
            )  # fmt: skip
        except subprocess.TimeoutExpired as error:
            raise EngineError("agy timed out", True) from error
        result = None
        for line in done.stdout.splitlines():
            if '"event":"result"' in line.replace(" ", ""):
                try:
                    result = json.loads(line)["result"]
                except (ValueError, KeyError):
                    continue
        if result is None:
            text = (done.stdout + done.stderr)[-300:]
            raise EngineError(f"no result: {text}", any(q in text.lower() for q in AGY_QUOTA))
        if result.get("status") != "SUCCESS":
            error = str(result.get("error", ""))[:300]
            raise EngineError(error, any(q in error.lower() for q in AGY_QUOTA))
        usage = result.get("usage") or {}
        return result.get("response", ""), {
            "prompt": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
            "thoughts": usage.get("thinking_tokens", 0),
        }

    return call


def parse_labels(text: str) -> list[dict]:
    """Accept {"labels": [...]} or {"e1": {...}, ...}, with or without a code fence.

    Models that ignore --json-schema (gpt-oss) answer in the second shape.
    """
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        start = text.find("{", pos)
        if start < 0:
            raise ValueError("no usable JSON object in the response")
        obj, end = decoder.raw_decode(text[start:])
        pos = start + end
        if isinstance(obj.get("labels"), list):
            return obj["labels"]
        keyed = [dict(v, key=k) for k, v in obj.items() if isinstance(v, dict)]
        if keyed:
            return keyed


def valid_label(label: dict, category_ids: list[str]) -> bool:
    """All fields present and every enum value legal (a weaker model can shift fields)."""
    return (
        all(f in label for f in LABEL_FIELDS)
        and label["category"] in category_ids
        and label["market_tier"] in TIER_VALUES
        and label["basis"] in ("evidence", "knowledge")
        and label["confidence"] in ("high", "medium", "low")
        and isinstance(label["evidence"], list)
    )


def labelled_by_others(model: str) -> set[str]:
    """Entities that already have a cached label from a model other than `model`."""
    done: set[str] = set()
    for path in (OUT / "llm_cache").glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row["model"] != model:
            done.add(row["entity_id"])
    return done


def run(args: argparse.Namespace) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    if taxonomy.get("status") != "frozen":
        raise SystemExit("taxonomy is not frozen")
    category_ids, taxonomy_text = taxonomy_prompt(taxonomy)
    system = SYSTEM + BATCH_NOTE + taxonomy_text
    packs = {p["entity_id"]: p for p in read_jsonl(OUT / "evidence.jsonl")}
    wanted = (
        json.loads((OUT / "pilot_ids.json").read_text(encoding="utf-8"))
        if args.ids == "pilot"
        else sorted(packs)
    )
    if args.ids == "remaining":  # entities no other model has labelled yet
        done = labelled_by_others(f"agy:{args.agy_model}")
        wanted = [e for e in wanted if e not in done]
    if args.limit:
        wanted = wanted[: args.limit]
    if args.engine == "api":
        load_env()
        call, model, workers, interval = (
            api_engine(args, system, category_ids),
            MODEL,
            1,
            60 / args.rpm,
        )
    else:
        call, model = agy_engine(args, system, category_ids), f"agy:{args.agy_model}"
        workers, interval = args.workers, 0.0
    cache_dir = OUT / "llm_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_file(entity_id: str) -> Path:
        # Per entity, independent of how entities are grouped into requests.
        key = json.dumps(
            [PROMPT_VERSION, model, args.thinking_budget, system, render_pack(packs[entity_id])]
        )
        return cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    todo = [e for e in wanted if not cache_file(e).exists()]
    chunks = [todo[i : i + args.batch] for i in range(0, len(todo), args.batch)]
    print(
        f"{model}: {len(wanted)} wanted, {len(wanted) - len(todo)} cached, {len(chunks)} requests"
    )
    started = time.perf_counter()
    failures: list[dict] = []
    lock = threading.Lock()
    pace = {"last": -interval, "pause_until": 0.0, "pause": 60.0}

    def wait_turn() -> None:
        while True:
            with lock:
                now = time.perf_counter()
                ready = max(pace["last"] + interval, pace["pause_until"])
                if now >= ready:
                    pace["last"] = now
                    return
            time.sleep(min(5.0, ready - now))

    def process(n: int, chunk: list[str]) -> None:
        keys = {f"e{i}": eid for i, eid in enumerate(chunk, 1)}
        user = "\n\n".join(
            f"### {k}\nEntity: {packs[e]['name']}\nEvidence:\n{render_pack(packs[e])}"
            for k, e in keys.items()
        )
        result = None
        for attempt in range(6):
            wait_turn()
            try:
                result = call(user)
                break
            except EngineError as error:
                if not error.retry:
                    with lock:
                        failures.append({"chunk": n, "error": str(error)})
                    return
                with lock:  # quota or overload: every worker pauses, the pause doubles
                    pace["pause_until"] = time.perf_counter() + pace["pause"]
                    pace["pause"] = min(pace["pause"] * 2, 1800)
                print(f"request {n}: {str(error)[:120]} (retry {attempt + 1})", flush=True)
        if result is None:
            with lock:
                failures.append({"chunk": n, "error": "no result after retries"})
            return
        text, tokens = result
        try:
            labels = parse_labels(text)
        except (ValueError, KeyError, TypeError) as error:
            with lock:
                failures.append({"chunk": n, "error": f"unparsable response: {error}"})
            return
        with lock:
            pace["pause"] = 60.0
        got = {}
        for label in labels:
            if isinstance(label, dict) and label.get("key") in keys and label["key"] not in got:
                if valid_label(label, category_ids):
                    got[label["key"]] = label
        for k, entity_id in keys.items():
            label = got.get(k)
            if label is None:
                with lock:
                    failures.append({"entity_id": entity_id, "error": "missing or invalid label"})
                continue
            known = {s["id"] for s in packs[entity_id]["snippets"]}
            row = {
                "entity_id": entity_id,
                "name": packs[entity_id]["name"],
                **{f: label[f] for f in LABEL_FIELDS},
                "unknown_evidence_ids": [e for e in label["evidence"] if e not in known],
                "model": model,
                "prompt_version": PROMPT_VERSION,
                # Request tokens shared equally by the entities of the request.
                "tokens": {t: round(v / len(keys), 1) for t, v in tokens.items()},
            }
            cache_file(entity_id).write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
        print(f"request {n}/{len(chunks)}: {len(got)}/{len(keys)} labels, {tokens}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda item: process(*item), enumerate(chunks, 1)))

    rows = []
    for entity_id in wanted:
        path = cache_file(entity_id)
        if path.exists():
            rows.append(json.loads(path.read_text(encoding="utf-8")))
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    (OUT / f"labels-{args.ids}.jsonl").write_text(body, encoding="utf-8", newline="\n")
    tok = {k: round(sum(r["tokens"][k] for r in rows)) for k in ("prompt", "output", "thoughts")}
    summary = {
        "wanted": len(wanted),
        "labels": len(rows),
        "failures": failures,
        "seconds": round(time.perf_counter() - started, 1),
        "tokens_all_labels": tok,
        "tokens_per_entity": {k: round(v / max(1, len(rows)), 1) for k, v in tok.items()},
        "by_basis": Counter(r["basis"] for r in rows),
        "by_confidence": Counter(r["confidence"] for r in rows),
        "unclear": sum(r["category"] == "other.unclear" for r in rows),
        "bad_evidence_ids": sum(bool(r["unknown_evidence_ids"]) for r in rows),
        "by_l1": Counter(r["category"].split(".")[0] for r in rows),
    }
    (OUT / f"run-{args.ids}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "failures"}, ensure_ascii=False))
    print(f"failures: {len(failures)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pack")
    sub.add_parser("pilot")
    p_run = sub.add_parser("run")
    p_run.add_argument("--ids", choices=["pilot", "all", "remaining"], default="pilot")
    p_run.add_argument("--limit", type=int, default=0)
    p_run.add_argument("--batch", type=int, default=20, help="entities per request")
    p_run.add_argument("--rpm", type=float, default=4, help="requests/minute (free tier 5)")
    p_run.add_argument("--thinking-budget", type=int, default=512, help="api engine only")
    p_run.add_argument("--engine", choices=["agy", "api"], default="agy")
    p_run.add_argument("--agy", default=str(Path.home() / "AppData/Local/agy/bin/agy.exe"))
    p_run.add_argument("--agy-model", default="gemini-3.8-flash-medium")
    p_run.add_argument("--workers", type=int, default=3, help="agy engine only")
    p_run.add_argument("--timeout", type=int, default=300, help="seconds per agy call")
    args = parser.parse_args()
    {"pack": pack, "pilot": pilot, "run": run}[args.cmd](args)


if __name__ == "__main__":
    main()
