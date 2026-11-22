#!/usr/bin/env python3
"""Find near-duplicate images in the products-v1 corpus (plan phase P0).

Exact duplicates are already merged by sha256 in `build_corpus_manifest.py`. This script finds the
same picture saved at another size, quality or format, which sha256 cannot see.

The target is "the same picture", not "a similar product shot". Colour variants, other angles
and other pack sizes of a product photographed on white often have identical or close pHashes
(e.g. two drug boxes that differ only in the printed name), and merging them would mislabel
images. Thresholds were set by inspecting pairs on both sides of each limit (2026-11-22).

Only images with the same set of entity ids are merged. A stock template reused for two products
(one pill bottle labelled "Perindopril 4mg" and "Klonopin 2mg") passes every pixel test, and a
cross-entity merge would give each image the other's label. Within one entity a wrong merge only
drops an image; the labels stay right. Identical files across entities are already merged by
sha256 in `build_corpus_manifest.py`.

Method:
  1. Features per image (cached by asset_id): 64-bit pHash of the EXIF-oriented image composited on
     white, a 16x16 RGB thumbnail, and the pixel size.
  2. Candidates: same entity ids, pHash Hamming distance <= --max-hamming (exact, all pairs),
     aspect ratios within --max-aspect, and 16x16 mean absolute RGB difference <= --max-mae.
  3. Verification: both images resized to 64x64; the 99th percentile of the per-pixel difference
     (max over channels) must be <= --max-p99. A small region that differs, such as a product
     name or a coloured part, fails this test even when the mean difference is small.
  4. Star grouping: images are visited best first (largest pixel area, then bytes, then asset_id).
     An unassigned image becomes a representative and takes every unassigned image that verified
     against it directly. No chaining: each member is within the thresholds of its representative.

Input is the corpus written by `build_corpus_manifest.py` (with or without merges applied; merged
members are expanded back). Output is a JSON file that `build_corpus_manifest.py
--near-duplicates` applies. Needs numpy and Pillow:

  python scripts/find_near_duplicates.py --corpus data/processed/products-v1 \
      --output data/processed/products-v1/near_duplicates.json
"""

import argparse
import hashlib
import json
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

VERSION = "near-duplicates-3"
ROOT = Path(__file__).resolve().parents[1]
HASH_SIZE, HASH_INPUT, THUMB, WORK_SIDE, FINE = 8, 32, 16, 512, 64
SAVE_EVERY = 2000
Image.MAX_IMAGE_PIXELS = None  # local, already-reviewed files; some product shots are huge

_N = np.arange(HASH_INPUT)
_DCT = np.cos(np.pi * (2 * _N[None, :] + 1) * _N[:, None] / (2 * HASH_INPUT))


