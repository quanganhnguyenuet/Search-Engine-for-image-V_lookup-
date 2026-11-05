import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from zipfile import ZipFile

from visualseek_preprocessing.culture_dataset import (
    RateLimitError,
    RequestGate,
    download,
    download_archive,
    image_path,
    main,
    make_asset,
    reconcile_records,
    select_records,
)


def record(category="am_thuc", subject="pho", filename="000001.jpg"):
    return {
        "category": category,
        "image_path": f"data/images/{category}/{subject}/{filename}",
        "image_id": "000001",
        "image_analysis": {"overall_description": "Một bát phở", "main_objects": ["phở"]},
    }


class CultureDatasetTests(unittest.TestCase):
    def test_inventory_reconciles_apostrophes_and_includes_unannotated_files(self):
        annotated = record("trang_phuc", "trang_phục_H'Mông")
        actual = "images/trang_phuc/trang_phục_HMông/000001.jpg"
        extra = "images/kien_truc/chua_mot_cot/000002.jpg"
        rows, aliases = reconcile_records([annotated], [actual, extra])
        self.assertEqual(len(rows), 2)
        self.assertEqual(aliases[0]["annotation_path"], image_path(annotated))
        matched = next(row for row in rows if row["category"] == "trang_phuc")
        self.assertEqual(image_path(matched), actual)
        self.assertEqual(matched["annotation_image_path"], annotated["image_path"])
        self.assertEqual(matched["image_analysis"], annotated["image_analysis"])
        fallback = next(row for row in rows if row["category"] == "kien_truc")
        self.assertEqual(fallback["metadata"]["annotation_status"], "missing")
        self.assertNotIn("image_analysis", fallback)

    def test_inventory_preserves_genuinely_missing_annotations(self):
        rows, aliases = reconcile_records([record()], [])
        self.assertEqual(rows, [record()])
        self.assertEqual(aliases, [])

    def test_offline_manifest_preserves_resolved_paths_and_extra_images(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = [record("trang_phuc", "trang_phục_H'Mông")]
            raw = root / "raw"
            raw.mkdir()
            (raw / "vietnamese_vqa_dataset.json").write_text(json.dumps(rows))
            paths = ["images/trang_phuc/trang_phục_HMông/000001.jpg",
                     "images/kien_truc/chua_mot_cot/000002.jpg"]
            for index, name in enumerate(paths):
                image = raw / name
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(f"image fixture {index}".encode())
            argv = ["prepare", "--offline", "--root", str(raw),
                    "--output", str(root / "output")]
            with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            report = json.loads((root / "output/report.json").read_text())
            self.assertEqual(report["available_images"], 2)
            self.assertEqual(report["unique_assets"], 2)
            self.assertEqual(report["path_aliases"], 1)
            self.assertEqual(report["unannotated_images"], 1)
            self.assertTrue(report["complete"])

    def test_bulk_download_resumes_partial_zip_and_reuses_complete_cache(self):
        content = io.BytesIO()
        with ZipFile(content, "w") as archive:
            archive.writestr("Vietnamese Culture VQA Dataset/annotations/"
                             "vietnamese_vqa_dataset.json", "[]")
        payload = content.getvalue()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "dataset.zip"
            partial = target.with_name("dataset.zip.part")
            partial.write_bytes(payload[:20])
            response = io.BytesIO(payload[20:])
            response.status = 206
            response.headers = {
                "Content-Type": "application/zip",
                "Content-Length": str(len(payload) - 20),
                "Content-Range": f"bytes 20-{len(payload) - 1}/{len(payload)}",
            }
            with patch("visualseek_preprocessing.culture_dataset.urlopen",
                       return_value=response) as get, contextlib.redirect_stdout(io.StringIO()):
                download_archive(target)
                self.assertEqual(get.call_args.args[0].get_header("Range"), "bytes=20-")
                download_archive(target)
                self.assertEqual(get.call_count, 1)
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(partial.exists())

    def test_bulk_rate_limit_preserves_partial_download(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "dataset.zip"
            partial = target.with_name("dataset.zip.part")
            partial.write_bytes(b"partial transfer")
            error = HTTPError("https://www.kaggle.com", 429, "Too Many Requests", {}, None)
            with patch("visualseek_preprocessing.culture_dataset.urlopen", side_effect=error):
                with self.assertRaises(RateLimitError):
                    download_archive(target)
            self.assertEqual(partial.read_bytes(), b"partial transfer")
            self.assertFalse(target.exists())

    def test_bulk_ignoring_range_restarts_without_appending_duplicate_bytes(self):
        content = io.BytesIO()
        with ZipFile(content, "w") as archive:
            archive.writestr("Vietnamese Culture VQA Dataset/annotations/"
                             "vietnamese_vqa_dataset.json", "[]")
        payload = content.getvalue()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "dataset.zip"
            target.with_name("dataset.zip.part").write_bytes(b"old incomplete bytes")
            response = io.BytesIO(payload)
            response.status = 200
            response.headers = {"Content-Type": "application/zip",
                                "Content-Length": str(len(payload))}
            with patch("visualseek_preprocessing.culture_dataset.urlopen",
                       return_value=response), contextlib.redirect_stdout(io.StringIO()):
                download_archive(target)
            self.assertEqual(target.read_bytes(), payload)

    def test_archive_creates_filtered_manifest_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive_path = root / "dataset.zip"
            rows = [record(), record("giao_thong")]
            prefix = "Vietnamese Culture VQA Dataset/"
            with ZipFile(archive_path, "w") as archive:
                archive.writestr(prefix + "annotations/vietnamese_vqa_dataset.json",
                                 json.dumps(rows))
                archive.writestr(prefix + image_path(rows[0]), b"image fixture")
            argv = ["prepare", "--archive", str(archive_path),
                    "--root", str(root / "raw"), "--output", str(root / "output")]
            with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
                with patch("visualseek_preprocessing.culture_dataset.urlopen") as get:
                    self.assertEqual(main(), 0)
                    get.assert_not_called()
            report = json.loads((root / "output/report.json").read_text())
            self.assertEqual(report["unique_assets"], 1)
            self.assertEqual(report["failures"], 0)
            self.assertEqual(report["categories"], {"am_thuc": 1})

    def test_rate_limit_stops_further_network_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            gate = RequestGate(0)
            error = HTTPError("https://www.kaggle.com", 429, "Too Many Requests", {}, None)
            with patch(
                "visualseek_preprocessing.culture_dataset.urlopen", side_effect=error
            ) as get:
                for index in range(2):
                    with self.assertRaises(RateLimitError):
                        download("images/a.jpg", Path(folder) / f"{index}.jpg", gate)
                self.assertEqual(get.call_count, 1)
            self.assertFalse(list(Path(folder).rglob("*.part")))

    def test_filters_categories_and_deduplicates_paths(self):
        food = record()
        self.assertEqual(select_records([food, food, record("giao_thong")]), [food])

    def test_sampling_is_deterministic_and_per_category(self):
        rows = [record(subject=str(i)) for i in range(10)] + [record("kien_truc")]
        selected = select_records(rows, limit=2)
        self.assertEqual(selected, select_records(list(reversed(rows)), limit=2))
        self.assertEqual(len(selected), 3)

    def test_rejects_unsafe_paths_and_category_mismatch(self):
        for path in ["../secret", "/images/am_thuc/a/b.jpg",
                     "data/images/am_thuc/../../secret.jpg", "images/le_hoi/a/b.jpg"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                image_path({**record(), "image_path": path})

    def test_repeated_source_image_ids_are_distinct_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            assets = []
            for index in range(2):
                row = record(subject=str(index))
                path = root / image_path(row)
                path.parent.mkdir(parents=True)
                path.write_bytes(f"different content {index}".encode())
                assets.append(make_asset(row, root))
            self.assertNotEqual(assets[0]["asset_id"], assets[1]["asset_id"])
            self.assertEqual(assets[0]["description"], "Một bát phở")

    def test_identical_content_has_same_identity_and_split(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            assets = []
            for category in ["am_thuc", "le_hoi"]:
                row = record(category)
                path = root / image_path(row)
                path.parent.mkdir(parents=True)
                path.write_bytes(b"identical image bytes")
                assets.append(make_asset(row, root))
            self.assertEqual(assets[0]["asset_id"], assets[1]["asset_id"])
            self.assertEqual(assets[0]["split"], assets[1]["split"])


if __name__ == "__main__":
    unittest.main()
