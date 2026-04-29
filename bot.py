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
async def fetch_json(url, retries=3):
    for i in range(retries):
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=HEADERS) as r:
                    if r.status == 200:
                        return await r.json()
        except Exception as e:
            print(f"[retry {i+1}] {url}: {e}")
            await asyncio.sleep(2)
    return None

def format_wib_time(date_str):
    try:
        if not date_str: return "N/A"
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
        if not date_str: return False
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.astimezone(wib).date() == datetime.now(wib).date()
    except:
        return False

def split_text(text, max_len=1024):
    if not text or len(text) <= max_len:
        return [text] if text else [""]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text[:max_len].rfind("\n")
        if split_at < max_len // 2:
            split_at = text[:max_len].rfind(". ")
        if split_at < max_len // 2:
            split_at = max_len
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return chunks

# --- DATA FETCHERS ---
async def get_btc_data():
    data = await fetch_json("https://min-api.cryptocompare.com/data/pricemultifull?fsyms=BTC&tsyms=USDT")
    if data and "RAW" in data and "BTC" in data["RAW"] and "USDT" in data["RAW"]["BTC"]:
        d = data["RAW"]["BTC"]["USDT"]
        return {"price": d.get("PRICE", 0), "change_24h": d.get("CHANGEPCT24HOUR", 0),
                "high": d.get("HIGH24HOUR", 0), "low": d.get("LOW24HOUR", 0), "volume": d.get("TOTALVOLUME24HTO", 0)}
    data = await fetch_json("https://api.coincap.io/v2/assets/bitcoin")
    if data and "data" in data:
        d = data["data"]
        return {"price": float(d.get("priceUsd", 0)), "change_24h": float(d.get("changePercent24Hr", 0)),
                "high": 0, "low": 0, "volume": float(d.get("volumeUsd24Hr", 0))}
    return {"price": 0, "change_24h": 0, "high": 0, "low": 0, "volume": 0}

async def get_global_data():
    data = await fetch_json("https://api.coingecko.com/api/v3/global")
    if data and "data" in data:
        d = data["data"]
        btc_dom = d.get("market_cap_percentage", {}).get("btc", 0)
        eth_dom = d.get("market_cap_percentage", {}).get("eth", 0)
        if 30 <= btc_dom <= 80:
            return {"market_cap": d.get("total_market_cap", {}).get("usd", 0),
                    "volume": d.get("total_volume", {}).get("usd", 0),
                    "btc_dom": btc_dom, "eth_dom": eth_dom,
                    "change_24h": d.get("market_cap_change_percentage_24h_usd", 0)}
    data = await fetch_json("https://api.coincap.io/v2/global")
    if data and "data" in data:
        d = data["data"]
        return {"market_cap": float(d.get("marketCap", 0)), "volume": float(d.get("volume", 0)),
                "btc_dom": float(d.get("btcDominance", 0)), "eth_dom": 0, "change_24h": 0}
    return {"market_cap": 0, "volume": 0, "btc_dom": 0, "eth_dom": 0, "change_24h": 0}

async def get_dxy_data():
    data = await fetch_json("https://api.exchangerate-api.com/v4/latest/USD")
    if data and "rates" in data:
        r = data["rates"]
        dxy = 50.14348112 * (r.get("EUR",1)**-0.576) * (r.get("JPY",1)**0.136) * (r.get("GBP",1)**-0.119) * (r.get("CAD",1)**0.091) * (r.get("SEK",1)**0.042) * (r.get("CHF",1)**0.036)
        if 90 <= dxy <= 120:
            return round(dxy, 2)
    return None

async def get_fear_greed():
    data = await fetch_json("https://api.alternative.me/fng/?limit=1")
    if data and "data" in data and len(data["data"]) > 0:
        return {"value": int(data["data"][0]["value"]), "label": data["data"][0]["value_classification"]}
    return {"value": 0, "label": "N/A"}

