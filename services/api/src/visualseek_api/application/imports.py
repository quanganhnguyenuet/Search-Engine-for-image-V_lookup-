"""/admin CSV import: approved image rows -> images of existing entities, or new entities.

An import is a CSV of approved images (entity name, image URL, optionally the dataset entity id,
the source page URL and its title). A background job reconciles each import until nothing is
left to do; every step is idempotent, so a restart, an admin decision or a release just runs the
job again:

  1. fetch   download every image (public IPs only, per-host spacing) and read its source page
             like the corpus was read (visualseek_retrieval.page_context);
  2. dedupe  sha256 against the collection and the import; after matching, near duplicates
             (pHash + pixel check of scripts/find_near_duplicates.py) against the target
             entity's images;
  3. ocr     RapidOCR with the corpus settings (scripts/backfill_ocr.py);
  4. match   csv_import.EntityMatcher: existing entities get the images at once, near names
             wait for an admin to pick the entity or say "new", new names go to step 5;
  5. label   new entity: evidence pack -> classify (taxonomy) and enrich (relations) with the
             agy / Gemini prompts of scripts/graph_classify.py and graph_enrich.py, registered in
             graph/admin/new_entities.jsonl. The next release adds it to the graph; then its
             images are embedded and upserted like an upload.

Files: <admin_data_dir>/imports/<id>/import.json (state), source.csv, images/, items/ (one JSON
per kept image: page summary, OCR) and <admin_data_dir>/pages/ (parsed page cache).
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import numpy as np
import structlog

from visualseek_api.core.config import Settings
from visualseek_api.core.errors import ApplicationError

logger = structlog.get_logger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[5]
SCRIPTS = REPO_ROOT / "scripts"
TAXONOMY = REPO_ROOT / "graph/taxonomy-v1.json"
MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 15_000_000
MIN_IMAGE_SIDE = 32
KEEP_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
FETCH_WORKERS = 6
HOST_INTERVAL = 1.0
LABEL_BATCH = 5
LABEL_RETRIES = 3
OCR_MAX_SIDE, OCR_MAX_TEXT = 960, 80  # scripts/backfill_ocr.py
# scripts/find_near_duplicates.py defaults
NEAR_MAX_HAMMING, NEAR_MAX_MAE, NEAR_MAX_ASPECT, NEAR_MAX_P99 = 6, 8.0, 0.03, 16.0
ISSUES_LIMIT = 500
THUMBS = 6


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bad(message: str, code: str = "invalid_import") -> ApplicationError:
    return ApplicationError(message, code=code)


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"admin_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Labeller:
    """Classify + enrich new entities with the corpus prompts (scripts/graph_classify.py and
    scripts/graph_enrich.py), through the agy CLI or the Gemini API."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._modules = None
        self._engines: dict[str, object] = {}

    def available(self) -> str | None:
        """None when labelling can run, else why not (shown to the admin)."""
        engine = self.settings.admin_llm_engine
        if engine == "off":
            return "Gán nhãn tự động đang tắt (ADMIN_LLM_ENGINE=off)."
        if engine == "agy" and not Path(self.settings.admin_agy_path).exists():
            return f"Không thấy agy tại {self.settings.admin_agy_path}."
        return None

    def _mods(self):
        if self._modules is None:
            self._modules = (_load_script("graph_classify"), _load_script("graph_enrich"))
        return self._modules

    def _engine(self, kind: str):
        if kind in self._engines:
            return self._engines[kind]
        classify, enrich = self._mods()
        settings = self.settings
        agy = settings.admin_llm_engine == "agy"
        model = (
            settings.admin_agy_classify_model
            if kind == "classify"
            else settings.admin_agy_enrich_model
        )
        args = SimpleNamespace(
            agy=settings.admin_agy_path,
            agy_model=model,
            timeout=settings.admin_agy_timeout,
            thinking_budget=0,
        )
        if kind == "classify":
            taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
            ids, text = classify.taxonomy_prompt(taxonomy)
            system = classify.SYSTEM + classify.BATCH_NOTE + text
            if agy:
                call, name = classify.agy_engine(args, system, ids), f"agy:{model}"
            else:
                classify.load_env()
                call, name = classify.api_engine(args, system, ids), classify.MODEL
            engine = (call, name, ids, classify)
        else:
            if agy:
                call, name = enrich.agy_engine(args, enrich.SYSTEM), f"agy:{model}"
            else:
                enrich.load_env()
                call, name = enrich.api_engine(args, enrich.SYSTEM), enrich.MODEL
            engine = (call, name, None, enrich)
        self._engines[kind] = engine
        return engine

    def _ask(self, kind: str, user: str) -> tuple[str, str]:
        call, name, _, module = self._engine(kind)
        last = None
        for attempt in range(LABEL_RETRIES):
            try:
                text, _ = call(user)
                return text, name
            except module.EngineError as error:
                last = error
                if not error.retry:
                    break
                time.sleep(20 * (attempt + 1))
        raise RuntimeError(f"{kind}: {str(last)[:200]}")

    def classify(self, packs: list[dict]) -> dict[str, dict]:
        """entity id -> label (category, product_type, market_tier, evidence, basis,
        confidence, model, prompt_version). Entities missing from the answer are left out."""
        _, _, ids, module = self._engine("classify")
        keys = {f"e{i}": p for i, p in enumerate(packs, 1)}
        user = "\n\n".join(
            f"### {k}\nEntity: {p['name']}\nEvidence:\n{module.render_pack(p)}"
            for k, p in keys.items()
        )
        text, name = self._ask("classify", user)
        out = {}
        for label in module.parse_labels(text):
            pack = keys.get(label.get("key")) if isinstance(label, dict) else None
            if pack and pack["entity_id"] not in out and module.valid_label(label, ids):
                out[pack["entity_id"]] = {
                    **{f: label[f] for f in module.LABEL_FIELDS},
                    "model": name,
                    "prompt_version": module.PROMPT_VERSION,
                }
        return out

    def enrich(self, packs: list[dict], labels: dict[str, dict], names: dict) -> dict[str, dict]:
        _, _, _, module = self._engine("enrich")
        keys = {f"e{i}": p for i, p in enumerate(packs, 1)}

        def cat_info(entity_id: str) -> dict | None:
            label = labels.get(entity_id)
            if not label:
                return None
            return {
                "category": label["category"],
                "category_name": names.get(label["category"], ("", ""))[0],
                "product_type": label["product_type"],
            }

        user = "\n\n".join(
            f"### {k}\nEntity: {p['name']}\nEvidence:\n"
            f"{module.render_pack(p, cat_info(p['entity_id']))}"
            for k, p in keys.items()
        )
        text, name = self._ask("enrich", user)
        out = {}
        for label in module.parse_labels(text):
            pack = keys.get(label.get("key")) if isinstance(label, dict) else None
            if pack and pack["entity_id"] not in out and module.valid_enrich(label):
                out[pack["entity_id"]] = {
                    **{f: label[f] for f in module.ENRICH_FIELDS},
                    "model": name,
                    "prompt_version": module.PROMPT_VERSION,
                }
        return out


