"""Strategy (b): consistent, plausible pseudonyms.

"Kovács Péterrel" becomes e.g. "Molnár Gáborral": the model sees natural
Hungarian and inflects the fake name like any other name; restoration finds
the fake name in any case form (and its parts on their own: "Gábornak",
"Molnár úr") and re-inflects the original.

Design choices
* Fake given names keep the gender of the original when it is known.
* Pools contain only names with regular, unambiguous inflection: no final
  a/e (lengthening), no final digraphs or old spellings, no surnames that are
  also common words in legal/medical prose ("Bíró", "Fehér", "Nagy", ...).
  This keeps restoration of the *fake* forms reliable.
* Choice is deterministic for a given key (HMAC), so the preview a user sees
  equals what is sent, and the same person gets the same pseudonym across the
  turns of a conversation. Trade-off: a stable key makes requests linkable
  for the provider; rotate PRIVACY_HUB_PSEUDONYM_KEY to limit linkability.
* Types without a safe plausible substitute (places, amounts, dates, ...)
  fall back to bracket tokens.
* Fake e-mails use the reserved example.com domain (RFC 2606); fake
  identifiers are synthetic values with valid check digits.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import random
import re
from collections.abc import Callable, Iterator

from .. import hu_ids
from .model import Mapping, MappingEntry
from .morphology import Inflection, Morphology
from .restore import HYPHENS, LETTERS, MAX_SUFFIX_LEN, BracketRestorer, Restorer, RestoreMatch
from .strategies import BracketStrategy, Strategy

# Regular-stem names only: no final a/e, digraph, double consonant or old spelling, at least 5
# letters for surnames (short stems prefix-match other words: "Kun" ~ "Kunság"),
# no common words or well-known public figures.
SURNAMES = (
    "Molnár", "Fodor", "Sipos", "Halmi", "Somogyi", "Hegedűs", "Pintér", "Fülöp", "Bodnár", "Antal",
    "Kardos", "Bogdán", "Szalai", "Kelemen", "Szilágyi", "Bánfi", "Rédei", "Kántor", "Szekeres", "Bartos",
    "Dobos", "Berényi", "Járai", "Kocsis",
)
MALE = (
    "Gábor", "Tamás", "Zoltán", "Ádám", "Bálint", "Dániel", "Márton", "Ákos", "Dávid", "Miklós",
    "Lajos", "Viktor", "Norbert", "Krisztián", "Kristóf", "Árpád",
)
FEMALE = (
    "Judit", "Edit", "Ildikó", "Enikő", "Ingrid", "Erzsébet", "Margit", "Ágnes", "Eszter", "Kitti",
    "Vivien", "Andi", "Zsuzsi", "Barbi", "Jázmin", "Klári",
)
# Name-forming endings that may be re-attached to a restored name.
NAME_ENDINGS = {"né", "ék", "éké", "né-", "éknak", "éknek", "éknál", "éknél", "ékkal", "ékkel", "éktől", "ékhez", "ékhoz"}
COMPANY_WORDS = (
    "Aranyhíd", "Kékmadár", "Ezüstfenyő", "Napkorong", "Tölgyliget", "Hársmező", "Fenyőpart", "Gyöngyház",
    "Vadgesztenye", "Szélmalom", "Kőhíd", "Ködvár", "Sasfészek", "Diófás", "Mandulavirág",
)
STREETS = ("Minta utca", "Próba utca", "Példa tér", "Teszt köz")
LEGAL_FORM_RE = re.compile(r"\s+((?:Nonprofit\s+)?(?:Kft|Zrt|Nyrt|Bt|Kkt|Rt)\.?)$", re.I)

BRACKET_TYPES = {"LOCATION", "MONEY", "HU_BIRTH_DATE", "DATE_TIME", "URL", "IP_ADDRESS", "NRP", "CREDIT_CARD",
                 "HU_PERSONAL_ID", "HU_LICENSE_PLATE", "HU_ID_CARD", "HU_COMPANY_REG_NUMBER"}
PERSON_TYPES = {"PERSON", "HU_PERSON"}
ORG_TYPES = {"HU_COMPANY", "ORGANIZATION"}


def _key() -> bytes:
    # Must be secret: with a known key, anyone could hash a list of common
    # names and find which real name a pseudonym stands for.
    raw = os.environ.get("PRIVACY_HUB_PSEUDONYM_KEY", "")
    if len(raw) < 16:
        raise RuntimeError("PRIVACY_HUB_PSEUDONYM_KEY must be set (>= 16 random characters)")
    return hashlib.sha256(("pseudonym|" + raw).encode()).digest()


class PseudonymStrategy(Strategy):
    name = "pseudonym"

    def __init__(self, gender_of: Callable[[str], str | None] | None = None, key: bytes | None = None) -> None:
        self._gender_of = gender_of or (lambda _given: None)
        self._key = key  # loaded lazily: restoration (in the gateway) does not need it
        self._bracket = BracketStrategy()

    def _rng(self, entity_type: str, original: str) -> random.Random:
        if self._key is None:
            self._key = _key()
        digest = hmac.new(self._key, f"{entity_type}|{original.casefold()}".encode(), hashlib.sha256).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    def new_entry(self, key, entity_type, original, avoid, morphology):
        if entity_type in BRACKET_TYPES:
            return self._bracket.new_entry(key, entity_type, original, avoid, morphology)
        rng = self._rng(entity_type, original)
        for _ in range(200):
            replacement, parts = self._candidate(entity_type, original, rng)
            if replacement is None:
                return self._bracket.new_entry(key, entity_type, original, avoid, morphology)
            norm_parts = {p[0].casefold() for p in parts} | {replacement.casefold()}
            if not any(n in a for n in norm_parts for a in avoid):
                return MappingEntry(key, entity_type, original, replacement, parts)
        return self._bracket.new_entry(key, entity_type, original, avoid, morphology)

    def _candidate(self, entity_type: str, original: str, rng: random.Random) -> tuple[str | None, list[list[str]]]:
        if entity_type in PERSON_TYPES:
            return self._person(original, rng)
        if entity_type in ORG_TYPES:
            m = LEGAL_FORM_RE.search(original)
            form = m.group(1) if m else ""
            word = rng.choice(COMPANY_WORDS)
            name = f"{word} {form}".strip() if form else f"{word} Csoport"
            return name, [[word, original[: m.start()] if m else original]]
        if entity_type == "EMAIL_ADDRESS":
            return f"{rng.choice(('kapcsolat', 'iroda', 'info', 'ugyfel'))}{rng.randrange(10, 99)}@example.com", []
        if entity_type in ("PHONE_NUMBER", "HU_PHONE_NUMBER"):
            return f"+36 30 000 {rng.randrange(0, 10000):04d}", []
        generators = {
            "HU_TAJ": hu_ids.gen_taj,
            "HU_TAX_ID": hu_ids.gen_tax_id,
            "HU_VAT_NUMBER": hu_ids.gen_adoszam,
            "HU_BANK_ACCOUNT": lambda r: hu_ids.gen_bank_account(r, 3 if len(hu_ids.digits(original)) == 24 else 2),
            "IBAN_CODE": hu_ids.gen_iban,
        }
        if entity_type in generators:
            return generators[entity_type](rng), []
        if entity_type == "HU_ADDRESS":
            return f"1011 Budapest, {rng.choice(STREETS)} {rng.randrange(1, 99)}.", []
        return None, []

    def _person(self, original: str, rng: random.Random) -> tuple[str, list[list[str]]]:
        words = original.split()
        if len(words) == 1:  # given name or surname alone
            gender = self._gender_of(words[0])
            if gender:
                fake = rng.choice(MALE if gender == "male" else FEMALE)
            else:
                fake = rng.choice(SURNAMES)
            return fake, [[fake, words[0]]]
        # Every word is replaced (middle names too): known given names by a
        # given name of the same gender, other words by a surname. Without any
        # known given name, Hungarian order is assumed (surname first).
        # Titles and initials ("dr.", "ifj.", "P.") are kept as they are.
        kept = [w.endswith(".") or not any(c.isalpha() for c in w) for w in words]
        genders: list[str | None] = [None if k else self._gender_of(w) for w, k in zip(words, kept)]
        known = [g for g in genders if g]
        person_gender = known[-1] if known else None
        if not known:
            first = next((i for i, k in enumerate(kept) if not k), 0)
            genders = [None if i <= first else "given" for i in range(len(words))]
        out: list[str] = []
        parts: list[list[str]] = []
        used: set[str] = set()
        for word, keep, gender in zip(words, kept, genders):
            if keep:
                out.append(word)
                continue
            if gender is None:
                pool = SURNAMES
            else:
                g = person_gender if gender == "given" else gender
                pool = MALE if g == "male" else FEMALE if g == "female" else MALE + FEMALE
            fake = rng.choice([p for p in pool if p not in used] or list(pool))
            used.add(fake)
            out.append(fake)
            parts.append([fake, word])
        return " ".join(out), parts

    def render(self, entry, inflection, morphology):
        if entry.replacement.startswith("["):
            return self._bracket.render(entry, inflection, morphology)
        if inflection.case or inflection.raw:
            return morphology.inflect(entry.replacement, inflection, entry.entity_type)
        return entry.replacement

    def restorer(self, mapping: Mapping, morphology: Morphology) -> Restorer:
        return PseudonymRestorer(mapping, morphology)


class PseudonymRestorer(Restorer):
    """Finds fake values (full or partial names, any case form) and puts the
    originals back, re-inflected; bracket fallbacks are restored as usual."""

    def __init__(self, mapping: Mapping, morphology: Morphology) -> None:
        super().__init__(mapping, morphology)
        self._bracket = BracketRestorer(
            Mapping(mapping.strategy, mapping.language, [e for e in mapping.entries if e.replacement.startswith("[")]),
            morphology,
        )
        # surface stem -> (entry, original part or None for the whole value, upper-case form?)
        self._forms: dict[str, tuple[MappingEntry, str | None, bool]] = {}
        for e in mapping.entries:
            if e.replacement.startswith("["):
                continue
            self._forms[e.replacement] = (e, None, False)
            if e.entity_type in PERSON_TYPES or e.entity_type in ORG_TYPES:
                for fake, orig in e.parts:
                    self._forms.setdefault(fake, (e, orig, False))
        # "KOVÁCS PÉTER" style (letter heads, tables): upper-case variants of names
        for form, (e, orig, _) in list(self._forms.items()):
            if e.entity_type in PERSON_TYPES | ORG_TYPES and form.upper() != form:
                self._forms.setdefault(form.upper(), (e, orig, True))
        if self._forms:
            alt = "|".join(re.escape(f) for f in sorted(self._forms, key=len, reverse=True))
            self._re: re.Pattern[str] | None = re.compile(
                rf"(?<![\w@.])(?P<stem>{alt})(?P<suf>[{HYPHENS}]?[{LETTERS}]{{0,{MAX_SUFFIX_LEN}}})(?![\w@])"
            )
            longest = max(len(f) for f in self._forms)
        else:
            self._re = None
            longest = 0
        # extra slack for stem changes ("Annának") and suffixes
        self.max_match_len = max(self._bracket.max_match_len, longest + MAX_SUFFIX_LEN + 4)

    def _inflected_variants(self, stem: str) -> dict[str, str]:
        """Surface forms of a fake value -> case, generated with the same rules
        that produced them (used when the model's form changes the stem)."""
        from .hungarian import CASES, inflect

        return {inflect(stem, c): c for c in CASES}

    def iter_matches(self, text: str, pos: int = 0) -> Iterator[RestoreMatch]:
        matches: list[RestoreMatch] = list(self._bracket.iter_matches(text, pos))
        if self._re is not None:
            for m in self._re.finditer(text, pos):
                stem, suf = m.group("stem"), m.group("suf")
                entry, original_part, upper = self._forms[stem]
                target = original_part or entry.original
                if upper:
                    suf = suf.lower()
                    stem = stem.title()
                inflection = self.morphology.parse_suffix(suf, stem) if suf else Inflection()
                if suf and inflection.case is None:
                    case = self._inflected_variants(stem).get(stem + suf)
                    if case is None and suf.lstrip(HYPHENS) not in NAME_ENDINGS:
                        continue  # some other word that starts like the fake value: leave it alone
                    inflection = Inflection(case, suf)
                replacement = self.morphology.inflect(target, inflection, entry.entity_type)
                if upper:
                    replacement = replacement.upper()
                matches.append(RestoreMatch(m.start(), m.end(), entry, replacement))
        matches.sort(key=lambda r: (r.start, -(r.end - r.start)))
        last_end = -1
        for r in matches:
            if r.start >= last_end:
                yield r
                last_end = r.end