async def get_news():
    """Returns list of dicts: {"title": str, "url": str}"""
    news = []

    # 1. CryptoPanic (has real crypto news with URLs)
    try:
        data = await fetch_json("https://cryptopanic.com/api/free/v1/posts/?auth_token=demo&filter=rising&currencies=BTC,ETH&public=true")
        if data and "results" in data:
            for item in data["results"][:7]:
                title = item.get("title", "").strip()
                url = item.get("url", "")
                if title and url:
                    news.append({"title": title, "url": url})
            if len(news) >= 3:
                return news
    except:
        pass

    # 2. CryptoCompare (has real crypto news with URLs)
    try:
        data = await fetch_json("https://min-api.cryptocompare.com/data/v2/news/?lang=EN&categories=BTC,ETH&excludeCategories=Sponsored")
        if data and "Data" in data:
            for item in data["Data"][:7]:
                title = item.get("title", "").strip()
                url = item.get("url", "") or item.get("guid", "")
                if title and url:
                    news.append({"title": title, "url": url})
            if len(news) >= 3:
                return news
    except:
        pass

    # 3. FF events fallback (no real URLs, use Forex Factory page)
    ff = await get_ff_events()
    if ff:
        for e in ff[:5]:
            news.append({
                "title": f"[USD] {e['title']} (Forecast: {e.get('forecast','N/A')})",
                "url": "https://www.forexfactory.com/calendar"
            })
        return news if news else [{"title": "Tidak ada berita tersedia", "url": ""}]

    return [{"title": "Tidak ada berita tersedia", "url": ""}]

async def get_ff_events():
    data = await fetch_json("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    if not data or not isinstance(data, list):
        return []
    events = []
    for e in data:
        if e.get("country") != "USD": continue
        impact = e.get("impact", "").lower()
        if impact not in ("high", "medium"): continue
        date_raw = e.get("date", "")
        events.append({
            "title": e.get("title", "Unknown"), "date_raw": date_raw,
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
async def call_groq(prompt, max_tokens=3000, timeout_sec=45):
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0.7}
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result["choices"][0]["message"]["content"].strip()
                print(f"[GROQ] Error {resp.status}")
    except Exception as e:
        print(f"[GROQ] Exception: {e}")
    return None

async def get_ai_analysis(btc, global_data, dxy, fear_greed, news):
    trend = "Bullish" if btc["change_24h"] > 0 else "Bearish"
    fg = f"{fear_greed['value']} - {fear_greed['label']}"
    news_titles = "\n".join(f"- {n['title']}" for n in news[:5])

    prompt = f"""Kamu analis crypto profesional. Berikan analisis dalam Bahasa Indonesia. JAWAB 3 BAGIAN INI SECARA TERPISAH, setiap bagian maks 800 karakter:

DATA:
- BTC: ${btc['price']:,.2f} ({btc['change_24h']:+.2f}%)
- Range: ${btc['high']:,.2f} / ${btc['low']:,.2f}
- Volume: ${btc['volume']:,.0f}
- DXY: {dxy if dxy else 'N/A'}
- Market Cap: ${global_data['market_cap']:,.0f}
- Volume Global: ${global_data['volume']:,.0f}
- BTC Dom: {global_data['btc_dom']:.1f}% | ETH Dom: {global_data['eth_dom']:.1f}%
- Fear & Greed: {fg}
- Market 24h: {global_data['change_24h']:+.2f}% | Trend: {trend}

BERITA:
{news_titles}

FORMAT JAWABAN (wajib ikuti):

[1] RINGKASAN PASAR
(2-3 kalimat. Kondisi BTC, DXY, volume, sentimen keseluruhan)

[2] PSIKOLOGI PASAR
(2-3 kalimat. Fear & Greed, panic selling/FOMO, perilaku trader)

[3] PREDIKSI ARAH MARKET
(2-3 kalimat. Prediksi 24-48 jam, support/resistance, skenario bullish/bearish)

PENTING:
- Bahasa Indonesia profesional
- Emoji cukup 1 per bagian
- WAJIB tulis [1] [2] [3] sebagai separator
- Jangan pakai ** atau ##
- Setiap bagian maks 800 karakter"""

    result = await call_groq(prompt, max_tokens=2000, timeout_sec=45)
    if result:
        parts = {}
        for label, key in [("[1]", "ringkasan"), ("[2]", "psikologi"), ("[3]", "prediksi")]:
            idx = result.find(label)
            if idx != -1:
                next_label = None
                for nl in ["[2]", "[3]"]:
                    ni = result.find(nl, idx + 3)
                    if ni != -1:
                        next_label = ni
                        break
                text = result[idx+3:next_label].strip() if next_label else result[idx+3:].strip()
                text = text.lstrip("0123456789.:- ").strip()
                parts[key] = text[:1024]
        if len(parts) == 3:
            return parts

    trend_dir = "naik" if btc["change_24h"] > 0 else "turun"
    return {
        "ringkasan": f"📊 BTC di ${btc['price']:,.2f} ({btc['change_24h']:+.2f}%), market {trend_dir} {global_data['change_24h']:+.2f}%. BTC Dom {global_data['btc_dom']:.1f}%, DXY {dxy if dxy else 'N/A'}. {'DXY menguat menekan crypto' if dxy and dxy > 100 else 'DXY melemah memberi ruang bagi crypto'}.",
        "psikologi": f"🧠 Fear & Greed {fear_greed['value']} ({fear_greed['label']}), sentimen {'ekstrem takut' if fear_greed['value'] < 25 else 'takut' if fear_greed['value'] < 40 else 'netral' if fear_greed['value'] < 60 else 'serakah'}. {'Peluang akumulasi bagi long-term trader' if fear_greed['value'] < 30 else 'Waspadai potensi koreksi' if fear_greed['value'] > 70 else 'Kondisi sideways, tunggu konfirmasi arah'}.",
        "prediksi": f"🎯 BTC {'potensi menguat' if btc['change_24h"] > 0 else 'potensi koreksi'} dalam 24-48 jam. Support ~${btc['low']:,.0f}, Resistance ~${btc['high']:,.0f}. Perhatikan data ekonomi AS yang bisa picu volatilitas."
    }

