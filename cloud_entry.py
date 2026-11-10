# -*- coding: utf-8 -*-
"""云端每日入口：给 GitHub Actions / 任意云主机调用，完全不需要你的电脑开机。

与本机版 daily_push.py 的区别：
  · 不读本地 config.json（本机配置可能含密钥，仓库里已忽略），全部配置来自环境变量 / GitHub Secrets；
  · 演出库 data/shows.json 与喜好 data/preferences.json 存在仓库里，workflow 每天回写提交，
    所以云端沙箱每次都是干净环境也不会丢「新上架」判断和长期喜好；
  · 手机端「我买了 / 想看」通过 GitHub Issue 反馈（见 cloud_prefs.py），推送里带一键入口；
  · 额外产出 docs/index.html，开启 GitHub Pages 后手机上可看完整网页版日报。

环境变量（在 GitHub 仓库 Settings → Secrets 里填，填哪个就启用哪个通道，可同时多个）：
  SERVERCHAN_KEY      Server酱 SendKey（微信收推，推荐）
  SERVERCHAN_CHANNEL  可选，Server酱通道号
  PUSHPLUS_TOKEN      PushPlus token
  BARK_KEY            Bark key（iPhone）
  BARK_SERVER         可选，自建 Bark 服务地址
  WECOM_WEBHOOK       企业微信群机器人 Webhook
  DINGTALK_WEBHOOK    钉钉群机器人 Webhook
  DINGTALK_SECRET     可选，钉钉加签密钥
  TELEGRAM_BOT_TOKEN  Telegram bot token（需同时填 TELEGRAM_CHAT_ID）
  TELEGRAM_CHAT_ID
  CUSTOM_WEBHOOK      自定义 Webhook（POST JSON）
  SHOW_CITIES         城市拼音，逗号分隔，默认 beijing
  PUSH_MODE           compact / full，默认 compact
  MAX_BYTES           单条推送最大字节，默认 3800
  PAGES_URL           网页版日报地址；留空则按 GITHUB_REPOSITORY 自动推断
  GITHUB_REPOSITORY   Actions 自动注入，格式 owner/repo
  GITHUB_TOKEN        Actions 自动注入（由 workflow 的 permissions 授权），用于同步喜好

本地试跑：  python cloud_entry.py --dry-run
手动触发云端任务：GitHub 仓库 → Actions → 演出日报 → Run workflow
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.core import (  # noqa: E402
    REPORTS, SHOWS_FILE, active_shows, build_markdown, build_report_html, load_config,
    load_json, load_prefs, log, rank, save_json, split_messages, today_str,
)
from app.fetch import refresh_all  # noqa: E402
from app.push import send_all  # noqa: E402
from cloud_prefs import pref_new_issue_url, sync_prefs_from_issues  # noqa: E402


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def channels_from_env() -> list[dict]:
    """填了哪个密钥就启用哪个通道，可以多个通道同时收推。"""
    out: list[dict] = []

    def add(kind: str, **kw):
        out.append({"type": kind, "enabled": True, **kw})

    if env("SERVERCHAN_KEY"):
        add("serverchan", sendkey=env("SERVERCHAN_KEY"), channel=env("SERVERCHAN_CHANNEL"))
    if env("PUSHPLUS_TOKEN"):
        add("pushplus", token=env("PUSHPLUS_TOKEN"))
    if env("BARK_KEY"):
        add("bark", key=env("BARK_KEY"), server=env("BARK_SERVER", "https://api.day.app"))
    if env("WECOM_WEBHOOK"):
        add("wecom_bot", webhook=env("WECOM_WEBHOOK"))
    if env("DINGTALK_WEBHOOK"):
        add("dingtalk", webhook=env("DINGTALK_WEBHOOK"), secret=env("DINGTALK_SECRET"))
    if env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
        add("telegram", bot_token=env("TELEGRAM_BOT_TOKEN"), chat_id=env("TELEGRAM_CHAT_ID"))
    if env("CUSTOM_WEBHOOK"):
        add("webhook", url=env("CUSTOM_WEBHOOK"))
    return out


def build_cloud_config() -> dict:
    cfg = load_config()
    cfg["cities"] = [c.strip().lower() for c in env("SHOW_CITIES", "beijing").split(",") if c.strip()]
    cfg["douban"]["enabled"] = env("DOUBAN_ENABLED", "1") != "0"
    cfg["push"]["mode"] = env("PUSH_MODE", "compact")
    try:
        cfg["push"]["max_bytes_per_message"] = int(env("MAX_BYTES", "3800"))
    except ValueError:
        cfg["push"]["max_bytes_per_message"] = 3800
    cfg["push"]["channels"] = channels_from_env()
    cfg["push"]["save_local"] = True
    return cfg


def cloud_links() -> tuple[str, str]:
    repo = env("GITHUB_REPOSITORY")
    pages = env("PAGES_URL")
    if not pages and repo and "/" in repo:
        owner, name = repo.split("/", 1)
        pages = f"https://{owner}.github.io/{name}/"
    return pages, pref_new_issue_url(repo) if repo else ""


def inject_header(md: str, extra: list[str]) -> str:
    """把云端入口插进日报抬头 —— split_messages 会把抬头带到每一条推送里。"""
    if not extra:
        return md
    i = md.find("\n## ")
    if i < 0:
        return md + "\n\n" + "\n".join(extra) + "\n"
    return md[:i + 1] + "\n".join(extra) + "\n\n" + md[i + 1:]


def inject_html_banner(html: str, links: list[str]) -> str:
    if not links:
        return html
    banner = ("<div class='note'>📱 " + " ｜ ".join(links) +
              "<br>记喜好：点「记一笔」，标题写 <b>我买了 音乐剧</b> 或 <b>想看 xxx演出名</b> 后提交，"
              "明天起同品类里会排前面，但一场都不会少。</div>")
    return html.replace("<div class='wrap'>", "<div class='wrap'>" + banner, 1)


def main() -> int:
    ap = argparse.ArgumentParser(description="云端演出日报：全量汇总 + 推送到手机")
    ap.add_argument("--dry-run", action="store_true", help="只生成日报，不发送推送")
    ap.add_argument("--no-fetch", action="store_true", help="跳过联网采集，用仓库里已有的数据")
    ap.add_argument("--mode", choices=["compact", "full"], default=None)
    ap.add_argument("--test-one", action="store_true", help="只推第一条，用于测试通道")
    args = ap.parse_args()

    cfg = build_cloud_config()
    today = today_str()
    log("=" * 60)
    log(f"云端任务开始 {today}｜城市 {cfg['cities']}｜推送通道 {len(cfg['push']['channels'])} 个")

    fetched, errors, new_count = 0, 0, 0
    if not args.no_fetch:
        try:
            stats = refresh_all(cfg)
            fetched, errors, new_count = stats["fetched"], len(stats["errors"]), stats["new"]
            log(f"采集：本次 {fetched} 条，新增 {new_count} 场，库内 {stats['total']} 场，失败页面 {errors} 个")
        except Exception as exc:  # noqa: BLE001
            log(f"采集异常（改用仓库里已有的演出库）：{exc}")
    else:
        log("跳过采集，使用仓库里已有的演出库")

    shows = active_shows(load_json(SHOWS_FILE, []) or [], today)
    if not shows:
        log("警告：演出库为空，日报将只有提示信息（多半是数据源风控，请查看运行日志）")

    prefs = load_prefs()
    prefs, applied = sync_prefs_from_issues(
        prefs, shows,
        repo=env("GITHUB_REPOSITORY"),
        token=env("GITHUB_TOKEN") or env("GH_TOKEN"),
        city=(cfg["cities"] or ["beijing"])[0],
    )
    if applied:
        log(f"手机端喜好已同步 {len(applied)} 条：{'；'.join(applied)}")

    ranked = rank(shows, prefs, today, cfg)
    log(f"日报：{ranked['total']} 场 / {ranked['category_count']} 个品类 / "
        f"新增 {ranked['new_today']} / 偏好命中 {ranked['liked_count']}")

    mode = args.mode or cfg["push"]["mode"]
    md = build_markdown(ranked, mode)
    pages, pref_url = cloud_links()
    extra: list[str] = []
    if fetched == 0 and not args.no_fetch:
        extra.append("> ⚠️ 今天数据源没有返回新内容（可能触发风控），下面用的是上一次成功抓取的演出库，请留意日期。")
    md_links = []
    if pages:
        md_links.append(f"[📱 网页版完整日报]({pages})")
    if pref_url:
        md_links.append(f"[⭐ 记一笔「我买了 / 想看」]({pref_url})")
    if md_links:
        extra.append("> " + " ｜ ".join(md_links))
    if pref_url:
        extra.append("> 记喜好：点上面链接，标题写 `我买了 音乐剧` 或 `想看 xxx演出名` 提交即可；"
                     "只影响同品类排序，任何一场都不会被藏起来。")
    md = inject_header(md, extra)

    (REPORTS / f"{today}.md").write_text(md, encoding="utf-8")
    (REPORTS / "latest.md").write_text(md, encoding="utf-8")

    html_links = []
    if pages:
        html_links.append(f"<a href='{pages}'>网页版</a>")
    if pref_url:
        html_links.append(f"<a href='{pref_url}' target='_blank'>⭐ 记一笔喜好</a>")
    html = inject_html_banner(build_report_html(ranked), html_links)
    (REPORTS / f"{today}.html").write_text(html, encoding="utf-8")
    (REPORTS / "latest.html").write_text(html, encoding="utf-8")
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text(html, encoding="utf-8")

    messages = split_messages(md, cfg["push"]["max_bytes_per_message"])
    if args.test_one:
        messages = messages[:1]
    log(f"日报已保存，拆成 {len(messages)} 条推送")

    results = []
    if args.dry_run:
        log("dry-run：跳过推送")
        print("\n" + md[:900] + "\n…（完整内容见 reports/latest.md）")
    else:
        if fetched == 0 and not args.no_fetch and cfg["push"]["channels"]:
            from app.push import send_one
            alert = (f"⚠️ 今天数据源没有返回任何演出（可能触发风控），"
                     f"下面这份日报用的是上一次成功抓取的 {len(shows)} 场，请留意日期。"
                     f"\n\n可在 GitHub 仓库 → Actions 查看本次运行日志，或手动重新运行一次。")
            for ch in cfg["push"]["channels"]:
                ok, detail = send_one(ch, f"演出日报 {today} · 数据源提醒", alert, today)
                log(f"数据源告警推送 {ch['type']}：{'成功' if ok else '失败 ' + detail}")
        results = send_all(cfg, messages, today)

    state = {
        "date": today,
        "total": ranked["total"],
        "categories": ranked["category_count"],
        "new_today": ranked["new_today"],
        "liked": ranked["liked_count"],
        "fetched": fetched,
        "failed_pages": errors,
        "messages": len(messages),
        "channels": len(cfg["push"]["channels"]),
        "sent": sum(r["sent"] for r in results),
        "prefs_synced": applied,
        "pages_url": pages,
        "repository": env("GITHUB_REPOSITORY"),
    }
    save_json(ROOT / "data" / "last_run.json", state)

    channels = cfg["push"]["channels"]
    if channels and results and all(r["sent"] == 0 for r in results):
        log("所有推送通道都失败了，请检查 Secrets 与 logs/push.log")
        return 1
    log("云端任务结束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
