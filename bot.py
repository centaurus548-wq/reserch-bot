import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import json
from datetime import datetime, timedelta
import pytz

# --- CONFIG ---
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
REPORT_HOUR = int(os.getenv("REPORT_HOUR_WIB", "8"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
last_alerted = set()

wib = pytz.timezone("Asia/Jakarta")
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
         "Juli", "Agustus", "September", "Oktober", "November", "Desember"]

# --- HELPERS ---
async def fetch(url, retries=3):
    for i in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15), headers=HEADERS) as r:
                    if r.status == 200:
                        return await r.json()
        except Exception as e:
            print(f"[retry {i+1}] {url}: {e}")
            await asyncio.sleep(2)
    return None

def format_wib_time(date_str):
    """Convert UTC date string to neat WIB format: 'Rabu, 30 April | 15:30 WIB'"""
    try:
        if not date_str:
            return "N/A"
        # Parse ISO format from FF: "2026-04-30T13:30:00-04:00" or "2026-04-30T13:30:00Z"
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        dt_wib = dt.astimezone(wib)
        today_wib = datetime.now(wib).date()
        event_date = dt_wib.date()

        day_name = HARI[dt_wib.weekday()]
        month_name = BULAN[dt_wib.month - 1]
        time_str = dt_wib.strftime("%H:%M")

        if event_date == today_wib:
            return f"Hari Ini | {time_str} WIB"
        elif event_date == today_wib + timedelta(days=1):
            return f"Besok ({day_name}) | {time_str} WIB"
        else:
            return f"{day_name}, {dt_wib.day} {month_name} | {time_str} WIB"
    except:
        return "N/A"

def is_today(date_str):
    try:
        if not date_str:
            return False
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.astimezone(wib).date() == datetime.now(wib).date()
    except:
        return False

# --- DATA FETCHERS ---
async def get_btc_data():
    data = await fetch("https://min-api.cryptocompare.com/data/pricemultifull?fsyms=BTC&tsyms=USDT")
    if data and "RAW" in data and "BTC" in data["RAW"] and "USDT" in data["RAW"]["BTC"]:
        d = data["RAW"]["BTC"]["USDT"]
        return {
            "price": d.get("PRICE", 0),
            "change_24h": d.get("CHANGEPCT24HOUR", 0),
            "high": d.get("HIGH24HOUR", 0),
            "low": d.get("LOW24HOUR", 0),
            "volume": d.get("TOTALVOLUME24HTO", 0)
        }
    data = await fetch("https://api.coincap.io/v2/assets/bitcoin")
    if data and "data" in data:
        d = data["data"]
        return {
            "price": float(d.get("priceUsd", 0)),
            "change_24h": float(d.get("changePercent24Hr", 0)),
            "high": 0, "low": 0,
            "volume": float(d.get("volumeUsd24Hr", 0))
        }
    return {"price": 0, "change_24h": 0, "high": 0, "low": 0, "volume": 0}

async def get_global_data():
    data = await fetch("https://api.coingecko.com/api/v3/global")
    if data and "data" in data:
        d = data["data"]
        btc_dom = d.get("market_cap_percentage", {}).get("btc", 0)
        eth_dom = d.get("market_cap_percentage", {}).get("eth", 0)
        if 30 <= btc_dom <= 80:
            return {
                "market_cap": d.get("total_market_cap", {}).get("usd", 0),
                "volume": d.get("total_volume", {}).get("usd", 0),
                "btc_dom": btc_dom,
                "eth_dom": eth_dom,
                "change_24h": d.get("market_cap_change_percentage_24h_usd", 0)
            }
    data = await fetch("https://api.coincap.io/v2/global")
    if data and "data" in data:
        d = data["data"]
        return {
            "market_cap": float(d.get("marketCap", 0)),
            "volume": float(d.get("volume", 0)),
            "btc_dom": float(d.get("btcDominance", 0)),
            "eth_dom": 0,
            "change_24h": 0
        }
    return {"market_cap": 0, "volume": 0, "btc_dom": 0, "eth_dom": 0, "change_24h": 0}

