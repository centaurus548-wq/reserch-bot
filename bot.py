# ==============================================================================
#  DISCORD CRYPTO RESEARCH BOT - FINAL OPTIMIZED VERSION
#  Features: CMC Primary, FF 6 Fallbacks, IMF CPI Secondary, AI 3 Fields,
#            Top Gainers/Losers, News Links, Realtime Alerts, Auto-Post
# ==============================================================================

import discord
from discord.ext import commands
import aiohttp
import asyncio
import os
import re
import json
import requests as req_lib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pytz
import time

# ======================== CONFIGURATION ========================

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0"))
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
CMC_API_KEY = os.environ.get("CMC_API_KEY", "")
REPORT_HOUR = int(os.environ.get("REPORT_HOUR_WIB", "8"))

# ======================== GLOBALS ========================

last_alerted = set()
ff_cache = {}
FF_CACHE_TTL = 300  # 5 minutes

# ======================== API URLs ========================

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_NEXT_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
FF_PROXY = "https://api.allorigins.win/raw?url=" + req_lib.utils.quote(FF_URL, safe="")
FF_PROXY2 = "https://corsproxy.io/?" + req_lib.utils.quote(FF_URL, safe="")

CMC_BASE = "https://pro-api.coinmarketcap.com"
CMC_HEADERS = {"X-CMC_PRO_API_KEY": CMC_API_KEY}

IMF_CPI_URL = "https://api.imf.org/external/sdmx/2.1/data/IMF.STA,CPI/USA....A?startPeriod=2023-01&endPeriod=2026-12&format=json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

ORANGE = 0xFF8C00

# ======================== HELPER FUNCTIONS ========================


