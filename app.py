#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import base64
import binascii
import hashlib
import hmac
import threading
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT, "static")
ASTOCK_DIR = os.path.join(ROOT, "vendor")
if not os.path.exists(os.path.join(ASTOCK_DIR, "astock.py")):
    ASTOCK_DIR = os.path.abspath(os.path.join(ROOT, "..", "a-stock-data", "scripts"))
sys.path.insert(0, ASTOCK_DIR)

import astock  # noqa: E402

CANDIDATE_CACHE = {"time": 0, "data": None}
GLOBAL_CACHE = {"time": 0, "data": None, "last_success": None}
LEADERSHIP_CACHE = {"time": 0, "data": None}
REQUEST_LOG = defaultdict(deque)
RATE_LIMIT = int(os.environ.get("DASHBOARD_RATE_LIMIT", "40"))
MAX_CONCURRENT_ANALYSES = int(os.environ.get("DASHBOARD_MAX_CONCURRENT_ANALYSES", "3"))
REQUEST_LOG_LOCK = threading.Lock()
ANALYZE_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_ANALYSES)


def decode(payload):
    data = json.loads(payload)
    if isinstance(data, dict) and data.get("error"):
        raise ValueError(data["error"])
    return data


def price_tick(price):
    return 0.01 if price < 1000 else 0.1


def rounded(price, tick):
    return round(round(price / tick) * tick, 2 if tick == 0.01 else 1)


