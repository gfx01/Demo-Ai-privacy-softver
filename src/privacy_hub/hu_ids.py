"""Hungarian identifier check digits and synthetic value generators.

Every algorithm carries a provenance record (``PROVENANCE``). Rule for this
project: a check digit is only *enforced* when its algorithm could be
confirmed from a law, official guidance or a well-documented library. All
other identifiers are matched by format only and marked
"format only, checksum unverified".

The generators produce synthetic values for tests, evaluation data and
pseudonyms. They are random numbers that satisfy the format (and the check
digit where one is implemented); they are not meant to resemble any person.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date

DIGITS = re.compile(r"\D")


@dataclass(frozen=True)
class Provenance:
    identifier: str
    status: str  # "law" | "standard" | "library_documented" | "format_only"
    source: str


PROVENANCE: dict[str, Provenance] = {
    "HU_TAJ": Provenance(
        "TAJ szám (social security number)",
        "law",
        "1996. évi XX. törvény, 2. számú melléklet: 9 digits, 9th digit (CDV) = (3·d1+7·d2+3·d3+7·d4+3·d5+7·d6+3·d7+7·d8) "
        "mod 10, no complement step. Read from two verbatim-identical unofficial GitHub mirrors of njt.jog.gov.hu "
        "(the official site was unreachable while building this) and cross-checked against the hungarian-validators "
        "test vectors.",
    ),
    "HU_TAX_ID": Provenance(
        "adóazonosító jel (personal tax ID)",
        "law",
        "1996. évi XX. törvény, 1. számú melléklet 3. pont: 10 digits, d1 = 8, d10 = (Σ i·d_i, i=1..9) mod 11; a "
        "sequence number giving remainder 10 is not issued. Read from unofficial mirrors of the NJT text; identical "
        "to validator.js (isTaxID huHuCheck) and loophp/tin, which cite the EC TIN algorithms specification.",
    ),
    "HU_VAT_NUMBER": Provenance(
        "adószám törzsszám (company tax number, first 8 digits)",
        "library_documented",
        "8-1-2 format; törzsszám check digit d8 = (10 − (9·d1+7·d2+3·d3+1·d4+9·d5+7·d6+3·d7) mod 10) mod 10. "
        "Same as python-stdnum stdnum.hu.anum and NAV Online Számla client-side validation code.",
    ),
    "HU_BANK_ACCOUNT": Provenance(
        "pénzforgalmi jelzőszám (bank account number, 2×8 or 3×8 digits)",
        "library_documented",
        "Weights 9,7,3,1 repeating; block 1 (digits 1-8) and block 2 (digits 9-16, or 9-24 for 3×8) each sum to "
        "0 mod 10. Quoted from 18/2009. (VIII. 6.) MNB rendelet annex in library comments (HungarianBankAccount, "
        "ibantools checkHungarianBBAN); the decree itself could not be fetched from this environment.",
    ),
    "IBAN_CODE": Provenance(
        "IBAN (HU: 28 characters)",
        "standard",
        "ISO 13616 / ISO 7064 MOD 97-10; HU length 28 per the SWIFT IBAN registry (via python-stdnum iban.dat).",
    ),
    "HU_COMPANY_REG_NUMBER": Provenance(
        "cégjegyzékszám (company registration number)", "format_only", "CC-FF-NNNNNN; no check digit is known."
    ),
    "HU_ID_CARD": Provenance(
        "személyi igazolvány okmányazonosító (ID card number)",
        "format_only",
        "6 digits + 2 letters (card format). Secondary sources only; no check digit known for the printed number.",
    ),
    "HU_PERSONAL_ID": Provenance(
        "személyi azonosító (personal identification number)",
        "format_only",
        "M-YYMMDD-SSSK. A check-digit rule exists in the law (1996. évi XX. tv. 3. melléklet) but could only be "
        "found in secondary sources here - format only, checksum unverified.",
    ),
    "HU_LICENSE_PLATE": Provenance("forgalmi rendszám", "format_only", "ABC-123 (until 2022), AA BC-123 (from 2022-07-01); no check digit."),
}


def digits(value: str) -> str:
    return DIGITS.sub("", value)


# --- TAJ ---------------------------------------------------------------------
def taj_check_digit(first8: str) -> int:
    return sum(int(d) * (3 if i % 2 == 0 else 7) for i, d in enumerate(first8)) % 10


def is_valid_taj(value: str) -> bool:
    d = digits(value)
    return len(d) == 9 and taj_check_digit(d[:8]) == int(d[8])


# --- adóazonosító jel ------------------------------------------------------------
EPOCH_1867 = date(1867, 1, 1)


def tax_id_check_digit(first9: str) -> int | None:
    remainder = sum((i + 1) * int(d) for i, d in enumerate(first9)) % 11
    return None if remainder == 10 else remainder


def is_valid_tax_id(value: str) -> bool:
    d = digits(value)
    if len(d) != 10 or d[0] != "8":
        return False
    check = tax_id_check_digit(d[:9])
    return check is not None and check == int(d[9])


# --- adószám ---------------------------------------------------------------------
_W9731 = (9, 7, 3, 1)


def _weighted_9731(ds: str) -> int:
    return sum(int(d) * _W9731[i % 4] for i, d in enumerate(ds))


def vat_core_check_digit(first7: str) -> int:
    return (10 - _weighted_9731(first7) % 10) % 10


# County codes accepted by NAV Online Számla validation: 02-20, 22-44, 51.
VAT_COUNTY_CODES = {f"{n:02d}" for n in [*range(2, 21), *range(22, 45), 51]}


def is_valid_vat_core(core8: str) -> bool:
    return len(core8) == 8 and core8.isdigit() and vat_core_check_digit(core8[:7]) == int(core8[7])


def is_valid_adoszam(value: str) -> bool:
    d = digits(value)
    if len(d) == 8:
        return is_valid_vat_core(d)
    return len(d) == 11 and is_valid_vat_core(d[:8]) and d[8] in "123456789" and d[9:] in VAT_COUNTY_CODES


# --- bank account (GIRO) -------------------------------------------------------------
def _block_ok(block: str) -> bool:
    return _weighted_9731(block) % 10 == 0


def is_valid_bank_account(value: str) -> bool:
    d = digits(value)
    if len(d) not in (16, 24):
        return False
    return _block_ok(d[:8]) and _block_ok(d[8:])


# --- IBAN ---------------------------------------------------------------------------
def iban_check_digits(country: str, bban: str) -> str:
    rearranged = bban + country + "00"
    numeric = "".join(str(int(c, 36)) for c in rearranged.upper())
    return f"{98 - int(numeric) % 97:02d}"


def is_valid_iban(value: str) -> bool:
    v = re.sub(r"\s", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}", v):
        return False
    if v.startswith("HU") and len(v) != 28:
        return False
    numeric = "".join(str(int(c, 36)) for c in v[4:] + v[:4])
    return int(numeric) % 97 == 1


# --- generators (synthetic data only) ---------------------------------------------
def gen_taj(rng: random.Random) -> str:
    first8 = f"{rng.randrange(10**7, 10**8):08d}"
    d = first8 + str(taj_check_digit(first8))
    return f"{d[:3]} {d[3:6]} {d[6:]}"


def gen_tax_id(rng: random.Random, born: date | None = None) -> str:
    born = born or date(rng.randrange(1940, 2005), rng.randrange(1, 13), rng.randrange(1, 29))
    while True:
        first9 = f"8{(born - EPOCH_1867).days:05d}{rng.randrange(0, 1000):03d}"
        check = tax_id_check_digit(first9)
        if check is not None:
            return first9 + str(check)


def gen_vat_core(rng: random.Random) -> str:
    first7 = f"{rng.randrange(10**6, 10**7):07d}"
    return first7 + str(vat_core_check_digit(first7))


def gen_adoszam(rng: random.Random) -> str:
    county = rng.choice(sorted(VAT_COUNTY_CODES))
    return f"{gen_vat_core(rng)}-{rng.choice('123')}-{county}"


def _gen_block(rng: random.Random, n: int, prefix: str = "") -> str:
    body = prefix + "".join(str(rng.randrange(10)) for _ in range(n - 1 - len(prefix)))
    return body + str((10 - _weighted_9731(body) % 10) % 10)


def gen_bank_account(rng: random.Random, blocks: int = 2) -> str:
    first = _gen_block(rng, 8)
    if blocks == 2:
        return f"{first}-{_gen_block(rng, 8)}"
    second = _gen_block(rng, 16)
    return f"{first}-{second[:8]}-{second[8:]}"


def gen_iban(rng: random.Random) -> str:
    acct = digits(gen_bank_account(rng, blocks=3))
    iban = "HU" + iban_check_digits("HU", acct) + acct
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def gen_company_reg(rng: random.Random) -> str:
    return f"Cg. {rng.randrange(1, 21):02d}-{rng.choice(['09', '06', '10', '07', '02'])}-{rng.randrange(0, 10**6):06d}"


def gen_id_card(rng: random.Random) -> str:
    letters = "ABCDEFGHIJKLMNOPRSTUVZ"
    return f"{rng.randrange(0, 10**6):06d}{rng.choice(letters)}{rng.choice(letters)}"