def split_text(text, max_len=1024):
    """Split text into chunks of max_len characters."""
    if not text:
        return [""]
    if len(text) <= max_len:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = remaining.rfind(". ", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    return chunks


def _cmc_fetch(url):
    """Synchronous fetch for CoinMarketCap API (needs sync requests)."""
    try:
        r = req_lib.get(url, headers=CMC_HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("[CMC Error] " + str(e))
    return None


def _groq_chat(prompt, max_tokens=1500):
    """Synchronous Groq AI chat completion."""
    try:
        r = req_lib.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + GROQ_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("[Groq Error] " + str(e))
    return None


def fmt_price(n):
    """Format number as USD price."""
    if not n or n <= 0:
        return "N/A"
    if n >= 1000:
        return "$" + str(round(n, 2))
    if n >= 1:
        return "$" + str(round(n, 4))
    return "$" + str(round(n, 6))


def fmt_big(n):
    """Format large number with commas."""
    if not n or n <= 0:
        return "N/A"
    return "$" + "{:,.0f}".format(n)


def pct_str(n):
    """Format percentage with sign."""
    if n is None:
        return "N/A"
    sign = "+" if n >= 0 else ""
    return sign + str(round(n, 2)) + "%"


# ======================== DATA FUNCTIONS ========================


def get_btc_data():
    """Get BTC data - CMC primary -> CryptoCompare -> CoinCap."""
    # --- CoinMarketCap (Primary) ---
    data = _cmc_fetch(CMC_BASE + "/v2/cryptocurrency/quotes/latest?id=1&convert=USD")
    if data and "data" in data:
        try:
            btc = data["data"]["1"]
            q = btc["quote"]["USD"]
            h24 = q.get("high_24h", {})
            l24 = q.get("low_24h", {})
            high_val = h24.get("price", 0) or q["price"]
            low_val = l24.get("price", 0) or q["price"]
            return {
                "price": q["price"],
                "change_24h": q["percent_change_24h"],
                "change_7d": q["percent_change_7d"],
                "high": high_val,
                "low": low_val,
                "volume": q["volume_24h"],
                "market_cap": q["market_cap"],
                "source": "CoinMarketCap",
            }
        except (KeyError, TypeError):
            pass

    # --- CryptoCompare Fallback ---
    try:
        r = req_lib.get(
            "https://min-api.cryptocompare.com/data/v2/histoday?fsym=BTC&tsym=USD&limit=1",
            timeout=15,
            headers={"User-Agent": UA},
        )
        if r.status_code == 200:
            d = r.json()["Data"]["Data"]
            today = d[-1] if len(d) > 1 else d[0]
            yesterday = d[-2] if len(d) > 1 else d[0]
            ch24 = 0
            if yesterday["close"] and yesterday["close"] > 0:
                ch24 = ((today["close"] - yesterday["close"]) / yesterday["close"]) * 100
            return {
                "price": today["close"],
                "change_24h": ch24,
                "change_7d": 0,
                "high": today["high"],
                "low": today["low"],
                "volume": today["volumeto"],
                "market_cap": 0,
                "source": "CryptoCompare",
            }
    except Exception:
        pass

    # --- CoinCap Fallback ---
    try:
        r = req_lib.get(
            "https://api.coincap.io/v2/assets/bitcoin",
            timeout=15,
            headers={"User-Agent": UA},
        )
        if r.status_code == 200:
            d = r.json()["data"]
            return {
                "price": float(d["priceUsd"]),
                "change_24h": float(d.get("changePercent24Hr", 0) or 0),
                "change_7d": 0,
                "high": float(d["priceUsd"]),
                "low": float(d["priceUsd"]),
                "volume": float(d.get("volumeUsd24Hr", 0) or 0),
                "market_cap": float(d.get("marketCapUsd", 0) or 0),
                "source": "CoinCap",
            }
    except Exception:
        pass

    return {
        "price": 0, "change_24h": 0, "change_7d": 0,
        "high": 0, "low": 0, "volume": 0, "market_cap": 0,
        "source": "N/A",
    }


def get_global_data():
    """Get global crypto data - CMC primary -> CoinGecko -> CoinCap."""
    # --- CoinMarketCap (Primary) ---
    data = _cmc_fetch(CMC_BASE + "/v1/global-metrics/quotes/latest")
    if data and "data" in data:
        try:
            d = data["data"]
            btc_dom = d.get("btc_dominance", 0)
            eth_dom = d.get("eth_dominance", 0)
            mcp = d.get("market_cap_percentage")
            if mcp:
                if not btc_dom:
                    btc_dom = mcp.get("BTC", 0)
                if not eth_dom:
                    eth_dom = mcp.get("ETH", 0)
            return {
                "market_cap": d["quote"]["USD"]["total_market_cap"],
                "volume": d["quote"]["USD"]["total_volume_24h"],
                "btc_dom": btc_dom,
                "eth_dom": eth_dom,
                "change_24h": d["quote"]["USD"]["total_market_cap_yesterday_percentage_change"],
                "total_cryptos": d.get("total_cryptocurrencies", 0),
                "source": "CoinMarketCap",
            }
        except (KeyError, TypeError):
            pass

    # --- CoinGecko Fallback ---
    try:
        r = req_lib.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=15,
            headers={"User-Agent": UA},
        )
        if r.status_code == 200:
            d = r.json()["data"]
            return {
                "market_cap": d["total_market_cap"]["usd"],
                "volume": d["total_volume"]["usd"],
                "btc_dom": d["market_cap_percentage"]["btc"],
                "eth_dom": d["market_cap_percentage"]["eth"],
                "change_24h": d["market_cap_change_percentage_24h_usd"],
                "total_cryptos": d.get("active_cryptocurrencies", 0),
                "source": "CoinGecko",
            }
    except Exception:
        pass

    # --- CoinCap Fallback ---
    try:
        r = req_lib.get(
            "https://api.coincap.io/v2/global",
            timeout=15,
            headers={"User-Agent": UA},
        )
        if r.status_code == 200:
            d = r.json()["data"]
            return {
                "market_cap": float(d.get("totalMarketCap", 0) or 0),
                "volume": float(d.get("totalVolume24Hr", 0) or 0),
                "btc_dom": float(d.get("btcDominance", 0) or 0),
                "eth_dom": 0,
                "change_24h": 0,
                "total_cryptos": 0,
                "source": "CoinCap",
            }
    except Exception:
        pass

    return {
        "market_cap": 0, "volume": 0, "btc_dom": 0,
        "eth_dom": 0, "change_24h": 0, "total_cryptos": 0,
        "source": "N/A",
    }


def get_top_gainers():
    """Get top 3 gainers from CMC listings."""
    data = _cmc_fetch(
        CMC_BASE + "/v1/cryptocurrency/listings/latest?limit=100&sort=volume_24h&sort_dir=desc"
    )
    if data and "data" in data:
        try:
            coins = data["data"]
            valid = [c for c in coins if c["quote"]["USD"].get("percent_change_24h") is not None]
            valid.sort(key=lambda x: x["quote"]["USD"]["percent_change_24h"], reverse=True)
            result = []
            for coin in valid[:3]:
                q = coin["quote"]["USD"]
                result.append({
                    "name": coin["name"],
                    "symbol": coin["symbol"].upper(),
                    "price": q["price"],
                    "change_24h": q["percent_change_24h"],
                })
            return result
        except (KeyError, TypeError):
            pass
    return []


def get_top_losers():
    """Get top 3 losers from CMC listings."""
    data = _cmc_fetch(
        CMC_BASE + "/v1/cryptocurrency/listings/latest?limit=100&sort=volume_24h&sort_dir=desc"
    )
    if data and "data" in data:
        try:
            coins = data["data"]
            valid = [c for c in coins if c["quote"]["USD"].get("percent_change_24h") is not None]
            valid.sort(key=lambda x: x["quote"]["USD"]["percent_change_24h"])
            result = []
            for coin in valid[:3]:
                q = coin["quote"]["USD"]
                result.append({
                    "name": coin["name"],
                    "symbol": coin["symbol"].upper(),
                    "price": q["price"],
                    "change_24h": q["percent_change_24h"],
                })
            return result
        except (KeyError, TypeError):
            pass
    return []


def get_fear_greed():
    """Get Fear & Greed Index from Alternative.me."""
    try:
        r = req_lib.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=10,
            headers={"User-Agent": UA},
        )
        if r.status_code == 200:
            d = r.json()["data"][0]
            return {"value": int(d["value"]), "classification": d["value_classification"]}
    except Exception:
        pass
    return {"value": 0, "classification": "N/A"}


