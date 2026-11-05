"""Validate a Kaggle export and atomically install it into a new artifact directory."""

import argparse
import shutil
import tempfile
from pathlib import Path
from zipfile import ZipFile

import _bootstrap  # noqa: F401

from visualseek_retrieval.artifact import ArtifactIndex


def import_archive(archive_path: Path, output: Path) -> int:
    if output.exists():
        raise ValueError("Output already exists; choose a new versioned directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="embedding-import-", dir=output.parent) as temporary:
        stage = Path(temporary) / "artifact"
        stage.mkdir()
        with ZipFile(archive_path) as archive:
            names = ("manifest.json", "vectors.npy", "assets.jsonl")
            if sum(archive.getinfo(name).file_size for name in names) > 512 * 1024 * 1024:
                raise ValueError("Artifact exceeds 512 MiB import limit")
            # Never extract arbitrary paths or executable files from the archive.
            for name in names:
                with archive.open(name) as source, (stage / name).open("wb") as target:
                    shutil.copyfileobj(source, target)
        index = ArtifactIndex(stage)
        count = len(index.assets)
        del index
        stage.rename(output)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/embeddings/culture-siglip2"))
    args = parser.parse_args()
    count = import_archive(args.archive, args.output)
    print(f"Imported {count} vectors to {args.output}. Restart the API if it was already running.")


if __name__ == "__main__":
    main()
