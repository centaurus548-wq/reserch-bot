# ==============================================================================
#  DISCORD CRYPTO RESEARCH BOT - V2 OPTIMIZED
#  Gemini API | CMC Primary | FF 3 Fallbacks | IMF CPI | AI 3 Fields
#  Top Gainers/Losers | News | Realtime Alerts | Auto-Post
# ==============================================================================

import discord
from discord.ext import commands
import asyncio
import os
import json
import requests as req_lib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pytz
import time

# ======================== CONFIGURATION ========================

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
CMC_API_KEY = os.environ.get("CMC_API_KEY", "")
REPORT_HOUR = int(os.environ.get("REPORT_HOUR_WIB", "8"))

# ======================== GLOBALS ========================

last_alerted = set()
ff_cache = {}
FF_CACHE_TTL = 300
imf_cpi_cache = {}
IMF_CPI_CACHE_TTL = 3600
fng_cache = {}
FNG_CACHE_TTL = 300
_last_ai_error = ""

# ======================== CONNECTION POOL ========================

session = req_lib.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})

# ======================== API URLs ========================

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_NEXT_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
CMC_BASE = "https://pro-api.coinmarketcap.com"
CMC_HEADERS = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
IMF_CPI_URL = "https://api.imf.org/external/sdmx/2.1/data/IMF.STA,CPI/USA....A?startPeriod=2023-01&endPeriod=2026-12&format=json"
CP_API = "https://cryptopanic.com/api/free/v1/posts/?auth_token=573c95d36a94ec5953e3bb0e5dca7d38&filter=rising&currencies=BTC,ETH&public=true"
ORANGE = 0xFF8C00
RED = 0xFF0000

# ======================== HELPER FUNCTIONS ========================


def split_text(text, max_len=1024):
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


def _safe_num(val, fallback=0):
    try:
        v = float(val)
        return v if v == v else fallback
    except (TypeError, ValueError):
        return fallback


def _cmc_fetch(url):
    try:
        r = session.get(url, headers=CMC_HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("[CMC Error] " + str(e))
    return None


def _fetch_json(url, timeout=15):
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code == 200 and r.text.strip():
            return r.json()
    except Exception:
        pass
    return None


def _fetch_text(url, timeout=15):
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code == 200 and r.text.strip():
            return r.text
    except Exception:
        pass
    return None


def _ai_chat(prompt, max_tokens=1500, retries=2):
    global _last_ai_error
    models = ["gemini-2.0-flash", "gemini-1.5-flash"]
    for model in models:
        _last_ai_error = ""
        for attempt in range(retries + 1):
            try:
                url = "https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent?key=" + GEMINI_API_KEY
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
                }
                r = session.post(url, json=payload, timeout=60)
                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            print("[Gemini] OK " + model)
                            return parts[0].get("text", "")
                    _last_ai_error = "Empty response from " + model
                elif r.status_code in [401, 403]:
                    _last_ai_error = "API Key invalid (HTTP " + str(r.status_code) + ")"
                    print("[Gemini Error] " + _last_ai_error)
                    return None
                elif r.status_code == 429:
                    retry_after = r.headers.get("Retry-After", "")
                    wait_sec = int(retry_after) if retry_after and retry_after.isdigit() else 10 * (attempt + 1)
                    _last_ai_error = "Rate limited 429, wait " + str(wait_sec) + "s"
                    print("[Gemini] 429 on " + model + ", wait " + str(wait_sec) + "s")
                    if attempt < retries:
                        time.sleep(wait_sec)
                    continue
                else:
                    _last_ai_error = "HTTP " + str(r.status_code) + ": " + r.text[:100]
                    print("[Gemini Error] " + _last_ai_error)
            except Exception as e:
                err_str = str(e)
                if "timeout" in err_str.lower():
                    _last_ai_error = "Timeout 60s on " + model
                elif "connection" in err_str.lower():
                    _last_ai_error = "Connection error: " + err_str[:80]
                else:
                    _last_ai_error = err_str[:100] + " (" + model + ")"
                print("[Gemini Error] " + str(attempt + 1) + "/" + str(retries + 1) + " [" + model + "]: " + err_str)
            if attempt < retries and not _last_ai_error.startswith("Rate limited"):
                time.sleep(3)
        if model == models[0]:
            print("[Gemini] " + models[0] + " failed, trying fallback...")
    return None


