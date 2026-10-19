"""Rule checks for the Hungarian morphology (a sanity net, not the evaluation;
the evaluation uses independently authored gold forms, see eval/)."""

import pytest

from privacy_hub.masking.hungarian import HungarianMorphology, harmony, inflect
from privacy_hub.masking.morphology import Inflection

FORMS = [
    ("Kovács Péter", "INS", "Kovács Péterrel"),
    ("Kovács Péter", "DAT", "Kovács Péternek"),
    ("Kovács Péter", "ACC", "Kovács Pétert"),
    ("Szabó Anna", "DAT", "Szabó Annának"),
    ("Szabó Anna", "INS", "Szabó Annával"),
    ("Szabó Anna", "ESS", "Szabó Annaként"),
    ("Kovács", "INS", "Kováccsal"),
    ("Kovács", "ACC", "Kovácsot"),
    ("Nagy", "INS", "Naggyal"),
    ("Kiss", "INS", "Kiss-sel"),
    ("Mariann", "DAT", "Mariann-nak"),
    ("Nikolett", "ABL", "Nikolett-től"),
    ("Noémi", "DAT", "Noéminek"),
    ("Kornél", "DAT", "Kornélnak"),
    ("Kiss", "ACC", "Kisst"),
    ("Tóth", "INS", "Tóthtal"),
    ("Tóth", "ACC", "Tóthot"),
    ("Balázs", "INS", "Balázzsal"),
    ("Mihály", "INS", "Mihállyal"),
    ("Tóth Éva", "ALL", "Tóth Évához"),
    ("Dávid", "DAT", "Dávidnak"),
    ("Ágnes", "DAT", "Ágnesnek"),
    ("Zsófi", "DAT", "Zsófinak"),
    ("Ödön", "ALL", "Ödönhöz"),
    ("Budapest", "SUP", "Budapesten"),
    ("Győr", "SUP", "Győrön"),
    ("Zöldmező Kft.", "INS", "Zöldmező Kft.-vel"),
    ("Fehér és Társa Bt.", "ABL", "Fehér és Társa Bt.-től"),
    ("Alfa Zrt.", "ACC", "Alfa Zrt.-t"),
    ("Széchenyi", "INS", "Széchenyivel"),
    ("Ádám", "INS", "Ádámmal"),
    ("Papp", "INS", "Papp-pal"),
    ("Gábor", "TRA", "Gáborrá"),
]


@pytest.mark.parametrize("base, case, expected", FORMS)
def test_inflect(base, case, expected):
    assert inflect(base, case) == expected


@pytest.mark.parametrize("base, case, surface", FORMS)
def test_split_inverts_inflect_with_lexicon(base, case, surface):
    lexicon = {w for b, _, _ in FORMS for w in b.split() if not w.endswith(".")}
    morph = HungarianMorphology(lexicon=lexicon)
    etype = "HU_COMPANY" if base.endswith(".") else "PERSON"
    got_base, infl = morph.split(etype, surface)
    assert (got_base, infl.case) == (base, case)


@pytest.mark.parametrize("word", ["Zsolt", "Bernát", "Kovácsné", "Krisztián", "Erzsébet"])
def test_nominatives_are_not_split(word):
    morph = HungarianMorphology(lexicon={"Zsolt", "Bernát", "Krisztián", "Erzsébet"})
    assert morph.split("PERSON", word)[1].case is None


def test_unknown_stem_needs_unambiguous_suffix():
    morph = HungarianMorphology(lexicon={"Péter"})
    assert morph.split("PERSON", "Zoltánnal") == ("Zoltán", Inflection("INS", "nal"))
    assert morph.split("PERSON", "Zoltánt")[1].case is None  # bare -t: too ambiguous


def test_harmony():
    assert [harmony(w) for w in ("Anna", "Péter", "Ödön", "Dávid", "Lili", "Ágnes")] == [0, 1, 2, 0, 1, 1]


@pytest.mark.parametrize("raw, stem, case", [
    ("-nek", "", "DAT"), ("nak", "", "DAT"), ("-vel", "", "INS"), ("-val", "", "INS"), ("-t", "", "ACC"),
    ("-ot", "", "ACC"), ("rel", "Gábor", "INS"), ("ral", "Gábor", "INS"), ("-ról", "", "DEL"),
    ("ért", "", "CAU"), ("-ék", "", None),
])
def test_parse_suffix(raw, stem, case):
    assert HungarianMorphology().parse_suffix(raw, stem).case == case


def test_bracket_placeholder_suffix_and_restore():
    morph = HungarianMorphology()
    assert morph.placeholder_suffix(Inflection("INS", "rel")) == "-vel"
    assert morph.inflect("Kovács Péter", morph.parse_suffix("-nak"), "PERSON") == "Kovács Péternek"
    assert morph.inflect("Zöldmező Kft.", morph.parse_suffix("-nak"), "HU_COMPANY") == "Zöldmező Kft.-nek"
    # unknown ending: glued to names, hyphenated after abbreviations
    assert morph.inflect("Kovács Péter", morph.parse_suffix("-ék"), "PERSON") == "Kovács Péterék"
    assert morph.inflect("Zöldmező Kft.", morph.parse_suffix("-ék"), "HU_COMPANY") == "Zöldmező Kft.-ék"
