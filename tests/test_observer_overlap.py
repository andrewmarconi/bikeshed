from bikeshed.observer.overlap import PARTIAL, SHINGLE, THRESHOLDS, match, normalise, shingles

LONG = "we should meet at the park at noon and bring the blue blanket for everyone"


def test_normalise_ignores_case_and_punctuation():
    assert normalise("  Hello,  WORLD!\nIt's me. ") == "hello world it s me"


def test_shingles_need_enough_words():
    assert shingles("one two three four five") == frozenset()
    assert len(shingles("one two three four five six seven")) == 2


def test_an_identical_text_is_exact_whatever_its_length():
    assert match("Same.", "same") == "exact"
    assert match(LONG, LONG.upper()) == "exact"


def test_a_long_text_inside_a_longer_source_is_exact():
    assert match(LONG, "Agreed. " + LONG + " Also snacks.") == "exact"


def test_a_short_text_inside_a_source_is_not_a_match():
    assert match("bring the blanket", LONG) is None


def test_partial_at_the_threshold_and_below():
    words = [f"w{i}" for i in range(16)]           # 11 shingles of 6 words
    text = " ".join(words)
    source = " ".join(words[:11] + ["x", "y", "z", "q", "r"])  # shares w0..w10: 6 shingles of 11
    assert 6 / 11 >= PARTIAL and match(text, source) == "partial"
    source = " ".join(words[:10] + ["x", "y", "z", "q", "r", "s"])  # shares w0..w9: 5 of 11
    assert 5 / 11 < PARTIAL and match(text, source) is None


def test_an_empty_text_never_matches():
    assert match("", "anything") is None
    assert match("   ", "") is None


def test_the_thresholds_line():
    assert SHINGLE == 6 and PARTIAL == 0.5
    assert THRESHOLDS == "_Matches: exact, or partial when at least 50% of a text's 6-word sequences appear in one source._"
