import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import json
import logging
from datetime import datetime, timedelta, time
import pytz
from pathlib import Path

# ====================== CONFIG ======================
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
REPORT_HOUR_WIB = int(os.getenv("REPORT_HOUR_WIB", "8"))

# Persistent storage untuk Railway
DATA_DIR = Path("/data") if Path("/data").exists() else Path(".")
LAST_ALERTED_FILE = DATA_DIR / "last_alerted.json"

# ====================== LOGGING ======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ====================== BOT SETUP ======================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
http_session: aiohttp.ClientSession = None

wib = pytz.timezone("Asia/Jakarta")

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

last_alerted = set()

# ====================== STORAGE ======================
def load_last_alerted():
    global last_alerted
    if LAST_ALERTED_FILE.exists():
        try:
            with open(LAST_ALERTED_FILE, "r", encoding="utf-8") as f:
                last_alerted = set(json.load(f))
            logger.info(f"Loaded {len(last_alerted)} previous alerts")
        except Exception as e:
            logger.error(f"Gagal load last_alerted: {e}")

def save_last_alerted():
    try:
        with open(LAST_ALERTED_FILE, "w", encoding="utf-8") as f:
            json.dump(list(last_alerted), f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Gagal save last_alerted: {e}")

# ====================== HELPERS ======================
async def fetch(url: str, retries: int = 3):
    for i in range(retries):
        try:
            async with http_session.get(url, timeout=aiohttp.ClientTimeout(total=15), headers=HEADERS) as r:
                if r.status == 200:
                    return await r.json()
        except Exception as e:
            logger.warning(f"Fetch failed {url} (attempt {i+1})")
            await asyncio.sleep(2 ** i)
    return None

def format_wib_time(date_str: str) -> str:
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        dt_wib = dt.astimezone(wib)
        day_name = HARI[dt_wib.weekday()]
        month_name = BULAN[dt_wib.month - 1]
        time_str = dt_wib.strftime("%H:%M")
        today = datetime.now(wib).date()
        if dt_wib.date() == today:
            return f"Hari Ini | {time_str} WIB"
        elif dt_wib.date() == today + timedelta(days=1):
            return f"Besok ({day_name}) | {time_str} WIB"
        return f"{day_name}, {dt_wib.day} {month_name} | {time_str} WIB"
    except:
        return "N/A"

def is_today(date_str: str) -> bool:
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.astimezone(wib).date() == datetime.now(wib).date()
    except:
        return False

def is_released_recently(event: dict) -> bool:
    try:
        if not event.get("actual") or str(event.get("actual")).strip() in ("", "N/A", "None"):
            return False
        dt = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        return (datetime.now(wib) - dt.astimezone(wib)).total_seconds() < 7200
    except:
        return False

# ====================== DATA FETCHERS ======================
async def get_btc_data():
    data = await fetch("https://min-api.cryptocompare.com/data/pricemultifull?fsyms=BTC&tsyms=USDT")
    if data and "RAW" in data and "BTC" in data["RAW"]:
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
        return {
            "market_cap": d.get("total_market_cap", {}).get("usd", 0),
            "volume": d.get("total_volume", {}).get("usd", 0),
            "btc_dom": d.get("market_cap_percentage", {}).get("btc", 0),
            "eth_dom": d.get("market_cap_percentage", {}).get("eth", 0),
            "change_24h": d.get("market_cap_change_percentage_24h_usd", 0)
        }
    return {"market_cap": 0, "volume": 0, "btc_dom": 0, "eth_dom": 0, "change_24h": 0}

async def get_dxy_data():
    data = await fetch("https://api.exchangerate-api.com/v4/latest/USD")
    if data and "rates" in data:
        r = data["rates"]
        try:
            dxy = 50.14348112 * (r.get("EUR",1)**-0.576) * (r.get("JPY",1)**0.136) * \
                  (r.get("GBP",1)**-0.119) * (r.get("CAD",1)**0.091) * \
                  (r.get("SEK",1)**0.042) * (r.get("CHF",1)**0.036)
            return round(dxy, 2) if 80 <= dxy <= 130 else None
        except:
            pass
    return None

async def get_fear_greed():
    data = await fetch("https://api.alternative.me/fng/?limit=1")
    if data and "data" in data and data["data"]:
        item = data["data"][0]
        return {"value": int(item["value"]), "label": item["value_classification"]}
    return {"value": 0, "label": "N/A"}

async def get_news():
    try:
        data = await fetch("https://cryptopanic.com/api/free/v1/posts/?auth_token=demo&filter=rising&currencies=BTC,ETH")
        if data and "results" in data:
            return [item.get("title", "") for item in data["results"][:5] if item.get("title")]
    except:
        pass
    return ["Tidak ada berita signifikan saat ini."]

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
        events.append({
            "title": e.get("title", "Unknown"),
            "date_raw": e.get("date", ""),
            "time_wib": format_wib_time(e.get("date", "")),
            "is_today": is_today(e.get("date", "")),
            "forecast": str(e.get("forecast", "N/A")),
            "previous": str(e.get("previous", "N/A")),
            "actual": str(e.get("actual", "")),
            "impact": impact.upper(),
            "is_released": bool(e.get("actual") and str(e.get("actual")).strip() not in ("", "N/A"))
        })
    return events

# ====================== AI ANALYSIS ======================
async def get_ai_analysis(btc, global_data, dxy, fear_greed, news):
    trend = "Bullish" if btc["change_24h"] > 0 else "Bearish"
    fg = f"{fear_greed['value']} - {fear_greed['label']}"
    
    prompt = f"""Kamu adalah analis crypto profesional. Berikan analisis mendetail dalam Bahasa Indonesia.

DATA PASAR:
- BTC Price: ${btc['price']:,.2f} ({btc['change_24h']:+.2f}% 24h)
- 24h Range: ${btc['high']:,.2f} / ${btc['low']:,.2f}
- Volume: ${btc['volume']:,.0f}
- DXY Index: {dxy if dxy else 'N/A'}
- BTC Dominance: {global_data['btc_dom']:.1f}%
- Fear & Greed: {fg}
- Market Trend: {trend}

BERITA TERKINI:
{chr(10).join('- ' + n for n in news[:5])}

Buat analisis dengan struktur berikut (setiap bagian minimal 3-4 kalimat):
RINGKASAN PASAR
PSIKOLOGI PASAR & SENTIMEN
PREDIKSI ARAH MARKET (24-48 jam)

Gunakan bahasa Indonesia yang natural, tambahkan 1-2 emoji per bagian."""

    try:
        async with http_session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 2800, "temperature": 0.7},
            timeout=45
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"AI Analysis error: {e}")
    
    return f"📊 BTC saat ini di ${btc['price']:,.2f} ({btc['change_24h']:+.2f}%). Sentimen pasar {trend.lower()}."

