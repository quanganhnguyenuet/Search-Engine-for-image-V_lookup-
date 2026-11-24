from visualseek_retrieval.pipeline import RELATION_ACCENTED, RELATION_FOLDED
from visualseek_retrieval.spotting import query_words
from visualseek_retrieval.text import normalize, surface
from visualseek_retrieval.type_request import Request, extract, longest_hits, phrase_hits


def run(query: str, spans: list[tuple[int, int]], entity_spans=()) -> Request:
    accented = surface(query) != normalize(query)
    return extract(
        query_words(query),
        spans,
        list(entity_spans),
        accented,
        RELATION_ACCENTED if accented else RELATION_FOLDED,
    )


def phrases(runs) -> list[str]:
    return [" ".join(r) for r in runs]


def test_type_next_to_a_brand_is_the_request():
    # "bugi Bosch": brand span is word 1.
    assert phrases(run("bugi Bosch", [(1, 2)]).include) == ["bugi"]
    assert phrases(run("Maxxis có những lốp nào", [(0, 1)]).include) == ["lốp"]


def test_relation_query_keeps_the_request_and_drops_the_anchor_descriptor():
    # "hãng làm nồi chiên không dầu Philips": the type describes the anchor, nothing is asked.
    assert not run("những sản phẩm khác của hãng làm nồi chiên không dầu Philips", [(11, 12)])
    # "còn có điện thoại nào khác" after the anchor is the request.
    asked = run("hãng của Redmi Note 11 còn có điện thoại nào khác", [(2, 5)], [(2, 5)])
    assert phrases(asked.include) == ["điện thoại"]
    asked = run("cùng hãng với vali Samsonite Lite-Box còn có những túi nào", [(4, 7)], [(4, 7)])
    assert phrases(asked.include) == ["túi"]
    # The type before the relation cue is the request.
    asked = run("phụ tùng khác cùng hãng với gương chiếu hậu Hyundai Stargazer", [(6, 11)])
    assert phrases(asked.include) == ["phụ tùng"]


def test_exclusion_phrase_is_not_a_request():
    asked = run("Yonex ngoài vợt tennis còn bán gì", [(0, 1)])
    assert phrases(asked.include) == []
    assert phrases(asked.exclude) == ["vợt tennis"]
    asked = run("ngoài lọc dầu Bosch ra thì Bosch còn sản phẩm nào cho ô tô", [(3, 4), (6, 7)])
    assert phrases(asked.include) == ["ô tô"]
    assert phrases(asked.exclude) == ["lọc dầu"]


def test_words_before_an_entity_describe_it():
    # "vali" is part of how the entity is called, not a type the user asks for.
    asked = run("ngoài Vali Roncato Box thì hãng sản xuất còn làm gì khác", [(2, 4)], [(2, 4)])
    assert not asked


def test_plain_brand_queries_ask_for_nothing():
    assert not run("đồ của thương hiệu VinFast", [(4, 5)])
    assert not run("sản phẩm Vittoria", [(2, 3)])
    assert not run("ảnh Schwalbe", [(1, 2)])


def test_camera_is_not_a_picture_filler():
    assert phrases(run("Canon còn có máy ảnh nào khác", [(0, 1)]).include) == ["máy ảnh"]


def test_folded_query_uses_folded_fillers():
    assert phrases(run("bugi Bosch va nhot Mobil cho o to", [(1, 2), (4, 5)]).include) == [
        "bugi",
        "nhot",
        "o to",
    ]


def test_longest_phrase_wins_over_its_words():
    candidates = {
        "ink": ("hộp", "mực", "máy", "in"),
        "camera": ("máy", "ảnh", "kỹ", "thuật", "số"),
        "scale": ("cân", "sức", "khỏe", "điện", "tử"),
        "phone": ("điện", "thoại", "thông", "minh"),
    }
    assert longest_hits(candidates, [("máy", "in")], True) == {"ink"}
    assert longest_hits(candidates, [("điện", "thoại")], True) == {"phone"}
    # No two-word hit: the words are tried one by one.
    assert longest_hits(candidates, [("máy", "chụp")], True) == {"ink", "camera"}
    assert longest_hits(candidates, [("xyz",)], True) == set()


def test_phrase_hits_needs_two_words_of_a_phrase_or_the_head_word_first():
    types = {
        "loa": ("loa", "bluetooth"),
        "chieu": ("máy", "chiếu", "mini"),
        "dao": ("dao", "làm", "bếp"),
        "bep": ("bếp", "gas"),
        "tai": ("tai", "nghe", "chống", "ồn"),
    }
    assert phrase_hits(types, ("tai", "nghe"), True) == {"tai"}
    assert phrase_hits(types, ("loa", "mini"), True) == {"loa"}
    assert phrase_hits(types, ("bếp", "mini"), True) == {"bep"}
    assert phrase_hits(types, ("bếp",), True) == {"bep"}  # the head, not "Dao làm bếp"
    assert phrase_hits(types, ("quạt", "mini"), True) == set()


def test_head_matches_win_and_modifier_matches_are_the_fallback():
    types = {
        "printer": ("máy", "in", "laser"),
        "toner": ("hộp", "mực", "máy", "in"),
        "plug": ("bugi", "ô", "tô"),
    }
    assert longest_hits(types, [("máy", "in")], True, head="prefer") == {"printer"}
    assert longest_hits(types, [("máy", "in")], True) == {"printer", "toner"}  # names: anywhere
    # "đồ ... cho ô tô": no type starts with "ô tô", the modifier match answers.
    assert longest_hits(types, [("ô", "tô")], True, head="prefer") == {"plug"}
    assert longest_hits(types, [("ô", "tô")], True, head="only") == set()
    # Only the cartridge holds "máy in" in a brand without printers: "only" refuses it.
    toner_only = {"toner": types["toner"]}
    assert longest_hits(toner_only, [("máy", "in")], True, head="only") == set()
