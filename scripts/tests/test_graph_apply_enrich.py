import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph_apply  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


def test_llm_row_replaces_free_row_instead_of_union(tmp_path, monkeypatch):
    (tmp_path / "graph").mkdir()
    write_jsonl(
        tmp_path / "graph/enrich-free.jsonl",
        [
            {"entity_id": "brick", "treats": ["chống thấm"], "material": "đồng"},
            {"entity_id": "free-only", "treats": ["dạ dày"]},
        ],
    )
    cache = tmp_path / "corpus/enrich_cache"
    cache.mkdir(parents=True)
    llm = {
        "entity_id": "brick",
        "treats": [],
        "material": "đất nung",
        "model": "agy:gemini-3.8-flash-low",
        "prompt_version": graph_apply.ENRICH_PROMPT_VERSION,
    }
    (cache / "a.json").write_text(json.dumps(llm, ensure_ascii=False), "utf-8")
    stale = {**llm, "material": "gỗ", "prompt_version": "old"}
    (cache / "b.json").write_text(json.dumps(stale, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(graph_apply, "ROOT", tmp_path)
    monkeypatch.setattr(graph_apply, "GRAPH_DIR", tmp_path / "corpus")
    monkeypatch.setattr(graph_apply, "ENRICH_DECISIONS", tmp_path / "none.jsonl")

    merged = graph_apply.load_enrichment()

    assert merged["brick"]["treats"] == []
    assert merged["brick"]["material"] == "đất nung"
    assert merged["brick"]["enrich_source"] == "gemini"
    assert merged["free-only"]["treats"] == ["dạ dày"]


def test_clean_enrichment_folds_variants_onto_one_node():
    clean = graph_apply.clean_enrichment(
        {
            "treats": ["viêm loét dạ dày", " "],
            "ingredients": ["Famotidine", "Famotidin", "Đẳng sâm", "AHA"],
            "material": "Gốm",
        }
    )
    assert clean["treats"] == ["viêm loét dạ dày", "dạ dày"]
    assert clean["ingredients"] == ["Famotidin", "Đẳng sâm", "AHA"]
    assert clean["material"] == "gốm sứ"
