import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import json
import time
import requests as req_lib
from datetime import datetime, timedelta
import pytz

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CMC_API_KEY = os.getenv("CMC_API_KEY", "")
REPORT_HOUR = int(os.getenv("REPORT_HOUR_WIB", "8"))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
last_alerted = set()

ff_cache = {"data": None, "timestamp": 0}
FF_CACHE_TTL = 300

wib = pytz.timezone("Asia/Jakarta")
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
         "Juli", "Agustus", "September", "Oktober", "November", "Desember"]

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_PROXY = "https://api.allorigins.win/raw?url=" + FF_URL
FF_PROXY2 = "https://corsproxy.io/?" + FF_URL

CMC_BASE = "https://pro-api.coinmarketcap.com"
CMC_HEADERS = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}


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


# ===== COINMARKETCAP (PRIMARY) =====

def _cmc_fetch(url):
    try:
        r = req_lib.get(url, headers=CMC_HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
        print("[CMC] Error " + str(r.status_code))
    except Exception as e:
        print("[CMC] " + str(e))
    return None


async def get_btc_data():
    # 1. CoinMarketCap
    if CMC_API_KEY:
        try:
            loop = asyncio.get_event_loop()
            url = CMC_BASE + "/v1/cryptocurrency/quotes/latest?symbol=BTC&convert=USD"
            data = await loop.run_in_executor(None, _cmc_fetch, url)
            if data and "data" in data and "BTC" in data["data"]:
                q = data["data"]["BTC"]["quote"]["USD"]
                p = q.get("price", 0)
                c24 = q.get("percent_change_24h", 0)
                c7d = q.get("percent_change_7d", 0)
                return {
                    "price": p, "change_24h": c24, "change_7d": c7d,
                    "high": p * (1 + abs(c24) / 100 * 1.5),
                    "low": p * (1 - abs(c24) / 100 * 1.5),
                    "volume": q.get("volume_24h", 0),
                    "market_cap": q.get("market_cap", 0)
                }
        except Exception as e:
            print("[BTC-CMC] " + str(e))

    # 2. CryptoCompare
    data = await fetch_json("https://min-api.cryptocompare.com/data/pricemultifull?fsyms=BTC&tsyms=USDT")
    if data and "RAW" in data and "BTC" in data["RAW"] and "USDT" in data["RAW"]["BTC"]:
        d = data["RAW"]["BTC"]["USDT"]
        return {"price": d.get("PRICE", 0), "change_24h": d.get("CHANGEPCT24HOUR", 0),
                "change_7d": 0, "high": d.get("HIGH24HOUR", 0), "low": d.get("LOW24HOUR", 0),
                "volume": d.get("TOTALVOLUME24HTO", 0), "market_cap": 0}

    # 3. CoinCap
    data = await fetch_json("https://api.coincap.io/v2/assets/bitcoin")
    if data and "data" in data:
        d = data["data"]
        return {"price": float(d.get("priceUsd", 0)), "change_24h": float(d.get("changePercent24Hr", 0)),
                "change_7d": 0, "high": 0, "low": 0,
                "volume": float(d.get("volumeUsd24Hr", 0)), "market_cap": 0}

    return {"price": 0, "change_24h": 0, "change_7d": 0, "high": 0, "low": 0, "volume": 0, "market_cap": 0}


async def get_global_data():
    # 1. CoinMarketCap
    if CMC_API_KEY:
        try:
            loop = asyncio.get_event_loop()
            url = CMC_BASE + "/v1/global-metrics/quotes/latest"
            data = await loop.run_in_executor(None, _cmc_fetch, url)
            if data and "data" in data:
                d = data["data"]
                bd = d.get("btc_dominance", 0)
                ed = d.get("eth_dominance", 0)
                if bd and ed:
                    return {
                        "market_cap": d.get("quote", {}).get("USD", {}).get("total_market_cap", 0),
                        "volume": d.get("quote", {}).get("USD", {}).get("total_volume_24h", 0),
                        "btc_dom": float(bd), "eth_dom": float(ed),
                        "change_24h": d.get("quote", {}).get("USD", {}).get("market_cap_change_24h", 0),
                        "total_cryptos": d.get("total_cryptocurrencies", 0)
                    }
        except Exception as e:
            print("[GLOBAL-CMC] " + str(e))

    # 2. CoinGecko
    data = await fetch_json("https://api.coingecko.com/api/v3/global")
    if data and "data" in data:
        d = data["data"]
        bd = d.get("market_cap_percentage", {}).get("btc", 0)
        ed = d.get("market_cap_percentage", {}).get("eth", 0)
        if 30 <= bd <= 80:
            return {"market_cap": d.get("total_market_cap", {}).get("usd", 0),
                    "volume": d.get("total_volume", {}).get("usd", 0),
                    "btc_dom": bd, "eth_dom": ed,
                    "change_24h": d.get("market_cap_change_percentage_24h_usd", 0),
                    "total_cryptos": 0}

    # 3. CoinCap
    data = await fetch_json("https://api.coincap.io/v2/global")
    if data and "data" in data:
        d = data["data"]
        return {"market_cap": float(d.get("marketCap", 0)), "volume": float(d.get("volume", 0)),
                "btc_dom": float(d.get("btcDominance", 0)), "eth_dom": 0,
                "change_24h": 0, "total_cryptos": 0}

    return {"market_cap": 0, "volume": 0, "btc_dom": 0, "eth_dom": 0, "change_24h": 0, "total_cryptos": 0}


async def get_top_gainers():
    if not CMC_API_KEY:
        return []
    try:
        loop = asyncio.get_event_loop()
        url = CMC_BASE + "/v1/cryptocurrency/listings/latest?limit=10&sort=percent_change_24h&sort_dir=desc&convert=USD"
        data = await loop.run_in_executor(None, _cmc_fetch, url)
        if data and "data" in data:
            gainers = []
            for coin in data["data"][:5]:
                q = coin["quote"]["USD"]
                gainers.append({"name": coin["name"], "symbol": coin["symbol"],
                    "price": q.get("price", 0), "change_24h": q.get("percent_change_24h", 0)})
            return gainers
    except Exception as e:
        print("[GAINERS] " + str(e))
    return []


async def get_top_losers():
    if not CMC_API_KEY:
        return []
    try:
        loop = asyncio.get_event_loop()
        url = CMC_BASE + "/v1/cryptocurrency/listings/latest?limit=10&sort=percent_change_24h&sort_dir=asc&convert=USD"
        data = await loop.run_in_executor(None, _cmc_fetch, url)
        if data and "data" in data:
            losers = []
            for coin in data["data"][:5]:
                q = coin["quote"]["USD"]
                losers.append({"name": coin["name"], "symbol": coin["symbol"],
                    "price": q.get("price", 0), "change_24h": q.get("percent_change_24h", 0)})
            return losers
    except Exception as e:
        print("[LOSERS] " + str(e))
    return []


async def get_dxy_data():
    # 1. exchangerate-api
    data = await fetch_json("https://api.exchangerate-api.com/v4/latest/USD")
    if data and "rates" in data:
        r = data["rates"]
        dxy = 50.14348112 * (r.get("EUR",1)**-0.576) * (r.get("JPY",1)**0.136) * (r.get("GBP",1)**-0.119) * (r.get("CAD",1)**0.091) * (r.get("SEK",1)**0.042) * (r.get("CHF",1)**0.036)
        if 90 <= dxy <= 120:
            return round(dxy, 2)
    # 2. frankfurter.app
    try:
        data = await fetch_json("https://api.frankfurter.app/latest?from=USD&to=EUR,GBP,JPY,CAD,SEK,CHF")
        if data and "rates" in data:
            r = data["rates"]
            dxy = 50.14348112 * (r.get("EUR",1)**-0.576) * (r.get("JPY",1)**0.136) * (r.get("GBP",1)**-0.119) * (r.get("CAD",1)**0.091) * (r.get("SEK",1)**0.042) * (r.get("CHF",1)**0.036)
            if 90 <= dxy <= 120:
                return round(dxy, 2)
    except:
        pass
    # 3. open.er-api.com
    try:
        data = await fetch_json("https://open.er-api.com/v6/latest/USD")
        if data and "rates" in data:
            r = data["rates"]
            dxy = 50.14348112 * (r.get("EUR",1)**-0.576) * (r.get("JPY",1)**0.136) * (r.get("GBP",1)**-0.119) * (r.get("CAD",1)**0.091) * (r.get("SEK",1)**0.042) * (r.get("CHF",1)**0.036)
            if 90 <= dxy <= 120:
                return round(dxy, 2)
    except:
        pass
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
                t = item.get("title", "").strip()
                u = item.get("url", "")
                if t and u:
                    news.append({"title": t, "url": u})
            if len(news) >= 3:
                return news
    except:
        pass
    try:
        data = await fetch_json("https://min-api.cryptocompare.com/data/v2/news/?lang=EN&categories=BTC,ETH&excludeCategories=Sponsored")
        if data and "Data" in data:
            for item in data["Data"][:7]:
                t = item.get("title", "").strip()
                u = item.get("url", "") or item.get("guid", "")
                if t and u:
                    news.append({"title": t, "url": u})
            if len(news) >= 3:
                return news
    except:
        pass
    ff = await get_ff_events()
    if ff:
        for e in ff[:5]:
            news.append({"title": "[USD] " + e["title"] + " (Forecast: " + e["forecast"] + ")",
                         "url": "https://www.forexfactory.com/calendar"})
        return news if news else [{"title": "Tidak ada berita tersedia", "url": ""}]
    return [{"title": "Tidak ada berita tersedia", "url": ""}]


# ===== FOREX FACTORY (6 METHODS + CACHE) =====

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
    try:
        r = req_lib.get(url, headers={"User-Agent": UA}, timeout=15)
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, list) and len(d) > 0:
                return d
    except Exception as e:
        print("[fetch] " + url[:60] + " failed: " + str(e))
    return None