async def get_macro_analysis(events):
    if not events: return None
    event_list = "\n".join(
        f"- [{e['impact']}] {e['title']} | {e['time_wib']} | F: {e['forecast']} | P: {e['previous']} | A: {e['actual'] if e['actual'] else 'belum'}"
        for e in events
    )
    prompt = f"""Analisis event ekonomi USD dalam Bahasa Indonesia. Setiap event maks 600 karakter:

{event_list}

Untuk SETIAP event:
EVENT: [nama]
RESEARCH: (1-2 kalimat)
PROYEKSI: (1-2 kalimat)
TERDAMPAK: (1-2 kalimat)

Indonesia profesional, emoji sedikit. Jangan ** atau ##. Separator === antar event."""
    return await call_groq(prompt, max_tokens=4000, timeout_sec=60)

async def get_realtime_alert(event):
    prompt = f"""Data ekonomi RILIS:
{event['title']} | Forecast: {event['forecast']} | Previous: {event['previous']} | Actual: {event['actual']}

Analisis Bahasa Indonesia (maks 400 kata):
VERDICT: BEAT/MISS/IN-LINE
DAMPAK: Arti data, reaksi USD/DXY, implikasi Fed, dampak BTC/ETH
SARAN: Apa dilakukan trader 1-6 jam, level BTC, risiko

Emoji sedikit. Jangan ** atau ##"""
    return await call_groq(prompt, max_tokens=800, timeout_sec=30)

