import os
import asyncio
import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pytz

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHAN = int(os.getenv("CHANNEL_ID", "0"))
GROQ = os.getenv("GROQ_API_KEY")
HOUR = int(os.getenv("REPORT_HOUR_WIB", "8"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# ===================== API FETCH =====================

async def fetch(session, url, retries=3):
    for i in range(retries):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15), headers=UA) as r:
                if r.status == 200:
                    return await r.json()
                if r.status == 429:
                    await asyncio.sleep(3 ** i)
                else:
                    print(f"[HTTP {r.status}] {url}")
        except Exception as e:
            print(f"[Retry {i+1}] {url}: {e}")
            if i < retries - 1:
                await asyncio.sleep(2)
    return None

# ===================== DATA SOURCES =====================

async def get_btc_data(session):
    url = "https://min-api.cryptocompare.com/data/pricemultifull?fsyms=BTC&tsyms=USDT"
    data = await fetch(session, url)
    if data and "RAW" in data:
        try:
            d = data["RAW"]["BTC"]["USDT"]
            return {"price": d.get("PRICE", 0), "change_24h": d.get("CHANGEPCT24HOUR", 0), "high_24h": d.get("HIGH24HOUR", 0), "low_24h": d.get("LOW24HOUR", 0), "volume_24h": d.get("TOTALVOLUME24HTO", 0), "mcap": d.get("MKTCAP", 0)}
        except (KeyError, TypeError):
            pass
    url2 = "https://api.coincap.io/v2/assets/bitcoin"
    data2 = await fetch(session, url2)
    if data2 and "data" in data2:
        d = data2["data"]
        return {"price": float(d.get("priceUsd", 0)), "change_24h": float(d.get("changePercent24Hr", 0)), "high_24h": 0, "low_24h": 0, "volume_24h": float(d.get("volumeUsd24Hr", 0)), "mcap": float(d.get("marketCapUsd", 0))}
    return None

async def get_global_data(session):
    url = "https://api.coingecko.com/api/v3/global"
    data = await fetch(session, url)
    if data and "data" in data:
        d = data["data"]
        mcap = d.get("total_market_cap", {}).get("usd", 0)
        vol = d.get("total_volume", {}).get("usd", 0)
        btc_d = d.get("market_cap_percentage", {}).get("btc", 0)
        if mcap > 0 and 30 < btc_d < 80:
            return {"total_market_cap": mcap, "volume_24h": vol, "btc_dominance": btc_d, "eth_dominance": d.get("market_cap_percentage", {}).get("eth", 0), "active_cryptos": d.get("active_cryptocurrencies", 0), "market_change_24h": d.get("market_cap_change_percentage_24h_usd", 0)}
    url2 = "https://api.coincap.io/v2/global"
    data2 = await fetch(session, url2)
    if data2 and "data" in data2:
        d = data2["data"]
        mcap = float(d.get("marketCapUsd", 0))
        btc_d = float(d.get("btcDominance", 0))
        if mcap > 0 and 30 < btc_d < 80:
            return {"total_market_cap": mcap, "volume_24h": float(d.get("volume24hUsd", 0)), "btc_dominance": btc_d, "eth_dominance": float(d.get("ethDominance", 0)), "active_cryptos": int(d.get("assets", 0)), "market_change_24h": float(d.get("marketCapChangePercentage24Hr", 0))}
    return None

async def get_dxy_data(session):
    url = "https://api.exchangerate-api.com/v4/latest/USD"
    data = await fetch(session, url)
    if data and "rates" in data:
        r = data["rates"]
        try:
            eurusd = 1.0 / r.get("EUR", 1)
            gbpusd = 1.0 / r.get("GBP", 1)
            usdjpy = r.get("JPY", 1)
            usdcad = r.get("CAD", 1)
            usdsek = r.get("SEK", 1)
            usdchf = r.get("CHF", 1)
            dxy = 50.14348112 * (eurusd ** (-0.576)) * (usdjpy ** 0.136) * (gbpusd ** (-0.119)) * (usdcad ** 0.091) * (usdsek ** 0.042) * (usdchf ** 0.036)
            if 90 < dxy < 120:
                return {"dxy": dxy}
        except Exception:
            pass
    return None

