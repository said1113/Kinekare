"""
bot.py
------
Kariyer Danışmanı Discord Botu — AI (Google Gemini) destekli DEMO sürümü.

Beyin: ai_advisor.py (Gemini). Meslek veritabanı: data/careers.json.
Kullanıcı verileri: storage.py → data/users.db (SQLite).

Etkileşim yolları:
  • /kesfet   → menülerle profil topla, AI kişisel öneri üretsin (seçimler kaydedilir)
  • /gecis    → "işimden sıkıldım" modu (kariyer geçişi)
  • /sor      → AI'a kariyer sorusu sor (son 10 soru-cevabı hatırlar)
  • /profilim → kayıtlı profili ve son önerileri gör
  • /unut     → profili ve sohbet hafızasını sil
  • /meslek /karsilastir → önce veritabanı, listede yoksa AI
  • /surpriz  → AI listede olmayan niş bir meslek bulur
  • Tüm slash komutlarının cevabını sadece kullanan kişi görür
  • Kanalda @bot → herkese açık, hafızasız cevap
  • DM → /sor gibi kişisel hafızalı sohbet
  • Butonlar & menüler her adımda

Çalıştırma:
  1) pip install -r requirements.txt
  2) .env dosyasına DISCORD_TOKEN ve GEMINI_API_KEY ekle
  3) python bot.py
"""

from __future__ import annotations

import asyncio
import os
import random

from dotenv import load_dotenv

# .env, ai_advisor'dan ÖNCE yüklenmeli: ai_advisor model adını import anında okur.
load_dotenv()

import discord  # noqa: E402
from discord import app_commands  # noqa: E402
from discord.ext import commands  # noqa: E402

import ai_advisor  # noqa: E402
import storage  # noqa: E402
from matching import load_careers, find_career_by_name  # noqa: E402

TOKEN = os.getenv("DISCORD_TOKEN")

CAREERS = load_careers()
storage.init_db()
BRAND_COLOR = 0x4285F4  # Google mavisi

# --------------------------------------------------------------------------- #
# Menü seçenekleri (görünen etiket -> kaydedilen değer)
# --------------------------------------------------------------------------- #
INTEREST_OPTIONS = [
    ("💻 Teknoloji", "teknoloji"), ("🎨 Sanat", "sanat"),
    ("🤝 İnsanlar", "insanlar"), ("🌿 Doğa / Çevre", "doga"),
    ("📊 Analiz / Problem çözme", "analiz"), ("✨ Yaratıcılık", "yaraticilik"),
    ("✍️ Yazma", "yazma"), ("🎤 Konuşma / Sunum", "konusma"),
    ("🖌️ Tasarım", "tasarim"), ("🩺 Sağlık", "saglik"),
    ("💼 İş / Girişim", "is-girisim"), ("🎮 Oyun", "oyun"),
    ("🔬 Bilim", "bilim"), ("🔧 El becerisi", "el-becerisi"),
    ("🗂️ Düzen / Organizasyon", "duzen"), ("🧭 Macera / Risk", "macera"),
]
STYLE_OPTIONS = [
    ("👥 Takımla çalışmak", "takım"), ("🧍 Bağımsız çalışmak", "bağımsız"),
    ("🏠 Uzaktan çalışmak", "uzaktan"), ("🏢 Ofiste çalışmak", "ofis"),
    ("🕒 Esnek saatler", "esnek çalışma"), ("📋 Yapılandırılmış / planlı", "planlı"),
    ("🌤️ Açık havada", "açık havada"), ("⚡ Hızlı tempo", "hızlı tempo"),
]
VALUE_OPTIONS = [
    ("❤️ İnsanlara yardım etmek", "insanlara yardım etmek"),
    ("💰 İyi kazanç", "iyi kazanç"),
    ("🕊️ Özgürlük / esneklik", "özgürlük"),
    ("🛠️ Bir şey yaratmak", "bir şey yaratmak"),
    ("🛡️ İstikrar / güvenlik", "istikrar"),
    ("📚 Sürekli öğrenmek", "öğrenmek"),
    ("🌍 Fark yaratmak / etki", "fark yaratmak"),
]

# kaydedilen değer -> emojisiz okunur etiket (örn. "is-girisim" -> "İş / Girişim")
_LABELS = {v: (l.split(" ", 1)[1] if " " in l else l)
           for l, v in (INTEREST_OPTIONS + STYLE_OPTIONS + VALUE_OPTIONS)}


