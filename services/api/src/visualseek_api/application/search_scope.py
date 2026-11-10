"""Explicit corpus scope, independent of similarity scores and relevance calibration.

Local rules recognize configured subjects, not arbitrary world knowledge. Unknown
requests require semantic planning or clarification; they never fall through to KNN.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from visualseek_retrieval.text import normalize

SCOPE_DESCRIPTION = (
    'Thư viện chỉ tìm ảnh văn hóa và cảnh quan Việt Nam: ẩm thực, kiến trúc, '
    'phong cảnh, lễ hội, trang phục và thủ công mỹ nghệ.'
)
SCOPE_EXAMPLES = {
    'am_thuc': 'Bánh chưng ngày Tết',
    'kien_truc': 'Phố cổ Hội An',
    'phong_canh': 'Ruộng bậc thang ở Sa Pa',
    'le_hoi': 'Lễ hội truyền thống Việt Nam',
    'trang_phuc': 'Người mặc áo dài',
    'thu_cong_my_nghe': 'Nghệ nhân làm gốm',
}

# Reviewed product policy, not a trained semantic classifier or a cosine threshold.
SUPPORTED_SUBJECTS = (
    'ẩm thực', 'món ăn', 'bánh', 'phở', 'bún', 'cơm', 'nem', 'chả', 'chè', 'cà phê',
    'áo dài', 'áo bà ba', 'trang phục', 'nón lá', 'thổ cẩm', 'đồ truyền thống',
    'kiến trúc', 'nhà cổ', 'ngôi nhà cổ kính', 'phố cổ', 'đình', 'chùa', 'đền',
    'lăng', 'lăng tẩm', 'cung đình', 'vua triều nguyễn', 'vua việt nam',
    'phong cảnh', 'cảnh', 'ruộng', 'thác nước', 'vịnh', 'hang động', 'khu vườn',
    'núi', 'biển', 'sông', 'hồ nước', 'cánh đồng', 'đồng lúa', 'hoa sen',
    'lễ hội', 'tết', 'trung thu', 'múa lân', 'đua thuyền', 'giỗ tổ',
    'thủ công', 'gốm', 'nặn bình', 'nặn một chiếc bình', 'đan lát', 'tranh',
    'sơn mài', 'làng nghề', 'pottery', 'ao dai', 'terraced', 'rice fields',
    'vietnamese food', 'vietnamese architecture', 'vietnamese festival',
    'traditional clothing', 'handicrafts', 'landscape',
)
UNSUPPORTED_SUBJECTS = (
    'tàu vũ trụ', 'phi hành gia', 'sao hỏa', 'hành tinh khác', 'người ngoài hành tinh',
    'spaceship', 'spacecraft', 'astronaut', 'mars', 'outer space', 'alien planet',
    'khoa học viễn tưởng', 'science fiction', 'siêu anh hùng', 'superhero',
    'khủng long', 'dinosaur', 'pokemon', 'anime',
    'tháp eiffel', 'eiffel tower', 'tượng nữ thần tự do', 'statue of liberty',
    'núi phú sĩ', 'mount fuji', 'kim tự tháp ai cập', 'egyptian pyramids',
    'hoa anh đào ở nhật', 'lễ hội ở nhật', 'đền ở nhật', 'kiến trúc nhật bản',
    'tokyo', 'paris', 'new york', 'london',
    'điện thoại', 'máy tính', 'laptop', 'iphone', 'smartphone', 'computer',
    'xe đua', 'race car', 'bitcoin', 'chứng khoán', 'stock market',
    'chẩn đoán', 'kê đơn', 'diagnosis', 'viết code', 'python code',
    'trạm vũ trụ', 'trên mặt trăng', 'sao kim', 'sao thổ', 'robot',
    'nam cực', 'bắc cực', 'dãy alps', 'san francisco', 'golden gate', 'cổng vàng',
    'bóng bầu dục', 'silicon valley', 'bắc âu', 'grand canyon', 'iceland', 'cực quang',
    'ảnh hoàn toàn trống', 'ảnh trắng trơn',
)


@dataclass(frozen=True)
class ScopeDecision:
    status: Literal['in_scope', 'out_of_scope', 'unclear']
    reason: str
    source: str = 'local_policy'

    def as_dict(self):
        return {'status': self.status, 'reason': self.reason, 'source': self.source,
                'description': SCOPE_DESCRIPTION, 'categories': list(SCOPE_EXAMPLES)}


def scope_suggestions(category: str | None = None) -> list[str]:
    if category in SCOPE_EXAMPLES:
        return [SCOPE_EXAMPLES[category]]
    return list(SCOPE_EXAMPLES.values())[:4]


@lru_cache(maxsize=16)
def _terms(terms: tuple[str, ...]) -> frozenset[str]:
    return frozenset(normalize(term) for term in terms if term)


def assess_scope(query: str, *, known_entity: bool = False,
                 catalog_titles: tuple[str, ...] = ()) -> ScopeDecision:
    words = normalize(query).split()
    supported = _terms(SUPPORTED_SUBJECTS) | _terms(catalog_titles)
    unsupported = _terms(UNSUPPORTED_SUBJECTS)
    lengths = {len(term.split()) for term in supported | unsupported}
    spans = {}
    for width in lengths:
        for start in range(len(words) - width + 1):
            spans.setdefault(" ".join(words[start:start + width]), start)
    outside = [spans[term] for term in unsupported if term in spans]
    if outside:
        # Negations/OR can change the scope of the mention. Ask the planner rather
        # than rejecting, e.g. "áo dài, không có tàu vũ trụ" by isolated keywords.
        # Only inspect words before the first outside mention: the syllable "trụ"
        # inside "tàu vũ trụ" must never be read as the operator "trừ".
        prefix = words[max(0, min(outside) - 5):min(outside)]
        logical = {'khong', 'tru', 'bo', 'loai', 'without', 'not', 'excluding'}
        if logical.intersection(prefix) or {'hoac', 'or'}.intersection(words):
            return ScopeDecision('unclear', 'scope_needs_interpretation')
        return ScopeDecision('out_of_scope', 'outside_supported_subjects')
    if known_entity or supported.intersection(spans):
        return ScopeDecision('in_scope', 'supported_subject')
    return ScopeDecision('unclear', 'unrecognized_subject')
