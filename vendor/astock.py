#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
astock.py - A股实时数据访问模块
=============================
为 Codex 提供A股实时行情、历史K线、板块资金、龙虎榜等数据能力。
基于 akshare (开源免费) + 东方财富/新浪直接API (零依赖备用)。

Usage:
    python astock.py quote 600519          # 个股实时行情
    python astock.py market               # 大盘总览
    python astock.py kline 600519 daily 5 # 日K线近5天
    python astock.py screen pe<20 pb<3 marketcap>100  # 条件选股
    python astock.py sector               # 板块行情
    python astock.py toplist              # 龙虎榜
    python astock.py finance 600519       # 财务摘要
"""

import sys
import json
import argparse
import time
import os
import subprocess
from datetime import datetime, timedelta

# ============================================================
# 依赖检测
# ============================================================
try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    try:
        import urllib.request
        import urllib.parse
        HAS_REQUESTS = False
    except ImportError:
        HAS_REQUESTS = False


def _fetch_json(url, headers=None):
    """通用JSON请求（requests优先，urllib备用）"""
    default_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Connection": "close",
    }
    h = headers or default_headers
    if HAS_REQUESTS:
        last_error = None
        for attempt in range(3):
            try:
                r = requests.get(url, headers=h, timeout=20)
                r.raise_for_status()
                r.encoding = r.apparent_encoding
                return r.json()
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        if os.name == "nt":
            env = os.environ.copy()
            env["ASTOCK_FETCH_URL"] = url
            command = ("$ProgressPreference='SilentlyContinue'; "
                       "$r=Invoke-RestMethod -Uri $env:ASTOCK_FETCH_URL -TimeoutSec 20; "
                       "$r | ConvertTo-Json -Depth 20 -Compress")
            try:
                output = subprocess.check_output(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                    stderr=subprocess.DEVNULL,
                    env=env,
                    timeout=30,
                )
                return json.loads(output.decode("utf-8-sig"))
            except Exception:
                pass
        raise last_error
    else:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=15) as resp:
            charset = resp.headers.get_content_charset() or 'utf-8'
            return json.loads(resp.read().decode(charset))


def _fetch_text(url, headers=None):
    """通用文本请求（用于Sina等非JSON接口）"""
    default_headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn"
    }
    h = {**default_headers, **(headers or {})}
    if HAS_REQUESTS:
        r = requests.get(url, headers=h, timeout=15)
        r.encoding = 'gbk'
        return r.text
    else:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode('gbk', errors='replace')


def _json_output(data, label="data"):
    """统一JSON输出，方便Codex解析"""
    if isinstance(data, str):
        return data
    if hasattr(data, 'to_dict'):
        data = data.to_dict(orient='records')
    return json.dumps(data, ensure_ascii=False, default=str, indent=2)


def _validate_symbol(symbol: str) -> str:
    """Validate and normalize a six-digit A-share or ETF code."""
    symbol = symbol.strip()
    if not symbol.isdigit() or len(symbol) > 6:
        raise ValueError("股票代码必须是1至6位数字")
    return symbol.zfill(6)


# ============================================================
# 1. 个股实时行情
# ============================================================
def get_quote(symbol: str) -> str:
    """
    获取个股实时行情
    symbol: 6位股票代码，如 600519(贵州茅台)、000001(平安银行)
    """
    symbol = _validate_symbol(symbol)

    # Direct quote endpoints avoid downloading the entire A-share snapshot.
    try:
        return _quote_em_api(symbol)
    except Exception:
        return _quote_sina_api(symbol)


def _quote_em_api(symbol: str) -> str:
    """东方财富直接API获取实时行情"""
    if symbol.startswith(('5', '6', '9')):
        prefix = "1"
    else:
        prefix = "0"

    secid = f"{prefix}.{symbol}"
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f116,f117,f162,f167,f168,f170,f171,f292"

    data = _fetch_json(url)
    d = data.get("data", {})
    if not d:
        raise ValueError(f"Eastmoney returned empty data for {symbol}")
    result = {
        "代码": d.get("f57"),
        "名称": d.get("f58"),
        "最新价": d.get("f43", 0) / 100 if d.get("f43") else None,
        "最高": d.get("f44", 0) / 100 if d.get("f44") else None,
        "最低": d.get("f45", 0) / 100 if d.get("f45") else None,
        "今开": d.get("f46", 0) / 100 if d.get("f46") else None,
        "昨收": d.get("f60", 0) / 100 if d.get("f60") else None,
        "成交量(手)": d.get("f47"),
        "成交额": d.get("f48"),
        "市盈率": d.get("f162"),
        "市净率": d.get("f167"),
        "总市值": d.get("f116"),
        "流通市值": d.get("f117"),
        "换手率": d.get("f168"),
        "数据源": "东方财富",
        "更新时间": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if result["最新价"] is not None and result["昨收"]:
        result["涨跌额"] = round(result["最新价"] - result["昨收"], 2)
        result["涨跌幅"] = "%s%%" % round((result["最新价"] / result["昨收"] - 1) * 100, 2)
    return _json_output(result, "quote")


def _quote_sina_api(symbol: str) -> str:
    """新浪财经实时行情（最稳定的备用方案）"""
    if symbol.startswith(('5', '6', '9')):
        sina_symbol = f"sh{symbol}"
    else:
        sina_symbol = f"sz{symbol}"

    url = f"https://hq.sinajs.cn/list={sina_symbol}"
    text = _fetch_text(url)

    # 解析: var hq_str_sh600519="贵州茅台,1305.000,1292.010,..."
    import re
    match = re.search(r'="([^"]*)"', text)
    if not match or not match.group(1):
        return json.dumps({"error": f"无法获取{symbol}的行情数据"}, ensure_ascii=False)

    parts = match.group(1).split(",")
    if len(parts) < 10:
        return json.dumps({"error": f"行情数据格式异常: {symbol}"}, ensure_ascii=False)

    name = parts[0]
    open_price = float(parts[1]) if parts[1] else 0
    prev_close = float(parts[2]) if parts[2] else 0
    current = float(parts[3]) if parts[3] else 0
    high = float(parts[4]) if parts[4] else 0
    low = float(parts[5]) if parts[5] else 0

    change_pct = round((current - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
    change_amt = round(current - prev_close, 2) if prev_close > 0 else 0

    result = {
        "代码": symbol,
        "名称": name,
        "最新价": current,
        "今开": open_price,
        "昨收": prev_close,
        "最高": high,
        "最低": low,
        "成交量": int(float(parts[8])) if parts[8] else 0,
        "成交额": float(parts[9]) if parts[9] else 0,
        "涨跌幅": f"{change_pct}%",
        "涨跌额": change_amt,
        "数据源": "新浪财经",
    }
    return _json_output(result, "quote")


# ============================================================
# 2. 大盘总览
# ============================================================
def get_market_overview() -> str:
    """
    获取A股大盘总览：上证、深证、创业板、科创板指数 + 涨跌统计
    """
    if HAS_AKSHARE:
        try:
            spot = ak.stock_zh_a_spot_em()
            total = len(spot)
            up = len(spot[spot['涨跌幅'] > 0])
            down = len(spot[spot['涨跌幅'] < 0])
            flat = total - up - down
            limit_up = len(spot[spot['涨跌幅'] >= 9.9])
            limit_down = len(spot[spot['涨跌幅'] <= -9.9])

            # 指数
            indices = {}
            for code, name in [("000001", "上证指数"), ("399001", "深证成指"),
                               ("399006", "创业板指"), ("000688", "科创50")]:
                try:
                    idx = ak.stock_zh_index_daily_em(symbol=f"sh{code}" if code.startswith("000") else f"sz{code}")
                    if not idx.empty:
                        last = idx.iloc[-1]
                        indices[name] = {
                            "收盘": float(last.get('close', 0)),
                            "日期": str(last.get('date', ''))
                        }
                except Exception:
                    pass

            # 板块涨幅前5
            try:
                sectors = ak.stock_board_industry_name_em()
                top_sectors = sectors.head(5)[['板块名称', '涨跌幅']].to_dict(orient='records')
            except Exception:
                top_sectors = []

            result = {
                "市场统计": {
                    "总股票数": total,
                    "上涨": up,
                    "下跌": down,
                    "平盘": flat,
                    "涨停": limit_up,
                    "跌停": limit_down,
                    "上涨占比": f"{up/total*100:.1f}%" if total > 0 else "0%",
                },
                "主要指数": indices,
                "领涨板块": top_sectors,
                "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            return _json_output(result, "market")
        except Exception as e:
            pass

    # 备用1：东方财富API → 备用2：新浪API
    try:
        return _market_em_api()
    except Exception:
        return _market_sina_api()


def _market_em_api() -> str:
    """东方财富指数API备用方案"""
    indices_map = {
        "1.000001": "上证指数",
        "0.399001": "深证成指",
        "0.399006": "创业板指",
        "1.000688": "科创50",
    }
    secids = ",".join(indices_map.keys())
    url = f"https://push2.eastmoney.com/api/qt/ulist.np/get?secids={secids}&fields=f1,f2,f3,f4,f5,f6,f12,f14"

    data = _fetch_json(url)
    result = {}
    for item in data.get("data", {}).get("diff", []):
        name = indices_map.get(item.get("f12", ""), item.get("f14", ""))
        result[name] = {
            "最新价": item.get("f2", 0) / 100 if item.get("f2") else None,
            "涨跌幅": f"{item.get('f3', 0) / 100}%",
        }
    return json.dumps({"指数": result, "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                      ensure_ascii=False, indent=2)


def _market_sina_api() -> str:
    """新浪财经指数API（最稳定的备用方案）"""
    indices = [
        ("sh000001", "上证指数"),
        ("sz399001", "深证成指"),
        ("sz399006", "创业板指"),
        ("sh000688", "科创50"),
    ]
    symbols = ",".join(s for s, _ in indices)
    url = f"https://hq.sinajs.cn/list={symbols}"
    text = _fetch_text(url)

    import re
    results = {}
    lines = re.findall(r'var hq_str_\w+="([^"]*)"', text)
    for i, line in enumerate(lines):
        if i >= len(indices):
            break
        parts = line.split(",")
        if len(parts) < 4:
            continue
        name = indices[i][1]
        open_price = float(parts[1]) if parts[1] else 0
        prev_close = float(parts[2]) if parts[2] else 0
        current = float(parts[3]) if parts[3] else 0
        high = float(parts[4]) if parts[4] else 0
        low = float(parts[5]) if parts[5] else 0

        change_pct = round((current - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
        results[name] = {
            "最新价": current,
            "今开": open_price,
            "昨收": prev_close,
            "最高": high,
            "最低": low,
            "涨跌幅": f"{change_pct}%",
        }

    return json.dumps({
        "指数": results,
        "数据源": "新浪财经",
        "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }, ensure_ascii=False, indent=2)


# ============================================================
# 3. K线历史数据
# ============================================================
def get_kline(symbol: str, period: str = "daily", count: int = 30) -> str:
    """
    获取K线数据
    symbol: 股票代码
    period: daily(日线) / weekly(周线) / monthly(月线)
    count: 获取天数/周数/月数
    """
    symbol = _validate_symbol(symbol)
    if count < 1 or count > 500:
        return json.dumps({"error": "数量必须在1到500之间"}, ensure_ascii=False)

    if HAS_AKSHARE:
        try:
            end_date = datetime.now().strftime("%Y%m%d")
            lookback_days = {
                "daily": count * 2 + 30,
                "weekly": count * 10 + 60,
                "monthly": count * 35 + 120,
            }[period]
            start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

            if period == "daily":
                df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                        start_date=start_date, end_date=end_date, adjust="qfq")
            elif period == "weekly":
                df = ak.stock_zh_a_hist(symbol=symbol, period="weekly",
                                        start_date=start_date, end_date=end_date, adjust="qfq")
            elif period == "monthly":
                df = ak.stock_zh_a_hist(symbol=symbol, period="monthly",
                                        start_date=start_date, end_date=end_date, adjust="qfq")
            else:
                return json.dumps({"error": f"不支持的周期: {period}，可选: daily/weekly/monthly"},
                                  ensure_ascii=False)

            df = df.tail(count)
            # akshare 返回列名已为中文，去掉冗余的"股票代码"列
            if '股票代码' in df.columns:
                df = df.drop(columns=['股票代码'])
            return _json_output(df.to_dict(orient='records'), "kline")
        except Exception as e:
            pass

    # 备用：新浪K线API
    return _kline_sina_api(symbol, period, count)


def _kline_sina_api(symbol: str, period: str, count: int) -> str:
    """新浪财经K线API备用方案（东方财富push2his常被限流）"""
    # 判断市场前缀
    if symbol.startswith(('5', '6', '9')):
        sina_symbol = f"sh{symbol}"
    else:
        sina_symbol = f"sz{symbol}"

    # scale: 240=日线, 1200=周线, 7200=月线
    scale_map = {"daily": "240", "weekly": "1200", "monthly": "7200"}
    scale = scale_map.get(period, "240")

    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sina_symbol}&scale={scale}"
           f"&ma=no&datalen={count}")

    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}

    try:
        data = _fetch_json(url, headers=headers)
        result = []
        prev_close = None
        for item in data:
            close = float(item.get("close", 0))
            open_price = float(item.get("open", 0))
            high = float(item.get("high", 0))
            low = float(item.get("low", 0))
            volume = int(float(item.get("volume", 0)))

            if prev_close and prev_close > 0:
                change_pct = round((close - prev_close) / prev_close * 100, 2)
                change_amt = round(close - prev_close, 2)
            else:
                change_pct = round((close - open_price) / open_price * 100, 2) if open_price > 0 else 0
                change_amt = round(close - open_price, 2) if open_price > 0 else 0

            result.append({
                "日期": item.get("day", ""),
                "开盘": open_price,
                "收盘": close,
                "最高": high,
                "最低": low,
                "成交量": volume,
                "涨跌幅": change_pct,
                "涨跌额": change_amt,
            })
            prev_close = close

        return _json_output(result, "kline")
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol, "sina_symbol": sina_symbol},
                          ensure_ascii=False)


# ============================================================
# 4. 条件选股
# ============================================================
def screen_stocks(conditions: str) -> str:
    """
    条件选股
    conditions: 条件表达式，多个用空格分隔
    支持: pe<20, pe>10, pb<3, marketcap>100(亿), rise>5, vol>10000
    """
    if not HAS_AKSHARE:
        return _screen_em_api(conditions)

    try:
        spot = ak.stock_zh_a_spot_em()
    except Exception as e:
        return json.dumps({"error": f"获取全市场行情失败: {e}"}, ensure_ascii=False)

    # 解析条件
    filters = []
    for cond in conditions.split():
        cond = cond.strip()
        if not cond:
            continue
        for op in ['<=', '>=', '!=', '<', '>', '=']:
            if op in cond:
                field_part = cond[:cond.index(op)].strip()
                value_part = cond[cond.index(op)+1:].strip()
                field_map = {
                    'pe': '市盈率-动态',
                    'pb': '市净率',
                    'marketcap': '总市值',
                    'rise': '涨跌幅',
                    'vol': '成交量',
                    'amount': '成交额',
                    'turnover': '换手率',
                }
                col = field_map.get(field_part.lower(), field_part)
                if field_part.lower() == 'roe':
                    return json.dumps({"error": "实时行情不含可靠ROE字段；请先筛选行情条件，再对候选逐只调用finance"},
                                      ensure_ascii=False)
                if col not in spot.columns:
                    return json.dumps({"error": f"未知条件字段: {field_part}，支持: {list(field_map.keys())}"},
                                       ensure_ascii=False)
                try:
                    val = float(value_part)
                except ValueError:
                    return json.dumps({"error": f"条件值无效: {value_part}"}, ensure_ascii=False)

                compare_val = val * 100000000 if field_part.lower() == 'marketcap' else val
                if op == '<':
                    spot = spot[spot[col] < compare_val]
                elif op == '<=':
                    spot = spot[spot[col] <= compare_val]
                elif op == '>':
                    spot = spot[spot[col] > compare_val]
                elif op == '>=':
                    spot = spot[spot[col] >= compare_val]
                elif op == '=':
                    spot = spot[spot[col] == compare_val]
                elif op == '!=':
                    spot = spot[spot[col] != compare_val]
                filters.append(f"{col}{op}{val}")
                break

    cols = ['代码', '名称', '最新价', '涨跌幅', '市盈率-动态', '市净率',
            '总市值', '换手率', '成交量']
    available_cols = [c for c in cols if c in spot.columns]
    result = spot[available_cols].head(30)

    return json.dumps({
        "conditions": filters,
        "matched_count": len(spot),
        "showing": min(30, len(spot)),
        "results": result.to_dict(orient='records'),
    }, ensure_ascii=False, default=str, indent=2)


def _screen_em_api(conditions: str) -> str:
    """Dependency-free real-time screening using quote fields."""
    import re
    field_map = {
        "pe": ("市盈率", "f9", 1.0),
        "pb": ("市净率", "f23", 1.0),
        "marketcap": ("总市值", "f20", 100000000.0),
        "rise": ("涨跌幅", "f3", 1.0),
        "vol": ("成交量", "f5", 1.0),
        "amount": ("成交额", "f6", 1.0),
        "turnover": ("换手率", "f8", 1.0),
    }
    parsed = []
    for token in conditions.split():
        match = re.match(r"^(pe|pb|marketcap|rise|vol|amount|turnover)(<=|>=|!=|=|<|>)(-?\d+(?:\.\d+)?)$",
                         token, re.IGNORECASE)
        if not match:
            if token.lower().startswith("roe"):
                return json.dumps({"error": "实时行情不含可靠ROE字段；请对初筛候选调用finance"}, ensure_ascii=False)
            return json.dumps({"error": "无效条件: %s" % token,
                               "supported": list(field_map.keys())}, ensure_ascii=False)
        key, op, raw_value = match.groups()
        label, api_field, scale = field_map[key.lower()]
        parsed.append((key.lower(), label, api_field, op, float(raw_value) * scale))

    fields = "f12,f14,f2,f3,f5,f6,f8,f9,f20,f23"
    try:
        stocks = []
        for page in range(1, 13):
            url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%s&pz=500&po=1&np=1"
                   "&fltt=2&invt=2&fid=f6&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
                   "&fields=%s" % (page, fields))
            page_data = _fetch_json(url).get("data", {})
            batch = page_data.get("diff", [])
            stocks.extend(batch)
            if len(batch) < 500:
                break
        operators = {
            "<": lambda a, b: a < b,
            "<=": lambda a, b: a <= b,
            ">": lambda a, b: a > b,
            ">=": lambda a, b: a >= b,
            "=": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
        }
        matched = []
        for stock in stocks:
            valid = True
            for _, _, api_field, op, target in parsed:
                value = stock.get(api_field)
                if value is None or value == "-" or not operators[op](float(value), target):
                    valid = False
                    break
            if valid:
                matched.append({
                    "代码": stock.get("f12"), "名称": stock.get("f14"),
                    "最新价": stock.get("f2"), "涨跌幅": stock.get("f3"),
                    "成交量": stock.get("f5"), "成交额": stock.get("f6"),
                    "换手率": stock.get("f8"), "市盈率": stock.get("f9"),
                    "市净率": stock.get("f23"), "总市值": stock.get("f20"),
                })
        return json.dumps({
            "conditions": conditions,
            "matched_count": len(matched),
            "showing": min(30, len(matched)),
            "data_source": "东方财富",
            "results": matched[:30],
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        try:
            return _screen_sina_api(conditions)
        except Exception as fallback_exc:
            return json.dumps({
                "error": "选股数据获取失败",
                "eastmoney": str(exc),
                "sina": str(fallback_exc),
            }, ensure_ascii=False)


def _screen_sina_api(conditions):
    """Parallel Sina pagination fallback for full-market screening."""
    import re
    from concurrent.futures import ThreadPoolExecutor
    field_map = {
        "pe": ("per", 1.0), "pb": ("pb", 1.0),
        "marketcap": ("mktcap", 10000.0),
        "rise": ("changepercent", 1.0), "vol": ("volume", 1.0),
        "amount": ("amount", 1.0), "turnover": ("turnoverratio", 1.0),
    }
    filters = []
    for token in conditions.split():
        match = re.match(r"^(pe|pb|marketcap|rise|vol|amount|turnover)(<=|>=|!=|=|<|>)(-?\d+(?:\.\d+)?)$",
                         token, re.IGNORECASE)
        if not match:
            raise ValueError("无效条件: %s" % token)
        key, op, raw = match.groups()
        api_field, value_scale = field_map[key.lower()]
        target = float(raw) * (100000000.0 if key.lower() == "marketcap" else 1.0)
        filters.append((api_field, value_scale, op, target))

    def fetch_page(page):
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "Market_Center.getHQNodeData?page=%s&num=100&sort=amount&asc=0"
               "&node=hs_a&symbol=&_s_r_a=page" % page)
        return _fetch_json(url)

    stocks = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        for batch in pool.map(fetch_page, range(1, 71)):
            if isinstance(batch, list):
                stocks.extend(batch)

    operators = {
        "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "=": lambda a, b: a == b, "!=": lambda a, b: a != b,
    }
    matched = []
    for stock in stocks:
        valid = True
        for api_field, value_scale, op, target in filters:
            value = stock.get(api_field)
            try:
                numeric = float(value) * value_scale
            except (TypeError, ValueError):
                valid = False
                break
            if not operators[op](numeric, target):
                valid = False
                break
        if valid:
            matched.append({
                "代码": stock.get("code"), "名称": stock.get("name"),
                "最新价": float(stock.get("trade", 0)),
                "涨跌幅": float(stock.get("changepercent", 0)),
                "成交量": float(stock.get("volume", 0)),
                "成交额": float(stock.get("amount", 0)),
                "换手率": float(stock.get("turnoverratio", 0)),
                "市盈率": stock.get("per"), "市净率": stock.get("pb"),
                "总市值": float(stock.get("mktcap", 0)) * 10000.0,
            })
    matched.sort(key=lambda item: item["成交额"], reverse=True)
    return json.dumps({
        "conditions": conditions,
        "matched_count": len(matched),
        "showing": min(30, len(matched)),
        "data_source": "新浪全市场行情",
        "results": matched[:30],
    }, ensure_ascii=False, indent=2)


# ============================================================
# 5. 板块行情
# ============================================================
def get_sectors() -> str:
    """获取行业板块行情"""
    if HAS_AKSHARE:
        try:
            df = ak.stock_board_industry_name_em()
            result = df.head(100)[['板块名称', '最新价', '涨跌幅', '总市值',
                                   '换手率', '上涨家数', '下跌家数',
                                   '领涨股票', '涨跌幅.1']].to_dict(orient='records')
            return _json_output(result, "sectors")
        except Exception:
            pass
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1"
           "&fltt=2&invt=2&fid=f3&fs=m:90+t:2"
           "&fields=f12,f14,f2,f3,f8,f62,f104,f105,f128,f136")
    try:
        data = _fetch_json(url).get("data", {}).get("diff", [])
        result = []
        for item in data:
            result.append({
                "板块代码": item.get("f12"),
                "板块名称": item.get("f14"),
                "最新价": item.get("f2"),
                "涨跌幅": item.get("f3"),
                "换手率": item.get("f8"),
                "主力净流入": item.get("f62"),
                "上涨家数": item.get("f104"),
                "下跌家数": item.get("f105"),
                "领涨股票": item.get("f128"),
                "领涨股涨跌幅": item.get("f136"),
            })
        return json.dumps({
            "data_source": "东方财富",
            "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sectors": result,
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        try:
            return _sector_sina_api()
        except Exception as fallback_exc:
            return json.dumps({
                "error": "板块数据获取失败",
                "eastmoney": str(exc),
                "sina": str(fallback_exc),
            }, ensure_ascii=False)


def _sector_sina_api():
    """Sina industry ranking fallback when Eastmoney blocks the client."""
    import re
    text = _fetch_text("https://money.finance.sina.com.cn/q/view/newSinaHy.php")
    match = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.DOTALL)
    if not match:
        raise ValueError("新浪板块响应格式异常")
    payload = json.loads(match.group(1))
    sectors = []
    for value in payload.values():
        p = value.split(",")
        if len(p) < 13:
            continue
        sectors.append({
            "板块代码": p[0],
            "板块名称": p[1],
            "成分股数": int(float(p[2])),
            "涨跌幅": round(float(p[5]), 3),
            "成交量": float(p[6]),
            "成交额": float(p[7]),
            "领涨股票": p[12],
            "领涨股代码": p[8].replace("sh", "").replace("sz", ""),
        })
    sectors.sort(key=lambda item: item["涨跌幅"], reverse=True)
    return json.dumps({
        "data_source": "新浪行业分类",
        "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
        "notice": "新浪与申万行业分类不同；用于板块强弱初筛，行业RS需另行计算",
        "sectors": sectors[:100],
    }, ensure_ascii=False, indent=2)


# ============================================================
# 6. 龙虎榜
# ============================================================
def get_toplist() -> str:
    """获取今日龙虎榜数据"""
    if HAS_AKSHARE:
        try:
            today = datetime.now().strftime("%Y%m%d")
            df = ak.stock_lhb_detail_em(start_date=today, end_date=today)
            if df.empty:
                # 尝试最近交易日
                for i in range(1, 7):
                    d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
                    df = ak.stock_lhb_detail_em(start_date=d, end_date=d)
                    if not df.empty:
                        today = d
                        break
            if not df.empty:
                return _json_output(df.to_dict(orient='records'), "toplist")
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    return json.dumps({"error": "获取龙虎榜需要akshare"}, ensure_ascii=False)


# ============================================================
# 7. 财务数据
# ============================================================
def get_finance(symbol: str) -> str:
    """获取个股财务摘要"""
    symbol = symbol.strip().zfill(6)

    if HAS_AKSHARE:
        try:
            df = ak.stock_financial_abstract(symbol=symbol)
            result = df.head(8).to_dict(orient='records')
            return _json_output(result, "finance")
        except Exception:
            pass

        try:
            df = ak.stock_financial_analysis_indicator(symbol=symbol)
            result = df.head(8).to_dict(orient='records')
            return _json_output(result, "finance")
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    return json.dumps({"error": "获取财务数据需要akshare"}, ensure_ascii=False)


# ============================================================
# 8. 资金流向
# ============================================================
def get_money_flow(symbol: str) -> str:
    """获取个股资金流向"""
    symbol = _validate_symbol(symbol)

    if HAS_AKSHARE:
        try:
            df = ak.stock_individual_fund_flow(stock=symbol, market="sh" if symbol.startswith(('5','6','9')) else "sz")
            result = df.tail(10).to_dict(orient='records')
            return _json_output(result, "money_flow")
        except Exception:
            pass

        try:
            df = ak.stock_individual_fund_flow_rank()
            row = df[df['代码'] == symbol]
            if not row.empty:
                return _json_output(row.to_dict(orient='records'), "money_flow")
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    market = "1" if symbol.startswith(('5', '6', '9')) else "0"
    url = ("https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get?"
           "lmt=10&klt=101&secid=%s.%s&fields1=f1,f2,f3,f7"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"
           % (market, symbol))
    try:
        payload = _fetch_json(url).get("data", {})
        rows = []
        for line in payload.get("klines", []):
            p = line.split(",")
            if len(p) < 6:
                continue
            rows.append({
                "日期": p[0],
                "主力净流入": float(p[1]),
                "小单净流入": float(p[2]),
                "中单净流入": float(p[3]),
                "大单净流入": float(p[4]),
                "超大单净流入": float(p[5]),
            })
        if not rows:
            raise ValueError("接口返回空数据")
        return json.dumps({
            "symbol": symbol,
            "name": payload.get("name"),
            "data_source": "东方财富资金流口径",
            "notice": "资金流为成交分类统计，不代表可识别的真实机构账户，仅作辅助过滤",
            "data": rows,
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error": "资金流数据获取失败: %s" % exc}, ensure_ascii=False)


# ============================================================
# 9. 分时数据
# ============================================================
def get_minute(symbol: str) -> str:
    """获取分时行情"""
    symbol = symbol.strip().zfill(6)

    if HAS_AKSHARE:
        try:
            df = ak.stock_intraday_em(symbol=symbol)
            return _json_output(df.to_dict(orient='records'), "minute")
        except Exception:
            pass

    # 备用1：东方财富分时API → 备用2：新浪5分钟K线
    try:
        return _minute_em_api(symbol)
    except Exception:
        return _minute_sina_api(symbol)


def _minute_em_api(symbol: str) -> str:
    """东方财富分时API"""
    if symbol.startswith(('5', '6', '9')):
        secid = f"1.{symbol}"
    else:
        secid = f"0.{symbol}"
    url = (f"https://push2his.eastmoney.com/api/qt/stock/trends2/get?"
           f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1")

    data = _fetch_json(url)
    trends = data.get("data", {}).get("trends", [])
    if not trends:
        raise ValueError(f"东方财富分时数据为空: {symbol}")
    result = []
    for line in trends:
        parts = line.split(",")
        result.append({
            "时间": parts[0],
            "均价": float(parts[1]) if len(parts) > 1 else None,
            "价格": float(parts[2]) if len(parts) > 2 else None,
            "成交量": int(parts[3]) if len(parts) > 3 else None,
        })
    return _json_output(result, "minute")


def _minute_sina_api(symbol: str) -> str:
    """新浪5分钟K线作为分时数据备用"""
    if symbol.startswith(('5', '6', '9')):
        sina_symbol = f"sh{symbol}"
    else:
        sina_symbol = f"sz{symbol}"

    # scale=5 → 5分钟K线，datalen=48 → 一天约48根5分钟K线
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sina_symbol}&scale=5&ma=no&datalen=48")

    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    data = _fetch_json(url, headers=headers)

    result = []
    for item in data:
        result.append({
            "时间": item.get("day", ""),
            "价格": float(item.get("close", 0)),
            "开盘": float(item.get("open", 0)),
            "最高": float(item.get("high", 0)),
            "最低": float(item.get("low", 0)),
            "成交量": int(float(item.get("volume", 0))),
        })
    return _json_output(result, "minute")


# ============================================================
# 10. 北向资金
# ============================================================
def get_north_flow() -> str:
    """获取北向资金（沪深港通）流入数据"""
    if HAS_AKSHARE:
        try:
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            result = df.tail(20).to_dict(orient='records')
            return json.dumps({
                "notice": "沪深港通自2024-08-19起不再披露盘中实时买卖金额；该序列可能是历史或盘后口径",
                "data": result,
            }, ensure_ascii=False, default=str, indent=2)
        except Exception:
            pass

        try:
            df = ak.stock_hsgt_north_net_flow_in_em(indicator="北上")
            result = df.tail(20).to_dict(orient='records')
            return _json_output(result, "north_flow")
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    return json.dumps({"error": "获取北向资金需要akshare"}, ensure_ascii=False)


# ============================================================
# 11. V4.0 technical diagnosis
# ============================================================
def _midpoint(rows, end_index, length):
    start = end_index - length + 1
    window = rows[start:end_index + 1]
    return (max(r["high"] for r in window) + min(r["low"] for r in window)) / 2.0


def _parse_klines(payload):
    data = json.loads(payload)
    if isinstance(data, dict) and data.get("error"):
        raise ValueError(data["error"])
    if isinstance(data, dict):
        data = data.get("kline") or data.get("data") or []
    rows = []
    for item in data:
        rows.append({
            "date": str(item.get("日期", item.get("date", ""))),
            "open": float(item.get("开盘", item.get("open", 0))),
            "close": float(item.get("收盘", item.get("close", 0))),
            "high": float(item.get("最高", item.get("high", 0))),
            "low": float(item.get("最低", item.get("low", 0))),
            "volume": float(item.get("成交量", item.get("volume", 0))),
        })
    return rows


def _ichimoku_snapshot(rows):
    if len(rows) < 80:
        raise ValueError("一目均衡表至少需要80根K线")
    end = len(rows) - 1
    tenkan = _midpoint(rows, end, 9)
    kijun = _midpoint(rows, end, 26)
    kijun_5 = _midpoint(rows, end - 5, 26)
    span_a_future = (tenkan + kijun) / 2.0
    span_b_future = _midpoint(rows, end, 52)

    cloud_end = end - 26
    cloud_tenkan = _midpoint(rows, cloud_end, 9)
    cloud_kijun = _midpoint(rows, cloud_end, 26)
    cloud_a = (cloud_tenkan + cloud_kijun) / 2.0
    cloud_b = _midpoint(rows, cloud_end, 52)
    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "kijun_direction": "上行" if kijun > kijun_5 else ("下行" if kijun < kijun_5 else "走平"),
        "cloud_top": max(cloud_a, cloud_b),
        "cloud_bottom": min(cloud_a, cloud_b),
        "future_cloud_bullish": span_a_future >= span_b_future,
    }


def analyze_v4(symbol: str) -> str:
    """Evaluate a symbol against the user's Ichimoku 9/26/52 and ATR rules."""
    symbol = _validate_symbol(symbol)
    daily = _parse_klines(get_kline(symbol, "daily", 120))
    weekly = _parse_klines(get_kline(symbol, "weekly", 100))
    d = _ichimoku_snapshot(daily)
    w = _ichimoku_snapshot(weekly)
    last = daily[-1]

    true_ranges = []
    for i in range(len(daily) - 14, len(daily)):
        prev_close = daily[i - 1]["close"]
        true_ranges.append(max(
            daily[i]["high"] - daily[i]["low"],
            abs(daily[i]["high"] - prev_close),
            abs(daily[i]["low"] - prev_close),
        ))
    atr = sum(true_ranges) / len(true_ranges)
    distance_atr = (last["close"] - d["kijun"]) / atr if atr else None
    return_10d = (last["close"] / daily[-11]["close"] - 1) * 100
    avg_volume_20 = sum(r["volume"] for r in daily[-21:-1]) / 20.0
    volume_ratio = last["volume"] / avg_volume_20 if avg_volume_20 else None

    above_cloud = last["close"] > d["cloud_top"]
    tk_bullish = d["tenkan"] >= d["kijun"]
    chikou_confirmed = last["close"] > daily[-27]["close"]
    weekly_bullish = weekly[-1]["close"] > w["cloud_top"] and w["tenkan"] >= w["kijun"]
    overheated = (distance_atr is not None and distance_atr > 2) or return_10d > 20
    three_roles = all((above_cloud, tk_bullish, chikou_confirmed,
                       d["future_cloud_bullish"], d["kijun_direction"] != "下行"))

    if overheated:
        verdict = "禁止追涨，等待回踩转换线或基準线"
    elif not weekly_bullish:
        verdict = "周线趋势未确认，不开新仓"
    elif three_roles:
        verdict = "结构合格；仍需结合大盘、行业、收盘确认和RR制定盘前条件单"
    elif above_cloud and d["kijun_direction"] == "上行":
        verdict = "观察回踩，尚未形成完整三役好転"
    else:
        verdict = "个股层未通过，不开新仓"

    in_session = datetime.now().weekday() < 5 and (
        datetime.now().strftime("%H:%M") < "15:00"
    ) and last["date"].startswith(datetime.now().strftime("%Y-%m-%d"))
    result = {
        "symbol": symbol,
        "as_of": last["date"],
        "bar_status": "盘中未完成" if in_session else "已完成",
        "price": round(last["close"], 3),
        "daily": {
            "tenkan_9": round(d["tenkan"], 3),
            "kijun_26": round(d["kijun"], 3),
            "kijun_direction": d["kijun_direction"],
            "cloud_top": round(d["cloud_top"], 3),
            "above_cloud": above_cloud,
            "future_cloud_bullish": d["future_cloud_bullish"],
            "chikou_confirmed": chikou_confirmed,
            "atr_14": round(atr, 3),
            "distance_from_kijun_atr": round(distance_atr, 3) if distance_atr is not None else None,
            "return_10d_pct": round(return_10d, 2),
            "volume_vs_20d": round(volume_ratio, 2) if volume_ratio is not None else None,
        },
        "weekly_trend_confirmed": weekly_bullish,
        "three_roles_bullish": three_roles,
        "overheated": overheated,
        "verdict": verdict,
        "limitations": [
            "主力资金流仅作过滤，不作为独立买点",
            "未包含公告、解禁、行业RS、滑点与无法成交风险",
            "盘中K线未完成时必须在收盘后复核",
        ],
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="A股实时数据工具 - 供 Codex 使用的命令行接口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python astock.py quote 600519           # 贵州茅台实时行情
  python astock.py market                # 大盘总览
  python astock.py kline 000001 daily 5  # 平安银行近5天日K
  python astock.py screen "pe<20 pb<3 marketcap>100" # 条件选股
  python astock.py sector                # 板块行情
  python astock.py toplist               # 龙虎榜
  python astock.py finance 600519        # 财务数据
  python astock.py flow 600519           # 资金流向
  python astock.py minute 600519         # 分时行情
  python astock.py analyze 600519        # V4.0一目均衡表/ATR诊断
  python astock.py north                 # 北向资金
"""
    )

    sub = parser.add_subparsers(dest="command", help="可用命令")

    p_quote = sub.add_parser("quote", help="个股实时行情")
    p_quote.add_argument("symbol", help="6位股票代码")

    sub.add_parser("market", help="大盘总览")

    p_kline = sub.add_parser("kline", help="K线数据")
    p_kline.add_argument("symbol", help="6位股票代码")
    p_kline.add_argument("period", nargs="?", default="daily",
                         choices=["daily", "weekly", "monthly"], help="周期")
    p_kline.add_argument("count", nargs="?", type=int, default=30, help="数量")

    p_screen = sub.add_parser("screen", help="条件选股")
    p_screen.add_argument("conditions", help='条件，如 "pe<20 pb<3 marketcap>100"')

    sub.add_parser("sector", help="板块行情")
    sub.add_parser("toplist", help="龙虎榜")

    p_fin = sub.add_parser("finance", help="财务数据")
    p_fin.add_argument("symbol", help="6位股票代码")

    p_flow = sub.add_parser("flow", help="资金流向")
    p_flow.add_argument("symbol", help="6位股票代码")

    p_min = sub.add_parser("minute", help="分时行情")
    p_min.add_argument("symbol", help="6位股票代码")

    p_analyze = sub.add_parser("analyze", help="按V4.0交易系统诊断个股")
    p_analyze.add_argument("symbol", help="6位股票代码")

    sub.add_parser("north", help="北向资金")

    # 检查依赖
    if not HAS_AKSHARE:
        print("[WARNING] akshare未安装，部分功能将使用备用API", file=sys.stderr)

    args = parser.parse_args()

    try:
        if args.command == "quote":
            output = get_quote(args.symbol)
        elif args.command == "market":
            output = get_market_overview()
        elif args.command == "kline":
            output = get_kline(args.symbol, args.period, args.count)
        elif args.command == "screen":
            output = screen_stocks(args.conditions)
        elif args.command == "sector":
            output = get_sectors()
        elif args.command == "toplist":
            output = get_toplist()
        elif args.command == "finance":
            output = get_finance(args.symbol)
        elif args.command == "flow":
            output = get_money_flow(args.symbol)
        elif args.command == "minute":
            output = get_minute(args.symbol)
        elif args.command == "analyze":
            output = analyze_v4(args.symbol)
        elif args.command == "north":
            output = get_north_flow()
        else:
            parser.print_help()
            return
        print(output)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "command": args.command}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
