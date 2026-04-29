import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import json
import requests as req_lib
from datetime import datetime, timedelta
import pytz

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
REPORT_HOUR = int(os.getenv("REPORT_HOUR_WIB", "8"))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
last_alerted = set()

wib = pytz.timezone("Asia/Jakarta")
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
         "Juli", "Agustus", "September", "Oktober", "November", "Desember"]

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_PROXY = "https://api.allorigins.win/raw?url=" + FF_URL
FF_PROXY2 = "https://corsproxy.io/?" + FF_URL


async def fetch_json(url, retries=3):
    for i in range(retries):
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers={"User-Agent": UA}) as r:
                    if r.status == 200:
                        return await r.json()
        except Exception as e:
            print("[retry " + str(i + 1) + "] " + url + ": " + str(e))
            await asyncio.sleep(2)
    return None


def format_wib_time(date_str):
    try:
        if not date_str:
            return "N/A"
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        dt_wib = dt.astimezone(wib)
        today_wib = datetime.now(wib).date()
        event_date = dt_wib.date()
        day_name = HARI[dt_wib.weekday()]
        month_name = BULAN[dt_wib.month - 1]
        time_str = dt_wib.strftime("%H:%M")
        if event_date == today_wib:
            return "Hari Ini | " + time_str + " WIB"
        elif event_date == today_wib + timedelta(days=1):
            return "Besok (" + day_name + ") | " + time_str + " WIB"
        else:
            return day_name + ", " + str(dt_wib.day) + " " + month_name + " | " + time_str + " WIB"
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


async def get_btc_data():
    data = await fetch_json("https://min-api.cryptocompare.com/data/pricemultifull?fsyms=BTC&tsyms=USDT")
    if data and "RAW" in data and "BTC" in data["RAW"] and "USDT" in data["RAW"]["BTC"]:
        d = data["RAW"]["BTC"]["USDT"]
        return {"price": d.get("PRICE", 0), "change_24h": d.get("CHANGEPCT24HOUR", 0),
                "high": d.get("HIGH24HOUR", 0), "low": d.get("LOW24HOUR", 0),
                "volume": d.get("TOTALVOLUME24HTO", 0)}
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
    news = []
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
    ff = await get_ff_events()
    if ff:
        for e in ff[:5]:
            news.append({"title": "[USD] " + e["title"] + " (Forecast: " + str(e.get("forecast", "N/A")) + ")",
                         "url": "https://www.forexfactory.com/calendar"})
        return news if news else [{"title": "Tidak ada berita tersedia", "url": ""}]
    return [{"title": "Tidak ada berita tersedia", "url": ""}]


def _parse_ff(data):
    events = []
    for e in data:
        if e.get("country") != "USD":
            continue
        impact = e.get("impact", "").lower()
        if impact not in ("high", "medium"):
            continue
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


def _try_fetch(url):
    """Sync fetch using requests library"""
    try:
        r = req_lib.get(url, headers={"User-Agent": UA}, timeout=15)
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, list) and len(d) > 0:
                return d
    except Exception as e:
        print("[fetch] " + url + " failed: " + str(e))
    return None


async def get_ff_events():
    print("[FF] Fetching...")

    # 1. Direct via requests
    d = _try_fetch(FF_URL)
    if d:
        print("[FF] direct OK: " + str(len(d)))
        return _parse_ff(d)

    # 2. Proxy allorigins
    d = _try_fetch(FF_PROXY)
    if d:
        print("[FF] allorigins OK: " + str(len(d)))
        return _parse_ff(d)

    # 3. Proxy corsproxy
    d = _try_fetch(FF_PROXY2)
    if d:
        print("[FF] corsproxy OK: " + str(len(d)))
        return _parse_ff(d)

    # 4. aiohttp direct
    d = await fetch_json(FF_URL)
    if d and isinstance(d, list) and len(d) > 0:
        print("[FF] aiohttp OK: " + str(len(d)))
        return _parse_ff(d)

    # 5. aiohttp proxy
    d = await fetch_json(FF_PROXY)
    if d and isinstance(d, list) and len(d) > 0:
        print("[FF] aiohttp proxy OK: " + str(len(d)))
        return _parse_ff(d)

    # 6. Next week via proxy
    ff_next = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
    d = _try_fetch("https://api.allorigins.win/raw?url=" + ff_next)
    if d:
        print("[FF] nextweek proxy OK: " + str(len(d)))
        return _parse_ff(d)

    print("[FF] ALL FAILED")
    return []


