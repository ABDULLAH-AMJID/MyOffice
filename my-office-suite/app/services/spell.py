"""Offline spell checking (English US/UK, Spanish) with a personal dictionary.

Word lists come from pyspellchecker (MIT, frequency lists - no internet needed).  British/American variants are
told apart with a list of common spelling pairs: in English (UK) "color" is flagged with "colour" as the suggestion,
and the other way round.  Urdu, Hindi and other scripts are not checked (no open word list with a suitable licence),
so they are never underlined.
"""
from __future__ import annotations

import logging
import re
import threading

from app.services.paths import user_subdir

log = logging.getLogger(__name__)

LANGS = {"en_US": ("English (US)", "en"), "en_GB": ("English (UK)", "en"), "es_ES": ("Espa\u00f1ol", "es")}
WORD_RE = re.compile(r"[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f]+(?:['\u2019][A-Za-z\u00c0-\u024f]+)*")

# American -> British spellings (the most frequent ones; both directions are derived from it)
_US_UK = """
color colour|colors colours|colored coloured|colorful colourful|favor favour|favorite favourite|favorites favourites|
honor honour|honors honours|humor humour|labor labour|neighbor neighbour|neighbors neighbours|neighborhood
neighbourhood|behavior behaviour|behaviors behaviours|flavor flavour|harbor harbour|rumor rumour|vapor vapour|
vigor vigour|savior saviour|endeavor endeavour|armor armour|parlor parlour|odor odour|tumor tumour|center centre|
centers centres|centered centred|theater theatre|theaters theatres|meter metre|meters metres|liter litre|liters
litres|fiber fibre|caliber calibre|somber sombre|specter spectre|organize organise|organized organised|organizing
organising|organization organisation|organizations organisations|realize realise|realized realised|realizing
realising|recognize recognise|recognized recognised|apologize apologise|apologized apologised|analyze analyse|
analyzed analysed|analyzing analysing|paralyze paralyse|criticize criticise|criticized criticised|emphasize
emphasise|emphasized emphasised|summarize summarise|summarized summarised|memorize memorise|prioritize prioritise|
minimize minimise|maximize maximise|optimize optimise|optimized optimised|utilize utilise|utilized utilised|
characterize characterise|categorize categorise|standardize standardise|finalize finalise|customize customise|
customized customised|authorize authorise|authorized authorised|civilization civilisation|globalization
globalisation|modernize modernise|specialize specialise|specialized specialised|catalog catalogue|dialog dialogue|
analog analogue|traveler traveller|travelers travellers|traveled travelled|traveling travelling|canceled cancelled|
canceling cancelling|labeled labelled|labeling labelling|modeled modelled|modeling modelling|fueled fuelled|
jewelry jewellery|counselor counsellor|marvelous marvellous|enrollment enrolment|fulfill fulfil|skillful skilful|
defense defence|offense offence|license licence|pretense pretence|gray grey|plow plough|program programme|
programs programmes|check cheque|tire tyre|tires tyres|curb kerb|pajamas pyjamas|aluminum aluminium|mustache
moustache|maneuver manoeuvre|estrogen oestrogen|pediatric paediatric|encyclopedia encyclopaedia|anemia anaemia|
anesthesia anaesthesia|fetus foetus|esophagus oesophagus|aging ageing|judgment judgement|acknowledgment
acknowledgement|artifact artefact|cozy cosy|draft draught|mold mould|smolder smoulder|yogurt yoghurt|
"""
US_TO_UK: dict[str, str] = {}
for _pair in re.split(r"[|\n]+", _US_UK):
    _w = _pair.split()
    if len(_w) == 2:
        US_TO_UK[_w[0]] = _w[1]
UK_TO_US = {v: k for k, v in US_TO_UK.items()}
# "check", "program", "draft" and "tire" are correct in British English in other senses: never flag them
_AMBIGUOUS = {"check", "program", "programs", "draft", "tire", "tires", "curb"}


def _american_forms(word: str) -> list[str]:
    """Candidate American spellings of a British word (colour -> color, organisation -> organization,
    centre -> center, travelled -> traveled) - the word list is American, British forms are accepted through them."""
    out = []
    for uk, us in (("our", "or"), ("isation", "ization"), ("ise", "ize"), ("ising", "izing"), ("ised", "ized"),
                   ("yse", "yze"), ("ysed", "yzed"), ("tre", "ter"), ("tres", "ters"), ("lled", "led"),
                   ("lling", "ling"), ("ller", "ler"), ("ogue", "og"), ("ence", "ense"), ("ae", "e"), ("oe", "e")):
        if uk in word:
            out.append(word.replace(uk, us))
    return out