def fmt_price(n):
    if not n or n <= 0:
        return "N/A"
    if n >= 1000:
        return "$" + str(round(n, 2))
    if n >= 1:
        return "$" + str(round(n, 4))
    return "$" + str(round(n, 6))


def fmt_big(n):
    if not n or n <= 0:
        return "N/A"
    return "$" + "{:,.0f}".format(n)


def pct_str(n):
    if n is None:
        return "N/A"
    sign = "+" if n >= 0 else ""
    return sign + str(round(n, 2)) + "%"


def _parse_cp_articles(data):
    if not data:
        return []
    articles = data.get("results", [])
    result = []
    for a in articles[:5]:
        title = a.get("title", "")
        if not title:
            continue
        url = a.get("url", "")
        source = a.get("source", {})
        source_name = source.get("title", "") if isinstance(source, dict) else str(source)
        result.append({"title": title, "url": url, "source": source_name})
    return result


# ======================== DATA FUNCTIONS ========================


def get_btc_data():
    data = _cmc_fetch(CMC_BASE + "/v2/cryptocurrency/quotes/latest?id=1&convert=USD")
    if data and "data" in data:
        try:
            q = data["data"]["1"]["quote"]["USD"]
            return {
                "price": q["price"], "change_24h": q["percent_change_24h"], "change_7d": q["percent_change_7d"],
                "high": q.get("high_24h", {}).get("price", 0) or q["price"],
                "low": q.get("low_24h", {}).get("price", 0) or q["price"],
                "volume": q["volume_24h"], "market_cap": q["market_cap"], "source": "CoinMarketCap",
            }
        except (KeyError, TypeError):
            pass
    try:
        r = session.get("https://min-api.cryptocompare.com/data/v2/histoday?fsym=BTC&tsym=USD&limit=1", timeout=15)
        if r.status_code == 200:
            d = r.json()["Data"]["Data"]
            today = d[-1] if len(d) > 1 else d[0]
            yesterday = d[-2] if len(d) > 1 else d[0]
            ch24 = 0
            if yesterday["close"] and yesterday["close"] > 0:
                ch24 = ((today["close"] - yesterday["close"]) / yesterday["close"]) * 100
            return {"price": today["close"], "change_24h": ch24, "change_7d": 0,
                    "high": today["high"], "low": today["low"], "volume": today["volumeto"],
                    "market_cap": 0, "source": "CryptoCompare"}
    except Exception:
        pass
    data = _fetch_json("https://api.coincap.io/v2/assets/bitcoin")
    if data:
        try:
            d = data["data"]
            return {"price": float(d["priceUsd"]), "change_24h": _safe_num(d.get("changePercent24Hr")),
                    "change_7d": 0, "high": float(d["priceUsd"]), "low": float(d["priceUsd"]),
                    "volume": _safe_num(d.get("volumeUsd24Hr")), "market_cap": _safe_num(d.get("marketCapUsd")),
                    "source": "CoinCap"}
        except (KeyError, TypeError):
            pass
    return {"price": 0, "change_24h": 0, "change_7d": 0, "high": 0, "low": 0, "volume": 0, "market_cap": 0, "source": "N/A"}


def get_global_data():
    data = _cmc_fetch(CMC_BASE + "/v1/global-metrics/quotes/latest")
    if data and "data" in data:
        try:
            d = data["data"]
            btc_dom = d.get("btc_dominance", 0)
            eth_dom = d.get("eth_dominance", 0)
            mcp = d.get("market_cap_percentage")
            if mcp:
                btc_dom = btc_dom or mcp.get("BTC", 0)
                eth_dom = eth_dom or mcp.get("ETH", 0)
            return {"market_cap": d["quote"]["USD"]["total_market_cap"], "volume": d["quote"]["USD"]["total_volume_24h"],
                    "btc_dom": btc_dom, "eth_dom": eth_dom,
                    "change_24h": d["quote"]["USD"]["total_market_cap_yesterday_percentage_change"],
                    "total_cryptos": d.get("total_cryptocurrencies", 0), "source": "CoinMarketCap"}
        except (KeyError, TypeError):
            pass
    data = _fetch_json("https://api.coingecko.com/api/v3/global")
    if data:
        try:
            d = data["data"]
            return {"market_cap": d["total_market_cap"]["usd"], "volume": d["total_volume"]["usd"],
                    "btc_dom": d["market_cap_percentage"]["btc"], "eth_dom": d["market_cap_percentage"]["eth"],
                    "change_24h": d["market_cap_change_percentage_24h_usd"],
                    "total_cryptos": d.get("active_cryptocurrencies", 0), "source": "CoinGecko"}
        except (KeyError, TypeError):
            pass
    data = _fetch_json("https://api.coincap.io/v2/global")
    if data:
        try:
            d = data["data"]
            return {"market_cap": _safe_num(d.get("totalMarketCap")), "volume": _safe_num(d.get("totalVolume24Hr")),
                    "btc_dom": _safe_num(d.get("btcDominance")), "eth_dom": 0, "change_24h": 0,
                    "total_cryptos": 0, "source": "CoinCap"}
        except (KeyError, TypeError):
            pass
    return {"market_cap": 0, "volume": 0, "btc_dom": 0, "eth_dom": 0, "change_24h": 0, "total_cryptos": 0, "source": "N/A"}


