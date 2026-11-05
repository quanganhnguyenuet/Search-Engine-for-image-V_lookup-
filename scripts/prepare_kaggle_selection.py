"""Freeze the exact local corpus as a small Kaggle input, without uploading images."""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import _bootstrap  # noqa: F401

from visualseek_preprocessing.culture_dataset import CATEGORIES, image_path


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def prepare_selection(manifest: Path, image_root: Path, output: Path) -> dict:
    root = image_root.resolve()
    rows = []
    ids = set()
    with manifest.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            asset_id = row["asset_id"]
            if not re.fullmatch(r"[a-f0-9]{64}", asset_id) or asset_id in ids:
                raise ValueError("Manifest must contain unique SHA-256 asset IDs")
            if row["category"] not in CATEGORIES:
                raise ValueError(f"Unsupported category: {row['category']}")
            name = image_path(row)
            file = (root / name).resolve()
            if not file.is_relative_to(root) or not file.is_file():
                raise ValueError(f"Missing or unsafe local image: {name}")
            if hash_file(file) != asset_id:
                raise ValueError(f"Local image differs from its manifest checksum: {name}")
            row.pop("questions", None)
            row.pop("annotation_metadata", None)
            rows.append(row)
            ids.add(asset_id)
            if len(rows) % 2000 == 0:
                print(f"Verified {len(rows)} local images", flush=True)
    if not rows:
        raise ValueError("Local manifest is empty")
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
    selection = {
        "schema_version": 1,
        "source_manifest": manifest.name,
        "count": len(rows),
        "categories": dict(Counter(row["category"] for row in rows)),
        "asset_ids_sha256": hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest(),
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
        archive.writestr("culture-assets.jsonl", payload)
        archive.writestr(
            "selection.json", json.dumps(selection, ensure_ascii=False, indent=2) + "\n"
        )
    temporary.replace(output)
    return selection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/processed/culture-search/assets.jsonl")
    )
    parser.add_argument("--image-root", type=Path, default=Path("data/raw/vietnamese-culture-vqa"))
    parser.add_argument("--output", type=Path, default=Path("data/exports/culture-selection.zip"))
    args = parser.parse_args()
    selection = prepare_selection(args.manifest, args.image_root, args.output)
    print(json.dumps(selection, ensure_ascii=False, indent=2))
    print(f"Kaggle selection bundle: {args.output}")


if __name__ == "__main__":
    main()