def labels(values: list[str]) -> str:
    return ", ".join(_LABELS.get(v, v) for v in values) or "—"


def _opts(pairs, selected=()):
    """Menü seçenekleri; `selected` içindekiler önceden işaretli gelir."""
    return [discord.SelectOption(label=lbl, value=val, default=val in selected)
            for lbl, val in pairs]


def _valid(values: list[str], pairs) -> list[str]:
    """Kayıtlı değerlerden sadece hâlâ menüde olanları tutar."""
    allowed = {v for _, v in pairs}
    return [v for v in values if v in allowed]


def profile_to_text(tags, style, values, transition=False) -> str:
    """Menü seçimlerini AI'ın anlayacağı doğal bir cümleye çevirir."""
    parts = []
    if tags:
        parts.append("İlgi alanlarım: " + labels(tags))
    if style:
        parts.append("Çalışma tarzım: " + labels(style))
    if values:
        parts.append("Benim için önemli olanlar: " + labels(values))
    text = ". ".join(parts)
    if transition:
        text = "İşimden sıkıldım ve kariyer değişikliği düşünüyorum. " + text
    return text or "Kendime uygun bir kariyer arıyorum."


def profile_summary(p: dict) -> str:
    return (f"• **İlgi alanları:** {labels(p['tags'])}\n"
            f"• **Çalışma tarzı:** {labels(p['styles'])}\n"
            f"• **Önemli olanlar:** {labels(p['values'])}")


# --------------------------------------------------------------------------- #
# Embed yardımcıları
# --------------------------------------------------------------------------- #
def recommend_embed(data: dict, transition=False) -> discord.Embed:
    title = "🔄 Sana uygun kariyer geçişleri" if transition else "🎯 Sana özel kariyer önerileri"
    emb = discord.Embed(title=title, description=data.get("intro") or "", color=BRAND_COLOR)
    for r in data.get("recommendations", []):
        emb.add_field(
            name=f"{r.get('emoji','▪️')} {r.get('name','')}",
            value=(f"{r.get('why_fits','')}\n"
                   f"👣 **İlk adım:** {r.get('first_step','—')}\n"
                   f"💰 {r.get('salary_range','—')}"),
            inline=False,
        )
    if data.get("encouragement"):
        emb.add_field(name="💬 Sırada", value=data["encouragement"], inline=False)
    emb.set_footer(text="Gemini ile üretildi • bu bir demo sürümüdür")
    return emb


def chat_embed(question: str, answer: str) -> discord.Embed:
    emb = discord.Embed(title="🤖 Kariyer Danışmanı", description=answer[:4000],
                        color=BRAND_COLOR)
    emb.set_author(name=question[:200])
    emb.set_footer(text="Gemini ile üretildi • demo")
    return emb


def career_embed(c: dict) -> discord.Embed:
    """Meslek kartı. Hem veritabanı kaydı hem AI'ın ürettiği kart için çalışır."""
    emb = discord.Embed(title=f"{c.get('emoji','▪️')} {c.get('name','Meslek')}",
                        description=c.get("description", ""), color=BRAND_COLOR)
    emb.add_field(name="🗂️ Kategori", value=c.get("category") or "—", inline=True)
    emb.add_field(name="💰 Maaş aralığı", value=c.get("salary_range") or "—", inline=True)
    if c.get("skills_required"):
        emb.add_field(name="🧩 Gerekli beceriler",
                      value=", ".join(c["skills_required"])[:1024], inline=False)
    if c.get("first_step"):
        emb.add_field(name="👣 İlk adım", value=c["first_step"][:1024], inline=False)
    if c.get("roadmap"):
        emb.add_field(name="🗺️ Yol haritası",
                      value="\n".join(f"• {s}" for s in c["roadmap"])[:1024], inline=False)
    if c.get("resources"):
        links = "\n".join(f"• [{r['title']}]({r['url']})" for r in c["resources"])
        emb.add_field(name="📚 Kaynaklar", value=links[:1024], inline=False)
    return emb


# --------------------------------------------------------------------------- #
# AI çağrıları (google-genai senkron; olay döngüsünü bloke etmemek için ayrı
# iş parçacığında çalıştırıyoruz) + kalıcı sohbet hafızası
# --------------------------------------------------------------------------- #
async def ai_recommend(profile_text: str) -> dict:
    return await asyncio.to_thread(ai_advisor.recommend, profile_text, CAREERS)