def get_top_movers():
    data = _cmc_fetch(CMC_BASE + "/v1/cryptocurrency/listings/latest?limit=100&sort=volume_24h&sort_dir=desc")
    gainers, losers = [], []
    if data and "data" in data:
        try:
            coins = data["data"]
            valid = [c for c in coins if c["quote"]["USD"].get("percent_change_24h") is not None]
            valid.sort(key=lambda x: x["quote"]["USD"]["percent_change_24h"])
            for coin in valid[:3]:
                q = coin["quote"]["USD"]
                losers.append({"name": coin["name"], "symbol": coin["symbol"].upper(), "price": q["price"], "change_24h": q["percent_change_24h"]})
            for coin in valid[-3:]:
                q = coin["quote"]["USD"]
                gainers.append({"name": coin["name"], "symbol": coin["symbol"].upper(), "price": q["price"], "change_24h": q["percent_change_24h"]})
        except (KeyError, TypeError):
            pass
    return gainers, losers


def get_fear_greed():
    global fng_cache
    if fng_cache.get("data"):
        if time.time() - fng_cache.get("ts", 0) < FNG_CACHE_TTL:
            return fng_cache["data"]
    data = _fetch_json("https://api.alternative.me/fng/?limit=1", timeout=10)
    if data:
        try:
            d = data["data"][0]
            result = {"value": int(d["value"]), "classification": d["value_classification"]}
            fng_cache = {"data": result, "ts": time.time()}
            return result
        except (KeyError, IndexError):
            pass
    return {"value": 0, "classification": "N/A"}


def get_news():
    data = _fetch_json(CP_API, timeout=10)
    result = _parse_cp_articles(data)
    if result:
        return result
    px = "https://api.allorigins.win/raw?url=" + req_lib.utils.quote(CP_API, safe="")
    data = _fetch_json(px, timeout=15)
    result = _parse_cp_articles(data)
    if result:
        return result
    text = _fetch_text("https://news.google.com/rss/search?q=bitcoin+cryptocurrency+market&hl=en-US&gl=US&ceid=US:en", timeout=15)
    if text:
        try:
            root = ET.fromstring(text)
            items = root.findall(".//item")
            if items:
                result = []
                for item in items[:5]:
                    t = item.find("title")
                    l = item.find("link")
                    s = item.find("source")
                    if t is not None and t.text:
                        result.append({"title": t.text, "url": l.text if l is not None else "", "source": s.text if s is not None else ""})
                if result:
                    return result
        except Exception:
            pass
    return []


def generate_news_descriptions(news):
    if not news or not GEMINI_API_KEY:
        return []
    headlines = "\n".join([str(i) + ". " + n["title"] for i, n in enumerate(news, 1)])
    prompt = "Untuk setiap berita crypto berikut, buat satu kalimat highlight/ringkasan singkat dalam Bahasa Indonesia (maks 20 kata per berita). Format: angka. highlight\n\nBERITA:\n" + headlines
    result = _ai_chat(prompt, max_tokens=300)
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
                descriptions[i] = line[len(prefix):].strip().replace("**", "")
                break
    return [{"title": n["title"], "url": n["url"], "source": n.get("source", ""), "description": descriptions.get(i, "")} for i, n in enumerate(news, 1)]