def get_news():
    """Get crypto news with titles, URLs and sources.
    Returns [{title, url, source}]. Descriptions are added later by AI."""
    # --- CryptoPanic Direct ---
    try:
        r = req_lib.get(
            "https://cryptopanic.com/api/free/v1/posts/?auth_token=573c95d36a94ec5953e3bb0e5dca7d38&filter=rising&currencies=BTC,ETH&public=true",
            timeout=10,
            headers={"User-Agent": UA},
        )
        if r.status_code == 200:
            articles = r.json().get("results", [])
            if articles:
                result = []
                for a in articles[:5]:
                    title = a.get("title", "")
                    url = a.get("url", "")
                    source = a.get("source", {})
                    source_name = source.get("title", "") if isinstance(source, dict) else str(source)
                    if title:
                        result.append({"title": title, "url": url, "source": source_name})
                if result:
                    return result
    except Exception:
        pass

    # --- CryptoPanic via Proxy ---
    try:
        cp_url = "https://cryptopanic.com/api/free/v1/posts/?auth_token=573c95d36a94ec5953e3bb0e5dca7d38&filter=rising&currencies=BTC,ETH&public=true"
        px = "https://api.allorigins.win/raw?url=" + req_lib.utils.quote(cp_url, safe="")
        r = req_lib.get(px, timeout=15, headers={"User-Agent": UA})
        if r.status_code == 200 and r.text.strip():
            articles = r.json().get("results", [])
            if articles:
                result = []
                for a in articles[:5]:
                    title = a.get("title", "")
                    url = a.get("url", "")
                    source = a.get("source", {})
                    source_name = source.get("title", "") if isinstance(source, dict) else str(source)
                    if title:
                        result.append({"title": title, "url": url, "source": source_name})
                if result:
                    return result
    except Exception:
        pass

    # --- Google News RSS ---
    try:
        r = req_lib.get(
            "https://news.google.com/rss/search?q=bitcoin+cryptocurrency+market&hl=en-US&gl=US&ceid=US:en",
            timeout=15,
            headers={"User-Agent": UA},
        )
        if r.status_code == 200:
            root = ET.fromstring(r.text)
            items = root.findall(".//item")
            if items:
                result = []
                for item in items[:5]:
                    title_el = item.find("title")
                    link_el = item.find("link")
                    source_el = item.find("source")
                    if title_el is not None and title_el.text:
                        link = link_el.text if link_el is not None else ""
                        source = source_el.text if source_el is not None else ""
                        result.append({"title": title_el.text, "url": link, "source": source})
                if result:
                    return result
    except Exception:
        pass

    return []


def generate_news_descriptions(news):
    """Use AI to generate brief highlight/description for each news article.
    Single AI call for all articles to save tokens."""
    if not news or not GROQ_API_KEY:
        return []

    headlines = ""
    for i, n in enumerate(news, 1):
        headlines += str(i) + ". " + n["title"] + "\n"

    prompt = (
        "Untuk setiap berita crypto berikut, buat satu kalimat highlight/ringkasan singkat "
        "dalam Bahasa Indonesia (maks 20 kata per berita). Format: angka. highlight\n\n"
        "BERITA:\n" + headlines
    )

    result = _groq_chat(prompt, max_tokens=300)
    if not result:
        return []

    descriptions = {}
    for line in result.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        for i in range(1, 6):
            prefix = str(i) + "."
            if line.startswith(prefix):
                desc = line[len(prefix):].strip()
                desc = desc.replace("**", "")
                descriptions[i] = desc
                break

    enriched = []
    for i, n in enumerate(news, 1):
        enriched.append({
            "title": n["title"],
            "url": n["url"],
            "source": n.get("source", ""),
            "description": descriptions.get(i, ""),
        })

    return enriched