def _history_for(user_id: int) -> list[dict]:
    """Kullanıcının kayıtlı hafızasını ai_advisor.chat'in beklediği biçime çevirir."""
    history = []
    for question, answer in storage.get_memory(user_id):
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
    return history


def remember(user_id: int, question: str, answer: str) -> None:
    if question and answer:
        storage.add_memory(user_id, question, answer)


async def ai_chat(user_id: int, user_input: str) -> str:
    """Kullanıcının geçmişiyle birlikte sorar; başarılı cevabı hafızaya ekler."""
    history = _history_for(user_id)
    answer = await asyncio.to_thread(ai_advisor.chat, user_input, CAREERS, history)
    remember(user_id, user_input, answer)
    return answer


async def ai_chat_stateless(user_input: str) -> str:
    """Hafızasız soru: geçmiş kullanılmaz, cevap hiçbir yere kaydedilmez."""
    return await asyncio.to_thread(ai_advisor.chat, user_input, CAREERS, None)


async def run_recommendation(interaction: discord.Interaction, tags, styles, values,
                             transition: bool) -> None:
    """Seçimleri kaydeder, AI'dan öneri alır ve mesajı sonuçla günceller."""
    storage.save_profile(interaction.user.id, tags, styles, values)
    profile_text = profile_to_text(tags, styles, values, transition)
    await interaction.response.defer()
    try:
        data = await ai_recommend(profile_text)
    except Exception as e:  # noqa: BLE001
        await interaction.edit_original_response(content=f"⚠️ {e}", embed=None, view=None)
        return
    # Önerileri kaydet; "Bir Soru Sor" ile gelen takip soruları da bunları bilsin
    names = [r.get("name", "") for r in data.get("recommendations", []) if r.get("name")]
    if names:
        storage.save_recommendations(interaction.user.id, names)
        remember(interaction.user.id, profile_text,
                 "Sana şu meslekleri önerdim: " + ", ".join(names))
    await interaction.edit_original_response(
        content=None,
        embed=recommend_embed(data, transition),
        view=ResultView(transition))


# --------------------------------------------------------------------------- #
# UI: Profil oluşturma menüleri
# --------------------------------------------------------------------------- #
class _MultiSelect(discord.ui.Select):
    def __init__(self, placeholder, options, target_attr, max_values):
        super().__init__(placeholder=placeholder, options=options,
                         min_values=0, max_values=max_values)
        self.target_attr = target_attr

    async def callback(self, interaction: discord.Interaction):
        setattr(self.view, self.target_attr, self.values)
        await interaction.response.defer()