def get_ff_events(force_refresh=False):
    global ff_cache
    if not force_refresh and ff_cache.get("data"):
        if time.time() - ff_cache.get("ts", 0) < FF_CACHE_TTL:
            return ff_cache["data"]
    methods = [
        (FF_URL, "direct"),
        ("https://api.allorigins.win/raw?url=" + req_lib.utils.quote(FF_URL, safe=""), "allorigins"),
        ("https://corsproxy.io/?" + req_lib.utils.quote(FF_URL, safe=""), "corsproxy"),
    ]
    for url, label in methods:
        data = _fetch_json(url, timeout=15)
        if data:
            ff_cache = {"data": data, "ts": time.time()}
            print("[FF] OK via " + label)
            return data
    nx_url = "https://api.allorigins.win/raw?url=" + req_lib.utils.quote(FF_NEXT_URL, safe="")
    data = _fetch_json(nx_url, timeout=15)
    if data:
        ff_cache = {"data": data, "ts": time.time()}
        print("[FF] OK via nextweek proxy")
        return data
    if ff_cache.get("data"):
        print("[FF] All failed, returning stale cache")
        return ff_cache["data"]
    print("[FF] All methods failed, no data")
    return []


def get_imf_cpi():
    global imf_cpi_cache
    if imf_cpi_cache.get("data"):
        if time.time() - imf_cpi_cache.get("ts", 0) < IMF_CPI_CACHE_TTL:
            return imf_cpi_cache["data"]
    try:
        r = session.get(IMF_CPI_URL, timeout=20, headers={"Accept": "application/json"})
        if r.status_code != 200:
            return None
        raw = r.json()
        data_sets = raw.get("data", {}).get("dataSets", [])
        if not data_sets:
            return None
        series = data_sets[0].get("series", {})
        if not series:
            return None
        observations = list(series.values())[0].get("observations", {})
        if not observations:
            return None
        structure = raw.get("data", {}).get("structure", {})
        dims = structure.get("dimensions", {}).get("observation", [])
        time_idx = -1
        time_vals = []
        for idx, dim in enumerate(dims):
            if dim.get("id") == "TIME_PERIOD":
                time_idx = idx
                time_vals = dim.get("values", [])
                break
        obs_list = []
        for key, val in observations.items():
            v = val[0] if isinstance(val, list) else val
            if v is None:
                continue
            parts = key.split(":")
            if 0 <= time_idx < len(parts):
                pos = int(parts[time_idx])
                period = time_vals[pos].get("id", "?") if pos < len(time_vals) else "?"
            else:
                period = "?"
            obs_list.append({"period": period, "value": v})
        if len(obs_list) < 2:
            return None
        obs_list.sort(key=lambda x: x["period"], reverse=True)
        latest, previous = obs_list[0], obs_list[1]
        change = 0
        if previous["value"] and previous["value"] != 0:
            change = ((latest["value"] - previous["value"]) / previous["value"]) * 100
        result = {
            "latest_period": latest["period"], "latest_value": latest["value"],
            "previous_period": previous["period"], "previous_value": previous["value"],
            "change_pct": round(change, 2),
            "trend": "Naik" if change > 0 else ("Turun" if change < 0 else "Stabil"),
        }
        imf_cpi_cache = {"data": result, "ts": time.time()}
        return result
    except Exception as e:
        print("[IMF CPI Error] " + str(e))
        return None


# ======================== AI ANALYSIS FUNCTIONS ========================


def _check_key():
    if not GEMINI_API_KEY or len(GEMINI_API_KEY) < 10:
        return "GEMINI_API_KEY belum diatur atau tidak valid."
    return None


