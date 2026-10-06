"""
ai_advisor.py
-------------
Botun "beyni". Google Gemini kullanarak kullanıcıya kişiye özel,
sohbet tarzında kariyer tavsiyesi üretir.

Önemli tasarım: AI, `careers.json` veritabanını BAĞLAM (grounding) olarak alır.
Böylece öneriler sizin doldurduğunuz veriye dayanır — AI rastgele uydurmaz.

Gerekli ortam değişkenleri (.env):
  GEMINI_API_KEY         = ...                   (zorunlu — https://aistudio.google.com/apikey)
  GEMINI_MODEL           = gemini-3.5-flash-lite (opsiyonel, ana model)
  GEMINI_FALLBACK_MODELS = gemini-3.6-flash,gemini-3.7-flash (opsiyonel, yedekler)
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import Optional

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

# İstemci ilk çağrıda oluşturulur (lazy). Anahtar yoksa anlamlı hata veririz.
_client: Optional[genai.Client] = None

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Ana model yoğunsa sırayla denenecek yedek modeller (virgülle ayrılmış).
FALLBACK_MODELS = [
    m.strip() for m in
    os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.6-flash,gemini-3.7-flash").split(",")
    if m.strip() and m.strip() != MODEL
]

# Hızlı ve ucuz yanıt için düşük "düşünme" seviyesi (demo/sohbet için ideal).
# LOW; 3.5-flash-lite, 3.6/3.7/3.8-flash modellerinin hepsinde destekleniyor.
_THINKING = types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
            raise RuntimeError(
                "GEMINI_API_KEY tanımlı değil. .env dosyasına ekle "
                "(https://aistudio.google.com/apikey).")
        _client = genai.Client()  # GEMINI_API_KEY ortamdan otomatik okunur
    return _client


# --------------------------------------------------------------------------- #
# İstek gönderme: geçici hatalarda yeniden dener, gerekirse yedek modele geçer
# --------------------------------------------------------------------------- #
# Sunucu yoğunluğu / geçici hatalar — bunlarda tekrar denemek mantıklı.
_RETRYABLE_CODES = {429, 500, 502, 503, 504}
# Bu kodlarda aynı modelde ısrar etmek yerine hemen sıradaki modele geç.
# (404 = model adı yanlış veya model kaldırılmış)
_SWITCH_CODES = _RETRYABLE_CODES | {404}


def _friendly_error(e: Exception) -> str:
    code = getattr(e, "code", None)
    if code == 503:
        return ("Gemini şu an çok yoğun (503), yedek modeller de yanıt vermedi. "
                "Bu geçici bir durum — birkaç dakika sonra tekrar dene.")
    if code == 429:
        return ("Ücretsiz kota sınırına ulaşıldı (429). Biraz bekleyip "
                "tekrar dene.")
    if code == 404:
        return "Model bulunamadı (404). .env dosyasındaki model adlarını kontrol et."
    if code in (401, 403):
        return "API anahtarı reddedildi (yetki). GEMINI_API_KEY'i kontrol et."
    return f"AI hatası: {e}"


def _generate(contents, config, retries_per_model: int = 2):
    """
    generate_content'i çağırır. Sıra: ana model, sonra FALLBACK_MODELS.
    - Geçici hatada (503/429/5xx) aynı modeli 1 kez daha dener, olmazsa sıradakine geçer.
    - 404'te doğrudan sıradaki modele geçer.
    - Yetki hatası (401/403) gibi kalıcı hatalarda hemen vazgeçer.
    """
    client = _get_client()
    last_err: Optional[Exception] = None
    for model in [MODEL, *FALLBACK_MODELS]:
        for attempt in range(retries_per_model):
            try:
                resp = client.models.generate_content(
                    model=model, contents=contents, config=config)
                if model != MODEL:
                    print(f"ℹ️ Yedek model kullanıldı: {model}")
                return resp
            except genai_errors.APIError as e:
                last_err = e
                code = getattr(e, "code", None)
                if code not in _SWITCH_CODES:
                    raise RuntimeError(_friendly_error(e)) from e
                if code == 404:
                    print(f"⚠️ Model bulunamadı, atlanıyor: {model}")
                    break  # bu modeli tekrar deneme, sıradakine geç
                if attempt < retries_per_model - 1:
                    time.sleep(2)  # kısa bekle, aynı modeli bir kez daha dene
                else:
                    print(f"⚠️ {model} yanıt vermedi ({code}), sıradaki modele geçiliyor")
    raise RuntimeError(_friendly_error(last_err) if last_err else "Bilinmeyen AI hatası")


# --------------------------------------------------------------------------- #
# Sistem talimatı — botun kişiliği ve kuralları
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """Sen sıcak, cesaretlendirici ve pratik bir kariyer danışmanı botusun.
Hedef kitlen: yeni kariyer yolları arayan gençler ve işinden sıkılıp değişiklik
isteyen yetişkinler.