# --- EMBED BUILDERS ---
def build_report_embeds(btc, global_data, dxy, fear_greed, news, ai):
    embed = discord.Embed(
        title=f"Laporan Pasar Crypto - {datetime.now(wib).strftime('%d %B %Y')}",
        color=discord.Color.orange()
    )

    trend_e = "📈" if btc["change_24h"] >= 0 else "📉"
    embed.add_field(name=f"{trend_e} Data Pasar", value=(
        f"BTC/USDT: ${btc['price']:,.2f} ({btc['change_24h']:+.2f}%)\n"
        f"High/Low: ${btc['high']:,.2f} / ${btc['low']:,.2f}\n"
        f"Volume 24h: ${btc['volume']:,.0f}\n"
        f"DXY Index: {dxy if dxy else 'N/A'}"
    ), inline=False)

    fg = f"{fear_greed['value']} - {fear_greed['label']}"
    trend = "Bullish" if global_data["change_24h"] > 0 else "Bearish"
    embed.add_field(name=f"{'🟢' if global_data['change_24h'] > 0 else '🔴'} Market Global", value=(
        f"Market Cap: ${global_data['market_cap']:,.0f}\n"
        f"Volume: ${global_data['volume']:,.0f}\n"
        f"BTC Dom: {global_data['btc_dom']:.1f}% | ETH Dom: {global_data['eth_dom']:.1f}%\n"
        f"Fear & Greed: {fg}\n"
        f"Market 24h: {global_data['change_24h']:+.2f}% | {trend}"
    ), inline=False)

    # Berita dengan LINK
    news_lines = []
    for n in news[:5]:
        title = n["title"]
        url = n.get("url", "")
        if url:
            # Discord embed: [text](url) = clickable link
            news_lines.append(f"• [{title}]({url})")
        else:
            news_lines.append(f"• {title}")
    embed.add_field(name="📰 Berita Terkini", value="\n".join(news_lines), inline=False)

    # AI Analysis - 3 field terpisah
    if isinstance(ai, dict):
        embed.add_field(name="🤖 Ringkasan Pasar", value=ai.get("ringkasan", "N/A")[:1024], inline=False)
        embed.add_field(name="🧠 Psikologi Pasar", value=ai.get("psikologi", "N/A")[:1024], inline=False)
        embed.add_field(name="🎯 Prediksi Market", value=ai.get("prediksi", "N/A")[:1024], inline=False)
    else:
        chunks = split_text(ai, 1024) if ai else ["Gagal generate analisis"]
        labels = ["🤖 Ringkasan Pasar", "🧠 Psikologi Pasar", "🎯 Prediksi Market"]
        for i, chunk in enumerate(chunks[:3]):
            embed.add_field(name=labels[i], value=chunk[:1024], inline=False)

    embed.set_footer(text="Not Financial Advice | DYOR | Groq AI | CryptoCompare | CoinGecko | Forex Factory")
    return embed

def build_macro_embed(events, ai_text):
    embed = discord.Embed(
        title=f"📅 Kalender Ekonomi USD - {datetime.now(wib).strftime('%d %B %Y')}",
        description="Event USD berdampak HIGH & MEDIUM",
        color=discord.Color.orange()
    )
    if not events:
        embed.add_field(name="⚠️ Info", value="API Forex Factory tidak dapat diakses. Coba !macro lagi.", inline=False)
        embed.set_footer(text="Forex Factory | Groq AI")
        return embed

    for e in events[:10]:
        icon = "🔴" if e["impact"] == "HIGH" else "🟡"
        markers = ""
        if e["is_today"]: markers += " ⬅️ HARI INI"
        if e["is_released"]: markers += " ✅"
        actual_line = f"Actual: {e['actual']}" if e["is_released"] else "Belum Rilis"
        embed.add_field(name=f"{icon} {e['title']}{markers}", value=(
            f"⏰ {e['time_wib']}\n"
            f"F: {e['forecast']} | P: {e['previous']}\n{actual_line}"
        ), inline=False)

    if ai_text and len(ai_text) > 50:
        chunks = split_text(ai_text, 1024)
        for i, chunk in enumerate(chunks):
            label = "🔍 Analisis Dampak" if i == 0 else "🔍 Analisis (lanjutan)"
            embed.add_field(name=label, value=chunk, inline=False)
    else:
        embed.add_field(name="🔍 Analisis Dampak", value="AI sedang memproses. Coba lagi.", inline=False)

    embed.set_footer(text="Forex Factory | Groq AI")
    return embed