def first_value(row, keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def build_30m_bars(minute_rows):
    """Aggregate one/five-minute quote rows into A-share session 30-minute bars."""
    buckets = {}
    for row in minute_rows if isinstance(minute_rows, list) else []:
        raw_time = str(first_value(row, ("\u65f6\u95f4", "day", "time", "datetime"), ""))
        if not raw_time:
            continue
        try:
            stamp = datetime.fromisoformat(raw_time.replace("/", "-"))
        except ValueError:
            try:
                stamp = datetime.strptime(raw_time[-5:], "%H:%M").replace(
                    year=datetime.now().year, month=datetime.now().month, day=datetime.now().day
                )
            except ValueError:
                continue
        minute_of_day = stamp.hour * 60 + stamp.minute
        if 570 <= minute_of_day <= 690:
            session_start = 570
        elif 780 <= minute_of_day <= 900:
            session_start = 780
        else:
            continue
        offset = max(0, minute_of_day - session_start - (1 if minute_of_day > session_start else 0))
        bucket_index = min(3, offset // 30)
        end_minute = session_start + (bucket_index + 1) * 30
        bucket_end = stamp.replace(hour=end_minute // 60, minute=end_minute % 60, second=0, microsecond=0)

        price = float(first_value(row, ("\u4ef7\u683c", "close", "\u6536\u76d8"), 0) or 0)
        open_price = float(first_value(row, ("\u5f00\u76d8", "open"), price) or price)
        high = float(first_value(row, ("\u6700\u9ad8", "high"), price) or price)
        low = float(first_value(row, ("\u6700\u4f4e", "low"), price) or price)
        volume = float(first_value(row, ("\u6210\u4ea4\u91cf", "volume"), 0) or 0)
        if price <= 0:
            continue
        key = bucket_end.isoformat(timespec="minutes")
        bar = buckets.get(key)
        if bar is None:
            buckets[key] = {
                "end": key, "open": open_price, "high": high,
                "low": low, "close": price, "volume": volume,
            }
        else:
            bar["high"] = max(bar["high"], high, price)
            bar["low"] = min(bar["low"], low, price)
            bar["close"] = price
            bar["volume"] += volume

    now = datetime.now().astimezone().replace(tzinfo=None)
    bars = sorted(buckets.values(), key=lambda item: item["end"])
    for bar in bars:
        bar["completed"] = datetime.fromisoformat(bar["end"]) <= now
        for key in ("open", "high", "low", "close"):
            bar[key] = round(bar[key], 3)
    return bars


def relevant_market(symbol, market):
    indices = market.get("指数", {})
    if symbol.startswith(("300", "301")):
        name = "创业板指"
    elif symbol.startswith("688"):
        name = "科创50"
    elif symbol.startswith(("000", "001", "002", "003")):
        name = "深证成指"
    else:
        name = "上证指数"
    item = indices.get(name, {})
    raw = str(item.get("涨跌幅", "0")).replace("%", "")
    try:
        change = float(raw)
    except ValueError:
        change = 0.0
    return {"name": name, "change_pct": change, "healthy": change > -1.5}


def get_global_environment():
    now = time.time()
    if GLOBAL_CACHE["data"] is not None and now - GLOBAL_CACHE["time"] < 60:
        return GLOBAL_CACHE["data"]
    secids = "100.DJIA,100.NDX,100.SPX,124.HSI,124.HSTECH,100.N225,100.KS11"
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?"
           "secids=%s&fields=f2,f3,f12,f14" % secids)
    try:
        payload = astock._fetch_json(url)
        indices = []
        weights = {"NDX": 1.35, "HSTECH": 1.35, "HSI": 1.15, "SPX": 1.0, "KS11": 0.9, "DJIA": 0.8, "N225": 0.8}
        for item in payload.get("data", {}).get("diff", []):
            code = str(item.get("f12", ""))
            price = float(item.get("f2") or 0) / 100
            change = float(item.get("f3") or 0) / 100
            if price <= 0 or code not in weights:
                continue
            indices.append({
                "code": code, "name": item.get("f14", code),
                "price": round(price, 2), "change_pct": round(change, 2),
                "weight": weights[code],
            })
        if not indices:
            raise ValueError("外围指数返回空数据")
        weighted_average = sum(item["change_pct"] * item["weight"] for item in indices) / sum(item["weight"] for item in indices)
        core_changes = [item["change_pct"] for item in indices if item["code"] in {"NDX", "SPX", "HSI", "HSTECH"}]
        core_worst = min(core_changes) if core_changes else 0
        negative = sum(1 for item in indices if item["change_pct"] < 0)
        positive = sum(1 for item in indices if item["change_pct"] > 0)
        if core_worst <= -2.5 or weighted_average <= -1.0:
            level, tone, advice, multiplier = "外围风险偏高", "risk", "暂停新开仓，等待A股自身结构确认", 0.0
        elif weighted_average <= -0.3 or negative >= 4:
            level, tone, advice, multiplier = "外围偏弱", "weak", "新仓风险预算减半，成长股从严", 0.5
        elif weighted_average >= 0.5 and positive >= 4:
            level, tone, advice, multiplier = "外围偏暖", "strong", "不构成买点，仅解除外围风险压制", 1.0
        else:
            level, tone, advice, multiplier = "外围中性", "neutral", "按A股大盘和行业结构执行", 1.0
        result = {
            "available": True, "indices": indices, "level": level, "tone": tone,
            "advice": advice, "healthy": tone != "risk", "risk_multiplier": multiplier,
            "weighted_change_pct": round(weighted_average, 2),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "notice": "美股数据可能对应上一交易时段；外围只用于调整新仓权限，不直接产生买卖信号。",
        }
    except Exception as exc:
        if GLOBAL_CACHE["last_success"] is not None:
            result = dict(GLOBAL_CACHE["last_success"])
            result.update({"stale": True, "notice": "外围接口暂时断开，当前保留最近一次成功数据。", "error": str(exc)})
        else:
            result = {
                "available": False, "indices": [], "level": "外围数据暂不可用", "tone": "neutral",
                "advice": "不因缺失数据自动放宽条件", "healthy": True, "risk_multiplier": 0.5,
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "error": str(exc),
            }
    if result.get("available") and not result.get("stale"):
        GLOBAL_CACHE["last_success"] = result
    GLOBAL_CACHE.update({"time": now, "data": result})
    return result


def get_market_leadership():
    now = time.time()
    if LEADERSHIP_CACHE["data"] is not None and now - LEADERSHIP_CACHE["time"] < 60:
        return LEADERSHIP_CACHE["data"]

    sector_payload = decode(astock.get_sectors())
    sector_rows = sector_payload.get("sectors", []) if isinstance(sector_payload, dict) else []
    sectors = []
    for row in sector_rows:
        name = str(row.get("板块名称", ""))
        leader = str(row.get("领涨股票", ""))
        if not name or "ST" in leader.upper():
            continue
        sectors.append({
            "name": name,
            "change_pct": round(float(row.get("涨跌幅") or 0), 2),
            "leader": leader,
            "leader_symbol": str(row.get("领涨股代码", "")).zfill(6),
            "amount": float(row.get("成交额") or 0),
            "turnover_pct": round(float(row.get("换手率") or 0), 2),
            "main_net_inflow": float(row.get("主力净流入") or 0),
            "up_count": int(float(row.get("上涨家数") or 0)),
            "down_count": int(float(row.get("下跌家数") or 0)),
        })
    sectors.sort(key=lambda item: item["change_pct"], reverse=True)
    top_sectors = sectors[:5]
    top_change = top_sectors[0]["change_pct"] if top_sectors else 0
    top3_average = sum(item["change_pct"] for item in top_sectors[:3]) / min(3, len(top_sectors)) if top_sectors else 0
    theme_keywords = {
        "文化传媒": ("媒体", "传媒", "影视", "出版", "广告", "游戏", "院线"),
        "科技成长": ("半导体", "电子", "元件", "通信", "软件", "计算机", "人工智能", "机器人"),
        "大金融": ("银行", "证券", "保险", "多元金融"),
        "大消费": ("食品", "零食", "饮料", "白酒", "家电", "旅游", "酒店"),
        "新能源": ("光伏", "电池", "风电", "储能", "新能源"),
        "医药医疗": ("医药", "医疗", "生物", "中药"),
    }
    theme_groups = {}
    for sector in top_sectors:
        for theme, keywords in theme_keywords.items():
            if any(keyword in sector["name"] for keyword in keywords):
                theme_groups.setdefault(theme, []).append(sector)
                break
    dominant_theme = None
    if theme_groups:
        dominant_theme = max(theme_groups.items(), key=lambda pair: (len(pair[1]), sum(item["change_pct"] for item in pair[1])))
    theme_count = len(dominant_theme[1]) if dominant_theme else 0
    theme_average = sum(item["change_pct"] for item in dominant_theme[1]) / theme_count if theme_count else 0

    if theme_count >= 2 and theme_average >= 2.0:
        line_status, line_label, line_tone = "CLEAR", dominant_theme[0], "strong"
        line_summary = "多个相关行业同步走强，主线相对清晰。"
    elif theme_count >= 2 and theme_average >= 1.5:
        line_status, line_label, line_tone = "EMERGING", dominant_theme[0], "watch"
        line_summary = "相关行业开始共振，仍需观察持续性与行业RS。"
    elif top_change >= 3.0:
        line_status, line_label, line_tone = "EMERGING", top_sectors[0]["name"], "watch"
        line_summary = "单一行业领涨明显，但尚未形成相关行业共振。"
    else:
        line_status, line_label, line_tone = "ROTATION", "快速轮动", "neutral"
        line_summary = "行业强度分散，暂未形成一致主线，不追逐单日涨幅。"

    heat_sectors = []
    for sector in sectors:
        breadth = sector["up_count"] + sector["down_count"]
        breadth_activity = min(breadth, 100) / 25.0
        flow_activity = max(0.0, len(str(int(abs(sector["main_net_inflow"])))) - 5) if sector["main_net_inflow"] else 0.0
        heat_score = abs(sector["change_pct"]) * 1.8 + sector["turnover_pct"] * 0.6 + breadth_activity + flow_activity
        item = dict(sector)
        item["heat_score"] = round(heat_score, 2)
        heat_sectors.append(item)
    heat_sectors.sort(key=lambda item: item["heat_score"], reverse=True)
    heat_sectors = heat_sectors[:15]
    if heat_sectors:
        maximum_heat = max(item["heat_score"] for item in heat_sectors) or 1
        for index, item in enumerate(heat_sectors):
            relative = item["heat_score"] / maximum_heat
            item["weight"] = 4 if index == 0 else (3 if relative >= 0.72 else (2 if relative >= 0.48 else 1))

    flow_url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1"
                "&fltt=2&invt=2&fid=f62&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
                "&fields=f12,f14,f2,f3,f8,f62,f184")
    top_flow = []
    flow_error = None
    try:
        flow_payload = astock._fetch_json(flow_url)
        for row in flow_payload.get("data", {}).get("diff", []):
            symbol = str(row.get("f12", "")).zfill(6)
            name = str(row.get("f14", symbol))
            if not symbol.isdigit() or "ST" in name.upper():
                continue
            top_flow.append({
                "symbol": symbol, "name": name,
                "price": float(row.get("f2") or 0),
                "change_pct": round(float(row.get("f3") or 0), 2),
                "turnover_pct": round(float(row.get("f8") or 0), 2),
                "main_net_inflow": float(row.get("f62") or 0),
                "main_net_inflow_pct": round(float(row.get("f184") or 0), 2),
            })
    except Exception as exc:
        flow_error = str(exc)
        if LEADERSHIP_CACHE["data"] is not None:
            top_flow = LEADERSHIP_CACHE["data"].get("top_flow", [])

    result = {
        "mainline": {
            "status": line_status, "label": line_label, "tone": line_tone,
            "summary": line_summary, "top3_average_pct": round(top3_average, 2),
            "sectors": top_sectors,
        },
        "heat_sectors": heat_sectors,
        "top_flow": top_flow[:10],
        "flow_available": bool(top_flow),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "notice": (
            "资金榜接口暂时断开，当前保留最近一次成功榜单；热度板块仍按最新行业数据计算。"
            if flow_error else
            "主力净流入为成交分类统计，仅作辅助过滤；盘中数据未完成，不能单独作为买点。"
        ),
    }
    LEADERSHIP_CACHE.update({"time": now if top_flow else now - 55, "data": result})
    return result


def summarize_market(market):
    indices = market.get("指数", {})
    changes = []
    for item in indices.values():
        try:
            changes.append(float(str(item.get("涨跌幅", "0")).replace("%", "")))
        except ValueError:
            pass
    if not changes:
        return {"level": "未知", "tone": "neutral", "advice": "等待行情数据", "position_cap": "--"}

    average = sum(changes) / len(changes)
    worst = min(changes)
    positive = sum(1 for value in changes if value > 0)
    if worst <= -2.5 or (average <= -1.5 and positive == 0):
        level, tone, advice, cap = "风险偏高", "risk", "停止追涨，优先处理弱势持仓", "0% - 30%"
    elif average <= -0.5 or worst <= -1.5:
        level, tone, advice, cap = "偏弱震荡", "weak", "新仓风险减半，只做强板块回踩", "30% - 50%"
    elif average >= 0.8 and positive >= 3:
        level, tone, advice, cap = "市场偏强", "strong", "按计划参与，仍需遵守追涨限制", "50% - 80%"
    elif average >= 0 and positive >= 2:
        level, tone, advice, cap = "震荡偏强", "steady", "控制节奏，优先三层同向标的", "40% - 60%"
    else:
        level, tone, advice, cap = "震荡分化", "neutral", "等待方向确认，避免临盘追热点", "30% - 50%"
    return {
        "level": level,
        "tone": tone,
        "advice": advice,
        "position_cap": cap,
        "average_change_pct": round(average, 2),
        "worst_change_pct": round(worst, 2),
    }


def analyze_v5_compatible(symbol):
    """Use V4 analysis, with a daily-only fallback for newer ETFs lacking 80 weekly bars."""
    try:
        return decode(astock.analyze_v4(symbol))
    except Exception as exc:
        if "80" not in str(exc):
            raise
    rows = astock._parse_klines(astock.get_kline(symbol, "daily", 120))
    if len(rows) < 26:
        raise ValueError("日线历史不足26根，暂时无法计算基準线")
    cloud_data_sufficient = len(rows) >= 80
    if cloud_data_sufficient:
        snapshot = astock._ichimoku_snapshot(rows)
    else:
        end = len(rows) - 1
        tenkan = astock._midpoint(rows, end, min(9, len(rows)))
        kijun = astock._midpoint(rows, end, 26)
        previous_kijun = astock._midpoint(rows, max(25, end - 5), 26)
        snapshot = {
            "tenkan": tenkan, "kijun": kijun,
            "kijun_direction": "上行" if kijun > previous_kijun else ("下行" if kijun < previous_kijun else "走平"),
            "cloud_top": kijun, "future_cloud_bullish": False,
        }
    last = rows[-1]
    true_ranges = []
    for index in range(len(rows) - 14, len(rows)):
        previous_close = rows[index - 1]["close"]
        true_ranges.append(max(
            rows[index]["high"] - rows[index]["low"],
            abs(rows[index]["high"] - previous_close),
            abs(rows[index]["low"] - previous_close),
        ))
    atr = sum(true_ranges) / len(true_ranges)
    distance = (last["close"] - snapshot["kijun"]) / atr if atr else 0
    return_10d = (last["close"] / rows[-11]["close"] - 1) * 100
    average_volume = sum(row["volume"] for row in rows[-21:-1]) / 20
    volume_ratio = last["volume"] / average_volume if average_volume else 0
    above_cloud = cloud_data_sufficient and last["close"] > snapshot["cloud_top"]
    chikou = len(rows) >= 27 and last["close"] > rows[-27]["close"]
    overheated = distance > 2 or return_10d > 20
    return {
        "symbol": symbol, "as_of": last["date"], "bar_status": "已完成",
        "price": round(last["close"], 3),
        "daily": {
            "tenkan_9": round(snapshot["tenkan"], 3), "kijun_26": round(snapshot["kijun"], 3),
            "kijun_direction": snapshot["kijun_direction"], "cloud_top": round(snapshot["cloud_top"], 3),
            "above_cloud": above_cloud, "future_cloud_bullish": snapshot["future_cloud_bullish"],
            "chikou_confirmed": chikou, "atr_14": round(atr, 3),
            "distance_from_kijun_atr": round(distance, 3), "return_10d_pct": round(return_10d, 2),
            "volume_vs_20d": round(volume_ratio, 2),
        },
        "weekly_trend_confirmed": False, "weekly_data_sufficient": False,
        "cloud_data_sufficient": cloud_data_sufficient,
        "three_roles_bullish": False, "overheated": overheated,
        "verdict": "ETF周线历史不足，仅提供日线持仓风控，不生成新开仓许可。",
        "limitations": ["周线历史不足80根", "完整云层数据不足" if not cloud_data_sufficient else "周线数据不足", "仅用于已有持仓的日线结构评估"],
    }


def analyze_previous_close(symbol):
    """Build the candidate snapshot strictly from bars dated before today."""
    daily = astock._parse_klines(astock.get_kline(symbol, "daily", 120))
    today = datetime.now().date()
    daily = [row for row in daily if datetime.fromisoformat(row["date"][:10]).date() < today]
    if len(daily) < 80:
        raise ValueError("昨收日线历史不足80根")

    weekly = astock._parse_klines(astock.get_kline(symbol, "weekly", 100))
    current_monday = today.fromordinal(today.toordinal() - today.weekday())
    weekly = [row for row in weekly if datetime.fromisoformat(row["date"][:10]).date() < current_monday]
    if len(weekly) < 80:
        raise ValueError("已完成周线历史不足80根")

    daily_snapshot = astock._ichimoku_snapshot(daily)
    weekly_snapshot = astock._ichimoku_snapshot(weekly)
    last = daily[-1]
    true_ranges = []
    for index in range(len(daily) - 14, len(daily)):
        previous_close = daily[index - 1]["close"]
        true_ranges.append(max(
            daily[index]["high"] - daily[index]["low"],
            abs(daily[index]["high"] - previous_close),
            abs(daily[index]["low"] - previous_close),
        ))
    atr = sum(true_ranges) / len(true_ranges)
    distance = (last["close"] - daily_snapshot["kijun"]) / atr if atr else 0
    return_10d = (last["close"] / daily[-11]["close"] - 1) * 100
    average_volume = sum(row["volume"] for row in daily[-21:-1]) / 20
    volume_ratio = last["volume"] / average_volume if average_volume else 0
    above_cloud = last["close"] > daily_snapshot["cloud_top"]
    tk_bullish = daily_snapshot["tenkan"] >= daily_snapshot["kijun"]
    chikou = last["close"] > daily[-27]["close"]
    weekly_bullish = weekly[-1]["close"] > weekly_snapshot["cloud_top"] and weekly_snapshot["tenkan"] >= weekly_snapshot["kijun"]
    overheated = distance > 2 or return_10d > 20
    three_roles = all((
        above_cloud, tk_bullish, chikou,
        daily_snapshot["future_cloud_bullish"], daily_snapshot["kijun_direction"] != "下行",
    ))
    return {
        "symbol": symbol, "as_of": last["date"][:10], "bar_status": "已完成",
        "price": round(last["close"], 3),
        "daily": {
            "tenkan_9": round(daily_snapshot["tenkan"], 3),
            "kijun_26": round(daily_snapshot["kijun"], 3),
            "kijun_direction": daily_snapshot["kijun_direction"],
            "cloud_top": round(daily_snapshot["cloud_top"], 3),
            "above_cloud": above_cloud,
            "future_cloud_bullish": daily_snapshot["future_cloud_bullish"],
            "chikou_confirmed": chikou,
            "atr_14": round(atr, 3),
            "distance_from_kijun_atr": round(distance, 3),
            "return_10d_pct": round(return_10d, 2),
            "volume_vs_20d": round(volume_ratio, 2),
        },
        "weekly_trend_confirmed": weekly_bullish,
        "three_roles_bullish": three_roles,
        "overheated": overheated,
    }


def normalize_quote_scale(quote, analysis):
    """Correct public quote endpoints that occasionally return ETF prices at 10x scale."""
    quote_price = float(quote.get("最新价") or 0)
    reference = float(analysis.get("price") or 0)
    if quote_price <= 0 or reference <= 0:
        return quote
    ratio = quote_price / reference
    factor = 10 if 8 <= ratio <= 12 else (100 if 80 <= ratio <= 120 else 1)
    if factor == 1:
        return quote
    normalized = dict(quote)
    for key in ("最新价", "最高", "最低", "今开", "昨收", "涨跌额"):
        value = normalized.get(key)
        if value not in (None, ""):
            normalized[key] = round(float(value) / factor, 3)
    normalized["价格缩放修正"] = "公开接口ETF报价按%s倍缩放，已用日线收盘价校正" % factor
    return normalized


def score_candidate(stock, market, global_env):
    symbol = str(stock.get("代码", "")).zfill(6)
    name = str(stock.get("名称", symbol))
    if not symbol.isdigit() or len(symbol) != 6:
        return None
    if any(flag in name.upper() for flag in ("ST", "N", "退")):
        return None
    change = float(stock.get("涨跌幅") or 0)

    analysis = analyze_previous_close(symbol)
    daily = analysis["daily"]
    market_state = relevant_market(symbol, market)
    distance = float(daily.get("distance_from_kijun_atr") or 0)
    volume = float(daily.get("volume_vs_20d") or 0)
    required = (
        analysis.get("weekly_trend_confirmed")
        and daily.get("above_cloud")
        and daily.get("kijun_direction") != "下行"
        and not analysis.get("overheated")
        and analysis.get("bar_status") == "已完成"
        and distance >= -0.2
        and distance <= 1.5
    )
    if not required:
        return None

    score = 0
    score += 2 if analysis.get("weekly_trend_confirmed") else 0
    score += 2 if daily.get("above_cloud") else 0
    score += 2 if daily.get("kijun_direction") == "上行" else 1
    score += 1 if analysis.get("three_roles_bullish") else 0
    score += 1 if -0.2 <= distance <= 0.8 else 0
    score += 1 if 0.8 <= volume <= 2.5 else 0
    score += 1 if daily.get("future_cloud_bullish") else 0

    atr = float(daily["atr_14"])
    kijun = float(daily["kijun_26"])
    tick = price_tick(float(stock.get("最新价") or analysis["price"]))
    zone_low = rounded(kijun - 0.10 * atr, tick)
    zone_high = rounded(kijun + 0.15 * atr, tick)
    environment_allowed = bool(market_state["healthy"] and global_env["healthy"])
    price_tradable = -8.0 < change < 8.0
    today_allowed = environment_allowed and price_tradable
    if not price_tradable:
        execution_note = "今日极端涨跌/跳空，计划暂停"
    elif not environment_allowed:
        execution_note = "今日环境否决，暂停执行"
    else:
        execution_note = "今日等待30分钟触发"
    return {
        "symbol": symbol,
        "name": name,
        "price": float(stock.get("最新价") or analysis["price"]),
        "change_pct": round(change, 2),
        "score": score,
        "bar_status": analysis.get("bar_status"),
        "watch_zone": {"low": zone_low, "high": zone_high},
        "distance_atr": round(distance, 2),
        "market": market_state["name"],
        "three_roles": bool(analysis.get("three_roles_bullish")),
        "basis_date": analysis.get("as_of"),
        "basis": "previous_close",
        "today_allowed": today_allowed,
        "execution_note": execution_note,
    }


def get_candidates():
    now = time.time()
    if CANDIDATE_CACHE["data"] is not None and now - CANDIDATE_CACHE["time"] < 300:
        return CANDIDATE_CACHE["data"]

    market = decode(astock.get_market_overview())
    global_env = get_global_environment()
    screened = decode(astock.screen_stocks("amount>500000000 turnover>0.3"))
    stocks = screened.get("results", [])[:24]
    candidates = []
    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = [pool.submit(score_candidate, stock, market, global_env) for stock in stocks]
        for future in futures:
            try:
                item = future.result()
                if item:
                    candidates.append(item)
            except Exception:
                continue
    candidates.sort(key=lambda item: (item["score"], -abs(item["distance_atr"])), reverse=True)
    result = {
        "candidates": candidates[:6],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provisional": False,
        "basis": "previous_close",
        "global_environment": global_env,
        "notice": "高流动性样本池仅用于控制扫描量；入选、评分和观察区间严格使用昨收日线。今日大盘、外围和30分钟只控制执行；行业RS、公告、解禁和有效RR仍需复核。",
    }
    CANDIDATE_CACHE.update({"time": now, "data": result})
    return result


def build_plan(symbol, quote, analysis, market, global_env, klines, flow):
    daily = analysis["daily"]
    live_price = float(quote.get("最新价") or analysis["price"])
    atr = float(daily["atr_14"])
    kijun = float(daily["kijun_26"])
    tenkan = float(daily["tenkan_9"])
    cloud_top = float(daily["cloud_top"])
    tick = price_tick(live_price)
    market_state = relevant_market(symbol, market)

    completed = analysis.get("bar_status") == "已完成"
    structural_ok = (
        analysis.get("weekly_trend_confirmed")
        and daily.get("above_cloud")
        and daily.get("kijun_direction") != "下行"
        and not analysis.get("overheated")
    )
    eligible = structural_ok and market_state["healthy"] and global_env["healthy"] and completed

    entry_low = rounded(kijun - 0.10 * atr, tick)
    entry_high = rounded(kijun + 0.15 * atr, tick)
    entry_mid = rounded((entry_low + entry_high) / 2.0, tick)
    recent_lows = [float(row["最低"]) for row in klines[-10:]]
    support_candidates = [cloud_top, kijun - 0.75 * atr, min(recent_lows)]
    support = max(value for value in support_candidates if value < entry_mid)
    stop = rounded(support - 0.10 * atr, tick)
    if stop >= entry_low:
        stop = rounded(entry_low - 0.65 * atr, tick)
    risk = max(entry_mid - stop, tick)
    targets = {
        "t1": rounded(entry_mid + risk, tick),
        "t2": rounded(entry_mid + 2 * risk, tick),
        "t3": rounded(entry_mid + 3 * risk, tick),
    }
    chase_ceiling = rounded(min(kijun + 1.5 * atr, entry_high + 0.35 * atr), tick)

    reasons = []
    if not completed:
        reasons.append("日线尚未收盘确认")
    if not analysis.get("weekly_trend_confirmed"):
        reasons.append("周线趋势未确认")
    if not daily.get("above_cloud"):
        reasons.append("价格仍在日线云层下方")
    if daily.get("kijun_direction") == "下行":
        reasons.append("基準线仍在下行")
    if analysis.get("overheated"):
        reasons.append("10日涨幅或距基準线触发追涨限制")
    if not market_state["healthy"]:
        reasons.append("对应市场指数跌幅超过风险阈值")
    if not global_env["healthy"]:
        reasons.append("外围市场处于高风险状态，暂停新开仓")

    flow_rows = flow.get("data", []) if isinstance(flow, dict) else []
    flow_5d = sum(float(row.get("主力净流入", 0)) for row in flow_rows[-5:])
    flow_positive = flow_5d > 0

    if eligible and live_price <= chase_ceiling:
        status = "CONDITIONAL_BUY"
        label = "符合条件，等待价位"
        summary = "日线结构通过，等待已完成30分钟K线触发；不触发不买。"
    elif eligible:
        status = "WAIT_PULLBACK"
        label = "等待回踩"
        summary = "结构通过，但实时价格超过允许追价上限。"
        reasons.append("实时价格高于允许追价上限")
    else:
        status = "NO_BUY"
        label = "暂不买入"
        summary = "当前未通过V5.0日线计划条件，30分钟没有开仓权限。"

    return {
        "version": "V5.0",
        "status": status,
        "label": label,
        "summary": summary,
        "entry": {"low": entry_low, "high": entry_high, "trigger": "只等待已完成30分钟K线触发"},
        "stop": stop,
        "targets": targets,
        "risk_per_share": rounded(risk, tick),
        "chase_ceiling": chase_ceiling,
        "reasons": reasons,
        "market": market_state,
        "flow_5d": round(flow_5d, 2),
        "flow_positive": flow_positive,
        "daily_signal": (
            "三役共振" if analysis.get("three_roles_bullish") else
            "云层上方回踩" if daily.get("above_cloud") else "无有效日线信号"
        ),
        "position": {
            "account_risk_pct": round(0.75 * global_env.get("risk_multiplier", 1.0), 2),
            "formula": "股数 = 账户资金 × 风险预算 ÷ (实际买价 - 硬止损价)",
            "industry_rs": "待复核",
        },
        "exit": {
            "hard_stop": stop,
            "daily_structure": rounded(max(kijun, cloud_top), tick),
            "tracking_fast": rounded(tenkan, tick),
            "tracking_slow": rounded(kijun, tick),
            "rules": [
                "触及硬止损立即执行，不等待日线收盘",
                "日线收盘确认结构破坏后退出",
                "1R后保护风险，2R/3R分批处理",
                "30分钟只预警，不单独推翻日线计划",
            ],
        },
        "checks": {
            "weekly": bool(analysis.get("weekly_trend_confirmed")),
            "above_cloud": bool(daily.get("above_cloud")),
            "kijun": daily.get("kijun_direction") != "下行",
            "not_overheated": not bool(analysis.get("overheated")),
            "market": market_state["healthy"],
            "global": global_env["healthy"],
            "completed": completed,
        },
    }


def build_execution(plan, minute_rows, tenkan):
    bars = build_30m_bars(minute_rows)
    completed = [bar for bar in bars if bar["completed"]]
    result = {
        "status": "WAIT_DAILY", "label": "等待日线计划",
        "message": "日线条件未通过，30分钟没有开仓权限。",
        "trigger_price": None, "effective_rr": None,
        "bars": bars[-8:],
        "rule": "30分钟只能触发、延迟或取消日线计划，不能创造新交易。",
    }
    if plan["status"] == "NO_BUY":
        return result
    if not completed:
        result.update({"status": "WAIT_DATA", "label": "等待30分钟收盘", "message": "暂无已完成的30分钟K线。"})
        return result

    latest = completed[-1]
    previous = completed[-2] if len(completed) >= 2 else None
    if latest["close"] <= plan["stop"]:
        result.update({"status": "CANCELLED", "label": "计划取消", "message": "价格已触及日线硬止损，禁止开新仓。"})
        return result
    if latest["close"] > plan["chase_ceiling"]:
        result.update({"status": "CANCELLED", "label": "超过追价上限", "message": "30分钟收盘价超过日线最高追价，等待新计划。"})
        return result

    touched = latest["low"] <= plan["entry"]["high"] and latest["high"] >= plan["entry"]["low"]
    if previous:
        touched = touched or (previous["low"] <= plan["entry"]["high"] and previous["high"] >= plan["entry"]["low"])
    reclaimed = latest["close"] >= tenkan and latest["close"] > latest["open"]
    higher_low_break = bool(previous and latest["low"] > previous["low"] and latest["close"] > previous["high"])
    if not (touched and (reclaimed or higher_low_break)):
        result.update({
            "status": "WAIT_TRIGGER", "label": "等待30分钟触发",
            "message": "日线计划有效，但尚未出现回踩后收复转换线或更高低点突破。",
        })
        return result

    trigger_price = latest["close"]
    risk = trigger_price - plan["stop"]
    reward = plan["targets"]["t2"] - trigger_price
    effective_rr = reward / risk if risk > 0 else 0
    if effective_rr < 1.5:
        result.update({
            "status": "CANCELLED", "label": "RR不足，取消买入",
            "message": "30分钟虽触发，但按实际价格复算后的RR低于1.5。",
            "trigger_price": trigger_price, "effective_rr": round(effective_rr, 2),
        })
        return result

    position_pct = min(100.0, 0.75 / (risk / trigger_price * 100)) if trigger_price > 0 else 0
    result.update({
        "status": "TRIGGERED", "label": "30分钟已触发",
        "message": "触发有效；下单前仍需核对行业RS、公告和滑点。",
        "trigger_price": trigger_price, "effective_rr": round(effective_rr, 2),
        "risk_per_share": rounded(risk, price_tick(trigger_price)),
        "max_position_pct": round(position_pct, 1),
    })
    return result


def analyze_symbol(symbol):
    if not symbol.isdigit() or len(symbol) != 6:
        raise ValueError("请输入6位股票代码")

    with ThreadPoolExecutor(max_workers=6) as pool:
        quote_future = pool.submit(lambda: decode(astock.get_quote(symbol)))
        analysis_future = pool.submit(lambda: analyze_v5_compatible(symbol))
        market_future = pool.submit(lambda: decode(astock.get_market_overview()))
        kline_future = pool.submit(lambda: decode(astock.get_kline(symbol, "daily", 120)))
        flow_future = pool.submit(lambda: decode(astock.get_money_flow(symbol)))
        minute_future = pool.submit(lambda: decode(astock.get_minute(symbol)))
        global_future = pool.submit(get_global_environment)
        quote = quote_future.result()
        analysis = analysis_future.result()
        market = market_future.result()
        klines = kline_future.result()
        try:
            flow = flow_future.result()
        except Exception:
            flow = {"data": [], "error": "资金流暂不可用"}
        try:
            minute_rows = minute_future.result()
        except Exception:
            minute_rows = []
        global_env = global_future.result()

    quote = normalize_quote_scale(quote, analysis)
    plan = build_plan(symbol, quote, analysis, market, global_env, klines, flow)
    execution = build_execution(plan, minute_rows, float(analysis["daily"]["tenkan_9"]))
    live_price = float(quote.get("最新价") or analysis["price"])
    raw_change = str(quote.get("涨跌幅", "")).replace("%", "")
    try:
        change_pct = float(raw_change)
    except ValueError:
        previous = float(quote.get("昨收") or live_price)
        change_pct = (live_price / previous - 1) * 100 if previous else 0.0
    return {
        "symbol": symbol,
        "name": quote.get("名称", symbol),
        "quote": quote,
        "live_price": live_price,
        "change_pct": round(change_pct, 2),
        "analysis": analysis,
        "market": market,
        "market_summary": summarize_market(market),
        "global_environment": global_env,
        "flow": flow,
        "plan": plan,
        "execution": execution,
        "klines": klines[-70:],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "disclaimer": "规则化交易参考，不构成投资建议。公告、解禁、行业RS和账户风险预算需另行核对。",
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            self.send_login_page()
            return
        if parsed.path != "/api/health" and not self.is_authorized():
            if parsed.path.startswith("/api/"):
                self.send_json({"error": "请先登录"}, 401)
            else:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        if parsed.path.startswith("/api/") and parsed.path != "/api/health" and not self.within_rate_limit():
            self.send_json({"error": "请求过于频繁，请稍后再试"}, 429)
            return
        if parsed.path == "/api/analyze":
            query = parse_qs(parsed.query)
            symbol = query.get("symbol", [""])[0].strip()
            if not ANALYZE_SEMAPHORE.acquire(blocking=False):
                self.send_json({"error": "当前分析任务较多，请稍后重试"}, 503)
                return
            try:
                self.send_json(analyze_symbol(symbol), 200)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            finally:
                ANALYZE_SEMAPHORE.release()
            return
        if parsed.path == "/api/market":
            try:
                market = decode(astock.get_market_overview())
                self.send_json({
                    "market": market,
                    "summary": summarize_market(market),
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }, 200)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/global":
            try:
                self.send_json(get_global_environment(), 200)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/leadership":
            try:
                self.send_json(get_market_leadership(), 200)
            except Exception as exc:
                if LEADERSHIP_CACHE["data"] is not None:
                    cached = dict(LEADERSHIP_CACHE["data"])
                    cached["stale"] = True
                    cached["notice"] = "行情接口暂时断开，当前保留最近一次成功的主线与资金榜。"
                    self.send_json(cached, 200)
                else:
                    self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/candidates":
            try:
                self.send_json(get_candidates(), 200)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/health":
            self.send_json({"status": "ok"}, 200)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/login":
            self.send_json({"error": "不支持的请求"}, 404)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
        except ValueError:
            length = 0
        form = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        supplied = form.get("password", [""])[0]
        password = os.environ.get("DASHBOARD_PASSWORD", "")
        if password and hmac.compare_digest(supplied, password):
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                "dashboard_session=%s; Path=/; Max-Age=86400; HttpOnly; Secure; SameSite=Lax" % self.session_token(password),
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_login_page("密码不正确，请重新输入", 401)

    @staticmethod
    def session_token(password):
        return hmac.new(password.encode("utf-8"), b"a-stock-workbench-session", hashlib.sha256).hexdigest()

    def send_login_page(self, error="", status=200):
        error_html = '<p class="error">%s</p>' % error if error else ""
        body = ("""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<title>登录 · A股交易工作台</title><style>"
                "*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;"
                "background:#070b12;color:#e8edf5;font-family:system-ui,-apple-system,sans-serif;padding:24px}"
                ".card{width:min(420px,100%%);padding:30px;border:1px solid #263142;border-radius:18px;background:#101722}"
                "h1{font-size:24px;margin:0 0 8px}p{color:#96a3b6;line-height:1.6}.error{color:#ff7b83}"
                "input,button{width:100%%;height:48px;border-radius:10px;font-size:16px}"
                "input{margin:18px 0 12px;padding:0 14px;border:1px solid #344157;background:#080d15;color:#fff}"
                "button{border:0;background:#2d7dff;color:#fff;font-weight:700}small{display:block;margin-top:16px;color:#65738a}"
                "</style></head><body><main class=\"card\"><h1>A股交易工作台 V5.0</h1>"
                "<p>请输入访问密码后进入工作台。</p>%s"
                "<form method=\"post\" action=\"/login\"><input type=\"password\" name=\"password\" "
                "placeholder=\"访问密码\" autocomplete=\"current-password\" required autofocus>"
                "<button type=\"submit\">进入工作台</button></form>"
                "<small>规则化交易参考，不构成投资建议。</small></main></body></html>""") % error_html
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def is_authorized(self):
        password = os.environ.get("DASHBOARD_PASSWORD", "")
        if not password:
            return True
        cookies = self.headers.get("Cookie", "")
        for item in cookies.split(";"):
            key, sep, value = item.strip().partition("=")
            if sep and key == "dashboard_session":
                if hmac.compare_digest(value, self.session_token(password)):
                    return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            supplied = decoded.split(":", 1)[1]
        except (ValueError, UnicodeDecodeError, IndexError, binascii.Error):
            return False
        return hmac.compare_digest(supplied, password)

    def within_rate_limit(self):
        now = time.time()
        client_ip = self.client_address[0]
        with REQUEST_LOG_LOCK:
            requests = REQUEST_LOG[client_ip]
            while requests and requests[0] < now - 60:
                requests.popleft()
            if len(requests) >= RATE_LIMIT:
                return False
            requests.append(now)
            # Prevent a public deployment from retaining an unbounded number of IP keys.
            if len(REQUEST_LOG) > 5000:
                stale = [ip for ip, hits in REQUEST_LOG.items() if not hits or hits[-1] < now - 120]
                for ip in stale[:1000]:
                    REQUEST_LOG.pop(ip, None)
            return True

    def end_headers(self):
        if not any(key.lower() == "cache-control" for key, _ in self._headers_buffer_pairs()):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
        )
        super().end_headers()

    def _headers_buffer_pairs(self):
        pairs = []
        for line in getattr(self, "_headers_buffer", []):
            try:
                text = line.decode("latin-1")
                if ":" in text:
                    pairs.append(text.split(":", 1))
            except Exception:
                pass
        return pairs

    def send_json(self, payload, status):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


class LocalHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer calls getfqdn() during bind, which can stall on some Windows DNS setups.
        TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("ASTOCK_DASHBOARD_PORT", "8765")))
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    server = LocalHTTPServer((host, port), Handler)
    print("A股交易决策面板: http://%s:%s" % (host, port))
    server.serve_forever()
