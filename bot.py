"""
Discord Crypto Research Bot
- Data: CoinMarketCap + CryptoCompare + CoinGecko + CryptoPanic
- AI: Groq API (llama-3.3-70b-versatile)
- Macro: Forex Factory scraping
- Commands: !report, !macro
- Auto-post daily report + macro
"""

import os
import re
import asyncio
import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pytz

load_dotenv()

# ===================== CONFIG =====================

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHAN = int(os.getenv("CHANNEL_ID", "0"))
GROQ = os.getenv("GROQ_API_KEY")
CMC_KEY = os.getenv("CMC_API_KEY", "")
HOUR = int(os.getenv("REPORT_HOUR_WIB", "8"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ===================== HTTP HELPER =====================

async def fetch(session, url, retries=3):
    for i in range(retries):
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15), headers=UA
            ) as r:
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


async def fetch_cmc(session, path):
    """Fetch data from CoinMarketCap API v1."""
    if not CMC_KEY:
        return None
    url = f"https://pro-api.coinmarketcap.com{path}"
    headers = {
        "X-CMC_PRO_API_KEY": CMC_KEY,
        "Accept": "application/json",
    }
    for attempt in range(2):
        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status == 200:
                    return await r.json()
                elif r.status == 429:
                    print("[CMC 429] Rate limited, waiting 30s...")
                    await asyncio.sleep(30)
                else:
                    text = await r.text()
                    print(f"[CMC {r.status}] {text[:200]}")
        except Exception as e:
            print(f"[CMC Retry {attempt+1}] {e}")
            if attempt < 1:
                await asyncio.sleep(3)
    return None