def build_realtime_embed(event, ai_text):
    verdict, vi = "IN-LINE", "⚪"
    try:
        f = float(event["forecast"]) if event["forecast"] not in ("N/A","","None",None) else None
        a = float(event["actual"]) if event["actual"] not in ("N/A","","None",None) else None
        if f is not None and a is not None:
            diff = abs(a - f)
            thr = abs(f) * 0.05 if f != 0 else 0.5
            if diff > thr:
                verdict = "BEAT" if a > f else "MISS"
                vi = "🟢" if a > f else "🔴"
    except: pass

    embed = discord.Embed(title=f"⚡ DATA RILIS - {event['title']}", color=discord.Color.orange())
    embed.add_field(name=f"Detail {vi} {verdict}", value=(
        f"⏰ {event['time_wib']}\nF: {event['forecast']} | P: {event['previous']} | A: {event['actual']}"
    ), inline=False)
    if ai_text:
        chunks = split_text(ai_text, 1024)
        for i, c in enumerate(chunks):
            label = "📝 Dampak & Saran" if i == 0 else "📝 (lanjutan)"
            embed.add_field(name=label, value=c, inline=False)
    embed.set_footer(text="Realtime | Forex Factory | Groq AI")
    return embed

# --- COMMANDS ---
@bot.command()
async def report(ctx):
    if CHANNEL_ID and ctx.channel.id != CHANNEL_ID:
        return
    async with asyncio.Lock():
        loading = await ctx.send("⏳ Mengumpulkan data...")
        try:
            btc, global_data, dxy, fear_greed, news = await asyncio.gather(
                get_btc_data(), get_global_data(), get_dxy_data(), get_fear_greed(), get_news()
            )
            ai = await get_ai_analysis(btc, global_data, dxy, fear_greed, news)
            embed = build_report_embeds(btc, global_data, dxy, fear_greed, news, ai)
            # HAPUS loading message DULU, baru kirim embed
            try:
                await loading.delete()
            except:
                pass
            await ctx.send(embed=embed)
        except Exception as e:
            try:
                await loading.delete()
                await ctx.send(f"❌ Error: {e}")
            except:
                pass

@bot.command()
async def macro(ctx):
    if CHANNEL_ID and ctx.channel.id != CHANNEL_ID:
        return
    async with asyncio.Lock():
        loading = await ctx.send("⏳ Mengambil kalender ekonomi...")
        try:
            events = await get_ff_events()
            ai_text = await get_macro_analysis(events) if events else None
            embed = build_macro_embed(events, ai_text)
            try:
                await loading.delete()
            except:
                pass
            await ctx.send(embed=embed)
        except Exception as e:
            try:
                await loading.delete()
                await ctx.send(f"❌ Error: {e}")
            except:
                pass

# --- LOOPS ---
async def auto_post():
    while True:
        now = datetime.now(wib)
        target = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_secs = (target - now).total_seconds()
        print(f"[AUTO] Next post in {wait_secs/3600:.1f} hours")
        await asyncio.sleep(wait_secs)

        channel = bot.get_channel(CHANNEL_ID)
        if not channel: continue

        try:
            btc, global_data, dxy, fear_greed, news = await asyncio.gather(
                get_btc_data(), get_global_data(), get_dxy_data(), get_fear_greed(), get_news()
            )
            ai = await get_ai_analysis(btc, global_data, dxy, fear_greed, news)
            embed1 = build_report_embeds(btc, global_data, dxy, fear_greed, news, ai)
            await channel.send(embed=embed1)
            print("[AUTO] Report posted")
            await asyncio.sleep(3)

            events = await get_ff_events()
            macro_ai = await get_macro_analysis(events) if events else None
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
                        print(f"[RT] {e['title']}")
        except Exception as ex:
            print(f"[RT] Error: {ex}")
        await asyncio.sleep(120)

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user}")
    bot.loop.create_task(auto_post())
    bot.loop.create_task(realtime_monitor())

bot.run(DISCORD_TOKEN)
