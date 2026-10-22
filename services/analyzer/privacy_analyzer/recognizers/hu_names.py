"""Gazetteer-based Hungarian person-name recognizer.

Uses the official list of registrable Hungarian given names, common
nicknames, frequent surnames and common foreign given names (see
fetch_lexicon.py) together with Hungarian name order (surname first) and the
rule-based morphology, so inflected forms are found:

* "Kovács Péterrel", "Évának", "dr. Tóth Gáborral"
* married names: "Kovácsné Tóth Éva", "Fehér Andrásné", "Kissnének"
* family forms: "Kovácsékkal", "Szűcséknek"
* a known surname inflected mid-sentence: "Kissnek szóltam"
* foreign names inside multi-word runs: "Thomas Becker", "Becker Thomast"
* lower-case chat style: after a greeting ("szia peti"), as a known
  surname + given name pair, also without accents ("horváth tamás",
  "kovacs petinek"), and lower-case given names that are not ordinary
  words (needs common_words.txt in the lexicon directory)
* capitalised names typed without accents, also inflected, in married or
  family form ("Reka", "Halasz Tibornal", "Kardos Tamasne", "Biroekhoz");
  OCR output loses accents the same way
* a capitalised name after a title or salutation even if the surname is
  unknown ("Mr. O'Brien", "Tisztelt Bencze Úr", "dr. Rozsnyainak")
* repeated parts of a name found elsewhere in the same text
  ("Thomas Becker ... Beckerrel")

Works without any statistical NER model and complements one when present.
Known gaps: ambiguous given names that are common words at the start of a
sentence ("Rózsa", "Virág"), initials ("K. Péter"), unknown surnames
without any cue.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult

from privacy_hub.masking.hungarian import HungarianMorphology

# incl. Latin Extended-A/B and Additional: ő ű ć č š ž ł ń, Romanian ș ț, Vietnamese
LETTER = "A-Za-zÀ-ÖØ-öø-ÿ\u0100-\u024f\u1e00-\u1eff"
TOKEN_RE = re.compile(rf"[{LETTER}][{LETTER}'’\-]*")
MAX_TOKEN = 40  # longer "words" are not names (and would make the word analysis slow)
TITLE_WORDS = {"dr", "ifj", "id", "özv", "prof", "néhai", "vitéz", "mr", "mrs", "ms", "miss", "sir", "herr", "frau"}
FOREIGN_ENDING = re.compile(
    r"(?:-?(?:t|ot|et|öt|nak|nek|val|vel|al|el|ra|re|ról|ről|tól|től|nál|nél|hoz|hez|höz|ért|ként|é|ék)|"
    r"(?<=s)szal|(?<=s)szel|(?<=r)ral|(?<=r)rel|(?<=n)nal|(?<=n)nel)$"
)
ACADEMIC_ABBREVIATIONS = {"med", "univ", "jur", "habil", "phil", "techn", "oec", "pharm", "vet", "dent"}
TITLE_BEFORE = re.compile(r"(?:\b(?:dr|ifj|id|özv|prof|mr|mrs|ms)\.?)\s*$", re.I)
INITIAL_BEFORE = re.compile(r"(?<![\w.])[A-ZÁÉÍÓÖŐÚÜŰ]\.\s")
INITIAL_AFTER = re.compile(r" [A-ZÁÉÍÓÖŐÚÜŰ]\.(?![\w.])")
# Frequent surnames that are also ordinary words (occupations, adjectives)
WORD_SURNAMES = {
    "Kertész", "Vadász", "Takács", "Juhász", "Molnár", "Kovács", "Szabó", "Lakatos", "Mészáros", "Bíró",
    "Pásztor", "Fazekas", "Kocsis", "Szűcs", "Varga", "Kádár", "Asztalos", "Ács", "Kántor", "Katona", "Papp",
    "Pap", "Gulyás", "Csizmadia", "Borbély", "Szakács", "Halász", "Csordás", "Kalmár", "Tímár", "Bognár",
    "Erdős", "Fodor", "Fekete", "Fehér", "Nagy", "Kis", "Kiss", "Barna", "Szőke", "Vörös", "Sárközi", "Somogyi",
    "Kun", "Német", "Németh", "Orosz", "Tóth", "Horváth", "Oláh", "Rácz", "Lengyel", "Magyar", "Székely",
    "Pintér", "Kerekes", "Szekeres", "Kardos", "Bodnár", "Szénási", "Mohácsi", "Vass", "Farkas", "Balogh",
    # animals, occupations and other ordinary words that are established surnames
    "Gombos", "Rigó", "Bárány", "Torma", "Péntek", "Szombat", "Holló", "Sólyom", "Sas", "Csóka", "Szarka", "Varjú",
    "Galamb", "Fecske", "Medve", "Róka", "Kakas", "Csuka", "Csiszár", "Varró", "Szántó", "Kalapos", "Dobos", "Ötvös",
    "Csikós", "Huszár", "Barát", "Cseh", "Szász", "Polgár", "Gróf", "Hegyi", "Erdei", "Kecskés", "Borsos", "Gazdag",
    "Sánta", "Hajdú", "Darázs",
}
# Stricter, for lower-case words: a Hungarian title written with its dot ("dr. rozsnyainak"),
# not e.g. "koponya-MR szükségességét"
TITLE_DOT_BEFORE = re.compile(r"(?<![\w-])(?:[Dd]r|ifj|id|özv|[Pp]rof)\.\s*$")
# Lower-case words that look like nicknames but are slang ("szabin" = on leave)
LOWER_NOT_NAMES = {"szabi", "nala", "nalunk", "nalatok", "naluk", "rola", "tole", "tolem"}  # + "nála" typed without accents
HONORIFICS = re.compile(r"^\s+(?:úr|úrnak|urat|úrral|úrtól|úrhoz|urak|asszony|asszonynak|asszonyt|asszonnyal|"
                        r"kisasszony|doktor|doktornő|doktorúr|ügyvéd|ügyvédnő|tanár|tanárnő|néni|bácsi)\b", re.I)
GREETING = re.compile(r"(?:^|[\s,(„\"“'])(?:szia|szió|sziasztok|helló|hello|hali|hi|hey|kedves|üdv|szervusz|csá|csao|ciao)[\s,!]*$", re.I)
# Words after a name that turn it into a street / institution name.
# (Prefix match: the street word itself may be inflected, "utcában", "téren".)
NOT_A_PERSON_AFTER = re.compile(
    r"^\s*-?(?:utc[aá]|útj|út\b|úto|úti|tér|ter[eé]|körút|köz\b|köz[eéb]|sor\b|sor[aáo]|fasor|sétány|"
    r"rakpart|park\b|park[ob]|liget|híd|hídon|gimnázium|általános|iskol|kórház|egyetem|kollégium|intézet|"
    r"alapítvány|társaság|díj|emlékérem|kft|zrt|bt\b|nyrt|kkt|ügyvédi|u\.|"
    # group, level and package names: "a Katica csoport", "Arany szintű támogatás"
    r"csoport|szint|csomag|kategóri|fokozat|tagság|érem|"
    # "a Csillag és a Virág csoport": both are group names
    r"és\s+(?:a\s+|az\s+)?\w+\s+csoport)",
    re.I,
)
# Capitalised words that start sentences or greetings but are not surnames.
STOPWORDS = {
    "A", "Az", "Egy", "Ma", "Tegnap", "Holnap", "Kedves", "Tisztelt", "Kérem", "Kérlek", "Szia", "Sziasztok",
    "Szervusz", "Helló", "Hello", "Hi", "Üdv", "Üdvözlettel", "Köszönöm", "Köszönjük", "Ezért", "Továbbá",
    "Valamint", "Mivel", "Hogy", "Ha", "Mert", "És", "De", "Sajnos", "Remélem", "Amikor", "Mint", "Ön", "Önök",
    "Re", "Fw", "Tárgy", "Ügyfél", "Partner", "Megrendelő", "Vállalkozó", "Bérlő", "Bérbeadó", "Eladó", "Vevő",
    "Beteg", "Páciens", "Panaszos", "Felperes", "Alperes", "Meghatalmazó", "Meghatalmazott", "Munkavállaló",
    "Munkáltató", "Név", "Neve", "Írj", "Foglald", "Fogalmazz", "Készíts", "Segíts", "Nézd", "Ellenőrizd",
    "Javítsd", "Fordítsd", "Válaszolj", "Küldd", "Hívd", "Szerda", "Hétfő", "Kedd", "Csütörtök", "Péntek",
    "Szombat", "Vasárnap", "Január", "Február", "Március", "Április", "Május", "Június", "Július", "Augusztus",
    "Szeptember", "Október", "November", "December", "Budapest", "Magyarország", "Debrecen", "Szeged", "Pécs",
    "Győr", "Miskolc", "Utca", "Cím", "Telefon", "Email", "Tel", "Mobil", "Dátum", "Ügyszám", "Iktatószám",
    "Dear", "Hallo", "Liebe", "Lieber", "Sehr", "Mr", "Mrs", "Ms", "Dr",
}
# After "Tisztelt" / "Kedves" these are roles, not names.
SALUTATION_NOUNS = {
    "Ügyfelünk", "Ügyfél", "Ügyfeleink", "Kolléga", "Kollégák", "Kollégáim", "Kollégám", "Hölgyem", "Uram", "Uraim",
    "Partnerünk", "Partnerünket", "Partnereink", "Szülők", "Szülő", "Szülőtárs", "Lakók", "Lakótárs", "Lakótársak",
    "Tulajdonosok", "Tulajdonostársak", "Bérlő", "Bérlők", "Vevő", "Vevőnk", "Vásárlónk", "Előfizető", "Előfizetőnk",
    "Felhasználó", "Doktor", "Doktornő", "Ügyvéd", "Ügyvédnő", "Igazgató", "Igazgatónő", "Asszonyom", "Címzett",
    "Csapat", "Mindenki", "Barátaim", "Tanár", "Tanárnő", "Elnök", "Polgármester", "Jegyző", "Bíróság", "Hivatal",
    "Cégvezető", "Vezetőség", "Munkatárs", "Munkatársak", "Pácienseink", "Páciens", "Résztvevők", "Jelentkező",
    "Pályázó", "Kérelmező", "Adózó", "Ügyintéző", "Hallgatók", "Tagok", "Testület", "Képviselő", "Képviselőtestület",
    "Ügyfélszolgálat", "Ügyfélszolgálatunk", "Hölgyeim", "Hatóság", "Adóhatóság", "Kormányhivatal", "Munkáltató",
    "Bizottság", "Közgyűlés", "Kuratórium", "Igazgatóság", "Osztály", "Iroda", "Irodánk", "Kollégium", "Szerkesztőség",
    "Bíró", "Bírónő", "Főorvos", "Főorvosnő", "Kancellária", "Titkárság", "Ügyvédi", "Társasház", "Közös",
    "Tisztviselő", "Felelős", "Vezető", "Biztos", "Titkár", "Szervezet", "Szolgálat", "Kar", "Tanszék", "Intézet",
}
SALUTATION = re.compile(r"(?:^|[\s,(„\"“'])(?:tisztelt|kedves|t\.)\s*$", re.I)
# Day and month names are lower case inside a Hungarian sentence; capitalised
# there and followed by a given name they are surnames ("kollégámat, Péntek Ritát").
CALENDAR_WORDS = {
    "Hétfő", "Kedd", "Szerda", "Csütörtök", "Péntek", "Szombat", "Vasárnap", "Január", "Február", "Március",
    "Április", "Május", "Június", "Július", "Augusztus", "Szeptember", "Október", "November", "December",
}
# Name particles between a foreign given name and the surname ("Jeroen de Vries",
# "Ursula von der Leyen"). Not "le", "el": those are Hungarian verb prefixes.
PARTICLES = {"de", "van", "von", "der", "den", "del", "della", "di", "da", "du", "ten", "ter", "zu", "dos", "das", "af"}
# Surname shapes that do not occur in Hungarian words: "O'Connor", "McGregor",
# "Johansson", "Nováková", "Kowalski", "Popescu", "Petrović" (also inflected)
FOREIGN_SURNAME = re.compile(
    r"^(?:O[’']|D[’']|Mc(?=[A-Z])|Mac(?=[A-Z])|Fitz(?=[a-z]))|[a-z][’'][A-Z]|"
    r"(?:sson|ová|ski|ska|sky|cki|cka|wicz|escu|eanu|vić|vič|ović|enko|mann)$"
)
# A run starting with a vehicle make is a model name ("Škoda Octavia", "Dacia Logan", "Opel Adam")
VEHICLE_MAKES = {
    "Škoda", "Skoda", "Opel", "Ford", "Renault", "Peugeot", "Citroën", "Citroen", "Toyota", "Suzuki", "Volkswagen",
    "VW", "Audi", "BMW", "Mercedes", "Mercedes-Benz", "Kia", "Hyundai", "Dacia", "Fiat", "Seat", "Cupra", "Volvo",
    "Nissan", "Mazda", "Honda", "Tesla", "Mitsubishi", "Subaru", "Lexus", "Porsche", "Jeep", "Chevrolet", "Lada",
    "Alfa", "Jaguar", "Iveco", "Scania", "DAF", "MAN", "Smart", "Mini", "Dodge", "Chrysler", "Isuzu", "Ssangyong",
}
# Tesseract confusions in Hungarian names: "rn" read as "m" (Bamabás), "ő" as
# "o", "e" or "6" (Lerinc), "ű" as "u", "ü" as "ii", "í" as "i" or "l"
OCR_SWAPS = (("m", "rn"), ("rn", "m"), ("o", "ő"), ("e", "ő"), ("é", "ő"), ("6", "ő"), ("u", "ű"), ("ii", "ü"),
             ("i", "í"), ("l", "í"), ("0", "o"))
# Legal case names: "a Pálfi kontra Vasvári ügy"
VERSUS = re.compile(r"\s+(?:kontra|vs\.?|v\.)\s+$")
VERSUS_AFTER = re.compile(r"^\s+(?:kontra|vs\.?|v\.)\s+")
# Endings after the family suffix -ék: "Kovácsékkal", "Kisséknél", "Tóthékat"
FAMILY_RE = re.compile(
    r"^(.{2,}?)ék(?:at|et|nak|nek|kal|kel|nál|nél|hoz|hez|höz|tól|től|ért|ra|re|ról|ről|on|en|ön|ban|ben|ba|be|"
    r"ból|ből|ig|é|éi|ként)?$"
)
# Possessive -é, also with a case ending: "Szabó Gáborét", "Annáéval", "Kovácséhoz"
POSSESSIVE_RE = re.compile(
    r"^(.{2,}?)é(?:t|nak|nek|val|vel|ról|ről|tól|től|hoz|hez|höz|nál|nél|ban|ben|ba|be|ból|ből|ra|re|ért|ig|i|it|ik|ként)?$"
)
# Old spelling of a surname ("Várady", "Kováts", "Tóth", "Rácz"), lower case and
# optionally inflected: "váradynál", "tóthtal", "rácznak"
OLD_SPELLING_LOWER = re.compile(
    r"^(?P<stem>[a-záéíóöőúüű]{2,}(?:th|cz|ts|[bcdfhkmprsvz]y|[aeo]y))"
    r"(?P<end>nak|nek|nál|nél|val|vel|tal|tel|hoz|hez|höz|tól|től|ra|re|ról|ről|t|ot|et|é|ék|né|nét|ban|ben|ba|be|"
    r"ból|ből|ért|ig)?$"
)
# Words that follow a person's name but never an attributive adjective: with an
# earlier full name, "Szabó szerint" at the start of a sentence is the person.
NAME_FOLLOWERS = {
    "szerint", "is", "pedig", "viszont", "ugyanis", "szintén", "sem", "azt", "ezt", "már", "még", "nem", "mondta",
    "írta", "kérte", "jelezte", "szólt", "hívott", "telefonált", "válaszolt", "küldte", "aláírta",
}
# Registrable given names that are also common words; on their own and at
# the start of a sentence they are not treated as names.
AMBIGUOUS_GIVEN = {
    "Alma", "Arany", "Áron", "Barna", "Bátor", "Bodza", "Boróka", "Bíbor", "Csillag", "Gyöngy", "Győző",
    "Hajnal", "Hanga", "Ibolya", "Jácint", "Kamilla", "Kincs", "Liliom", "Menta", "Napsugár", "Nemes",
    "Remény", "Rózsa", "Szende", "Szilárd", "Tavasz", "Viola", "Virág", "Vidor", "Vitéz", "Zsálya", "Mária",
    "Dalma", "Ernő", "Kende", "Hunor", "Bende", "Kis", "Nagy", "Fehér", "Fekete",
}
# Common Hungarian nicknames (most are not on the official list).
NICKNAMES = {
    "Peti", "Pisti", "Laci", "Feri", "Józsi", "Jóska", "Jani", "Janika", "Zoli", "Gabi", "Tomi", "Andris", "Dani",
    "Gergő", "Misi", "Miki", "Robi", "Zsolti", "Csabi", "Ricsi", "Krisz", "Kriszti", "Zsuzsi", "Kati", "Erzsi",
    "Marcsi", "Juci", "Ili", "Ica", "Bori", "Évi", "Ani", "Anni", "Panni", "Fruzsi", "Eszti", "Moni", "Niki",
    "Viki", "Dóri", "Rebi", "Nóri", "Szandi", "Bogi", "Reni", "Orsi", "Timi", "Klári", "Gizi", "Juli", "Lali",
    "Bandi", "Sanyi", "Pityu", "Öcsi", "Balu", "Gazsi", "Lacika", "Ferike", "Petike", "Zsófi", "Dorka", "Zsuzsa",
    "Marika", "Ági", "Évike", "Kata", "Detti", "Betti", "Pali", "Tibi", "Gyuri", "Matyi", "Marci", "Barni",
    "Levi", "Ádi", "Bazsi", "Imi", "Karcsi", "Lajcsi", "Norbi", "Szabi", "Vili",
    "Erzsike", "Katika", "Juliska", "Annuska", "Ancsa", "Ancsi", "Bözsi", "Böske", "Panka", "Zsófika", "Dodó",
    "Gyurika", "Jancsi", "Pistike", "Józsika", "Sanyika", "Andriska", "Bandika", "Tomika", "Danika", "Zolika",
    "Csabika", "Gabika", "Robika", "Misike", "Margó", "Manyi", "Rozi", "Teri", "Vera", "Nusi", "Lili", "Hanna",
    "Dani", "Bence", "Gergely", "Pisti", "Ottó", "Feri", "Edit", "Csilla", "Kinga", "Tünde",
    "Benci", "Bencus", "Zsombi", "Boti", "Roli", "Domi", "Beni", "Atti", "Gyuszi", "Csongi", "Krisztián", "Andi",
    "Bea", "Brigi", "Heni", "Ildi", "Jutka", "Meli", "Szilvi", "Vali", "Magdi", "Manci", "Hajni", "Barbi", "Dori",
    "Vivi", "Sári", "Mari", "Mariska", "Annus", "Edina", "Emese", "Enikő", "Flóra", "Kitti", "Petra", "Lilla",
}


# Case endings as typed without accents, longest first: "Tamast",
# "Rekanak", "Tibornal", "Szolnokrol". Vowel lengthening (Réka -> Rékának)
# disappears in the accent-less form, so stripping the ending is enough.
PLAIN_ENDINGS = tuple(sorted({
    "t", "ot", "et", "at", "nak", "nek", "val", "vel", "ban", "ben", "ba", "be", "bol", "ra", "re", "rol",
    "tol", "nal", "nel", "hoz", "hez", "on", "en", "n", "ert", "kent", "ig", "e",
}, key=len, reverse=True))
# -val/-vel and -vá/-vé assimilated to the final consonant: "tamassal", "peterrel"
PLAIN_ASSIMILATED = re.compile(r"^(.*?([b-df-hj-np-tv-z]))\2(?:al|el)$")


def plain_stems(token: str) -> set[str]:
    """Possible accent-less base forms of an accent-less word."""
    p = _plain(token)
    stems = {p}
    for ending in PLAIN_ENDINGS:
        if p.endswith(ending) and len(p) - len(ending) >= 3:
            stems.add(p[: -len(ending)])
    m = PLAIN_ASSIMILATED.match(p)
    if m and len(m.group(1)) >= 3:
        stems.add(m.group(1))
    return stems


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _plain(s: str) -> str:
    """Lower case without accents: 'Kovács' -> 'kovacs'."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if not unicodedata.combining(c))


