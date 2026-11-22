import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import find_near_duplicates as nd  # noqa: E402


def picture(size, tint=(0, 0, 0), label=False):
    """A product-like shot: dark box on white; `label` prints a small mark on the box."""
    width, height = size
    grid = np.full((height, width, 3), 255, dtype=np.uint8)
    y0, y1, x0, x1 = height // 4, 3 * height // 4, width // 3, 2 * width // 3
    grid[y0:y1, x0:x1] = np.array([40, 40, 40]) + np.array(tint)
    grid[y0:y0 + height // 10, :width // 5] = 120
    if label:  # ~2% of the area, like a different strength printed on a drug box
        grid[height // 2:height // 2 + height // 12, width // 2:width // 2 + width // 6] = 250
    return Image.fromarray(grid.astype(np.uint8))


@pytest.fixture
def images(tmp_path):
    files = {
        "a_large": picture((800, 600)),
        "b_small": picture((400, 300)),                  # same picture, half size
        "c_red": picture((800, 600), tint=(180, 0, 0)),  # colour variant: same layout
        "d_other": picture((600, 800)),                  # different aspect ratio
        "e_label": picture((800, 600), label=True),      # same layout, other printed label
    }
    rows = []
    for name, image in files.items():
        path = tmp_path / f"{name}.png"
        image.save(path)
        rows.append({"asset_id": name, "image_path": path.name, "bytes": path.stat().st_size})
    return tmp_path, rows


def run(images, tmp_path, **thresholds):
    root, rows = images
    cache, errors = nd.compute_features(root, rows, tmp_path / "cache.npz", workers=1)
    limits = {"max_hamming": 6, "max_mae": 8.0, "max_aspect": 0.03, "max_p99": 20.0}
    found = nd.find(rows, cache, nd.fine_loader(root, rows), **{**limits, **thresholds})
    return found, cache, errors


def test_resized_copy_merges_into_the_larger_image(images, tmp_path):
    (groups, _), _, errors = run(images, tmp_path)
    assert not errors
    assert [(g["representative"], [m["asset_id"] for m in g["members"]]) for g in groups] == [
        ("a_large", ["b_small"])]


def test_colour_variant_and_other_aspect_stay_apart_even_with_a_loose_hash(images, tmp_path):
    (groups, pairs), cache, _ = run(images, tmp_path, max_hamming=64)
    merged = {m["asset_id"] for g in groups for m in g["members"]}
    assert merged == {"b_small"}
    assert all({"c_red", "d_other", "e_label"}.isdisjoint((p["a"], p["b"])) for p in pairs)


def test_a_small_printed_difference_fails_the_fine_check(images, tmp_path):
    (groups, _), cache, _ = run(images, tmp_path, max_hamming=64)
    root, rows = images
    mae16 = np.abs(np.frombuffer(cache["a_large"][1], dtype=np.uint8).astype(np.int16)
                   - np.frombuffer(cache["e_label"][1], dtype=np.uint8)).mean()
    fine_of = nd.fine_loader(root, rows)
    assert mae16 <= 8.0  # the coarse thumbnail barely sees the label
    assert nd.fine_p99(fine_of("a_large"), fine_of("e_label")) > 20.0
    assert "e_label" not in {m["asset_id"] for g in groups for m in g["members"]}


def test_features_are_cached_by_asset_id(images, tmp_path):
    root, rows = images
    first, _ = nd.compute_features(root, rows, tmp_path / "cache.npz", workers=1)
    (root / "a_large.png").unlink()  # a cached image is never decoded again
    second, errors = nd.compute_features(root, rows, tmp_path / "cache.npz", workers=1)
    assert not errors and first == second


def test_identical_pictures_of_different_entities_stay_apart(images, tmp_path):
    root, rows = images
    for row in rows:
        row["entity_ids"] = ["e2"] if row["asset_id"] == "b_small" else ["e1"]
    cache, _ = nd.compute_features(root, rows, tmp_path / "cache.npz", workers=1)
    groups, pairs = nd.find(rows, cache, nd.fine_loader(root, rows), max_hamming=6,
                            max_mae=8.0, max_aspect=0.03, max_p99=20.0)
    assert groups == [] and pairs == []


def test_star_grouping_does_not_chain():
    cache = {k: (0, bytes(768), w, 100) for k, w in (("big", 300), ("mid", 200), ("tiny", 100))}
    cache["tiny"] = (0b111, bytes([60] * 768), 100, 100)  # close to mid only
    cache["mid"] = (0b11, bytes([30] * 768), 200, 100)
    rows = [{"asset_id": k, "bytes": 1} for k in cache]
    flat = {k: np.zeros((4, 4, 3), dtype=np.int16) for k in cache}
    groups, _ = nd.find(rows, cache, flat.__getitem__, max_hamming=2, max_mae=40.0,
                        max_aspect=10, max_p99=0)
    members = {g["representative"]: [m["asset_id"] for m in g["members"]] for g in groups}
    assert members == {"big": ["mid"]}  # tiny verified only against mid, so it stays alone
