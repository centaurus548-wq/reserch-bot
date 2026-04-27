import os
import discord
from discord.ext import commands
import aiohttp
import asyncio
import json
import math
from datetime import datetime, timezone, timedelta

# ==================== CONFIG ====================
from dotenv import load_dotenv
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
REPORT_HOUR_WIB = int(os.getenv("REPORT_HOUR_WIB", "8"))  # Default 08:00 WIB

# WIB = UTC+7
WIB_OFFSET = timedelta(hours=7)

# ==================== BOT SETUP ====================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ==================== HELPER ====================
async def safe_get(session, url, retries=2, **kwargs):
    """HTTP GET with auto-retry"""
    for attempt in range(retries + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15), **kwargs) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 429:
                    wait = 2 ** (attempt + 1)
                    print(f"⚠️ Rate limited on {url}, waiting {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    return None
        except Exception as e:
            if attempt < retries:
                await asyncio.sleep(2)
            else:
                print(f"❌ Failed after {retries+1} retries: {url} - {e}")
                return None
    return None


def fmt_num(val, prefix="$"):
    """Format number with commas"""
    if val is None or val == "N/A":
        return "N/A"
    try:
        v = float(val)
        if v >= 1e12:
            return f"{prefix}{v/1e12:.2f}T"
        elif v >= 1e9:
            return f"{prefix}{v/1e9:.2f}B"
        elif v >= 1e6:
            return f"{prefix}{v/1e6:.2f}M"
        else:
            return f"{prefix}{v:,.0f}"
    except (ValueError, TypeError):
        return "N/A"


# ==================== API FUNCTIONS ====================

async def fetch_btc_usdt():
    """Fetch BTC/USDT data from Binance"""
    async with aiohttp.ClientSession() as session:
        # 24h ticker
        tick = await safe_get(session,
            "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT")
        if not tick:
            return None

        # Order book top 5
        ob = await safe_get(session,
            "https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=5")
        if not ob:
            ob = {"bids": [["0", "0"]], "asks": [["0", "0"]]}

        # 7-day klines
        klines_raw = await safe_get(session,
            "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=7")

        klines = []
        if klines_raw:
            for k in klines_raw:
                klines.append({
                    "date": datetime.utcfromtimestamp(k[0] / 1000).strftime("%m/%d"),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "vol": float(k[5]),
                })

        return {
            "price": float(tick["lastPrice"]),
            "change": float(tick["priceChange"]),
            "change_pct": float(tick["priceChangePercent"]),
            "high": float(tick["highPrice"]),
            "low": float(tick["lowPrice"]),
            "vol_btc": float(tick["volume"]),
            "vol_usdt": float(tick["quoteAssetVolume"]),
            "trades": int(tick["count"]),
            "bid": float(ob["bids"][0][0]),
            "ask": float(ob["asks"][0][0]),
            "klines": klines,
        }


async def fetch_market_data():
    """Fetch global market data from CoinCap (FREE, no API key needed)"""
    async with aiohttp.ClientSession() as session:
        result = {
            "total_mc": None,
            "total_vol": None,
            "mc_change_pct": None,
            "btc_dom": None,
            "eth_dom": None,
            "active": None,
            "trending": [],
            "fng": "N/A",
            "fng_label": "N/A",
        }

        # === CoinCap Global (gratis, tanpa API key) ===
        cg = await safe_get(session, "https://api.coincap.io/v2/global")
        if cg and cg.get("data"):
            d = cg["data"]
            result["total_mc"] = d.get("marketCapUsd")
            result["total_vol"] = d.get("volume24hUsd")
            result["btc_dom"] = d.get("btcDominance")
            result["eth_dom"] = d.get("ethDominance")
            result["active"] = d.get("cryptocurrencies")
            if d.get("marketCapChangePercentage24hUsd") is not None:
                result["mc_change_pct"] = d["marketCapChangePercentage24hUsd"]

        # === Trending dari CoinCap (top gainers) ===
        tg = await safe_get(session,
            "https://api.coincap.io/v2/assets?limit=10&sort=changePercent24h&order=desc")
        if tg and tg.get("data"):
            result["trending"] = [c["name"] for c in tg["data"][:10]]

        # === Fear & Greed Index (alternative.me, gratis) ===
        fg = await safe_get(session, "https://api.alternative.me/fng/?limit=1")
        if fg and fg.get("data") and len(fg["data"]) > 0:
            result["fng"] = fg["data"][0]["value"]
            result["fng_label"] = fg["data"][0]["value_classification"]

        return result


async def fetch_dxy():
    """Fetch DXY (US Dollar Index) from exchange rates"""
    async with aiohttp.ClientSession() as session:
        data = await safe_get(session, "https://open.er-api.com/v6/latest/USD")
        if not data or "rates" not in data:
            return {"dxy": None, "eur_usd": None, "usd_jpy": None, "gbp_usd": None}

        r = data["rates"]

        eurusd = 1 / r.get("EUR", 1)
        usdjpy = r.get("JPY", 1)
        gbpusd = 1 / r.get("GBP", 1)
        usdcad = r.get("CAD", 1)
        usdsek = r.get("SEK", 1)
        usdchf = r.get("CHF", 1)

        # DXY formula (weighted geometric average)
        dxy = (
            50.14348112
            * (eurusd ** -0.576)
            * (usdjpy ** 0.136)
            * (gbpusd ** -0.119)
            * (usdcad ** 0.091)
            * (usdsek ** 0.042)
            * (usdchf ** 0.036)
        )

        return {
            "dxy": round(dxy, 2),
            "eur_usd": round(eurusd, 4),
            "usd_jpy": round(usdjpy, 2),
            "gbp_usd": round(gbpusd, 4),
        }


async def fetch_crypto_news():
    """Fetch crypto news - CryptoCompare (primary) + CryptoPanic (fallback)"""
    async with aiohttp.ClientSession() as session:

        # === CryptoCompare News (gratis) ===
        try:
            cc = await safe_get(session,
                "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=popular&limit=8")
            if cc and cc.get("Data"):
                items = []
                for a in cc["Data"][:8]:
                    items.append({
                        "title": a.get("title", ""),
                        "url": a.get("url", ""),
                        "source": a.get("source", ""),
                        "date": datetime.utcfromtimestamp(a.get("published_on", 0)).strftime("%Y-%m-%d"),
                    })
                return items
        except Exception as e:
            print(f"⚠️ CryptoCompare news failed: {e}")

        # === Fallback: CryptoPanic (gratis, no key needed) ===
        try:
            cp = await safe_get(session,
                "https://cryptopanic.com/api/v1/posts/?auth_token=&public=true&kind=news&filter=rising&limit=8")
            if cp and cp.get("results"):
                items = []
                for a in cp["results"][:8]:
                    items.append({
                        "title": a.get("title", ""),
                        "url": a.get("url", ""),
                        "source": a.get("source", {}).get("title", ""),
                        "date": a.get("published_at", "")[:10],
                    })
                return items
        except Exception as e:
            print(f"⚠️ CryptoPanic news failed: {e}")

        return []


# ==================== GROQ AI ====================

async def groq_analyze(btc, market, dxy, news):
    """Send collected data to Groq for AI analysis"""
    if not btc:
        return "Maaf, gagal mengambil data BTC dari Binance."

    klines_str = ""
    for k in btc.get("klines", []):
        klines_str += (
            f"  {k['date']}: O={k['open']:,.0f} H={k['high']:,.0f} "
            f"L={k['low']:,.0f} C={k['close']:,.0f}\n"
        )

    news_str = ""
    for n in news[:6]:
        news_str += f"- [{n.get('source','')}] {n['title']}\n"

    mc_str = fmt_num(market.get("total_mc"))
    vol_str = fmt_num(market.get("total_vol"))
    mc_chg = market.get("mc_change_pct")
    mc_chg_str = f"{mc_chg:+.2f}%" if mc_chg is not None else "N/A"

    prompt = f"""Kamu adalah analis crypto profesional. Buat laporan pasar harian berdasarkan data real-time berikut. Tulis dalam **Bahasa Indonesia** yang natural dan mudah dipahami.

=== DATA BTC/USDT (Binance) ===
Harga Sekarang: ${btc['price']:,.2f}
Perubahan 24h: {btc['change_pct']:+.2f}% ({btc['change']:+,.2f})
High 24h: ${btc['high']:,.2f}
Low 24h: ${btc['low']:,.2f}
Volume 24h: ${btc['vol_usdt']:,.0f} USDT
Trades 24h: {btc['trades']:,}
Bid: ${btc['bid']:,.2f} | Ask: ${btc['ask']:,.2f}
Spread: ${btc['ask'] - btc['bid']:,.2f}

7 Hari Terakhir:
{klines_str}

=== DATA PASAR GLOBAL (CoinCap) ===
Total Market Cap: {mc_str}
Total Volume 24h: {vol_str}
Perubahan MC 24h: {mc_chg_str}
BTC Dominance: {market.get('btc_dom', 'N/A')}%
ETH Dominance: {market.get('eth_dom', 'N/A')}%
Aktif: {market.get('active', 'N/A')} coins
Fear & Greed Index: {market.get('fng', 'N/A')} ({market.get('fng_label', 'N/A')})
Trending: {', '.join(market.get('trending', [])[:7]) if market.get('trending') else 'N/A'}

=== DXY (US DOLLAR INDEX) ===
DXY: {dxy.get('dxy', 'N/A')}
EUR/USD: {dxy.get('eur_usd', 'N/A')}
USD/JPY: {dxy.get('usd_jpy', 'N/A')}
GBP/USD: {dxy.get('gbp_usd', 'N/A')}

=== BERITA CRYPTO TERKINI ===
{news_str if news_str else 'Tidak ada data berita tersedia.'}

Buat laporan dengan struktur berikut:
1. RINGKASAN PASAR - overview singkat kondisi market
2. ANALISIS BTC/USDT - harga, trend, support/resistance berdasarkan data 7 hari, volume analysis
3. DXY & KORELASI MAKRO - analisis DXY dan dampaknya ke crypto
4. MARKET GLOBAL & ALTCOIN - dominasi, fear & greed, trending coins
5. BERITA TERKINI - rangkuman berita penting
6. OUTLOOK & LEVEL PENTING - prediksi dan level yang harus diwaspadai

PENTING:
- Gunakan angka spesifik dari data di atas
- Sebutkan level support dan resistance yang realistis
- Jelaskan korelasi DXY dengan BTC
- Gunakan emoji agar mudah dibaca
- Maksimal 800 kata"""

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 1200,
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            data = await resp.json()
            if "choices" in data:
                return data["choices"][0]["message"]["content"]
            return f"Error dari Groq: {data}"


# ==================== EMBED BUILDER ====================

def parse_sections(text):
    """Parse AI analysis into sections with their titles"""
    sections = []
    current_title = None
    current_lines = []

    for line in text.split("\n"):
        # Detect section headers (numbered like "1.", "2.", or emoji-started bold lines)
        stripped = line.strip()
        is_header = False
        if stripped and (stripped[0].isdigit() or stripped.startswith("#") or stripped.startswith("**")):
            # Check if it's a section header (short line, likely a title)
            if len(stripped) < 80 and stripped.endswith(":"):
                is_header = True
            elif stripped[0].isdigit() and ". " in stripped[:5]:
                is_header = True

        if is_header and current_lines:
            sections.append({
                "title": current_title or "Analisis",
                "content": "\n".join(current_lines).strip()
            })
            current_title = stripped.rstrip(":").lstrip("#*0123456789. ")
            current_lines = []
        else:
            if is_header:
                current_title = stripped.rstrip(":").lstrip("#*0123456789. ")
            else:
                current_lines.append(line)

    if current_lines:
        sections.append({
            "title": current_title or "Analisis",
            "content": "\n".join(current_lines).strip()
        })

    return sections


def build_embed(btc, market, dxy, analysis):
    """Build Discord embed message with budget system"""
    now = datetime.now(timezone.utc)

    # Color: orange for neutral
    if btc and btc["change_pct"] >= 0:
        color = discord.Color.orange()
        arrow = "🟢"
    elif btc:
        color = discord.Color.red()
        arrow = "🔴"
    else:
        color = discord.Color.orange()
        arrow = "⚠️"

    embed = discord.Embed(
        title="📊 DAILY CRYPTO MARKET REPORT",
        description=f"**{now.strftime('%d %B %Y')}** | Auto-Generated Report",
        color=color,
        timestamp=now,
    )

    # ====== BTC/USDT ======
    if btc:
        btc_val = (
            f"**${btc['price']:,.2f}** {arrow} {btc['change_pct']:+.2f}%\n"
            f"High: `${btc['high']:,.2f}` | Low: `${btc['low']:,.2f}`\n"
            f"Vol 24h: `{fmt_num(btc['vol_usdt'])}` | Trades: `{btc['trades']:,}`"
        )
    else:
        btc_val = "⚠️ Gagal mengambil data dari Binance"

    embed.add_field(name="🪙 BTC/USDT", value=btc_val, inline=False)

    # ====== DXY ======
    dxy_val = dxy.get("dxy", "N/A")
    if dxy_val is None:
        dxy_val = "N/A"
    embed.add_field(
        name="💹 DXY Index",
        value=(
            f"**DXY: {dxy_val}**\n"
            f"EUR/USD: `{dxy.get('eur_usd') or 'N/A'}` | "
            f"USD/JPY: `{dxy.get('usd_jpy') or 'N/A'}` | "
            f"GBP/USD: `{dxy.get('gbp_usd') or 'N/A'}`"
        ),
        inline=False,
    )

    # ====== Market Overview ======
    mc_str = fmt_num(market.get("total_mc"))
    vol_str = fmt_num(market.get("total_vol"))
    mc_chg = market.get("mc_change_pct")
    mc_chg_str = f"{mc_chg:+.1f}%" if mc_chg is not None else "N/A"
    btc_dom = market.get("btc_dom")
    btc_dom_str = f"{btc_dom:.1f}%" if btc_dom is not None else "N/A"
    eth_dom = market.get("eth_dom")
    eth_dom_str = f"{eth_dom:.1f}%" if eth_dom is not None else "N/A"

    embed.add_field(
        name="📈 Market Overview",
        value=(
            f"Total MC: `{mc_str}` ({mc_chg_str})\n"
            f"BTC Dom: `{btc_dom_str}` | "
            f"ETH Dom: `{eth_dom_str}` | "
            f"Vol 24h: `{vol_str}`\n"
            f"Fear & Greed: **{market.get('fng', 'N/A')}** ({market.get('fng_label', 'N/A')})"
        ),
        inline=False,
    )

    # ====== Trending ======
    trending = market.get("trending", [])
    if trending:
        embed.add_field(
            name="🔥 Trending",
            value=" | ".join(trending[:7]),
            inline=False,
        )

    # ====== News (top 3 with links) ======
    if news_items:
        news_val = ""
        for i, n in enumerate(news_items[:3]):
            title = n.get("title", "")
            url = n.get("url", "")
            if url:
                news_val += f"• [{title}]({url})\n"
            else:
                news_val += f"• {title}\n"
        if news_val:
            embed.add_field(name="📰 Berita", value=news_val[:500], inline=False)

    # ====== AI Analysis (budget: ~3000 chars for analysis) ======
    BUDGET = 3000
    sections = parse_sections(analysis)
    total_used = 0

    for sec in sections:
        remaining = BUDGET - total_used
        if remaining <= 50:
            break
        content = sec["content"][:remaining]
        embed.add_field(name=f"🤖 {sec['title']}", value=content, inline=False)
        total_used += len(sec["title"]) + len(content) + 10

    # ====== Footer ======
    embed.set_footer(text="⚡ Groq AI | Binance | CoinCap | Not Financial Advice")
    embed.set_thumbnail(
        url="https://assets.coingecko.com/coins/images/1/small/bitcoin.png"
    )
    return embed


# Global var for news
news_items = []


# ==================== REPORT GENERATOR ====================

async def generate_report():
    """Fetch all data, analyze, and send to channel"""
    global news_items
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("❌ Channel not found!")
        return None

    msg = await channel.send("🔄 Mengumpulkan data pasar...")

    try:
        # Fetch all data concurrently
        btc, market, dxy, news = await asyncio.gather(
            fetch_btc_usdt(),
            fetch_market_data(),
            fetch_dxy(),
            fetch_crypto_news(),
        )
        news_items = news

        await msg.edit(content="🤖 Menganalisis data dengan Groq AI...")

        # Groq AI analysis
        analysis = await groq_analyze(btc, market, dxy, news)

        # Build embed
        embed = build_embed(btc, market, dxy, analysis)

        await msg.delete()
        await channel.send(embed=embed)
        print(f"✅ Report sent at {datetime.now(timezone.utc)}")
        return True

    except Exception as e:
        try:
            await msg.edit(content=f"❌ Error: `{e}`")
        except:
            pass
        print(f"❌ Error: {e}")
        return False


# ==================== AUTO-POST LOOP ====================

async def auto_post_loop():
    """Auto-post daily report at specified WIB hour using asyncio"""
    while True:
        now_utc = datetime.now(timezone.utc)
        now_wib = now_utc + WIB_OFFSET
        target_h = REPORT_HOUR_WIB
        target_m = 0

        # Calculate seconds until next target time
        target = now_wib.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
        if now_wib >= target:
            target += timedelta(days=1)  # next day

        wait_seconds = (target - now_wib).total_seconds()
        hours_wait = wait_seconds / 3600
        print(f"⏰ Next report in {hours_wait:.1f} hours (at {target_h:02d}:{target_m:02d} WIB)")

        await asyncio.sleep(wait_seconds)
        print("🚀 Triggering scheduled report...")
        await generate_report()


# ==================== BOT EVENTS ====================

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")
    print(f"📡 Channel ID: {CHANNEL_ID}")
    print(f"⏰ Auto-post: {REPORT_HOUR_WIB}:00 WIB daily")
    # Start auto-post loop in background
    bot.loop.create_task(auto_post_loop())


@bot.command()
async def report(ctx):
    """!report - Manual trigger daily report"""
    await ctx.send("⏳ Generating report...")
    await generate_report()


@bot.command()
async def btc(ctx):
    """!btc - Quick BTC/USDT price check"""
    try:
        data = await fetch_btc_usdt()
        if not data:
            await ctx.send("❌ Gagal mengambil data BTC.")
            return
        arrow = "🟢" if data["change_pct"] >= 0 else "🔴"
        embed = discord.Embed(
            title="🪙 BTC/USDT",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Price",
            value=f"**${data['price']:,.2f}** {arrow} {data['change_pct']:+.2f}%",
            inline=False,
        )
        embed.add_field(
            name="Range",
            value=f"H: `${data['high']:,.2f}` | L: `${data['low']:,.2f}`",
            inline=True,
        )
        embed.add_field(
            name="Volume",
            value=f"`{fmt_num(data['vol_usdt'])}`",
            inline=True,
        )
        embed.set_footer(text=f"Binance | {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Error: `{e}`")


@bot.command()
async def dxy(ctx):
    """!dxy - Quick DXY Index check"""
    try:
        data = await fetch_dxy()
        embed = discord.Embed(title="💹 DXY Index", color=discord.Color.gold())
        embed.add_field(
            name="DXY",
            value=f"**{data.get('dxy') or 'N/A'}**",
            inline=False,
        )
        embed.add_field(
            name="Major Pairs",
            value=(
                f"EUR/USD: `{data.get('eur_usd') or 'N/A'}`\n"
                f"USD/JPY: `{data.get('usd_jpy') or 'N/A'}`\n"
                f"GBP/USD: `{data.get('gbp_usd') or 'N/A'}`"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Exchange Rate API | {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Error: `{e}`")


@bot.command()
async def trending(ctx):
    """!trending - Show top gainers"""
    try:
        market = await fetch_market_data()
        names = market.get("trending", [])[:10]
        if not names:
            await ctx.send("❌ Gagal mengambil data trending.")
            return
        embed = discord.Embed(title="🔥 Top Gainers (24h)", color=discord.Color.orange())
        embed.add_field(
            name="Top 10",
            value="\n".join(f"{i+1}. {n}" for i, n in enumerate(names)),
            inline=False,
        )
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Error: `{e}`")


# ==================== RUN ====================

if __name__ == "__main__":
    print("🚀 Starting bot...")
    bot.run(DISCORD_BOT_TOKEN)