def get_ai_analysis(btc, gd, fg, news, gainers, losers):
    global _last_ai_error
    key_err = _check_key()
    if key_err:
        _last_ai_error = key_err
        result = {"1]": key_err, "2]": key_err, "3]": key_err}
        return result
    try:
        news_text = "\n".join(["- " + n["title"] for n in news[:5]]) if news else "Tidak tersedia"
        gainers_text = "\n".join([g["name"] + " (" + g["symbol"] + "): +" + str(round(g["change_24h"], 2)) + "%" for g in gainers]) if gainers else "Tidak tersedia"
        losers_text = "\n".join([l["name"] + " (" + l["symbol"] + "): " + str(round(l["change_24h"], 2)) + "%" for l in losers]) if losers else "Tidak tersedia"
        prompt = (
            "Kamu adalah analis crypto profesional. Analisis data pasar berikut dan berikan insight dalam Bahasa Indonesia.\n\n"
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
            "[1] Ringkasan Pasar: Overview kondisi pasar saat ini\n"
            "[2] Psikologi Pasar: Analisis perilaku trader, Fear & Greed context\n"
            "[3] Prediksi Market: Proyeksi arah market jangka pendek\n\n"
            "Masing-masing bagian 5-10 kalimat. Gunakan Bahasa Indonesia."
        )
        result = _ai_chat(prompt, max_tokens=2000)
        if result:
            parsed = {}
            for part in result.split("["):
                for sep in ["1]", "2]", "3]"]:
                    if part.startswith(sep):
                        content = part[len(sep):].strip()
                        if content.startswith("**") and "**" in content[2:]:
                            content = content[content.index("**", 2) + 2:].strip()
                        parsed[sep] = content
            for sep in ["1]", "2]", "3]"]:
                if sep not in parsed:
                    parsed[sep] = "Tidak tersedia"
            return parsed
        err_msg = _last_ai_error or "Unknown error"
        print("[AI Analysis] Failed: " + err_msg)
        fail = "Gagal: " + err_msg
        result = {"1]": fail, "2]": fail, "3]": fail}
        return result
    except Exception as e:
        err_msg = str(e)[:200]
        print("[AI Analysis Error] " + err_msg)
        fail = "Error: " + err_msg
        result = {"1]": fail, "2]": fail, "3]": fail}
        return result


def get_macro_analysis(events):
    global _last_ai_error
    key_err = _check_key()
    if key_err:
        return key_err
    if not events:
        return "Tidak ada event USD untuk dianalisis saat ini."
    try:
        wib = pytz.timezone("Asia/Jakarta")
        today_str = datetime.now(wib).strftime("%Y-%m-%d")
        impact_rank = {"High": 0, "Medium": 1, "Low": 2, "": 3}
        sorted_events = sorted(events, key=lambda e: (0 if e.get("date", "").startswith(today_str) else 1, impact_rank.get(e.get("impact", ""), 3)))
        event_lines = []
        for e in sorted_events[:10]:
            title = e.get("title", "Unknown")
            forecast = e.get("forecast") or "-"
            previous = e.get("previous") or "-"
            actual = e.get("actual") or "-"
            impact = e.get("impact", "")
            line = "- " + title + " [" + impact + "] | Forecast: " + str(forecast) + " | Previous: " + str(previous)
            if str(actual) != "-":
                line += " | Actual: " + str(actual)
            event_lines.append(line)
        prompt = (
            "Kamu adalah analis makroekonomi yang fokus pada dampak ke crypto market.\n\n"
            "EVENT EKONOMI USD:\n" + "\n".join(event_lines) + "\n\n"
            "Analisa setiap event berdasarkan:\n"
            "1. Penjelasan singkat apa itu event dan kenapa penting untuk crypto\n"
            "2. Dampak ke BTC/crypto: Bullish/Bearish/Neutral dengan alasan\n"
            "3. Level dampak: Tinggi/Sedang/Rendah\n\n"
            "Gunakan format per event:\n"
            "**Nama Event** [Impact]\nPenjelasan: ...\nDampak: Bullish/Bearish/Neutral - ...\nLevel: ...\n\n"
            "Jawab dalam Bahasa Indonesia, ringkas dan terstruktur."
        )
        result = _ai_chat(prompt, max_tokens=1500)
        if result:
            return result
        err_msg = _last_ai_error or "Unknown error"
        print("[Macro AI] Failed: " + err_msg)
        return "Gagal mengambil analisis makroekonomi: " + err_msg
    except Exception as e:
        err_msg = str(e)[:200]
        print("[Macro AI Error] " + err_msg)
        return "Error analisis makroekonomi: " + err_msg


def get_realtime_alert(event):
    global _last_ai_error
    key_err = _check_key()
    if key_err:
        return key_err
    try:
        title = event.get("title", "Unknown")
        country = event.get("country", "")
        forecast = event.get("forecast") or "-"
        previous = event.get("previous") or "-"
        actual = event.get("actual") or "-"
        prompt = (
            "Kamu adalah analis ekonomi realtime. Event ekonomi baru saja rilis:\n\n"
            "- " + title + "\n- Negara: " + country + "\n"
            "- Forecast: " + str(forecast) + "\n- Previous: " + str(previous) + "\n- Actual: " + str(actual) + "\n\n"
            "Berikan analisis dalam Bahasa Indonesia:\n"
            "- VERDICT: Bullish/Bearish/Neutral untuk crypto (1-2 kalimat)\n"
            "- DAMPAK: Dampak langsung ke BTC dan pasar crypto (2-3 kalimat)\n"
            "- SARAN: Rekomendasi untuk trader crypto (1-2 kalimat)\n\nJawab ringkas."
        )
        result = _ai_chat(prompt, max_tokens=500)
        if result:
            return result
        err_msg = _last_ai_error or "Unknown error"
        print("[Realtime AI] Failed: " + err_msg)
        return "Gagal mengambil analisis realtime: " + err_msg
    except Exception as e:
        err_msg = str(e)[:200]
        print("[Realtime AI Error] " + err_msg)
        return "Error analisis realtime: " + err_msg


