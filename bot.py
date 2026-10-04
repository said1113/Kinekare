"""
bot.py
------
Kariyer Danışmanı Discord Botu — AI (Google Gemini) destekli DEMO sürümü.

Beyin: ai_advisor.py (gemini-3.8-flash). Veritabanı: data/careers.json.

Etkileşim yolları:
  • /kesfet  → menülerle profil topla, AI kişisel öneri üretsin
  • /gecis   → "işimden sıkıldım" modu (kariyer geçişi)
  • /sor     → AI'a serbest kariyer sorusu sor
  • /meslek /karsilastir /surpriz → veritabanından hızlı bilgi
  • Serbest metin (@bot veya DM) → AI ile sohbet
"""

from __future__ import annotations

import asyncio
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import ai_advisor
from matching import UserProfile, load_careers, find_career_by_name, surprise_pick

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

CAREERS = load_careers()
BRAND_COLOR = 0x4285F4  # Google mavisi

# --- Menü seçenekleri (görünen etiket -> AI'a gidecek kelime) ---
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


def _opts(pairs):
    return [discord.SelectOption(label=lbl, value=val) for lbl, val in pairs]


def profile_to_text(tags, style, values, transition=False) -> str:
    """Menü seçimlerini AI'ın anlayacağı doğal bir cümleye çevirir."""
    parts = []
    if tags:
        parts.append("İlgi alanlarım: " + ", ".join(tags))
    if style:
        parts.append("Çalışma tarzım: " + ", ".join(style))
    if values:
        parts.append("Benim için önemli olanlar: " + ", ".join(values))
    text = ". ".join(parts)
    if transition:
        text = "İşimden sıkıldım ve kariyer değişikliği düşünüyorum. " + text
    return text or "Kendime uygun bir kariyer arıyorum."


# --- Embed yardımcıları ---
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
    emb = discord.Embed(title=f"{c.get('emoji','▪️')} {c['name']}",
                        description=c["description"], color=BRAND_COLOR)
    emb.add_field(name="🗂️ Kategori", value=c.get("category", "—"), inline=True)
    emb.add_field(name="💰 Maaş aralığı", value=c.get("salary_range", "—"), inline=True)
    if c.get("skills_required"):
        emb.add_field(name="🧩 Gerekli beceriler",
                      value=", ".join(c["skills_required"]), inline=False)
    if c.get("first_step"):
        emb.add_field(name="👣 İlk adım", value=c["first_step"], inline=False)
    if c.get("roadmap"):
        emb.add_field(name="🗺️ Yol haritası",
                      value="\n".join(f"• {s}" for s in c["roadmap"]), inline=False)
    if c.get("resources"):
        links = "\n".join(f"• [{r['title']}]({r['url']})" for r in c["resources"])
        emb.add_field(name="📚 Kaynaklar", value=links, inline=False)
    return emb


# --- AI çağrılarını olay döngüsünü bloke etmeden çalıştır ---
# (google-genai istemcisi senkron; ayrı iş parçacığında çalıştırıyoruz)
async def ai_recommend(profile_text: str) -> dict:
    return await asyncio.to_thread(ai_advisor.recommend, profile_text, CAREERS)


async def ai_chat(user_input: str) -> str:
    return await asyncio.to_thread(ai_advisor.chat, user_input, CAREERS, None)


# --- UI: Profil menüleri (AI'a girdi toplar) ---
class _MultiSelect(discord.ui.Select):
    def __init__(self, placeholder, options, target_attr, max_values):
        super().__init__(placeholder=placeholder, options=options,
                         min_values=0, max_values=max_values)
        self.target_attr = target_attr

    async def callback(self, interaction: discord.Interaction):
        setattr(self.view, self.target_attr, self.values)
        await interaction.response.defer()


class ProfileView(discord.ui.View):
    def __init__(self, transition_mode: bool = False):
        super().__init__(timeout=300)
        self.sel_tags: list[str] = []
        self.sel_style: list[str] = []
        self.sel_values: list[str] = []
        self.transition_mode = transition_mode
        self.add_item(_MultiSelect("1️⃣ İlgi alanların (birden fazla seçebilirsin)",
                                   _opts(INTEREST_OPTIONS), "sel_tags", 5))
        self.add_item(_MultiSelect("2️⃣ Nasıl çalışmayı seversin?",
                                   _opts(STYLE_OPTIONS), "sel_style", 3))
        self.add_item(_MultiSelect("3️⃣ Senin için ne önemli?",
                                   _opts(VALUE_OPTIONS), "sel_values", 3))

    @discord.ui.button(label="AI Önerisi Al", style=discord.ButtonStyle.success,
                       emoji="✨", row=3)
    async def generate(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (self.sel_tags or self.sel_style or self.sel_values):
            await interaction.response.send_message(
                "En az bir seçim yapmalısın 🙂", ephemeral=True)
            return
        profile_text = profile_to_text(self.sel_tags, self.sel_style,
                                       self.sel_values, self.transition_mode)
        await interaction.response.defer()
        try:
            data = await ai_recommend(profile_text)
        except Exception as e:
            await interaction.edit_original_response(
                content=f"⚠️ AI'a ulaşılamadı: `{e}`\nGEMINI_API_KEY'i kontrol et.",
                view=None)
            return
        await interaction.edit_original_response(
            content=None,
            embed=recommend_embed(data, self.transition_mode),
            view=ResultView(self.transition_mode))


# --- UI: Sonuç ekranı — soru sor / yeniden ---
class QuestionModal(discord.ui.Modal, title="Kariyer Danışmanına Sor"):
    soru = discord.ui.TextInput(
        label="Sorun ne?", style=discord.TextStyle.paragraph,
        placeholder="Örn: Grafik tasarımcı olmak için hangi becerilere ihtiyacım var?",
        max_length=400)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            answer = await ai_chat(str(self.soru))
        except Exception as e:
            answer = f"⚠️ AI'a ulaşılamadı: {e}"
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
            content="Profilini yeniden oluşturalım 👇",
            embed=None, view=ProfileView(self.transition_mode))


