"""Admin: inspect entities, edit the graph as a change log, hide and upload images, release.

What takes effect when:
  * graph edits (brand, label, attributes, relations) are appended to graph/admin/changes.jsonl
    and stay pending until a release (scripts/admin_release.py) rebuilds the artifacts; the
    search keeps serving the released graph meanwhile;
  * hiding an image is immediate: its asset joins the identity review's blocked assets, which
    every Qdrant query and the visual re-ranking already exclude;
  * an uploaded image is embedded (bge-m3 doc text + SigLIP2 image) and upserted at once.

Files: <admin_dir>/changes.jsonl, images.jsonl, releases.jsonl (small, in git) and
<admin_data_dir>/uploads.jsonl, uploads/, vectors/, release-logs/ (data).
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import structlog
from fastapi import status

from visualseek_api.core.auth import AuthUser
from visualseek_api.core.config import Settings
from visualseek_api.core.errors import ApplicationError

logger = structlog.get_logger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[5]
RELEASE_SCRIPT = REPO_ROOT / "scripts/admin_release.py"
MAX_TEXT = 80
MAX_LIST_ITEMS = 12
MAX_CAPTION = 300
MAX_BRAND_NAME = 60
UPLOAD_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
MIN_IMAGE_SIDE = 32
UPLOAD_PREFIX = "admin-uploads"
UPLOAD_DOMAIN = "admin-upload"
HISTORY_LIMIT = 50
LOG_TAIL = 60


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _bad(message: str, code: str = "invalid_admin_request") -> ApplicationError:
    return ApplicationError(message, code=code)


def _not_found(message: str) -> ApplicationError:
    return ApplicationError(message, code="not_found", status_code=status.HTTP_404_NOT_FOUND)


def _conflict(message: str, code: str) -> ApplicationError:
    return ApplicationError(message, code=code, status_code=status.HTTP_409_CONFLICT)


def _actor(user: AuthUser) -> dict:
    return {"id": user.id, "email": user.email}


class AdminStore:
    """Append-only JSONL files; one lock serialises the writers of this process."""

    def __init__(self, admin_dir: Path, data_dir: Path) -> None:
        from visualseek_retrieval.admin_overlay import ActorDirectory

        self.actors = ActorDirectory(data_dir)
        self.admin_dir = admin_dir
        self.data_dir = data_dir
        self.changes_path = admin_dir / "changes.jsonl"
        self.images_path = admin_dir / "images.jsonl"
        self.releases_path = admin_dir / "releases.jsonl"
        self.uploads_path = data_dir / "uploads.jsonl"
        self.uploads_dir = data_dir / "uploads"
        self.vectors_dir = data_dir / "vectors"
        self.trash_dir = data_dir / "trash"
        self.logs_dir = data_dir / "release-logs"
        # Reentrant: a writer reads the file under the same lock (no torn last line).
        self.lock = threading.RLock()

    def _append(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _read(self, path: Path) -> list[dict]:
        with self.lock:
            return _read_jsonl(path)

    def changes(self) -> list[dict]:
        return self._read(self.changes_path)

    def append_changes(self, rows: list[dict]) -> list[dict]:
        with self.lock:
            seq = max((row["seq"] for row in self.changes()), default=0)
            stamped = [
                {"seq": seq + i + 1, **row, "actor": self.actors.public(row.get("actor"))}
                for i, row in enumerate(rows)
            ]
            self._append(self.changes_path, stamped)
        return stamped

    def releases(self) -> list[dict]:
        return self._read(self.releases_path)

    def image_events(self) -> list[dict]:
        return self._read(self.images_path)

    def hidden(self) -> dict[str, dict]:
        """asset id -> the event that hid it, for images hidden now."""
        latest: dict[str, dict] = {}
        for row in self.image_events():
            latest[row["asset_id"]] = row
        return {asset: row for asset, row in latest.items() if row["hidden"]}

    def append_image_event(self, row: dict) -> None:
        with self.lock:
            row = {**row, "actor": self.actors.public(row.get("actor"))}
            self._append(self.images_path, [row])

    def named(self, row: dict) -> dict:
        """A log row for the admin pages, with the e-mail behind its actor alias."""
        alias = (row.get("actor") or {}).get("id")
        return {**row, "actor": {"id": alias, "email": self.actors.name(alias)}}

    def uploads(self) -> dict[str, dict]:
        """asset id -> latest record of every uploaded image that is not deleted."""
        latest: dict[str, dict] = {}
        for row in self._read(self.uploads_path):
            latest[row["asset_id"]] = row
        return {asset: row for asset, row in latest.items() if row["op"] != "delete"}

    def append_upload(self, row: dict) -> None:
        with self.lock:
            self._append(self.uploads_path, [row])


@dataclass
class NewImage:
    """An image to add to an existing entity: the upload form or a CSV import row."""

    asset_id: str
    data: bytes
    image: object  # decoded PIL image
    file_name: str
    caption: str = ""
    # CSV import: {"pages": [...], "ocr": [...]}, the doc text lines (doc_text.context_sections)
    context: dict | None = None
    page_url: str | None = None
    page_title: str = ""
    domain: str | None = None
    origin: str = ""  # "csv:<import id>" for imported images


@dataclass
class ReleaseJob:
    status: str = "idle"  # idle | running | succeeded | failed
    started_at: str | None = None
    finished_at: str | None = None
    actor: str | None = None  # shown while running; releases.jsonl gets actor_alias
    actor_alias: str | None = None
    log_path: Path | None = None
    error: str | None = None


class AdminService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = AdminStore(Path(settings.admin_dir), Path(settings.admin_data_dir))
        self.job = ReleaseJob()
        self._job_lock = threading.Lock()
        # Set by the application: swaps app.state.search after a release.
        self.on_reload = None
        from visualseek_api.application.imports import ImportService

        self.imports = ImportService(self, settings)

    # ---------------------------------------------------------------- service preparation

    def prepare(self, service) -> None:
        """Lay uploads and hidden images over a freshly loaded search service."""
        index = EntityIndex(service)
        service.admin_index = index
        uploads = self.store.uploads()
        if uploads:
            service.executor.submit(self._attach_uploads, service, uploads).result()
        self._apply_hidden(service)

    def _attach_uploads(self, service, uploads: dict[str, dict]) -> None:
        """Runs on the search thread: fusion rows and payloads are read there."""
        ids, docs, imgs = [], [], []
        for asset_id, row in sorted(uploads.items()):
            path = self.store.uploads_dir / row["file"]
            vectors = self.store.vectors_dir / f"{asset_id}.npz"
            if not (path.exists() and vectors.exists()):
                logger.warning("upload_files_missing", asset_id=asset_id)
                continue
            try:
                row = self._refresh_upload(service, row, vectors)
            except Exception as error:  # Qdrant down: serve the stored vectors, retry next load
                logger.warning("upload_refresh_failed", asset_id=asset_id, error=str(error)[:200])
            saved = np.load(vectors)
            ids.append(asset_id)
            docs.append(saved["doc"])
            imgs.append(saved["img"])
            self._register(service, row, path)
        if ids and service.pipeline.fusion is not None:
            service.pipeline.fusion.extend(ids, np.stack(docs), np.stack(imgs))

    def _register(self, service, row: dict, path: Path) -> None:
        asset_id = row["asset_id"]
        service.upload_paths[asset_id] = path
        service.image_paths[asset_id] = row["payload"]["image_path"]
        service.asset_entities[asset_id] = row["payload"]["entity_ids"]
        service.details[asset_id] = {
            "description": row["doc_text"],
            "page_url": row["payload"].get("page_url"),
        }
        service.pipeline.payloads[asset_id] = {
            k: row["payload"].get(k)
            for k in (
                "asset_id",
                "entity_ids",
                "brand_ids",
                "image_path",
                "page_title",
                "domain",
                "categories",
                "market_tiers",
            )
        }
        service.admin_index.add_image(row["entity_id"], asset_id)

    def _refresh_upload(self, service, row: dict, vectors: Path) -> dict:
        """Re-embed or re-label an upload whose entity changed in a release."""
        text = self._doc_text(
            service, row["entity_id"], row.get("caption") or "", row.get("context")
        )
        payload = self._payload(service, row["entity_id"], row["file"], row)
        if text == row["doc_text"] and payload == row["payload"]:
            return row
        from qdrant_client import models

        from visualseek_retrieval import lexical
        from visualseek_retrieval.qdrant_store import DENSE, SPARSE, point_id

        client, collection = service.pipeline.client, service.pipeline.collection
        if text != row["doc_text"]:
            doc = self._embed_doc(service, text)
            sparse = lexical.document_vector(text, service.avg_doc_tokens)
            client.update_vectors(
                collection,
                points=[
                    models.PointVectors(
                        id=point_id(row["asset_id"]),
                        vector={
                            DENSE: doc.tolist(),
                            SPARSE: models.SparseVector(
                                indices=sparse.indices, values=sparse.values
                            ),
                        },
                    )
                ],
                wait=True,
            )
            img = np.load(vectors)["img"]
            np.savez(vectors, doc=doc, img=img)
        client.set_payload(
            collection, payload=payload, points=[point_id(row["asset_id"])], wait=True
        )
        updated = {**row, "op": "update", "doc_text": text, "payload": payload, "at": _now()}
        self.store.append_upload(updated)
        logger.info("upload_refreshed", asset_id=row["asset_id"])
        return updated

    def _apply_hidden(self, service) -> None:
        blocked = set(service.base_blocked) | set(self.store.hidden())
        # Uploads deleted since the last load are still fusion rows until the next reload.
        blocked |= getattr(service, "deleted_uploads", set())
        # One assignment: the search thread reads either the old or the new list.
        service.pipeline.review.blocked_assets = sorted(blocked)

    # ---------------------------------------------------------------- reading

    def overview(self, service) -> dict:
        index: EntityIndex = service.admin_index
        state, released = self._states()
        pending = _pending(state, released, index)
        releases = self.store.releases()
        return {
            "entities": len(index.entities),
            "with_brand": sum(bool(index.brand_of(e)) for e in index.entities),
            "without_category": sum(e not in index.labels for e in index.entities),
            "needs_review": sum(
                bool(index.labels.get(e, {}).get("needs_review")) for e in index.entities
            ),
            "images": len(service.image_paths),
            "hidden_images": len(self.store.hidden()),
            "uploaded_images": len(self.store.uploads()),
            "pending_entities": len(pending),
            "pending_fields": sum(len(f) for f in pending.values()),
            "imported_entities": sum(
                bool(index.labels.get(e, {}).get("imported")) for e in index.entities
            ),
            "pending_new_entities": len(self.imports.pending_new_entities(service)),
            "last_release": self._release_view(releases[-1]) if releases else None,
            "graph_fingerprint": service.info["graph_fingerprint"],
        }

    def list_entities(self, service, q: str, flt: str, offset: int, limit: int) -> dict:
        from visualseek_retrieval.text import normalize

        index: EntityIndex = service.admin_index
        state, released = self._states()
        pending = _pending(state, released, index)
        hidden = self.store.hidden()
        uploads = self.store.uploads()
        needle = normalize(q) if q else ""
        items = []
        for entity_id in index.order:
            if needle and needle not in index.search_text[entity_id] and q.strip() != entity_id:
                continue
            label = index.labels.get(entity_id) or {}
            images = index.images.get(entity_id, [])
            row = {
                "id": entity_id,
                "name": index.name(entity_id),
                "brand": index.brand_name(index.brand_of(entity_id)),
                "category": label.get("category_name"),
                "images": len(images),
                "hidden_images": sum(a in hidden for a in images),
                "uploaded_images": sum(a in uploads for a in images),
                "needs_review": bool(label.get("needs_review")),
                "imported": bool(label.get("imported")),
                "pending": sorted(pending.get(entity_id, {})),
                "thumb": next(
                    (a for a in images if a not in hidden), images[0] if images else None
                ),
            }
            if _matches(flt, row, label, entity_id in state):
                items.append(row)
        return {"total": len(items), "items": items[offset : offset + limit]}

    def entity(self, service, entity_id: str) -> dict:
        index: EntityIndex = service.admin_index
        entity_id = service.pipeline.review.canonical(entity_id)
        if entity_id not in index.entities:
            raise _not_found("Unknown entity.")
        state, released = self._states()
        live = index.live_fields(entity_id)
        draft = {**live, **state.get(entity_id, {})}
        pending = _pending(state, released, index).get(entity_id, {})
        hidden = self.store.hidden()
        uploads = self.store.uploads()
        label = index.labels.get(entity_id) or {}
        history = [row for row in self.store.changes() if row["entity_id"] == entity_id]
        images = index.images.get(entity_id, [])
        image_history = [row for row in self.store.image_events() if row["asset_id"] in set(images)]
        view = service.graph_view
        edges = [
            {
                "rel": rel,
                "target": dst,
                "label": view.values.get(dst, ("", dst))[1],
                "type": view.values.get(dst, ("", ""))[0],
                "source": meta.get("source"),
                "confidence": meta.get("confidence"),
                "reviewed": meta.get("reviewed"),
                "shared_with": len(view.by_value.get(dst, ())) - 1,
            }
            for (rel, dst), meta in sorted(view.out.get(entity_id, {}).items())
        ]
        return {
            "id": entity_id,
            "name": index.name(entity_id),
            "members": [
                {"id": m, "name": index.graph_entities[m]["name"]} for m in index.members(entity_id)
            ],
            "aliases": index.aliases(entity_id),
            "label": {
                key: label.get(key)
                for key in (
                    "category",
                    "category_name",
                    "category_l1",
                    "product_type",
                    "market_tier",
                    "source",
                    "model",
                    "basis",
                    "confidence",
                    "needs_review",
                )
            },
            "live": _display(index, live),
            "draft": _display(index, draft),
            "pending": sorted(pending),
            "edges": edges,
            "images": [
                {
                    "asset_id": asset_id,
                    "hidden": asset_id in hidden,
                    "hidden_reason": (hidden.get(asset_id) or {}).get("reason"),
                    "uploaded": asset_id in uploads,
                    "description": (service.details.get(asset_id) or {}).get("description"),
                    "page_url": (service.details.get(asset_id) or {}).get("page_url"),
                }
                for asset_id in images
            ],
            "history": [
                self.store.named(r)
                for r in sorted(history, key=lambda r: -r["seq"])[:HISTORY_LIMIT]
            ],
            "image_history": [self.store.named(r) for r in image_history[-HISTORY_LIMIT:][::-1]],
        }

    def vocab(self, service, kind: str, q: str, limit: int) -> list[dict]:
        from visualseek_retrieval.text import normalize

        index: EntityIndex = service.admin_index
        view = service.graph_view
        needle = normalize(q) if q else ""
        if kind == "brand":
            pool = [
                (b["id"], b["name"], len(index.brand_entities.get(b["id"], ())))
                for b in index.brands.values()
            ]
        elif kind == "category":
            pool = [
                (cid, f"{name} · {l1}", len(view.by_value.get(f"category:{cid}", ())))
                for cid, (name, l1) in index.category_names.items()
            ]
        elif kind in {"purpose", "ingredient", "model", "place"}:
            pool = [
                (vid, name, len(view.by_value.get(vid, ())))
                for vid, (vtype, name) in view.values.items()
                if vtype == kind
            ]
        else:
            raise _bad(f"Unknown vocabulary {kind!r}.")
        hits = [
            {"id": vid, "label": name, "count": count}
            for vid, name, count in pool
            if not needle or needle in normalize(name) or needle in normalize(vid)
        ]
        hits.sort(key=lambda h: (-h["count"], h["label"].casefold()))
        return hits[:limit]

    def preview(self, service, entity_id: str, changes: dict, siblings: int) -> dict:
        """The entity's relations as the next release would build them: the saved drafts plus
        the unsaved ``changes`` of the edit form. Nothing is written."""
        from visualseek_api.application.preview import preview_relations
        from visualseek_retrieval.admin_overlay import FIELDS

        index: EntityIndex = service.admin_index
        entity_id = service.pipeline.review.canonical(entity_id)
        if entity_id not in index.entities:
            raise _not_found("Unknown entity.")
        unknown = sorted(set(changes) - set(FIELDS))
        if unknown:
            raise _bad(f"Unknown fields: {', '.join(unknown)}.")
        state, _ = self._states()
        fields = {
            **state.get(entity_id, {}),
            **{name: _validate(name, value, index) for name, value in changes.items()},
        }
        return preview_relations(
            _preview_module(),
            service.graph_view,
            index,
            service.categories or {},
            entity_id,
            fields,
            siblings,
        )

    # ---------------------------------------------------------------- graph edits

    def edit(self, service, entity_id: str, changes: dict, note: str, user: AuthUser) -> dict:
        from visualseek_retrieval.admin_overlay import FIELDS

        index: EntityIndex = service.admin_index
        entity_id = service.pipeline.review.canonical(entity_id)
        if entity_id not in index.entities:
            raise _not_found("Unknown entity.")
        unknown = sorted(set(changes) - set(FIELDS))
        if unknown:
            raise _bad(f"Unknown fields: {', '.join(unknown)}.")
        state, _ = self._states()
        draft = {**index.live_fields(entity_id), **state.get(entity_id, {})}
        clean = {name: _validate(name, value, index) for name, value in changes.items()}
        needs_category = any(n != "brand" for n in clean) and not (
            clean.get("category") or draft.get("category")
        )
        if needs_category:
            raise _bad("Choose a category for this product first.", code="category_required")
        rows = [
            {
                "at": _now(),
                "actor": _actor(user),
                "entity_id": entity_id,
                "field": name,
                "before": draft.get(name),
                "after": value,
                "note": note[:200],
            }
            for name, value in clean.items()
            if value != draft.get(name)
        ]
        if rows:
            self.store.append_changes(rows)
            logger.info("admin_edit", entity_id=entity_id, fields=[r["field"] for r in rows])
        return self.entity(service, entity_id)

    def discard(self, service, entity_id: str, fields: list[str], user: AuthUser) -> dict:
        """Set pending fields back to their released value (a new row; history is kept)."""
        index: EntityIndex = service.admin_index
        entity_id = service.pipeline.review.canonical(entity_id)
        if entity_id not in index.entities:
            raise _not_found("Unknown entity.")
        state, released = self._states()
        pending = _pending(state, released, index).get(entity_id, {})
        live = index.live_fields(entity_id)
        base = released.get(entity_id, {})
        rows = [
            {
                "at": _now(),
                "actor": _actor(user),
                "entity_id": entity_id,
                "field": name,
                "before": pending[name],
                "after": base.get(name, live.get(name)),
                "note": "discard",
            }
            for name in fields
            if name in pending
        ]
        if rows:
            self.store.append_changes(rows)
        return self.entity(service, entity_id)

    def _states(self) -> tuple[dict, dict]:
        from visualseek_retrieval.admin_overlay import fold, last_release

        return fold(self.store.changes()), last_release(self.store.releases())["state"]

    # ---------------------------------------------------------------- images

    def set_hidden(self, service, asset_id: str, hidden: bool, reason: str, user: AuthUser) -> dict:
        if asset_id not in service.image_paths:
            raise _not_found("Unknown image.")
        currently = asset_id in self.store.hidden()
        if currently != hidden:
            self.store.append_image_event(
                {
                    "at": _now(),
                    "actor": _actor(user),
                    "asset_id": asset_id,
                    "hidden": hidden,
                    "reason": reason[:200],
                }
            )
            self._apply_hidden(service)
            logger.info("admin_image_hidden" if hidden else "admin_image_shown", asset_id=asset_id)
        return {"asset_id": asset_id, "hidden": hidden}

    def upload(self, service, entity_id: str, data: bytes, caption: str, user: AuthUser) -> dict:
        from PIL import Image, UnidentifiedImageError

        index: EntityIndex = service.admin_index
        entity_id = service.pipeline.review.canonical(entity_id)
        if entity_id not in index.entities:
            raise _not_found("Unknown entity.")
        pipeline = service.pipeline
        if pipeline.image_encoder is None or pipeline.fusion is None:
            raise ApplicationError(
                "Image vectors are not loaded; uploads need the SigLIP2 artifacts.",
                code="uploads_unavailable",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if len(data) > self.settings.admin_upload_max_bytes:
            raise _bad("The image is too large.", code="image_too_large")
        try:
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError, SyntaxError) as error:
            raise _bad("The file is not a readable image.", code="invalid_image") from error
        extension = UPLOAD_FORMATS.get(image.format or "")
        if extension is None:
            raise _bad("Use a JPEG, PNG or WebP image.", code="invalid_image")
        if min(image.size) < MIN_IMAGE_SIDE:
            raise _bad("The image is too small.", code="invalid_image")
        asset_id = hashlib.sha256(data).hexdigest()
        if asset_id in service.image_paths or asset_id in self.store.uploads():
            raise _conflict("This image is already in the collection.", "duplicate_image")
        caption = " ".join(caption.split())[:MAX_CAPTION]
        entry = NewImage(asset_id, data, image, f"{asset_id}.{extension}", caption=caption)
        self.add_images(service, entity_id, [entry], _actor(user))
        logger.info("admin_image_uploaded", asset_id=asset_id, entity_id=entity_id)
        return {"asset_id": asset_id, "entity_id": entity_id}

    def add_images(
        self, service, entity_id: str, entries: list[NewImage], actor: dict, batch: int = 16
    ) -> list[dict]:
        """Embed (bge-m3 doc text + SigLIP2 image) and upsert images of an existing entity, in
        batches on the search thread; returns the upload rows. Used by the upload form and by
        the CSV import."""
        pipeline = service.pipeline
        if pipeline.image_encoder is None or pipeline.fusion is None:
            raise ApplicationError(
                "Image vectors are not loaded; uploads need the SigLIP2 artifacts.",
                code="uploads_unavailable",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        rows: list[dict] = []
        for start in range(0, len(entries), batch):
            chunk = entries[start : start + batch]
            prepared = [
                (
                    entry,
                    self._doc_text(service, entity_id, entry.caption, entry.context),
                    self._payload(service, entity_id, entry.file_name, entry),
                )
                for entry in chunk
            ]
            rows += service.executor.submit(
                self._embed_and_store, service, entity_id, prepared, actor
            ).result()
        return rows

    def _embed_and_store(self, service, entity_id: str, prepared: list, actor: dict) -> list[dict]:
        """On the search thread: GPU work is serialised there and fusion rows are read there."""
        from qdrant_client import models

        from visualseek_retrieval import lexical
        from visualseek_retrieval.qdrant_store import DENSE, IMAGE, SPARSE, point_id

        pipeline = service.pipeline
        texts = [text for _, text, _ in prepared]
        docs = self._embed_docs(service, texts)
        imgs = pipeline.image_encoder.encode_images([entry.image for entry, _, _ in prepared])
        points = []
        for (entry, text, payload), doc, img in zip(prepared, docs, imgs, strict=True):
            sparse = lexical.document_vector(text, service.avg_doc_tokens)
            points.append(
                models.PointStruct(
                    id=point_id(entry.asset_id),
                    vector={
                        DENSE: doc.tolist(),
                        SPARSE: models.SparseVector(indices=sparse.indices, values=sparse.values),
                        IMAGE: img.astype(np.float16).astype(np.float32).tolist(),
                    },
                    payload=payload,
                )
            )
        pipeline.client.upsert(pipeline.collection, points=points, wait=True)
        self.store.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.store.vectors_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for (entry, text, payload), doc, img in zip(prepared, docs, imgs, strict=True):
            path = self.store.uploads_dir / entry.file_name
            path.write_bytes(entry.data)
            # float16 like img_siglip2.npy, so a reload scores it exactly as now.
            img16 = img.astype(np.float16)
            np.savez(self.store.vectors_dir / f"{entry.asset_id}.npz", doc=doc, img=img16)
            row = {
                "op": "add",
                "at": _now(),
                "actor": actor,
                "asset_id": entry.asset_id,
                "entity_id": entity_id,
                "file": entry.file_name,
                "caption": entry.caption,
                "doc_text": text,
                "payload": payload,
            }
            if entry.context is not None:
                row["context"] = entry.context
            if entry.origin:
                row["origin"] = entry.origin
            self.store.append_upload(row)
            pipeline.fusion.extend([entry.asset_id], doc[None, :], img16[None, :])
            self._register(service, row, path)
            rows.append(row)
        return rows

    def delete_upload(self, service, asset_id: str, user: AuthUser) -> dict:
        upload = self.store.uploads().get(asset_id)
        if upload is None:
            raise _not_found("Only images uploaded in admin can be deleted; hide others.")
        from qdrant_client import models

        from visualseek_retrieval.qdrant_store import point_id

        pipeline = service.pipeline
        pipeline.client.delete(
            pipeline.collection,
            points_selector=models.PointIdsList(points=[point_id(asset_id)]),
            wait=True,
        )
        self.store.append_upload(
            {"op": "delete", "at": _now(), "actor": _actor(user), "asset_id": asset_id}
        )
        # The file goes to trash instead of being removed, so a mistake can be undone by hand.
        self.store.trash_dir.mkdir(parents=True, exist_ok=True)
        for source in (
            self.store.uploads_dir / upload["file"],
            self.store.vectors_dir / f"{asset_id}.npz",
        ):
            if source.exists():
                source.replace(self.store.trash_dir / source.name)

        def forget() -> None:
            pipeline.payloads.pop(asset_id, None)

        service.executor.submit(forget).result()
        service.upload_paths.pop(asset_id, None)
        service.image_paths.pop(asset_id, None)
        service.details.pop(asset_id, None)
        service.asset_entities.pop(asset_id, None)
        service.admin_index.remove_image(upload["entity_id"], asset_id)
        if not hasattr(service, "deleted_uploads"):
            service.deleted_uploads = set()
        service.deleted_uploads.add(asset_id)
        self._apply_hidden(service)
        logger.info("admin_upload_deleted", asset_id=asset_id)
        return {"asset_id": asset_id, "deleted": True}

    def _doc_text(self, service, entity_id: str, caption: str, context: dict | None = None) -> str:
        """Entity card + page lines + OCR, as build_doc_texts.compose; an image uploaded by hand
        has only its caption as page text."""
        from visualseek_retrieval.doc_text import context_sections, entity_card

        index: EntityIndex = service.admin_index
        brands = {b["id"]: b["name"] for b in index.brands.values()}
        card = entity_card(index.graph_entities[entity_id], brands)
        if context is not None:
            sections = context_sections(context.get("pages") or [], context.get("ocr") or [])
            return "\n".join([card, *sections])
        return f"{card}\nTrang: {caption}" if caption else card

    def _payload(self, service, entity_id: str, file_name: str, source) -> dict:
        """``source``: a NewImage, or an upload row (whose payload keeps the page fields)."""
        index: EntityIndex = service.admin_index
        label = index.labels.get(entity_id) or {}
        categories = service.categories or {}
        if isinstance(source, dict):
            old = source.get("payload") or {}
            page_url, domain = old.get("page_url"), old.get("domain")
            page_title = old.get("page_title") or source.get("caption") or ""
        else:
            page_url, domain = source.page_url, source.domain
            page_title = source.page_title or source.caption
        return {
            "asset_id": file_name.split(".")[0],
            "entity_ids": [entity_id],
            "brand_ids": [b] if (b := index.brand_of(entity_id)) else [],
            "image_path": f"{UPLOAD_PREFIX}/{file_name}",
            "domain": domain or UPLOAD_DOMAIN,
            "page_url": page_url,
            "page_title": page_title,
            "graph_fingerprint": service.info["graph_fingerprint"],
            "doc_texts_sha256": service.info["doc_texts_sha256"],
            "categories": [label["category"]] if label.get("category") else [],
            "category_l1s": [label["category_l1"]] if label.get("category_l1") else [],
            "market_tiers": [label["market_tier"]]
            if label.get("category_l1") == "fashion" and label.get("market_tier")
            else [],
            "taxonomy_sha256": categories.get("taxonomy_sha256"),
        }

    @staticmethod
    def _embed_docs(service, texts: list[str]) -> np.ndarray:
        """bge-m3 at the document length of build_doc_texts.py (the query encoder is capped
        shorter). Call on the search thread only: it changes the shared model's length."""
        from visualseek_retrieval.encoder import DOC_MAX_TOKENS

        model = service.pipeline.encoder.model
        query_length = model.max_seq_length
        model.max_seq_length = DOC_MAX_TOKENS
        try:
            return service.pipeline.encoder.encode(texts)
        finally:
            model.max_seq_length = query_length

    @classmethod
    def _embed_doc(cls, service, text: str) -> np.ndarray:
        return cls._embed_docs(service, [text])[0]

    # ---------------------------------------------------------------- release

    def release_status(self, service) -> dict:
        index: EntityIndex = service.admin_index
        state, released = self._states()
        pending = _pending(state, released, index)
        live = {e: index.live_fields(e) for e in pending if e in index.entities}
        items = [
            {
                "entity_id": entity_id,
                "name": index.name(entity_id) if entity_id in index.entities else entity_id,
                "field": name,
                "before": _display_value(
                    index,
                    name,
                    released.get(entity_id, {}).get(name, live.get(entity_id, {}).get(name)),
                ),
                "after": _display_value(index, name, value),
            }
            for entity_id, fields in sorted(pending.items())
            for name, value in sorted(fields.items())
        ]
        return {
            "pending": items,
            "new_entities": self.imports.pending_new_entities(service),
            "job": self._job_view(),
            "history": [self._release_view(r) for r in self.store.releases()[-20:][::-1]],
        }

    def _release_view(self, row: dict) -> dict:
        return {**_release_summary(row), "actor": self.store.actors.name(row.get("actor"))}

    def _job_view(self) -> dict:
        job = self.job
        tail = []
        if job.log_path and job.log_path.exists():
            tail = job.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[
                -LOG_TAIL:
            ]
        return {
            "status": job.status,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "actor": job.actor,
            "error": job.error,
            "log": tail,
        }

    def start_release(self, service, user: AuthUser) -> dict:
        from visualseek_retrieval.admin_overlay import fold, last_release

        state = fold(self.store.changes())
        released = last_release(self.store.releases())["state"]
        if not _pending(state, released, service.admin_index) and not (
            self.imports.pending_new_entities(service)
        ):
            raise _conflict("Nothing to release.", "nothing_to_release")
        with self._job_lock:
            if self.job.status == "running":
                raise _conflict("A release is already running.", "release_running")
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            self.store.logs_dir.mkdir(parents=True, exist_ok=True)
            self.job = ReleaseJob(
                status="running",
                started_at=_now(),
                actor=user.email or user.id,
                actor_alias=self.store.actors.alias(user.id, user.email),
                log_path=self.store.logs_dir / f"release-{stamp}.log",
            )
        threading.Thread(
            target=self._run_release,
            args=(self.job, service),
            name="admin-release",
            daemon=True,
        ).start()
        return self._job_view()

    def _run_release(self, job: ReleaseJob, service) -> None:
        """scripts/admin_release.py in this process: the re-embedding of changed doc texts uses
        the bge-m3 already on the GPU (a second copy does not fit on a 4 GB card)."""
        try:
            release = _release_module()
            with job.log_path.open("w", encoding="utf-8") as log:
                release.OUT = log
                try:
                    release.release(
                        True,
                        actor=job.actor_alias or "",
                        device=self.settings.search_embed_device,
                        encoder=_SharedDocEncoder(service),
                    )
                    release.log("reloading_search")
                    if self.on_reload is not None:
                        self.on_reload()
                    release.log("search_reloaded")
                    # Images of the entities this release created (CSV import) go in now.
                    self.imports.after_release()
                finally:
                    release.OUT = sys.stdout
            job.status = "succeeded"
        except (Exception, SystemExit) as error:  # the script stops with SystemExit
            logger.exception("admin_release_failed")
            job.status = "failed"
            job.error = str(error)[:300] or type(error).__name__
            with job.log_path.open("a", encoding="utf-8") as log:
                row = {"at": _now(), "step": "failed", "error": job.error}
                log.write(json.dumps(row) + "\n")
        finally:
            job.finished_at = _now()


