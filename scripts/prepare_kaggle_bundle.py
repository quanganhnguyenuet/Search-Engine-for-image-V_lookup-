"""Package only the code needed for Kaggle, never local data or credentials."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "scripts/kaggle_embed.py",
    "scripts/_bootstrap.py",
    "scripts/kaggle-requirements.txt",
]
PACKAGE_DIRECTORIES = [
    "packages/embedding/src/visualseek_embedding",
    "packages/retrieval/src/visualseek_retrieval",
    "packages/preprocessing/src/visualseek_preprocessing",
]


def build_bundle(output: Path) -> int:
    # Package initializers import contracts too. Include each package's Python source
    # tree rather than maintaining an incomplete list of direct script imports.
    sources = [ROOT / name for name in FILES]
    for directory in PACKAGE_DIRECTORIES:
        sources.extend(sorted((ROOT / directory).rglob("*.py")))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
        for source in sources:
            archive.write(source, source.relative_to(ROOT))
    temporary.replace(output)
    return len(sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/exports/culture-kaggle-code.zip"))
    args = parser.parse_args()
    count = build_bundle(args.output)
    print(f"Kaggle code bundle: {args.output} ({count} source files)")


if __name__ == "__main__":
    main()
