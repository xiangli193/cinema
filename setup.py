# -*- coding: utf-8 -*-
"""一次性配置向导：城市 → 手机推送通道 → 试抓 → 试推 → 装定时任务。

  python setup.py                       交互式配置（推荐）
  python setup.py --channel serverchan --key SCTxxxx --city beijing --time 09:00 --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.core import CITY_NAMES, load_config, log, save_config  # noqa: E402
from app.push import CHANNEL_HELP  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

CHANNELS = [
    ("serverchan", "Server酱（微信推送，最省事）", [
        ("sendkey", "SendKey（或完整的 https://*.push.ft07.com/send/xxx.send 地址）", "")]),
    ("pushplus", "PushPlus（微信公众号推送）", [
        ("token", "token", ""), ("topic", "群组编码 topic（可留空）", "")]),
    ("bark", "Bark（iPhone 推送）", [
        ("key", "Bark 设备 key", ""), ("server", "服务器地址（可留空，默认 api.day.app）", "")]),
    ("wecom_bot", "企业微信群机器人", [("webhook", "Webhook 完整地址", "")]),
    ("dingtalk", "钉钉群机器人", [
        ("webhook", "Webhook 完整地址", ""), ("secret", "加签 secret（没加签就留空）", "")]),
    ("telegram", "Telegram Bot", [
        ("bot_token", "bot_token", ""), ("chat_id", "chat_id", "")]),
    ("webhook", "自定义 Webhook", [("url", "接收 POST JSON 的地址", "")]),
    ("local", "只存本地 reports/（稍后再配手机）", []),
]


def ask(prompt: str, default: str = "") -> str:
    suffix = f"（回车 = {default}）" if default else ""
    try:
        v = input(f"{prompt}{suffix}：").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return v or default


def print_channels():
    print("\n可选择的手机推送通道：")
    for i, (key, label, _) in enumerate(CHANNELS, 1):
        print(f"  {i}. {label}")
        print(f"     {CHANNEL_HELP.get(key, '')}")
    print("  0. 跳过（日报仍会每天生成在 reports/ 里）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", help="城市拼音，多个用逗号分隔，如 beijing,shanghai")
    ap.add_argument("--channel", help="通道类型，如 serverchan")
    ap.add_argument("--key", help="通道的主密钥/地址（sendkey / token / webhook / key）")
    ap.add_argument("--time", help="每天推送时间，如 09:00")
    ap.add_argument("--yes", action="store_true", help="全部使用默认值，不询问")
    ap.add_argument("--no-fetch", action="store_true", help="不试抓数据")
    ap.add_argument("--no-install", action="store_true", help="不安装定时任务")
    args = ap.parse_args()
    interactive = not (args.channel or args.yes)

    print("\n" + "=" * 62)
    print("  演出雷达 · 初始化配置")
    print("  每天汇总话剧 / 音乐剧 / 演唱会 / 音乐会等全部演出，并推送到手机")
    print("=" * 62)

    cfg = load_config()

    # 1. 城市
    cities_default = ",".join(cfg.get("cities", ["beijing"]))
    if args.city:
        cities = args.city
    elif interactive:
        print("\n【1/4】关注哪些城市？常用：beijing shanghai guangzhou shenzhen hangzhou chengdu nanjing wuhan xian")
        cities = ask("城市拼音（多个用逗号分隔）", cities_default)
    else:
        cities = cities_default
    chosen = [c.strip().lower() for c in cities.split(",") if c.strip().lower() in CITY_NAMES]
    cfg["cities"] = chosen[:5] or ["beijing"]
    print(f"  → 已选：" + "、".join(CITY_NAMES[c] for c in cfg["cities"]))

    # 2. 推送通道
    enabled = []
    if args.channel:
        spec = next((x for x in CHANNELS if x[0] == args.channel), None)
        if not spec:
            print(f"未知通道 {args.channel}；可选：" + ", ".join(x[0] for x in CHANNELS))
            return 1
        fields = {k: (args.key if k in ("sendkey", "token", "key", "webhook", "url", "bot_token") else "")
                  for k, _, _ in spec[2]}
        enabled.append({"type": spec[0], "enabled": True, **fields})
    elif interactive:
        print("\n【2/4】手机推送通道")
        print_channels()
        picks = ask("输入编号（可多选，逗号分隔）", "0")
        for p in picks.split(","):
            p = p.strip()
            if not p.isdigit() or int(p) == 0 or int(p) > len(CHANNELS):
                continue
            typ, label, fields = CHANNELS[int(p) - 1]
            print(f"\n  · {label}")
            values = {}
            for key, desc, default in fields:
                values[key] = ask(f"    {desc}", default)
            main_field = fields[0][0] if fields else ""
            if main_field and not values.get(main_field):
                print("    （没填密钥，这个通道先跳过）")
                continue
            enabled.append({"type": typ, "enabled": True, **values})
    else:
        enabled = [c for c in cfg["push"]["channels"] if c.get("enabled")]

    cfg["push"]["channels"] = enabled
    print(f"  → 启用通道：" + (", ".join(c["type"] for c in enabled) if enabled else "无（日报只落地本地）"))

    # 3. 时间
    if args.time:
        cfg["push_time"] = args.time
    elif interactive:
        print("\n【3/4】每天几点推送？")
        cfg["push_time"] = ask("时间（24 小时制）", cfg.get("push_time", "09:00"))
    save_config(cfg)
    print(f"  → 配置已保存 config.json，推送时间 {cfg['push_time']}")

    # 4. 试抓 + 试推 + 定时任务
    import subprocess
    print("\n【4/4】跑通全链路")
    auto = args.yes or not interactive

    def confirm(question: str) -> bool:
        return True if auto else ask(question, "y").lower().startswith("y")

    if not args.no_fetch and confirm("现在抓一次真实演出数据？"):
        from app.fetch import refresh_all
        print("  正在抓取（约 10～20 秒）…")
        st = refresh_all(cfg)
        print(f"  抓到 {st['fetched']} 条，新增 {st['new']} 场，库内共 {st['total']} 场")

    if enabled and confirm("发一条测试推送到手机？"):
        print("  正在发送…")
        r = subprocess.run([sys.executable, str(ROOT / "daily_push.py"), "--no-fetch", "--test-one"],
                           capture_output=True, cwd=str(ROOT))
        out = r.stdout.decode("utf-8", "replace").strip()
        print("  " + (out.splitlines()[-1] if out else "完成"))
        print("  详情见 logs/push.log")

    if not args.no_install and confirm("安装每天自动运行的定时任务？"):
        r = subprocess.run([sys.executable, str(ROOT / "schedule.py"), "install"],
                           capture_output=True, cwd=str(ROOT))
        print("  " + r.stdout.decode("utf-8", "replace").strip())
        if r.returncode != 0:
            print("  ⚠ 系统策略不允许自动创建任务计划，没关系：双击 daemon.bat 保持窗口开着即可，")
            print("     开机晚了也会自动补跑当天那一次，不需要管理员权限。")

    print("\n" + "=" * 62)
    print("配置完成。接下来：")
    print("  · 双击 start.bat 打开应用（可在界面点「我买了 / 想看」教它你的喜好）")
    print("  · 每天到点自动推送；也可双击 run_daily.bat 立刻跑一次")
    print("  · 定时任务装不上时，改用 daemon.bat 常驻定时（无需管理员权限）")
    print("  ⚠ 上面这些定时方式都要求电脑开机。若电脑经常关机，请双击 cloud_setup.bat")
    print("     改成云端定时（GitHub Actions 每天在云端抓取并推送，关机也照常收）。")
    print("  · 想改配置随时再运行 python setup.py；卸载任务计划用 python schedule.py uninstall")
    print("=" * 62 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