@lru_cache(maxsize=1)
def _preview_module():
    """A copy of admin_release for previews (a running release loads its own)."""
    return _release_module()


def _release_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("admin_release", RELEASE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SharedDocEncoder:
    """The search's bge-m3 at document length, run on the search thread (GPU work is serialised
    there). Has what build_doc_texts.embed_new uses: ``model`` and ``encode``."""

    def __init__(self, service) -> None:
        self.service = service
        self.model = service.pipeline.encoder.model
        # build_doc_texts records the device in texts_manifest.json.
        self.device = service.pipeline.encoder.device

    def encode(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        return self.service.executor.submit(self._encode, texts, batch_size).result()

    def _encode(self, texts: list[str], batch_size: int) -> np.ndarray:
        from visualseek_retrieval.encoder import DOC_MAX_TOKENS

        query_length = self.model.max_seq_length
        self.model.max_seq_length = DOC_MAX_TOKENS
        try:
            return self.service.pipeline.encoder.encode(texts, batch_size=batch_size)
        finally:
            self.model.max_seq_length = query_length


# -------------------------------------------------------------------- entity index


class EntityIndex:
    """Canonical entities of a loaded search service, with what the admin pages show."""

    def __init__(self, service) -> None:
        pipeline = service.pipeline
        review = pipeline.review
        graph = pipeline.graph
        categories = service.categories or {}
        self.review = review
        self.graph_entities = {e["id"]: e for e in graph["entities"]}
        self.brands = {b["id"]: b for b in graph["brands"]}
        self.labels: dict[str, dict] = categories.get("entities", {})
        self.category_names: dict[str, tuple[str, str]] = {}
        self._load_taxonomy(categories)
        brand_by_member = {e["src"]: e["dst"] for e in graph["edges"] if e["rel"] == "MADE_BY"}
        self.entities = sorted({review.canonical(e) for e in self.graph_entities})
        self._brand: dict[str, str | None] = {}
        self.brand_entities: dict[str, list[str]] = defaultdict(list)
        for entity_id in self.entities:
            brand = next(
                (brand_by_member[m] for m in self.members(entity_id) if m in brand_by_member),
                None,
            )
            self._brand[entity_id] = brand
            if brand:
                self.brand_entities[brand].append(entity_id)
        self.images: dict[str, list[str]] = defaultdict(list)
        for asset_id, entity_ids in service.asset_entities.items():
            for owner in review.result_ids(asset_id, entity_ids):
                if owner in self.graph_entities:
                    self.images[owner].append(asset_id)
        from visualseek_retrieval.text import normalize

        self.search_text = {
            e: normalize(
                " ".join([self.name(e), *self.aliases(e), self.brand_name(self._brand[e])])
            )
            for e in self.entities
        }
        self.order = sorted(self.entities, key=lambda e: self.name(e).casefold())

    def _load_taxonomy(self, categories: dict) -> None:
        path = REPO_ROOT / "graph/taxonomy-v1.json"
        if path.exists():
            taxonomy = json.loads(path.read_text(encoding="utf-8"))
            for l1 in taxonomy["categories"]:
                for child in l1["children"]:
                    self.category_names[child["id"]] = (child["name"], l1["id"])
        else:  # names only, from the category file
            for cid, name in (categories.get("category_names") or {}).items():
                self.category_names[cid] = (name, cid.partition(".")[0])

    def members(self, entity_id: str) -> list[str]:
        return [entity_id, *self.review.members.get(entity_id, ())]

    def name(self, entity_id: str) -> str:
        return self.graph_entities[entity_id]["name"]

    def aliases(self, entity_id: str) -> list[str]:
        out: list[str] = []
        for member in self.members(entity_id):
            out += self.graph_entities.get(member, {}).get("aliases", [])
            out += self.review.aliases.get(member, [])
        return list(dict.fromkeys(out))

    def brand_of(self, entity_id: str) -> str | None:
        return self._brand.get(entity_id)

    def brand_name(self, brand_id: str | None) -> str:
        if not brand_id:
            return ""
        return (self.brands.get(brand_id) or {}).get("name", brand_id)

    def live_fields(self, entity_id: str) -> dict:
        """The released value of every admin field."""
        from visualseek_retrieval.admin_overlay import ENRICH_FIELDS, LIST_FIELDS

        label = self.labels.get(entity_id) or {}
        out = {
            "brand": self.brand_of(entity_id),
            "category": label.get("category"),
            "product_type": label.get("product_type"),
            "market_tier": label.get("market_tier"),
            "reviewed": label.get("source") == "human",
        }
        for name in ENRICH_FIELDS:
            out[name] = (
                list(label.get(name) or []) if name in LIST_FIELDS else label.get(name) or ""
            )
        return out

    def add_image(self, entity_id: str, asset_id: str) -> None:
        if asset_id not in self.images[entity_id]:
            self.images[entity_id].append(asset_id)

    def remove_image(self, entity_id: str, asset_id: str) -> None:
        if asset_id in self.images.get(entity_id, []):
            self.images[entity_id].remove(asset_id)


# -------------------------------------------------------------------- helpers


def _pending(state: dict, released: dict, index: EntityIndex) -> dict:
    from visualseek_retrieval.admin_overlay import pending_fields

    def live(entity_id: str) -> dict:
        return index.live_fields(entity_id) if entity_id in index.entities else {}

    return pending_fields(state, released, live)


def _matches(flt: str, row: dict, label: dict, edited: bool) -> bool:
    if flt in ("", "all"):
        return True
    if flt == "needs_review":
        return row["needs_review"]
    if flt == "no_brand":
        return not row["brand"]
    if flt == "no_category":
        return not label.get("category")
    if flt == "no_images":
        return row["images"] == 0
    if flt == "pending":
        return bool(row["pending"])
    if flt == "edited":
        return edited
    if flt == "hidden_images":
        return row["hidden_images"] > 0
    if flt == "uploaded":
        return row["uploaded_images"] > 0
    if flt == "imported":
        return row["imported"]
    raise _bad(f"Unknown filter {flt!r}.")


def _validate(name: str, value, index: EntityIndex):
    from visualseek_retrieval.admin_overlay import (
        LIST_FIELDS,
        MARKET_TIERS,
        NEW_BRAND_PREFIX,
        TEXT_FIELDS,
    )

    if name == "brand":
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise _bad("brand must be a brand id or new:<name>.")
        if value.startswith(NEW_BRAND_PREFIX):
            brand_name = " ".join(value[len(NEW_BRAND_PREFIX) :].split())
            if not brand_name or len(brand_name) > MAX_BRAND_NAME:
                raise _bad("A new brand needs a name of 1-60 characters.")
            from visualseek_retrieval.admin_overlay import new_brand_id
            from visualseek_retrieval.text import normalize

            if not new_brand_id(brand_name):
                raise _bad("A new brand name needs letters or digits.")
            # A "new" brand that already exists under that name or alias is that brand.
            key = normalize(brand_name)
            for brand in index.brands.values():
                if key == normalize(brand["name"]) or key in brand.get("aliases", ()):
                    return brand["id"]
            if new_brand_id(brand_name) in index.brands:
                return new_brand_id(brand_name)
            return f"{NEW_BRAND_PREFIX}{brand_name}"
        if value not in index.brands:
            raise _bad(f"Unknown brand {value}.")
        return value
    if name == "category":
        if value not in index.category_names:
            raise _bad(f"Unknown category {value!r}.")
        return value
    if name == "market_tier":
        if value not in MARKET_TIERS:
            raise _bad(f"market_tier must be one of {', '.join(MARKET_TIERS)}.")
        return value
    if name == "reviewed":
        if not isinstance(value, bool):
            raise _bad("reviewed must be true or false.")
        return value
    if name in LIST_FIELDS:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise _bad(f"{name} must be a list of texts.")
        items = list(dict.fromkeys(" ".join(v.split()) for v in value if v.strip()))
        if len(items) > MAX_LIST_ITEMS or any(len(v) > MAX_TEXT for v in items):
            raise _bad(f"{name}: at most {MAX_LIST_ITEMS} items of {MAX_TEXT} characters.")
        return items
    if name in TEXT_FIELDS or name == "product_type":
        if value is None:
            value = ""
        if not isinstance(value, str) or len(value) > MAX_TEXT:
            raise _bad(f"{name} must be a text of at most {MAX_TEXT} characters.")
        text = " ".join(value.split())
        if name == "product_type" and not text:
            raise _bad("product_type cannot be empty.")
        return text
    raise _bad(f"Unknown field {name}.")


def _display_value(index: EntityIndex, name: str, value):
    """A field value with names instead of ids, for the UI."""
    from visualseek_retrieval.admin_overlay import NEW_BRAND_PREFIX

    if name == "brand":
        if not value:
            return None
        if value.startswith(NEW_BRAND_PREFIX):
            from visualseek_retrieval.admin_overlay import new_brand_id

            name = value[len(NEW_BRAND_PREFIX) :]
            released = new_brand_id(name)
            if released in index.brands:  # created by an earlier release
                return {"id": released, "name": index.brand_name(released), "new": False}
            return {"id": value, "name": name, "new": True}
        return {"id": value, "name": index.brand_name(value), "new": False}
    if name == "category":
        if not value:
            return None
        cat_name, l1 = index.category_names.get(value, (value, ""))
        return {"id": value, "name": cat_name, "l1": l1}
    return value


def _display(index: EntityIndex, fields: dict) -> dict:
    return {name: _display_value(index, name, value) for name, value in fields.items()}


def _release_summary(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "state"}