async def ask_groq(session, prompt, max_tokens=1200):
    """Call Groq AI API. Returns string or None."""
    if not GROQ:
        return None
    for attempt in range(2):
        try:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
                headers={
                    "Authorization": f"Bearer {GROQ}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
                elif resp.status == 429:
                    print("[Groq 429] Rate limited, waiting 35s...")
                    await asyncio.sleep(35)
                else:
                    text = await resp.text()
                    print(f"[Groq {resp.status}] {text[:200]}")
        except Exception as e:
            print(f"[Groq Retry {attempt+1}] {e}")
            if attempt < 1:
                await asyncio.sleep(5)
    return None


# ===================== DATA SOURCES =====================

async def get_cmc_listings(session):
    """Get top 10 coins from CMC with full market data."""
    data = await fetch_cmc(session, "/v1/cryptocurrency/listings/latest?limit=10&convert=USD")
    if not data or "data" not in data:
        return None
    coins = []
    for c in data["data"][:10]:
        quote = c.get("quote", {}).get("USD", {})
        coins.append({
            "rank": c.get("cmc_rank", 0),
            "name": c.get("name", ""),
            "symbol": c.get("symbol", ""),
            "price": quote.get("price", 0),
            "change_1h": quote.get("percent_change_1h", 0),
            "change_24h": quote.get("percent_change_24h", 0),
            "change_7d": quote.get("percent_change_7d", 0),
            "volume_24h": quote.get("volume_24h", 0),
            "mcap": quote.get("market_cap", 0),
            "dominance": quote.get("market_cap_dominance", 0),
        })
    return coins


async def get_cmc_global(session):
    """Get global market data from CMC."""
    data = await fetch_cmc(session, "/v1/global-metrics/quotes/latest")
    if not data or "data" not in data:
        return None
    d = data["data"]
    q = d.get("quote", {}).get("USD", {})
    return {
        "total_market_cap": q.get("total_market_cap", 0),
        "total_volume_24h": q.get("total_volume_24h", 0),
        "btc_dominance": d.get("btc_dominance", 0),
        "eth_dominance": d.get("eth_dominance", 0),
        "active_cryptos": d.get("active_cryptocurrencies", 0),
        "market_cap_change_24h": q.get("total_market_cap_yesterday_percentage_change", 0),
    }


async def get_cmc_trending(session):
    """Get latest trending/trending coins from CMC."""
    data = await fetch_cmc(
        session,
        "/v1/cryptocurrency/listings/latest?limit=5&sort=volume_24h_desc&convert=USD"
    )
    if not data or "data" not in data:
        return None
    result = []
    for c in data["data"][:5]:
        quote = c.get("quote", {}).get("USD", {})
        result.append({
            "name": c.get("name", ""),
            "symbol": c.get("symbol", ""),
            "price": quote.get("price", 0),
            "change_24h": quote.get("percent_change_24h", 0),
            "volume_24h": quote.get("volume_24h", 0),
        })
    return result


async def get_btc_data(session):
    """Get BTC detailed price data from CryptoCompare with CoinCap fallback."""
    url = "https://min-api.cryptocompare.com/data/pricemultifull?fsyms=BTC&tsyms=USDT"
    data = await fetch(session, url)
    if data and "RAW" in data:
        try:
            d = data["RAW"]["BTC"]["USDT"]
            return {
                "price": d.get("PRICE", 0),
                "change_24h": d.get("CHANGEPCT24HOUR", 0),
                "high_24h": d.get("HIGH24HOUR", 0),
                "low_24h": d.get("LOW24HOUR", 0),
                "volume_24h": d.get("TOTALVOLUME24HTO", 0),
                "mcap": d.get("MKTCAP", 0),
            }
        except (KeyError, TypeError):
            pass
    # Fallback: CoinCap
    url2 = "https://api.coincap.io/v2/assets/bitcoin"
    data2 = await fetch(session, url2)
    if data2 and "data" in data2:
        d = data2["data"]
        return {
            "price": float(d.get("priceUsd", 0)),
            "change_24h": float(d.get("changePercent24Hr", 0)),
            "high_24h": 0,
            "low_24h": 0,
            "volume_24h": float(d.get("volumeUsd24Hr", 0)),
            "mcap": float(d.get("marketCapUsd", 0)),
        }
    return None


async def get_dxy_data(session):
    """Calculate DXY from exchange rates."""
    url = "https://api.exchangerate-api.com/v4/latest/USD"
    data = await fetch(session, url)
    if not data or "rates" not in data:
        return None
    r = data["rates"]
    try:
        eurusd = 1.0 / r.get("EUR", 1)
        gbpusd = 1.0 / r.get("GBP", 1)
        usdjpy = r.get("JPY", 1)
        usdcad = r.get("CAD", 1)
        usdsek = r.get("SEK", 1)
        usdchf = r.get("CHF", 1)
        dxy = (
            50.14348112
            * (eurusd ** -0.576)
            * (usdjpy ** 0.136)
            * (gbpusd ** -0.119)
            * (usdcad ** 0.091)
            * (usdsek ** 0.042)
            * (usdchf ** 0.036)
        )
        if 90 < dxy < 120:
            return {"dxy": dxy}
    except Exception:
        pass
    return None


async def get_fear_greed(session):
    """Get Fear & Greed Index."""
    url = "https://api.alternative.me/fng/?limit=1"
    data = await fetch(session, url)
    if data and "data" in data and len(data["data"]) > 0:
        d = data["data"][0]
        return {"value": int(d.get("value", 0)), "label": d.get("value_classification", "N/A")}
    return None


async def get_global_data(session):
    """Get global market data from CoinGecko with CoinCap fallback."""
    url = "https://api.coingecko.com/api/v3/global"
    data = await fetch(session, url)
    if data and "data" in data:
        d = data["data"]
        mcap = d.get("total_market_cap", {}).get("usd", 0)
        btc_d = d.get("market_cap_percentage", {}).get("btc", 0)
        if mcap > 0 and 30 < btc_d < 80:
            return {
                "total_market_cap": mcap,
                "volume_24h": d.get("total_volume", {}).get("usd", 0),
                "btc_dominance": btc_d,
                "eth_dominance": d.get("market_cap_percentage", {}).get("eth", 0),
                "active_cryptos": d.get("active_cryptocurrencies", 0),
                "market_change_24h": d.get("market_cap_change_percentage_24h_usd", 0),
            }
    # Fallback: CoinCap
    url2 = "https://api.coincap.io/v2/global"
    data2 = await fetch(session, url2)
    if data2 and "data" in data2:
        d = data2["data"]
        mcap = float(d.get("marketCapUsd", 0))
        btc_d = float(d.get("btcDominance", 0))
        if mcap > 0 and 30 < btc_d < 80:
            return {
                "total_market_cap": mcap,
                "volume_24h": float(d.get("volume24hUsd", 0)),
                "btc_dominance": btc_d,
                "eth_dominance": float(d.get("ethDominance", 0)),
                "active_cryptos": int(d.get("assets", 0)),
                "market_change_24h": float(d.get("marketCapChangePercentage24Hr", 0)),
            }
    return None


async def get_news(session):
    """Get crypto news from CryptoPanic with CryptoCompare fallback."""
    url = "https://cryptopanic.com/api/free/v1/posts/?public=true&filter=rising&currencies=BTC,ETH&limit=5"
    data = await fetch(session, url)
    if data and "results" in data and len(data["results"]) > 0:
        return [
            {"title": i.get("title", ""), "url": i.get("url", ""), "source": i.get("source", {}).get("title", "")}
            for i in data["results"][:5]
        ]
    # Fallback: CryptoCompare
    url2 = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest&limit=5"
    data2 = await fetch(session, url2)
    if data2 and "Data" in data2 and len(data2["Data"]) > 0:
        return [
            {"title": i.get("title", ""), "url": i.get("url", ""), "source": i.get("source", "")}
            for i in data2["Data"][:5]
        ]
    return None


# ===================== FOREX FACTORY CALENDAR =====================

async def get_ff_calendar(session):
    """Scrape Forex Factory economic calendar for today's high-impact events."""
    url = "https://www.forexfactory.com/calendar"
    extra_headers = {
        "User-Agent": UA["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.forexfactory.com/",
    }
    for attempt in range(2):
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=20), headers=extra_headers
            ) as resp:
                if resp.status != 200:
                    print(f"[FF HTTP {resp.status}]")
                    continue
                html = await resp.text()
                events = _parse_ff_calendar(html)
                if events:
                    print(f"[FF] Found {len(events)} high-impact events")
                    return events
                else:
                    print("[FF] No events parsed from HTML")
        except Exception as e:
            print(f"[FF Retry {attempt+1}] {e}")
            await asyncio.sleep(2)
    return None


