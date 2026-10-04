"""
ai_advisor.py
-------------
Botun "beyni". Google Gemini (gemini-3.8-flash) kullanarak kullanıcıya kişiye özel,
sohbet tarzında kariyer tavsiyesi üretir.

Önemli tasarım: AI, `careers.json` veritabanını BAĞLAM (grounding) olarak alır.
Böylece öneriler sizin doldurduğunuz veriye dayanır — AI rastgele uydurmaz.

Gerekli ortam değişkenleri (.env):
  GEMINI_API_KEY = ...              (zorunlu — https://aistudio.google.com/apikey)
  GEMINI_MODEL   = gemini-3.8-flash (opsiyonel)
"""

from __future__ import annotations

import json
import os
from typing import Optional

from google import genai
from google.genai import types

# İstemci ilk çağrıda oluşturulur (lazy). Anahtar yoksa anlamlı hata veririz.
_client: Optional[genai.Client] = None

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# Hızlı ve ucuz yanıt için düşük "düşünme" seviyesi (demo/sohbet için ideal).
# NOT: gemini-3.8-flash yalnızca LOW/MEDIUM/HIGH destekler (MINIMAL hata verir).
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
    resp = client.models.generate_content(
        model=MODEL,
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

    resp = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=1000,
            thinking_config=_THINKING,
        ),
    )
    return (resp.text or "").strip()