async def get_fear_greed(session):
    url = "https://api.alternative.me/fng/?limit=1"
    data = await fetch(session, url)
    if data and "data" in data and len(data["data"]) > 0:
        d = data["data"][0]
        return {"value": int(d.get("value", 0)), "label": d.get("value_classification", "N/A")}
    return None

async def get_news(session):
    url = "https://cryptopanic.com/api/free/v1/posts/?public=true&filter=rising&currencies=BTC,ETH&limit=5"
    data = await fetch(session, url)
    if data and "results" in data and len(data["results"]) > 0:
        return [{"title": i.get("title", ""), "url": i.get("url", ""), "source": i.get("source", {}).get("title", "")} for i in data["results"][:5]]
    url2 = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest&limit=5"
    data2 = await fetch(session, url2)
    if data2 and "Data" in data2 and len(data2["Data"]) > 0:
        return [{"title": i.get("title", ""), "url": i.get("url", ""), "source": i.get("source", "")} for i in data2["Data"][:5]]
    ff = await get_ff_events(session)
    if ff:
        return [{"title": f"[USD] {e['title']} (Forecast: {e['forecast']}, Previous: {e['previous']})", "url": "https://www.forexfactory.com/calendar", "source": "Forex Factory"} for e in ff[:5]]
    return None

async def get_ff_events(session):
    """Forex Factory calendar - USD events only"""
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    data = await fetch(session, url)
    if not data or not isinstance(data, list):
        return None
    wib = pytz.timezone("Asia/Jakarta")
    today_str = datetime.now(wib).strftime("%Y-%m-%d")
    events = []
    for e in data:
        if e.get("country", "") != "USD":
            continue
        impact = e.get("impact", "").strip().upper()
        if impact not in ("HIGH", "MEDIUM"):
            continue
        title = e.get("title", "")
        forecast = e.get("forecast", "")
        previous = e.get("previous", "")
        actual = e.get("actual", "")
        date = e.get("date", "")
        try:
            dt = datetime.strptime(date[:19], "%Y-%m-%dT%H:%M:%S")
            dt_wib = dt.astimezone(wib)
            time_str = dt_wib.strftime("%H:%M")
            day_name = dt_wib.strftime("%A")
            date_str = dt_wib.strftime("%d %B")
            full_date = f"{day_name}, {date_str}"
            is_today = today_str in date
            is_released = bool(actual and actual.strip() and actual.strip() != "-")
        except Exception:
            time_str = "-"
            full_date = "-"
            is_today = False
            is_released = False
        events.append({"time": time_str, "date": full_date, "is_today": is_today, "is_released": is_released, "currency": "USD", "title": title, "impact": impact, "forecast": forecast if forecast else "-", "previous": previous if previous else "-", "actual": actual if actual else "-"})
    events.sort(key=lambda x: (x["date"], x["time"]))
    return events if events else None

# ===================== AI ANALYSIS =====================