def _parse_ff_calendar(html):
    """Parse Forex Factory calendar HTML to extract high-impact events."""
    events = []
    # Find today section
    markers = [
        'data-flexbox="today"', ">Today<", ">Hari Ini<", 'class="calendar__day"'
    ]
    start = 0
    for m in markers:
        idx = html.find(m)
        if idx > 0:
            start = idx
            break
    chunk = html[start : start + 80000]
    # Find event rows
    rows = re.findall(
        r'<tr[^>]*?data-event-id="(\d+)"[^>]*?>([\s\S]*?)</tr>', chunk
    )
    if not rows:
        rows = re.findall(
            r'data-event-id="(\d+)"[^>]*?>([\s\S]{200,3000}?)</(?:tr|div)>', chunk
        )
    for eid, row in rows:
        # Check high impact
        if not re.search(
            r'impact[^"]*?high|impact-icon[^"]*?high|impact--3|'
            r'calendar__impact--high|fa-bolt|icon--ff-high',
            row,
            re.I,
        ):
            continue
        # Time
        t = re.search(
            r'(?:calendar__time|cal-time)[^>]*?><[^>]*?><[^>]*?>([^<]+)', row, re.I
        )
        if not t:
            t = re.search(r'time.*?<span[^>]*?>([^<]+)', row, re.I)
        # Currency
        c = re.search(r'title="([A-Z]{3})"', row)
        # Title
        ti = re.search(
            r'(?:event-title|event__title|calendar__event)[^>]*?><[^>]*?>([^<]+(?:</(?:span|a)>)[^<]*)',
            row,
            re.I,
        )
        if not ti:
            ti = re.search(r'event[^>]*?><[^>]*?><[^>]*?>([^<]+)', row, re.I)
        # Forecast
        f = re.search(
            r'forecast[^>]*?><[^>]*?><[^>]*?>([^<]*)', row, re.I
        )
        if not f:
            f = re.search(r'forecast[^>]*?>([^<]+)', row, re.I)
        # Previous
        p = re.search(
            r'previous[^>]*?><[^>]*?><[^>]*?>([^<]*)', row, re.I
        )
        if not p:
            p = re.search(r'previous[^>]*?>([^<]+)', row, re.I)
        # Actual
        a = re.search(
            r'actual[^>]*?><[^>]*?><[^>]*?>([^<]*)', row, re.I
        )
        if not a:
            a = re.search(r'actual[^>]*?>([^<]+)', row, re.I)
        title = re.sub(r"<[^>]+>", "", ti.group(1).strip()) if ti else ""
        if title and c:
            events.append({
                "time": t.group(1).strip() if t else "",
                "currency": c.group(1),
                "title": title,
                "forecast": f.group(1).strip() if f else "-",
                "previous": p.group(1).strip() if p else "-",
                "actual": a.group(1).strip() if a else "-",
            })
    return events if events else None