async def get_dxy_data():
    data = await fetch("https://api.exchangerate-api.com/v4/latest/USD")
    if data and "rates" in data:
        rates = data["rates"]
        eur = rates.get("EUR", 1)
        gbp = rates.get("GBP", 1)
        jpy = rates.get("JPY", 1)
        cad = rates.get("CAD", 1)
        sek = rates.get("SEK", 1)
        chf = rates.get("CHF", 1)
        dxy = 50.14348112 * (eur ** -0.576) * (jpy ** 0.136) * (gbp ** -0.119) * (cad ** 0.091) * (sek ** 0.042) * (chf ** 0.036)
        if 90 <= dxy <= 120:
            return round(dxy, 2)
    return None

async def get_fear_greed():
    data = await fetch("https://api.alternative.me/fng/?limit=1")
    if data and "data" in data and len(data["data"]) > 0:
        return {"value": int(data["data"][0]["value"]), "label": data["data"][0]["value_classification"]}
    return {"value": 0, "label": "N/A"}

async def get_news():
    try:
        data = await fetch("https://cryptopanic.com/api/free/v1/posts/?auth_token=demo&filter=rising&currencies=BTC,ETH&public=true")
        if data and "results" in data and len(data["results"]) > 0:
            news = []
            for item in data["results"][:5]:
                title = item.get("title", "")
                if title:
                    news.append(title)
            if len(news) >= 3:
                return news
    except:
        pass
    try:
        data = await fetch("https://min-api.cryptocompare.com/data/v2/news/?lang=EN&categories=BTC,ETH&excludeCategories=Sponsored")
        if data and "Data" in data and len(data["Data"]) > 0:
            news = []
            for item in data["Data"][:5]:
                title = item.get("title", "")
                if title:
                    news.append(title)
            if len(news) >= 3:
                return news
    except:
        pass
    ff = await get_ff_events()
    if ff:
        news = [f"[USD] {e['title']} (Forecast: {e.get('forecast','N/A')})" for e in ff[:5]]
        return news if news else ["Tidak ada berita tersedia saat ini."]
    return ["Tidak ada berita tersedia saat ini."]

