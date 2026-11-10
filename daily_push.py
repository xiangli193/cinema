# -*- coding: utf-8 -*-
"""每日定时任务入口：抓取最新演出 → 生成「面面俱到」的日报 → 推送到手机。

由 Windows 任务计划程序每天自动调用；也可以手动双击 run_daily.bat 跑一次。

常用参数：
  python daily_push.py --dry-run      只看日报不推送（存到 reports/）
  python daily_push.py --no-fetch     不联网，用现有数据生成并推送
  python daily_push.py --mode full    推送详细版（默认 compact 精简版，省流量）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core import (  # noqa: E402
    REPORTS, SHOWS_FILE, active_shows, build_markdown, build_report_html,
    load_config, load_json, load_prefs, log, rank, split_messages, today_str,
)
from app.fetch import refresh_all  # noqa: E402
from app.push import send_all  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="演出日报：全量汇总 + 推送到手机")
    ap.add_argument("--dry-run", action="store_true", help="只生成日报，不发送推送")
    ap.add_argument("--no-fetch", action="store_true", help="跳过联网采集")
    ap.add_argument("--mode", choices=["compact", "full"], default=None)
    ap.add_argument("--test-one", action="store_true", help="只推送第一条消息，用于测试通道")
    args = ap.parse_args()

    cfg = load_config()
    today = today_str()
    log("=" * 60)
    log(f"开始每日任务 {today}")

    if not args.no_fetch:
        try:
            stats = refresh_all(cfg)
            log(f"采集：库内 {stats['total']} 场，新增 {stats['new']} 场")
        except Exception as exc:  # noqa: BLE001
            log(f"采集异常（改用已有数据）：{exc}")
    else:
        log("跳过采集，使用本地已有数据")

    shows = active_shows(load_json(SHOWS_FILE, []) or [], today)
    prefs = load_prefs()
    ranked = rank(shows, prefs, today, cfg)
    log(f"日报生成：{ranked['total']} 场 / {ranked['category_count']} 个品类 / "
        f"新增 {ranked['new_today']} / 偏好命中 {ranked['liked_count']}")

    mode = args.mode or cfg.get("push", {}).get("mode", "compact")
    md = build_markdown(ranked, mode)
    (REPORTS / f"{today}.md").write_text(md, encoding="utf-8")
    (REPORTS / "latest.md").write_text(md, encoding="utf-8")
    html = build_report_html(ranked)
    (REPORTS / f"{today}.html").write_text(html, encoding="utf-8")
    (REPORTS / "latest.html").write_text(html, encoding="utf-8")

    max_bytes = int(cfg.get("push", {}).get("max_bytes_per_message", 3200))
    messages = split_messages(md, max_bytes)
    if args.test_one:
        messages = messages[:1]
    log(f"日报已保存 reports/{today}.md 与 .html，共拆成 {len(messages)} 条推送")

    if args.dry_run:
        log("dry-run：跳过推送")
        print("\n" + md[:1200] + "\n…（完整内容见 reports/）")
        return 0

    results = send_all(cfg, messages, today)
    channels = [c for c in cfg.get("push", {}).get("channels", []) if c.get("enabled")]
    if channels and results and all(r["sent"] == 0 for r in results):
        log("所有推送通道都失败了，请检查 logs/push.log")
        return 1
    log("每日任务结束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