# ======================== EMBED BUILDERS ========================


def _fmt_coin_line(c):
    sign = "+" if c["change_24h"] >= 0 else ""
    return "**" + c["name"] + "** (" + c["symbol"] + "): " + fmt_price(c["price"]) + " | " + sign + str(round(c["change_24h"], 2)) + "%"


def build_report_embeds(btc, gd, fg, news, gainers, losers, ai):
    wib = pytz.timezone("Asia/Jakarta")
    now_str = datetime.now(wib).strftime("%d %b %Y %H:%M WIB")
    emb = discord.Embed(title="Laporan Pasar Crypto", description="Data real-time " + now_str, color=ORANGE)
    btc_val = ("**Harga:** " + fmt_price(btc["price"]) + "\n**24h:** " + pct_str(btc["change_24h"]) + " | **7d:** " + pct_str(btc["change_7d"]) + "\n"
               + "**High:** " + fmt_price(btc["high"]) + " | **Low:** " + fmt_price(btc["low"]) + "\n"
               + "**Volume:** " + fmt_big(btc["volume"]) + " | **MCap:** " + fmt_big(btc["market_cap"]) + "\n*Sumber: " + btc["source"] + "*")
    emb.add_field(name="BTC / USD", value=btc_val, inline=False)
    g_val = ("**Total Cap:** " + fmt_big(gd["market_cap"]) + "\n**Volume 24h:** " + fmt_big(gd["volume"]) + "\n"
             + "**BTC Dom:** " + str(round(gd["btc_dom"], 1)) + "% | **ETH Dom:** " + str(round(gd["eth_dom"], 1)) + "%\n"
             + "**24h Change:** " + pct_str(gd["change_24h"]) + "\n**Total Crypto:** " + (str(gd["total_cryptos"]) if gd["total_cryptos"] > 0 else "N/A") + "\n*Sumber: " + gd["source"] + "*")
    emb.add_field(name="Market Global", value=g_val, inline=False)
    emb.add_field(name="Fear & Greed Index", value="**" + str(fg["value"]) + "/100 - " + fg["classification"] + "**", inline=False)
    if news:
        news_val = ""
        for i, n in enumerate(news[:5], 1):
            if n.get("url"):
                news_val += "**" + str(i) + ". [" + n["title"] + "](" + n["url"] + ")**\n"
            else:
                news_val += "**" + str(i) + ". " + n["title"] + "**\n"
            if n.get("description"):
                news_val += "_" + n["description"] + "_\n"
            if n.get("source"):
                news_val += "Source: " + n["source"] + "\n"
            news_val += "\n"
        news_val = news_val.strip()
        chunks = split_text(news_val, 1024)
        for ci, chunk in enumerate(chunks):
            fn = "Berita Terkini" if ci == 0 else "Berita (" + str(ci + 1) + ")"
            emb.add_field(name=fn, value=chunk, inline=False)
    else:
        emb.add_field(name="Berita Terkini", value="Tidak ada berita tersedia saat ini.", inline=False)
    if gainers:
        emb.add_field(name="Top 3 Gainers", value="\n".join([_fmt_coin_line(g) for g in gainers]), inline=False)
    else:
        emb.add_field(name="Top 3 Gainers", value="Tidak tersedia.", inline=False)
    if losers:
        emb.add_field(name="Top 3 Losers", value="\n".join([_fmt_coin_line(l) for l in losers]), inline=False)
    else:
        emb.add_field(name="Top 3 Losers", value="Tidak tersedia.", inline=False)
    for key, label in [("1]", "Ringkasan Pasar"), ("2]", "Psikologi Pasar"), ("3]", "Prediksi Market")]:
        emb.add_field(name=label, value=ai.get(key, "Tidak tersedia")[:1024], inline=False)
    return [emb]


