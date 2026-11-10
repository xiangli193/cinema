# -*- coding: utf-8 -*-
"""推送到手机：Server酱 / PushPlus / Bark / 企业微信机器人 / 钉钉 / Telegram / 通用 Webhook。

全部只用标准库 urllib。没配置任何通道时，日报仍然会落到 reports/，不会白跑一趟。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request

from .core import log

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CHANNEL_HELP = {
    "serverchan": "Server酱（微信推送，推荐）：https://sct.ftqq.com/ 扫码拿 SendKey",
    "pushplus":   "PushPlus（微信公众号推送）：https://www.pushplus.plus/ 拿 token",
    "bark":       "Bark（iPhone 推送）：App Store 装 Bark，复制推送地址里的 key",
    "wecom_bot":  "企业微信群机器人：群设置 → 群机器人 → 复制 Webhook",
    "dingtalk":   "钉钉群机器人：群设置 → 智能群助手 → 复制 Webhook（可选加签 secret）",
    "telegram":   "Telegram Bot：@BotFather 拿 bot_token，@userinfobot 拿 chat_id",
    "webhook":    "自定义 Webhook：POST 一个 JSON {title, content, date}",
    "local":      "只保存到本地 reports/ 目录（不用手机推送时选这个）",
}


def _request(url: str, data: bytes | None = None, headers: dict | None = None,
             timeout: int = 20) -> tuple[bool, str]:
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")[:300]
            return 200 <= resp.status < 300, f"HTTP {resp.status} {body}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _post_json(url: str, payload: dict, headers: dict | None = None) -> tuple[bool, str]:
    return _request(url, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    {"Content-Type": "application/json; charset=utf-8", **(headers or {})})


def _post_form(url: str, form: dict) -> tuple[bool, str]:
    return _request(url, urllib.parse.urlencode(form).encode("utf-8"),
                    {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})


def send_one(channel: dict, title: str, content: str, date_str: str) -> tuple[bool, str]:
    t = (channel.get("type") or "").lower()
    if t == "local":
        return True, "已保存到本地 reports/"

    if t == "serverchan":
        key = channel.get("sendkey", "")
        if not key:
            return False, "缺少 sendkey"
        url = key if key.startswith("http") else f"https://sctapi.ftqq.com/{key}.send"
        return _post_form(url, {"title": title, "desp": content, "channel": channel.get("channel", "")})

    if t == "pushplus":
        token = channel.get("token", "")
        if not token:
            return False, "缺少 token"
        return _post_json("https://www.pushplus.plus/send", {
            "token": token, "title": title, "content": content, "template": "markdown",
            "topic": channel.get("topic", ""),
        })

    if t == "bark":
        key = channel.get("key", "")
        if not key:
            return False, "缺少 Bark key"
        server = (channel.get("server") or "https://api.day.app").rstrip("/")
        if channel.get("mode") == "get":
            url = f"{server}/{urllib.parse.quote(key)}/{urllib.parse.quote(title)}/{urllib.parse.quote(content)}"
            return _request(url, headers={"Content-Type": "application/x-www-form-urlencoded"})
        return _post_json(f"{server}/push", {
            "device_key": key, "title": title, "body": content,
            "group": "演出日报", "sound": "minuet", "icon": "https://api.day.app/favicon.png",
        })

    if t == "wecom_bot":
        hook = channel.get("webhook", "")
        if not hook:
            return False, "缺少 webhook"
        return _post_json(hook, {"msgtype": "markdown",
                                 "markdown": {"content": content}})

    if t == "dingtalk":
        hook = channel.get("webhook", "")
        if not hook:
            return False, "缺少 webhook"
        secret = channel.get("secret", "")
        if secret:
            ts = str(round(time.time() * 1000))
            sign = urllib.parse.quote_plus(base64.b64encode(hmac.new(
                secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()).decode())
            hook += ("&" if "?" in hook else "?") + f"timestamp={ts}&sign={sign}"
        return _post_json(hook, {"msgtype": "markdown",
                                 "markdown": {"title": title, "text": content}})

    if t == "telegram":
        token, chat = channel.get("bot_token", ""), channel.get("chat_id", "")
        if not token or not chat:
            return False, "缺少 bot_token / chat_id"
        ok, detail = _post_form(f"https://api.telegram.org/bot{token}/sendMessage",
                                {"chat_id": chat, "text": content,
                                 "parse_mode": "Markdown", "disable_web_page_preview": "true"})
        if not ok:  # Markdown 语法偶尔不兼容，退回纯文本重试
            ok, detail = _post_form(f"https://api.telegram.org/bot{token}/sendMessage",
                                    {"chat_id": chat, "text": content,
                                     "disable_web_page_preview": "true"})
        return ok, detail

    if t == "webhook":
        hook = channel.get("url", "")
        if not hook:
            return False, "缺少 url"
        return _post_json(hook, {"title": title, "content": content, "date": date_str})

    return False, f"未知通道类型：{t}"


def send_all(cfg: dict, messages: list[str], date_str: str) -> list[dict]:
    """逐条推送所有分块；任何一条失败都不会阻断其它通道。"""
    channels = [c for c in cfg.get("push", {}).get("channels", []) if c.get("enabled")]
    if not channels:
        log("未启用任何推送通道，日报已保存在 reports/（运行 python setup.py 配置手机推送）")
        return []
    results = []
    total = len(messages)
    for ch in channels:
        t = ch.get("type", "?")
        sent, failed = 0, []
        for idx, body in enumerate(messages, 1):
            title = f"演出日报 {date_str}" if total == 1 else f"演出日报 {date_str} ({idx}/{total})"
            ok, detail = send_one(ch, title, body, date_str)
            if ok:
                sent += 1
            else:
                failed.append(f"第{idx}条：{detail}")
            time.sleep(0.4)
        item = {"type": t, "sent": sent, "total": total, "failed": failed}
        results.append(item)
        log(f"通道 {t} 推送结果：成功 {sent}/{total}" + (f"，失败 {failed}" if failed else ""))
    return results