async def get_macro_analysis(events):
    if not events:
        return "Tidak ada event ekonomi USD berdampak tinggi atau sedang minggu ini."
    
    event_list = "\n".join([f"- [{e['impact']}] {e['title']} | {e['time_wib']} | Forecast: {e['forecast']}" for e in events])
    
    prompt = f"""Kamu adalah analis makroekonomi profesional. Analisis event-event berikut dalam Bahasa Indonesia:

{event_list}

Untuk setiap event penting, berikan analisis dengan format:
EVENT: [Nama Event]
RESEARCH: Penjelasan singkat apa itu event dan kenapa penting
PROYEKSI: Apa yang diharapkan dan potensi dampaknya
TERDAMPAK: Bagaimana dampaknya ke BTC, ETH, dan market crypto

Gunakan bahasa Indonesia profesional."""

    try:
        async with http_session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 3500, "temperature": 0.7},
            timeout=50
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Macro Analysis error: {e}")
        return "Analisis makro sedang tidak tersedia saat ini."

async def get_realtime_alert(event):
    prompt = f"""Kamu adalah analis crypto dan makro. Data ekonomi USD baru saja rilis:

Event: {event['title']}
Actual: {event['actual']}
Forecast: {event['forecast']}
Previous: {event['previous']}
Waktu: {event['time_wib']}

Berikan analisis singkat tapi mendalam dalam Bahasa Indonesia dengan format:
VERDICT: BEAT / MISS / IN-LINE
PENJELASAN DAMPAK: 
SARAN TRADING: 

Gunakan emoji secukupnya."""

    try:
        async with http_session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 900, "temperature": 0.7},
            timeout=30
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Realtime AI error: {e}")
        return f"Data {event['title']} rilis Actual: {event['actual']}"