async def get_ai_analysis(session, btc, dxy, gdata, fg, news):
    if not GROQ:
        return None
    btc_price = f"${btc['price']:,.2f}" if btc and btc.get("price") else "N/A"
    btc_change = f"{btc['change_24h']:+.2f}%" if btc and btc.get("change_24h") else "N/A"
    btc_high = f"${btc['high_24h']:,.2f}" if btc and btc.get("high_24h") else "N/A"
    btc_low = f"${btc['low_24h']:,.2f}" if btc and btc.get("low_24h") else "N/A"
    btc_vol = f"${btc['volume_24h']:,.0f}" if btc and btc.get("volume_24h") else "N/A"
    dxy_val = f"{dxy['dxy']:.2f}" if dxy else "N/A"
    mcap = f"${gdata['total_market_cap']:,.0f}" if gdata and gdata.get("total_market_cap", 0) > 0 else "N/A"
    mc_ch = f"{gdata['market_change_24h']:+.2f}%" if gdata and gdata.get("market_change_24h") else "N/A"
    vol = f"${gdata['volume_24h']:,.0f}" if gdata and gdata.get("volume_24h", 0) > 0 else "N/A"
    btc_dom = f"{gdata['btc_dominance']:.1f}%" if gdata else "N/A"
    eth_dom = f"{gdata['eth_dominance']:.1f}%" if gdata else "N/A"
    fg_val = f"{fg['value']} ({fg['label']})" if fg else "N/A"
    news_str = ""
    if news:
        for n in news[:5]:
            news_str += f"- {n['title']}\n"
    prompt = f"""Kamu analis crypto profesional. Analisis pasar hari ini dalam Bahasa Indonesia.

DATA:
BTC/USDT: {btc_price} ({btc_change})
High/Low 24h: {btc_high} / {btc_low}
Volume BTC: {btc_vol}
DXY: {dxy_val}
Total Market Cap: {mcap} ({mc_ch})
Volume Global: {vol}
BTC Dom: {btc_dom} | ETH Dom: {eth_dom}
Fear & Greed: {fg_val}

BERITA:
{news_str if news_str else "Tidak tersedia"}

Buat analisis dalam 2 bagian:
1. Ringkasan Pasar - rangkum data harga, volume, market cap, DXY
2. Analisis Teknikal BTC/USDT - analisis high/low, dominasi BTC, fear & greed, dampak DXY

Jawab padat dalam Bahasa Indonesia, maks 300 kata. Jangan gunakan emoji berlebihan."""
    try:
        async with session.post("https://api.groq.com/openai/v1/chat/completions",
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 1200, "temperature": 0.7},
            headers={"Authorization": f"Bearer {GROQ}", "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                return (await resp.json())["choices"][0]["message"]["content"]
    except Exception:
        pass
    return None

async def get_macro_analysis(session, events):
    if not GROQ:
        return None
    wib = pytz.timezone("Asia/Jakarta")
    today = datetime.now(wib).strftime("%A, %d %B %Y")
    if events:
        ev_list = []
        for e in events[:8]:
            status = "SUDAH RILIS" if e.get("is_released") else "BELUM RILIS"
            ev_list.append(f"Event: {e['title']}\nWaktu: {e['date']} pukul {e['time']} WIB | Impact: {e['impact']}\nForecast: {e['forecast']} | Previous: {e['previous']} | Actual: {e['actual']} | Status: {status}")
        ev_str = "\n\n---\n\n".join(ev_list)
        prompt = f"""Kamu analis forex & makroekonomi senior. Buat EVENT BRIEF detail untuk SETIAP event ekonomi USD dalam Bahasa Indonesia.

HARI INI: {today}

EVENT:
{ev_str}

Untuk SETIAP event, buat analisis lengkap dengan format ini (tanpa emoji berlebihan):

**NAMA EVENT**
{today} / HARI, TANGGAL BULAN - Pukul HH:MM WIB
Perkiraan: X.X% | Sebelumnya: X.X% | Status: SUDAH/BELUM RILIS

**Research**
[Jelaskan konteks historis event ini. Data release sebelumnya bagaimana? Tren terkini? Kebijakan The Fed/FOMC terkait? Minimal 3 kalimat.]

**Proyeksi**
[Proyeksi dampak ke DXY Index dan USD. Jika beat/miss forecast, bagaimana efek ke DXY? Dampak ke BTC/USDT dan pasar crypto? Minimal 3 kalimat.]

**Wildcard**
[Faktor risiko tak terduga. Apa yang bisa membuat hasil di luar ekspektasi? Minimal 2 kalimat.]

**Terdampak**
[Pair forex dan crypto: EURUSD - GBPUSD - USDJPY - BTC/USDT - ETH/USDT]

PENTING:
- Bahasa Indonesia
- Maks 200 kata per event
- Setiap bagian harus substansial
- Jangan gunakan emoji berlebihan"""
    else:
        prompt = f"""Kamu analis forex & makroekonomi senior. Hari ini {today}.
Tidak ada event USD berdampak tinggi hari ini.

Buat outlook makro lengkap dalam Bahasa Indonesia:
- Kondisi DXY Index terkini dan tren
- Kebijakan The Fed / FOMC terbaru
- Event ekonomi penting minggu ini
- Dampak ke BTC/USDT dan pasar crypto

Jawab detail, maks 300 kata."""
    try:
        async with session.post("https://api.groq.com/openai/v1/chat/completions",
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 2500, "temperature": 0.7},
            headers={"Authorization": f"Bearer {GROQ}", "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=45)) as resp:
            if resp.status == 200:
                return (await resp.json())["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[Macro AI Error] {e}")
    return None

async def get_realtime_alert(session, event, btc_now, dxy_now):
    """AI analysis for realtime event alert"""
    if not GROQ:
        return None
    btc_p = f"${btc_now['price']:,.2f}" if btc_now and btc_now.get("price") else "N/A"
    btc_c = f"{btc_now['change_24h']:+.2f}%" if btc_now and btc_now.get("change_24h") else "N/A"
    dxy_v = f"{dxy_now['dxy']:.2f}" if dxy_now else "N/A"
    prompt = f"""Data ekonomi USD baru saja rilis. Buat analisis singkat dalam Bahasa Indonesia.

EVENT: {event['title']}
Waktu: {event['date']} {event['time']} WIB
Forecast: {event['forecast']}
Previous: {event['previous']}
ACTUAL: {event['actual']}

BTC/USDT saat ini: {btc_p} ({btc_c})
DXY Index saat ini: {dxy_v}

Analisis dalam 3 bagian singkat:
1. Hasil - apakah beat/miss/in-line forecast? Apa artinya?
2. Dampak - dampak ke DXY, USD, dan pasar crypto
3. Saran - apa yang harus dilakukan trader? entry, hold, atau avoid?

Jawab padat, maks 150 kata. Tanpa emoji berlebihan."""
    try:
        async with session.post("https://api.groq.com/openai/v1/chat/completions",
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 800, "temperature": 0.7},
            headers={"Authorization": f"Bearer {GROQ}", "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 200:
                return (await resp.json())["choices"][0]["message"]["content"]
    except Exception:
        pass
    return None

# ===================== FORMAT HELPERS =====================

def fmt_num(n):
    if not n or n <= 0: return "N/A"
    return f"${n:,.0f}"

def fg_emoji(v):
    if not v: return "N/A"
    if v <= 20: return f"{v} Extreme Fear"
    elif v <= 40: return f"{v} Fear"
    elif v <= 60: return f"{v} Neutral"
    elif v <= 80: return f"{v} Greed"
    else: return f"{v} Extreme Greed"

IMPACT_ICONS = {"HIGH": "HIGH", "MEDIUM": "MED"}

# ===================== EMBED BUILDERS =====================

def build_embed(btc, dxy, gdata, fg, news, analysis):
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    embed = discord.Embed(title=f"Laporan Pasar Crypto Harian - {now.strftime('%d %B %Y')}", color=discord.Color.orange(), timestamp=now)
    dp = "Data tidak tersedia"
    if btc:
        ch = btc.get("change_24h", 0) or 0
        arrow = "+" if ch >= 0 else ""
        dp = f"**BTC/USDT:** ${btc['price']:,.2f} ({arrow}{ch:.2f}% 24h)\n"
        dp += f"**High/Low:** ${btc['high_24h']:,.2f} / ${btc['low_24h']:,.2f}\n"
        dp += f"**Volume 24h:** {fmt_num(btc.get('volume_24h'))}\n"
        dp += f"**DXY Index:** {dxy['dxy']:.2f}" if dxy else "**DXY Index:** N/A"
    embed.add_field(name="Data Pasar", value=dp, inline=False)
    mg = "Data tidak tersedia"
    if gdata:
        fg_s = fg_emoji(fg["value"]) if fg else "N/A"
        mc = gdata.get("market_change_24h", 0) or 0
        me = "+" if mc >= 0 else ""
        mg = f"**Market Cap:** {fmt_num(gdata.get('total_market_cap'))}\n"
        mg += f"**Volume Global:** {fmt_num(gdata.get('volume_24h'))}\n"
        mg += f"**BTC Dom:** {gdata.get('btc_dominance', 0):.1f}% | **ETH Dom:** {gdata.get('eth_dominance', 0):.1f}%\n"
        mg += f"**Fear & Greed:** {fg_s}\n"
        mg += f"**Market 24h:** {me}{mc:.2f}%\n"
        if mc > 0: mg += "**Trending:** Bullish"
        elif mc < 0: mg += "**Trending:** Bearish"
        else: mg += "**Trending:** Sideways"
    embed.add_field(name="Market Global", value=mg, inline=False)
    if news and len(news) > 0:
        lines = [f"**{i+1}.** [{n['title']}]({n['url']}) - *{n.get('source', '')}*" for i, n in enumerate(news)]
        embed.add_field(name="Berita Terkini", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Berita Terkini", value="Tidak ada berita tersedia saat ini.", inline=False)
    # AI ANALYSIS - always show
    if analysis:
        lines = analysis.strip().split("\n")
        sections = []
        cur_sec = []
        cur_title = None
        for line in lines:
            s = line.strip()
            if not s: continue
            is_t = s.startswith("1.") or s.startswith("2.") or s.startswith("Ringkasan") or s.startswith("Analisis")
            if is_t and cur_title:
                sections.append((cur_title, "\n".join(cur_sec)))
                cur_sec = []
            if is_t:
                c = s.lstrip("0123456789.").strip()
                if not c.startswith("**"): c = f"**{c}**"
                if "Ringkasan" in c or "Pasar" in c: cur_title = f"{c}"
                elif "Teknikal" in c: cur_title = f"{c}"
                else: cur_title = c
            else:
                cur_sec.append(s)
        if cur_title and cur_sec:
            sections.append((cur_title, "\n".join(cur_sec)))
        if not sections and analysis.strip():
            sections.append(("Analisis AI", analysis.strip()))
        for title, content in sections:
            if len(content) > 1024: content = content[:1024].rsplit(" ", 1)[0] + "..."
            embed.add_field(name=title, value=content, inline=False)
    embed.set_footer(text="Not Financial Advice | DYOR\nGroq AI | CryptoCompare | CoinGecko | CryptoPanic | Forex Factory")
    return embed

def build_macro_embed(events, analysis):
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    embed = discord.Embed(title=f"Economic Calendar (USD) - {now.strftime('%d %B %Y')}", description="Event makroekonomi USD berdampak tinggi", color=discord.Color.orange(), timestamp=now)
    if events:
        ev_text = ""
        for e in events[:10]:
            impact = IMPACT_ICONS.get(e.get("impact", "HIGH"), "HIGH")
            actual = f" | Aktual: {e['actual']}" if e.get("is_released") else ""
            if e.get("is_today"):
                ev_text += f"[{impact}] **{e['title']}** - {e['time']} WIB (HARI INI)\n"
            else:
                ev_text += f"[{impact}] **{e['title']}** - {e['date']}, {e['time']} WIB\n"
            ev_text += f"Forecast: {e['forecast']} | Previous: {e['previous']}{actual}\n\n"
        embed.add_field(name="Event Minggu Ini", value=ev_text.strip(), inline=False)
    else:
        embed.add_field(name="Info", value="Tidak ada event USD berdampak tinggi minggu ini.", inline=False)
    if analysis:
        lines = analysis.strip().split("\n")
        sections = []
        cur = []
        cur_title = None
        section_keys = ["RESEARCH", "PROYEKSI", "WILDCARD", "TERDAMPAK", "OUTLOOK"]
        for line in lines:
            s = line.strip()
            if not s: continue
            clean = s.replace("**", "").strip().upper()
            is_header = any(clean.startswith(k) for k in section_keys)
            is_event = any(w in clean for w in ["CPI", "NFP", "GDP", "PPI", "FOMC", "RATE", "EMPLOYMENT", "RETAIL", "PMI", "ISM", "JOBLESS", "UNEMPLOYMENT", "NONFARM", "CONSUMER", "MANUFACTURING", "PRESIDENT", "FEDERAL FUNDS"])
            if is_header:
                if cur_title and cur:
                    sections.append((cur_title, "\n".join(cur)))
                    cur = []
                cur_title = f"**{s.replace('**', '').strip()}**"
            elif is_event and (s.startswith("**") or len(s) < 80):
                if cur_title and cur:
                    sections.append((cur_title, "\n".join(cur)))
                    cur = []
                t = s.replace("**", "").strip()
                cur_title = f"**EVENT BRIEF - {t}**"
            else:
                cur.append(s)
        if cur_title and cur:
            sections.append((cur_title, "\n".join(cur)))
        if not sections and analysis.strip():
            sections.append(("Analisis Makro", analysis.strip()))
        for title, content in sections:
            if len(content) > 1024: content = content[:1024].rsplit(" ", 1)[0] + "..."
            embed.add_field(name=title, value=content, inline=False)
    embed.set_footer(text="Research brief - tunggu konfirmasi angka sebelum entry. Bukan sinyal langsung.\nData: Forex Factory | Analysis: Groq AI")
    return embed

def build_realtime_embed(event, btc, dxy, analysis):
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    actual = event.get("actual", "-")
    forecast = event.get("forecast", "-")
    if actual != "-" and forecast != "-" and forecast != "":
        try:
            a = float(actual.replace("%", "").replace("K", "").replace("B", "").replace("M", "").strip())
            f = float(forecast.replace("%", "").replace("K", "").replace("B", "").replace("M", "").strip())
            if a > f:
                verdict = "BEAT (lebih tinggi dari forecast)"
            elif a < f:
                verdict = "MISS (lebih rendah dari forecast)"
            else:
                verdict = "IN-LINE (sesuai forecast)"
        except Exception:
            verdict = "Data rilis"
    else:
        verdict = "Data rilis"
    embed = discord.Embed(
        title=f"DATA RILIS: {event['title']}",
        description=f"{event['date']} | {event['time']} WIB",
        color=discord.Color.orange(),
        timestamp=now,
    )
    detail = f"**Forecast:** {forecast}\n**Previous:** {event['previous']}\n**Actual:** {actual}\n**Verdict:** {verdict}"
    embed.add_field(name="Data", value=detail, inline=False)
    if btc:
        ch = btc.get("change_24h", 0) or 0
        embed.add_field(name="BTC/USDT Sekarang", value=f"${btc['price']:,.2f} ({ch:+.2f}%)", inline=False)
    if dxy:
        embed.add_field(name="DXY Index Sekarang", value=f"{dxy['dxy']:.2f}", inline=False)
    if analysis:
        if len(analysis) > 1024: analysis = analysis[:1024].rsplit(" ", 1)[0] + "..."
        embed.add_field(name="Analisis & Saran", value=analysis, inline=False)
    embed.set_footer(text="Not Financial Advice | DYOR\nForex Factory | Groq AI")
    return embed

# ===================== REPORT GENERATORS =====================

async def generate_report():
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(get_btc_data(session), get_dxy_data(session), get_global_data(session), get_fear_greed(session), get_news(session), return_exceptions=True)
        btc, dxy, gdata, fg, news = [None if isinstance(r, Exception) else r for r in results]
        for name, r in zip(["BTC", "DXY", "Global", "FG", "News"], results):
            if isinstance(r, Exception): print(f"[{name} Error] {r}")
        analysis = await get_ai_analysis(session, btc, dxy, gdata, fg, news)
        return build_embed(btc, dxy, gdata, fg, news, analysis)

async def generate_macro():
    async with aiohttp.ClientSession() as session:
        events = await get_ff_events(session)
        analysis = await get_macro_analysis(session, events)
        return build_macro_embed(events, analysis)

# ===================== REALTIME MONITOR =====================

last_alerted = set()

async def realtime_monitor():
    """Check every 2 minutes for newly released data, post immediately"""
    await bot.wait_until_ready()
    await asyncio.sleep(60)  # Wait 1 min after bot start
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                events = await get_ff_events(session)
                if not events:
                    await asyncio.sleep(120)
                    continue
                newly_released = []
                for e in events:
                    eid = f"{e['title']}_{e['date']}_{e['time']}"
                    if e.get("is_released") and eid not in last_alerted:
                        newly_released.append(e)
                        last_alerted.add(eid)
                for e in newly_released:
                    channel = bot.get_channel(CHAN)
                    if not channel:
                        continue
                    # Get current BTC & DXY for context
                    results = await asyncio.gather(get_btc_data(session), get_dxy_data(session), return_exceptions=True)
                    btc = None if isinstance(results[0], Exception) else results[0]
                    dxy = None if isinstance(results[1], Exception) else results[1]
                    analysis = await get_realtime_alert(session, e, btc, dxy)
                    embed = build_realtime_embed(e, btc, dxy, analysis)
                    await channel.send(embed=embed)
                    print(f"[Realtime] Posted: {e['title']} - Actual: {e['actual']}")
        except Exception as ex:
            print(f"[Realtime Error] {ex}")
        await asyncio.sleep(120)  # Check every 2 minutes

# ===================== BOT COMMANDS =====================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"Channel ID: {CHAN}")
    print(f"Auto-post at {HOUR}:00 WIB")
    print(f"Realtime monitoring: active")
    bot.loop.create_task(auto_post_loop())
    bot.loop.create_task(realtime_monitor())

@bot.command()
async def report(ctx):
    try:
        embed = await generate_report()
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error: {e}")

@bot.command()
async def macro(ctx):
    try:
        embed = await generate_macro()
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error: {e}")

# ===================== AUTO POST =====================

async def auto_post_loop():
    wib = pytz.timezone("Asia/Jakarta")
    await bot.wait_until_ready()
    while True:
        now = datetime.now(wib)
        target = now.replace(hour=HOUR, minute=0, second=0, microsecond=0)
        if now >= target: target += timedelta(days=1)
        wait = (target - now).total_seconds()
        print(f"Next auto-post in {wait/3600:.1f} hours ({target.strftime('%Y-%m-%d %H:%M WIB')})")
        await asyncio.sleep(wait)
        channel = bot.get_channel(CHAN)
        if channel:
            try:
                embed1 = await generate_report()
                await channel.send(embed=embed1)
                await asyncio.sleep(3)
                embed2 = await generate_macro()
                await channel.send(embed=embed2)
                print(f"Auto-post sent ({datetime.now(wib).strftime('%H:%M WIB')})")
            except Exception as e:
                print(f"Auto-post failed: {e}")

# ===================== MAIN =====================

if __name__ == "__main__":
    print("Starting bot...")
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("DISCORD_BOT_TOKEN not set!")
