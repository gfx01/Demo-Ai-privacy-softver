import random

import pytest

from privacy_hub import hu_ids as h


@pytest.mark.parametrize("gen, check", [
    (h.gen_taj, h.is_valid_taj),
    (h.gen_tax_id, h.is_valid_tax_id),
    (h.gen_adoszam, h.is_valid_adoszam),
    (lambda r: h.gen_bank_account(r, 2), h.is_valid_bank_account),
    (lambda r: h.gen_bank_account(r, 3), h.is_valid_bank_account),
    (h.gen_iban, h.is_valid_iban),
])
def test_generators_produce_valid_values(gen, check):
    rng = random.Random(42)
    for _ in range(500):
        assert check(gen(rng))


def test_taj_law_example_shape():
    # CDV per 1996. évi XX. tv. 2. melléklet: odd positions x3, even x7, sum mod 10
    first8 = "12345678"
    expected = (3 * 1 + 7 * 2 + 3 * 3 + 7 * 4 + 3 * 5 + 7 * 6 + 3 * 7 + 7 * 8) % 10
    assert h.taj_check_digit(first8) == expected
    assert h.is_valid_taj(first8 + str(expected))
    assert not h.is_valid_taj(first8 + str((expected + 1) % 10))
    assert h.is_valid_taj("123 456 78" + str(expected))


def test_single_digit_errors_are_detected():
    rng = random.Random(1)
    for gen, check in ((h.gen_taj, h.is_valid_taj), (h.gen_adoszam, h.is_valid_adoszam)):
        for _ in range(200):
            value = h.digits(gen(rng))
            pos = rng.randrange(8)  # digits covered by the check digit
            wrong = value[:pos] + str((int(value[pos]) + rng.randrange(1, 10)) % 10) + value[pos + 1 :]
            assert not check(wrong), (value, wrong)


@pytest.mark.parametrize("value, valid", [
    # hungarian-validators test vectors; 123456788 distinguishes the law's
    # "remainder" rule from a "10 - remainder" misreading seen in the wild.
    ("111111110", True), ("123456788", True), ("987654322", True), ("000000000", True),
    ("111 111 110", True), ("111-111-110", True), ("111-111 110", True),
    ("111111111", False), ("12345678", False), ("1234567890", False),
])
def test_taj_published_vectors(value, valid):
    assert h.is_valid_taj(value) is valid


@pytest.mark.parametrize("value, valid", [
    ("8071592153", True), ("8071592154", False), ("8071592152", False), ("8071582153", False), ("7071592153", False),
])
def test_tax_id_published_vectors(value, valid):
    assert h.is_valid_tax_id(value) is valid


def test_tax_id_structure():
    assert not h.is_valid_tax_id("1234567890")  # must start with 8
    rng = random.Random(3)
    v = h.gen_tax_id(rng)
    assert len(v) == 10 and v[0] == "8"


def test_adoszam_rejects_bad_county_and_vat_code():
    rng = random.Random(5)
    core = h.gen_vat_core(rng)
    assert h.is_valid_adoszam(f"{core}-2-42")
    assert not h.is_valid_adoszam(f"{core}-2-21")  # 21 is not a county code
    assert not h.is_valid_adoszam(f"{core}-0-42")


def test_iban_mod97_known_shape():
    rng = random.Random(9)
    iban = h.gen_iban(rng).replace(" ", "")
    assert len(iban) == 28 and iban.startswith("HU")
    broken = iban[:-1] + str((int(iban[-1]) + 1) % 10)
    assert not h.is_valid_iban(broken)


def test_provenance_lists_every_enforced_check():
    for key in ("HU_TAJ", "HU_TAX_ID", "HU_VAT_NUMBER", "HU_BANK_ACCOUNT", "IBAN_CODE"):
        assert h.PROVENANCE[key].status in {"law", "standard", "library_documented"}
    for key in ("HU_COMPANY_REG_NUMBER", "HU_ID_CARD", "HU_PERSONAL_ID", "HU_LICENSE_PLATE"):
        assert h.PROVENANCE[key].status == "format_only"