# --- Bot ---
intents = discord.Intents.default()
intents.message_content = True  # serbest metin için (dev portalında da açılmalı)
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Giriş: {bot.user} | {len(synced)} komut senkronize edildi")
    except Exception as e:
        print(f"⚠️ Komut senkronizasyonu başarısız: {e}")


@bot.tree.command(name="kesfet", description="AI ile kişiselleştirilmiş kariyer önerileri al")
async def kesfet(interaction: discord.Interaction):
    await interaction.response.send_message(
        content="**Kariyer keşfine hoş geldin!** ✨\nMenülerden seç, sonra "
                "**AI Önerisi Al**'a bas — sana özel öneriler üreteyim.",
        view=ProfileView(), ephemeral=True)


@bot.tree.command(name="gecis", description="İşinden sıkıldıysan: AI destekli kariyer geçişi")
async def gecis(interaction: discord.Interaction):
    await interaction.response.send_message(
        content="**Yeni bir başlangıç?** 🔄\nİlgi alanlarını seç; sana uyabilecek "
                "yeni kariyer yollarını birlikte bulalım.",
        view=ProfileView(transition_mode=True), ephemeral=True)


@bot.tree.command(name="sor", description="Kariyer danışmanına serbest bir soru sor")
@app_commands.describe(soru="Merak ettiğin her şey")
async def sor(interaction: discord.Interaction, soru: str):
    await interaction.response.defer()
    try:
        answer = await ai_chat(soru)
    except Exception as e:
        answer = f"⚠️ AI'a ulaşılamadı: {e}"
    await interaction.followup.send(embed=chat_embed(soru, answer))


async def career_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    return [
        app_commands.Choice(name=c["name"], value=c["id"])
        for c in CAREERS if cur in c["name"].lower() or cur in c["id"].lower()
    ][:25]


@bot.tree.command(name="meslek", description="Bir mesleğin detaylarını veritabanından gör")
@app_commands.describe(meslek="Meslek adı")
@app_commands.autocomplete(meslek=career_autocomplete)
async def meslek(interaction: discord.Interaction, meslek: str):
    c = find_career_by_name(meslek, CAREERS)
    if c is None:
        await interaction.response.send_message(
            "Bu mesleği bulamadım. `/meslek` yazıp listeden seç.", ephemeral=True)
        return
    await interaction.response.send_message(embed=career_embed(c))


@bot.tree.command(name="karsilastir", description="İki mesleği yan yana karşılaştır")
@app_commands.describe(meslek1="Birinci meslek", meslek2="İkinci meslek")
@app_commands.autocomplete(meslek1=career_autocomplete, meslek2=career_autocomplete)
async def karsilastir(interaction: discord.Interaction, meslek1: str, meslek2: str):
    a = find_career_by_name(meslek1, CAREERS)
    b = find_career_by_name(meslek2, CAREERS)
    if not a or not b:
        await interaction.response.send_message("Meslekler bulunamadı.", ephemeral=True)
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
    await interaction.response.send_message(embed=emb)


@bot.tree.command(name="surpriz", description="Rastgele ilginç bir meslek keşfet")
async def surpriz(interaction: discord.Interaction):
    pick = surprise_pick(UserProfile(), CAREERS)
    emb = career_embed(pick.career)
    emb.title = "🎲 " + emb.title
    await interaction.response.send_message(embed=emb)


@bot.tree.command(name="yardim", description="Botun ne yapabileceğini gör")
async def yardim(interaction: discord.Interaction):
    emb = discord.Embed(title="🤖 Kariyer Danışmanı — Yardım", color=BRAND_COLOR,
                        description="AI ile yeni ve ilginç kariyer yolları öneriyorum!")
    emb.add_field(name="/kesfet", value="Menülerle profil oluştur → AI kişisel öneri üretsin", inline=False)
    emb.add_field(name="/gecis", value="İşinden sıkıldıysan kariyer geçişi önerileri", inline=False)
    emb.add_field(name="/sor", value="Serbest bir kariyer sorusu sor", inline=False)
    emb.add_field(name="/meslek", value="Bir mesleğin detaylarını gör", inline=False)
    emb.add_field(name="/karsilastir", value="İki mesleği karşılaştır", inline=False)
    emb.add_field(name="/surpriz", value="Rastgele ilginç bir meslek", inline=False)
    emb.add_field(name="💬 Serbest metin",
                  value="Beni etiketle ve yaz — örn. *\"@bot sanatı seviyorum ama teknolojiye de ilgim var\"*",
                  inline=False)
    await interaction.response.send_message(embed=emb, ephemeral=True)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if bot.user in message.mentions or isinstance(message.channel, discord.DMChannel):
        text = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if not text:
            await message.reply("Merhaba! İlgi alanlarını yaz ya da `/kesfet` dene 🎯")
        else:
            async with message.channel.typing():
                try:
                    answer = await ai_chat(text)
                except Exception as e:
                    answer = f"⚠️ AI'a ulaşılamadı: {e}"
            await message.reply(answer[:2000])
    await bot.process_commands(message)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN bulunamadı. .env dosyasını kontrol et.")
    bot.run(TOKEN)