def _save_ff_cache(events):
    ff_cache["data"] = events
    ff_cache["timestamp"] = time.time()


async def get_ff_events(force_refresh=False):
    now_ts = time.time()
    if not force_refresh and ff_cache["data"] is not None:
        if (now_ts - ff_cache["timestamp"]) < FF_CACHE_TTL:
            print("[FF] Using cache: " + str(len(ff_cache["data"])) + " events")
            return ff_cache["data"]

    print("[FF] Fetching fresh...")

    # 1. requests direct
    d = _try_fetch(FF_URL)
    if d:
        _save_ff_cache(_parse_ff(d))
        print("[FF] direct OK: " + str(len(ff_cache["data"])))
        return ff_cache["data"]

    # 2. allorigins proxy
    d = _try_fetch(FF_PROXY)
    if d:
        _save_ff_cache(_parse_ff(d))
        print("[FF] allorigins OK: " + str(len(ff_cache["data"])))
        return ff_cache["data"]

    # 3. corsproxy
    d = _try_fetch(FF_PROXY2)
    if d:
        _save_ff_cache(_parse_ff(d))
        print("[FF] corsproxy OK: " + str(len(ff_cache["data"])))
        return ff_cache["data"]

    # 4. aiohttp direct
    d = await fetch_json(FF_URL)
    if d and isinstance(d, list) and len(d) > 0:
        _save_ff_cache(_parse_ff(d))
        print("[FF] aiohttp OK: " + str(len(ff_cache["data"])))
        return ff_cache["data"]

    # 5. aiohttp proxy
    d = await fetch_json(FF_PROXY)
    if d and isinstance(d, list) and len(d) > 0:
        _save_ff_cache(_parse_ff(d))
        print("[FF] aiohttp proxy OK: " + str(len(ff_cache["data"])))
        return ff_cache["data"]

    # 6. next week via proxy
    ff_next = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
    d = _try_fetch("https://api.allorigins.win/raw?url=" + ff_next)
    if d:
        _save_ff_cache(_parse_ff(d))
        print("[FF] nextweek proxy OK: " + str(len(ff_cache["data"])))
        return ff_cache["data"]

    print("[FF] ALL FAILED")
    return []