# ===================== AI ANALYSIS =====================

async def get_ai_analysis(session, btc, dxy, gdata, fg, news, cmc_coins, cmc_global):
    """Groq AI analyzes the full crypto market."""
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

    # CMC top coins summary
    cmc_str = ""
    if cmc_coins:
        for c in cmc_coins[:5]:
            cmc_str += f"- {c['name']} ({c['symbol']}): ${c['price']:,.4f} ({c['change_24h']:+.2f}%) Vol: ${c['volume_24h']:,.0f}\n"

    # CMC global (if available, overrides CoinGecko)
    if cmc_global:
        mcap = f"${cmc_global['total_market_cap']:,.0f}"
        vol = f"${cmc_global['total_volume_24h']:,.0f}"
        btc_dom = f"{cmc_global['btc_dominance']:.1f}%"
        eth_dom = f"{cmc_global['eth_dominance']:.1f}%"
        mc_ch = f"{cmc_global['market_cap_change_24h']:+.2f}%"

    # News
    news_str = ""
    if news:
        for n in news[:3]:
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

TOP 5 COINS (CoinMarketCap):
{cmc_str if cmc_str else "Tidak tersedia"}

BERITA:
{news_str if news_str else "Tidak tersedia"}

Buat analisis dalam 3 bagian:
1. Ringkasan Pasar - rangkum data harga, volume, market cap, DXY, top coins
2. Analisis Teknikal BTC/USDT - analisis high/low, dominasi BTC, fear & greed, dampak DXY
3. Sentimen & Outlook - berdasarkan berita dan fear & greed index

Jawab padat dalam Bahasa Indonesia, maks 400 kata."""

    return await ask_groq(session, prompt, max_tokens=1500)


async def get_macro_analysis(session, events):
    """Groq AI analyzes macro event impact on USD and crypto."""
    today = datetime.now(pytz.timezone("Asia/Jakarta")).strftime("%d %B %Y")
    if events:
        ev_str = "\n".join([
            f"- {e['time']} {e['currency']} {e['title']} "
            f"(Forecast: {e['forecast']}, Previous: {e['previous']}, Actual: {e['actual']})"
            for e in events[:8]
        ])
        prompt = f"""Kamu analis forex & crypto profesional. Analisis event makroekonomi hari ini ({today}) dalam Bahasa Indonesia.

EVENT MAKRO HARI INI:
{ev_str}

Untuk setiap event, analisis:
1. Dampak terhadap DXY (Dollar Index)
2. Dampak terhadap BTC/USDT (crypto market)
3. Pasangan mata uang forex yang terdampak

Format: point-by-point per event. Jawab padat, maks 300 kata."""
    else:
        prompt = f"""Kamu analis forex & crypto profesional. Hari ini tanggal {today}.
Berikan outlook kondisi makroekonomi yang mempengaruhi pasar crypto hari ini dalam Bahasa Indonesia.

Cakup:
- Kondisi DXY dan kebijakan The Fed
- Event ekonomi penting minggu ini (CPI, NFP, GDP, PPI, rate decision, dll)
- Dampak terhadap BTC/USDT dan pasar crypto secara keseluruhan
- Sentimen pasar global