def _utc_to_wib(date_str):
    if not date_str or "T" not in date_str:
        return None
    try:
        wib = pytz.timezone("Asia/Jakarta")
        utc_time = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=pytz.UTC)
        return utc_time.astimezone(wib)
    except Exception:
        return None


def build_macro_embed(events, ai_text, imf_data=None):
    wib = pytz.timezone("Asia/Jakarta")
    now = datetime.now(wib)
    now_str = now.strftime("%d %b %Y %H:%M WIB")
    emb = discord.Embed(title="Kalender Ekonomi (USD)", description="Fokus event USD - " + now_str, color=ORANGE)
    if imf_data:
        cpi_val = ("**Periode Terbaru:** " + str(imf_data["latest_period"]) + " | **CPI:** " + str(imf_data["latest_value"]) + "\n"
                   + "**Sebelumnya:** " + str(imf_data["previous_period"]) + " | **CPI:** " + str(imf_data["previous_value"]) + "\n"
                   + "**Perubahan:** " + str(imf_data["change_pct"]) + "% (" + imf_data["trend"] + ")")
        emb.add_field(name="US CPI Data (IMF)", value=cpi_val, inline=False)
    if not events:
        emb.add_field(name="Event Ekonomi USD", value="Tidak ada event USD tersedia saat ini.", inline=False)
        if ai_text:
            for i, chunk in enumerate(split_text(ai_text, 1024)):
                fn = "Analisis Makroekonomi" if i == 0 else "Analisis Makroekonomi (" + str(i + 1) + ")"
                emb.add_field(name=fn, value=chunk, inline=False)
        return [emb]
    event_text = ""
    current_marker = ""
    today_date = now.date()
    tomorrow_date = (now + timedelta(days=1)).date()
    yesterday_date = (now - timedelta(days=1)).date()
    for e in events[:15]:
        wib_dt = _utc_to_wib(e.get("date", ""))
        if wib_dt:
            ev_date = wib_dt.date()
            if ev_date == today_date:
                marker = "--- Hari Ini ---"
            elif ev_date == tomorrow_date:
                marker = "--- Besok ---"
            elif ev_date == yesterday_date:
                marker = "--- Kemarin ---"
            else:
                marker = wib_dt.strftime("%d %b") + " ---"
            if marker != current_marker:
                if event_text:
                    event_text += "\n"
                event_text += "**" + marker + "**\n"
                current_marker = marker
            time_s = wib_dt.strftime("%H:%M WIB")
        else:
            time_s = "-"
        impact = e.get("impact", "")
        badge = "HIGH" if impact == "High" else ("MED" if impact == "Medium" else "LOW")
        title = e.get("title", "Unknown")
        forecast_s = str(e.get("forecast") or "-").strip() or "-"
        previous_s = str(e.get("previous") or "-").strip() or "-"
        actual_s = str(e.get("actual") or "-").strip() or "-"
        event_text += "`" + time_s + "` **" + title + "** [" + badge + "]\n"
        data_line = "  Forecast: " + forecast_s + " | Previous: " + previous_s
        if actual_s != "-":
            data_line += " | Actual: " + actual_s
        event_text += data_line + "\n\n"
    for ci, chunk in enumerate(split_text(event_text, 1024)):
        fn = "Event Ekonomi USD" if ci == 0 else "Event Ekonomi USD (" + str(ci + 1) + ")"
        emb.add_field(name=fn, value=chunk, inline=False)
    if ai_text:
        for i, chunk in enumerate(split_text(ai_text, 1024)):
            fn = "Analisis Makroekonomi" if i == 0 else "Analisis Makroekonomi (" + str(i + 1) + ")"
            emb.add_field(name=fn, value=chunk, inline=False)
    return [emb]