async def call_groq(prompt, max_tokens=3000, timeout_sec=45):
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": "Bearer " + GROQ_API_KEY, "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0.7}
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result["choices"][0]["message"]["content"].strip()
                print("[GROQ] Error " + str(resp.status))
    except Exception as e:
        print("[GROQ] " + str(e))
    return None


async def get_ai_analysis(btc, gd, dxy, fg, news):
    trend = "Bullish" if btc["change_24h"] > 0 else "Bearish"
    fg_s = str(fg["value"]) + " - " + fg["label"]
    nt = "\n".join("- " + n["title"] for n in news[:5])
    dxy_s = str(dxy) if dxy else "N/A"

    prompt = (
        "Kamu analis crypto profesional. Bahasa Indonesia. "
        "JAWAB 3 BAGIAN, setiap bagian maks 800 karakter:\n\n"
        "DATA: BTC $" + f"{btc['price']:,.2f}" + " (" + f"{btc['change_24h']:+.2f}" + "%) "
        "Range $" + f"{btc['high']:,.2f}" + "/$" + f"{btc['low']:,.2f}" + " "
        "Vol $" + f"{btc['volume']:,.0f}" + " DXY " + dxy_s + " "
        "MCap $" + f"{gd['market_cap']:,.0f}" + " Vol $" + f"{gd['volume']:,.0f}" + " "
        "BTC Dom " + f"{gd['btc_dom']:.1f}" + "% ETH Dom " + f"{gd['eth_dom']:.1f}" + "% "
        "F&G " + fg_s + " Mkt " + f"{gd['change_24h']:+.2f}" + "% " + trend + "\n"
        "BERITA:\n" + nt + "\n\n"
        "[1] RINGKASAN PASAR (2-3 kalimat)\n"
        "[2] PSIKOLOGI PASAR (2-3 kalimat)\n"
        "[3] PREDIKSI ARAH MARKET (2-3 kalimat)\n\n"
        "Emoji 1 per bagian. WAJIB tulis [1] [2] [3]. Jangan ** atau ##."
    )

    result = await call_groq(prompt, max_tokens=2000)
    if result:
        parts = {}
        seps = [("[1]", "ringkasan"), ("[2]", "psikologi"), ("[3]", "prediksi")]
        for si, (lab, key) in enumerate(seps):
            idx = result.find(lab)
            if idx != -1:
                nxt = len(result)
                for nl, _ in seps[si + 1:]:
                    ni = result.find(nl, idx + 3)
                    if ni != -1:
                        nxt = ni
                        break
                txt = result[idx + 3:nxt].strip().lstrip("0123456789.:- ").strip()
                parts[key] = txt[:1024]
        if len(parts) == 3:
            return parts

    cd = btc["change_24h"]
    ds = "menguat menekan crypto" if (dxy and dxy > 100) else "melemah memberi ruang bagi crypto"
    fv = fg["value"]
    fl = fg["label"]
    if fv < 25:
        fd = "ekstrem takut"
        fa = "Peluang akumulasi bagi long-term trader"
    elif fv < 40:
        fd = "takut"
        fa = "Potensi rebound tapi tunggu konfirmasi"
    elif fv < 60:
        fd = "netral"
        fa = "Kondisi sideways, tunggu konfirmasi arah"
    elif fv < 75:
        fd = "serakah"
        fa = "Waspadai potensi profit taking"
    else:
        fd = "ekstrem serakah"
        fa = "Risiko koreksi tinggi, waspada"
    pd = "potensi menguat" if cd > 0 else "potensi koreksi"

    return {
        "ringkasan": "BTC di $" + f"{btc['price']:,.2f}" + " (" + f"{cd:+.2f}" + "%), market "
            + ("naik" if cd > 0 else "turun") + " " + f"{gd['change_24h']:+.2f}" + "%. "
            "BTC Dom " + f"{gd['btc_dom']:.1f}" + "%, DXY " + dxy_s + ". DXY " + ds + ".",
        "psikologi": "Fear & Greed " + str(fv) + " (" + fl + "), sentimen " + fd + ". " + fa + ".",
        "prediksi": "BTC " + pd + " dalam 24-48 jam. Support ~$" + f"{btc['low']:,.0f}"
            + ", Resistance ~$" + f"{btc['high']:,.0f}" + ". Perhatikan data ekonomi AS."
    }