Jawab padat, maks 250 kata."""

    return await ask_groq(session, prompt, max_tokens=1000)


# ===================== FORMAT HELPERS =====================

def fmt_num(n):
    if not n or n <= 0:
        return "N/A"
    return f"${n:,.0f}"


def fmt_price(n):
    if not n or n <= 0:
        return "N/A"
    if n >= 1:
        return f"${n:,.2f}"
    return f"${n:,.4f}"


def fg_emoji(v):
    if not v:
        return "N/A"
    if v <= 20:
        return f"{v} Extreme Fear"
    elif v <= 40:
        return f"{v} Fear"
    elif v <= 60:
        return f"{v} Neutral"
    elif v <= 80:
        return f"{v} Greed"
    else:
        return f"{v} Extreme Greed"


def chg_emoji(v):
    if v is None:
        return "N/A"
    if v >= 0:
        return f"+{v:.2f}%"
    return f"{v:.2f}%"


CURRENCY_FLAGS = {
    "USD": "US", "EUR": "EU", "GBP": "GB", "JPY": "JP",
    "AUD": "AU", "CAD": "CA", "CHF": "CH", "NZD": "NZ",
    "CNY": "CN", "KRW": "KR", "SGD": "SG", "HKD": "HK",
}


# ===================== EMBED BUILDERS =====================

def build_report_embed(btc, dxy, gdata, fg, news, analysis, cmc_coins, cmc_global):
    """Build the main crypto report embed."""
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    embed = discord.Embed(
        title=f"📊 Laporan Pasar Crypto Harian — {now.strftime('%d %B %Y')}",
        color=discord.Color.orange(),
        timestamp=now,
    )

    # --- DATA PASAR ---
    dp = "Data tidak tersedia"
    if btc:
        ch = btc.get("change_24h", 0) or 0
        arrow = "↑" if ch >= 0 else "↓"
        dp = f"**BTC/USDT:** ${btc['price']:,.2f} ({arrow} {ch:+.2f}% 24h)\n"
        dp += f"**High/Low:** ${btc['high_24h']:,.2f} / ${btc['low_24h']:,.2f}\n"
        dp += f"**Volume 24h:** {fmt_num(btc.get('volume_24h'))}\n"
        dp += f"**DXY Index:** {dxy['dxy']:.2f}" if dxy else "**DXY Index:** N/A"
    embed.add_field(name="📈 Data Pasar", value=dp, inline=False)

    # --- TOP 10 COINS (CMC) ---
    if cmc_coins and len(cmc_coins) > 0:
        coins_text = ""
        for c in cmc_coins:
            name = c["name"]
            sym = c["symbol"]
            price = fmt_price(c["price"])
            ch24 = chg_emoji(c["change_24h"])
            coins_text += f"**#{c['rank']} {name}** ({sym}) — {price} ({ch24})\n"
        embed.add_field(name="🏆 Top 10 Coins (CoinMarketCap)", value=coins_text, inline=False)
    else:
        embed.add_field(name="🏆 Top 10 Coins", value="Data CMC tidak tersedia", inline=False)

    # --- MARKET GLOBAL ---
    mg = "Data tidak tersedia"
    source = "CoinGecko"
    if gdata:
        # Use CMC global data if available
        if cmc_global:
            source = "CoinMarketCap"
            total_mcap = cmc_global["total_market_cap"]
            total_vol = cmc_global["total_volume_24h"]
            btc_dom_val = cmc_global["btc_dominance"]
            eth_dom_val = cmc_global["eth_dominance"]
            mc_change = cmc_global["market_cap_change_24h"]
        else:
            total_mcap = gdata.get("total_market_cap", 0)
            total_vol = gdata.get("volume_24h", 0)
            btc_dom_val = gdata.get("btc_dominance", 0)
            eth_dom_val = gdata.get("eth_dominance", 0)
            mc_change = gdata.get("market_change_24h", 0)

        fg_s = fg_emoji(fg["value"]) if fg else "N/A"
        mc = mc_change or 0
        me = "↑" if mc >= 0 else "↓"
        mg = f"**Market Cap:** {fmt_num(total_mcap)}\n"
        mg += f"**Volume Global:** {fmt_num(total_vol)}\n"
        mg += f"**BTC Dom:** {btc_dom_val:.1f}% | **ETH Dom:** {eth_dom_val:.1f}%\n"
        mg += f"**Fear & Greed:** {fg_s}\n"
        mg += f"**Market 24h:** {me} {mc:+.2f}%\n"
        if mc > 0:
            mg += "**Trending:** Bullish"
        elif mc < 0:
            mg += "**Trending:** Bearish"
        else:
            mg += "**Trending:** Sideways"
        mg += f"\n*Source: {source}*"
    embed.add_field(name="🌐 Market Global", value=mg, inline=False)

    # --- BERITA ---
    if news and len(news) > 0:
        lines = [
            f"**{i+1}.** [{n['title']}]({n['url']}) — *{n.get('source', '')}*"
            for i, n in enumerate(news)
        ]
        embed.add_field(name="📰 Berita Crypto Terkini", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📰 Berita Crypto Terkini", value="Tidak ada berita tersedia saat ini.", inline=False)

    # --- AI ANALYSIS ---
    if analysis:
        lines = analysis.strip().split("\n")
        sections = []
        cur_sec = []
        cur_title = None
        for line in lines:
            s = line.strip()
            if not s:
                continue
            is_t = (
                s.startswith("1.") or s.startswith("2.") or s.startswith("3.")
                or s.startswith("Ringkasan") or s.startswith("Analisis")
                or s.startswith("Sentimen")
            )
            if is_t and cur_title:
                sections.append((cur_title, "\n".join(cur_sec)))
                cur_sec = []
            if is_t:
                c = s.lstrip("0123456789.").strip()
                if not c.startswith("**"):
                    c = f"**{c}**"
                if "Ringkasan" in c or "Pasar" in c:
                    cur_title = f"📈 {c}"
                elif "Teknikal" in c:
                    cur_title = f"🔍 {c}"
                elif "Sentimen" in c or "Outlook" in c:
                    cur_title = f"🎯 {c}"
                else:
                    cur_title = c
            else:
                cur_sec.append(s)
        if cur_title and cur_sec:
            sections.append((cur_title, "\n".join(cur_sec)))
        if not sections and analysis.strip():
            sections.append(("🤖 Analisis AI", analysis.strip()))
        for title, content in sections:
            if len(content) > 1024:
                content = content[:1024].rsplit(" ", 1)[0] + "..."
            embed.add_field(name=title, value=content, inline=False)

    embed.set_footer(
        text="⚠️ Not Financial Advice | DYOR\n"
        "Groq AI | CoinMarketCap | CryptoCompare | CoinGecko | CryptoPanic | Alternative.me"
    )
    return embed


def build_macro_embed(events, analysis):
    """Build the macro economic calendar embed."""
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    embed = discord.Embed(
        title=f"📅 Economic Calendar — {now.strftime('%d %B %Y')}",
        description="Event makroekonomi berdampak tinggi hari ini",
        color=discord.Color.blue(),
        timestamp=now,
    )
    if events:
        ev_text = ""
        for e in events[:10]:
            flag = CURRENCY_FLAGS.get(e["currency"], "🌐")
            actual_str = ""
            if e["actual"] not in ["-", "", "None"]:
                actual_str = f" | **Aktual:** {e['actual']}"
            ev_text += f"**{flag} {e['currency']}** | {e['time']}\n"
            ev_text += f"**{e['title']}**\n"
            ev_text += f"Forecast: {e['forecast']} | Previous: {e['previous']}{actual_str}\n\n"
        embed.add_field(name="🔥 High Impact Events", value=ev_text.strip(), inline=False)
    else:
        embed.add_field(
            name="ℹ️",
            value="Tidak ada event high-impact terdeteksi untuk hari ini.",
            inline=False,
        )
    if analysis:
        if len(analysis) > 1024:
            analysis = analysis[:1024].rsplit(" ", 1)[0] + "..."
        embed.add_field(name="💱 Analisis Dampak Makro (AI)", value=analysis, inline=False)
    embed.set_footer(text="⚠️ Not Financial Advice | DYOR\nData: Forex Factory | Analysis: Groq AI")
    return embed


# ===================== REPORT GENERATORS =====================

async def generate_report():
    """Generate the full crypto market report."""
    async with aiohttp.ClientSession() as session:
        # Fetch all data sources in parallel
        results = await asyncio.gather(
            get_btc_data(session),
            get_dxy_data(session),
            get_global_data(session),
            get_fear_greed(session),
            get_news(session),
            get_cmc_listings(session),
            get_cmc_global(session),
            return_exceptions=True,
        )
        btc, dxy, gdata, fg, news, cmc_coins, cmc_global = [
            None if isinstance(r, Exception) else r for r in results
        ]
        for name, r in zip(
            ["BTC", "DXY", "Global", "FG", "News", "CMC Listings", "CMC Global"],
            results,
        ):
            if isinstance(r, Exception):
                print(f"[{name} Error] {r}")

        # Log CMC status
        if cmc_coins:
            print(f"[CMC] {len(cmc_coins)} coins loaded")
        else:
            print("[CMC] Listings unavailable, using fallback data")
        if cmc_global:
            print(f"[CMC] Global data loaded — BTC Dom: {cmc_global['btc_dominance']:.1f}%")
        else:
            print("[CMC] Global unavailable, using CoinGecko fallback")

        # AI analysis
        analysis = await get_ai_analysis(
            session, btc, dxy, gdata, fg, news, cmc_coins, cmc_global
        )
        if analysis:
            print("[AI] Analysis generated successfully")
        else:
            print("[AI] Analysis failed")

        return build_report_embed(
            btc, dxy, gdata, fg, news, analysis, cmc_coins, cmc_global
        )


async def generate_macro():
    """Generate the macro economic calendar report."""
    async with aiohttp.ClientSession() as session:
        events = await get_ff_calendar(session)
        if not events:
            print("[Macro] No FF events, using AI-only analysis")
        analysis = await get_macro_analysis(session, events)
        if analysis:
            print("[Macro AI] Analysis generated successfully")
        else:
            print("[Macro AI] Analysis failed")
        return build_macro_embed(events, analysis)


# ===================== BOT COMMANDS =====================

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    print(f"📡 Channel ID: {CHAN}")
    print(f"🤖 Groq API: {'SET' if GROQ else 'NOT SET'}")
    print(f"📊 CMC API: {'SET' if CMC_KEY else 'NOT SET'}")
    print(f"⏰ Auto-post at {HOUR}:00 WIB")
    bot.loop.create_task(auto_post_loop())


@bot.command()
async def report(ctx):
    """Generate crypto market report."""
    msg = await ctx.send("⏳ Generating report...")
    try:
        embed = await generate_report()
        await msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await msg.edit(content=f"❌ Error generating report: {e}")


@bot.command()
async def macro(ctx):
    """Generate macro economic calendar report."""
    msg = await ctx.send("⏳ Fetching economic calendar...")
    try:
        embed = await generate_macro()
        await msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await msg.edit(content=f"❌ Error: {e}")


# ===================== AUTO POST =====================

async def auto_post_loop():
    """Auto-post daily report and macro at scheduled hour (WIB)."""
    wib = pytz.timezone("Asia/Jakarta")
    await bot.wait_until_ready()
    while True:
        now = datetime.now(wib)
        target = now.replace(hour=HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        print(f"⏰ Next auto-post in {wait/3600:.1f} hours ({target.strftime('%Y-%m-%d %H:%M WIB')})")
        await asyncio.sleep(wait)
        channel = bot.get_channel(CHAN)
        if channel:
            try:
                print("📤 Sending daily auto-post...")
                embed1 = await generate_report()
                await channel.send(embed=embed1)
                await asyncio.sleep(5)
                embed2 = await generate_macro()
                await channel.send(embed=embed2)
                print(f"✅ Auto-post sent ({datetime.now(wib).strftime('%H:%M WIB')})")
            except Exception as e:
                print(f"❌ Auto-post failed: {e}")


# ===================== MAIN =====================

if __name__ == "__main__":
    print("🚀 Starting Crypto Research Bot...")
    print(f"   Discord Token: {'SET' if TOKEN else 'NOT SET'}")
    print(f"   Groq API Key: {'SET' if GROQ else 'NOT SET'}")
    print(f"   CMC API Key:  {'SET' if CMC_KEY else 'NOT SET'}")
    print(f"   Channel ID:   {CHAN}")
    print(f"   Report Hour:  {HOUR}:00 WIB")
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ DISCORD_BOT_TOKEN not set!")
