import numpy as np
import pytest

from visualseek_retrieval.fusion import (
    FusionSettings,
    VisualFusion,
    fuse,
    has_visual_attribute,
    zscore,
)


@pytest.mark.parametrize(
    "query",
    [
        "chuột máy tính màu trắng",
        "ghế gỗ",
        "bình nước bằng inox",
        "sản phẩm họa tiết caro",
        "đồng hồ treo tường hình tròn",
        "đồng hồ đeo tay dây da",
    ],
)
def test_visual_attribute_detected(query):
    assert has_visual_attribute(query)


@pytest.mark.parametrize(
    "query",
    [
        "máy lọc nước",
        "thuốc kháng sinh",
        "đồng hồ thông minh",
        "sữa dưỡng da",
        "chuot mau trang",
        "",
    ],
)
def test_object_names_are_not_attributes(query):
    assert not has_visual_attribute(query)


def test_zscore_constant_is_zero():
    assert not zscore(np.array([0.5, 0.5, 0.5])).any()


def test_fuse_alpha_extremes_follow_one_channel():
    text, img = np.array([0.9, 0.1, 0.5]), np.array([0.1, 0.9, 0.5])
    assert np.argmax(fuse(text, img, 1.0)) == 0
    assert np.argmax(fuse(text, img, 0.0)) == 1


def test_rank_unions_image_candidates_and_uses_both_scores():
    ids = ["a", "b", "c"]
    doc = np.eye(3, dtype=np.float32)
    img = np.array([[1, 0], [0, 1], [0.6, 0.8]], dtype=np.float32)
    fusion = VisualFusion(ids, doc, img, FusionSettings(img_candidates=1, alpha_attribute=0.0))
    # "b" is not a text candidate but is the image top-1, so it enters and wins on looks.
    ranked = fusion.rank("áo màu đỏ", ["a", "c"], doc[0], np.array([0, 1], dtype=np.float32))
    assert [a for a, _ in ranked] == ["b", "c", "a"]
    assert fusion.candidates(["a", "zz"], np.array([1, 0], dtype=np.float32), 0) == ["a"]


def test_row_count_mismatch_raises():
    with pytest.raises(ValueError):
        VisualFusion(["a"], np.zeros((2, 3)), np.zeros((1, 2)))
