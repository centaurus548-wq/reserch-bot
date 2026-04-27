import os
import asyncio
import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pytz

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
REPORT_HOUR_WIB = int(os.getenv("REPORT_HOUR_WIB", "8"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def fetch_json(session, url, retries=3):
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 429:
                    await asyncio.sleep(2 ** attempt)
                else:
                    print(f"[HTTP {resp.status}] {url}")
                    return None
        except Exception as e:
            print(f"[Retry {attempt+1}] {url}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2)
    return None

async def get_btc_data(session):
    url = "https://min-api.cryptocompare.com/data/pricemultifull?fsyms=BTC&tsyms=USDT"
    data = await fetch_json(session, url)
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
    url2 = "https://api.coincap.io/v2/assets/bitcoin"
    data2 = await fetch_json(session, url2)
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

async def get_global_data(session):
    url = "https://api.coincap.io/v2/global"
    data = await fetch_json(session, url)
    if data and "data" in data:
        d = data["data"]
        return {
            "total_market_cap": float(d.get("marketCapUsd", 0)),
            "volume_24h": float(d.get("volume24hUsd", 0)),
            "btc_dominance": float(d.get("btcDominance", 0)),
            "eth_dominance": float(d.get("ethDominance", 0)),
            "active_cryptos": int(d.get("assets", 0)),
        }
    url2 = "https://min-api.cryptocompare.com/data/pricemultifull?fsyms=BTC,ETH&tsyms=USD"
    data2 = await fetch_json(session, url2)
    if data2 and "RAW" in data2:
        try:
            btc_mcap = float(data2["RAW"]["BTC"]["USD"].get("MKTCAP", 0))
            eth_mcap = float(data2["RAW"]["ETH"]["USD"].get("MKTCAP", 0))
            total = btc_mcap + eth_mcap
            if total > 0:
                return {
                    "total_market_cap": total,
                    "volume_24h": 0,
                    "btc_dominance": (btc_mcap / total) * 100,
                    "eth_dominance": (eth_mcap / total) * 100,
                    "active_cryptos": 0,
                }
        except (KeyError, TypeError, ZeroDivisionError):
            pass
    return None

async def get_dxy_data(session):
    url = "https://api.exchangerate-api.com/v4/latest/USD"
    data = await fetch_json(session, url)
    if data and "rates" in data:
        rates = data["rates"]
        usdjpy = rates.get("JPY", 0)
        usdgbp = rates.get("GBP", 0)
        usdcad = rates.get("CAD", 0)
        usdsek = rates.get("SEK", 0)
        usdchf = rates.get("CHF", 0)
        usdeur = 1.0 / rates.get("EUR", 1)
        if all([usdeur > 0, usdjpy > 0, usdgbp > 0, usdcad > 0, usdsek > 0, usdchf > 0]):
            dxy = (50.14348112 * (usdeur ** 0.576) * (usdjpy ** 0.136) * (usdgbp ** 0.119) * (usdcad ** 0.091) * (usdsek ** 0.042) * (usdchf ** 0.036))
            return {
                "dxy": dxy,
                "eur_usd": rates.get("EUR", 0),
                "usd_jpy": usdjpy,
                "gbp_usd": rates.get("GBP", 0),
                "aud_usd": rates.get("AUD", 0),
            }
    return None

async def get_fear_greed(session):
    url = "https://api.alternative.me/fng/?limit=1"
    data = await fetch_json(session, url)
    if data and "data" in data and len(data["data"]) > 0:
        d = data["data"][0]
        return {"value": int(d.get("value", 0)), "label": d.get("value_classification", "N/A")}
    return None

async def get_news(session):
    url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=popular&limit=5"
    data = await fetch_json(session, url)
    if data and "Data" in data and len(data["Data"]) > 0:
        return [{"title": i.get("title",""), "url": i.get("url",""), "source": i.get("source","")} for i in data["Data"][:5]]
    url2 = "https://cryptopanic.com/api/free/v1/posts/?public=true&filter=rising&currencies=BTC,ETH&limit=5"
    data2 = await fetch_json(session, url2)
    if data2 and "results" in data2:
        return [{"title": i.get("title",""), "url": i.get("url",""), "source": i.get("source",{}).get("title","")} for i in data2["results"][:5]]
    return None

async def get_ai_analysis(session, btc, dxy, gdata, fg, news):
    if not GROQ_API_KEY:
        return None
    btc_price = f"${btc['price']:,.2f}" if btc and btc.get("price") else "N/A"
    btc_change = f"{btc['change_24h']:+.2f}%" if btc and btc.get("change_24h") else "N/A"
    btc_high = f"${btc['high_24h']:,.2f}" if btc and btc.get("high_24h") else "N/A"
    btc_low = f"${btc['low_24h']:,.2f}" if btc and btc.get("low_24h") else "N/A"
    dxy_val = f"{dxy['dxy']:.2f}" if dxy and dxy.get("dxy") else "N/A"
    eur_usd = f"{dxy['eur_usd']:.4f}" if dxy and dxy.get("eur_usd") else "N/A"
    usd_jpy = f"{dxy['usd_jpy']:.2f}" if dxy and dxy.get("usd_jpy") else "N/A"
    mcap = f"${gdata['total_market_cap']/1e12:.2f}T" if gdata and gdata.get("total_market_cap",0) > 0 else "N/A"
    vol = f"${gdata['volume_24h']/1e9:.1f}B" if gdata and gdata.get("volume_24h",0) > 0 else "N/A"
    btc_dom = f"{gdata['btc_dominance']:.1f}%" if gdata and gdata.get("btc_dominance") else "N/A"
    fg_val = f"{fg['value']} ({fg['label']})" if fg else "N/A"
    news_str = ""
    if news:
        for n in news[:3]:
            news_str += f"- {n['title']}\n"
    prompt = f"""Kamu analis crypto profesional. Analisis pasar hari ini dalam Bahasa Indonesia.

DATA:
BTC/USDT: {btc_price} ({btc_change})
High/Low 24h: {btc_high} / {btc_low}
DXY: {dxy_val}
EUR/USD: {eur_usd} | USD/JPY: {usd_jpy}
Total Market Cap: {mcap}
24h Volume: {vol}
BTC Dominance: {btc_dom}
Fear & Greed: {fg_val}

BERITA:
{news_str if news_str else "Tidak tersedia"}

Buat 3 bagian singkat:
1. Kondisi Pasar
2. Sentimen & Berita
3. Outlook

Jawab padat, maks 300 kata."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 1200, "temperature": 0.7}
    try:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result["choices"][0]["message"]["content"]
            else:
                print(f"[Groq HTTP {resp.status}]")
    except Exception as e:
        print(f"[Groq Error] {e}")
    return None

def fmt_price(n):
    if not n or n <= 0: return "N/A"
    return f"${n:,.2f}"

def fmt_big(n):
    if not n or n <= 0: return "N/A"
    if n >= 1e12: return f"${n/1e12:.2f}T"
    elif n >= 1e9: return f"${n/1e9:.2f}B"
    elif n >= 1e6: return f"${n/1e6:.2f}M"
    return f"${n:,.0f}"

def fmt_vol(n):
    if not n or n <= 0: return "N/A"
    if n >= 1e9: return f"${n/1e9:.2f}B"
    elif n >= 1e6: return f"${n/1e6:.2f}M"
    return f"${n:,.0f}"

def fg_emoji(v):
    if not v: return "N/A"
    if v <= 20: return "😱 Extreme Fear"
    elif v <= 40: return "😟 Fear"
    elif v <= 60: return "😐 Neutral"
    elif v <= 80: return "😊 Greed"
    else: return "🤑 Extreme Greed"

def build_embed(btc, dxy, gdata, fg, news, analysis):
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    embed = discord.Embed(
        title="📊 Daily Crypto Market Report",
        description=f"📅 **{now.strftime('%d %B %Y')}** | ⏰ {now.strftime('%H.%M')} WIB",
        color=discord.Color.orange(),
        timestamp=now,
    )
    budget = 0
    # BTC/USDT
    btc_val = "Data tidak tersedia"
    if btc:
        ch = btc.get("change_24h", 0) or 0
        e = "🟢" if ch >= 0 else "🔴"
        btc_val = f"💰 **Harga:** {fmt_price(btc.get('price'))}\n{e} **24h:** {ch:+.2f}%\n📈 **High:** {fmt_price(btc.get('high_24h'))}\n📉 **Low:** {fmt_price(btc.get('low_24h'))}\n📊 **Volume:** {fmt_vol(btc.get('volume_24h'))}\n🪙 **Market Cap:** {fmt_big(btc.get('mcap'))}"
    embed.add_field(name="₿ BTC/USDT", value=btc_val, inline=True)
    budget += len(btc_val) + 30
    # DXY
    dxy_val = "Data tidak tersedia"
    if dxy:
        dxy_val = f"💵 **DXY:** {dxy['dxy']:.2f}\n\n🇪🇺 EUR/USD: {dxy['eur_usd']:.4f}\n🇯🇵 USD/JPY: {dxy['usd_jpy']:.2f}\n🇬🇧 GBP/USD: {dxy['gbp_usd']:.4f}\n🇦🇺 AUD/USD: {dxy['aud_usd']:.4f}"
    embed.add_field(name="💱 DXY Index & Forex", value=dxy_val, inline=True)
    budget += len(dxy_val) + 30
    # Global
    gm_val = "Data tidak tersedia"
    if gdata:
        lines = [f"🌐 **Market Cap:** {fmt_big(gdata.get('total_market_cap'))}", f"📊 **24h Volume:** {fmt_big(gdata.get('volume_24h'))}", f"₿ **BTC Dominance:** {gdata.get('btc_dominance', 0):.1f}%", f"Ξ **ETH Dominance:** {gdata.get('eth_dominance', 0):.1f}%"]
        if fg:
            lines.append(f"😨 **Fear & Greed:** {fg['value']} {fg_emoji(fg['value'])}")
        gm_val = "\n".join(lines)
    embed.add_field(name="🌍 Global Market", value=gm_val, inline=True)
    budget += len(gm_val) + 30
    # News
    if news and len(news) > 0:
        news_lines = []
        for i, n in enumerate(news, 1):
            line = f"**{i}.** [{n['title']}]({n['url']}) — *{n.get('source', '')}*"
            if budget + len(line) > 2500: break
            news_lines.append(line)
            budget += len(line)
        if news_lines:
            embed.add_field(name="📰 Berita Crypto Terkini", value="\n".join(news_lines), inline=False)
    # AI Analysis
    if analysis:
        remain = 6000 - budget - 150
        if len(analysis) > remain:
            analysis = analysis[:remain].rsplit(" ", 1)[0] + "..."
        embed.add_field(name="🤖 Analisis AI (Groq)", value=analysis, inline=False)
    embed.set_footer(text="⚠️ Not Financial Advice | DYOR\nGroq AI | CryptoCompare | CoinCap | Alternative.me")
    return embed

async def generate_report():
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            get_btc_data(session), get_dxy_data(session),
            get_global_data(session), get_fear_greed(session),
            get_news(session), return_exceptions=True
        )
        btc, dxy, gdata, fg, news = [None if isinstance(r, Exception) else r for r in results]
        for name, r in zip(["BTC","DXY","Global","FG","News"], results):
            if isinstance(r, Exception):
                print(f"[{name} Error] {r}")
        analysis = await get_ai_analysis(session, btc, dxy, gdata, fg, news)
        return build_embed(btc, dxy, gdata, fg, news, analysis)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    print(f"📡 Channel ID: {CHANNEL_ID}")
    print(f"⏰ Auto-post at {REPORT_HOUR_WIB}:00 WIB")
    bot.loop.create_task(auto_post_loop())

@bot.command()
async def report(ctx):
    msg = await ctx.send("⏳ Generating report...")
    try:
        embed = await generate_report()
        await msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await msg.edit(content=f"❌ Error: {e}")

async def auto_post_loop():
    wib = pytz.timezone("Asia/Jakarta")
    await bot.wait_until_ready()
    while True:
        now = datetime.now(wib)
        target = now.replace(hour=REPORT_HOUR_WIB, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        print(f"⏰ Next auto-post in {wait/3600:.1f} hours ({target.strftime('%Y-%m-%d %H:%M WIB')})")
        await asyncio.sleep(wait)
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            try:
                embed = await generate_report()
                await channel.send(embed=embed)
                print(f"✅ Auto-post sent")
            except Exception as e:
                print(f"❌ Auto-post failed: {e}")
        else:
            print("❌ Channel not found!")

if __name__ == "__main__":
    print("🚀 Starting bot...")
    if DISCORD_BOT_TOKEN:
        bot.run(DISCORD_BOT_TOKEN)
    else:
        print("❌ DISCORD_BOT_TOKEN not set!")