# ====================== EMBED BUILDERS ======================
def build_embed(btc, global_data, dxy, fear_greed, news, ai_text):
    embed = discord.Embed(
        title=f"Laporan Pasar Crypto Harian - {datetime.now(wib).strftime('%d %B %Y')}",
        color=discord.Color.orange()
    )
    embed.add_field(name="📈 BTC/USDT", value=f"Price: `${btc['price']:,.2f}` ({btc['change_24h']:+.2f}%)\nHigh/Low: `${btc['high']:,.2f}` / `${btc['low']:,.2f}`\nVolume: `${btc['volume']:,.0f}`", inline=False)
    embed.add_field(name="🌍 Market Global", value=f"Market Cap: `${global_data['market_cap']:,.0f}`\nBTC Dom: {global_data['btc_dom']:.1f}% | ETH Dom: {global_data['eth_dom']:.1f}%\nFear & Greed: {fear_greed['value']} - {fear_greed['label']}\nDXY: {dxy if dxy else 'N/A'}", inline=False)
    embed.add_field(name="📰 Berita Terkini", value="\n".join(f"• {n}" for n in news[:5]), inline=False)
    if ai_text and len(ai_text) > 50:
        embed.add_field(name="🤖 Analisis AI", value=ai_text[:3900], inline=False)
    embed.set_footer(text="Not Financial Advice • DYOR")
    return embed

def build_macro_embed(events, ai_text):
    embed = discord.Embed(title=f"📅 Kalender Ekonomi USD - {datetime.now(wib).strftime('%d %B %Y')}", color=discord.Color.orange())
    for e in events[:10]:
        icon = "🔴" if e["impact"] == "HIGH" else "🟡"
        today = " ⬅️ HARI INI" if e["is_today"] else ""
        released = " ✅ RILIS" if e["is_released"] else ""
        embed.add_field(
            name=f"{icon} {e['title']}{today}{released}",
            value=f"⏰ {e['time_wib']}\nForecast: {e['forecast']} | Prev: {e['previous']}\nActual: {e['actual'] or 'Belum rilis'}",
            inline=False
        )
    if ai_text and len(ai_text) > 30:
        embed.add_field(name="🔍 Analisis Dampak AI", value=ai_text[:3900], inline=False)
    return embed

def build_realtime_embed(event, ai_text):
    embed = discord.Embed(title=f"⚡ DATA RILIS - {event['title']}", color=discord.Color.orange())
    embed.add_field(name="Detail Rilis", value=f"⏰ {event['time_wib']}\nForecast: {event['forecast']}\nPrevious: {event['previous']}\nActual: {event['actual']}", inline=False)
    if ai_text:
        embed.add_field(name="📝 Analisis & Saran", value=ai_text[:3500], inline=False)
    embed.set_footer(text="Realtime Economic Data Alert")
    return embed