class ImportService:
    def __init__(self, admin, settings: Settings) -> None:
        from visualseek_retrieval import page_context

        self.admin = admin
        self.settings = settings
        self.root = Path(settings.admin_data_dir) / "imports"
        page_context.set_cache(Path(settings.admin_data_dir) / "pages")
        self.labeller = Labeller(settings)
        self.lock = threading.RLock()
        self.states: dict[str, dict] = {}
        self.staged: dict[str, Path] = {}  # asset id -> file of an import image, for thumbnails
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="admin-import")
        self.queued: set[str] = set()
        self.get_service = lambda: None  # set by the application (the current search service)
        self._ocr = None
        self._features: dict[str, tuple | None] = {}
        self._near = None
        self._load()

    # ---------------------------------------------------------------- persistence

    def _load(self) -> None:
        if not self.root.exists():
            return
        for path in sorted(self.root.glob("*/import.json")):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                logger.warning("import_state_unreadable", path=str(path))
                continue
            self.states[state["id"]] = state
            for row in state["rows"]:
                if row.get("file"):
                    self.staged[row["asset_id"]] = path.parent / "images" / row["file"]

    def _save(self, state: dict) -> None:
        with self.lock:
            folder = self.root / state["id"]
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "import.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)

    def _item_path(self, state: dict, asset_id: str) -> Path:
        return self.root / state["id"] / "items" / f"{asset_id}.json"

    def _item(self, state: dict, asset_id: str) -> dict:
        return json.loads(self._item_path(state, asset_id).read_text(encoding="utf-8"))

    def _write_item(self, state: dict, item: dict) -> None:
        path = self._item_path(state, item["asset_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")

    def staged_file(self, asset_id: str) -> Path | None:
        path = self.staged.get(asset_id)
        return path if path is not None and path.exists() else None

    # ---------------------------------------------------------------- API

    def create(self, file_name: str, data: bytes, actor: dict) -> dict:
        from visualseek_retrieval.csv_import import CsvError, group_rows, read_rows

        if len(data) > MAX_CSV_BYTES:
            raise _bad("Tệp CSV quá lớn (tối đa 5 MB).", code="csv_too_large")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("cp1258")
            except UnicodeDecodeError as error:
                raise _bad("CSV phải là UTF-8.", code="invalid_csv") from error
        try:
            rows, skipped = read_rows(text)
        except CsvError as error:
            raise _bad(str(error), code="invalid_csv") from error
        import_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        groups = group_rows(rows)
        by_line = {line: g["key"] for g in groups for line in g["lines"]}
        state = {
            "id": import_id,
            "file_name": Path(file_name or "import.csv").name[:120],
            "created_at": _now(),
            "actor": actor,
            "status": "queued",
            "step": "queued",
            "progress": {"done": 0, "total": len(rows)},
            "error": None,
            "rows": [{**row, "group": by_line[row["line"]], "status": None} for row in rows],
            "skipped": [{**row, "status": "skipped"} for row in skipped],
            "groups": [
                {
                    "key": g["key"],
                    "name": g["name"],
                    "csv_entity_id": g["csv_entity_id"],
                    "match": None,
                    "decision": None,
                    "target": None,
                    "near_checked": [],
                    "published": [],
                    "registered": False,
                    "label": None,
                    "enrich": None,
                    "error": None,
                }
                for g in groups
            ],
        }
        folder = self.root / import_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "source.csv").write_bytes(data)
        with self.lock:
            self.states[import_id] = state
            self._save(state)
        logger.info("import_created", import_id=import_id, rows=len(rows), groups=len(groups))
        self.enqueue(import_id)
        return self.view(import_id)

    def list(self) -> list[dict]:
        with self.lock:
            states = sorted(self.states.values(), key=lambda s: s["created_at"], reverse=True)
            return [self._summary(s) for s in states]

    def view(self, import_id: str) -> dict:
        service = self.get_service()
        with self.lock:
            state = self._state(import_id)
            groups = [self._group_view(state, g, service) for g in state["groups"]]
            issues = [
                {
                    k: row.get(k)
                    for k in ("line", "entity_name", "image_url", "status", "reason", "dup_of")
                }
                for row in [*state["skipped"], *state["rows"]]
                if row.get("status") in ("skipped", "duplicate", "failed")
            ]
            return {
                **self._summary(state),
                "groups": groups,
                "issues": issues[:ISSUES_LIMIT],
                "issues_total": len(issues),
                "labeller": self.labeller.available(),
            }

    def decide(self, import_id: str, key: str, entity_id: str | None, new: bool, actor: dict):
        """Set a group's entity: an existing ``entity_id``, or ``new``. Allowed until its images
        are in the collection (an existing match) or the new entity is released."""
        service = self.get_service()
        with self.lock:
            state = self._state(import_id)
            group = self._group(state, key)
            status = self._group_status(state, group, service)
            if status in ("published", "released", "publishing"):
                raise _bad("Ảnh của nhóm này đã được nạp, không đổi được nữa.", "group_locked")
            if new == bool(entity_id):
                raise _bad("Chọn một entity có sẵn hoặc tạo entity mới.")
            if entity_id:
                entity_id = service.pipeline.review.canonical(entity_id)
                if entity_id not in service.admin_index.entities:
                    raise _bad("Không có entity này.", "not_found")
            previous = group.get("decision") or {}
            if (
                previous.get("type") == "new"
                and group.get("target") in self._waiting_entities(service)
                and not self._wanted_elsewhere(group)
            ):
                self._drop_new_entity(state, group, actor)
            group["decision"] = {
                "type": "new" if new else "existing",
                "entity_id": entity_id,
                "at": _now(),
                "actor": actor,
                "auto": False,
            }
            group["target"] = entity_id
            group["registered"] = False
            group["label"] = group["enrich"] = group["error"] = None
            group["near_checked"] = []
            self._save(state)
        self.enqueue(import_id)
        return self.view(import_id)

    def retry(self, import_id: str, key: str | None = None) -> dict:
        with self.lock:
            state = self._state(import_id)
            for group in state["groups"]:
                if key is None or group["key"] == key:
                    group["error"] = None
                    if group.get("registered") and not group.get("label"):
                        group["registered"] = False  # label again
            for row in state["rows"]:
                if row.get("status") == "failed":
                    row["status"] = None
            state["error"] = None
            self._save(state)
        self.enqueue(import_id)
        return self.view(import_id)

    def _waiting_entities(self, service) -> dict[str, dict]:
        """Registry rows of new entities the live graph does not have yet, by entity id."""
        from visualseek_retrieval.admin_overlay import load_new_entities

        live = service.admin_index.graph_entities
        rows = load_new_entities(Path(self.settings.admin_dir))
        return {entity_id: row for entity_id, row in rows.items() if entity_id not in live}

    def _wanted_elsewhere(self, group: dict) -> bool:
        """Another group, of any import, shares this group's new entity."""
        return any(
            other is not group
            and other.get("target") == group["target"]
            and (other.get("decision") or {}).get("type") == "new"
            for state in self.states.values()
            for other in state["groups"]
        )

    def pending_new_entities(self, service) -> list[dict]:
        """Registered new entities the live graph does not have yet (the next release adds them)."""
        rows = self._waiting_entities(service)
        names = service.admin_index.category_names
        out = []
        for entity_id, row in sorted(rows.items(), key=lambda kv: kv[1]["name"].casefold()):
            label = row.get("label") or {}
            out.append(
                {
                    "entity_id": entity_id,
                    "name": row["name"],
                    "import_id": row.get("import_id"),
                    "category": label.get("category"),
                    "category_name": names.get(label.get("category"), (None,))[0],
                    "product_type": label.get("product_type"),
                    "labelled": bool(label),
                }
            )
        return out

    def after_release(self) -> None:
        """Publish the images of new entities that the release just added."""
        with self.lock:
            ids = [
                s["id"]
                for s in self.states.values()
                if any((g.get("decision") or {}).get("type") == "new" for g in s["groups"])
            ]
        for import_id in ids:
            self.enqueue(import_id)

    def resume(self) -> None:
        """At start-up: run every import that still has work (a restart interrupted it)."""
        with self.lock:
            ids = [s["id"] for s in self.states.values() if s["status"] not in ("done",)]
        for import_id in ids:
            self.enqueue(import_id)

    def enqueue(self, import_id: str) -> None:
        with self.lock:
            if import_id in self.queued:
                return
            self.queued.add(import_id)
            state = self.states[import_id]
            if state["status"] not in ("running",):
                state["status"] = "queued"
        self.worker.submit(self._run, import_id)

    # ---------------------------------------------------------------- views

    def _state(self, import_id: str) -> dict:
        state = self.states.get(import_id)
        if state is None:
            raise ApplicationError("Unknown import.", code="not_found", status_code=404)
        return state

    @staticmethod
    def _group(state: dict, key: str) -> dict:
        for group in state["groups"]:
            if group["key"] == key:
                return group
        raise ApplicationError("Unknown group.", code="not_found", status_code=404)

    @staticmethod
    def _kept(state: dict, group: dict) -> list[str]:
        return [
            r["asset_id"]
            for r in state["rows"]
            if r["group"] == group["key"] and r.get("status") == "ok"
        ]

    def _group_status(self, state: dict, group: dict, service) -> str:
        rows = [r for r in state["rows"] if r["group"] == group["key"]]
        if any(r.get("status") is None for r in rows) or group.get("match") is None:
            return "processing"
        kept = self._kept(state, group)
        decision = group.get("decision")
        if decision is None:
            return "waiting_match"
        if not kept:
            return "no_images"
        published = set(group.get("published") or ())
        if decision["type"] == "existing":
            return "published" if published >= set(kept) else "publishing"
        if not group.get("registered"):
            return "label_failed" if group.get("error") else "labelling"
        live = service is not None and group["target"] in service.admin_index.graph_entities
        if not live:
            return "ready" if group.get("label") else "ready_unlabelled"
        return "released" if published >= set(kept) else "publishing"

    def _summary(self, state: dict) -> dict:
        service = self.get_service()
        statuses = [self._group_status(state, g, service) for g in state["groups"]]
        kinds = [
            ((g.get("decision") or {}).get("type"), (g.get("match") or {}).get("kind"))
            for g in state["groups"]
        ]
        rows = state["rows"]
        return {
            "id": state["id"],
            "file_name": state["file_name"],
            "created_at": state["created_at"],
            "actor": (state.get("actor") or {}).get("email"),
            "status": state["status"],
            "step": state["step"],
            "progress": state["progress"],
            "error": state["error"],
            "counts": {
                "rows": len(rows) + len(state["skipped"]),
                "images": sum(r.get("status") == "ok" for r in rows),
                "duplicates": sum(r.get("status") == "duplicate" for r in rows),
                "failed": sum(r.get("status") == "failed" for r in rows),
                "skipped": len(state["skipped"]),
                "groups": len(state["groups"]),
                "existing": sum(d == "existing" for d, _ in kinds),
                "new": sum(d == "new" for d, _ in kinds),
                "waiting_match": statuses.count("waiting_match"),
                "ready": statuses.count("ready") + statuses.count("ready_unlabelled"),
                "released": statuses.count("released"),
                "published_images": sum(len(g.get("published") or ()) for g in state["groups"]),
                "label_failed": statuses.count("label_failed"),
            },
        }

    def _group_view(self, state: dict, group: dict, service) -> dict:
        kept = self._kept(state, group)
        rows = [r for r in state["rows"] if r["group"] == group["key"]]
        match = group.get("match") or {}
        index = service.admin_index if service is not None else None

        def thumb(entity_id: str) -> str | None:
            if index is None:
                return None
            return next(iter(index.images.get(entity_id, ())), None)

        target = group.get("target")
        label = group.get("label") or {}
        enrich = group.get("enrich") or {}
        names = index.category_names if index else {}
        return {
            "key": group["key"],
            "name": group["name"],
            "csv_entity_id": group["csv_entity_id"],
            "kind": match.get("kind"),
            "score": match.get("score"),
            "candidates": [
                {
                    **c,
                    "thumb": thumb(c["entity_id"]),
                    "images": len(index.images.get(c["entity_id"], ())) if index else 0,
                }
                for c in match.get("candidates") or []
            ],
            "decision": {k: v for k, v in (group.get("decision") or {}).items() if k != "actor"}
            or None,
            "status": self._group_status(state, group, service),
            "target": {
                "id": target,
                "name": index.name(target)
                if index is not None and target in index.graph_entities
                else group["name"],
                "live": bool(index is not None and target in index.graph_entities),
            }
            if target
            else None,
            "rows": len(rows),
            "images": len(kept),
            "duplicates": sum(r.get("status") == "duplicate" for r in rows),
            "failed": sum(r.get("status") == "failed" for r in rows),
            "published": len(group.get("published") or ()),
            "thumbs": kept[:THUMBS],
            "label": {
                "category": label.get("category"),
                "category_name": names.get(label.get("category"), (None,))[0],
                "product_type": label.get("product_type"),
                "basis": label.get("basis"),
                "confidence": label.get("confidence"),
                "model": label.get("model"),
            }
            if label
            else None,
            "enrich": {
                k: enrich.get(k)
                for k in (
                    "brand",
                    "treats",
                    "ingredients",
                    "fits",
                    "origin",
                    "material",
                    "form_factor",
                    "target_audience",
                )
            }
            if enrich
            else None,
            "error": group.get("error"),
        }

    # ---------------------------------------------------------------- the job

    def _set(self, state: dict, **fields) -> None:
        with self.lock:
            state.update(fields)
            self._save(state)

    def _run(self, import_id: str) -> None:
        with self.lock:
            self.queued.discard(import_id)
            state = self.states[import_id]
        self._set(state, status="running", error=None)
        try:
            service = self.get_service()
            if service is None:
                raise RuntimeError("search service is not loaded")
            self._fetch(state, service)
            self._ocr_all(state)
            self._match(state, service)
            self._near_duplicates(state, service)
            self._publish_existing(state, service)
            self._label_new(state, service)
            self._publish_released(state, self.get_service())
            service = self.get_service()
            statuses = [self._group_status(state, g, service) for g in state["groups"]]
            if "waiting_match" in statuses:
                status = "waiting_match"
            elif {"ready", "ready_unlabelled"} & set(statuses):
                status = "waiting_release"
            elif "label_failed" in statuses:
                status = "label_failed"
            else:
                status = "done"
            self._set(state, status=status, step="done")
        except Exception as error:  # the import shows the error; a retry runs the job again
            logger.exception("import_failed", import_id=import_id)
            self._set(state, status="failed", error=str(error)[:300] or type(error).__name__)

    # step 1: download images and pages -----------------------------------------------------

    def _fetch(self, state: dict, service) -> None:
        from visualseek_retrieval import page_context
        from visualseek_retrieval.csv_import import page_summary

        todo = [r for r in state["rows"] if r.get("status") is None]
        if not todo:
            return
        self._set(
            state,
            step="fetch",
            progress={"done": len(state["rows"]) - len(todo), "total": len(state["rows"])},
        )
        polite = page_context.Polite(HOST_INTERVAL)
        pages: dict[str, dict | None] = {}
        page_lock = threading.Lock()
        known = set(service.image_paths) | set(self.admin.store.uploads())
        folder = self.root / state["id"] / "images"
        folder.mkdir(parents=True, exist_ok=True)

        def page_of(url: str) -> dict | None:
            if not url:
                return None
            with page_lock:
                if url in pages:
                    return pages[url]
            try:
                page = page_context.fetch_page(url, polite, retries=1)
            except Exception as error:  # a parser bug must not fail the image
                page = {"status": "failed", "error": str(error)[:200]}
            with page_lock:
                pages[url] = page
            return page

        def one(row: dict) -> dict:
            try:
                host = page_context.check_public(row["image_url"])
                polite.wait(host)
                body, final_url, _ = page_context.fetch_image(
                    row["image_url"], MAX_IMAGE_BYTES, referer=row["page_url"] or None
                )
            except Exception as error:
                return {"status": "failed", "reason": f"tải ảnh lỗi: {str(error)[:160]}"}
            raw_id = hashlib.sha256(body).hexdigest()
            try:
                data, extension, size = _normalise_image(body)
            except ValueError as error:
                return {"status": "failed", "reason": str(error)}
            asset_id = hashlib.sha256(data).hexdigest()
            page = page_of(row["page_url"])
            return {
                "raw_id": raw_id,
                "asset_id": asset_id,
                "data": data,
                "extension": extension,
                "size": size,
                "final_url": final_url,
                "page": page_summary(page, row["image_url"]) if page else None,
            }

        done = len(state["rows"]) - len(todo)
        seen = {r["asset_id"]: r["line"] for r in state["rows"] if r.get("status") == "ok"}
        with ThreadPoolExecutor(
            max_workers=FETCH_WORKERS, thread_name_prefix="import-fetch"
        ) as pool:
            for row, result in zip(todo, pool.map(one, todo), strict=True):
                with self.lock:
                    if result.get("status") == "failed":
                        row.update(status="failed", reason=result["reason"])
                    elif result["asset_id"] in known or result["raw_id"] in known:
                        row.update(
                            status="duplicate",
                            reason="ảnh đã có trong kho",
                            asset_id=result["asset_id"],
                        )
                    elif result["asset_id"] in seen:
                        row.update(
                            status="duplicate",
                            reason=f"trùng ảnh dòng {seen[result['asset_id']]}",
                            asset_id=result["asset_id"],
                            dup_of=result["asset_id"],
                        )
                    else:
                        file_name = f"{result['asset_id']}.{result['extension']}"
                        path = folder / file_name
                        path.write_bytes(result["data"])
                        domain = urlsplit(row["page_url"] or row["image_url"]).hostname or ""
                        self._write_item(
                            state,
                            {
                                "asset_id": result["asset_id"],
                                "file": file_name,
                                "width": result["size"][0],
                                "height": result["size"][1],
                                "image_url": row["image_url"],
                                "page_url": row["page_url"] or None,
                                "page_title": row["page_title"],
                                "domain": domain.lower(),
                                "page": result["page"],
                                "ocr": None,
                            },
                        )
                        row.update(status="ok", asset_id=result["asset_id"], file=file_name)
                        seen[result["asset_id"]] = row["line"]
                        self.staged[result["asset_id"]] = path
                    done += 1
                    state["progress"] = {"done": done, "total": len(state["rows"])}
                    if done % 20 == 0:
                        self._save(state)
        self._set(state)

    # step 3: OCR -------------------------------------------------------------------------

    def _ocr_all(self, state: dict) -> None:
        todo = []
        for row in state["rows"]:
            if row.get("status") == "ok" and self._item(state, row["asset_id"]).get("ocr") is None:
                todo.append(row)
        if not todo:
            return
        self._set(state, step="ocr", progress={"done": 0, "total": len(todo)})
        for n, row in enumerate(todo, 1):
            item = self._item(state, row["asset_id"])
            try:
                item["ocr"] = self._ocr_file(self.root / state["id"] / "images" / item["file"])
            except Exception as error:  # OCR is extra evidence; the image stays
                logger.warning(
                    "import_ocr_failed", asset_id=row["asset_id"], error=str(error)[:200]
                )
                item["ocr"] = []
            self._write_item(state, item)
            if n % 10 == 0 or n == len(todo):
                self._set(state, progress={"done": n, "total": len(todo)})

    def _ocr_file(self, path: Path) -> list[dict]:
        """scripts/backfill_ocr.ocr_file: longest side 960 px, no angle classifier."""
        from PIL import Image, ImageOps

        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR

            self._ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        image.thumbnail((OCR_MAX_SIDE, OCR_MAX_SIDE))
        width, height = image.size
        result, _ = self._ocr(np.asarray(image)[:, :, ::-1], use_cls=False)
        boxes = []
        for quad, text, score in result or []:
            xs, ys = [p[0] for p in quad], [p[1] for p in quad]
            boxes.append(
                {
                    "box": [
                        round(min(xs) / width, 3),
                        round(min(ys) / height, 3),
                        round((max(xs) - min(xs)) / width, 3),
                        round((max(ys) - min(ys)) / height, 3),
                    ],
                    "text": str(text)[:OCR_MAX_TEXT],
                    "score": round(float(score), 2),
                }
            )
        return boxes

    # step 4: match -----------------------------------------------------------------------

    def _matcher(self, service):
        from visualseek_retrieval.csv_import import EntityMatcher

        index = service.admin_index
        names = {e: index.name(e) for e in index.entities}
        strings = {
            e: [
                *(
                    index.graph_entities[m]["name"]
                    for m in index.members(e)
                    if m in index.graph_entities
                ),
                *index.aliases(e),
            ]
            for e in index.entities
        }
        return EntityMatcher(names, strings, service.pipeline.review.canonical)

    def _match(self, state: dict, service) -> None:
        todo = [g for g in state["groups"] if g.get("match") is None]
        if not todo:
            return
        self._set(state, step="match", progress={"done": 0, "total": len(todo)})
        matcher = self._matcher(service)
        for group in todo:
            match = matcher.match(group["name"], group["csv_entity_id"]).as_dict()
            with self.lock:
                group["match"] = match
                if match["kind"] == "existing":
                    group["decision"] = {
                        "type": "existing",
                        "entity_id": match["entity_id"],
                        "at": _now(),
                        "auto": True,
                    }
                    group["target"] = match["entity_id"]
                elif match["kind"] == "new":
                    group["decision"] = {
                        "type": "new",
                        "entity_id": None,
                        "at": _now(),
                        "auto": True,
                    }
        self._set(state, progress={"done": len(todo), "total": len(todo)})

    # step 2b: near duplicates --------------------------------------------------------------

    def _near_module(self):
        if self._near is None:
            self._near = _load_script("find_near_duplicates")
        return self._near

    def _feature(self, key: str, path: Path | None):
        """(phash, 16x16 thumb int16, width, height, 64x64 fine int16) or None."""
        if key in self._features:
            return self._features[key]
        near = self._near_module()
        value = None
        if path is not None and path.exists():
            try:
                from PIL import Image

                with Image.open(path) as probe:
                    width, height = probe.size
                image = near.load_rgb(path)
                thumb = np.asarray(
                    image.resize((near.THUMB, near.THUMB), Image.LANCZOS), dtype=np.int16
                )
                fine = np.asarray(
                    image.resize((near.FINE, near.FINE), Image.LANCZOS), dtype=np.int16
                )
                value = (near.phash(image), thumb, width, height, fine)
            except Exception:
                value = None
        self._features[key] = value
        return value

    def _same_picture(self, a, b) -> bool:
        if a is None or b is None:
            return False
        if bin(a[0] ^ b[0]).count("1") > NEAR_MAX_HAMMING:
            return False
        aspect = abs(np.log((a[2] / a[3]) / (b[2] / b[3])))
        if aspect > NEAR_MAX_ASPECT:
            return False
        if float(np.abs(a[1] - b[1]).mean()) > NEAR_MAX_MAE:
            return False
        return float(np.percentile(np.abs(a[4] - b[4]).max(axis=2), 99)) <= NEAR_MAX_P99

    def _near_duplicates(self, state: dict, service) -> None:
        """Within a decided group: drop an image that is the same picture (another size or
        quality) as an image the target entity already has, or as an earlier image of the group."""
        index = service.admin_index
        for group in state["groups"]:
            decision = group.get("decision")
            if decision is None:
                continue
            kept = self._kept(state, group)
            todo = [a for a in kept if a not in set(group.get("near_checked") or ())]
            if not todo:
                continue
            self._set(state, step="dedupe")
            references: list[tuple[str, object]] = []
            if decision["type"] == "existing" and group.get("target"):
                for asset_id in index.images.get(group["target"], ()):
                    references.append(
                        (asset_id, self._feature(asset_id, service.image_file(asset_id)))
                    )
            folder = self.root / state["id"] / "images"
            rows = {
                r["asset_id"]: r
                for r in state["rows"]
                if r["group"] == group["key"] and r.get("status") == "ok"
            }
            for asset_id in kept:
                feature = self._feature(asset_id, folder / rows[asset_id]["file"])
                if asset_id in todo:
                    same = next(
                        (ref for ref, f in references if self._same_picture(feature, f)), None
                    )
                    if same is not None:
                        with self.lock:
                            rows[asset_id].update(
                                status="duplicate", reason="gần trùng một ảnh đã có", dup_of=same
                            )
                        continue
                references.append((asset_id, feature))
            with self.lock:
                group["near_checked"] = sorted(set(group.get("near_checked") or ()) | set(todo))
            self._set(state)

    # step 4b / 6: publish images ---------------------------------------------------------------

    def _new_images(self, state: dict, group: dict) -> list:
        from PIL import Image

        from visualseek_api.application.admin import NewImage
        from visualseek_retrieval.doc_text import ocr_lines, page_lines

        published = set(group.get("published") or ())
        out = []
        for asset_id in self._kept(state, group):
            if asset_id in published:
                continue
            item = self._item(state, asset_id)
            data = (self.root / state["id"] / "images" / item["file"]).read_bytes()
            image = Image.open(io.BytesIO(data))
            image.load()
            source = {"page_title": item["page_title"], "page": item["page"]}
            context = {"pages": page_lines([source]), "ocr": ocr_lines(item["ocr"])}
            out.append(
                NewImage(
                    asset_id=asset_id,
                    data=data,
                    image=image,
                    file_name=item["file"],
                    context=context,
                    page_url=item["page_url"],
                    page_title=item["page_title"] or (item.get("page") or {}).get("title") or "",
                    domain=item["domain"] or None,
                    origin=f"csv:{state['id']}",
                )
            )
        return out

    def _publish(self, state: dict, group: dict, service) -> None:
        uploads = self.admin.store.uploads()
        published = set(group.get("published") or ())
        # Upserted by a run that stopped before it saved the import state.
        already = {a for a in self._kept(state, group) if a not in published and a in uploads}
        if already:
            with self.lock:
                group["published"] = sorted(published | already)
            self._set(state)
        entries = self._new_images(state, group)
        if not entries:
            return
        self._set(state, step="publish")
        actor = (group.get("decision") or {}).get("actor") or state.get("actor") or {}
        for start in range(0, len(entries), 16):
            chunk = entries[start : start + 16]
            self.admin.add_images(service, group["target"], chunk, actor)
            with self.lock:
                group["published"] = sorted(
                    set(group.get("published") or ()) | {e.asset_id for e in chunk}
                )
            self._set(state)
        logger.info(
            "import_published",
            import_id=state["id"],
            entity_id=group["target"],
            images=len(entries),
        )

    def _publish_existing(self, state: dict, service) -> None:
        for group in state["groups"]:
            if (group.get("decision") or {}).get("type") == "existing" and group.get("target"):
                self._publish(state, group, service)

    def _publish_released(self, state: dict, service) -> None:
        for group in state["groups"]:
            decision = group.get("decision") or {}
            if (
                decision.get("type") == "new"
                and group.get("registered")
                and group.get("target") in service.admin_index.graph_entities
            ):
                self._publish(state, group, service)

    # step 5: new entities ----------------------------------------------------------------

    def _label_new(self, state: dict, service) -> None:
        from visualseek_retrieval.csv_import import evidence_pack

        index = service.admin_index
        claims = self._claim_new(state, service)
        if not claims:
            return
        self._set(state, step="label", progress={"done": 0, "total": len(claims)})
        packs = {}
        for group, _, entity_id in claims:
            items = [self._item(state, a) for a in self._kept(state, group)]
            packs[entity_id] = evidence_pack(entity_id, group["name"], items)
        unavailable = self.labeller.available()
        done = 0
        for start in range(0, len(claims), LABEL_BATCH):
            chunk = claims[start : start + LABEL_BATCH]
            chunk_packs = [packs[entity_id] for _, _, entity_id in chunk]
            labels, enrich, error = {}, {}, unavailable
            if not unavailable:
                try:
                    labels = self.labeller.classify(chunk_packs)
                    enrich = self.labeller.enrich(chunk_packs, labels, index.category_names)
                except Exception as exc:  # the entity is still registered, without a label
                    logger.warning("import_label_failed", error=str(exc)[:300])
                    error = str(exc)[:300]
            for group, decision, entity_id in chunk:
                label, extra = labels.get(entity_id), enrich.get(entity_id)
                if label is None and not error:
                    error = "LLM không trả nhãn cho entity này"
                done += 1
                with self.lock:  # decide() takes it too
                    if group.get("decision") is not decision:
                        continue  # decided again while the labeller ran; that queued a new run
                    self._register(state, group, packs[entity_id], label, extra)
                    group["label"], group["enrich"] = label, extra
                    group["registered"] = True
                    group["error"] = None if label else error
            self._set(state, progress={"done": done, "total": len(claims)})

    def _claim_new(self, state: dict, service) -> list[tuple[dict, dict, str]]:
        """Give every unregistered new group its entity id, and return (group, decision, id) for
        the ones to label. A product that already waits for a release (the same dataset id, else
        the same name) keeps its id, and its label when it has one."""
        from visualseek_retrieval.csv_import import new_entity_id
        from visualseek_retrieval.text import normalize

        claims = []
        with self.lock:
            todo = [
                g
                for g in state["groups"]
                if (g.get("decision") or {}).get("type") == "new"
                and not g.get("registered")
                and self._kept(state, g)
            ]
            if not todo:
                return []
            waiting = self._waiting_entities(service)
            by_name = {normalize(row["name"]): entity_id for entity_id, row in waiting.items()}
            taken = {*service.admin_index.graph_entities, *waiting}
            for s in self.states.values():
                taken.update(g["target"] for g in s["groups"] if g.get("registered"))
            for group in todo:
                if not group.get("target"):
                    known = group["csv_entity_id"] if group["csv_entity_id"] in waiting else None
                    group["target"] = (
                        known
                        or by_name.get(normalize(group["name"]))
                        or new_entity_id(group["csv_entity_id"], group["name"], taken)
                    )
                taken.add(group["target"])
                row = waiting.get(group["target"])
                if row and row.get("label"):
                    group.update(label=row["label"], enrich=row.get("enrich"))
                    group.update(registered=True, error=None)
                else:
                    claims.append((group, group["decision"], group["target"]))
            self._save(state)
        return claims

    def _register(self, state: dict, group: dict, pack: dict, label, enrich) -> None:
        actor = (group.get("decision") or {}).get("actor") or state.get("actor") or {}
        row = {
            "at": _now(),
            "actor": actor,
            "import_id": state["id"],
            "entity_id": group["target"],
            "name": group["name"],
            "op": "add",
            "pack": pack,
            "label": label,
            "enrich": enrich,
        }
        self._append_new_entity(row)

    def _drop_new_entity(self, state: dict, group: dict, actor: dict) -> None:
        self._append_new_entity(
            {
                "at": _now(),
                "actor": actor,
                "import_id": state["id"],
                "entity_id": group["target"],
                "name": group["name"],
                "op": "drop",
            }
        )

    def _append_new_entity(self, row: dict) -> None:
        from visualseek_retrieval.admin_overlay import NEW_ENTITIES_FILE

        path = Path(self.settings.admin_dir) / NEW_ENTITIES_FILE
        with self.admin.store.lock:
            row = {**row, "actor": self.admin.store.actors.public(row.get("actor"))}
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _normalise_image(body: bytes) -> tuple[bytes, str, tuple[int, int]]:
    """(bytes to store, extension, size). JPEG/PNG/WebP are kept as downloaded; other formats
    (GIF first frame, BMP, TIFF ...) become PNG."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(body)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(body))
        image.load()
    except Image.DecompressionBombError as error:  # not an OSError: it would end the import
        raise ValueError("ảnh quá lớn") from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise ValueError("không đọc được ảnh") from error
    if min(image.size) < MIN_IMAGE_SIDE:
        raise ValueError("ảnh quá nhỏ")
    extension = KEEP_FORMATS.get(image.format or "")
    if extension:
        return body, extension, image.size
    image.seek(0)
    frame = ImageOps.exif_transpose(image).convert("RGBA" if "A" in image.getbands() else "RGB")
    out = io.BytesIO()
    frame.save(out, format="PNG", optimize=True)
    return out.getvalue(), "png", frame.size