async def get_ff_events():
    data = await fetch("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    if not data or not isinstance(data, list):
        return []
    events = []
    for e in data:
        if e.get("country") != "USD":
            continue
        impact = e.get("impact", "").lower()
        if impact not in ("high", "medium"):
            continue
        date_raw = e.get("date", "")
        events.append({
            "title": e.get("title", "Unknown"),
            "date_raw": date_raw,
            "time_wib": format_wib_time(date_raw),
            "date_short": date_raw[:10] if date_raw else "N/A",
            "is_today": is_today(date_raw),
            "forecast": str(e.get("forecast", "N/A")),
            "previous": str(e.get("previous", "N/A")),
            "impact": impact.upper(),
            "actual": str(e.get("actual", "")),
            "is_released": bool(e.get("actual"))
        })
    return events

# --- AI ANALYSIS ---
async def get_ai_analysis(btc, global_data, dxy, fear_greed, news):
    trend = "Bullish" if btc["change_24h"] > 0 else "Bearish"
    fg = f"{fear_greed['value']} - {fear_greed['label']}"

    prompt = f"""Kamu adalah analis crypto profesional. Berikan analisis mendetail dalam Bahasa Indonesia berdasarkan data real-time berikut:

DATA PASAR:
- BTC Price: ${btc['price']:,.2f} ({btc['change_24h']:+.2f}% 24h)
- 24h Range: ${btc['high']:,.2f} / ${btc['low']:,.2f}
- 24h Volume: ${btc['volume']:,.0f}
- DXY Index: {dxy if dxy else 'N/A'}
- Market Cap: ${global_data['market_cap']:,.0f}
- Volume Global: ${global_data['volume']:,.0f}
- BTC Dominance: {global_data['btc_dom']:.1f}%
- ETH Dominance: {global_data['eth_dom']:.1f}%
- Fear & Greed Index: {fg}
- Market 24h: {global_data['change_24h']:+.2f}%
- Trend Saat Ini: {trend}

BERITA TERKINI:
{chr(10).join('- ' + n for n in news[:5])}

BUAT ANALISIS DENGAN FORMAT BERIKUT (WAJIB lengkap dan detail, setiap bagian minimal 3-4 kalimat):

RINGKASAN PASAR
Jelaskan kondisi pasar secara keseluruhan, bagaimana performa BTC, korelasi dengan DXY, volume perdagangan, dan sentimen pasar saat ini.

PSIKOLOGI PASAR & SENTIMEN
Analisis Fear & Greed Index, apa artinya untuk perilaku trader, apakah ada tanda panic selling atau FOMO. Hubungkan dengan berita yang ada dan dampaknya ke psikologi pasar.

PREDIKSI ARAH MARKET
Berdasarkan semua data di atas, berikan prediksi arah market jangka pendek (24-48 jam). Sertakan level-level kunci yang perlu diperhatikan (support/resistance area), dan skenario bullish vs bearish.

PENTING:
- Gunakan bahasa Indonesia yang natural dan profesional
- Gunakan sedikit emoji, cukup 1-2 per bagian (misalnya 📊 🧠 🎯)
- Setiap bagian WAJIB minimal 3-4 kalimat penjelasan yang substantif
- Berikan analisis yang actionable dan berbasis data
- Jangan gunakan format markdown bold ** , cukup tulis judul bagian dengan UPPERCASE"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 3000, "temperature": 0.7},
                timeout=aiohttp.ClientTimeout(total=45)
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    content = result["choices"][0]["message"]["content"].strip()
                    print(f"[AI] Analysis: {len(content)} chars")
                    return content
                else:
                    print(f"[AI] Error {resp.status}")
    except Exception as e:
        print(f"[AI] Exception: {e}")

    # Fallback
    trend_dir = "naik" if btc["change_24h"] > 0 else "turun"
    return (
        f"📊 RINGKASAN PASAR\n"
        f"BTC saat ini berada di ${btc['price']:,.2f} dengan perubahan {btc['change_24h']:+.2f}% dalam 24 jam terakhir. "
        f"Market secara keseluruhan menunjukkan tren {trend_dir} dengan Market 24h di {global_data['change_24h']:+.2f}%. "
        f"BTC Dominance berada di {global_data['btc_dom']:.1f}% menandakan {'penguatan' if global_data['btc_dom'] > 55 else 'distribusi'} Bitcoin terhadap altcoin. "
        f"DXY Index di level {dxy if dxy else 'N/A'} menunjukkan {'penguatan' if dxy and dxy > 100 else 'pelemahan'} dolar AS yang {'memberi tekanan' if dxy and dxy > 100 else 'memberi ruang'} bagi aset crypto.\n\n"
        f"🧠 PSIKOLOGI PASAR & SENTIMEN\n"
        f"Fear & Greed Index berada di {fear_greed['value']} ({fear_greed['label']}), menandakan sentimen pasar yang {'sangat takut' if fear_greed['value'] < 25 else 'takut' if fear_greed['value'] < 40 else 'netral' if fear_greed['value'] < 60 else 'serakah'}. "
        f"Kondisi ini biasanya {'membuka peluang akumulasi bagi investor jangka panjang' if fear_greed['value'] < 30 else 'menunjukkan kehati-hatian pasar' if fear_greed['value'] < 50 else 'menandakan optimisme namun perlu waspada koreksi'}. "
        f"Volume perdagangan ${btc['volume']:,.0f} menunjukkan tingkat partisipasi trader yang {'aktif' if btc['volume'] > 15000000000 else 'relatif rendah'}. "
        f"Berita terkini terkait data ekonomi AS perlu diperhatikan karena dapat mempengaruhi sentimen secara drastis.\n\n"
        f"🎯 PREDIKSI ARAH MARKET\n"
        f"Dalam jangka pendek 24-48 jam, BTC berpotensi {'menguat' if btc['change_24h'] > 0 else 'terkoreksi'} dengan level support di sekitar ${btc['low']:,.0f} dan resistance di ${btc['high']:,.0f}. "
        f"{'DXY yang menguat dapat memberi tekanan tambahan pada crypto' if dxy and dxy > 100 else 'Pelemahan DXY dapat menjadi katalis positif bagi pergerakan crypto'}. "
        f"Fear & Greed di level {fear_greed['value']} menandakan {'potensi reversal jika ada katalis positif' if fear_greed['value'] < 30 else 'tren yang mungkin berlanjut sesuai momentum saat ini'}. "
        f"Perhatikan data ekonomi AS yang akan rilis karena berpotensi menyebabkan volatilitas tinggi."
    )

async def get_macro_analysis(events):
    if not events:
        return "Tidak ada event ekonomi USD berdampak tinggi/sedang minggu ini."

    event_list = "\n".join(
        f"- [{e['impact']}] {e['title']} | Waktu: {e['time_wib']} | Forecast: {e['forecast']} | Previous: {e['previous']} | Actual: {e['actual'] if e['actual'] else 'belum rilis'}"
        for e in events
    )

    prompt = f"""Kamu adalah analis ekonomi makro profesional. Analisis event ekonomi USD berikut dalam Bahasa Indonesia:

{event_list}

BUAT ANALISIS DENGAN FORMAT BERIKUT (setiap event WAJIB dijelaskan lengkap):

Untuk SETIAP event, buat analisis dengan format:
EVENT: [nama event]
RESEARCH: Apa itu event ini dan mengapa penting untuk pasar? (2-3 kalimat)
PROYEKSI: Apa yang diharapkan berdasarkan forecast vs previous? Apakah ada tren menarik? (2-3 kalimat)
WILDCARD: Skenario surprise apa yang mungkin terjadi? Bagaimana jika actual jauh dari forecast? (2 kalimat)
TERDAMPAK: Asset crypto apa yang paling terdampak (BTC, ETH, altcoin tertentu) dan bagaimana arahnya? (2-3 kalimat)

PENTING:
- Bahasa Indonesia profesional
- Gunakan sedikit emoji per event (misalnya 📌 🔍 ⚡ 💥)
- SETIAP event WAJIB punya ke-4 bagian di atas dengan penjelasan minimal 2 kalimat
- Jangan gunakan format markdown bold ** """

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 4000, "temperature": 0.7},
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    content = result["choices"][0]["message"]["content"].strip()
                    print(f"[AI] Macro: {len(content)} chars")
                    return content
                else:
                    print(f"[AI] Macro Error {resp.status}")
    except Exception as e:
        print(f"[AI] Macro Exception: {e}")

    return "Gagal menghasilkan analisis makro. Silakan coba lagi nanti."

async def get_realtime_alert(event):
    prompt = f"""Kamu adalah analis ekonomi dan crypto. Data ekonomi baru saja RILIS secara realtime:

Event: {event['title']}
Country: USD
Forecast: {event['forecast']}
Previous: {event['previous']}
Actual: {event['actual']}
Waktu Rilis: {event['time_wib']}

Berikan analisis MENINGKALKAN dalam Bahasa Indonesia (maks 500 kata) dengan format:

VERDICT: BEAT / MISS / IN-LINE (jelaskan kenapa)

PENJELASAN DAMPAK:
- Apa artinya data ini untuk perekonomian AS?
- Bagaimana reaksi USD (DXY) yang kemungkinan terjadi?
- Apa implikasinya untuk kebijakan Fed (rate hike/cut)?
- Bagaimana dampak langsung ke BTC, ETH, dan crypto market?
- Seperti apa volatilitas yang bisa terjadi?

SARAN TRADING:
- Apa yang sebaiknya dilakukan trader crypto dalam 1-6 jam ke depan?
- Level BTC yang perlu diwaspadai?
- Risiko apa yang harus diperhatikan?

PENTING:
- Gunakan sedikit emoji (⚡ 🔴 📈 📉)
- Bahasa Indonesia profesional
- Analisis harus substantif dan actionable
- Jangan gunakan format markdown bold ** """

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 800, "temperature": 0.7},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[AI] Realtime Exception: {e}")

    return f"Data {event['title']} baru rilis. Actual: {event['actual']} vs Forecast: {event['forecast']} vs Previous: {event['previous']}."

# --- EMBED BUILDERS ---
def build_embed(btc, global_data, dxy, fear_greed, news, ai_text):
    embed = discord.Embed(
        title=f"Laporan Pasar Crypto Harian - {datetime.now(wib).strftime('%d %B %Y')}",
        color=discord.Color.orange()
    )

    trend_emoji = "📈" if btc["change_24h"] >= 0 else "📉"
    embed.add_field(
        name=f"{trend_emoji} Data Pasar",
        value=(
            f"BTC/USDT: ${btc['price']:,.2f} ({btc['change_24h']:+.2f}%)\n"
            f"High/Low: ${btc['high']:,.2f} / ${btc['low']:,.2f}\n"
            f"Volume 24h: ${btc['volume']:,.0f}\n"
            f"DXY Index: {dxy if dxy else 'N/A'}"
        ),
        inline=False
    )

    fg = f"{fear_greed['value']} - {fear_greed['label']}"
    trend = "Bullish" if global_data["change_24h"] > 0 else "Bearish"
    trend_icon = "🟢" if global_data["change_24h"] > 0 else "🔴"
    embed.add_field(
        name=f"{trend_icon} Market Global",
        value=(
            f"Market Cap: ${global_data['market_cap']:,.0f}\n"
            f"Volume Global: ${global_data['volume']:,.0f}\n"
            f"BTC Dom: {global_data['btc_dom']:.1f}% | ETH Dom: {global_data['eth_dom']:.1f}%\n"
            f"Fear & Greed: {fg}\n"
            f"Market 24h: {global_data['change_24h']:+.2f}% | Trend: {trend}"
        ),
        inline=False
    )

    news_text = "\n".join(f"• {n}" for n in news[:5])
    embed.add_field(name="📰 Berita Terkini", value=news_text, inline=False)

    if ai_text and len(ai_text) > 50:
        embed.add_field(name="🤖 Analisis AI", value=ai_text[:4000], inline=False)

    embed.set_footer(text="Not Financial Advice | DYOR | Groq AI | CryptoCompare | CoinGecko | Forex Factory")
    return embed

def build_macro_embed(events, ai_text):
    now_wib = datetime.now(wib)
    embed = discord.Embed(
        title=f"📅 Kalender Ekonomi USD - {now_wib.strftime('%d %B %Y')}",
        description="Event makroekonomi USD berdampak tinggi & sedang",
        color=discord.Color.orange()
    )

    if not events:
        embed.description = "Tidak ada event ekonomi USD berdampak tinggi/sedang minggu ini."
        return embed

    for e in events[:10]:
        impact_icon = "🔴" if e["impact"] == "HIGH" else "🟡"
        today_marker = " ⬅️ HARI INI" if e["is_today"] else ""
        released_marker = " ✅ RILIS" if e["is_released"] else ""

        if e["is_released"]:
            actual_line = f"Actual: {e['actual']}"
        else:
            actual_line = "Status: Belum Rilis"

        embed.add_field(
            name=f"{impact_icon} {e['title']}{today_marker}{released_marker}",
            value=(
                f"⏰ {e['time_wib']}\n"
                f"Forecast: {e['forecast']} | Previous: {e['previous']}\n"
                f"{actual_line}"
            ),
            inline=False
        )

    if ai_text and len(ai_text) > 50:
        embed.add_field(name="🔍 Analisis Dampak", value=ai_text[:4000], inline=False)
    else:
        embed.add_field(
            name="🔍 Analisis Dampak",
            value="Analisis sedang di-generate oleh AI. Coba beberapa saat lagi.",
            inline=False
        )

    embed.set_footer(text="Sumber: Forex Factory | Groq AI")
    return embed

def build_realtime_embed(event, ai_text):
    # Determine verdict
    verdict = "IN-LINE"
    verdict_icon = "⚪"
    try:
        forecast = float(event["forecast"]) if event["forecast"] not in ("N/A", "", None, "None") else None
        actual = float(event["actual"]) if event["actual"] not in ("N/A", "", None, "None") else None
        if forecast is not None and actual is not None:
            diff = abs(actual - forecast)
            threshold = abs(forecast) * 0.05 if forecast != 0 else 0.5
            if diff > threshold:
                if actual > forecast:
                    verdict = "BEAT"
                    verdict_icon = "🟢"
                else:
                    verdict = "MISS"
                    verdict_icon = "🔴"
    except:
        pass

    embed = discord.Embed(
        title=f"⚡ DATA RILIS - {event['title']}",
        color=discord.Color.orange()
    )
    embed.add_field(
        name=f"Detail Data {verdict_icon} {verdict}",
        value=(
            f"⏰ {event['time_wib']}\n"
            f"Forecast: {event['forecast']}\n"
            f"Previous: {event['previous']}\n"
            f"Actual: {event['actual']}"
        ),
        inline=False
    )
    if ai_text and len(ai_text) > 20:
        embed.add_field(name="📝 Penjelasan Dampak & Saran", value=ai_text[:3500], inline=False)
    embed.set_footer(text="Realtime Alert | Forex Factory | Groq AI")
    return embed

# --- COMMANDS ---
@bot.command()
async def report(ctx):
    if CHANNEL_ID and ctx.channel.id != CHANNEL_ID:
        return
    msg = await ctx.send("Mengumpulkan data dan menganalisis pasar...")
    try:
        btc, global_data, dxy, fear_greed, news = await asyncio.gather(
            get_btc_data(), get_global_data(), get_dxy_data(),
            get_fear_greed(), get_news()
        )
        ai_text = await get_ai_analysis(btc, global_data, dxy, fear_greed, news)
        embed = build_embed(btc, global_data, dxy, fear_greed, news, ai_text)
        await msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await msg.edit(content=f"Error: {e}")

@bot.command()
async def macro(ctx):
    if CHANNEL_ID and ctx.channel.id != CHANNEL_ID:
        return
    msg = await ctx.send("Mengambil data kalender ekonomi...")
    try:
        events = await get_ff_events()
        ai_text = await get_macro_analysis(events)
        embed = build_macro_embed(events, ai_text)
        await msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await msg.edit(content=f"Error: {e}")

# --- LOOPS ---
async def auto_post():
    while True:
        now = datetime.now(wib)
        target = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            print("[AUTO] Channel not found")
            continue

        try:
            btc, global_data, dxy, fear_greed, news = await asyncio.gather(
                get_btc_data(), get_global_data(), get_dxy_data(),
                get_fear_greed(), get_news()
            )
            ai_text = await get_ai_analysis(btc, global_data, dxy, fear_greed, news)
            embed1 = build_embed(btc, global_data, dxy, fear_greed, news, ai_text)
            await channel.send(embed=embed1)
            print("[AUTO] Report posted")
            await asyncio.sleep(3)

            events = await get_ff_events()
            macro_ai = await get_macro_analysis(events)
            embed2 = build_macro_embed(events, macro_ai)
            await channel.send(embed=embed2)
            print("[AUTO] Macro posted")
        except Exception as e:
            print(f"[AUTO] Error: {e}")

async def realtime_monitor():
    await bot.wait_until_ready()
    while True:
        try:
            events = await get_ff_events()
            for e in events:
                if e["is_released"] and e["title"] not in last_alerted:
                    last_alerted.add(e["title"])
                    channel = bot.get_channel(CHANNEL_ID)
                    if channel:
                        ai_text = await get_realtime_alert(e)
                        embed = build_realtime_embed(e, ai_text)
                        await channel.send(embed=embed)
                        print(f"[REALTIME] Posted: {e['title']}")
        except Exception as ex:
            print(f"[REALTIME] Error: {ex}")
        await asyncio.sleep(120)

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user}")
    bot.loop.create_task(auto_post())
    bot.loop.create_task(realtime_monitor())

bot.run(DISCORD_TOKEN)
