"""Versioned retrieval text: visual annotations only, never VQA answers."""

METADATA_VERSION = "title-tags-description-chunk-mean-l2-v2"


def metadata_text(asset: dict) -> str:
    return "\n".join(
        value for value in (
            str(asset.get("keyword", "")).replace("_", " "),
            ", ".join(str(tag) for tag in asset.get("tags", [])),
            str(asset.get("description", "")),
        ) if value.strip()
    ).strip() or str(asset["category"])
