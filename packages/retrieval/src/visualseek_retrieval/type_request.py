"""Which kind of product a brand query asks for (P1): "Canon còn máy ảnh nào khác" is not "Canon".

A query that names a brand used to reach every product of the brand. Here the words that are
neither a catalogue name nor a cue are read as the requested product type ("máy ảnh") and the
excluded one ("ngoài vợt tennis"), and the pipeline matches them against the ``product_type``
of the brand's own products. The scope is one brand (a handful to a few dozen products), so
plain word n-gram containment is precise enough: no model, no LLM.

Words that do NOT state a request:
  - spans linked to a catalogue entity or brand;
  - connective and relation cues ("và", "trừ", "hãng", "cùng thương hiệu với");
  - filler ("còn", "những", "gì", "khác", "sản phẩm", "đồ");
  - with a relation cue, the descriptor of the anchor: everything between the cue and the end
    of the entity it introduces ("hãng làm nồi chiên không dầu Philips", "cùng hãng với vali
    Samsonite Lite-Box");
  - a run of words that ends right where an entity span starts ("vali" in "vali Roncato Box").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from visualseek_retrieval import connectors
from visualseek_retrieval.text import normalize

MAX_NGRAM = 4
MIN_CHARS = 2
# Words that never name a product type. Matched on the accented word when the query carries
# diacritics ("bán" is not "bàn"), else on the folded word.
FILLER_ACCENTED = frozenset(
    "còn có những các mọi món nào gì khác sản phẩm đồ hãng làm bán cho của thì là với cùng như "
    "nhiều một ra được nữa thêm xem ảnh hình tìm kiếm cần muốn và hoặc hay trong trên dưới từ "
    "đến loại mẫu xuất cả đó này ấy".split()
)
FILLER_FOLDED = frozenset(normalize(word) for word in FILLER_ACCENTED)
# "ngoài X ra thì ..." and "ngoài X còn ...": the excluded phrase ends at one of these.
EXCLUSION_END = frozenset(("ra", "thi", "con"))


@dataclass
class Request:
    """Word n-gram runs of what is asked for and of what is excluded.

    ``clauses[i]`` is the clause of ``include[i]``: the query cut at "và", "hoặc", "ngoài ra",
    "nhưng" and list commas. Runs of one clause describe one thing together ("phụ tùng ...
    xe ... Air Blade"); runs of different clauses are alternatives ("lều và bếp").
    ``anchor`` carries the descriptor words between a relation cue and a brand when no
    catalogue entity was spotted there ("hãng làm nồi chiên không dầu Philips").
    """

    include: list[tuple[str, ...]] = field(default_factory=list)
    exclude: list[tuple[str, ...]] = field(default_factory=list)
    clauses: list[int] = field(default_factory=list)
    anchor: list[tuple[str, ...]] = field(default_factory=list)

    def clause_of(self, index: int) -> int:
        return self.clauses[index] if index < len(self.clauses) else 0

    def __bool__(self) -> bool:
        return bool(self.include or self.exclude)


def _word_index(text: str, offset: int) -> int:
    """Index of the word that starts at character ``offset`` of single-spaced ``text``."""
    return text[:offset].count(" ")


def _runs(indexes, consumed: list[bool], entity_starts: set[int]) -> list[list[int]]:
    """Runs of consecutive word indexes that state a type; one that ends where an entity span
    starts describes that entity ("vali" in "vali Roncato Box") and is dropped."""
    runs: list[list[int]] = []
    for index in indexes:
        if consumed[index]:
            continue
        if runs and runs[-1][-1] == index - 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return [run for run in runs if run[-1] + 1 not in entity_starts]


def extract(
    words: list[str],
    spans: list[tuple[int, int]],
    entity_spans: list[tuple[int, int]],
    accented: bool,
    relation: re.Pattern | None = None,
) -> Request:
    """Requested and excluded type phrases of a query piece.

    ``words`` are the surface words (lower case, with marks); ``spans`` are the word ranges
    linked to catalogue entities or brands, ``entity_spans`` the entity ones among them;
    ``relation`` is the relation-cue pattern ("hãng", "thương hiệu") of the query's script.
    """
    if not words:
        return Request()
    keys = words if accented else [normalize(word) for word in words]
    text = " ".join(keys)
    consumed = [False] * len(keys)

    def consume(start: int, end: int) -> None:
        for index in range(max(start, 0), min(end, len(keys))):
            consumed[index] = True

    for start, end in spans:
        consume(start, end)
    separators = []
    for cue in connectors.find(text):
        consume(_word_index(text, cue.start), _word_index(text, cue.end) + 1)
        if cue.kind in connectors.SEPARATORS:
            separators.append(_word_index(text, cue.start))
    anchor_range = range(0)
    if relation is not None and (found := relation.search(text)):
        cue_end = _word_index(text, found.end()) + 1
        consume(_word_index(text, found.start()), cue_end)
        # The anchor's own descriptor: from the cue to the end of the entity or brand it
        # introduces. What follows ("còn có điện thoại nào khác") is the request.
        after = [end for start, end in spans if start >= cue_end]
        if after:
            anchor_range = range(cue_end, max(after))
    fillers = FILLER_ACCENTED if accented else FILLER_FOLDED
    for index, key in enumerate(keys):
        # "ảnh" asks for a picture ("ảnh Schwalbe") unless it is part of "máy ảnh".
        if key in fillers and not (index and normalize(keys[index - 1]) == "may"):
            consumed[index] = True

    starts = {start for start, _ in entity_spans}
    anchor_runs = (
        [tuple(words[i] for i in run) for run in _runs(anchor_range, consumed, starts)]
        if anchor_range and not any(start >= anchor_range.start for start, _ in entity_spans)
        else []
    )
    if anchor_range:
        consume(anchor_range.start, anchor_range.stop)

    exclusion = range(0)
    cue = connectors.has_negation(text)
    if cue is not None:
        begin = _word_index(text, cue.end) + 1
        end = len(keys)
        if cue.kind == "besides":
            end = next(
                (i for i in range(begin, len(keys)) if normalize(keys[i]) in EXCLUSION_END),
                len(keys),
            )
        exclusion = range(begin, end)
    positive = [i for i in range(len(keys)) if i not in exclusion]

    include = _runs(positive, consumed, starts)
    return Request(
        include=[tuple(words[i] for i in run) for run in include],
        exclude=[tuple(words[i] for i in run) for run in _runs(exclusion, consumed, starts)],
        clauses=[sum(cut < run[0] for cut in separators) for run in include],
        anchor=anchor_runs,
    )


def grams(runs: list[tuple[str, ...]], accented: bool) -> set[tuple[str, ...]]:
    """Every word n-gram (longest 4) of the runs, in the script the query is written in."""
    out: set[tuple[str, ...]] = set()
    for run in runs:
        keys = tuple(run if accented else (normalize(word) for word in run))
        for size in range(1, MAX_NGRAM + 1):
            for start in range(len(keys) - size + 1):
                gram = keys[start : start + size]
                if sum(len(word) for word in gram) >= MIN_CHARS:
                    out.add(gram)
    return out


def contains(words: tuple[str, ...], wanted: set[tuple[str, ...]]) -> bool:
    """Whether any of the n-grams occurs contiguously in ``words``."""
    for gram in wanted:
        size = len(gram)
        if any(words[i : i + size] == gram for i in range(len(words) - size + 1)):
            return True
    return False


def starts_with(words: tuple[str, ...], wanted: set[tuple[str, ...]]) -> bool:
    """Whether ``words`` begin with one of the n-grams: the head of a product type."""
    return any(words[: len(gram)] == gram for gram in wanted)


def by_head(
    found: set[str],
    candidates: dict[str, tuple[str, ...]],
    wanted: set[tuple[str, ...]],
    head: str | None,
    initial: tuple[str, ...] | None = None,
) -> set[str]:
    """Vietnamese noun phrases are head-first: "Hộp mực máy in" is a cartridge, "Lốp xe máy" a
    tyre. A phrase at the start of a product type names the product; elsewhere it only
    qualifies it. ``head="prefer"`` keeps the head matches when there are any (the modifier
    ones still answer "đồ Bosch cho ô tô"); ``"only"`` keeps nothing else; ``None`` keeps all.

    The same holds in the query: ``initial`` is the n-gram that starts the query phrase. A
    match only through a later n-gram ("công trình" in "máy công trình") qualifies the word
    before it, so the head of the type does not matter ("prefer") or is not shown ("only").
    """
    if head is None:
        return found
    if initial is not None:
        lead = {initial} & wanted
        if not lead or not any(contains(candidates[key], lead) for key in found):
            return found if head == "prefer" else set()
        wanted = lead
    heads = {key for key in found if starts_with(candidates[key], wanted)}
    return heads if heads or head == "only" else found


def longest_hits(
    candidates: dict[str, tuple[str, ...]],
    runs: list[tuple[str, ...]],
    accented: bool,
    head: str | None = None,
) -> set[str]:
    """Candidates matching the longest phrase of each run that matches any candidate.

    "máy in" must not also mean "máy" (cameras, scanners): once a two-word phrase of the run
    matches a product, the one-word grams of that run are not tried. ``head`` as in
    ``by_head`` (product types; names are matched anywhere).
    """
    hits: set[str] = set()
    for run in runs:
        by_size: dict[int, set[tuple[str, ...]]] = {}
        for gram in grams([run], accented):
            by_size.setdefault(len(gram), set()).add(gram)
        keys = tuple(run if accented else (normalize(word) for word in run))
        for size in sorted(by_size, reverse=True):
            found = {key for key, words in candidates.items() if contains(words, by_size[size])}
            if found:
                hits |= by_head(found, candidates, by_size[size], head, keys[:size])
                break
    return hits


def phrase_hits(
    candidates: dict[str, tuple[str, ...]],
    run: tuple[str, ...],
    accented: bool,
    head_word: bool = True,
) -> set[str]:
    """Candidates for one run over the whole catalogue, where one word of a phrase is too weak.

    The longest matching phrase must cover two words of a multi-word run: "bếp mini" must not
    mean every "mini" product. Without such a phrase only the head word counts (Vietnamese noun
    phrases are head-first), and only where a product type starts with it ("Bếp cắm trại",
    not "Dao bếp"). Head matches are preferred as in ``by_head`` ("máy in": printers, not
    "Hộp mực máy in").
    """
    keys = tuple(run if accented else (normalize(word) for word in run))
    by_size: dict[int, set[tuple[str, ...]]] = {}
    for gram in grams([run], accented):
        by_size.setdefault(len(gram), set()).add(gram)
    for size in sorted(by_size, reverse=True):
        if size < min(2, len(keys)):
            break
        found = {key for key, words in candidates.items() if contains(words, by_size[size])}
        if found:
            return by_head(found, candidates, by_size[size], "prefer", keys[:size])
    if not head_word or len(keys) < 2 or len(keys[0]) < MIN_CHARS:
        return set()
    return {key for key, words in candidates.items() if words[:1] == keys[:1]}


def take_mentions(
    runs: list[tuple[str, ...]], index: dict[tuple[str, ...], set[str]]
) -> tuple[list[tuple[str, ...]], set[str]]:
    """Product names found in the runs (folded alias -> products), longest first, and what is
    left of the runs once the named words are taken out."""
    found: set[str] = set()
    left: list[tuple[str, ...]] = []
    for run in runs:
        keys = [normalize(word) for word in run]
        used = [False] * len(run)
        for size in range(len(run), 0, -1):
            for start in range(len(run) - size + 1):
                if any(used[start : start + size]):
                    continue
                hit = index.get(tuple(keys[start : start + size]))
                if hit:
                    found |= hit
                    used[start : start + size] = [True] * size
        piece: list[str] = []
        for word, taken in zip(run, used, strict=True):
            if taken:
                if piece:
                    left.append(tuple(piece))
                piece = []
            else:
                piece.append(word)
        if piece:
            left.append(tuple(piece))
    return left, found
