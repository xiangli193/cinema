# -*- coding: utf-8 -*-
"""云端偏好记忆：让「关机也能推送」的同时，手机上依然能记喜好。

云端沙箱每次运行都是干净环境，读不到你电脑上的文件。因此喜好存在仓库的
data/preferences.json 里（由 workflow 每天回写），而手机端的输入走 GitHub Issue：

  1. 推送里带一个「记一笔」链接，点开是预填好的新建 Issue 页（带 pref 标签）；
  2. 标题写 `我买了 音乐剧`、`想看 剧院名`、`看过了 xxx演出` 之类，提交即可；
  3. 次日云端任务读取这些 open issue → 累积到偏好权重 → 关闭 issue 并回复确认。

偏好只影响同品类内部的先后顺序，绝不会过滤掉任何一场演出（全量铁律不变）。
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request

from app.core import apply_action, categorize, enrich, log

API = "https://api.github.com"

# 标题写法 -> 行为。写得宽松一点，手机上打字越省事越好。
_RULES = [
    (r"^(?:我买了|买了|已买|已购|买过|bought)\s*[:：]?\s*(.+)$", "bought"),
    (r"^(?:想看|想买|收藏|mark|want)\s*[:：]?\s*(.+)$", "want"),
    (r"^(?:看过了|我看过|看过|点过购票|点过了|点过|逛过|click)\s*[:：]?\s*(.+)$", "click"),
]


def parse_pref_title(title: str) -> tuple[str, str] | None:
    """把一条 Issue 标题解析成 (action, 关键词)。无法识别返回 None。"""
    t = (title or "").strip()
    for pattern, action in _RULES:
        m = re.match(pattern, t, re.I)
        if not m:
            continue
        kw = re.sub(r"[《》【】\[\]「」\"'“”‘’]", "", m.group(1)).strip()
        kw = re.sub(r"\s+", " ", kw)[:60]
        if len(kw) >= 2:
            return action, kw
    return None


def _gh(method: str, path: str, token: str, payload: dict | None = None) -> dict | list | None:
    url = API + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "show-radar-cloud",
        "Content-Type": "application/json; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}
    except Exception as exc:  # noqa: BLE001
        log(f"GitHub API {method} {path} 失败：{exc}")
        return None


def _find_show(shows: list[dict], kw: str) -> dict | None:
    """在演出库里找最贴近关键词的一场；找不到就返回 None（用虚拟条目记品类偏好）。"""
    k = re.sub(r"\s+", "", kw).lower()
    best, best_score = None, 0.0
    for s in shows:
        t = re.sub(r"\s+", "", s.get("title", "")).lower()
        if not t:
            continue
        if k == t:
            score = 3.0
        elif k in t:
            score = 2.0 + min(len(k) / max(len(t), 1), 0.5)
        elif t in k:
            score = 1.5
        else:
            score = 0.0
        if score > best_score:
            best, best_score = s, score
    return best


def _virtual_show(kw: str, city: str) -> dict:
    """关键词没匹配到具体演出时，也把这份喜好记下来（例如「我买了 音乐剧」）。"""
    s = enrich({
        "title": kw,
        "category": categorize(kw),
        "city": city,
        "venue": "",
        "price": "",
    })
    s["tags"] = list(dict.fromkeys([kw, *(s.get("tags") or [])]))[:12]
    s["id"] = "manual-" + hashlib.md5(kw.encode("utf-8")).hexdigest()[:10]
    return s


def pref_new_issue_url(repo: str) -> str:
    """手机端记喜好的入口：打开就是预填好标题的新建 Issue 页面。"""
    if not repo:
        return ""
    return (f"https://github.com/{repo}/issues/new?labels=pref&title="
            + urllib.parse.quote("我买了 "))


def sync_prefs_from_issues(prefs: dict, shows: list[dict], repo: str = "",
                           token: str = "", city: str = "") -> tuple[dict, list[str]]:
    """读取带 pref 标签的 open issue → 累积偏好 → 关闭并回复。返回 (偏好, 已处理说明)。"""
    if not repo or not token:
        return prefs, []
    issues = _gh("GET", f"/repos/{urllib.parse.quote(repo)}/issues?labels=pref&state=open&per_page=50", token)
    if not isinstance(issues, list):
        return prefs, []
    applied: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        number = issue.get("number")
        parsed = parse_pref_title(issue.get("title", ""))
        if not parsed:
            log(f"Issue #{number} 标题无法识别，跳过（标题写「我买了 xxx」或「想看 xxx」）")
            continue
        action, kw = parsed
        show = _find_show(shows, kw) or _virtual_show(kw, city)
        prefs = apply_action(prefs, show, action)
        label = {"bought": "我买了", "want": "想看", "click": "看过购票页"}[action]
        note = f"{label}「{kw}」" + ("" if show.get("id", "").startswith("manual-") else "（已匹配到具体演出）")
        applied.append(note)
        _gh("POST", f"/repos/{urllib.parse.quote(repo)}/issues/{number}/comments", token,
            {"body": f"已记下：{note}。明天的日报会把它排到同品类靠前位置；"
                     "日报仍是全量的，不会因此少掉任何一场演出。"})
        _gh("PATCH", f"/repos/{urllib.parse.quote(repo)}/issues/{number}", token,
            {"state": "closed"})
        log(f"偏好已同步：Issue #{number} → {note}")
    return prefs, applied