def _load(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {_nfc(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


class HuNameRecognizer(EntityRecognizer):
    ENTITY = "PERSON"

    def __init__(self, lexicon_dir: str | None = None, supported_language: str = "hu") -> None:
        directory = Path(lexicon_dir or os.environ.get("ANALYZER_LEXICON_DIR", "/app/data/lexicon"))
        self.male = _load(directory / "given_male.txt")
        self.female = _load(directory / "given_female.txt")
        if not self.male:
            raise RuntimeError(f"Hungarian given-name list not found in {directory}")
        self.given = self.male | self.female | NICKNAMES
        # the Magyar Ispell surname list leaves out surnames that are also
        # ordinary words; the most frequent of those are added here
        self.surnames = _load(directory / "surnames.txt") | WORD_SURNAMES
        # Ordinary Hungarian words (lower case). Optional: without it, lone
        # lower-case given names are only accepted after a greeting.
        self.common = {w.lower() for w in _load(directory / "common_words.txt")}
        self.foreign = _load(directory / "foreign_given.txt") - STOPWORDS
        # foreign given names that are also Hungarian words ("Leszek" = "I will be", "Oda", "Martin")
        # count only next to a word that is not an ordinary word ("Martin Schneider")
        self.foreign_ambiguous = {n for n in self.foreign if n.lower() in self.common}
        # places: "Thomas van Budapesten" is not a name with a particle
        from .hu_places import EXONYMS, QUARTERS, REGIONS  # here: hu_places imports this module

        self.places = (_load(directory / "settlements.txt") | _load(directory / "countries.txt")
                       | _load(directory / "foreign_cities.txt") | EXONYMS | QUARTERS | REGIONS)
        self.morph = HungarianMorphology(lexicon=self.given | self.surnames)
        # chat text is often typed without accents: "kovacs petinek"
        self.plain_given = {_plain(n) for n in self.given}
        self.plain_male = {_plain(n) for n in self.male}
        self.plain_surnames = {_plain(n) for n in self.surnames}
        self.plain_common = {_plain(w) for w in self.common}
        super().__init__(supported_entities=[self.ENTITY], supported_language=supported_language, name="HuNameRecognizer")

    def load(self) -> None:
        pass

    # -- token classification -------------------------------------------------
    def _stem(self, token: str) -> tuple[str, str | None]:
        stem, case, _ = self.morph.split_word(token)
        return (stem, case) if case else (token, None)

    def _married_base(self, token: str) -> str | None:
        """'Andrásné' / 'Lászlónéhoz' / 'Kissnének' -> 'András' / 'László' / 'Kiss'."""
        m = re.match(
            r"^(.{3,}?)né(?:nek|nak|nél|nál|hez|hoz|höz|vel|val|t|ről|ról|től|tól|re|ra|é|ért|ként|ben|ban|ből|ból|be|ba|ék)?$",
            token,
        )
        if not m:
            return None
        base = m.group(1)
        # "Imréné" -> "Imre", "Lászlóné" -> "László"
        if base[-1:] in ("á", "é") and base[:-1] + {"á": "a", "é": "e"}[base[-1]] in self.given | self.surnames:
            base = base[:-1] + {"á": "a", "é": "e"}[base[-1]]
        return base

    @staticmethod
    def _possessive_bases(token: str) -> set[str]:
        """'Gáborét' -> {'Gábor'}, 'Annáéval' -> {'Anná', 'Anna'} (possessive -é)."""
        m = POSSESSIVE_RE.match(token)
        if not m:
            return set()
        base = m.group(1)
        out = {base}
        if base[-1:] in ("á", "é"):
            out.add(base[:-1] + {"á": "a", "é": "e"}[base[-1]])
        return out

    def is_given(self, token: str) -> bool:
        if token in self.given or self._stem(token)[0] in self.given:
            return True
        if self._possessive_bases(token) & self.given:
            return True
        base = self._married_base(token)
        return base is not None and base in self.male

    def is_known_surname(self, token: str) -> bool:
        stem = self._stem(token)[0]
        base = self._married_base(token) or stem
        family = self._family_base(token)
        parts = {token, stem, base, *base.split("-")} | ({family} if family else set())
        if any(p in self.surnames or self._stem(p)[0] in self.surnames for p in parts):
            return True
        # base forms only (no further stemming, which is slow on new words):
        # possessive "Kovácsét", old spelling "Várady" = "Váradi", "Csáky" = "Csáki"
        extra = self._possessive_bases(token)
        extra |= {p[:-1] + "i" for p in parts | extra if len(p) > 3 and p.endswith("y") and p[-2] not in "aeiouáéíóöőúüűgnlt"}
        return bool(extra & self.surnames)

    def is_given_plain(self, token: str) -> bool:
        """A given name typed without accents, also inflected or as a married
        form: "Reka", "Tamast", "Rekanak", "Tamasne". Only for words written
        without any accent; accented words take the exact path."""
        if not token.isascii():
            return False
        stems = plain_stems(token)
        if stems & self.plain_given:
            return True
        return any(s.endswith("ne") and s[:-2] in self.plain_male for s in stems)

    def is_surname_plain(self, token: str) -> bool:
        """A known surname typed without accents, also inflected, married or
        family form: "Halasz", "Kardosne", "Biroekhoz"."""
        if not token.isascii():
            return False
        stems = plain_stems(token)
        if stems & self.plain_surnames:
            return True
        return any(s.endswith(("ne", "ek")) and s[:-2] in self.plain_surnames for s in stems)

    def _surname_shaped(self, token: str) -> bool:
        """An unknown word that looks like a surname made from a place name
        ("Füleki", "Fenyvesi", "Rákóczy") and is not an ordinary word: kept at
        the start of a sentence when a given name follows."""
        return (
            len(token) >= 5
            and token[-1] in "iy"
            and token not in STOPWORDS
            and not self._common_word(token)
            and not self._plain_common_word(token)
        )

    def _plain_marked_surname(self, token: str) -> bool:
        """'lukacsek', 'kardosne': a known surname typed without accents in
        family (-ék) or married (-né) form, whose base is not an ordinary word.
        Not a conditional verb form: "kellene" is "kell" + "-ene", not "Kelle" + "-né";
        not an inflected ordinary word: "kernek" is "kérnek", not "Kern" + "-ék".
        Accents partly lost (OCR: "lukacsék") are folded first."""
        token = _plain(token)
        if self._plain_common_word(token):
            return False
        for stem in plain_stems(token):
            base = stem[:-2]
            if not (stem.endswith(("ne", "ek")) and base in self.plain_surnames and base not in self.plain_common):
                continue
            if stem.endswith("ne") and base.endswith(("e", "a")) and base[:-1] in self.plain_common:
                continue
            return True
        return False

    def _plain_common_word(self, token: str) -> bool:
        """An accent-less word that is an ordinary word once its accents are
        gone ("Rozsa", "Halasz"): not a name on its own."""
        return bool(self.plain_common) and bool(plain_stems(token) & self.plain_common)

    def _family_base(self, token: str) -> str | None:
        """'Kovácsékkal' -> 'Kovács', 'Vargáéknak' -> 'Varga' (any word; only
        real case endings after -ék, so 'Emlékeztető' is not 'Eml' + -ék)."""
        m = FAMILY_RE.match(token)
        if not m:
            return None
        base = m.group(1)
        if base[-1:] in ("á", "é"):
            base = base[:-1] + {"á": "a", "é": "e"}[base[-1]]
        return base

    def _family_form(self, token: str) -> bool:
        base = self._family_base(token)
        return base is not None and (base in self.surnames or base.capitalize() in self.surnames)

    @staticmethod
    def _capitalised(token: str) -> bool:
        return token[:1].isupper() and (not token.isupper() or len(token) == 1)

    @classmethod
    def _name_like(cls, token: str) -> bool:
        """Capitalised, and no inner capital except after - or ' ('GmbH',
        'NetHorizont' are not name parts; 'Szabó-Nagy', "O'Brien" are)."""
        if not cls._capitalised(token):
            return False
        return all(not c.isupper() or token[k - 1] in "-'’" for k, c in enumerate(token) if k > 0)

    def _foreign_given(self, token: str) -> bool:
        """'Thomas', 'Thomast', 'Thomasszal' (foreign given names are not in
        the morphology lexicon, so endings are stripped here)."""
        if token in self.foreign or self._stem(token)[0] in self.foreign:
            return True
        m = FOREIGN_ENDING.search(token)
        return bool(m) and token[: m.start()] in self.foreign

    def _ocr_given(self, token: str) -> bool:
        """A given name with one typical OCR confusion ("Bamabástól" = "Barnabástól")."""
        for a, b in OCR_SWAPS:
            k = token.find(a)
            while k >= 0:
                v = token[:k] + b + token[k + len(a):]
                # also with the accents lost: "Lerincnel" -> "Lőrincnel" -> "lorincnel"
                if self.is_given(v) or self.is_given_plain(_plain(v)):
                    return True
                k = token.find(a, k + 1)
        return False

    def _foreign_backed(self, words: list[str], n: int) -> bool:
        """A foreign given name that is also a Hungarian word needs a neighbour
        in the run that is not an ordinary word ("Martin Schneider", not "Oda Holnap")."""
        w = words[n]
        if not ({w} | self._foreign_bases(w)) & self.foreign_ambiguous:
            return True
        return any(
            0 <= k < len(words) and words[k] not in STOPWORDS and words[k] not in PARTICLES and not self._common_word(words[k])
            for k in (n - 1, n + 1)
        )

    @staticmethod
    def _foreign_bases(token: str) -> set[str]:
        """'Thomast' -> {'Thomas'}, "O'Connorral" -> {"O'Connor"}."""
        m = FOREIGN_ENDING.search(token)
        return {token[: m.start()]} if m and m.start() >= 2 else set()

    def _foreign_surname_shape(self, token: str) -> bool:
        return any(FOREIGN_SURNAME.search(t) for t in {token} | self._foreign_bases(token))

    _PLACE_ENDINGS = ("ban", "ben", "ba", "be", "ból", "ből", "ra", "re", "ról", "ről", "on", "en", "ön", "n",
                      "hoz", "hez", "höz", "tól", "től", "nál", "nél", "ig", "t", "ot", "et", "at")

    def _is_place(self, token: str) -> bool:
        """A settlement or country, also with a case ending ("Budapesten", "Szegedre")."""
        cands = {token}
        for e in self._PLACE_ENDINGS:
            if token.endswith(e) and len(token) - len(e) >= 3:
                cands.add(token[: -len(e)])
        cands |= {c[:-1] + {"á": "a", "é": "e"}[c[-1]] for c in cands if c[-1:] in ("á", "é")}
        return bool(cands & self.places)

    def _particle_surname(self, text, tokens, k: int) -> int:
        """Index of the last token of 'de Vries' / 'van der Berg' starting at
        token k (a particle), or -1. The word after the particles must look like
        a surname: capitalised, not a given name, ordinary word or place."""
        j = k
        while j < len(tokens) and j - k < 2 and tokens[j][2] in PARTICLES:
            j += 1
        if j == k or j >= len(tokens):
            return -1
        if any(text[tokens[n][1] : tokens[n + 1][0]] != " " for n in range(k - 1, j)):
            return -1  # only single spaces between the words
        w = tokens[j][2]
        if not self._name_like(w) or w in STOPWORDS or self.is_given(w) or self._foreign_given(w):
            return -1
        if self._common_word(w) or self._is_place(w):
            return -1
        return j

    def _common_word(self, token: str) -> bool:
        """An ordinary word, also inflected: the word list has lemmas only, so
        a known lemma of 5+ letters followed by up to 7 letters counts too
        ("szükségességét", "rózsának"; not "rozs" in "rozsnyainak")."""
        low = token.lower()
        if not self.common:
            return False
        if low in self.common or self._stem(token)[0].lower() in self.common:
            return True
        return any(low[:k] in self.common for k in range(len(low) - 1, max(4, len(low) - 8), -1))

    @staticmethod
    def _sentence_initial(text: str, start: int) -> bool:
        # a bounded window: copying the whole prefix per token was quadratic in the text length
        before = text[max(0, start - 200) : start].rstrip()
        if not before and start > 200:
            before = text[:start].rstrip()
        if not before:
            return True
        if before[-1] == "." and TITLE_BEFORE.search(before):
            return False  # "dr. Bíró Éva": the dot belongs to the title
        return before[-1] in ".!?:\n\"„(" or before.endswith(("–", "-"))

    # -- analysis -------------------------------------------------------------
    def analyze(self, text, entities, nlp_artifacts=None):  # noqa: ARG002
        # tokens longer than any name are skipped: the word analysis is not linear in length
        tokens = [(m.start(), m.end(), m.group(0)) for m in TOKEN_RE.finditer(text) if len(m.group(0)) <= MAX_TOKEN]
        results: list[RecognizerResult] = []
        i = 0
        while i < len(tokens):
            if not self._capitalised(tokens[i][2]):
                found = self._lowercase_name(text, tokens, i)
                if found:
                    results.append(found[0])
                    i = found[1]
                else:
                    i += 1
                continue
            if not self._name_like(tokens[i][2]):
                i += 1
                continue
            run = [i]
            j = i
            while j + 1 < len(tokens) and len(run) < 5:
                sep = text[tokens[j][1] : tokens[j + 1][0]]
                if sep in (" ", "\n") and self._name_like(tokens[j + 1][2]):
                    j += 1
                    run.append(j)
                    continue
                # "Jeroen de Vries", "Ursula von der Leyen": particles after a given name
                if sep == " " and tokens[j + 1][2] in PARTICLES and (
                    self.is_given(tokens[j][2]) or self._foreign_given(tokens[j][2])
                ):
                    last = self._particle_surname(text, tokens, j + 1)
                    if last > 0:
                        run.extend(range(j + 1, last + 1))
                        j = last
                        continue
                break
            results.extend(self._run_names(text, tokens, run))
            i = j + 1
        taken = [(r.start, r.end) for r in results]
        results.extend(self._single_surnames(text, tokens, taken))
        taken = [(r.start, r.end) for r in results]
        results.extend(self._case_parties(text, tokens, taken))
        results.extend(self._repeated_parts(text, tokens, results))
        return [self._with_initial(text, r) for r in results]

    def _run_names(self, text, tokens, run):
        """Names in a run of capitalised words. A title or salutation cue
        ("Kedves Liam O'Connor") and the given-name rules are combined, so the
        cue extends a name the lexicon only partly knows."""
        a = self._name_in_run(text, tokens, run)
        b = self._cued_name(text, tokens, run)
        if a and b and a.start < b.end and b.start < a.end:
            why = f"{a.analysis_explanation.textual_explanation} + {b.analysis_explanation.textual_explanation}"
            return [self._result(min(a.start, b.start), max(a.end, b.end), max(a.score, b.score), why)]
        return [r for r in (a, b) if r]

    def _case_parties(self, text, tokens, taken):
        """Parties of a legal case: "a Pálfi kontra Vasvári ügyben"."""
        out = []
        for start, end, w in tokens:
            if any(s <= start < e for s, e in taken) or not self._name_like(w) or w in STOPWORDS:
                continue
            if not (VERSUS_AFTER.match(text[end:]) or VERSUS.search(text[max(0, start - 10) : start])):
                continue
            if (self._common_word(w) and not self.is_known_surname(w)) or NOT_A_PERSON_AFTER.match(text[end:]):
                continue
            out.append(self._result(start, end, 0.5, "party of a legal case (X kontra Y)"))
        return out

    def _with_initial(self, text, r):
        """'K. Péternek', 'B. Gergellyel': an initial belongs to the name; so
        does one after a lone surname: 'dr. Fülöp J.'."""
        m = INITIAL_BEFORE.search(text, max(0, r.start - 4), r.start)
        if m and m.end() == r.start:
            r = self._result(m.start(), r.end, r.score, r.analysis_explanation.textual_explanation + " + initial")
        after = INITIAL_AFTER.match(text, r.end)
        if after and len(TOKEN_RE.findall(text[r.start : r.end])) == 1:
            r = self._result(r.start, after.end(), r.score, r.analysis_explanation.textual_explanation + " + initial")
        elif r.end - r.start >= 3 and text[r.end - 2] == " " and text[r.end - 1].isupper() and text[r.end : r.end + 1] == ".":
            r = self._result(r.start, r.end + 1, r.score, r.analysis_explanation.textual_explanation)  # "Fülöp J" + "."
        return r

    def _cued_name(self, text, tokens, run):
        """Capitalised words that are not known names but follow a title
        ("Mr. O'Brien", "dr. Rozsnyainak") or a salutation ("Tisztelt
        Bencze Úr"). Up to three words; roles ("Tisztelt Ügyfelünk") and
        street/institution names are excluded."""
        k = 0
        while k < len(run) and tokens[run[k]][2] in ("Dr", "Mr", "Mrs", "Ms", "Prof", "Ifj", "Özv", "Id", "Tisztelt", "Kedves"):
            k += 1
        if k >= len(run):
            return None
        start = tokens[run[k]][0]
        before = text[max(0, start - 12) : start]
        if TITLE_BEFORE.search(before):
            why = "word after a title"
        elif SALUTATION.search(before):
            why = "word after a salutation"
        else:
            return None
        words = []
        for pos, n in enumerate(run[k : k + 3]):
            w = tokens[n][2]
            if w in STOPWORDS or w in SALUTATION_NOUNS or HONORIFICS.match(" " + w):
                break
            nxt = tokens[run[k + pos + 1]][2] if k + pos + 1 < len(run) else ""
            if why == "word after a salutation" and w.endswith("i") and nxt in SALUTATION_NOUNS:
                break  # "Tisztelt Adatvédelmi Tisztviselő", "Kedves Pénzügyi Osztály": a role
            if why == "word after a salutation" and self._common_word(w) and not self.is_known_surname(w):
                break
            words.append(n)
        if not words:
            return None
        span_start, span_end = tokens[words[0]][0], tokens[words[-1]][1]
        if NOT_A_PERSON_AFTER.match(text[span_end:]):
            return None
        return self._result(span_start, span_end, 0.55, why)

    def _repeated_parts(self, text, tokens, found):
        """A surname or given name already found as part of a multi-word name
        is masked again where it appears alone ("Thomas Becker ... Beckerrel",
        "Gombos Ildikó ... Gomboséknál")."""
        parts: set[str] = set()
        for r in found:
            words = [w for w in TOKEN_RE.findall(text[r.start : r.end]) if w.lower().rstrip(".") not in TITLE_WORDS]
            why = r.analysis_explanation.textual_explanation if r.analysis_explanation else ""
            # a single word counts only with a cue: "Tisztelt Brennan Úr" ... "Aoife Brennan"
            if len(words) >= 2 or (len(words) == 1 and why.startswith(("word after a", "surname + title"))):
                for w in words:
                    parts.update({w, self._stem(w)[0], *self._foreign_bases(w)})
        parts -= STOPWORDS
        parts -= PARTICLES
        if not parts:
            return []
        taken = [(r.start, r.end) for r in found]
        out = []
        for start, end, w in tokens:
            if any(s <= start < e for s, e in taken) or not self._name_like(w) or w in STOPWORDS:
                continue
            candidates = {w, self._stem(w)[0], self._married_base(w), self._family_base(w),
                          *self._foreign_bases(w), *self._possessive_bases(w)}
            if not candidates & parts:
                continue
            nxt = re.match(r"\s+([a-záéíóöőúüű]+)", text[end:])
            follows = bool(nxt) and nxt.group(1) in NAME_FOLLOWERS  # "Szabó szerint", "Kovács is"
            if self._sentence_initial(text, start) and (w in AMBIGUOUS_GIVEN or self._common_word(w)) and not follows:
                continue
            if NOT_A_PERSON_AFTER.match(text[end:]):
                continue
            out.append(self._result(start, end, 0.5, "repeated part of a name in the same text"))
        return out

    def _result(self, start: int, end: int, score: float, why: str) -> RecognizerResult:
        return RecognizerResult(
            self.ENTITY, start, end, score, AnalysisExplanation(recognizer=self.name, original_score=score, textual_explanation=why)
        )

    def _name_in_run(self, text, tokens, run):
        words = [tokens[k][2] for k in run]
        if words[0] in VEHICLE_MAKES:
            return None  # "Škoda Octavia", "Dacia Logan": a car model
        # accent-less given names count next to exact ones: "Barta Kristof" (Barta is both)
        given_idx = [n for n, w in enumerate(words) if self.is_given(w) or (len(words) > 1 and self.is_given_plain(w))]
        if not given_idx:
            # typed without accents: "Reka", "Halasz Tibornal"; on its own the
            # word must not be an ordinary word once accents are gone
            given_idx = [
                n for n, w in enumerate(words)
                if self.is_given_plain(w) and (len(words) > 1 or not self._plain_common_word(w))
            ]
        if not given_idx and len(words) >= 2:
            # "Thomas Becker", "Becker Thomast": foreign given names only in runs
            given_idx = [n for n, w in enumerate(words) if self._foreign_given(w) and self._foreign_backed(words, n)]
        if not given_idx and len(words) >= 2 and any(self.is_known_surname(w) for w in words):
            # OCR output next to a known surname: "Kun Bamabástól", "Nagy Lerincnél"
            given_idx = [n for n, w in enumerate(words) if not self.is_known_surname(w) and self._ocr_given(w)]
        if not given_idx:
            return None
        last_given = given_idx[-1]
        first = 0
        while first < last_given:
            w = words[first]
            start = tokens[run[first]][0]
            calendar_surname = (
                w in CALENDAR_WORDS and first + 1 == last_given and not self._sentence_initial(text, start)
            )
            if (w in STOPWORDS and not calendar_surname) or w.lower().rstrip(".") in TITLE_WORDS:
                first += 1
                continue
            # after "Beteg:" / "Név:" the field value starts, not a sentence
            field_value = text[max(0, start - 50) : start].rstrip().endswith(":")
            if self._sentence_initial(text, start) and not field_value and not (
                self.is_known_surname(w) or self.is_given(w) or w.endswith("né") or w in self.foreign
                or self.is_surname_plain(w) or self.is_given_plain(w) or self._surname_shaped(w)
            ):
                first += 1
                continue
            break
        end_idx = last_given
        # Western order "Péter Kovács" / "Thomas Becker": a surname-like word after the given name
        if end_idx + 1 < len(words) and first == end_idx and words[end_idx + 1] not in STOPWORDS and (
            self.is_known_surname(words[end_idx + 1]) or self.is_surname_plain(words[end_idx + 1])
            or words[end_idx] in self.foreign or words[end_idx + 1] in PARTICLES
            or self._foreign_surname_shape(words[end_idx + 1])
        ):
            end_idx += 1
            while end_idx + 1 < len(words) and words[end_idx] in PARTICLES:
                end_idx += 1  # "Jeroen de Vriesnek"
        span_start = tokens[run[first]][0]
        span_end = tokens[run[end_idx]][1]
        name_words = words[first : end_idx + 1]
        if NOT_A_PERSON_AFTER.match(text[span_end:]):
            return None
        if len(name_words) == 1:
            w = name_words[0]
            if w in STOPWORDS or not (self.is_given(w) or self.is_given_plain(w)):
                return None
            after = words[end_idx + 1 :]
            if after and self._common_word(w) and not any(self.is_given(x) or self.is_known_surname(x) for x in after):
                return None  # "a belső Fehér Könyvünk": a title, not a person
            stem = self._stem(w)[0]
            if (w in AMBIGUOUS_GIVEN or stem in AMBIGUOUS_GIVEN) and self._sentence_initial(text, span_start):
                return None
            score, why = 0.6, "given name alone"
        elif any(
            self.is_known_surname(w) or self.is_surname_plain(w) for w in name_words if not self.is_given(w)
        ) or all(self.is_given(w) or self.is_given_plain(w) for w in name_words):
            score, why = 0.85, "surname + given name"
        else:
            score, why = 0.7, "capitalised word + given name"
        if TITLE_BEFORE.search(text[max(0, span_start - 6) : span_start]):
            score = min(1.0, score + 0.1)
        return self._result(span_start, span_end, score, why)

    def _lower_given(self, w: str) -> bool:
        """A lower-case token that is a given name, also typed without accents."""
        cap = w[:1].upper() + w[1:]
        if self.is_given(cap):
            return True
        stem = self._stem(cap)[0]
        return _plain(cap) in self.plain_given or _plain(stem) in self.plain_given or self.is_given_plain(cap)

    def _lower_surname(self, w: str) -> bool:
        cap = w[:1].upper() + w[1:]
        return self.is_known_surname(cap) or _plain(cap) in self.plain_surnames or self.is_surname_plain(cap)

    def _lowercase_name(self, text, tokens, i):
        """Chat style: 'szia peti', 'horváth tamással', 'kovacs petinek',
        'gáborral' (only if the word is not an ordinary word)."""
        start, end, w = tokens[i]
        cap = w[:1].upper() + w[1:]
        if i + 1 < len(tokens) and text[end : tokens[i + 1][0]] == " ":
            nxt = tokens[i + 1][2]
            if (
                not self._capitalised(nxt)
                and self._lower_surname(w)
                and self._lower_given(nxt)
                and cap not in AMBIGUOUS_GIVEN
            ):
                return self._result(start, tokens[i + 1][1], 0.6, "lower-case surname + given name"), i + 2
        if self._stem(cap)[0].lower() in LOWER_NOT_NAMES or w in LOWER_NOT_NAMES:
            return None
        if len(w) >= 3 and cap not in AMBIGUOUS_GIVEN and (self._lower_given(w) or self._family_form(cap)):
            if GREETING.search(text[max(0, start - 25) : start]):
                return self._result(start, end, 0.5, "lower-case name after greeting"), i + 1
            if self.common and len(w) >= 4 and not self._common_word(w) and self.is_given(cap):
                # extend to a preceding lower-case surname-like word ("mohácsi bence")
                if i > 0 and text[tokens[i - 1][1] : start] == " ":
                    pw = tokens[i - 1][2]
                    if not self._capitalised(pw) and len(pw) >= 4 and (self._lower_surname(pw) or not self._common_word(pw)):
                        return self._result(tokens[i - 1][0], end, 0.5, "lower-case surname + given name"), i + 1
                return self._result(start, end, 0.45, "lower-case given name (not an ordinary word)"), i + 1
        if (
            len(w) >= 4
            and w not in ACADEMIC_ABBREVIATIONS
            and TITLE_DOT_BEFORE.search(text[max(0, start - 6) : start])
            and not (self.common and self._common_word(w))
        ):
            return self._result(start, end, 0.5, "lower-case word after a title"), i + 1
        if len(w) >= 6 and (
            self._family_form(cap)
            or (self._married_base(cap) and self._lower_surname(self._married_base(cap)))
            or self._plain_marked_surname(w)
        ):
            return self._result(start, end, 0.5, "lower-case family / married form"), i + 1
        m = OLD_SPELLING_LOWER.match(w)
        if m and len(w) >= 4:
            stem = m.group("stem")
            if self.is_known_surname(stem[:1].upper() + stem[1:]) and not self._common_word(stem) and not self._common_word(w):
                return self._result(start, end, 0.45, "lower-case surname in old spelling"), i + 1
        return None

    def _single_surnames(self, text, tokens, taken):
        """Known surnames standing alone: with a title/honorific, inflected
        mid-sentence ("Kissnek"), as family form ("Kovácsékkal") or as a
        married-name form ("Kissné")."""
        out = []
        for start, end, w in tokens:
            if any(s <= start < e for s, e in taken) or not self._capitalised(w) or w in STOPWORDS:
                continue
            initial = self._sentence_initial(text, start)
            if not self.is_known_surname(w):
                # typed without accents: "Biroekhoz", "Kardosne", "Halasz urnak", "a Kovacsnak"
                if self.is_surname_plain(w) and not self._plain_common_word(w):
                    p = _plain(w)
                    stems = plain_stems(w)
                    marked = any(x.endswith(("ne", "ek")) and x[:-2] in self.plain_surnames for x in stems)
                    titled = HONORIFICS.match(text[end:]) or TITLE_BEFORE.search(text[max(0, start - 6) : start])
                    inflected = not initial and any(x != p and x in self.plain_surnames for x in stems)
                    if marked or titled or inflected:
                        out.append(self._result(start, end, 0.5, "surname typed without accents"))
                        continue
                # married / family form of an unknown surname mid-sentence: "Erdősné", "Vadászék"
                base = self._married_base(w) or self._family_base(w)
                if base and self._name_like(w) and len(base) >= 3 and base not in STOPWORDS:
                    if not self._common_word(base) or base in self.surnames:
                        out.append(self._result(start, end, 0.5, "married / family form"))
                continue
            stem, case = self._stem(w)
            if HONORIFICS.match(text[end:]) or TITLE_BEFORE.search(text[max(0, start - 6) : start]):
                out.append(self._result(start, end, 0.6, "surname + title"))
            elif self._family_form(w) or self._married_base(w):
                out.append(self._result(start, end, 0.6, "family / married-name form"))
            elif case and not initial and w not in AMBIGUOUS_GIVEN and stem not in AMBIGUOUS_GIVEN:
                out.append(self._result(start, end, 0.5, "inflected surname"))
        return out