# ====================== COMMANDS (UNTUK TESTING) ======================
@bot.command()
async def report(ctx):
    """Manual test untuk Daily Market Report"""
    if CHANNEL_ID and ctx.channel.id != CHANNEL_ID:
        await ctx.send("❌ Command ini hanya bisa digunakan di channel yang sudah ditentukan.")
        return
    
    msg = await ctx.send("🔄 Mengumpulkan data pasar dan menghasilkan analisis AI...")
    try:
        btc, global_data, dxy, fear_greed, news = await asyncio.gather(
            get_btc_data(), get_global_data(), get_dxy_data(), get_fear_greed(), get_news()
        )
        ai_text = await get_ai_analysis(btc, global_data, dxy, fear_greed, news)
        embed = build_embed(btc, global_data, dxy, fear_greed, news, ai_text)
        await msg.delete()
        await ctx.send(embed=embed)
        logger.info("Manual !report executed successfully")
    except Exception as e:
        await msg.edit(content=f"❌ Error saat menjalankan report: {e}")
        logger.error(f"Manual report error: {e}")

@bot.command()
async def macro(ctx):
    """Manual test untuk Kalender Ekonomi"""
    if CHANNEL_ID and ctx.channel.id != CHANNEL_ID:
        await ctx.send("❌ Command ini hanya bisa digunakan di channel yang sudah ditentukan.")
        return
    
    msg = await ctx.send("📅 Mengambil data kalender ekonomi dan analisis AI...")
    try:
        events = await get_ff_events()
        ai_text = await get_macro_analysis(events)
        embed = build_macro_embed(events, ai_text)
        await msg.delete()
        await ctx.send(embed=embed)
        logger.info("Manual !macro executed successfully")
    except Exception as e:
        await msg.edit(content=f"❌ Error saat menjalankan macro: {e}")
        logger.error(f"Manual macro error: {e}")

# ====================== TASKS ======================
@tasks.loop(time=time(hour=REPORT_HOUR_WIB, minute=0, tzinfo=wib))
async def daily_report():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return
    try:
        logger.info("Menjalankan Daily Report otomatis...")
        btc, global_data, dxy, fear_greed, news = await asyncio.gather(
            get_btc_data(), get_global_data(), get_dxy_data(), get_fear_greed(), get_news()
        )
        ai_text = await get_ai_analysis(btc, global_data, dxy, fear_greed, news)
        await channel.send(embed=build_embed(btc, global_data, dxy, fear_greed, news, ai_text))

        await asyncio.sleep(4)
        events = await get_ff_events()
        macro_ai = await get_macro_analysis(events)
        await channel.send(embed=build_macro_embed(events, macro_ai))
        logger.info("✅ Daily Report otomatis selesai")
    except Exception as e:
        logger.exception(f"Error daily_report: {e}")

@tasks.loop(minutes=2)
async def realtime_monitor():
    try:
        events = await get_ff_events()
        for e in events:
            key = e["title"]
            if is_released_recently(e) and e["is_released"] and key not in last_alerted:
                last_alerted.add(key)
                save_last_alerted()
                channel = bot.get_channel(CHANNEL_ID)
                if channel:
                    ai_text = await get_realtime_alert(e)
                    await channel.send(embed=build_realtime_embed(e, ai_text))
                    logger.info(f"Realtime alert dikirim: {key}")
    except Exception as ex:
        logger.error(f"Realtime monitor error: {ex}")

# ====================== EVENTS ======================
@bot.event
async def on_ready():
    global http_session
    http_session = aiohttp.ClientSession()
    load_last_alerted()
    
    logger.info(f"✅ Bot berhasil online sebagai {bot.user}")
    daily_report.start()
    realtime_monitor.start()
    logger.info(f"Daily report dijadwalkan setiap hari pukul {REPORT_HOUR_WIB:02d}:00 WIB")

@bot.event
async def on_disconnect():
    if http_session and not http_session.closed:
        await http_session.close()

# ====================== RUN ======================
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.critical("DISCORD_BOT_TOKEN belum di-set di Railway!")
    elif not GROQ_API_KEY:
        logger.critical("GROQ_API_KEY belum di-set!")
    elif CHANNEL_ID == 0:
        logger.critical("CHANNEL_ID belum di-set!")
    else:
        bot.run(DISCORD_TOKEN)
