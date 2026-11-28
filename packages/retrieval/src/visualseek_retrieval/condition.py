"""Drug queries by condition: "thuốc đau dạ dày", "thuốc trị cao huyết áp".

The words after "thuốc" name a complaint; the graph's TREATS purposes say which drugs are
labelled for it. This module only finds the purpose nodes; the pipeline keeps the drugs among
their products. It is deliberately strict: when in doubt it returns nothing and the query is
answered by content search.
"""

from __future__ import annotations

import re

from visualseek_retrieval.enrichment import PURPOSE_RULES, purpose_buckets
from visualseek_retrieval.text import normalize, surface

# "thuốc" that is not a medicine for people, so human drugs must not answer it.
NOT_HUMAN_MEDICINE = (
    "thuốc lá", "thuốc lào", "thuốc nổ", "thuốc súng", "thuốc phóng", "thuốc trừ",
    "thuốc diệt", "thuốc nhuộm", "thuốc bảo vệ thực vật", "thuốc tẩy rửa", "thuốc thử",
    "thú y", "cho chó", "cho mèo", "cho lợn", "cho heo", "cho gà", "cho vịt", "cho bò",
    "cho cá", "cho tôm", "cho cây", "cho lúa", "cho hoa",
)  # fmt: skip
# Who the medicine is for. The graph does not know which drugs these people may take
# (a cough syrup with codeine is not for a child), so such a query is never filtered by
# complaint: a list headed by the complaint would look like an answer for them.
AUDIENCE = (
    "trẻ", "em bé", "bé", "sơ sinh", "bà bầu", "mẹ bầu", "mang thai", "có thai", "thai kỳ",
    "cho con bú", "người già", "người cao tuổi", "người lớn",
)  # fmt: skip
# A dosage form asked for ("dạng viên", "bôi", "siro"). The purposes say nothing about forms,
# so filtering by the complaint would drop that half of the query.
FORMS = (
    "dạng", "viên", "siro", "si rô", "tiêm", "truyền", "bôi", "gel", "kem", "xịt", "dán",
    "nhỏ", "gói", "bột", "ngậm", "đặt",
)  # fmt: skip
# Names of the shared buckets ("giảm đau hạ sốt"). A bucket holds more than any phrase inside
# its name, so it counts as an exact purpose only when the query says the whole name.
BUCKET_NAMES = frozenset(surface(name) for name, _ in PURPOSE_RULES)
# Words between "thuốc" and the complaint. Stripped one at a time, and only while the phrase
# still does not match, so "giảm đau" stays whole.
LEADING_WORDS = (
    "đặc trị", "điều trị", "hỗ trợ", "trị", "chữa", "giảm", "chống", "phòng", "dùng", "uống",
    "cho", "người", "bị", "bệnh", "chứng", "khi", "để",
)  # fmt: skip
TRAILING_WORDS = ("loại nào", "nào", "gì", "tốt nhất", "tốt", "hiệu quả")
# Purpose buckets that name a dosage form, not a complaint: filtering by them would drop every
# product of that form whose label happens not to say it.
FORM_BUCKETS = frozenset({"nhỏ mắt"})
# A one-word complaint this short or vague matches too much to act on.
TOO_VAGUE = frozenset({"đau", "viêm", "bệnh", "trị", "chữa", "uống", "bổ", "mới", "tốt"})


def _strip(phrase: str, words: tuple[str, ...], leading: bool) -> str | None:
    """``phrase`` without the first of ``words`` it starts (or ends) with; None if none does."""
    for word in sorted(words, key=len, reverse=True):
        if leading and (phrase == word or phrase.startswith(word + " ")):
            return phrase[len(word) :].strip()
        if not leading and phrase.endswith(" " + word):
            return phrase[: -len(word)].strip()
    return None


def condition_phrase(query: str) -> str | None:
    """What follows "thuốc" in a query about medicine for people, else None. None too when the
    query says who it is for or which dosage form it wants.

    Only accented queries are read: without accents "thuoc" is also "thuộc", and complaints
    such as "ho" cannot be told apart from other words.
    """
    text = surface(query)
    excluded = (*NOT_HUMAN_MEDICINE, *AUDIENCE, *FORMS)
    if any(re.search(rf"\b{re.escape(term)}\b", text) for term in excluded):
        return None
    found = re.search(r"\bthuốc\b(.*)$", text)
    if not found:
        return None
    phrase = found.group(1).strip()
    while (shorter := _strip(phrase, TRAILING_WORDS, leading=False)) is not None:
        phrase = shorter
    return phrase or None


def match_purposes(phrase: str, purposes: dict[str, str]) -> list[list[str]]:
    """Purpose node ids of a complaint, narrowest first. ``purposes``: node id -> label.

    The first set holds the nodes whose label is the complaint or contains it as whole words
    ("ho" -> "chữa ho"). The last one adds its shared bucket ("trào ngược dạ dày" ->
    "dạ dày"), which is wider ("mất ngủ" -> "an thần" also holds antidepressants): the caller
    uses it only when the first has too few products. A shorter label found inside the
    complaint never matches on its own: "đau" in "đau dạ dày" must not bring in the
    painkillers. A phrase that names a dosage form matches nothing.
    """
    variants: list[str] = []
    while phrase:
        variants.append(phrase)
        phrase = _strip(phrase, LEADING_WORDS, leading=True)
    if any(variant in FORM_BUCKETS for variant in variants):
        return []

    def bucket_of(variant: str) -> set[str]:
        wanted = {surface(bucket) for bucket in purpose_buckets([variant])} - FORM_BUCKETS
        return {n for n, label in purposes.items() if surface(label) in wanted}

    # The complaint without its leading verbs first: "trị ho" and "chống dị ứng" must find
    # what "ho" and "dị ứng" find. A verb is kept when what is left is too vague ("giảm đau").
    wider = bucket_of(variants[0]) if variants else set()
    for variant in reversed(variants):
        if variant in TOO_VAGUE or len(normalize(variant)) < 2:
            continue
        whole = re.compile(rf"(?<!\w){re.escape(variant)}(?!\w)")
        exact = {
            n
            for n, label in purposes.items()
            if (name := surface(label)) == variant
            or (name not in BUCKET_NAMES and whole.search(name))
        }
        if exact:
            if wider <= exact:
                return [sorted(exact)]
            return [sorted(exact), sorted(exact | wider)]
    return [sorted(wider)] if wider else []
