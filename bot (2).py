"""
Discord Crypto Research Bot
- Data: CoinMarketCap + CryptoCompare + CoinGecko + CryptoPanic
- AI: Groq API (llama-3.3-70b-versatile)
- Commands: !report
- Auto-post daily report
"""

import os
import asyncio
import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pytz
import hashlib
import re
import time
import xml.etree.ElementTree as ET
from collections import namedtuple
from email.utils import parsedate_to_datetime

load_dotenv()

# ===================== CONFIG =====================

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHAN = int(os.getenv("CHANNEL_ID", "0"))
GROQ = os.getenv("GROQ_API_KEY")
CMC_KEY = os.getenv("CMC_API_KEY", "")
CRYPTOPANIC_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
CRYPTOCOMPARE_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "")
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
    """Fetch JSON from URL."""
    for i in range(retries):
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15), headers=UA
            ) as r:
                if r.status == 200:
                    ct = r.headers.get("Content-Type", "")
                    if "json" in ct:
                        return await r.json()
                    # Some APIs return JSON without proper content-type
                    try:
                        return await r.json()
                    except Exception:
                        text = await r.text()
                        if text.strip().startswith("<"):
                            return None  # HTML page, not JSON
                        return None
                if r.status == 429:
                    await asyncio.sleep(3 ** i)
                else:
                    print(f"[HTTP {r.status}] {url}")
        except Exception as e:
            print(f"[Retry {i+1}] {url}: {e}")
            if i < retries - 1:
                await asyncio.sleep(2)
    return None


async def fetch_text(session, url, retries=2):
    """Fetch raw text from URL (for RSS feeds)."""
    for i in range(retries):
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15), headers=UA
            ) as r:
                if r.status == 200:
                    return await r.text()
                elif r.status == 429:
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


# ===================== DXY + OTHER DATA =====================

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


# News dedup: track posted news URLs to avoid reuse
_posted_news_urls = set()

RSS_FEEDS = [
    ("https://cointelegraph.com/rss", "Cointelegraph"),
    ("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk"),
]


async def _fetch_rss_news(session, feed_url, source_name, max_age_hours=48):
    """Parse RSS feed and return news items within max_age_hours."""
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    cutoff = now - timedelta(hours=max_age_hours)
    results = []

    text = await fetch_text(session, feed_url)
    if not text or len(text) < 100:
        return results

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        print(f"[RSS] Parse error for {source_name}")
        return results

    for item in root.findall(".//item"):
        if len(results) >= 10:
            break
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub_date_str = item.findtext("pubDate", "")

        if not title or not link:
            continue

        # Parse pubDate for age check
        pub_dt = None
        if pub_date_str:
            try:
                pub_dt = parsedate_to_datetime(pub_date_str)
                pub_wib = pub_dt.astimezone(pytz.timezone("Asia/Jakarta"))
                if pub_wib < cutoff:
                    continue
            except Exception:
                pass

        # Dedup check
        url_hash = hashlib.md5(link.encode()).hexdigest()[:12]
        if url_hash in _posted_news_urls:
            continue

        results.append({"title": title, "url": link, "source": source_name})

    return results