def load_rgb(path: Path) -> Image.Image:
    """First frame, EXIF orientation applied, transparency composited on white."""
    with Image.open(path) as image:
        image.draft("RGB", (WORK_SIDE, WORK_SIDE))  # JPEG decodes at a reduced scale
        image = ImageOps.exif_transpose(image)
        image.thumbnail((WORK_SIDE, WORK_SIDE))  # bound memory before any full-size copy
        if image.mode in ("RGBA", "LA", "P", "PA"):
            image = image.convert("RGBA")
            canvas = Image.new("RGBA", image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(canvas, image)
        return image.convert("RGB")


def phash(image: Image.Image) -> int:
    """pHash: DCT of a 32x32 grey image, low 8x8 frequencies compared to their median."""
    grey = np.asarray(image.convert("L").resize((HASH_INPUT, HASH_INPUT), Image.LANCZOS),
                      dtype=np.float64)
    low = (_DCT @ grey @ _DCT.T)[:HASH_SIZE, :HASH_SIZE]
    bits = (low > np.median(low)).flatten()
    return int("".join("1" if bit else "0" for bit in bits), 2)


def features(job: tuple[str, str]) -> tuple[str, int | None, bytes | None, int, int, str]:
    asset_id, path = job
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with Image.open(path) as probe:  # real size (draft() shrinks it), header only
                width, height = probe.size
                if probe.getexif().get(0x0112) in (5, 6, 7, 8):  # EXIF rotates by 90 degrees
                    width, height = height, width
            image = load_rgb(Path(path))
            thumb = np.asarray(image.resize((THUMB, THUMB), Image.LANCZOS), dtype=np.uint8)
            return asset_id, phash(image), thumb.tobytes(), width, height, ""
    except Exception as error:  # unreadable file: reported, never merged
        return asset_id, None, None, 0, 0, f"{type(error).__name__}: {error}"[:200]


def read_corpus(corpus: Path) -> tuple[Path, list[dict]]:
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    images = {}
    with (corpus / "assets.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            asset = json.loads(line)
            for item in [asset, *asset.get("merged", [])]:
                images[item["asset_id"]] = {
                    "asset_id": item["asset_id"], "image_path": item["image_path"],
                    "bytes": item["bytes"], "entity_ids": sorted(item.get("entity_ids")
                                                                  or asset["entity_ids"])}
    return Path(manifest["image_root"]), [images[key] for key in sorted(images)]


def load_cache(path: Path) -> dict[str, tuple]:
    if not path.exists():
        return {}
    data = np.load(path, allow_pickle=False)
    return {str(asset_id): (int(h), bytes(t), int(w), int(hh))
            for asset_id, h, t, w, hh in zip(data["asset_id"], data["phash"], data["thumb"],
                                             data["width"], data["height"], strict=True)}


def save_cache(path: Path, cache: dict[str, tuple]) -> None:
    keys = sorted(cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".tmp.npz")
    np.savez(temporary, asset_id=np.array(keys),
             phash=np.array([cache[k][0] for k in keys], dtype=np.uint64),
             thumb=np.array([np.frombuffer(cache[k][1], dtype=np.uint8) for k in keys]),
             width=np.array([cache[k][2] for k in keys], dtype=np.int64),
             height=np.array([cache[k][3] for k in keys], dtype=np.int64))
    temporary.replace(path)


def compute_features(image_root: Path, images: list[dict], cache_path: Path,
                     workers: int) -> tuple[dict[str, tuple], dict[str, str]]:
    cache = load_cache(cache_path)
    todo = [(i["asset_id"], str(image_root / i["image_path"])) for i in images
            if i["asset_id"] not in cache]
    errors: dict[str, str] = {}
    if todo:
        print(f"Computing features for {len(todo)} images ({len(cache)} cached)", flush=True)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for start in range(0, len(todo), SAVE_EVERY):
                try:
                    for asset_id, value, thumb, width, height, error in pool.map(
                            features, todo[start:start + SAVE_EVERY], chunksize=16):
                        if value is None:
                            errors[asset_id] = error
                        else:
                            cache[asset_id] = (value, thumb, width, height)
                except BrokenProcessPool:
                    save_cache(cache_path, cache)
                    raise SystemExit(f"A worker died (often memory); {len(cache)} images are "
                                     "cached. Rerun, with fewer --workers, to continue.") from None
                save_cache(cache_path, cache)
                print(f"  {min(start + SAVE_EVERY, len(todo))}/{len(todo)}", flush=True)
    return cache, errors


def candidate_pairs(hashes: np.ndarray, max_hamming: int, block: int = 1024):
    """All (i, j, distance) with i < j and Hamming distance <= max_hamming; exact, O(n^2 / 2)."""
    for start in range(0, len(hashes), block):
        rows = hashes[start:start + block]
        distance = np.bitwise_count(rows[:, None] ^ hashes[None, :])
        for i, j in zip(*np.nonzero(distance <= max_hamming), strict=True):
            a = start + int(i)
            if a < j:
                yield a, int(j), int(distance[i, j])


def fine_loader(image_root: Path, images: list[dict]):
    """asset_id -> 64x64 RGB array (int16), decoded on first use; only candidates need it."""
    paths = {i["asset_id"]: image_root / i["image_path"] for i in images}
    loaded: dict[str, np.ndarray] = {}

    def fine_of(asset_id: str) -> np.ndarray:
        if asset_id not in loaded:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                image = load_rgb(paths[asset_id]).resize((FINE, FINE), Image.LANCZOS)
            loaded[asset_id] = np.asarray(image, dtype=np.int16)
        return loaded[asset_id]

    return fine_of


def fine_p99(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.percentile(np.abs(a - b).max(axis=2), 99))


def find(images: list[dict], cache: dict[str, tuple], fine_of, *, max_hamming: int,
         max_mae: float, max_aspect: float, max_p99: float) -> tuple[list[dict], list[dict]]:
    """Return (groups, verified pairs). Images without `entity_ids` count as one entity set."""
    ids = [i["asset_id"] for i in images if i["asset_id"] in cache]
    size = {i["asset_id"]: i["bytes"] for i in images}
    entities = {i["asset_id"]: tuple(i.get("entity_ids") or ()) for i in images}
    hashes = np.array([cache[k][0] for k in ids], dtype=np.uint64)
    thumbs = {k: np.frombuffer(cache[k][1], dtype=np.uint8).astype(np.int16) for k in ids}
    pairs = []
    for a, b, distance in candidate_pairs(hashes, max_hamming):
        ka, kb = ids[a], ids[b]
        if entities[ka] != entities[kb]:
            continue
        (_, _, wa, ha), (_, _, wb, hb) = cache[ka], cache[kb]
        aspect = abs(np.log((wa / ha) / (wb / hb)))
        if aspect > max_aspect:
            continue
        mae = float(np.abs(thumbs[ka] - thumbs[kb]).mean())
        if mae > max_mae:
            continue
        p99 = fine_p99(fine_of(ka), fine_of(kb))
        if p99 <= max_p99:
            pairs.append({"a": ka, "b": kb, "hamming": distance, "mae": round(mae, 2),
                          "p99": round(p99, 1), "aspect": round(float(aspect), 4)})

    neighbours: dict[str, dict[str, dict]] = {}
    for pair in pairs:
        neighbours.setdefault(pair["a"], {})[pair["b"]] = pair
        neighbours.setdefault(pair["b"], {})[pair["a"]] = pair
    order = sorted(neighbours, key=lambda k: (-cache[k][2] * cache[k][3], -size[k], k))
    assigned: set[str] = set()
    groups = []
    for representative in order:
        if representative in assigned:
            continue
        members = sorted(k for k in neighbours[representative] if k not in assigned)
        if not members:
            continue
        assigned.update([representative, *members])
        groups.append({"representative": representative, "members": [
            {"asset_id": k, "hamming": neighbours[representative][k]["hamming"],
             "mae": neighbours[representative][k]["mae"],
             "p99": neighbours[representative][k]["p99"]} for k in members]})
    groups.sort(key=lambda g: g["representative"])
    return groups, pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/processed/products-v1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, help="feature cache (.npz); default under --corpus")
    parser.add_argument("--pairs", type=Path, help="also write every verified pair (jsonl)")
    parser.add_argument("--max-hamming", type=int, default=6)
    parser.add_argument("--max-mae", type=float, default=8.0,
                        help="mean absolute RGB difference of 16x16 thumbnails, 0..255")
    parser.add_argument("--max-aspect", type=float, default=0.03,
                        help="|log(aspect_a / aspect_b)|")
    parser.add_argument("--max-p99", type=float, default=16.0,
                        help="99th percentile of the 64x64 per-pixel difference, 0..255")
    parser.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1))
    args = parser.parse_args()

    image_root, images = read_corpus(args.corpus)
    cache, errors = compute_features(image_root, images, args.cache or (
        args.corpus / "cache" / "image_features.npz"), args.workers)
    groups, pairs = find(images, cache, fine_loader(image_root, images),
                         max_hamming=args.max_hamming, max_mae=args.max_mae,
                         max_aspect=args.max_aspect, max_p99=args.max_p99)
    entity_ids = {i["asset_id"]: i["entity_ids"] for i in images}
    cross = sum(any(entity_ids[m["asset_id"]] != entity_ids[g["representative"]]
                    for m in g["members"]) for g in groups)
    result = {
        "version": VERSION,
        "params": {"hash": f"phash {HASH_SIZE}x{HASH_SIZE} from {HASH_INPUT}x{HASH_INPUT}",
                   "thumb": f"{THUMB}x{THUMB} RGB", "fine": f"{FINE}x{FINE} RGB p99",
                   "max_hamming": args.max_hamming, "max_mae": args.max_mae,
                   "max_aspect": args.max_aspect, "max_p99": args.max_p99,
                   "grouping": "star, best first by pixel area, bytes, asset_id",
                   "scope": "same entity ids only"},
        "counts": {"images": len(images), "hashed": len(images) - len(errors),
                   "unreadable": len(errors), "verified_pairs": len(pairs),
                   "groups": len(groups),
                   "merged_members": sum(len(g["members"]) for g in groups),
                   "groups_across_entities": cross,
                   "largest_group": max((len(g["members"]) + 1 for g in groups), default=0)},
        "phash": {k: f"{cache[k][0]:016x}" for k in sorted(cache)
                  if k in {i["asset_id"] for i in images}},
        "unreadable": dict(sorted(errors.items())),
        "groups": groups,
    }
    text = json.dumps(result, ensure_ascii=False, indent=1) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_name(args.output.name + ".tmp").write_bytes(text.encode("utf-8"))
    args.output.with_name(args.output.name + ".tmp").replace(args.output)
    if args.pairs:
        args.pairs.write_bytes("".join(json.dumps(p) + "\n" for p in pairs).encode("utf-8"))
    print(json.dumps(result["counts"], indent=2))
    print(f"sha256 {hashlib.sha256(text.encode('utf-8')).hexdigest()}  {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
