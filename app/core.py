# -*- coding: utf-8 -*-
"""演出雷达 · 核心逻辑（数据 / 偏好 / 排序 / 日报）

三条铁律（来自用户需求，改动时请勿破坏）：
  1. 每日汇总必须「面面俱到」：所有未开演演出全部保留，绝不因为偏好过滤掉任何一场。
  2. 偏好只影响「同一品类内部的先后顺序」——喜欢的排前面一点，仅此而已。
  3. 「我买了 / 想看 / 点过购票」会写入长期偏好，越用越懂你。

零第三方依赖，仅使用 Python 标准库。
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
LOGS = ROOT / "logs"
for _d in (DATA, REPORTS, LOGS):
    _d.mkdir(parents=True, exist_ok=True)

SHOWS_FILE = DATA / "shows.json"
PREFS_FILE = DATA / "preferences.json"
INBOX_FILE = DATA / "inbox.json"
SOURCES_FILE = DATA / "sources.json"
CONFIG_FILE = ROOT / "config.json"
PUSH_LOG = LOGS / "push.log"

# 品类固定顺序：用户最关心的四类排最前，其余依次铺开，保证「面面俱到」
CATEGORY_ORDER = [
    "话剧", "音乐剧", "演唱会", "音乐会",
    "舞剧", "歌剧", "戏曲", "音乐节", "小型现场",
    "喜剧", "相声脱口秀", "展览", "其他",
]
CATEGORY_ICON = {
    "话剧": "🎭", "音乐剧": "🎤", "演唱会": "🎸", "音乐会": "🎻",
    "舞剧": "💃", "歌剧": "🎼", "戏曲": "🥁", "音乐节": "🎪",
    "小型现场": "🎧", "喜剧": "😄", "相声脱口秀": "🎙️", "展览": "🖼️", "其他": "✨",
}

# 豆瓣同城：城市拼音 -> 中文名
CITY_NAMES = {
    "beijing": "北京", "shanghai": "上海", "guangzhou": "广州", "shenzhen": "深圳",
    "hangzhou": "杭州", "chengdu": "成都", "nanjing": "南京", "wuhan": "武汉",
    "xian": "西安", "chongqing": "重庆", "suzhou": "苏州", "tianjin": "天津",
    "changsha": "长沙", "xiamen": "厦门", "qingdao": "青岛", "dalian": "大连",
    "shenyang": "沈阳", "zhengzhou": "郑州", "jinan": "济南", "hefei": "合肥",
    "fuzhou": "福州", "kunming": "昆明", "ningbo": "宁波", "wuxi": "无锡",
    "dongguan": "东莞", "foshan": "佛山", "harbin": "哈尔滨", "changchun": "长春",
    "shijiazhuang": "石家庄", "taiyuan": "太原", "nanchang": "南昌", "nanning": "南宁",
    "guiyang": "贵阳", "wenzhou": "温州", "zhuhai": "珠海", "huizhou": "惠州",
}

# 豆瓣同城精确子分类（活动列表 URL 尾缀 -> 品类）
DB_SUBCATEGORIES = {
    "1101": "话剧", "1102": "音乐剧", "1103": "舞剧", "1104": "歌剧",
    "1105": "戏曲",
    "1001": "小型现场", "1002": "音乐会", "1003": "演唱会", "1004": "音乐节",
    "comedy": "喜剧",
}

# 标题关键词 -> 品类（按优先级从上往下匹配，越具体的越靠前）
_TITLE_RULES = [
    ("音乐剧", ["音乐剧", "百老汇音乐剧", "西区音乐剧", "原版音乐剧"]),
    ("演唱会", ["演唱会", "世界巡演", "巡回演唱会"]),
    ("音乐会", ["音乐会", "交响", "室内乐", "独奏", "钢琴独奏", "合唱", "爱乐乐团", "管弦", "弦乐", "国乐"]),
    ("音乐节", ["音乐节", "草莓音乐节", "迷笛", "摩登天空"]),
    ("舞剧", ["舞剧", "芭蕾", "舞蹈剧场", "现代舞", "歌舞剧"]),
    ("歌剧", ["歌剧", "瓦格纳", "图兰朵", "茶花女"]),
    ("戏曲", ["京剧", "昆曲", "越剧", "评剧", "黄梅戏", "豫剧", "川剧", "秦腔", "戏曲"]),
    ("相声脱口秀", ["脱口秀", "相声", "单口喜剧", "开放麦", "喜剧专场", "即兴之夜"]),
    ("喜剧", ["喜剧", "爆笑", "笑探", "开心麻花"]),
    ("小型现场", ["livehouse", "live house", "小型现场", "不插电", "乐队巡演", "巡演"]),
    ("话剧", ["话剧", "舞台剧", "戏剧", "沉浸式", "悬疑剧", "大戏", "剧场版", "剧本杀"]),
    ("展览", ["展览", "艺术展", "沉浸式展", "画展", "特展", "大展"]),
]

# 演出相关判断：只剔除「根本不是演出的同城活动」（羽毛球、瑜伽、讲座…），
# 绝不因为用户偏好剔除任何一场演出。
PERFORMANCE_CATEGORIES = {
    "话剧", "音乐剧", "演唱会", "音乐会", "舞剧", "歌剧", "戏曲",
    "音乐节", "小型现场", "喜剧", "相声脱口秀",
}
NON_SHOW_WORDS = [
    "羽毛球", "瑜伽", "冥想", "公益", "读书会", "讲座", "课程", "相亲", "徒步",
    "露营", "市集", "观影", "健身", "跑步", "志愿者", "招聘", "英语角", "手工",
    "烘焙", "桌游", "聚会", "交友", "摄影", "骑行", "登山", "沙龙", "体验课",
]
SHOW_WORDS = [
    "剧", "演出", "音乐会", "演唱会", "音乐节", "live", "剧场", "舞台", "演奏",
    "相声", "脱口秀", "巡演", "门票", "售票", "交响", "钢琴", "独奏", "合唱",
    "乐团", "沉浸", "喜剧", "芭蕾", "魔术", "马戏", "大秀", "艺术展", "大展",
]


def is_performance(show: dict) -> bool:
    """是否属于「演出票」范畴。非演出活动不进日报，保证汇总的都是能买票的演出。"""
    cat = show.get("category", "")
    if cat in PERFORMANCE_CATEGORIES or cat == "展览":
        return True
    title = (show.get("title") or "").lower()
    if any(w in title for w in NON_SHOW_WORDS):
        return False
    return any(w in title for w in SHOW_WORDS)

# 用于从标题里抽取「偏好标签」的常见词（无需分词库）
KNOWN_TAGS = [
    "开心麻花", "孟京辉", "赖声川", "林兆华", "田沁鑫", "曹禺", "老舍", "莎士比亚",
    "阿加莎", "东野圭吾", "沉浸式", "悬疑", "爆笑", "喜剧", "悲剧", "经典", "原版",
    "百老汇", "西区", "中文版", "音乐剧", "话剧", "舞剧", "歌剧", "戏曲", "京剧",
    "交响", "钢琴", "小提琴", "古筝", "民乐", "国乐", "爵士", "摇滚", "民谣", "古典",
    "演唱会", "音乐会", "音乐节", "livehouse", "脱口秀", "相声", "亲子", "儿童",
    "芭蕾", "现代舞", "国风", "国潮", "VR", "互动", "环境式", "驻场", "巡演",
]

ACTION_WEIGHT = {"bought": 6.0, "want": 3.0, "click": 1.0, "dislike": -2.0}
ACTION_LABEL = {"bought": "已购买", "want": "想看", "click": "看过购票页", "dislike": "不感兴趣"}

DEFAULT_CONFIG = {
    "cities": ["beijing"],
    "push_time": "09:00",
    "douban": {
        "enabled": True,
        "selling_pages": 3,
        "main_pages": 2,
        "sub_pages": 1,
        "request_delay": 0.5,
    },
    "push": {
        "mode": "compact",
        "max_bytes_per_message": 3800,
        "save_local": True,
        "channels": [],
    },
    "ranking": {
        # 永不因偏好过滤：这个开关只是把规则显式写进配置，代码里也强制保留全部演出
        "preference_never_filters": True,
        "new_bonus": 3.0,
        "soon_bonus": 3.0,
        "preference_weight": 1.0,
    },
}


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:  # pythonw.exe 下没有控制台，日志仍要写进文件
        pass
    with PUSH_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log(f"读取 {path.name} 失败：{exc}，使用默认值")
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    user_cfg = load_json(CONFIG_FILE, {}) or {}
    return deep_merge(DEFAULT_CONFIG, user_cfg)


def save_config(cfg: dict) -> None:
    save_json(CONFIG_FILE, cfg)


def today_str() -> str:
    return date.today().isoformat()


def weekday_cn(d: date) -> str:
    return "周一周二周三周四周五周六周日"[d.weekday() * 2:d.weekday() * 2 + 2]


# ---------------------------------------------------------------- 演出数据处理

_MARKETING = re.compile(
    r"周一至周日|周末|精品|爆笑|天天|开心|解压|综艺卡司|吐槽|校服换装|校园教室|"
    r"角色扮演|互动表演|沉浸式体验|悬疑反转|互动天花板|超值|限时|特惠|预售|"
    r"官方|正版|门票|购票|强烈推荐|必看|经典|长期有效|免费|公益|LIVE|现场",
    re.I)


def clean_keyword(title: str) -> str:
    """把冗长的营销标题压成短而准的大麦搜索词（有剧名优先用剧名）。"""
    t = html.unescape(title or "")
    m = re.search(r"《([^》]{2,28})》", t)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()[:28]
    t = re.split(r"[|｜]", t)[0]
    t = re.sub(r"[#＃][^\s]{1,12}", " ", t)
    t = re.sub(r"[【\[][^】\]]{1,24}[】\]]", " ", t)   # 去掉【剧社】【场地】宣传前缀
    t = re.sub(r"[＞><《》〈〉（）()\"'“”‘’!！,，。·:;；~—\-&.]", " ", t)
    t = _MARKETING.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:26]


def display_title(title: str, n: int = 40) -> str:
    """日报/推送用的精简标题：砍掉竖线后的营销长尾，保留剧名。"""
    t = re.split(r"[|｜]", html.unescape(title or ""))[0].strip()
    m = re.search(r"《([^》]{2,30})》", t)
    if m:
        t = (t[:m.start()][:16] + "《" + m.group(1) + "》")
    t = t.replace("[", "【").replace("]", "】").replace("\n", " ")
    return t[:n] + ("…" if len(t) > n else "")


def damai_search_url(title: str) -> str:
    """主力购票入口：直接跳到大麦搜索结果页（永远可用，不依赖易变的 itemId）。"""
    return "https://search.damai.cn/search.htm?keyword=" + urllib.parse.quote(clean_keyword(title))


def douban_ticket_url(detail_url: str) -> str:
    """豆瓣「购票直通车」：会跳转到该演出对应的猫眼/票务页面（更精准，可能需登录）。"""
    m = re.search(r"/event/(\d+)", detail_url or "")
    return f"https://www.douban.com/event/{m.group(1)}/ticket_auth" if m else ""


def parse_price(text: str) -> float:
    if not text:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)", str(text).replace(",", ""))
    return float(m.group(1)) if m else 0.0


def price_band(price_min: float) -> str:
    if price_min <= 0:
        return "免费/未标价"
    if price_min < 100:
        return "100元以下"
    if price_min < 300:
        return "100-300元"
    if price_min < 600:
        return "300-600元"
    if price_min < 1000:
        return "600-1000元"
    return "1000元以上"


def categorize(title: str, hint: str = "") -> str:
    """优先采用豆瓣精确子分类；否则按标题关键词推断。"""
    if hint in CATEGORY_ORDER:
        return hint
    t = (title or "").lower()
    for cat, words in _TITLE_RULES:
        for w in words:
            if w.lower() in t:
                return cat
    return "其他"


def extract_tags(show: dict) -> list[str]:
    """从一场演出里提取用于偏好学习的特征标签。"""
    title = show.get("title", "")
    tags = {show.get("category", "其他")}
    for m in re.findall(r"[《【\[]([^》】\]]{2,24})[》】\]]", title):
        tags.add(m.strip()[:20])
    for kw in KNOWN_TAGS:
        if kw.lower() in title.lower():
            tags.add(kw)
    venue = (show.get("venue") or "").split()[0] if show.get("venue") else ""
    if venue:
        tags.add(venue[:16])
    if show.get("organizer") and show["organizer"] not in ("猫眼演出", "豆瓣"):
        tags.add(show["organizer"][:16])
    return [t for t in tags if t][:12]


def dedupe_key(show: dict) -> str:
    """跨来源去重：同名 + 同城 + 同一开演日期视为同一场。"""
    title = re.sub(r"\s+", "", clean_keyword(show.get("title", "")))[:24]
    start = (show.get("start") or "")[:10]
    city = show.get("city", "")
    return f"{title}|{city}|{start}"


def enrich(show: dict) -> dict:
    """补齐派生字段，保证任何来源的数据都能被界面/日报安全使用。"""
    s = dict(show)
    s.setdefault("category", categorize(s.get("title", ""), s.get("category_hint", "")))
    s.setdefault("city", "")
    s.setdefault("venue", "")
    s.setdefault("price", "")
    s["price_min"] = parse_price(s.get("price"))
    s["price_band"] = price_band(s["price_min"])
    if not s.get("buy_url"):
        s["buy_url"] = damai_search_url(s.get("title", ""))
    if s.get("detail_url", "").find("douban.com/event/") >= 0:
        s["ticket_url"] = douban_ticket_url(s["detail_url"])
    else:
        s.setdefault("ticket_url", s.get("buy_url"))
    s["tags"] = extract_tags(s)
    if not s.get("id"):
        s["id"] = "x-" + hashlib.md5(dedupe_key(s).encode("utf-8")).hexdigest()[:12]
    return s


def active_shows(shows: list[dict], today: str | None = None) -> list[dict]:
    """只剔除已经结束的演出（日期不可逆），其它一律保留 —— 这是「全量」的边界。"""
    today = today or today_str()
    out = []
    for s in shows:
        end = (s.get("end") or s.get("start") or "")[:10]
        if end and end < today:
            continue
        out.append(s)
    return out


# ---------------------------------------------------------------- 偏好记忆

def default_prefs() -> dict:
    return {
        "weights": {"category": {}, "tag": {}, "venue": {}, "city": {}, "price_band": {}},
        "actions": [],
        "purchases": [],
        "updated_at": "",
    }


def load_prefs() -> dict:
    p = load_json(PREFS_FILE, None) or default_prefs()
    for k in default_prefs():
        p.setdefault(k, default_prefs()[k])
    for k in default_prefs()["weights"]:
        p["weights"].setdefault(k, {})
    return p


def apply_action(prefs: dict, show: dict, action: str) -> dict:
    """把一次行为沉淀进长期偏好。同一场演出重复点「已购买」不会重复计权。"""
    if action not in ACTION_WEIGHT:
        return prefs
    seen = {(a.get("show_id"), a.get("action")) for a in prefs["actions"]}
    if action in ("bought", "want") and (show.get("id"), action) in seen:
        # 同一场演出重复点「已购买 / 想看」不重复计权
        return prefs
    if action == "click":
        # 同一场演出一天只累计一次「看过购票页」，避免反复点击刷权重
        today = date.today().isoformat()
        if any(a.get("show_id") == show.get("id") and a.get("action") == "click"
               and (a.get("ts") or "").startswith(today) for a in prefs["actions"]):
            return prefs

    w = ACTION_WEIGHT[action]
    weights = prefs["weights"]
    weights["category"][show.get("category", "其他")] = (
        weights["category"].get(show.get("category", "其他"), 0.0) + w * 1.0)
    tag_n = max(1, len(show.get("tags") or []))
    for tag in (show.get("tags") or []):
        weights["tag"][tag] = weights["tag"].get(tag, 0.0) + w * 0.55 / math.sqrt(tag_n)
    if show.get("venue"):
        weights["venue"][show["venue"]] = weights["venue"].get(show["venue"], 0.0) + w * 0.45
    if show.get("city"):
        weights["city"][show["city"]] = weights["city"].get(show["city"], 0.0) + w * 0.35
    pb = show.get("price_band") or price_band(parse_price(show.get("price")))
    weights["price_band"][pb] = weights["price_band"].get(pb, 0.0) + w * 0.25

    entry = {
        "show_id": show.get("id"), "title": show.get("title", ""),
        "category": show.get("category", ""), "action": action,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    prefs["actions"].append(entry)
    prefs["actions"] = prefs["actions"][-500:]
    if action == "bought":
        prefs["purchases"].append({**entry, "buy_url": show.get("buy_url", "")})
    prefs["updated_at"] = entry["ts"]
    save_json(PREFS_FILE, prefs)
    return prefs


def score_show(show: dict, prefs: dict, today: str | None = None,
               cfg: dict | None = None) -> tuple[float, list[str], bool, float]:
    """给一场演出打分：偏好分 + 客观时效分。分数只用来排序，从不用来过滤。"""
    today = today or today_str()
    cfg = cfg or load_config()
    r = cfg.get("ranking", {})
    w = prefs.get("weights", {})
    reasons: list[str] = []
    score = 0.0
    k = float(r.get("preference_weight", 1.0))

    cw = w.get("category", {}).get(show.get("category", ""), 0.0)
    if cw:
        score += 2.2 * cw * k
        if cw > 0:
            reasons.append(f"你常看{show['category']}")
    tw = sum(w.get("tag", {}).get(t, 0.0) for t in show.get("tags", []))
    if tw:
        score += 1.6 * tw * k
        hit = max(show.get("tags", []), key=lambda t: w.get("tag", {}).get(t, 0.0), default="")
        if hit and w.get("tag", {}).get(hit, 0) > 0:
            reasons.append(f"关联「{hit}」")
    vw = w.get("venue", {}).get(show.get("venue", ""), 0.0)
    if vw:
        score += 0.9 * vw * k
        if vw > 0:
            reasons.append("常去的场馆")
    c_w = w.get("city", {}).get(show.get("city", ""), 0.0)
    if c_w:
        score += 0.5 * c_w * k
    pw = w.get("price_band", {}).get(show.get("price_band", ""), 0.0)
    if pw:
        score += 0.4 * pw * k

    pref_score = score  # 只由偏好产生的分数，用来控制 ⭐ 数量
    # 客观因子：新上架、临近开演、热度（不因偏好而隐藏任何一场）
    if show.get("first_seen") == today:
        score += float(r.get("new_bonus", 3.0))
        reasons.append("今日新收录")
    start = (show.get("start") or "")[:10]
    if start:
        try:
            days = (date.fromisoformat(start) - date.fromisoformat(today)).days
            if 0 <= days <= 14:
                score += float(r.get("soon_bonus", 3.0)) * (1 - days / 14)
                if days <= 3:
                    reasons.append("快开演了")
        except ValueError:
            pass
    interest = float(show.get("interest") or 0)
    score += min(1.2, math.log(interest + 1) * 0.25)

    liked = cw > 0 or tw > 0 or vw > 0
    return round(score, 3), reasons[:3], bool(liked), round(pref_score, 3)


def rank(shows: list[dict], prefs: dict, today: str | None = None,
         cfg: dict | None = None) -> dict:
    """全量分组排序。

    ⚠️ 关键：返回的条目数 == 传入的条目数，一场都不少。
    偏好只在每个品类分组内部决定前后，品类本身按固定顺序全部铺开。
    """
    today = today or today_str()
    scored = []
    for s in shows:
        sc, reasons, liked, pref = score_show(s, prefs, today, cfg)
        item = dict(s)
        item["_score"] = sc
        item["_reasons"] = reasons
        item["_liked"] = liked
        item["_pref"] = pref
        item["_new"] = s.get("first_seen") == today
        scored.append(item)

    groups_map: dict[str, list[dict]] = {}
    for item in scored:
        groups_map.setdefault(item.get("category") or "其他", []).append(item)
    for cat in groups_map:
        items = groups_map[cat]
        items.sort(key=lambda x: (-x["_score"], x.get("start") or "9999"))
        # ⭐ 只给每组里偏好分最高的一小撮，避免「全场都是星」失去意义；
        # 注意：这只是标记，排序和收录仍然覆盖全部演出。
        k = max(1, math.ceil(len(items) * 0.3))
        top_ids = {x["id"] for x in sorted(items, key=lambda y: -y["_pref"])[:k]}
        for x in items:
            x["_liked"] = x["_pref"] > 0 and x["id"] in top_ids

    ordered = [c for c in CATEGORY_ORDER if c in groups_map]
    ordered += sorted(c for c in groups_map if c not in CATEGORY_ORDER)
    groups = [{"category": c, "icon": CATEGORY_ICON.get(c, "✨"),
               "items": groups_map[c]} for c in ordered]

    total_out = sum(len(g["items"]) for g in groups)
    assert total_out == len(shows), "全量约束被破坏：日报漏掉了演出"
    return {
        "date": today,
        "weekday": weekday_cn(date.fromisoformat(today)),
        "groups": groups,
        "total": total_out,
        "category_count": len(groups),
        "new_today": sum(1 for g in groups for i in g["items"] if i["_new"]),
        "liked_count": sum(1 for g in groups for i in g["items"] if i["_liked"]),
    }


# ---------------------------------------------------------------- 日报生成

def short_date(text: str) -> str:
    """2026-11-13T19:30:00 / 11月13日 周五 19:30 -> 11/13 19:30"""
    if not text:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}:\d{2})", text)
    if m:
        return f"{m.group(2)}/{m.group(3)} {m.group(4)}"
    m2 = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m2:
        t = re.search(r"(\d{1,2}:\d{2})", text)
        return f"{m2.group(1)}/{m2.group(2)}" + (f" {t.group(1)}" if t else "")
    return text[:16]


def fmt_price(show: dict) -> str:
    p = (show.get("price") or "").strip().replace("￥", "¥")
    p = re.sub(r"¥\s*", "¥", p)
    if not p or p == "¥":
        return "价格待定"
    return p if ("元" in p or "¥" in p) else f"¥{p}"


def build_markdown(ranked: dict, mode: str = "compact") -> str:
    """生成日报 Markdown。compact 每条一行且标题本身就是购票链接，手机上一点直达买票页。"""
    head = [
        f"# {CATEGORY_ICON['话剧']} 演出日报 · {ranked['date']}（{ranked['weekday']}）", "",
        f"**共 {ranked['total']} 场 · 覆盖 {ranked['category_count']} 个品类 · 今日新增 {ranked['new_today']} 场**", "",
        "> 全部演出都在这里，不会因为你不喜欢就藏起来；⭐ 只是把你可能喜欢的排到每组前面。", "",
    ]
    lines = []
    for g in ranked["groups"]:
        lines.append(f"## {g['icon']} {g['category']}（{len(g['items'])}）")
        lines.append("")
        for i, s in enumerate(g["items"], 1):
            star = "⭐ " if s["_liked"] else ""
            new = " 🆕" if s["_new"] else ""
            when = short_date(s.get("start") or s.get("date_text"))
            parts = [p for p in (when, s.get("venue", ""), fmt_price(s)) if p]
            meta = " ｜ ".join(parts)
            label = display_title(s["title"])
            if mode == "full":
                title_link = f"[{label}]({s.get('detail_url') or s['buy_url']})"
                buy = f"[去大麦买票]({s['buy_url']})"
                if s.get("ticket_url") and s.get("on_selling"):
                    buy += f" · [豆瓣购票直通车]({s['ticket_url']})"
                lines.append(f"{i}. {star}**{title_link}**{new}  \n   {meta}  \n   {buy}")
            else:
                title_link = f"[{label}]({s['buy_url']})"
                lines.append(f"{i}. {star}{title_link}{new} ｜ {meta}")
        lines.append("")
    lines.append("---")
    lines.append("数据来源：豆瓣同城（在售演出）+ 自定义源。购票链接跳转大麦搜索结果页；点开后请核对场次与票价。")
    return "\n".join(head + lines)


def split_messages(md: str, max_bytes: int = 3200) -> list[str]:
    """按「品类章节」拆成多条推送，保证一条都不被截断（面面俱到）。

    只有第一条带完整抬头，后续消息用一行短抬头，尽量少占手机通知。
    """
    lines = md.splitlines(keepends=True)
    header, sections, cur = [], [], []
    in_header = True
    for ln in lines:
        if ln.startswith("## "):
            in_header = False
            if cur:
                sections.append(cur)
            cur = [ln]
        elif in_header:
            header.append(ln)
        else:
            cur.append(ln)
    if cur:
        sections.append(cur)

    full_head = "".join(header)
    short_head = "> 演出日报（续）· 全量汇总，⭐ 仅表示偏好置顶\n\n"
    head = lambda i: full_head if i == 0 else short_head  # noqa: E731
    limit = max(700, max_bytes) - 300
    messages, buf, size = [], [], 0
    for sec in sections:
        sec_text = "".join(sec)
        b = len(sec_text.encode("utf-8"))
        if b > limit:  # 单个超长品类再按行切
            for ln in sec:
                lb = len(ln.encode("utf-8"))
                if size + lb > limit and buf:
                    messages.append(head(len(messages)) + "".join(buf))
                    buf, size = [], 0
                buf.append(ln)
                size += lb
            continue
        if size + b > limit and buf:
            messages.append(head(len(messages)) + "".join(buf))
            buf, size = [], 0
        buf.append(sec_text)
        size += b
    if buf:
        messages.append(head(len(messages)) + "".join(buf))
    return messages or [md]


def build_report_html(ranked: dict) -> str:
    """独立 HTML 日报（可本地查看，也可随时发布到线上给手机打开）。"""
    css = """
    body{margin:0;background:#f7f5f1;color:#242220;font:15px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}
    .wrap{max-width:860px;margin:0 auto;padding:28px 18px 60px}
    h1{font-size:26px;margin:0 0 6px} .sub{color:#777;font-size:14px;margin-bottom:18px}
    .note{background:#fff5e8;border:1px solid #f0dcc0;border-radius:10px;padding:12px 14px;margin-bottom:24px;font-size:14px;color:#8a5a20}
    h2{font-size:19px;margin:28px 0 12px;padding-bottom:8px;border-bottom:1px solid #e6e0d6}
    .item{background:#fff;border:1px solid #ece7df;border-radius:12px;padding:14px 16px;margin-bottom:10px}
    .t{font-size:15px;font-weight:600} .t a{color:#242220;text-decoration:none}
    .m{color:#888;font-size:13px;margin:6px 0 10px}
    .btn{display:inline-block;padding:5px 12px;border-radius:7px;background:#b3372c;color:#fff;text-decoration:none;font-size:13px;margin-right:8px}
    .btn2{background:#f1ece4;color:#5b5148} .star{color:#d98b18} .new{color:#b3372c;font-size:12px}
    footer{color:#999;font-size:12px;margin-top:34px;text-align:center}
    """
    out = ["<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>演出日报 {ranked['date']}</title><style>{css}</style></head><body><div class='wrap'>",
           f"<h1>🎭 演出日报 · {ranked['date']}（{ranked['weekday']}）</h1>",
           f"<div class='sub'>共 {ranked['total']} 场 · {ranked['category_count']} 个品类 · 今日新增 {ranked['new_today']} 场</div>",
           "<div class='note'>全部演出都在这里，不会因为偏好藏起任何一场；⭐ 只是把你可能喜欢的排到每组前面。</div>"]
    for g in ranked["groups"]:
        out.append(f"<h2>{g['icon']} {html.escape(g['category'])}（{len(g['items'])}）</h2>")
        for s in g["items"]:
            star = "<span class='star'>⭐ </span>" if s["_liked"] else ""
            new = " <span class='new'>NEW</span>" if s["_new"] else ""
            meta = " ｜ ".join(x for x in (short_date(s.get("start") or s.get("date_text")),
                                          s.get("venue", ""), fmt_price(s)) if x)
            out.append("<div class='item'>")
            out.append(f"<div class='t'>{star}<a href='{html.escape(s.get('buy_url',''), quote=True)}' target='_blank'>{html.escape(s['title'])}</a>{new}</div>")
            out.append(f"<div class='m'>{html.escape(meta)}</div>")
            out.append(f"<a class='btn' href='{html.escape(s.get('buy_url',''), quote=True)}' target='_blank'>去大麦买票</a>")
            if s.get("detail_url"):
                out.append(f"<a class='btn btn2' href='{html.escape(s['detail_url'], quote=True)}' target='_blank'>演出详情</a>")
            out.append("</div>")
    out.append("<footer>演出雷达 · 全量汇总，偏好仅影响排序</footer></div></body></html>")
    return "\n".join(out)
