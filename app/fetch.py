# -*- coding: utf-8 -*-
"""演出信息采集：豆瓣同城（在售演出）+ 自定义 JSON/RSS 源 + 手动收件箱。

大麦自身的列表接口有风控（会返回验证码页），所以这里：
  * 用豆瓣同城抓「有哪些演出 / 什么时候 / 多少钱 / 在哪儿」——数据稳定且分类精细；
  * 购票跳转统一生成大麦搜索页链接 + 豆瓣购票直通车链接，两者都可一点直达买票页；
  * 预留可插拔数据源，随时能接其它票务/剧院网站。
"""
from __future__ import annotations

import gzip
import html
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

from .core import (
    CATEGORY_ORDER, CITY_NAMES, DB_SUBCATEGORIES, INBOX_FILE, SHOWS_FILE,
    SOURCES_FILE, dedupe_key, enrich, is_performance, load_json, log, save_json, today_str,
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def http_get(url: str, timeout: int = 15, retries: int = 2) -> str | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "identity",
        "Referer": "https://www.douban.com/",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                charset = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace")
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                log(f"请求失败 {url} -> {exc}")
            else:
                time.sleep(0.8)
    return None


def strip_tags(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def _first(pattern: str, text: str, default: str = "", flags: int = 0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else default


def parse_loose_date(text: str) -> tuple[str, str]:
    """'11月11日 14:30 ~ 11月29日 21:30' -> ('2026-11-11T14:30:00','2026-11-29T21:30:00')"""
    today = date.today()
    found = re.findall(r"(\d{1,2})月(\d{1,2})日(?:\s+(\d{1,2}):(\d{2}))?", text or "")
    results = []
    for mo, dy, hh, mm in found[:2]:
        year = today.year
        if int(mo) < today.month - 1:
            year += 1
        try:
            results.append(f"{year:04d}-{int(mo):02d}-{int(dy):02d}T{int(hh or 0):02d}:{mm or '00'}:00")
        except ValueError:
            continue
    start = results[0] if results else ""
    end = results[1] if len(results) > 1 else start
    return start, end


def parse_douban_list(text: str, city: str, hint: str = "",
                      on_selling: bool = False) -> list[dict]:
    """解析豆瓣同城主活动列表（出售 / 各分类页通用）。"""
    blocks = re.split(r'<li class="list-entry"\s+itemscope', text)[1:]
    out = []
    for block in blocks:
        b = block.split("</li>")[0]
        m = re.search(r'href="(https://www\.douban\.com/event/(\d+)/[^"]*)"', b)
        if not m:
            continue
        detail_url = html.unescape(m.group(1)).split("?")[0]
        title = _first(r'itemprop="summary">([^<]+)</span>', b) or _first(r'title="([^"]+)"\s+itemprop="url"', b)
        if not title:
            continue
        title = html.unescape(title).strip()
        start = _first(r'itemprop="startDate"\s+datetime="([^"]+)"', b)
        end = _first(r'itemprop="endDate"\s+datetime="([^"]+)"', b)
        date_text = strip_tags(_first(r'<li class="event-time">(.*?)</li>', b, flags=re.S))
        if not start:
            start, end2 = parse_loose_date(date_text)
            end = end or end2
        location = _first(r'itemprop="location"\s+content="([^"]+)"', b) or _first(r'<li title="([^"]+)">', b)
        location = html.unescape(location)
        # 去掉开头的城市名，只留场馆
        cn = CITY_NAMES.get(city, "")
        venue = re.sub(r"^" + re.escape(cn) + r"\s*", "", location).strip() if cn else location.strip()
        fee = strip_tags(_first(r'<li class="fee">(.*?)</li>', b, flags=re.S))
        fee = fee.replace("费用：", "").strip()
        price = _first(r"<strong>([^<]+)</strong>", _first(r'<li class="fee">(.*?)</li>', b, flags=re.S)) or fee
        poster = _first(r'data-lazy="([^"]+)"', b)
        organizer = strip_tags(_first(r'target="db-event-owner"[^>]*>(.*?)</a>', b, flags=re.S))
        attend = int(_first(r"(\d+)人参加", b, "0") or 0)
        interest = int(_first(r"(\d+)人感兴趣", b, "0") or 0)
        out.append({
            "id": f"db-{re.search(r'/event/(\d+)', detail_url).group(1)}",
            "title": title, "category_hint": hint, "city": CITY_NAMES.get(city, city),
            "venue": venue[:60], "start": start, "end": end or start,
            "date_text": date_text, "price": price.strip(), "poster": poster,
            "organizer": organizer, "attend": attend, "interest": interest,
            "detail_url": detail_url, "source": "豆瓣同城", "on_selling": on_selling,
        })
    return out


def parse_douban_shop(text: str, city: str) -> list[dict]:
    """解析页面右侧「购票直通车」区块：这批是明确正在售票的活动，正好对上「别等卖完才知道」。"""
    out = []
    for b in re.split(r'<li class="col-xs-12[^"]*list-entry"', text)[1:]:
        block = b.split("</li>")[0]
        m = re.search(r'href="(https://www\.douban\.com/event/(\d+)/[^"]*)"', block)
        if not m:
            continue
        title = _first(r"<img alt=\"([^\"]+)\"", block) or strip_tags(_first(r'<p class="event-title">(.*?)</p>', block, flags=re.S))
        if not title:
            continue
        title = html.unescape(title).strip()
        date_text = strip_tags(_first(r'<p class="tip">(.*?)</p>', block, flags=re.S))
        start, end = parse_loose_date(date_text)
        price = _first(r"on-selling-events-price[^>]*>([^<]+)<", block).replace("&yen;", "¥")
        detail_url = html.unescape(m.group(1)).split("?")[0]
        out.append({
            "id": f"db-{m.group(2)}", "title": title, "category_hint": "",
            "city": CITY_NAMES.get(city, city), "venue": "", "start": start, "end": end,
            "date_text": date_text, "price": html.unescape(price).strip(),
            "poster": _first(r"<img[^>]+src=\"([^\"]+)\"", block),
            "detail_url": detail_url, "source": "豆瓣同城·购票直通车", "on_selling": True,
        })
    return out


def douban_targets(cfg: dict) -> list[tuple[str, str, str, bool]]:
    """(url, city, 精确品类提示, 是否售票页)"""
    d = cfg.get("douban", {})
    targets = []
    for city in cfg.get("cities", ["beijing"]):
        city = city.strip().lower()
        if not city:
            continue
        base = f"https://{city}.douban.com/events/"
        for p in range(int(d.get("selling_pages", 3))):
            targets.append((f"{base}selling?start={p * 10}", city, "", True))
        for main in ("drama", "music", "comedy"):
            pages = int(d.get("main_pages", 2)) if main in ("drama", "music") else 1
            for p in range(pages):
                targets.append((f"{base}future-{main}?start={p * 10}", city, "", False))
        for code, label in DB_SUBCATEGORIES.items():
            for p in range(int(d.get("sub_pages", 1))):
                targets.append((f"{base}future-{code}?start={p * 10}", city, label, False))
    return targets


def parse_custom_source(src: dict) -> list[dict]:
    """可插拔数据源：type = json | rss，字段会被规范化。"""
    url = src.get("url", "")
    if not url or src.get("enabled") is False:
        return []
    text = http_get(url)
    if not text:
        return []
    items = []
    kind = (src.get("type") or "json").lower()
    if kind == "rss":
        try:
            root = ET.fromstring(text)
            for it in root.iter():
                if not it.tag.endswith("item") and not it.tag.endswith("entry"):
                    continue
                def child(name):
                    for c in it:
                        if c.tag.endswith(name):
                            return (c.text or "").strip()
                    return ""
                items.append({"title": child("title"), "detail_url": child("link"),
                              "date_text": child("pubDate")})
        except ET.ParseError as exc:
            log(f"RSS 解析失败 {url}: {exc}")
    else:
        try:
            import json
            data = json.loads(text)
            items = data.get("shows", data) if isinstance(data, dict) else data
        except Exception as exc:  # noqa: BLE001
            log(f"JSON 源解析失败 {url}: {exc}")
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        title = str(it["title"]).strip()
        start = it.get("start") or it.get("date") or ""
        if not re.match(r"\d{4}-\d{2}-\d{2}", str(start)):
            start, _ = parse_loose_date(str(it.get("date_text") or start))
        out.append({
            "id": "", "title": title, "category_hint": it.get("category", ""),
            "city": it.get("city") or src.get("city", ""),
            "venue": it.get("venue", ""), "start": start, "end": it.get("end") or start,
            "date_text": it.get("date_text", ""), "price": str(it.get("price", "")),
            "detail_url": it.get("detail_url") or it.get("url", ""),
            "buy_url": it.get("buy_url", ""), "poster": it.get("poster", ""),
            "source": src.get("name", "自定义源"), "on_selling": bool(it.get("on_selling", True)),
        })
    return out


def read_inbox() -> list[dict]:
    items = load_json(INBOX_FILE, []) or []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        it = dict(it)
        it.setdefault("source", "手动添加")
        it.setdefault("on_selling", True)
        it.setdefault("category_hint", it.get("category", ""))
        start, end = parse_loose_date(it.get("date_text", ""))
        it.setdefault("start", it.get("start") or start)
        it.setdefault("end", it.get("end") or end or it["start"])
        out.append(it)
    return out


def selling_ids_from(text: str) -> set[str]:
    """页面「购票直通车」里出现的活动 = 明确正在售票，用来打准售票状态。"""
    ids = re.findall(r'douban\.com/event/(\d+)/[^"\']*icn=list-shopitem', text)
    return {f"db-{i}" for i in ids}


def store_key(s: dict) -> str:
    """豆瓣条目用稳定的 event id 去重；其它来源用标题+城市+日期。"""
    return s["id"] if str(s.get("id", "")).startswith("db-") else dedupe_key(s)


def refresh_all(cfg: dict | None = None) -> dict:
    """采集 + 合并去重 + 落盘。返回统计，供界面和日报脚本使用。"""
    from .core import load_config
    cfg = cfg or load_config()
    today = today_str()
    existing = load_json(SHOWS_FILE, []) or []
    by_key = {store_key(s): s for s in existing}

    raw: list[dict] = []
    errors: list[str] = []
    selling_ids: set[str] = set()
    if cfg.get("douban", {}).get("enabled", True):
        delay = float(cfg["douban"].get("request_delay", 0.5))
        for url, city, hint, selling in douban_targets(cfg):
            text = http_get(url)
            if text is None:
                errors.append(url)
                continue
            if "异常请求" in text or "sec.douban.com" in text:
                log("豆瓣触发风控，跳过该页（已抓取的数据照常保留）")
                errors.append(url)
                time.sleep(2)
                continue
            try:
                items = parse_douban_list(text, city, hint, selling)
                raw.extend(items)
                raw.extend(parse_douban_shop(text, city))
                selling_ids |= selling_ids_from(text)
                if selling:
                    selling_ids |= {x["id"] for x in items}
            except Exception as exc:  # noqa: BLE001
                log(f"解析失败 {url}: {exc}")
                errors.append(url)
            time.sleep(delay)

    for src in (load_json(SOURCES_FILE, {}) or {}).get("sources", []):
        raw.extend(parse_custom_source(src))
    raw.extend(read_inbox())

    new_count, kept = 0, 0
    for item in raw:
        try:
            show = enrich(item)
            if not is_performance(show):   # 只剔除羽毛球/瑜伽这类非演出活动，绝不按喜好过滤
                continue
            key = store_key(show)
        except Exception as exc:  # noqa: BLE001
            log(f"跳过异常条目: {exc}")
            continue
        kept += 1
        if key in by_key:
            old = by_key[key]
            for f in ("price", "price_min", "price_band", "poster", "interest", "attend",
                      "on_selling", "date_text", "end", "venue", "organizer", "buy_url",
                      "ticket_url", "detail_url", "tags", "source"):
                if show.get(f):
                    old[f] = show[f]
            if show.get("start"):
                old["start"] = show["start"]
            # 豆瓣精确子分类优先于标题推断
            if item.get("category_hint") in CATEGORY_ORDER:
                old["category"] = item["category_hint"]
                old["tags"] = enrich({**old, "category_hint": item["category_hint"]})["tags"]
            old["last_seen"] = today
            old.setdefault("first_seen", today)
        else:
            show["first_seen"] = today
            show["last_seen"] = today
            by_key[key] = show
            new_count += 1

    shows = [s for s in by_key.values() if is_performance(s)]
    for s in shows:
        if s.get("id") in selling_ids:
            s["on_selling"] = True
    shows.sort(key=lambda s: (s.get("start") or "9999", s.get("title", "")))
    save_json(SHOWS_FILE, shows)
    log(f"采集完成：抓到 {len(raw)} 条（演出 {kept} 条），新增 {new_count} 场，"
        f"库内共 {len(shows)} 场，售票中 {sum(1 for s in shows if s.get('on_selling'))} 场，失败 {len(errors)} 个页面")
    return {"fetched": len(raw), "new": new_count, "total": len(shows), "errors": errors}