async def get_macro_analysis(events):
    if not events:
        return None
    lines = []
    for e in events:
        a = e["actual"] if e["actual"] else "belum"
        lines.append("- [" + e["impact"] + "] " + e["title"] + " | " + e["time_wib"] + " | F: " + e["forecast"] + " | P: " + e["previous"] + " | A: " + a)
    el = "\n".join(lines)
    prompt = ("Analisis event ekonomi USD. Indonesia. Event maks 600 karakter:\n\n" + el + "\n\n"
        "EVENT: [nama]\nRESEARCH: (1-2 kalimat)\nPROYEKSI: (1-2 kalimat)\nTERDAMPAK: (1-2 kalimat)\n\n"
        "Emoji sedikit. Jangan ** atau ##. Separator === antar event.")
    return await call_groq(prompt, max_tokens=4000, timeout_sec=60)


async def get_realtime_alert(event):
    prompt = ("Data RILIS: " + event["title"] + " | F: " + event["forecast"] + " | P: " + event["previous"] + " | A: " + event["actual"]
        + "\n\nVERDICT: BEAT/MISS/IN-LINE\nDAMPAK: Arti data, USD/DXY, Fed, BTC/ETH\nSARAN: Trading 1-6 jam, level BTC, risiko\n\nEmoji sedikit. Jangan ** atau ##")
    r = await call_groq(prompt, max_tokens=800, timeout_sec=30)
    if r:
        return r
    return "Data " + event["title"] + " rilis. Actual: " + event["actual"] + " vs Forecast: " + event["forecast"] + "."


def build_report_embeds(btc, gd, dxy, fg, news, ai):
    embed = discord.Embed(title="Laporan Pasar Crypto - " + datetime.now(wib).strftime("%d %B %Y"), color=discord.Color.orange())
    ti = "📈" if btc["change_24h"] >= 0 else "📉"
    embed.add_field(name=ti + " Data Pasar", value=(
        "BTC/USDT: $" + f"{btc['price']:,.2f}" + " (" + f"{btc['change_24h']:+.2f}" + "%)\n"
        "High/Low: $" + f"{btc['high']:,.2f}" + " / $" + f"{btc['low']:,.2f}" + "\n"
        "Volume 24h: $" + f"{btc['volume']:,.0f}" + "\n"
        "DXY Index: " + (str(dxy) if dxy else "N/A")), inline=False)

    fs = str(fg["value"]) + " - " + fg["label"]
    mi = "🟢" if gd["change_24h"] > 0 else "🔴"
    tr = "Bullish" if gd["change_24h"] > 0 else "Bearish"
    embed.add_field(name=mi + " Market Global", value=(
        "Market Cap: $" + f"{gd['market_cap']:,.0f}" + "\n"
        "Volume: $" + f"{gd['volume']:,.0f}" + "\n"
        "BTC Dom: " + f"{gd['btc_dom']:.1f}" + "% | ETH Dom: " + f"{gd['eth_dom']:.1f}" + "%\n"
        "Fear & Greed: " + fs + "\n"
        "Market 24h: " + f"{gd['change_24h']:+.2f}" + "% | " + tr), inline=False)

    nl = []
    for n in news[:5]:
        u = n.get("url", "")
        if u:
            nl.append("[" + n["title"] + "](" + u + ")")
        else:
            nl.append(n["title"])
    embed.add_field(name="📰 Berita Terkini", value="\n".join(nl), inline=False)

    if isinstance(ai, dict):
        embed.add_field(name="🤖 Ringkasan Pasar", value=ai.get("ringkasan", "N/A")[:1024], inline=False)
        embed.add_field(name="🧠 Psikologi Pasar", value=ai.get("psikologi", "N/A")[:1024], inline=False)
        embed.add_field(name="🎯 Prediksi Market", value=ai.get("prediksi", "N/A")[:1024], inline=False)
    else:
        chunks = split_text(ai, 1024) if ai else ["Gagal generate analisis"]
        for i, chunk in enumerate(chunks[:3]):
            embed.add_field(name=["🤖 Ringkasan", "🧠 Psikologi", "🎯 Prediksi"][i], value=chunk[:1024], inline=False)

    embed.set_footer(text="Not Financial Advice | DYOR | Groq AI")
    return embed