def checkable(word: str) -> bool:
    """Words worth checking: letters only, not acronyms (NATO), not mixed-case identifiers (iPhone, McDonald)."""
    if len(word) < 2 or not WORD_RE.fullmatch(word):
        return False
    if word.isupper():
        return False
    return not any(ch.isupper() for ch in word[1:])


class Speller:
    def __init__(self, lang: str = "en_US"):
        self.lang = lang if lang in LANGS else "en_US"
        self._spell = None
        self._lock = threading.Lock()
        self.ignored: set[str] = set()
        self._custom_path = user_subdir("dict") / "custom.txt"
        self.custom: set[str] = set()
        try:
            self.custom = {ln.strip().lower() for ln in self._custom_path.read_text(encoding="utf-8").splitlines()
                           if ln.strip()}
        except OSError:
            pass
        self._cache: dict[str, bool] = {}

    # the word list (0.5 - 1 s to load) is read on first use
    @property
    def spell(self):
        if self._spell is None:
            with self._lock:
                if self._spell is None:
                    from spellchecker import SpellChecker

                    self._spell = SpellChecker(language=LANGS[self.lang][1], distance=2)
        return self._spell

    def ready(self) -> bool:
        return self._spell is not None

    def load_async(self) -> None:
        if self._spell is None:
            threading.Thread(target=lambda: self.spell, name="spell-load", daemon=True).start()

    def variant_suggestion(self, word: str) -> str | None:
        """British spelling in an American text (or the other way round)."""
        low = word.lower()
        if low in _AMBIGUOUS:
            return None
        table = UK_TO_US if self.lang == "en_US" else US_TO_UK if self.lang == "en_GB" else {}
        hit = table.get(low)
        if hit is None:
            return None
        return hit.capitalize() if word[:1].isupper() else hit

    def check(self, word: str) -> bool:
        """True if ``word`` is spelled correctly (or not checkable)."""
        if not checkable(word):
            return True
        low = word.lower().replace("\u2019", "'")
        if low in self.ignored or low in self.custom:
            return True
        hit = self._cache.get(low)
        if hit is None:
            if self.variant_suggestion(word):
                hit = False
            else:
                base = low[:-2] if low.endswith("'s") else low
                hit = bool(self.spell.known([base])) or bool(self.spell.known([low]))
                if not hit and self.lang == "en_GB":
                    hit = base in UK_TO_US or any(self.spell.known([alt]) for alt in _american_forms(base))
            if len(self._cache) > 50000:
                self._cache.clear()
            self._cache[low] = hit
        return hit

    def suggest(self, word: str, limit: int = 6) -> list[str]:
        v = self.variant_suggestion(word)
        if v:
            return [v]
        low = word.lower()
        sp = self.spell
        cands = None
        try:
            sp.distance = 1
            cands = sp.candidates(low)
            if not cands or cands == {low}:
                sp.distance = 2 if len(low) < 14 else 1
                cands = sp.candidates(low)
        finally:
            sp.distance = 2
        if not cands:
            return []
        ranked = sorted((c for c in cands if c != low), key=lambda c: -sp.word_usage_frequency(c))[:limit]
        if self.lang == "en_GB":
            ranked = [US_TO_UK.get(c, c) for c in ranked]
        elif self.lang == "en_US":
            ranked = [UK_TO_US.get(c, c) for c in ranked]
        seen, out = set(), []
        for c in ranked:
            if c in seen:
                continue
            seen.add(c)
            out.append(c.capitalize() if word[:1].isupper() else c)
        return out

    def add(self, word: str) -> None:
        low = word.lower()
        self.custom.add(low)
        self._cache.pop(low, None)
        try:
            self._custom_path.write_text("\n".join(sorted(self.custom)) + "\n", encoding="utf-8")
        except OSError as exc:
            log.warning("Personal dictionary not saved: %s", exc)

    def ignore(self, word: str) -> None:
        low = word.lower()
        self.ignored.add(low)
        self._cache.pop(low, None)

    def errors(self, text: str):
        """(start, end, word) of every misspelled word in ``text``."""
        for m in WORD_RE.finditer(text):
            w = m.group()
            if not self.check(w):
                yield m.start(), m.end(), w


_spellers: dict[str, Speller] = {}


def speller(lang: str = "en_US") -> Speller:
    if lang not in _spellers:
        _spellers[lang] = Speller(lang)
    return _spellers[lang]
