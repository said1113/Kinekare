"""
matching.py
-----------
Kariyer eşleştirme motoru + veri yükleme yardımcıları.

Not: AI'lı sürüme geçtiğimizden bu yana botun ana önerileri Gemini'den geliyor.
Bu dosya artık şunlar için kullanılıyor:
  • load_careers()        -> veritabanını diskten okur (bot.py + ai_advisor.py)
  • find_career_by_name() -> /meslek ve /karsilastir için arama
  • surprise_pick()       -> /surpriz için ilginç meslek seçimi
  • UserProfile           -> surprise_pick'e boş profil geçmek için

Eski ağırlıklı puanlama (score_career/recommend) ve serbest metin ayrıştırma
(profile_from_text) hâlâ burada duruyor ama bot artık bunları çağırmıyor —
istersen ileride silebilirsin, zararı yok.
"""

from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Skor ağırlıkları. Toplam mutlak değer önemli değil; göreli oranlar önemli.
WEIGHTS = {
    "tags": 3.0,        # ilgi alanları en belirleyici
    "work_style": 2.0,  # çalışma tarzı ikinci öncelik
    "values": 2.5,      # değerler de güçlü sinyal
}


# --------------------------------------------------------------------------- #
# Veri yükleme
# --------------------------------------------------------------------------- #
def _load_json(name: str):
    path = os.path.join(DATA_DIR, name)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_careers() -> List[dict]:
    """Meslek veritabanını diskten okur."""
    return _load_json("careers.json")


def load_keywords() -> Dict[str, List[str]]:
    """Serbest metin analizi için anahtar kelime sözlüğünü okur."""
    return _load_json("keywords.json").get("tags", {})


# --------------------------------------------------------------------------- #
# Profil
# --------------------------------------------------------------------------- #
@dataclass
class UserProfile:
    """Kullanıcının seçtiği/çıkarılan tercihler."""
    tags: List[str] = field(default_factory=list)
    work_style: List[str] = field(default_factory=list)
    values: List[str] = field(default_factory=list)
    transition_mode: bool = False  # "işinden sıkılmış" kariyer geçişi modu

    def is_empty(self) -> bool:
        return not (self.tags or self.work_style or self.values)


@dataclass
class MatchResult:
    """Tek bir mesleğin eşleştirme sonucu."""
    career: dict
    score: float
    max_score: float
    matched_tags: List[str]
    matched_style: List[str]
    matched_values: List[str]

    @property
    def percent(self) -> int:
        """0-100 arası uyum yüzdesi."""
        if self.max_score <= 0:
            return 0
        return round((self.score / self.max_score) * 100)

    def reasons(self) -> List[str]:
        """Kullanıcıya gösterilecek 'neden bu meslek' gerekçeleri."""
        out = []
        if self.matched_tags:
            out.append(f"İlgi alanların: {', '.join(self.matched_tags)}")
        if self.matched_values:
            out.append(f"Önemsediklerin: {', '.join(self.matched_values)}")
        if self.matched_style:
            out.append(f"Çalışma tarzın: {', '.join(self.matched_style)}")
        return out


# --------------------------------------------------------------------------- #
# Eşleştirme
# --------------------------------------------------------------------------- #
def _overlap(a: List[str], b: List[str]) -> List[str]:
    bset = set(b)
    return [x for x in a if x in bset]


def score_career(profile: UserProfile, career: dict) -> MatchResult:
    """Bir profili tek bir mesleğe karşı puanlar."""
    m_tags = _overlap(profile.tags, career.get("tags", []))
    m_style = _overlap(profile.work_style, career.get("work_style", []))
    m_values = _overlap(profile.values, career.get("values", []))

    score = (
        len(m_tags) * WEIGHTS["tags"]
        + len(m_style) * WEIGHTS["work_style"]
        + len(m_values) * WEIGHTS["values"]
    )

    # max_score = kullanıcının seçtiği her şeyin bu meslekte karşılık bulması
    max_score = (
        len(profile.tags) * WEIGHTS["tags"]
        + len(profile.work_style) * WEIGHTS["work_style"]
        + len(profile.values) * WEIGHTS["values"]
    )

    return MatchResult(
        career=career,
        score=score,
        max_score=max_score,
        matched_tags=m_tags,
        matched_style=m_style,
        matched_values=m_values,
    )


def recommend(profile: UserProfile, careers: List[dict], top_n: int = 3) -> List[MatchResult]:
    """Profili tüm mesleklere karşı puanlar ve en iyi eşleşmeleri döndürür."""
    results = [score_career(profile, c) for c in careers]
    # Skoru olmayanları ele; skora göre azalan sırala
    results = [r for r in results if r.score > 0]
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_n]


def surprise_pick(profile: UserProfile, careers: List[dict]) -> MatchResult | None:
    """
    'Beni şaşırt': profile bir miktar uyan AMA yüksek surprise_factor'a sahip
    (yani beklenmedik / niş) bir meslek seçer.
    """
    scored = [score_career(profile, c) for c in careers]
    # En az bir eşleşmesi olan ve sürpriz katsayısı yüksek olanları öne al
    candidates = [r for r in scored if r.score > 0]
    if not candidates:
        candidates = scored  # profil boşsa hepsinden seç
    candidates.sort(
        key=lambda r: (r.career.get("surprise_factor", 1), r.score),
        reverse=True,
    )
    return candidates[0] if candidates else None


# --------------------------------------------------------------------------- #
# Serbest metin → profil
# --------------------------------------------------------------------------- #
def _normalize(text: str) -> str:
    """Küçük harfe çevir + Türkçe aksanları sadeleştir (eşleştirme kolaylığı için)."""
    text = text.lower()
    # basit türkçe normalizasyonu
    replace = {"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "î": "i", "â": "a"}
    text = "".join(replace.get(ch, ch) for ch in text)
    return unicodedata.normalize("NFKD", text)


def profile_from_text(text: str, keywords: Dict[str, List[str]]) -> UserProfile:
    """
    Serbest metinden ilgi etiketleri çıkarır.
    Örn: 'sanat ve teknolojiyi seven biriyim' -> tags=['sanat', 'teknoloji']
    """
    norm = _normalize(text)
    found: List[str] = []
    for tag, words in keywords.items():
        for w in words:
            if _normalize(w) in norm:
                found.append(tag)
                break
    return UserProfile(tags=found)


def find_career_by_name(query: str, careers: List[dict]) -> dict | None:
    """İsim veya id ile meslek arar (kısmi, aksan-duyarsız)."""
    q = _normalize(query).strip()
    if not q:
        return None
    # önce tam id / tam isim
    for c in careers:
        if _normalize(c["id"]) == q or _normalize(c["name"]) == q:
            return c
    # sonra kısmi eşleşme
    for c in careers:
        if q in _normalize(c["name"]) or q in _normalize(c["id"]):
            return c
    return None