KURALLAR:
- Sana verilen MESLEK VERİTABANI'ndaki meslekleri temel al. Öncelikle bu listedeki
  meslekleri öner; ama kullanıcının profiline daha iyi uyan komşu bir alan varsa
  kısaca ondan da bahsedebilirsin.
- Kişiselleştir: kullanıcının yazdığı ilgi, beceri, değer ve durumu dikkate al.
- Basit ve anlaşılır ol. Jargon kullanma. Genç bir okuyucunun anlayacağı dilde yaz.
- Her öneride NEDEN uygun olduğunu açıkla — kara kutu olma.
- Gerçekçi ol: maaş, gerekli beceri ve ilk adımı net söyle.
- Kısa tut. Uzun paragraflardan kaçın.
- Türkçe yanıt ver.
"""


def _careers_context(careers: list[dict]) -> str:
    """Veritabanını AI'a verilecek kompakt bir metne dönüştürür."""
    lines = []
    for c in careers:
        lines.append(
            f"- {c['name']} ({c.get('category','')}): {c.get('description','')} "
            f"| İlgi: {', '.join(c.get('tags', []))} "
            f"| Beceriler: {', '.join(c.get('skills_required', []))} "
            f"| Maaş: {c.get('salary_range','?')} "
            f"| İlk adım: {c.get('first_step','?')}"
        )
    return "MESLEK VERİTABANI:\n" + "\n".join(lines)


def _strip_json_fence(text: str) -> str:
    """Model bazen ```json ...``` içine sarar; temizle."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    return t.strip()


# --------------------------------------------------------------------------- #
# 1) Yapılandırılmış öneri — embed kartlarına dönüştürmek için JSON döndürür
# --------------------------------------------------------------------------- #
RECOMMEND_INSTRUCTION = """Kullanıcının anlattıklarına göre EN UYGUN 3 mesleği seç ve
SADECE aşağıdaki JSON şemasında yanıt ver (başka hiçbir metin ekleme):

{
  "intro": "kullanıcıya 1-2 cümlelik kişisel giriş",
  "recommendations": [
    {
      "name": "meslek adı",
      "emoji": "tek bir emoji",
      "why_fits": "neden bu kişiye uygun (1-2 cümle)",
      "first_step": "bugün atabileceği somut ilk adım",
      "salary_range": "maaş aralığı"
    }
  ],
  "encouragement": "cesaretlendirici kapanış + bir takip sorusu"
}"""


def recommend(user_input: str, careers: list[dict]) -> dict:
    """
    Kullanıcının serbest metnine / profil özetine göre yapılandırılmış öneri üretir.
    Dönen dict embed kartlarına çevrilir.
    """
    client = _get_client()
    prompt = (
        f"{_careers_context(careers)}\n\n"
        f"KULLANICI: {user_input}\n\n"
        f"{RECOMMEND_INSTRUCTION}"
    )
    resp = _generate(
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=1500,
            response_mime_type="application/json",  # doğrudan JSON iste
            thinking_config=_THINKING,
        ),
    )
    raw = _strip_json_fence(resp.text)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Nadiren JSON bozulursa: metni tek bir "intro" olarak geri ver
        return {"intro": raw or "Bir öneri üretemedim, tekrar dener misin?",
                "recommendations": [], "encouragement": ""}


# --------------------------------------------------------------------------- #
# 2) Serbest sohbet — takip soruları, genel kariyer danışmanlığı
# --------------------------------------------------------------------------- #
def chat(user_input: str, careers: list[dict],
         history: Optional[list[dict]] = None) -> str:
    """
    Sohbet tarzı, düz metin yanıt üretir (takip soruları, "X mesleği bana uygun mu?"
    gibi serbest sorular için).
    `history` = önceki mesajlar [{"role": "user"/"assistant", "content": "..."}]
    """
    client = _get_client()

    # Geçmişi Gemini biçimine çevir (Gemini rolleri: "user" / "model").
    contents: list[types.Content] = []
    for msg in (history or []):
        role = "model" if msg.get("role") == "assistant" else "user"
        contents.append(types.Content(
            role=role, parts=[types.Part.from_text(text=msg["content"])]))

    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(
            text=f"{_careers_context(careers)}\n\nKULLANICI SORUSU: {user_input}")]))

    resp = _generate(
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=1000,
            thinking_config=_THINKING,
        ),
    )
    return (resp.text or "").strip()


# --------------------------------------------------------------------------- #
# 3) İki mesleği karşılaştır — veritabanında olmayan meslekler için de çalışır
# --------------------------------------------------------------------------- #
COMPARE_INSTRUCTION = """İki mesleği karşılaştır. Meslek veritabanında varsa oradaki
bilgiyi kullan; yoksa genel bilgine dayan. Kısa ve net yaz, şu başlıkları kullan:

**Ne yapar?** (her biri için 1 cümle)
**Gerekli beceriler**
**Yaklaşık maaş (Türkiye)**
**Çalışma ortamı**
**Kimlere daha uygun?**

Maaşların tahmini olduğunu belirt."""


def compare(career_a: str, career_b: str, careers: list[dict]) -> str:
    """İki mesleği AI ile karşılaştırır (düz metin döndürür)."""
    prompt = (
        f"{_careers_context(careers)}\n\n"
        f"KARŞILAŞTIRILACAK MESLEKLER: {career_a} ve {career_b}\n\n"
        f"{COMPARE_INSTRUCTION}"
    )
    resp = _generate(
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=1200,
            thinking_config=_THINKING,
        ),
    )
    return (resp.text or "").strip()


# --------------------------------------------------------------------------- #
# 4) Meslek kartı — veritabanında olmayan meslekler ve /surpriz için
# --------------------------------------------------------------------------- #
# Dönen JSON, careers.json ile aynı alan adlarını kullanır; böylece bot aynı
# kart tasarımıyla gösterebilir.
CAREER_SCHEMA = """{
  "name": "meslek adı (Türkçe)",
  "emoji": "tek bir emoji",
  "category": "kategori, örn. Teknoloji / Tasarım",
  "description": "ne yaptığını anlatan 2 cümle",
  "skills_required": ["beceri 1", "beceri 2", "beceri 3", "beceri 4"],
  "salary_range": "Türkiye için yaklaşık aylık maaş aralığı, sonuna (yaklaşık) yaz",
  "first_step": "bugün atılabilecek somut ve ücretsiz ilk adım",
  "roadmap": ["30 gün: ...", "60 gün: ...", "90 gün: ..."]
}"""

# /surpriz her seferinde farklı bir alandan seçsin diye rastgele tema veriyoruz.
SURPRISE_THEMES = [
    "uzay ve havacılık", "denizcilik ve okyanus", "tarım ve gıda teknolojisi",
    "müze, sanat ve kültürel miras", "spor ve performans", "oyun ve eğlence",
    "sağlık teknolojisi", "çevre ve iklim", "moda ve tekstil", "müzik ve ses",
    "hukuk ve etik", "şehircilik ve ulaşım", "hayvanlar ve doğa", "film ve animasyon",
    "yapay zeka ve veri", "el sanatları ve zanaat", "psikoloji ve insan davranışı",
    "turizm ve deneyim tasarımı", "enerji", "bilim iletişimi",
]


def _json_generate(prompt: str, max_tokens: int = 1200) -> dict:
    """JSON isteyip ayrıştırılmış dict döndürür; bozuk JSON'da RuntimeError verir."""
    resp = _generate(
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            thinking_config=_THINKING,
        ),
    )
    try:
        data = json.loads(_strip_json_fence(resp.text))
    except (json.JSONDecodeError, TypeError) as e:
        raise RuntimeError("AI geçerli bir yanıt üretemedi, tekrar dener misin?") from e
    if not isinstance(data, dict):
        raise RuntimeError("AI geçerli bir yanıt üretemedi, tekrar dener misin?")
    return data


def describe_career(name: str) -> dict:
    """
    Veritabanında olmayan bir meslek için kart verisi üretir.
    Girdi gerçek bir meslek değilse {"error": "..."} döner.
    """
    prompt = (
        f"KULLANICININ SORDUĞU MESLEK: {name}\n\n"
        "Bu meslek için SADECE aşağıdaki JSON şemasında yanıt ver:\n"
        f"{CAREER_SCHEMA}\n\n"
        "Eğer yazılan şey gerçek bir meslek değilse SADECE şunu döndür: "
        '{"error": "kısa açıklama"}'
    )
    return _json_generate(prompt)


def surprise_career(careers: list[dict]) -> dict:
    """Veritabanında OLMAYAN, niş ve ilginç bir meslek önerir."""
    existing = ", ".join(c["name"] for c in careers)
    theme = random.choice(SURPRISE_THEMES)
    prompt = (
        f"Şu alandan az bilinen, niş ama gerçek ve ilginç bir meslek seç: {theme}.\n"
        f"Bu listedeki meslekleri SEÇME: {existing}\n"
        "Gençlerin 'bunu hiç duymamıştım!' diyeceği bir meslek olsun.\n\n"
        "SADECE aşağıdaki JSON şemasında yanıt ver:\n"
        f"{CAREER_SCHEMA}"
    )
    return _json_generate(prompt)