def get_ff_events(force_refresh=False):
    """Get Forex Factory events with 6 fallback methods and caching."""
    global ff_cache

    if not force_refresh and ff_cache.get("data"):
        if time.time() - ff_cache.get("timestamp", 0) < FF_CACHE_TTL:
            return ff_cache["data"]

    events = []

    # --- Method 1: requests direct ---
    try:
        r = req_lib.get(FF_URL, timeout=15, headers={"User-Agent": UA})
        if r.status_code == 200 and r.text.strip():
            events = r.json()
            if events:
                ff_cache = {"data": events, "timestamp": time.time()}
                print("[FF] Method 1 success (requests direct)")
                return events
    except Exception:
        pass

    # --- Method 2: allorigins proxy ---
    try:
        proxy_url = "https://api.allorigins.win/raw?url=" + req_lib.utils.quote(FF_URL, safe="")
        r = req_lib.get(proxy_url, timeout=15, headers={"User-Agent": UA})
        if r.status_code == 200 and r.text.strip():
            events = r.json()
            if events:
                ff_cache = {"data": events, "timestamp": time.time()}
                print("[FF] Method 2 success (allorigins)")
                return events
    except Exception:
        pass

    # --- Method 3: corsproxy ---
    try:
        proxy_url2 = "https://corsproxy.io/?" + req_lib.utils.quote(FF_URL, safe="")
        r = req_lib.get(proxy_url2, timeout=15, headers={"User-Agent": UA})
        if r.status_code == 200 and r.text.strip():
            events = r.json()
            if events:
                ff_cache = {"data": events, "timestamp": time.time()}
                print("[FF] Method 3 success (corsproxy)")
                return events
    except Exception:
        pass

    # --- Method 4: requests nextweek via proxy ---
    try:
        nx_proxy = "https://api.allorigins.win/raw?url=" + req_lib.utils.quote(FF_NEXT_URL, safe="")
        r = req_lib.get(nx_proxy, timeout=15, headers={"User-Agent": UA})
        if r.status_code == 200 and r.text.strip():
            events = r.json()
            if events:
                ff_cache = {"data": events, "timestamp": time.time()}
                print("[FF] Method 4 success (nextweek proxy)")
                return events
    except Exception:
        pass

    # --- Method 5: urllib direct ---
    try:
        import urllib.request
        req = urllib.request.Request(FF_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            if body.strip():
                events = json.loads(body)
                if events:
                    ff_cache = {"data": events, "timestamp": time.time()}
                    print("[FF] Method 5 success (urllib direct)")
                    return events
    except Exception:
        pass

    # --- Method 6: urllib via allorigins proxy ---
    try:
        import urllib.request
        px = "https://api.allorigins.win/raw?url=" + req_lib.utils.quote(FF_URL, safe="")
        req = urllib.request.Request(px, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            if body.strip():
                events = json.loads(body)
                if events:
                    ff_cache = {"data": events, "timestamp": time.time()}
                    print("[FF] Method 6 success (urllib proxy)")
                    return events
    except Exception:
        pass

    if ff_cache.get("data"):
        print("[FF] All methods failed, returning cached data")
        return ff_cache["data"]

    print("[FF] All methods failed, no data available")
    return []


def get_imf_cpi():
    """Get US CPI data from IMF SDMX API as secondary source for macro."""
    try:
        r = req_lib.get(
            IMF_CPI_URL,
            timeout=20,
            headers={"User-Agent": UA, "Accept": "application/json"},
        )
        if r.status_code != 200:
            print("[IMF CPI] HTTP " + str(r.status_code))
            return None

        raw = r.json()

        data_sets = raw.get("data", {}).get("dataSets", [])
        if not data_sets:
            print("[IMF CPI] No dataSets found")
            return None

        ds = data_sets[0]
        series = ds.get("series", {})
        if not series:
            print("[IMF CPI] No series found")
            return None

        series_key = list(series.keys())[0]
        observations = series[series_key].get("observations", {})
        if not observations:
            print("[IMF CPI] No observations found")
            return None

        structure = raw.get("data", {}).get("structure", {})
        dims = structure.get("dimensions", {}).get("observation", [])
        time_dim_index = -1
        time_values = []
        for idx, dim in enumerate(dims):
            if dim.get("id") == "TIME_PERIOD":
                time_dim_index = idx
                time_values = dim.get("values", [])
                break

        obs_list = []
        for obs_key, obs_val in observations.items():
            parts = obs_key.split(":")
            value = obs_val[0] if isinstance(obs_val, list) else obs_val
            if value is None:
                continue

            if time_dim_index >= 0 and time_dim_index < len(parts):
                pos_idx = int(parts[time_dim_index])
                if pos_idx < len(time_values):
                    period = time_values[pos_idx].get("id", str(pos_idx))
                else:
                    period = "Unknown"
            else:
                period = "Unknown"

            obs_list.append({"period": period, "value": value})

        if len(obs_list) < 2:
            print("[IMF CPI] Not enough data points (" + str(len(obs_list)) + ")")
            return None

        obs_list.sort(key=lambda x: x["period"], reverse=True)
        latest = obs_list[0]
        previous = obs_list[1]

        change = 0
        if previous["value"] and previous["value"] != 0:
            change = ((latest["value"] - previous["value"]) / previous["value"]) * 100

        result = {
            "latest_period": latest["period"],
            "latest_value": latest["value"],
            "previous_period": previous["period"],
            "previous_value": previous["value"],
            "change_pct": round(change, 2),
            "trend": "Naik" if change > 0 else ("Turun" if change < 0 else "Stabil"),
        }
        print("[IMF CPI] Fetched: " + latest["period"] + " = " + str(latest["value"]))
        return result

    except Exception as e:
        print("[IMF CPI Error] " + str(e))
        return None


# ======================== AI ANALYSIS FUNCTIONS ========================


def get_ai_analysis(btc, gd, fg, news, gainers, losers):
    """AI analysis parsed into 3 parts via [1][2][3] separators."""
    try:
        news_text = ""
        if news:
            for n in news[:5]:
                news_text += "- " + n["title"] + "\n"
        if not news_text:
            news_text = "Tidak tersedia\n"

        gainers_text = ""
        if gainers:
            for g in gainers:
                gainers_text += g["name"] + " (" + g["symbol"] + "): +" + str(round(g["change_24h"], 2)) + "%\n"
        if not gainers_text:
            gainers_text = "Tidak tersedia\n"

        losers_text = ""
        if losers:
            for l in losers:
                losers_text += l["name"] + " (" + l["symbol"] + "): " + str(round(l["change_24h"], 2)) + "%\n"
        if not losers_text:
            losers_text = "Tidak tersedia\n"

        prompt = (
            "Kamu adalah analis crypto profesional. Analisis data pasar berikut dan berikan "
            "insight dalam Bahasa Indonesia.\n\n"
            "DATA PASAR BTC:\n"
            "- Harga: " + fmt_price(btc["price"]) + "\n"
            "- Perubahan 24h: " + pct_str(btc["change_24h"]) + "\n"
            "- Perubahan 7d: " + pct_str(btc["change_7d"]) + "\n"
            "- High 24h: " + fmt_price(btc["high"]) + "\n"
            "- Low 24h: " + fmt_price(btc["low"]) + "\n"
            "- Volume 24h: " + fmt_big(btc["volume"]) + "\n\n"
            "MARKET GLOBAL:\n"
            "- Total Market Cap: " + fmt_big(gd["market_cap"]) + "\n"
            "- Total Volume: " + fmt_big(gd["volume"]) + "\n"
            "- BTC Dominance: " + str(round(gd["btc_dom"], 1)) + "%\n"
            "- ETH Dominance: " + str(round(gd["eth_dom"], 1)) + "%\n"
            "- Perubahan 24h: " + pct_str(gd["change_24h"]) + "\n\n"
            "FEAR & GREED INDEX: " + str(fg["value"]) + " (" + fg["classification"] + ")\n\n"
            "TOP 3 GAINERS:\n" + gainers_text + "\n"
            "TOP 3 LOSERS:\n" + losers_text + "\n"
            "BERITA TERKINI:\n" + news_text + "\n\n"
            "Berikan analisis dalam 3 bagian, gunakan separator [1] [2] [3]:\n"
            "[1] Ringkasan Pasar: Overview kondisi pasar saat ini, korelasi antar data, sentimen keseluruhan\n"
            "[2] Psikologi Pasar: Analisis perilaku trader, Fear & Greed context, crowd sentiment\n"
            "[3] Prediksi Market: Proyeksi arah market jangka pendek berdasarkan data teknikal dan fundamental\n\n"
            "Masing-masing bagian 5-10 kalimat. Gunakan Bahasa Indonesia. Jangan gunakan emoji berlebihan."
        )

        result = _groq_chat(prompt, max_tokens=2000)
        if result:
            parts = result.split("[")
            parsed = {}
            for part in parts:
                for sep in ["1]", "2]", "3]"]:
                    if part.startswith(sep):
                        content = part[len(sep):].strip()
                        if content.startswith("**") and "**" in content[2:]:
                            content = content[content.index("**") + 2:]
                            if content.startswith("**"):
                                content = content[2:]
                        parsed[sep] = content

            for sep in ["1]", "2]", "3]"]:
                if sep not in parsed:
                    parsed[sep] = "Tidak tersedia"

            return parsed

    except Exception as e:
        print("[AI Analysis Error] " + str(e))

    return {"1]": "Gagal mengambil analisis AI.", "2]": "Gagal mengambil analisis AI.", "3]": "Gagal mengambil analisis AI."}


def get_macro_analysis(events):
    """AI analysis for macro economic events."""
    try:
        if not events:
            return "Tidak ada event ekonomi untuk dianalisis saat ini."

        event_text = ""
        for e in events[:10]:
            title = e.get("title", "Unknown")
            country = e.get("country", "")
            impact = e.get("impact", "")
            forecast = e.get("forecast", "-")
            previous = e.get("previous", "-")
            actual = e.get("actual", "-")
            if not forecast:
                forecast = "-"
            if not previous:
                previous = "-"
            if not actual:
                actual = "-"
            event_text += (
                "- " + title + " | " + country + " | Impact: " + impact
                + " | Forecast: " + str(forecast)
                + " | Previous: " + str(previous)
                + " | Actual: " + str(actual) + "\n"
            )

        prompt = (
            "Kamu adalah analis ekonomi makro profesional. Untuk setiap event berikut, "
            "berikan analisis dalam Bahasa Indonesia.\n\n"
            "EVENT EKONOMI:\n" + event_text + "\n\n"
            "Untuk setiap event, berikan:\n"
            "- RESEARCH: Penjelasan singkat apa itu event dan kenapa penting untuk crypto (2-3 kalimat)\n"
            "- PROYEKSI: Proyeksi dampak ke BTC dan pasar crypto (2-3 kalimat)\n"
            "- TERDAMPAK: Level dampak (Tinggi/Sedang/Rendah) untuk crypto (1-2 kalimat)\n\n"
            "Format: tulis nama event lalu RESEARCH/PROYEKSI/TERDAMPAK. Gunakan Bahasa Indonesia."
        )

        result = _groq_chat(prompt, max_tokens=2000)
        if result:
            return result

    except Exception as e:
        print("[Macro AI Error] " + str(e))

    return "Gagal mengambil analisis makroekonomi."


def get_realtime_alert(event):
    """AI analysis for realtime economic data release alert."""
    try:
        title = event.get("title", "Unknown")
        country = event.get("country", "")
        forecast = event.get("forecast", "-")
        previous = event.get("previous", "-")
        actual = event.get("actual", "-")

        if not forecast:
            forecast = "-"
        if not previous:
            previous = "-"
        if not actual:
            actual = "-"

        prompt = (
            "Kamu adalah analis ekonomi realtime. Event ekonomi baru saja rilis:\n\n"
            "- " + title + "\n"
            "- Negara: " + country + "\n"
            "- Forecast: " + str(forecast) + "\n"
            "- Previous: " + str(previous) + "\n"
            "- Actual: " + str(actual) + "\n\n"
            "Berikan analisis dalam Bahasa Indonesia:\n"
            "- VERDICT: Bullish/Bearish/Neutral untuk crypto (1-2 kalimat)\n"
            "- DAMPAK: Dampak langsung ke BTC dan pasar crypto (2-3 kalimat)\n"
            "- SARAN: Rekomendasi untuk trader crypto (1-2 kalimat)\n\n"
            "Jawab ringkas dalam Bahasa Indonesia."
        )

        result = _groq_chat(prompt, max_tokens=500)
        if result:
            return result

    except Exception as e:
        print("[Realtime AI Error] " + str(e))

    return "Gagal mengambil analisis realtime."


# ======================== EMBED BUILDERS ========================


def build_report_embeds(btc, gd, fg, news, gainers, losers, ai):
    """Build report embeds - returns list of embeds."""
    embeds = []
    wib = pytz.timezone("Asia/Jakarta")
    now_str = datetime.now(wib).strftime("%d %b %Y %H:%M WIB")

    # ========== EMBED 1: Data Pasar + Market Global + Fear & Greed ==========
    emb1 = discord.Embed(
        title="Laporan Pasar Crypto",
        description="Data real-time " + now_str,
        color=ORANGE,
    )

    price_s = fmt_price(btc["price"])
    ch24_s = pct_str(btc["change_24h"])
    ch7d_s = pct_str(btc["change_7d"])
    high_s = fmt_price(btc["high"])
    low_s = fmt_price(btc["low"])
    vol_s = fmt_big(btc["volume"])
    mcap_s = fmt_big(btc["market_cap"])
    src_s = btc["source"]

    btc_val = (
        "**Harga:** " + price_s + "\n"
        + "**24h:** " + ch24_s + " | **7d:** " + ch7d_s + "\n"
        + "**High:** " + high_s + " | **Low:** " + low_s + "\n"
        + "**Volume:** " + vol_s + " | **MCap:** " + mcap_s + "\n"
        + "*Sumber: " + src_s + "*"
    )
    emb1.add_field(name="BTC / USD", value=btc_val, inline=False)

    g_mcap = fmt_big(gd["market_cap"])
    g_vol = fmt_big(gd["volume"])
    g_btc_dom = str(round(gd["btc_dom"], 1)) + "%"
    g_eth_dom = str(round(gd["eth_dom"], 1)) + "%"
    g_ch24 = pct_str(gd["change_24h"])
    g_total = str(gd["total_cryptos"]) if gd["total_cryptos"] > 0 else "N/A"
    g_src = gd["source"]

    g_val = (
        "**Total Cap:** " + g_mcap + "\n"
        + "**Volume 24h:** " + g_vol + "\n"
        + "**BTC Dom:** " + g_btc_dom + " | **ETH Dom:** " + g_eth_dom + "\n"
        + "**24h Change:** " + g_ch24 + "\n"
        + "**Total Crypto:** " + g_total + "\n"
        + "*Sumber: " + g_src + "*"
    )
    emb1.add_field(name="Market Global", value=g_val, inline=False)

    fg_val = str(fg["value"])
    fg_cls = fg["classification"]
    fg_label = fg_val + "/100 - " + fg_cls
    emb1.add_field(name="Fear & Greed Index", value="**" + fg_label + "**", inline=False)

    embeds.append(emb1)

    # ========== EMBED 2: Berita + Top Movers ==========
    emb2 = discord.Embed(title="Berita & Top Movers", color=ORANGE)

    if news:
        news_val = ""
        for i, n in enumerate(news[:5], 1):
            title = n.get("title", "")
            url = n.get("url", "")
            desc = n.get("description", "")
            source = n.get("source", "")
            if url:
                news_val += "**" + str(i) + ". [" + title + "](" + url + ")**\n"
            else:
                news_val += "**" + str(i) + ". " + title + "**\n"
            if desc:
                news_val += "_" + desc + "_\n"
            if source:
                news_val += "Source: " + source + "\n"
            news_val += "\n"
        news_val = news_val.strip()
        if len(news_val) > 1024:
            chunks = split_text(news_val, 1024)
            for ci, chunk in enumerate(chunks):
                field_name = "Berita Terkini" if ci == 0 else "Berita Terkini (" + str(ci + 1) + ")"
                emb2.add_field(name=field_name, value=chunk, inline=False)
        else:
            emb2.add_field(name="Berita Terkini", value=news_val, inline=False)
    else:
        emb2.add_field(
            name="Berita Terkini",
            value="Tidak ada berita tersedia saat ini.",
            inline=False,
        )

    if gainers:
        g_text = ""
        for g in gainers:
            name = g["name"]
            sym = g["symbol"]
            price = fmt_price(g["price"])
            ch = "+" + str(round(g["change_24h"], 2)) + "%"
            g_text += "**" + name + "** (" + sym + "): " + price + " | " + ch + "\n"
        emb2.add_field(name="Top 3 Gainers", value=g_text, inline=False)
    else:
        emb2.add_field(name="Top 3 Gainers", value="Tidak tersedia.", inline=False)

    if losers:
        l_text = ""
        for l in losers:
            name = l["name"]
            sym = l["symbol"]
            price = fmt_price(l["price"])
            ch = str(round(l["change_24h"], 2)) + "%"
            l_text += "**" + name + "** (" + sym + "): " + price + " | " + ch + "\n"
        emb2.add_field(name="Top 3 Losers", value=l_text, inline=False)
    else:
        emb2.add_field(name="Top 3 Losers", value="Tidak tersedia.", inline=False)

    embeds.append(emb2)

    # ========== EMBED 3: AI Analysis (3 fields) ==========
    emb3 = discord.Embed(title="Analisis AI", color=ORANGE)

    ringkasan = ai.get("1]", "Tidak tersedia")
    psikologi = ai.get("2]", "Tidak tersedia")
    prediksi = ai.get("3]", "Tidak tersedia")

    emb3.add_field(name="Ringkasan Pasar", value=ringkasan[:1024], inline=False)
    emb3.add_field(name="Psikologi Pasar", value=psikologi[:1024], inline=False)
    emb3.add_field(name="Prediksi Market", value=prediksi[:1024], inline=False)

    embeds.append(emb3)

    return embeds


def build_macro_embed(events, ai_text, imf_data=None):
    """Build macro calendar embeds - returns list of embeds."""
    embeds = []
    wib = pytz.timezone("Asia/Jakarta")
    now = datetime.now(wib)

    # ========== EMBED: IMF CPI (if available) ==========
    if imf_data:
        emb_cpi = discord.Embed(
            title="IMF - US Consumer Price Index (Secondary Data)",
            color=ORANGE,
        )
        cpi_val = (
            "**Periode Terbaru:** " + str(imf_data["latest_period"]) + "\n"
            + "**CPI:** " + str(imf_data["latest_value"]) + "\n"
            + "**Periode Sebelumnya:** " + str(imf_data["previous_period"]) + "\n"
            + "**CPI Sebelumnya:** " + str(imf_data["previous_value"]) + "\n"
            + "**Perubahan:** " + str(imf_data["change_pct"]) + "% (" + imf_data["trend"] + ")\n\n"
            + "*Sumber: International Monetary Fund (SDMX API)*"
        )
        emb_cpi.add_field(name="US CPI Data", value=cpi_val, inline=False)
        embeds.append(emb_cpi)

    # ========== EMBED: FF Events ==========
    emb_ff = discord.Embed(
        title="Kalender Ekonomi - Forex Factory",
        color=ORANGE,
    )

    if not events:
        emb_ff.add_field(
            name="Event Ekonomi",
            value="Tidak ada data event tersedia saat ini.",
            inline=False,
        )
        embeds.append(emb_ff)
        return embeds

    event_text = ""
    current_day_marker = ""
    max_events = 20

    for e in events[:max_events]:
        date_str = e.get("date", "")
        event_date = None
        if date_str:
            try:
                event_date = datetime.strptime(date_str.split("T")[0], "%Y-%m-%d").date()
            except Exception:
                pass

        if event_date:
            if event_date == now.date():
                day_label = "--- Hari Ini ---"
            elif event_date == (now + timedelta(days=1)).date():
                day_label = "--- Besok ---"
            elif event_date == (now + timedelta(days=-1)).date():
                day_label = "--- Kemarin ---"
            else:
                day_label = ""

            if day_label and day_label != current_day_marker:
                if event_text:
                    event_text += "\n"
                event_text += "**" + day_label + "**\n"
                current_day_marker = day_label

        time_str = e.get("date", "")
        wib_time = "-"
        if time_str and "T" in time_str:
            try:
                utc_time = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
                utc_time = utc_time.replace(tzinfo=pytz.UTC)
                wib_time = utc_time.astimezone(wib).strftime("%H:%M WIB")
            except Exception:
                wib_time = time_str.split("T")[1][:5] + " WIB"

        impact = e.get("impact", "")
        if impact == "High":
            impact_label = "HIGH"
        elif impact == "Medium":
            impact_label = "MED"
        else:
            impact_label = "LOW"

        title = e.get("title", "Unknown")
        country = e.get("country", "")
        forecast = e.get("forecast")
        previous = e.get("previous")
        actual = e.get("actual")

        forecast_s = str(forecast) if forecast and str(forecast).strip() != "" else "-"
        previous_s = str(previous) if previous and str(previous).strip() != "" else "-"
        actual_s = str(actual) if actual and str(actual).strip() != "" else "-"

        event_text += (
            "`" + wib_time + "` **" + title + "** (" + country + ") [" + impact_label + "]\n"
        )

        data_line = "  Forecast: " + forecast_s + " | Previous: " + previous_s
        if actual_s != "-":
            data_line += " | Actual: " + actual_s
        event_text += data_line + "\n\n"

    if len(event_text) > 4000:
        chunks = split_text(event_text, 4000)
        emb_ff.add_field(name="Event Ekonomi", value=chunks[0], inline=False)
        embeds.append(emb_ff)
        for chunk in chunks[1:]:
            extra = discord.Embed(title="Kalender Ekonomi (lanjutan)", color=ORANGE)
            extra.add_field(name="Event Ekonomi", value=chunk, inline=False)
            embeds.append(extra)
    else:
        emb_ff.add_field(name="Event Ekonomi", value=event_text, inline=False)
        embeds.append(emb_ff)

    # ========== EMBED: AI Macro Analysis ==========
    if ai_text:
        emb_ai = discord.Embed(title="Analisis Makroekonomi AI", color=ORANGE)
        chunks = split_text(ai_text, 1024)
        for i, chunk in enumerate(chunks):
            field_name = "Insight" if i == 0 else ("Insight (lanjutan " + str(i + 1) + ")")
            emb_ai.add_field(name=field_name, value=chunk, inline=False)
        embeds.append(emb_ai)

    return embeds


def build_realtime_embed(event, ai_text):
    """Build realtime economic alert embed."""
    impact = event.get("impact", "")
    if impact == "High":
        impact_str = "HIGH"
        color = 0xFF0000
    elif impact == "Medium":
        impact_str = "MEDIUM"
        color = ORANGE
    else:
        impact_str = "LOW"
        color = ORANGE

    title = event.get("title", "Unknown")
    country = event.get("country", "")

    emb = discord.Embed(
        title="Data Ekonomi Baru Dirilis",
        description="**" + title + "** (" + country + ") [" + impact_str + "]",
        color=color,
    )

    date_str = event.get("date", "")
    time_wib = ""
    if date_str and "T" in date_str:
        try:
            wib = pytz.timezone("Asia/Jakarta")
            utc_time = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
            utc_time = utc_time.replace(tzinfo=pytz.UTC)
            time_wib = utc_time.astimezone(wib).strftime("%d %b %Y %H:%M WIB")
        except Exception:
            time_wib = date_str

    actual = event.get("actual")
    forecast = event.get("forecast")
    previous = event.get("previous")

    actual_s = str(actual) if actual and str(actual).strip() != "" else "-"
    forecast_s = str(forecast) if forecast and str(forecast).strip() != "" else "-"
    previous_s = str(previous) if previous and str(previous).strip() != "" else "-"

    data_val = ""
    if time_wib:
        data_val += "**Waktu:** " + time_wib + "\n"
    data_val += (
        "**Actual:** " + actual_s + "\n"
        + "**Forecast:** " + forecast_s + "\n"
        + "**Previous:** " + previous_s
    )
    emb.add_field(name="Data", value=data_val, inline=False)

    if ai_text:
        emb.add_field(name="Analisis AI", value=ai_text[:1024], inline=False)

    return emb


# ======================== BOT SETUP ========================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

command_lock = asyncio.Lock()


# ======================== BOT COMMANDS ========================


@bot.command(name="report")
async def cmd_report(ctx):
    """Generate full crypto market report."""
    if command_lock.locked():
        await ctx.send("Sedang ada report yang sedang diproses. Tunggu sebentar...")
        return

    async with command_lock:
        loading = await ctx.send("Mengumpulkan data dan menganalisa pasar...")
        try:
            loop = asyncio.get_event_loop()
            btc = await loop.run_in_executor(None, get_btc_data)
            gd = await loop.run_in_executor(None, get_global_data)
            fg = await loop.run_in_executor(None, get_fear_greed)
            news = await loop.run_in_executor(None, get_news)
            gainers = await loop.run_in_executor(None, get_top_gainers)
            losers = await loop.run_in_executor(None, get_top_losers)

            news_enriched = await loop.run_in_executor(None, generate_news_descriptions, news)
            if news_enriched:
                news = news_enriched

            ai = await loop.run_in_executor(
                None, get_ai_analysis, btc, gd, fg, news, gainers, losers
            )

            embeds = build_report_embeds(btc, gd, fg, news, gainers, losers, ai)

            try:
                await loading.delete()
            except Exception:
                pass

            for emb in embeds:
                await ctx.send(embed=emb)

        except Exception as e:
            print("[Report Error] " + str(e))
            try:
                await loading.delete()
            except Exception:
                pass
            await ctx.send("Gagal membuat report: " + str(e))


@bot.command(name="macro")
async def cmd_macro(ctx):
    """Generate macro economic calendar."""
    if command_lock.locked():
        await ctx.send("Sedang ada macro yang sedang diproses. Tunggu sebentar...")
        return

    async with command_lock:
        loading = await ctx.send("Mengumpulkan data kalender ekonomi...")
        try:
            loop = asyncio.get_event_loop()

            events = await loop.run_in_executor(None, get_ff_events, True)
            imf_cpi = await loop.run_in_executor(None, get_imf_cpi)

            ai_text = ""
            if events:
                ai_text = await loop.run_in_executor(
                    None, get_macro_analysis, events
                )

            embeds = build_macro_embed(events, ai_text, imf_cpi)

            try:
                await loading.delete()
            except Exception:
                pass

            for emb in embeds:
                await ctx.send(embed=emb)

        except Exception as e:
            print("[Macro Error] " + str(e))
            try:
                await loading.delete()
            except Exception:
                pass
            await ctx.send("Gagal membuat macro: " + str(e))


# ======================== AUTO POST ========================


async def auto_post():
    """Auto-post report + macro every day at REPORT_HOUR WIB."""
    await bot.wait_until_ready()
    wib = pytz.timezone("Asia/Jakarta")

    while not bot.is_closed():
        now = datetime.now(wib)
        target = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)

        delay = (target - now).total_seconds()
        print("[Auto] Next post in " + str(round(delay / 3600, 1)) + " hours")
        await asyncio.sleep(delay)

        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            print("[Auto] Channel not found!")
            continue

        try:
            loop = asyncio.get_event_loop()

            btc = await loop.run_in_executor(None, get_btc_data)
            gd = await loop.run_in_executor(None, get_global_data)
            fg = await loop.run_in_executor(None, get_fear_greed)
            news = await loop.run_in_executor(None, get_news)
            gainers = await loop.run_in_executor(None, get_top_gainers)
            losers = await loop.run_in_executor(None, get_top_losers)

            news_enriched = await loop.run_in_executor(None, generate_news_descriptions, news)
            if news_enriched:
                news = news_enriched

            ai = await loop.run_in_executor(
                None, get_ai_analysis, btc, gd, fg, news, gainers, losers
            )

            report_embeds = build_report_embeds(btc, gd, fg, news, gainers, losers, ai)
            for emb in report_embeds:
                await channel.send(embed=emb)

            await asyncio.sleep(3)

            events = []
            for attempt in range(3):
                force = (attempt == 0)
                events = await loop.run_in_executor(None, get_ff_events, force)
                if events:
                    break
                print("[Auto] FF retry " + str(attempt + 1) + "/3 failed")
                await asyncio.sleep(10)

            imf_cpi = await loop.run_in_executor(None, get_imf_cpi)

            ai_text = ""
            if events:
                ai_text = await loop.run_in_executor(None, get_macro_analysis, events)

            macro_embeds = build_macro_embed(events, ai_text, imf_cpi)
            for emb in macro_embeds:
                await channel.send(embed=emb)

            print("[Auto] Post completed at " + now.strftime("%Y-%m-%d %H:%M"))

        except Exception as e:
            print("[Auto Error] " + str(e))


# ======================== REALTIME MONITOR ========================


async def realtime_monitor():
    """Monitor FF events every 120s and alert when new data releases."""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            loop = asyncio.get_event_loop()
            events = await loop.run_in_executor(None, get_ff_events)

            if events:
                for e in events:
                    title = e.get("title", "")
                    country = e.get("country", "")
                    actual = e.get("actual")

                    if actual and str(actual).strip() not in ["-", "", "None"]:
                        event_id = title + "_" + country + "_" + str(actual)

                        if event_id not in last_alerted:
                            last_alerted.add(event_id)

                            if len(last_alerted) > 500:
                                to_remove = list(last_alerted)[:250]
                                for x in to_remove:
                                    last_alerted.discard(x)

                            channel = bot.get_channel(CHANNEL_ID)
                            if channel:
                                ai_text = await loop.run_in_executor(
                                    None, get_realtime_alert, e
                                )
                                emb = build_realtime_embed(e, ai_text)
                                await channel.send(embed=emb)
                                print("[Realtime] Alert: " + title)
        except Exception as e:
            print("[Realtime Error] " + str(e))

        await asyncio.sleep(120)


# ======================== BOT EVENTS ========================


@bot.event
async def on_ready():
    print("Bot online: " + bot.user.name)
    print("Channel ID: " + str(CHANNEL_ID))
    print("Auto-post at " + str(REPORT_HOUR) + ":00 WIB")
    bot.loop.create_task(auto_post())
    bot.loop.create_task(realtime_monitor())


# ======================== MAIN ========================

if __name__ == "__main__":
    print("Starting bot...")
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("ERROR: DISCORD_BOT_TOKEN not set!")