class ProfileView(discord.ui.View):
    """Üç menü + öneri butonu. `profile` verilirse eski seçimler işaretli gelir."""
    def __init__(self, transition_mode: bool = False, profile: dict | None = None):
        super().__init__(timeout=300)
        profile = profile or {}
        self.sel_tags = _valid(profile.get("tags", []), INTEREST_OPTIONS)[:5]
        self.sel_style = _valid(profile.get("styles", []), STYLE_OPTIONS)[:3]
        self.sel_values = _valid(profile.get("values", []), VALUE_OPTIONS)[:3]
        self.transition_mode = transition_mode

        self.add_item(_MultiSelect("1️⃣ İlgi alanların (birden fazla seçebilirsin)",
                                   _opts(INTEREST_OPTIONS, self.sel_tags), "sel_tags", 5))
        self.add_item(_MultiSelect("2️⃣ Nasıl çalışmayı seversin?",
                                   _opts(STYLE_OPTIONS, self.sel_style), "sel_style", 3))
        self.add_item(_MultiSelect("3️⃣ Senin için ne önemli?",
                                   _opts(VALUE_OPTIONS, self.sel_values), "sel_values", 3))

    @discord.ui.button(label="AI Önerisi Al", style=discord.ButtonStyle.success,
                       emoji="✨", row=3)
    async def generate(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (self.sel_tags or self.sel_style or self.sel_values):
            await interaction.response.send_message(
                "En az bir seçim yapmalısın 🙂", ephemeral=True)
            return
        await run_recommendation(interaction, self.sel_tags, self.sel_style,
                                 self.sel_values, self.transition_mode)


class WelcomeBackView(discord.ui.View):
    """Kayıtlı profili olan kullanıcıya: aynı seçimlerle devam et ya da değiştir."""
    def __init__(self, profile: dict, transition_mode: bool = False):
        super().__init__(timeout=300)
        self.profile = profile
        self.transition_mode = transition_mode

    @discord.ui.button(label="Aynı seçimlerle öneri al", style=discord.ButtonStyle.success,
                       emoji="✨")
    async def same(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = self.profile
        await run_recommendation(interaction, p["tags"], p["styles"], p["values"],
                                 self.transition_mode)

    @discord.ui.button(label="Seçimleri değiştir", style=discord.ButtonStyle.secondary,
                       emoji="✏️")
    async def change(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Seçimlerini güncelle 👇 (eski seçimlerin işaretli)",
            view=ProfileView(self.transition_mode, self.profile))


# --------------------------------------------------------------------------- #
# UI: Sonuç ekranı — soru sor / yeniden başla
# --------------------------------------------------------------------------- #
class QuestionModal(discord.ui.Modal, title="Kariyer Danışmanına Sor"):
    soru = discord.ui.TextInput(
        label="Sorun ne?", style=discord.TextStyle.paragraph,
        placeholder="Örn: Grafik tasarımcı olmak için hangi becerilere ihtiyacım var?",
        max_length=400)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            answer = await ai_chat(interaction.user.id, str(self.soru))
        except Exception as e:  # noqa: BLE001
            answer = f"⚠️ {e}"
        await interaction.followup.send(
            embed=chat_embed(str(self.soru), answer), ephemeral=True)


class ResultView(discord.ui.View):
    def __init__(self, transition_mode: bool = False):
        super().__init__(timeout=300)
        self.transition_mode = transition_mode

    @discord.ui.button(label="Bir Soru Sor", style=discord.ButtonStyle.primary, emoji="💬")
    async def ask(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(QuestionModal())

    @discord.ui.button(label="Yeniden Başla", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Seçimlerini güncelle 👇 (son seçimlerin işaretli)",
            embed=None,
            view=ProfileView(self.transition_mode, storage.get_profile(interaction.user.id)))


class ConfirmDeleteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Evet, hepsini sil", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        storage.delete_user(interaction.user.id)
        await interaction.response.edit_message(
            content="🧹 Profilin ve sohbet hafızan silindi. Yeni bir başlangıç yapabiliriz.",
            view=None)

    @discord.ui.button(label="Vazgeç", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Tamam, hiçbir şey silinmedi.", view=None)


# --------------------------------------------------------------------------- #
# Bot
# --------------------------------------------------------------------------- #
intents = discord.Intents.default()
intents.message_content = True  # serbest metin için (dev portalında da açılmalı)
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Giriş: {bot.user} | {len(synced)} komut senkronize edildi")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Komut senkronizasyonu başarısız: {e}")


# ---- Slash komutları ------------------------------------------------------ #
async def start_profile_flow(interaction: discord.Interaction, transition: bool) -> None:
    """/kesfet ve /gecis için ortak başlangıç: kayıtlı profil varsa onu hatırlat."""
    if transition:
        intro = "**Yeni bir başlangıç?** 🔄\n"
    else:
        intro = "**Kariyer keşfine hoş geldin!** ✨\n"
    profile = storage.get_profile(interaction.user.id)
    if profile and (profile["tags"] or profile["styles"] or profile["values"]):
        await interaction.response.send_message(
            content=(intro + "Seni hatırlıyorum! Geçen sefer şunları seçmiştin:\n"
                     + profile_summary(profile)),
            view=WelcomeBackView(profile, transition), ephemeral=True)
        return
    await interaction.response.send_message(
        content=intro + "Menülerden seç, sonra **AI Önerisi Al**'a bas — "
                        "sana özel öneriler üreteyim.",
        view=ProfileView(transition), ephemeral=True)


@bot.tree.command(name="kesfet", description="AI ile kişiselleştirilmiş kariyer önerileri al")
async def kesfet(interaction: discord.Interaction):
    await start_profile_flow(interaction, transition=False)


@bot.tree.command(name="gecis", description="İşinden sıkıldıysan: AI destekli kariyer geçişi")
async def gecis(interaction: discord.Interaction):
    await start_profile_flow(interaction, transition=True)


@bot.tree.command(name="sor", description="Kariyer danışmanına soru sor (son 10 soruyu hatırlar)")
@app_commands.describe(soru="Merak ettiğin her şey")
async def sor(interaction: discord.Interaction, soru: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        answer = await ai_chat(interaction.user.id, soru)
    except Exception as e:  # noqa: BLE001
        answer = f"⚠️ {e}"
    await interaction.followup.send(embed=chat_embed(soru, answer), ephemeral=True)


@bot.tree.command(name="profilim", description="Kayıtlı profilini ve son önerilerini gör")
async def profilim(interaction: discord. Interaction):
    uid = interaction.user.id
    p = storage.get_profile(uid)
    mem = storage.memory_count(uid)
    if not p and not mem:
        await interaction.response.send_message(
            "Henüz kayıtlı bir profilin yok. `/kesfet` ile başlayabilirsin 🎯", ephemeral=True)
        return
    emb = discord.Embed(title=f"👤 {interaction.user.display_name} — Profil",
                        description="Silmek istersen `/unut` yazman yeterli.",
                        color=BRAND_COLOR)
    if p:
        emb.add_field(name="🎯 İlgi alanları", value=labels(p["tags"]), inline=False)
        emb.add_field(name="🧭 Çalışma tarzı", value=labels(p["styles"]), inline=False)
        emb.add_field(name="❤️ Önemli olanlar", value=labels(p["values"]), inline=False)
        emb.add_field(name="✨ Son önerilerin",
                      value=", ".join(p["recommendations"]) or "—", inline=False)
        emb.set_footer(text=f"Son güncelleme: {p['updated_at']}")
    emb.add_field(name="💬 Sohbet hafızası",
                  value=f"{mem}/{storage.MEMORY_SIZE} soru-cevap", inline=False)
    await interaction.response.send_message(embed=emb, ephemeral=True)


@bot.tree.command(name="unut", description="Profilini ve sohbet hafızanı sil")
async def unut(interaction: discord.Interaction):
    await interaction.response.send_message(
        "⚠️ Profilin (seçimlerin, son önerilerin) ve sohbet hafızan **kalıcı olarak** "
        "silinecek. Emin misin?",
        view=ConfirmDeleteView(), ephemeral=True)


async def career_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    return [
        app_commands.Choice(name=c["name"], value=c["id"])
        for c in CAREERS if cur in c["name"].lower() or cur in c["id"].lower()
    ][:25]


@bot.tree.command(name="meslek", description="Bir mesleğin detaylarını gör (listede yoksa AI ile)")
@app_commands.describe(meslek="Meslek adı (listeden seç ya da istediğini yaz)")
@app_commands.autocomplete(meslek=career_autocomplete)
async def meslek(interaction: discord.Interaction, meslek: str):
    c = find_career_by_name(meslek, CAREERS)
    if c is not None:
        await interaction.response.send_message(embed=career_embed(c), ephemeral=True)
        return
    # Veritabanında yok → kartı AI üretsin
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await asyncio.to_thread(ai_advisor.describe_career, meslek)
    except Exception as e:  # noqa: BLE001
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        return
    if data.get("error") or not data.get("name"):
        await interaction.followup.send(
            f"🤔 \"{meslek}\" bir meslek gibi görünmüyor. Başka bir şey dener misin?",
            ephemeral=True)
        return
    emb = career_embed(data)
    emb.set_footer(text="Listede yok • Gemini ile üretildi • maaşlar tahminidir")
    await interaction.followup.send(embed=emb, ephemeral=True)


@bot.tree.command(name="karsilastir", description="İki mesleği karşılaştır (listede olmayanlar AI ile)")
@app_commands.describe(meslek1="Birinci meslek (listeden seç ya da istediğini yaz)",
                       meslek2="İkinci meslek (listeden seç ya da istediğini yaz)")
@app_commands.autocomplete(meslek1=career_autocomplete, meslek2=career_autocomplete)
async def karsilastir(interaction: discord.Interaction, meslek1: str, meslek2: str):
    a = find_career_by_name(meslek1, CAREERS)
    b = find_career_by_name(meslek2, CAREERS)
    if not a or not b:
        # En az biri veritabanında yok → karşılaştırmayı AI yapsın
        name_a = a["name"] if a else meslek1
        name_b = b["name"] if b else meslek2
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            answer = await asyncio.to_thread(ai_advisor.compare, name_a, name_b, CAREERS)
        except Exception as e:  # noqa: BLE001
            answer = f"⚠️ {e}"
        emb = discord.Embed(title=f"⚖️ {name_a} vs {name_b}",
                            description=answer[:4000], color=BRAND_COLOR)
        emb.set_footer(text="Gemini ile üretildi • maaşlar tahminidir")
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    emb = discord.Embed(title=f"⚖️ {a['name']} vs {b['name']}", color=BRAND_COLOR)
    for c in (a, b):
        emb.add_field(
            name=f"{c.get('emoji','▪️')} {c['name']}",
            value=(f"**Kategori:** {c.get('category','—')}\n"
                   f"**Maaş:** {c.get('salary_range','—')}\n"
                   f"**Beceriler:** {', '.join(c.get('skills_required', [])[:3])}\n"
                   f"**İlk adım:** {c.get('first_step','—')}"),
            inline=True)
    await interaction.response.send_message(embed=emb, ephemeral=True)


@bot.tree.command(name="surpriz", description="Hiç duymadığın ilginç bir meslek keşfet")
async def surpriz(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await asyncio.to_thread(ai_advisor.surprise_career, CAREERS)
        if not data.get("name"):
            raise RuntimeError("boş yanıt")
        emb = career_embed(data)
        emb.set_footer(text="Gemini ile üretildi • maaşlar tahminidir")
    except Exception:  # noqa: BLE001
        # AI'a ulaşılamazsa veritabanındaki niş mesleklerden rastgele birini göster
        niche = [c for c in CAREERS if c.get("surprise_factor", 1) >= 3] or CAREERS
        emb = career_embed(random.choice(niche))
        emb.set_footer(text="Veritabanından seçildi")
    emb.title = "🎲 " + emb.title
    await interaction.followup.send(embed=emb, ephemeral=True)


@bot.tree.command(name="yardim", description="Botun ne yapabileceğini gör")
async def yardim(interaction: discord.Interaction):
    emb = discord.Embed(title="🤖 Kariyer Danışmanı — Yardım", color=BRAND_COLOR,
                        description="AI ile yeni ve ilginç kariyer yolları öneriyorum!")
    emb.add_field(name="/kesfet", value="Menülerle profil oluştur → AI kişisel öneri üretsin", inline=False)
    emb.add_field(name="/gecis", value="İşinden sıkıldıysan kariyer geçişi önerileri", inline=False)
    emb.add_field(name="/sor", value="Kariyer sorusu sor — son 10 soruyu hatırlarım", inline=False)
    emb.add_field(name="/profilim", value="Kayıtlı seçimlerini ve son önerilerini gör", inline=False)
    emb.add_field(name="/unut", value="Profilini ve sohbet hafızanı sil", inline=False)
    emb.add_field(name="/meslek", value="Herhangi bir mesleğin detaylarını gör", inline=False)
    emb.add_field(name="/karsilastir", value="Herhangi iki mesleği karşılaştır", inline=False)
    emb.add_field(name="/surpriz", value="Hiç duymadığın ilginç bir meslek", inline=False)
    emb.add_field(name="💬 Botu etiketle",
                  value="Kanalda beni etiketleyip yazarsan **herkese açık** cevap veririm ve "
                        "o soruyu hatırlamam. Örn. *\"@bot sanatı seviyorum ama teknolojiye de "
                        "ilgim var\"*",
                  inline=False)
    emb.set_footer(text="Komutların cevaplarını sadece sen görürsün.")
    await interaction.response.send_message(embed=emb, ephemeral=True)


# ---- Serbest metin etkileşimi (AI sohbet) -------------------------------- #
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    is_dm = isinstance(message.channel, discord.DMChannel)
    if bot.user in message.mentions or is_dm:
        text = (message.content.replace(f"<@{bot.user.id}>", "")
                .replace(f"<@!{bot.user.id}>", "").strip())
        if not text:
            await message.reply("Merhaba! Bir soru yaz ya da `/kesfet` dene 🎯")
        else:
            async with message.channel.typing():
                try:
                    if is_dm:
                        # DM özel: /sor gibi kişisel hafızayı kullanır
                        answer = await ai_chat(message.author.id, text)
                    else:
                        # Kanalda etiketleme: herkese açık, hafızasız
                        answer = await ai_chat_stateless(text)
                except Exception as e:  # noqa: BLE001
                    answer = f"⚠️ {e}"
            await message.reply(answer[:2000])
    await bot.process_commands(message)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN bulunamadı. .env dosyasını kontrol et.")
    bot.run(TOKEN)