# ===== GROQ AI =====

async def call_groq(prompt, max_tokens=3000, timeout_sec=45):
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": "Bearer " + GROQ_API_KEY, "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens, "temperature": 0.7}
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result["choices"][0]["message"]["content"].strip()
                print("[GROQ] Error " + str(resp.status))
    except Exception as e:
        print("[GROQ] " + str(e))
    return None


async def get_ai_analysis(btc, gd, dxy, fg, news, gainers, losers):
    trend = "Bullish" if btc["change_24h"] > 0 else "Bearish"
    fg_s = str(fg["value"]) + " - " + fg["label"]
    nt = "\n".join("- " + n["title"] for n in news[:5])
    dxy_s = str(dxy) if dxy else "N/A"
    c7d = btc.get("change_7d", 0)

    extra = ""
    if gainers:
        extra += "\nTOP GAINERS:\n"
        for g in gainers[:3]:
            extra += "- " + g["symbol"] + ": " + f"{g['change_24h']:+.1f}" + "%\n"
    if losers:
        extra += "\nTOP LOSERS:\n"
        for l in losers[:3]:
            extra += "- " + l["symbol"] + ": " + f"{l['change_24h']:+.1f}" + "%\n"

    prompt = (
        "Kamu analis crypto profesional. Bahasa Indonesia. "
        "JAWAB 3 BAGIAN, setiap bagian maks 800 karakter:\n\n"
        "DATA: BTC $" + f"{btc['price']:,.2f}" + " (" + f"{btc['change_24h']:+.2f}"
        + "%) 7d: " + f"{c7d:+.2f}" + "% "
        "Vol $" + f"{btc['volume']:,.0f}" + " DXY " + dxy_s + " "
        "MCap $" + f"{gd['market_cap']:,.0f}" + " "
        "BTC Dom " + f"{gd['btc_dom']:.1f}" + "% "
        "ETH Dom " + f"{gd['eth_dom']:.1f}" + "% "
        "F&G " + fg_s + " Mkt " + f"{gd['change_24h']:+.2f}" + "% " + trend
        + extra
        + "\nBERITA:\n" + nt + "\n\n"
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

    # Fallback
    cd = btc["change_24h"]
    if dxy and dxy > 100:
        ds = "menguat menekan crypto"
    else:
        ds = "melemah memberi ruang bagi crypto"
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
    if cd > 0:
        pd = "potensi menguat"
    else:
        pd = "potensi koreksi"

    ringkasan = ("BTC di $" + f"{btc['price']:,.2f}" + " (" + f"{cd:+.2f}" + "%), "
        + "market " + ("naik" if cd > 0 else "turun") + " "
        + f"{gd['change_24h']:+.2f}" + "%. BTC Dom "
        + f"{gd['btc_dom']:.1f}" + "%, DXY " + dxy_s + ". DXY " + ds + ".")
    psikologi = ("Fear & Greed " + str(fv) + " (" + fl + "), sentimen "
        + fd + ". " + fa + ".")
    prediksi = ("BTC " + pd + " dalam 24-48 jam. Support ~$"
        + f"{btc['low']:,.0f}" + ", Resistance ~$" + f"{btc['high']:,.0f}"
        + ". Perhatikan data ekonomi AS.")

    return {"ringkasan": ringkasan, "psikologi": psikologi, "prediksi": prediksi}


async def get_macro_analysis(events):
    if not events:
        return None
    lines = []
    for e in events:
        a = e["actual"] if e["actual"] else "belum"
        lines.append("- [" + e["impact"] + "] " + e["title"] + " | " + e["time_wib"]
            + " | F: " + e["forecast"] + " | P: " + e["previous"] + " | A: " + a)
    el = "\n".join(lines)
    prompt = ("Analisis event ekonomi USD. Indonesia. Event maks 600 karakter:\n\n"
        + el + "\n\nEVENT: [nama]\nRESEARCH: (1-2 kalimat)\nPROYEKSI: (1-2 kalimat)\n"
        "TERDAMPAK: (1-2 kalimat)\n\nEmoji sedikit. Jangan ** atau ##. "
        "Separator === antar event.")
    return await call_groq(prompt, max_tokens=4000, timeout_sec=60)


async def get_realtime_alert(event):
    prompt = ("Data RILIS: " + event["title"] + " | F: " + event["forecast"]
        + " | P: " + event["previous"] + " | A: " + event["actual"]
        + "\n\nVERDICT: BEAT/MISS/IN-LINE\nDAMPAK: Arti data, USD/DXY, Fed, BTC/ETH\n"
        "SARAN: Trading 1-6 jam, level BTC, risiko\n\nEmoji sedikit. Jangan ** atau ##")
    r = await call_groq(prompt, max_tokens=800, timeout_sec=30)
    if r:
        return r
    return ("Data " + event["title"] + " rilis. Actual: " + event["actual"]
        + " vs Forecast: " + event["forecast"] + ".")


# ===== EMBED BUILDERS =====

def build_report_embeds(btc, gd, dxy, fg, news, ai, gainers, losers):
    embed = discord.Embed(
        title="Laporan Pasar Crypto - " + datetime.now(wib).strftime("%d %B %Y"),
        color=discord.Color.orange()
    )

    # Data Pasar
    if btc["change_24h"] >= 0:
        ti = "📈"
    else:
        ti = "📉"
    c7d = btc.get("change_7d", 0)
    dxy_s = str(dxy) if dxy else "N/A"
    data_pasar = (
        "BTC/USDT: $" + f"{btc['price']:,.2f}" + " (" + f"{btc['change_24h']:+.2f}" + "%)\n"
        "High/Low: $" + f"{btc['high']:,.2f}" + " / $" + f"{btc['low']:,.2f}" + "\n"
        "Volume 24h: $" + f"{btc['volume']:,.0f}" + "\n"
        "7d Change: " + f"{c7d:+.2f}" + "%\n"
        "DXY Index: " + dxy_s
    )
    embed.add_field(name=ti + " Data Pasar", value=data_pasar, inline=False)

    # Market Global
    fs = str(fg["value"]) + " - " + fg["label"]
    if gd["change_24h"] > 0:
        mi = "🟢"
        tr = "Bullish"
    else:
        mi = "🔴"
        tr = "Bearish"
    tc = ""
    if gd.get("total_cryptos") and gd["total_cryptos"] > 0:
        tc = "\nTotal Crypto: " + str(gd["total_cryptos"])
    market_global = (
        "Market Cap: $" + f"{gd['market_cap']:,.0f}" + "\n"
        "Volume: $" + f"{gd['volume']:,.0f}" + "\n"
        "BTC Dom: " + f"{gd['btc_dom']:.1f}" + "% | ETH Dom: "
        + f"{gd['eth_dom']:.1f}" + "%\n"
        "Fear & Greed: " + fs + "\n"
        "Market 24h: " + f"{gd['change_24h']:+.2f}" + "% | " + tr
        + tc
    )
    embed.add_field(name=mi + " Market Global", value=market_global, inline=False)

    # Berita dengan link
    nl = []
    for n in news[:5]:
        u = n.get("url", "")
        if u:
            nl.append("[" + n["title"] + "](" + u + ")")
        else:
            nl.append(n["title"])
    embed.add_field(name="📰 Berita Terkini", value="\n".join(nl), inline=False)

    # Top Movers (dari CMC)
    if gainers or losers:
        movers = ""
        if gainers:
            movers += "🏆 Top Gainers:\n"
            for g in gainers[:3]:
                movers += "  " + g["symbol"] + ": " + f"{g['change_24h']:+.1f}" + "% ($" + f"{g['price']:,.2f}" + ")\n"
        if losers:
            movers += "📉 Top Losers:\n"
            for l in losers[:3]:
                movers += "  " + l["symbol"] + ": " + f"{l['change_24h']:+.1f}" + "% ($" + f"{l['price']:,.2f}" + ")\n"
        embed.add_field(name="🔥 Top Movers 24h", value=movers.strip(), inline=False)

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

    embed.set_footer(text="Not Financial Advice | DYOR | Groq AI | CoinMarketCap | CryptoCompare | Forex Factory")
    return embed


def build_macro_embed(events, ai_text):
    embed = discord.Embed(
        title="📅 Kalender Ekonomi USD - " + datetime.now(wib).strftime("%d %B %Y"),
        description="Event USD berdampak HIGH & MEDIUM",
        color=discord.Color.orange()
    )
    if not events:
        embed.add_field(name="⚠️ Info", value="API gagal diakses. Coba !macro lagi.", inline=False)
        embed.set_footer(text="Forex Factory | Groq AI")
        return embed

    for e in events[:10]:
        if e["impact"] == "HIGH":
            ic = "🔴"
        else:
            ic = "🟡"
        mk = ""
        if e["is_today"]:
            mk += " ⬅️ HARI INI"
        if e["is_released"]:
            mk += " ✅"
        if e["is_released"]:
            al = "Actual: " + e["actual"]
        else:
            al = "Belum Rilis"
        val = ("⏰ " + e["time_wib"] + "\n"
            + "F: " + e["forecast"] + " | P: " + e["previous"] + "\n" + al)
        embed.add_field(name=ic + " " + e["title"] + mk, value=val, inline=False)

    if ai_text and len(ai_text) > 50:
        for i, chunk in enumerate(split_text(ai_text, 1024)):
            if i == 0:
                label = "🔍 Analisis Dampak"
            else:
                label = "🔍 (lanjutan)"
            embed.add_field(name=label, value=chunk, inline=False)
    else:
        embed.add_field(name="🔍 Analisis Dampak", value="AI memproses. Coba lagi.", inline=False)

    embed.set_footer(text="Forex Factory | Groq AI")
    return embed


def build_realtime_embed(event, ai_text):
    verdict = "IN-LINE"
    vi = "⚪"
    try:
        f_val = event["forecast"]
        a_val = event["actual"]
        ff = float(f_val) if f_val not in ("N/A", "", "None", None) else None
        af = float(a_val) if a_val not in ("N/A", "", "None", None) else None
        if ff is not None and af is not None:
            diff = abs(af - ff)
            thr = abs(ff) * 0.05 if ff != 0 else 0.5
            if diff > thr:
                if af > ff:
                    verdict = "BEAT"
                    vi = "🟢"
                else:
                    verdict = "MISS"
                    vi = "🔴"
    except:
        pass

    embed = discord.Embed(
        title="⚡ DATA RILIS - " + event["title"],
        color=discord.Color.orange()
    )
    det = ("⏰ " + event["time_wib"] + "\n"
        + "F: " + event["forecast"] + " | P: " + event["previous"]
        + " | A: " + event["actual"])
    embed.add_field(name="Detail " + vi + " " + verdict, value=det, inline=False)
    if ai_text:
        for i, c in enumerate(split_text(ai_text, 1024)):
            if i == 0:
                label = "📝 Dampak & Saran"
            else:
                label = "📝 (lanjutan)"
            embed.add_field(name=label, value=c, inline=False)
    embed.set_footer(text="Realtime | Forex Factory | Groq AI")
    return embed


# ===== COMMANDS =====

@bot.command()
async def report(ctx):
    if CHANNEL_ID and ctx.channel.id != CHANNEL_ID:
        return
    async with asyncio.Lock():
        loading = await ctx.send("⏳ Mengumpulkan data...")
        try:
            btc, gd, dxy, fg, news = await asyncio.gather(
                get_btc_data(), get_global_data(), get_dxy_data(), get_fear_greed(), get_news()
            )
            gainers, losers = await asyncio.gather(get_top_gainers(), get_top_losers())
            ai = await get_ai_analysis(btc, gd, dxy, fg, news, gainers, losers)
            embed = build_report_embeds(btc, gd, dxy, fg, news, ai, gainers, losers)
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


# ===== LOOPS =====

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
            print("[AUTO] Fetching data...")

            # FF events dulu, retry 3x
            events = []
            for attempt in range(3):
                events = await get_ff_events(force_refresh=True)
                if events:
                    break
                print("[AUTO] FF attempt " + str(attempt + 1) + " failed, retry in 10s...")
                await asyncio.sleep(10)

            if events:
                print("[AUTO] FF got " + str(len(events)) + " events")
            else:
                print("[AUTO] FF all attempts failed")

            btc, gd, dxy, fg, news = await asyncio.gather(
                get_btc_data(), get_global_data(), get_dxy_data(), get_fear_greed(), get_news()
            )
            gainers, losers = await asyncio.gather(get_top_gainers(), get_top_losers())
            ai = await get_ai_analysis(btc, gd, dxy, fg, news, gainers, losers)

            await ch.send(embed=build_report_embeds(btc, gd, dxy, fg, news, ai, gainers, losers))
            print("[AUTO] Report posted")
            await asyncio.sleep(3)

            # Macro: pakai cached FF data
            if not events:
                events = await get_ff_events(force_refresh=True)
            mai = await get_macro_analysis(events) if events else None
            await ch.send(embed=build_macro_embed(events, mai))
            print("[AUTO] Macro posted")

        except Exception as e:
            print("[AUTO] Error: " + str(e))


async def realtime_monitor():
    await bot.wait_until_ready()
    print("[RT] Started")
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
                        print("[RT] " + e["title"])
        except:
            pass
        await asyncio.sleep(120)


@bot.event
async def on_ready():
    print("Bot online: " + str(bot.user))
    bot.loop.create_task(auto_post())
    bot.loop.create_task(realtime_monitor())


bot.run(DISCORD_TOKEN)