def build_macro_embed(events, ai_text):
    embed = discord.Embed(title="📅 Kalender Ekonomi USD - " + datetime.now(wib).strftime("%d %B %Y"),
        description="Event USD berdampak HIGH & MEDIUM", color=discord.Color.orange())
    if not events:
        embed.add_field(name="⚠️ Info", value="API gagal diakses. Coba !macro lagi.", inline=False)
        embed.set_footer(text="Forex Factory | Groq AI")
        return embed
    for e in events[:10]:
        ic = "🔴" if e["impact"] == "HIGH" else "🟡"
        mk = ""
        if e["is_today"]:
            mk += " ⬅️ HARI INI"
        if e["is_released"]:
            mk += " ✅"
        al = "Actual: " + e["actual"] if e["is_released"] else "Belum Rilis"
        embed.add_field(name=ic + " " + e["title"] + mk,
            value="⏰ " + e["time_wib"] + "\nF: " + e["forecast"] + " | P: " + e["previous"] + "\n" + al, inline=False)
    if ai_text and len(ai_text) > 50:
        for i, chunk in enumerate(split_text(ai_text, 1024)):
            embed.add_field(name="🔍 Analisis Dampak" if i == 0 else "🔍 (lanjutan)", value=chunk, inline=False)
    else:
        embed.add_field(name="🔍 Analisis Dampak", value="AI memproses. Coba lagi.", inline=False)
    embed.set_footer(text="Forex Factory | Groq AI")
    return embed


def build_realtime_embed(event, ai_text):
    verdict = "IN-LINE"
    vi = "⚪"
    try:
        ff = float(event["forecast"]) if event["forecast"] not in ("N/A", "", "None", None) else None
        af = float(event["actual"]) if event["actual"] not in ("N/A", "", "None", None) else None
        if ff is not None and af is not None:
            diff = abs(af - ff)
            thr = abs(ff) * 0.05 if ff != 0 else 0.5
            if diff > thr:
                verdict = "BEAT" if af > ff else "MISS"
                vi = "🟢" if af > ff else "🔴"
    except:
        pass
    embed = discord.Embed(title="⚡ DATA RILIS - " + event["title"], color=discord.Color.orange())
    embed.add_field(name="Detail " + vi + " " + verdict,
        value="⏰ " + event["time_wib"] + "\nF: " + event["forecast"] + " | P: " + event["previous"] + " | A: " + event["actual"], inline=False)
    if ai_text:
        for i, c in enumerate(split_text(ai_text, 1024)):
            embed.add_field(name="📝 Dampak & Saran" if i == 0 else "📝 (lanjutan)", value=c, inline=False)
    embed.set_footer(text="Realtime | Forex Factory | Groq AI")
    return embed


@bot.command()
async def report(ctx):
    if CHANNEL_ID and ctx.channel.id != CHANNEL_ID:
        return
    async with asyncio.Lock():
        loading = await ctx.send("⏳ Mengumpulkan data...")
        try:
            btc, gd, dxy, fg, news = await asyncio.gather(get_btc_data(), get_global_data(), get_dxy_data(), get_fear_greed(), get_news())
            ai = await get_ai_analysis(btc, gd, dxy, fg, news)
            embed = build_report_embeds(btc, gd, dxy, fg, news, ai)
            try:
                await loading.delete()
            except:
                pass
            await ctx.send(embed=embed)
        except Exception as e:
            try:
                await loading.delete()
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
            except:
                pass


async def auto_post():
    while True:
        now = datetime.now(wib)
        target = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        print("[AUTO] Next in " + str(round((target - now).total_seconds() / 3600, 1)) + "h")
        await asyncio.sleep((target - now).total_seconds())
        ch = bot.get_channel(CHANNEL_ID)
        if not ch:
            continue
        try:
            btc, gd, dxy, fg, news = await asyncio.gather(get_btc_data(), get_global_data(), get_dxy_data(), get_fear_greed(), get_news())
            ai = await get_ai_analysis(btc, gd, dxy, fg, news)
            await ch.send(embed=build_report_embeds(btc, gd, dxy, fg, news, ai))
            await asyncio.sleep(3)
            events = await get_ff_events()
            mai = await get_macro_analysis(events) if events else None
            await ch.send(embed=build_macro_embed(events, mai))
            print("[AUTO] Done")
        except Exception as e:
            print("[AUTO] " + str(e))


async def realtime_monitor():
    await bot.wait_until_ready()
    while True:
        try:
            events = await get_ff_events()
            for e in events:
                if e["is_released"] and e["title"] not in last_alerted:
                    last_alerted.add(e["title"])
                    ch = bot.get_channel(CHANNEL_ID)
                    if ch:
                        ai_text = await get_realtime_alert(e)
                        await ch.send(embed=build_realtime_embed(e, ai_text))
        except:
            pass
        await asyncio.sleep(120)


@bot.event
async def on_ready():
    print("Bot online: " + str(bot.user))
    bot.loop.create_task(auto_post())
    bot.loop.create_task(realtime_monitor())


bot.run(DISCORD_TOKEN)