def build_realtime_embed(event, ai_text):
    impact = event.get("impact", "")
    if impact == "High":
        impact_str, color = "HIGH", RED
    elif impact == "Medium":
        impact_str, color = "MEDIUM", ORANGE
    else:
        impact_str, color = "LOW", ORANGE
    title = event.get("title", "Unknown")
    country = event.get("country", "")
    emb = discord.Embed(title="Data Ekonomi Baru Dirilis", description="**" + title + "** (" + country + ") [" + impact_str + "]", color=color)
    wib_dt = _utc_to_wib(event.get("date", ""))
    time_wib = wib_dt.strftime("%d %b %Y %H:%M WIB") if wib_dt else event.get("date", "")
    actual_s = str(event.get("actual") or "-").strip() or "-"
    forecast_s = str(event.get("forecast") or "-").strip() or "-"
    previous_s = str(event.get("previous") or "-").strip() or "-"
    data_val = "**Waktu:** " + time_wib + "\n**Actual:** " + actual_s + "\n**Forecast:** " + forecast_s + "\n**Previous:** " + previous_s
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
    if command_lock.locked():
        await ctx.send("Sedang ada report yang sedang diproses. Tunggu sebentar...")
        return
    async with command_lock:
        loading = await ctx.send("Mengumpulkan data dan menganalisa pasar...")
        try:
            loop = asyncio.get_running_loop()
            btc, gd, fg, news, (gainers, losers) = await asyncio.gather(
                loop.run_in_executor(None, get_btc_data),
                loop.run_in_executor(None, get_global_data),
                loop.run_in_executor(None, get_fear_greed),
                loop.run_in_executor(None, get_news),
                loop.run_in_executor(None, get_top_movers),
            )
            news_enriched = await loop.run_in_executor(None, generate_news_descriptions, news)
            if news_enriched:
                news = news_enriched
            await asyncio.sleep(3)
            ai = await loop.run_in_executor(None, get_ai_analysis, btc, gd, fg, news, gainers, losers)
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
    if command_lock.locked():
        await ctx.send("Sedang ada macro yang sedang diproses. Tunggu sebentar...")
        return
    async with command_lock:
        loading = await ctx.send("Mengumpulkan data kalender ekonomi...")
        try:
            loop = asyncio.get_running_loop()
            all_events, imf_cpi = await asyncio.gather(
                loop.run_in_executor(None, get_ff_events, True),
                loop.run_in_executor(None, get_imf_cpi),
            )
            usd_events = [e for e in all_events if e.get("country") == "USD"] if all_events else []
            ai_text = ""
            if usd_events:
                ai_text = await loop.run_in_executor(None, get_macro_analysis, usd_events)
            embeds = build_macro_embed(usd_events, ai_text, imf_cpi)
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
            loop = asyncio.get_running_loop()
            btc, gd, fg, news, (gainers, losers) = await asyncio.gather(
                loop.run_in_executor(None, get_btc_data),
                loop.run_in_executor(None, get_global_data),
                loop.run_in_executor(None, get_fear_greed),
                loop.run_in_executor(None, get_news),
                loop.run_in_executor(None, get_top_movers),
            )
            news_enriched = await loop.run_in_executor(None, generate_news_descriptions, news)
            if news_enriched:
                news = news_enriched
            await asyncio.sleep(3)
            ai = await loop.run_in_executor(None, get_ai_analysis, btc, gd, fg, news, gainers, losers)
            for emb in build_report_embeds(btc, gd, fg, news, gainers, losers, ai):
                await channel.send(embed=emb)
            await asyncio.sleep(3)
            all_events, imf_cpi = await asyncio.gather(
                loop.run_in_executor(None, get_ff_events, False),
                loop.run_in_executor(None, get_imf_cpi),
            )
            usd_events = [e for e in all_events if e.get("country") == "USD"] if all_events else []
            ai_text = ""
            if usd_events:
                ai_text = await loop.run_in_executor(None, get_macro_analysis, usd_events)
            for emb in build_macro_embed(usd_events, ai_text, imf_cpi):
                await channel.send(embed=emb)
            print("[Auto] Post completed at " + now.strftime("%Y-%m-%d %H:%M"))
        except Exception as e:
            print("[Auto Error] " + str(e))


# ======================== REALTIME MONITOR ========================


async def realtime_monitor():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            loop = asyncio.get_running_loop()
            events = await loop.run_in_executor(None, get_ff_events)
            if events:
                for e in events:
                    actual = e.get("actual")
                    if not actual or str(actual).strip() in ["-", "", "None"]:
                        continue
                    title = e.get("title", "")
                    country = e.get("country", "")
                    event_id = title + "_" + country + "_" + str(actual)
                    if event_id in last_alerted:
                        continue
                    last_alerted.add(event_id)
                    if len(last_alerted) > 500:
                        for x in list(last_alerted)[:250]:
                            last_alerted.discard(x)
                    channel = bot.get_channel(CHANNEL_ID)
                    if channel:
                        ai_text = await loop.run_in_executor(None, get_realtime_alert, e)
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
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set!")
    if not GEMINI_API_KEY:
        print("WARNING: GEMINI_API_KEY not set!")
    bot.run(DISCORD_TOKEN)
