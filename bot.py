import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import json
from datetime import datetime, timedelta
import pytz

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
        return {
            "price": d.get("PRICE", 0),
            "change_24h": d.get("CHANGEPCT24HOUR", 0),
            "high": d.get("HIGH24HOUR", 0),
            "low": d.get("LOW24HOUR", 0),
            "volume": d.get("TOTALVOLUME24HTO", 0)
        }
    data = await fetch_json("https://api.coincap.io/v2/assets/bitcoin")
    if data and "data" in data:
        d = data["data"]
        return {
            "price": float(d.get("priceUsd", 0)),
            "change_24h": float(d.get("changePercent24Hr", 0)),
            "high": 0,
            "low": 0,
            "volume": float(d.get("volumeUsd24Hr", 0))
        }
    return {"price": 0, "change_24h": 0, "high": 0, "low": 0, "volume": 0}


async def get_global_data():
    data = await fetch_json("https://api.coingecko.com/api/v3/global")
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
    data = await fetch_json("https://api.coincap.io/v2/global")
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
    data = await fetch_json("https://api.exchangerate-api.com/v4/latest/USD")
    if data and "rates" in data:
        r = data["rates"]
        eur = r.get("EUR", 1)
        gbp = r.get("GBP", 1)
        jpy = r.get("JPY", 1)
        cad = r.get("CAD", 1)
        sek = r.get("SEK", 1)
        chf = r.get("CHF", 1)
        dxy = 50.14348112 * (eur ** -0.576) * (jpy ** 0.136) * (gbp ** -0.119) * (cad ** 0.091) * (sek ** 0.042) * (chf ** 0.036)
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
            news.append({
                "title": "[USD] " + e["title"] + " (Forecast: " + str(e.get("forecast", "N/A")) + ")",
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
        print("[GROQ] Exception: " + str(e))
    return None


async def get_ai_analysis(btc, global_data, dxy, fear_greed, news):
    trend = "Bullish" if btc["change_24h"] > 0 else "Bearish"
    fg = str(fear_greed["value"]) + " - " + fear_greed["label"]
    news_titles = "\n".join("- " + n["title"] for n in news[:5])

    prompt = (
        "Kamu analis crypto profesional. Berikan analisis dalam Bahasa Indonesia. "
        "JAWAB 3 BAGIAN INI SECARA TERPISAH, setiap bagian maks 800 karakter:\n\n"
        "DATA:\n"
        "- BTC: $" + f"{btc['price']:,.2f}" + " (" + f"{btc['change_24h']:+.2f}" + "%)\n"
        "- Range: $" + f"{btc['high']:,.2f}" + " / $" + f"{btc['low']:,.2f}" + "\n"
        "- Volume: $" + f"{btc['volume']:,.0f}" + "\n"
        "- DXY: " + (str(dxy) if dxy else "N/A") + "\n"
        "- Market Cap: $" + f"{global_data['market_cap']:,.0f}" + "\n"
        "- Volume Global: $" + f"{global_data['volume']:,.0f}" + "\n"
        "- BTC Dom: " + f"{global_data['btc_dom']:.1f}" + "% | ETH Dom: " + f"{global_data['eth_dom']:.1f}" + "%\n"
        "- Fear & Greed: " + fg + "\n"
        "- Market 24h: " + f"{global_data['change_24h']:+.2f}" + "% | Trend: " + trend + "\n\n"
        "BERITA:\n" + news_titles + "\n\n"
        "FORMAT JAWABAN (wajib ikuti):\n\n"
        "[1] RINGKASAN PASAR\n"
        "(2-3 kalimat. Kondisi BTC, DXY, volume, sentimen)\n\n"
        "[2] PSIKOLOGI PASAR\n"
        "(2-3 kalimat. Fear & Greed, panic selling/FOMO, perilaku trader)\n\n"
        "[3] PREDIKSI ARAH MARKET\n"
        "(2-3 kalimat. Prediksi 24-48 jam, support/resistance, skenario bullish/bearish)\n\n"
        "PENTING:\n"
        "- Bahasa Indonesia profesional\n"
        "- Emoji cukup 1 per bagian\n"
        "- WAJIB tulis [1] [2] [3] sebagai separator\n"
        "- Jangan pakai ** atau ##\n"
        "- Setiap bagian maks 800 karakter"
    )

    result = await call_groq(prompt, max_tokens=2000, timeout_sec=45)
    if result:
        parts = {}
        separators = [("[1]", "ringkasan"), ("[2]", "psikologi"), ("[3]", "prediksi")]
        for idx_s, (label, key) in enumerate(separators):
            idx = result.find(label)
            if idx != -1:
                next_idx = len(result)
                for next_label, _ in separators[idx_s + 1:]:
                    ni = result.find(next_label, idx + 3)
                    if ni != -1:
                        next_idx = ni
                        break
                text = result[idx + 3:next_idx].strip()
                text = text.lstrip("0123456789.:- ").strip()
                parts[key] = text[:1024]
        if len(parts) == 3:
            return parts

    change_dir = btc["change_24h"]
    dxy_status = "menguat menekan crypto" if (dxy and dxy > 100) else "melemah memberi ruang bagi crypto"
    fear_val = fear_greed["value"]
    fear_label = fear_greed["label"]

    if fear_val < 25:
        fear_desc = "ekstrem takut"
        fear_advice = "Peluang akumulasi bagi long-term trader"
    elif fear_val < 40:
        fear_desc = "takut"
        fear_advice = "Potensi rebound tapi tunggu konfirmasi"
    elif fear_val < 60:
        fear_desc = "netral"
        fear_advice = "Kondisi sideways, tunggu konfirmasi arah"
    elif fear_val < 75:
        fear_desc = "serakah"
        fear_advice = "Waspadai potensi profit taking"
    else:
        fear_desc = "ekstrem serakah"
        fear_advice = "Risiko koreksi tinggi, waspada"

    if change_dir > 0:
        prediksi_dir = "potensi menguat"
    else:
        prediksi_dir = "potensi koreksi"

    ringkasan = (
        "BTC di $" + f"{btc['price']:,.2f}" + " (" + f"{change_dir:+.2f}" + "%), "
        "market " + ("naik" if change_dir > 0 else "turun") + " " + f"{global_data['change_24h']:+.2f}" + "%. "
        "BTC Dom " + f"{global_data['btc_dom']:.1f}" + "%, DXY " + (str(dxy) if dxy else "N/A") + ". "
        "DXY " + dxy_status + "."
    )

    psikologi = (
        "Fear & Greed " + str(fear_val) + " (" + fear_label + "), sentimen " + fear_desc + ". "
        + fear_advice + "."
    )

    prediksi = (
        "BTC " + prediksi_dir + " dalam 24-48 jam. "
        "Support ~$" + f"{btc['low']:,.0f}" + ", Resistance ~$" + f"{btc['high']:,.0f}" + ". "
        "Perhatikan data ekonomi AS yang bisa picu volatilitas."
    )

    return {
        "ringkasan": ringkasan,
        "psikologi": psikologi,
        "prediksi": prediksi
    }


async def get_macro_analysis(events):
    if not events:
        return None

    lines = []
    for e in events:
        actual_str = e["actual"] if e["actual"] else "belum"
        line = "- [" + e["impact"] + "] " + e["title"] + " | " + e["time_wib"] + " | F: " + e["forecast"] + " | P: " + e["previous"] + " | A: " + actual_str
        lines.append(line)
    event_list = "\n".join(lines)

    prompt = (
        "Analisis event ekonomi USD dalam Bahasa Indonesia. Setiap event maks 600 karakter:\n\n"
        + event_list + "\n\n"
        "Untuk SETIAP event:\n"
        "EVENT: [nama]\n"
        "RESEARCH: (1-2 kalimat)\n"
        "PROYEKSI: (1-2 kalimat)\n"
        "TERDAMPAK: (1-2 kalimat)\n\n"
        "Indonesia profesional, emoji sedikit. Jangan ** atau ##. Separator === antar event."
    )

    return await call_groq(prompt, max_tokens=4000, timeout_sec=60)


async def get_realtime_alert(event):
    prompt = (
        "Data ekonomi RILIS:\n"
        + event["title"] + " | Forecast: " + event["forecast"]
        + " | Previous: " + event["previous"] + " | Actual: " + event["actual"] + "\n\n"
        "Analisis Bahasa Indonesia (maks 400 kata):\n"
        "VERDICT: BEAT/MISS/IN-LINE\n"
        "DAMPAK: Arti data, reaksi USD/DXY, implikasi Fed, dampak BTC/ETH\n"
        "SARAN: Apa dilakukan trader 1-6 jam, level BTC, risiko\n\n"
        "Emoji sedikit. Jangan ** atau ##"
    )

    result = await call_groq(prompt, max_tokens=800, timeout_sec=30)
    if result:
        return result
    return (
        "Data " + event["title"] + " rilis. "
        "Actual: " + event["actual"] + " vs Forecast: " + event["forecast"]
        + " vs Previous: " + event["previous"] + "."
    )


def build_report_embeds(btc, global_data, dxy, fear_greed, news, ai):
    embed = discord.Embed(
        title="Laporan Pasar Crypto - " + datetime.now(wib).strftime("%d %B %Y"),
        color=discord.Color.orange()
    )

    if btc["change_24h"] >= 0:
        trend_icon = "📈"
    else:
        trend_icon = "📉"

    data_pasar = (
        "BTC/USDT: $" + f"{btc['price']:,.2f}" + " (" + f"{btc['change_24h']:+.2f}" + "%)\n"
        "High/Low: $" + f"{btc['high']:,.2f}" + " / $" + f"{btc['low']:,.2f}" + "\n"
        "Volume 24h: $" + f"{btc['volume']:,.0f}" + "\n"
        "DXY Index: " + (str(dxy) if dxy else "N/A")
    )
    embed.add_field(name=trend_icon + " Data Pasar", value=data_pasar, inline=False)

    fg = str(fear_greed["value"]) + " - " + fear_greed["label"]
    if global_data["change_24h"] > 0:
        market_icon = "🟢"
        trend = "Bullish"
    else:
        market_icon = "🔴"
        trend = "Bearish"

    market_global = (
        "Market Cap: $" + f"{global_data['market_cap']:,.0f}" + "\n"
        "Volume: $" + f"{global_data['volume']:,.0f}" + "\n"
        "BTC Dom: " + f"{global_data['btc_dom']:.1f}" + "% | ETH Dom: " + f"{global_data['eth_dom']:.1f}" + "%\n"
        "Fear & Greed: " + fg + "\n"
        "Market 24h: " + f"{global_data['change_24h']:+.2f}" + "% | " + trend
    )
    embed.add_field(name=market_icon + " Market Global", value=market_global, inline=False)

    news_lines = []
    for n in news[:5]:
        title = n["title"]
        url = n.get("url", "")
        if url:
            news_lines.append("[" + title + "](" + url + ")")
        else:
            news_lines.append(title)
    embed.add_field(name="📰 Berita Terkini", value="\n".join(news_lines), inline=False)

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
        title="📅 Kalender Ekonomi USD - " + datetime.now(wib).strftime("%d %B %Y"),
        description="Event USD berdampak HIGH & MEDIUM",
        color=discord.Color.orange()
    )

    if not events:
        embed.add_field(name="⚠️ Info", value="API Forex Factory tidak dapat diakses. Coba !macro lagi.", inline=False)
        embed.set_footer(text="Forex Factory | Groq AI")
        return embed

    for e in events[:10]:
        if e["impact"] == "HIGH":
            icon = "🔴"
        else:
            icon = "🟡"

        markers = ""
        if e["is_today"]:
            markers += " ⬅️ HARI INI"
        if e["is_released"]:
            markers += " ✅"

        if e["is_released"]:
            actual_line = "Actual: " + e["actual"]
        else:
            actual_line = "Belum Rilis"

        value = (
            "⏰ " + e["time_wib"] + "\n"
            "F: " + e["forecast"] + " | P: " + e["previous"] + "\n"
            + actual_line
        )
        embed.add_field(name=icon + " " + e["title"] + markers, value=value, inline=False)

    if ai_text and len(ai_text) > 50:
        chunks = split_text(ai_text, 1024)
        for i, chunk in enumerate(chunks):
            if i == 0:
                label = "🔍 Analisis Dampak"
            else:
                label = "🔍 Analisis (lanjutan)"
            embed.add_field(name=label, value=chunk, inline=False)
    else:
        embed.add_field(name="🔍 Analisis Dampak", value="AI sedang memproses. Coba lagi.", inline=False)

    embed.set_footer(text="Forex Factory | Groq AI")
    return embed


def build_realtime_embed(event, ai_text):
    verdict = "IN-LINE"
    vi = "⚪"
    try:
        f_val = event["forecast"]
        a_val = event["actual"]
        forecast_f = float(f_val) if f_val not in ("N/A", "", "None", None) else None
        actual_f = float(a_val) if a_val not in ("N/A", "", "None", None) else None
        if forecast_f is not None and actual_f is not None:
            diff = abs(actual_f - forecast_f)
            threshold = abs(forecast_f) * 0.05 if forecast_f != 0 else 0.5
            if diff > threshold:
                if actual_f > forecast_f:
                    verdict = "BEAT"
                    vi = "🟢"
                else:
                    verdict = "MISS"
                    vi = "🔴"
    except:
        pass

    embed = discord.Embed(title="⚡ DATA RILIS - " + event["title"], color=discord.Color.orange())
    detail = (
        "⏰ " + event["time_wib"] + "\n"
        "F: " + event["forecast"] + " | P: " + event["previous"] + " | A: " + event["actual"]
    )
    embed.add_field(name="Detail " + vi + " " + verdict, value=detail, inline=False)

    if ai_text:
        chunks = split_text(ai_text, 1024)
        for i, c in enumerate(chunks):
            if i == 0:
                label = "📝 Dampak & Saran"
            else:
                label = "📝 (lanjutan)"
            embed.add_field(name=label, value=c, inline=False)

    embed.set_footer(text="Realtime | Forex Factory | Groq AI")
    return embed


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
            try:
                await ctx.send("❌ Error: " + str(e))
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
            try:
                await ctx.send("❌ Error: " + str(e))
            except:
                pass


async def auto_post():
    while True:
        now = datetime.now(wib)
        target = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_secs = (target - now).total_seconds()
        print("[AUTO] Next post in " + str(round(wait_secs / 3600, 1)) + " hours")
        await asyncio.sleep(wait_secs)

        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            continue

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
            print("[AUTO] Error: " + str(e))


async def realtime_monitor():
    await bot.wait_until_ready()
    print("[RT] Realtime monitor started")
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
                        print("[RT] " + e["title"])
        except Exception as ex:
            print("[RT] Error: " + str(ex))
        await asyncio.sleep(120)


@bot.event
async def on_ready():
    print("Bot online: " + str(bot.user))
    bot.loop.create_task(auto_post())
    bot.loop.create_task(realtime_monitor())


bot.run(DISCORD_TOKEN)
