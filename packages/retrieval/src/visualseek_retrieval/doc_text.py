"""The entity card at the top of every doc_text (scripts/build_doc_texts.py), shared with the
images an admin uploads so that both are embedded from the same wording, and the page / OCR
sections that follow it."""

from __future__ import annotations

from visualseek_retrieval.text import normalize

OCR_MIN_SCORE = 0.8
OCR_MAX_ITEMS = 30
DESCRIPTION_CHARS = 200
PAGE_MAX_LINES = 6


def entity_card(entity: dict, brand_names: dict[str, str]) -> str:
    parts = [f"Sản phẩm: {entity['name']}"]
    if entity["brand_id"]:
        parts.append(f"Hãng: {brand_names[entity['brand_id']]}")
    if entity["notes"]:
        parts.append(f"Ghi chú: {'; '.join(entity['notes'])}")
    return " | ".join(parts)


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for value in values:
        value = " ".join(str(value).split())
        key = normalize(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def page_lines(sources: list[dict]) -> list[str]:
    """Page context lines of an asset's sources (page title, h1, JSON-LD product, description)."""
    lines: list[str] = []
    for source in sources:
        page = source.get("page") or {}
        lines += [
            source.get("page_title") or "",
            page.get("title") or "",
            page.get("og_title") or "",
        ]
        lines += list(page.get("h1") or [])
        for product in page.get("products") or []:
            if product.get("lists_this_image"):
                lines.append(" ".join(filter(None, [product.get("name"), product.get("model")])))
        if page.get("description"):
            lines.append(page["description"][:DESCRIPTION_CHARS])
    return unique(lines)[:PAGE_MAX_LINES]


def ocr_lines(ocr: list[dict] | None) -> list[str]:
    items = [
        item["text"]
        for item in ocr or []
        if item.get("score", 0) >= OCR_MIN_SCORE and len(normalize(item["text"])) >= 2
    ]
    return unique(items)[:OCR_MAX_ITEMS]


def context_sections(pages: list[str], ocr: list[str]) -> list[str]:
    sections = []
    if pages:
        sections.append("Trang: " + " | ".join(pages))
    if ocr:
        sections.append("Chữ trên ảnh: " + " ".join(ocr))
    return sections