async def get_news(session):
    """Get crypto news from multiple sources with tiered fallback.
    Tier 1: CryptoPanic (with optional API key)
    Tier 2: CryptoCompare (with optional API key)
    Tier 3: RSS feeds (Cointelegraph, CoinDesk) — always free, no auth needed
    Deduplicates against previously posted news."""
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    cutoff_24h = now - timedelta(hours=24)
    cutoff_48h = now - timedelta(hours=48)
    cutoff_24h_ts = int(cutoff_24h.timestamp())
    cutoff_48h_ts = int(cutoff_48h.timestamp())
    results = []

    def _add_results(new_items):
        for item in new_items:
            h = hashlib.md5(item["url"].encode()).hexdigest()[:12]
            if h not in {hashlib.md5(r["url"].encode()).hexdigest()[:12] for r in results}:
                results.append(item)

    # ===== TIER 1: CryptoPanic =====
    cp_url = "https://cryptopanic.com/api/free/v1/posts/?currencies=BTC,ETH&limit=20"
    if CRYPTOPANIC_KEY:
        cp_url += f"&auth_token={CRYPTOPANIC_KEY}"
    else:
        cp_url += "&public=true&filter=rising"
    data = await fetch(session, cp_url)
    if data and isinstance(data, dict) and "results" in data:
        items = data["results"]
        if isinstance(items, list) and len(items) > 0:
            for item in items:
                title = item.get("title", "")
                url_link = item.get("url", "")
                source = item.get("source", {}).get("title", "CryptoPanic")
                created = item.get("created_at", "")
                if created:
                    try:
                        pub_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        pub_wib = pub_dt.astimezone(pytz.timezone("Asia/Jakarta"))
                        if pub_wib < cutoff_24h:
                            continue
                    except Exception:
                        pass
                url_hash = hashlib.md5(url_link.encode()).hexdigest()[:12]
                if url_hash in _posted_news_urls:
                    continue
                results.append({"title": title, "url": url_link, "source": source})
                if len(results) >= 5:
                    break
            if results:
                print(f"[News] CryptoPanic: {len(results)} items")

    # ===== TIER 2: CryptoCompare =====
    if len(results) < 5:
        cc_url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest&limit=20"
        cc_headers = dict(UA)
        if CRYPTOCOMPARE_KEY:
            cc_headers["authorization"] = f"Apikey {CRYPTOCOMPARE_KEY}"
        try:
            async with session.get(
                cc_url, headers=cc_headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    data2 = await r.json()
                    items2 = data2.get("Data", [])
                    if isinstance(items2, list) and len(items2) > 0:
                        for item in items2:
                            if len(results) >= 5:
                                break
                            title = item.get("title", "")
                            url_link = item.get("url", "")
                            source = item.get("source", "CryptoCompare")
                            published = item.get("published_on", 0)
                            if published and published < cutoff_48h_ts:
                                continue
                            url_hash = hashlib.md5(url_link.encode()).hexdigest()[:12]
                            if url_hash in _posted_news_urls:
                                continue
                            if url_hash in {hashlib.md5(r["url"].encode()).hexdigest()[:12] for r in results}:
                                continue
                            results.append({"title": title, "url": url_link, "source": source})
                        if len(results) > 0:
                            print(f"[News] CryptoCompare: {len(results)} total items")
                    elif data2.get("Message"):
                        print(f"[News] CryptoCompare: {data2.get('Message')}")
                else:
                    print(f"[News] CryptoCompare: HTTP {r.status}")
        except Exception as e:
            print(f"[News] CryptoCompare error: {e}")

    # ===== TIER 3: RSS Feeds (always free, no auth) =====
    if len(results) < 5:
        print(f"[News] Trying RSS feeds ({len(results)} items so far)")
        for feed_url, source_name in RSS_FEEDS:
            if len(results) >= 5:
                break
            rss_items = await _fetch_rss_news(session, feed_url, source_name, max_age_hours=48)
            if rss_items:
                for item in rss_items:
                    if len(results) >= 5:
                        break
                    h = hashlib.md5(item["url"].encode()).hexdigest()[:12]
                    if h in _posted_news_urls:
                        continue
                    if h in {hashlib.md5(r["url"].encode()).hexdigest()[:12] for r in results}:
                        continue
                    results.append(item)
                print(f"[News] {source_name} RSS: +{min(len(rss_items), 5 - len(results) + len(rss_items))} items")

    # Mark as posted
    if results:
        for r in results:
            h = hashlib.md5(r["url"].encode()).hexdigest()[:12]
            _posted_news_urls.add(h)
        if len(_posted_news_urls) > 200:
            for x in list(_posted_news_urls)[:100]:
                _posted_news_urls.discard(x)

    return results if results else None


# ===================== AI ANALYSIS + FALLBACK =====================

def fallback_analysis(btc, dxy, gdata, fg, news, cmc_coins, cmc_global):
    """Rule-based crypto market analysis when Groq is unavailable. Returns dict."""
    result = {
        "news_summaries": [n.get("title", "")[:120] for n in news] if news else [],
        "ringkasan_pasar": "",
        "psikologi_pasar": "",
        "prediksi_market": "",
    }

    # --- Ringkasan Pasar ---
    rp_lines = []
    if btc and btc.get("price"):
        ch = btc.get("change_24h", 0) or 0
        ch7d = btc.get("change_7d", 0) or 0
        arrow_24h = "naik" if ch >= 0 else "turun"
        arrow_7d = "naik" if ch7d >= 0 else "turun"
        rp_lines.append(f"Kondisi pasar saat ini menunjukkan bahwa harga Bitcoin (BTC) sedang {arrow_24h} dengan perubahan 24 jam sebesar {ch:+.2f}% dan perubahan 7 hari sebesar {ch7d:+.2f}% ke level ${btc['price']:,.2f}.")
        if btc.get("volume_24h"):
            rp_lines.append(f"Volume perdagangan 24 jam mencapai ${btc['volume_24h']:,.0f}, menunjukkan aktivitas pasar yang {'cukup tinggi' if btc['volume_24h'] > 30e9 else 'normal'}.")
    if gdata or cmc_global:
        src = cmc_global or gdata
        mc = src.get("market_cap_change_24h") or src.get("market_change_24h") or 0
        trend = "bullish" if mc > 1 else ("bearish" if mc < -1 else "konsolidasi")
        rp_lines.append(f"Total Market Cap global mencapai ${src.get('total_market_cap', 0):,.0f} dengan perubahan {mc:+.2f}%, menunjukkan pasar sedang dalam tren {trend}.")
        rp_lines.append(f"Dominasi BTC sebesar {src.get('btc_dominance', 0):.1f}%, ETH memiliki dominasi sebesar {src.get('eth_dominance', 0):.1f}%.")
    if cmc_coins and len(cmc_coins) > 0:
        gainers = [c for c in cmc_coins if (c.get("change_24h") or 0) > 0]
        losers = [c for c in cmc_coins if (c.get("change_24h") or 0) < 0]
        rp_lines.append(f"Dari top 10 CoinMarketCap: {len(gainers)} coin naik, {len(losers)} coin turun.")
    if news and len(news) > 0:
        rp_lines.append(f"Berita terkini tentang {news[0].get('title', '')[:60]} dan perkembangan lainnya mempengaruhi kondisi pasar.")
    result["ringkasan_pasar"] = "\n".join(rp_lines) if rp_lines else "Data tidak tersedia."

    # --- Psikologi Pasar ---
    pp_lines = []
    if fg:
        fg_val = fg["value"]
        fg_label = fg["label"]
        pp_lines.append(f"Fear & Greed Index saat ini berada pada level {fg_val} ({fg_label}), yang menunjukkan bahwa pasar dalam kondisi {fg_label.lower()}.")
        if fg_val <= 25:
            pp_lines.append("Secara historis, level ini sering menjadi zona akumulasi bagi investor jangka panjang. Pasar sedang dalam ketakutan ekstrem.")
            pp_lines.append("Trader cenderung melakukan panic selling, namun potensi rebound cukup tinggi.")
        elif fg_val <= 45:
            pp_lines.append("Pasar masih dalam tekanan, namun belum mencapai level panic ekstrem. Sebagian investor mulai wait-and-see.")
        elif fg_val <= 55:
            pp_lines.append("Tidak ada dominasi fear atau greed yang signifikan. Kondisi ini biasanya terjadi sebelum pergerakan arah yang jelas.")
        elif fg_val <= 75:
            pp_lines.append("Pasar didominasi oleh optimisme. Trader disarankan mempertimbangkan pengambilan profit sebagian.")
        else:
            pp_lines.append("Kondisi ini sering menjadi sinyal peringatan akan potensi reversal. Waspadai profit taking masif.")
        if news and len(news) > 0:
            pp_lines.append(f"Dengan adanya berita tentang {news[0].get('title', '')[:60]}, dapat mempengaruhi kondisi pasar.")
            pp_lines.append("Trader perlu memantau perkembangan berita untuk menyesuaikan strategi.")
    else:
        pp_lines.append("Data Fear & Greed Index tidak tersedia saat ini.")
    result["psikologi_pasar"] = "\n".join(pp_lines) if pp_lines else "Data tidak tersedia."

    # --- Prediksi Market ---
    pm_lines = []
    if btc and (gdata or cmc_global):
        ch = btc.get("change_24h", 0) or 0
        src = cmc_global or gdata
        mc = src.get("market_cap_change_24h") or src.get("market_change_24h") or 0
        ch7d = btc.get("change_7d", 0) or 0
        pm_lines.append("Dalam jangka pendek, pasar dipengaruhi oleh berita terkini dan data ekonomi.")
        if ch > 3 and mc > 1:
            pm_lines.append("Momentum bullish kuat terdeteksi. Potensi continuation ke atas, namun perhatikan resistance terdekat.")
        elif ch < -3 and mc < -1:
            pm_lines.append("Tekanan bearish signifikan. Potensi penurunan lanjutan, monitor level support kunci.")
        elif ch > 1:
            pm_lines.append("Tren short-term bullish dengan kenaikan moderat. Perhatikan apakah volume mendukung.")
        elif ch < -1:
            pm_lines.append("Tren short-term bearish. Perhatikan apakah terjadi bounce di area support.")
        else:
            pm_lines.append("Pasar dalam kondisi konsolidasi. Tunggu breakout untuk arah yang jelas.")
        if ch7d > 5:
            pm_lines.append("Dengan melihat tren positif dalam 7 hari terakhir, pasar memiliki potensi untuk terus meningkat.")
        elif ch7d < -5:
            pm_lines.append("Tren 7 hari negatif perlu diwaspadai, potensi tekanan lanjutan.")
        if fg:
            fg_val = fg["value"]
            if fg_val <= 25 and ch > 0:
                pm_lines.append("Sinyal contrarian: Fear ekstrem + harga naik = potensi reversal bullish.")
            elif fg_val >= 75 and ch < 0:
                pm_lines.append("Warning: Greed ekstrem + harga turun = potensi koreksi lebih dalam.")
        pm_lines.append("Trader perlu memiliki strategi yang fleksibel dan siap untuk menyesuaikan dengan perubahan pasar.")
    else:
        pm_lines.append("Data tidak cukup untuk memberikan prediksi yang akurat.")
    result["prediksi_market"] = "\n".join(pm_lines) if pm_lines else "Data tidak tersedia."

    result["ringkasan_pasar"] += "\n\n*Analisis berbasis data rule-based (Groq AI tidak tersedia).*"
    return result


def parse_ai_response(text, news):
    """Parse AI response into structured dict with news summaries and 3 analysis sections."""
    result = {
        "news_summaries": [],
        "ringkasan_pasar": "",
        "psikologi_pasar": "",
        "prediksi_market": "",
    }

    sections = {}
    cur_section = None
    cur_lines = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()

        # Detect section headers
        if upper.startswith("RINGKASAN BERITA"):
            if cur_section:
                sections[cur_section] = "\n".join(cur_lines)
            cur_section = "news_summaries"
            cur_lines = []
            continue
        elif upper.startswith("RINGKASAN PASAR"):
            if cur_section:
                sections[cur_section] = "\n".join(cur_lines)
            cur_section = "ringkasan_pasar"
            cur_lines = []
            continue
        elif upper.startswith("PSIKOLOGI PASAR"):
            if cur_section:
                sections[cur_section] = "\n".join(cur_lines)
            cur_section = "psikologi_pasar"
            cur_lines = []
            continue
        elif upper.startswith("PREDIKSI MARKET"):
            if cur_section:
                sections[cur_section] = "\n".join(cur_lines)
            cur_section = "prediksi_market"
            cur_lines = []
            continue

        cur_lines.append(stripped)

    if cur_section:
        sections[cur_section] = "\n".join(cur_lines)

    # Extract news summaries
    news_text = sections.get("news_summaries", "")
    if news_text:
        for line in news_text.split("\n"):
            clean = re.sub(r"^[\d\.\-\)\s]+", "", line).strip()
            if clean and len(clean) > 5:
                result["news_summaries"].append(clean)

    result["ringkasan_pasar"] = sections.get("ringkasan_pasar", "")
    result["psikologi_pasar"] = sections.get("psikologi_pasar", "")
    result["prediksi_market"] = sections.get("prediksi_market", "")

    # Fallback: if no summaries parsed, use news titles
    if not result["news_summaries"] and news:
        result["news_summaries"] = [n.get("title", "")[:120] for n in news]

    return result


async def get_ai_analysis(session, btc, dxy, gdata, fg, news, cmc_coins, cmc_global):
    """Groq AI analyzes crypto market. Returns dict with news_summaries + 3 analysis sections."""
    btc_price = f"${btc['price']:,.2f}" if btc and btc.get("price") else "N/A"
    btc_change = f"{btc['change_24h']:+.2f}%" if btc and btc.get("change_24h") else "N/A"
    btc_change_7d = f"{btc.get('change_7d', 0):+.2f}%" if btc else "N/A"
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

    cmc_str = ""
    if cmc_coins:
        for c in cmc_coins[:5]:
            cmc_str += f"- {c['name']} ({c['symbol']}): ${c['price']:,.4f} ({c['change_24h']:+.2f}%) Vol: ${c['volume_24h']:,.0f}\n"

    if cmc_global:
        mcap = f"${cmc_global['total_market_cap']:,.0f}"
        vol = f"${cmc_global['total_volume_24h']:,.0f}"
        btc_dom = f"{cmc_global['btc_dominance']:.1f}%"
        eth_dom = f"{cmc_global['eth_dominance']:.1f}%"
        mc_ch = f"{cmc_global['market_cap_change_24h']:+.2f}%"

    news_str = ""
    if news:
        for i, n in enumerate(news, 1):
            news_str += f"{i}. {n.get('title', '')}\n"

    prompt = f"""Kamu analis crypto profesional. Analisis pasar hari ini dalam Bahasa Indonesia.

DATA:
BTC/USDT: {btc_price} ({btc_change})
Perubahan 7 hari (Td): {btc_change_7d}
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

Buat output dalam format berikut persis (gunakan header yang sama):

RINGKASAN BERITA:
1. [ringkasan 1 kalimat berita 1 dalam Bahasa Indonesia]
2. [ringkasan 1 kalimat berita 2 dalam Bahasa Indonesia]
3. [ringkasan 1 kalimat berita 3 dalam Bahasa Indonesia]
(dan seterusnya sesuai jumlah berita)

RINGKASAN PASAR:
[paragraf analisis ringkasan kondisi pasar saat ini, sebutkan harga BTC, market cap, dominasi, berita terkini]

PSIKOLOGI PASAR:
[paragraf analisis psikologi pasar, hubungkan dengan Fear & Greed Index dan dampak berita terkini]

PREDIKSI MARKET:
[paragraf prediksi pergerakan market ke depan, berikan saran untuk trader]

Jawab padat, total maks 600 kata."""

    raw = await ask_groq(session, prompt, max_tokens=2000)
    if raw:
        return parse_ai_response(raw, news)
    print("[AI] Groq unavailable, using rule-based fallback")
    return fallback_analysis(btc, dxy, gdata, fg, news, cmc_coins, cmc_global)


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

MAX_FIELD_VAL = 1024
MAX_FIELD_NAME = 256


def _truncate(text, max_len):
    """Truncate text to max_len characters at last word boundary."""
    if len(text) <= max_len:
        return text
    # Reserve 3 chars for "..." suffix to guarantee result <= max_len
    return text[:max_len - 3].rsplit(" ", 1)[0] + "..."


# Simple namedtuple for collecting fields before adding to embed
_Field = namedtuple("_Field", ["name", "value", "inline"])


def _estimate_embed_chars(fields):
    """Rough estimate of total embed character count (fields only)."""
    return sum(len(f.name) + len(f.value) for f in fields)


# Maximum total characters allowed in a Discord embed
MAX_EMBED_TOTAL = 5800  # safety margin below Discord's 6000


def build_report_embed(btc, gdata, fg, news, ai_result, cmc_coins, cmc_global):
    """Build report as a SINGLE embed."""
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    summaries = ai_result.get("news_summaries", []) if ai_result else []

    embed = discord.Embed(title="Laporan Pasar Crypto", color=discord.Color.orange(), timestamp=now)
    temp_fields = []  # collect fields first to check total size

    # Field: Data real-time
    temp_fields.append(_Field(
        name="Data real-time", value=now.strftime("%d %B %Y %H:%M WIB"), inline=False
    ))

    # Field: BTC / USD
    btc_val = ""
    if btc:
        btc_val += f"**Harga:** ${btc.get('price', 0):,.2f}\n"
        btc_val += f"**24h:** {btc.get('change_24h', 0):+.1f}% | **Td:** {btc.get('change_7d', 0):+.1f}%\n"
        btc_val += f"**High:** ${btc.get('high_24h', 0):,.2f} | **Low:** ${btc.get('low_24h', 0):,.2f}\n"
        btc_val += f"**Volume:** ${btc.get('volume_24h', 0):,.0f} | **MCap:** ${btc.get('mcap', 0):,.0f}\n"
        btc_val += "**Sumber:** CoinMarketCap"
    temp_fields.append(_Field(
        name="BTC / USD", value=btc_val or "Data tidak tersedia", inline=False
    ))

    # Field: Market Global
    mg = ""
    src = cmc_global or gdata
    if src:
        mg += f"**Total Cap:** ${src.get('total_market_cap', 0):,.0f}\n"
        mg += f"**Volume 24h:** ${src.get('total_volume_24h', 0):,.0f}\n"
        mg += f"**BTC Dom:** {src.get('btc_dominance', 0):.1f}% | **ETH Dom:** {src.get('eth_dominance', 0):.1f}%\n"
        mc = src.get("market_cap_change_24h") or src.get("market_change_24h") or 0
        mg += f"**24h Change:** {mc:+.2f}%\n"
        ac = src.get("active_cryptos", "N/A")
        mg += f"**Total Crypto:** {ac:,}" if isinstance(ac, (int, float)) else f"**Total Crypto:** {ac}"
        mg += "\n**Sumber:** CoinMarketCap"
    temp_fields.append(_Field(
        name="Market Global", value=mg or "Data tidak tersedia", inline=False
    ))

    # Field: Fear & Greed Index
    if fg:
        fgi = f"**{fg['value']}/100 - {fg['label']}**"
    else:
        fgi = "N/A"
    temp_fields.append(_Field(
        name="Fear & Greed Index", value=fgi, inline=False
    ))

    # Fields: Berita Terkini (all news items)
    if news and len(news) > 0:
        for i, n in enumerate(news[:5]):
            title = n.get("title", "")
            url = n.get("url", "")
            source = n.get("source", "")
            summary = summaries[i] if i < len(summaries) else title[:120]
            field_val = f"**{i+1}.** [{title}]({url})\n{summary}\nSource: {source}"
            field_name = "Berita Terkini" if i == 0 else "\u200B"
            temp_fields.append(_Field(
                name=field_name,
                value=_truncate(field_val, MAX_FIELD_VAL),
                inline=False,
            ))
    else:
        temp_fields.append(_Field(
            name="Berita Terkini", value="Tidak ada berita tersedia saat ini.", inline=False
        ))

    # Top 3 Gainers
    if cmc_coins and len(cmc_coins) > 0:
        sorted_c = sorted(cmc_coins, key=lambda x: x.get("change_24h", 0) or 0, reverse=True)
        gainers = [c for c in sorted_c if (c.get("change_24h") or 0) > 0][:3]
        if not gainers:
            gainers = sorted_c[:3]
        gt = ""
        for c in gainers:
            gt += f"{c['name']} ({c['symbol']}): {fmt_price(c['price'])} | {chg_emoji(c['change_24h'])}\n"
        temp_fields.append(_Field(
            name="Top 3 Gainers", value=gt.strip() or "N/A", inline=False
        ))

        # Top 3 Losers
        losers = [c for c in sorted_c if (c.get("change_24h") or 0) < 0][:3]
        if not losers:
            losers = list(reversed(sorted_c[-3:]))
        lt = ""
        for c in losers:
            lt += f"{c['name']} ({c['symbol']}): {fmt_price(c['price'])} | {chg_emoji(c['change_24h'])}\n"
        temp_fields.append(_Field(
            name="Top 3 Losers", value=lt.strip() or "N/A", inline=False
        ))

    # AI Analysis sections
    rp = ai_result.get("ringkasan_pasar", "") if ai_result else ""
    pp = ai_result.get("psikologi_pasar", "") if ai_result else ""
    pm = ai_result.get("prediksi_market", "") if ai_result else ""

    ai_sections = []
    if rp:
        ai_sections.append(("Ringkasan Pasar", rp))
    if pp:
        ai_sections.append(("Psikologi Pasar", pp))
    if pm:
        ai_sections.append(("Prediksi Market", pm))

    # Calculate remaining budget for AI sections
    base_chars = _estimate_embed_chars(temp_fields)
    # Reserve chars for title (~25), footer (~130), timestamp, url
    overhead = 200
    ai_budget = MAX_EMBED_TOTAL - base_chars - overhead

    # Smart truncation: distribute budget evenly among AI sections
    if ai_sections:
        section_names_len = sum(len(n) for n, _ in ai_sections)
        per_section_budget = (ai_budget - section_names_len) // len(ai_sections)
        if per_section_budget < 300:
            # Very tight — truncate each section to fit
            per_section_budget = max(100, per_section_budget)
        ai_sections = [(n, _truncate(v, min(MAX_FIELD_VAL, per_section_budget))) for n, v in ai_sections]

    for section_name, section_val in ai_sections:
        temp_fields.append(_Field(
            name=section_name,
            value=section_val,
            inline=False,
        ))

    # Safety: enforce 25-field limit
    if len(temp_fields) > 25:
        temp_fields = temp_fields[:25]

    # Safety: if still over total char limit, trim last fields
    total = _estimate_embed_chars(temp_fields)
    if total + overhead > MAX_EMBED_TOTAL:
        while temp_fields and _estimate_embed_chars(temp_fields) + overhead > MAX_EMBED_TOTAL:
            removed = temp_fields.pop()
            print(f"[Embed] Removed field '{removed.name[:30]}' to fit char limit")

    # Add all fields to embed
    for f in temp_fields:
        embed.add_field(name=f.name, value=f.value, inline=f.inline)

    embed.set_footer(
        text="⚠️ Not Financial Advice | DYOR\n"
        "Groq AI | CoinMarketCap | CryptoCompare | CoinGecko | CryptoPanic | Alternative.me"
    )

    return embed


# ===================== REPORT GENERATORS =====================

async def generate_report():
    """Generate the full crypto market report. Returns single embed."""
    async with aiohttp.ClientSession() as session:
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
        btc_cc, dxy, gdata, fg, news, cmc_coins, cmc_global = [
            None if isinstance(r, Exception) else r for r in results
        ]
        for name, r in zip(
            ["BTC", "DXY", "Global", "FG", "News", "CMC Listings", "CMC Global"],
            results,
        ):
            if isinstance(r, Exception):
                print(f"[{name} Error] {r}")

        if cmc_coins:
            print(f"[CMC] {len(cmc_coins)} coins loaded")
        else:
            print("[CMC] Listings unavailable, using fallback data")
        if cmc_global:
            print(f"[CMC] Global data loaded — BTC Dom: {cmc_global['btc_dominance']:.1f}%")
        else:
            print("[CMC] Global unavailable, using CoinGecko fallback")

        # Build BTC display data: CMC primary (for price, 7d, volume, mcap) + CryptoCompare for high/low
        btc = None
        if cmc_coins:
            for c in cmc_coins:
                if c.get("symbol") == "BTC":
                    btc = {
                        "price": c["price"],
                        "change_24h": c["change_24h"],
                        "change_7d": c.get("change_7d", 0),
                        "volume_24h": c["volume_24h"],
                        "mcap": c["mcap"],
                        "high_24h": 0,
                        "low_24h": 0,
                    }
                    break
        if btc and btc_cc:
            btc["high_24h"] = btc_cc.get("high_24h", 0)
            btc["low_24h"] = btc_cc.get("low_24h", 0)
        elif btc_cc:
            btc = btc_cc
            btc["change_7d"] = 0

        # AI analysis
        ai_result = await get_ai_analysis(
            session, btc, dxy, gdata, fg, news, cmc_coins, cmc_global
        )
        if ai_result:
            print("[AI] Analysis generated successfully")
        else:
            print("[AI] Analysis failed")

        return build_report_embed(
            btc, gdata, fg, news, ai_result, cmc_coins, cmc_global
        )


# ===================== BOT COMMANDS =====================

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    print(f"📡 Channel ID: {CHAN}")
    print(f"🤖 Groq API: {'SET' if GROQ else 'NOT SET'}")
    print(f"📊 CMC API: {'SET' if CMC_KEY else 'NOT SET'}")
    print(f"📰 CryptoPanic API: {'SET' if CRYPTOPANIC_KEY else 'NOT SET (RSS fallback)'}")
    print(f"📰 CryptoCompare API: {'SET' if CRYPTOCOMPARE_KEY else 'NOT SET (RSS fallback)'}")
    print(f"⏰ Auto-post at {HOUR}:00 WIB")
    bot.loop.create_task(auto_post_loop())


@bot.command()
async def report(ctx):
    """Generate crypto market report (single embed)."""
    msg = await ctx.send("⏳ Generating report...")
    try:
        embed = await generate_report()
        await msg.delete()
        await ctx.send(embed=embed)
    except discord.errors.NotFound:
        pass
    except discord.errors.HTTPException as he:
        try:
            await msg.edit(content=f"❌ Gagal mengirim report: {he}")
        except discord.errors.NotFound:
            pass
    except Exception as e:
        try:
            await msg.edit(content=f"❌ Error generating report: {e}")
        except discord.errors.NotFound:
            pass


# ===================== AUTO POST =====================

async def auto_post_loop():
    """Auto-post daily report at scheduled hour (WIB)."""
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
                embed = await generate_report()
                await channel.send(embed=embed)
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
