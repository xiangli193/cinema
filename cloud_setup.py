# -*- coding: utf-8 -*-
"""云端部署向导：把项目变成「电脑关机也照常每天推送」的形式。

用法：
  python cloud_setup.py              交互向导：初始化 git、首次提交、打印后续步骤
  python cloud_setup.py --check      只检查，不改动任何文件
  python cloud_setup.py --workbuddy  打印备用方案（WorkBuddy 云端定时任务）的提示词
  python cloud_setup.py --yes        向导中不再逐项确认
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent
WORKBUDDY_PROMPT = """每天生成并推送一份「演出日报」，要求如下：
1. 抓取豆瓣同城北京正在售票的演出：https://beijing.douban.com/events/selling 及 future-drama、future-music、future-comedy 和各子分类页，尽量翻页取全。
2. 必须是全量汇总：所有未开演的演出一场都不能少，按品类分组（话剧、音乐剧、演唱会、音乐会、舞剧、歌剧、戏曲、音乐节、小型现场、喜剧、脱口秀、展览等）。我的偏好只影响同一品类内部的先后顺序，绝不能用偏好过滤或删减任何一场。
3. 每条演出的标题做成大麦购票链接 https://search.damai.cn/search.htm?keyword=剧名，并注明日期、场馆、票价；正在售票的标出来，新上架的标 NEW。
4. 用 HTTP POST 表单把日报发到 https://sctapi.ftqq.com/你的SendKey.send ，参数 title=演出日报 日期、desp=日报 Markdown 正文；内容过长时按品类拆成多条，标题加 (1/N)，保证所有品类都发到。
5. 结尾注明数据来源、总场数和品类名数。
我的偏好清单（买了票后我会回来更新这一段）：音乐剧、话剧优先；其余品类照常全量展示。
（把上面 SendKey 换成你在 sct.ftqq.com 扫码拿到的真实值。）"""


def run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip() + (("\n" + r.stderr.strip()) if r.stderr else "")


def git(*args: str) -> tuple[int, str]:
    return run(["git", *args])


def ask(question: str, default: str = "") -> str:
    try:
        v = input(question + (f" [{default}]：" if default else "：")).strip()
    except EOFError:
        return default
    return v or default


def check() -> None:
    print("=== 云端部署自检 ===")
    for p in ("cloud_entry.py", "cloud_prefs.py", ".github/workflows/daily.yml",
              ".gitignore", "app/core.py", "app/fetch.py", "app/push.py"):
        print(("  OK  " if (ROOT / p).exists() else "  缺失 ") + p)
    code, out = git("rev-parse", "--is-inside-work-tree")
    print("  git 仓库：" + ("已初始化" if code == 0 and out.strip() == "true" else "尚未初始化（向导会帮你做）"))
    code, out = git("remote", "-v")
    print("  远程仓库：" + (out.splitlines()[0] if code == 0 and out.strip() else "尚未设置"))
    print("  config.json 是否会被提交：" + ("不会（已忽略）" if (ROOT / ".gitignore").exists() else "请检查 .gitignore"))
    print("\n提示：这台电脑上的定时任务（schedule.py / daemon.py）仍然依赖开机；云端任务不需要。")


def main() -> int:
    ap = argparse.ArgumentParser(description="云端部署向导")
    ap.add_argument("--check", action="store_true", help="只检查，不改动文件")
    ap.add_argument("--workbuddy", action="store_true", help="打印 WorkBuddy 云端任务提示词")
    ap.add_argument("--yes", action="store_true", help="跳过确认")
    args = ap.parse_args()

    if args.workbuddy:
        print("把下面这段粘贴到 WorkBuddy 小程序的「云端定时任务」提示词里（执行环境选云端）：\n")
        print(WORKBUDDY_PROMPT)
        return 0
    if args.check:
        check()
        return 0

    print("=" * 68)
    print(" 演出雷达 · 云端部署向导（做完之后，电脑关机也能每天收到推送）")
    print("=" * 68)
    print("\n原理：代码、演出库、喜好记录放进 GitHub 私有仓库，GitHub Actions 每天在")
    print("云端服务器定时抓取并推送；你的电脑不参与，也不需要开机。\n")

    repo = "" if args.yes else ask(
        "你的 GitHub 仓库名，格式 用户名/仓库名（还没建可先直接回车）", "")
    if repo and "/" not in repo:
        print("  格式应为 owner/repo，例如 zhangsan/show-radar；已忽略这个值。")
        repo = ""

    # 1. 网页版日报初始文件
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    latest = ROOT / "reports" / "latest.html"
    if latest.exists():
        (docs / "index.html").write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
        print("  ✓ 已用今天的日报生成 docs/index.html（网页版）")
    else:
        (docs / "index.html").write_text(
            "<!doctype html><meta charset='utf-8'><title>演出日报</title>"
            "<p>网页版日报会在第一次云端任务运行后自动生成。</p>", encoding="utf-8")

    # 2. 初始化并首次提交
    code, out = git("rev-parse", "--is-inside-work-tree")
    if code != 0 or out.strip() != "true":
        if not args.yes and not ask("现在初始化本地 git 仓库？", "y").lower().startswith("y"):
            print("  已跳过 git 初始化，后面可手动执行 git init。")
        else:
            print("  " + git("init", "-b", "main")[1])
    code, out = git("status", "--porcelain")
    print(f"\n  待提交文件 {len([x for x in out.splitlines() if x.strip()])} 个"
          "（config.json、logs/、.workbuddy/ 已被 .gitignore 排除）")
    if not args.yes and not ask("现在做首次提交？", "y").lower().startswith("y"):
        print("  已跳过提交。")
    else:
        git("add", "-A")
        code, out = git("-c", "user.name=show-radar", "-c", "user.email=show-radar@localhost",
                        "commit", "-m", "chore: 演出雷达云端版初始化")
        print("  " + (out.splitlines()[0] if out.strip() else "首次提交完成"))

    owner = repo.split("/")[0] if repo else "你的用户名"
    name = repo.split("/", 1)[1] if repo else "仓库名"
    print("\n" + "=" * 68)
    print(" 接下来在网页上做 4 步（约 5 分钟）")
    print("=" * 68)
    print(f"""
【1】新建仓库：打开 https://github.com/new
     仓库名填 {name}，务必选 Private（私有）——喜好和购买记录会写进仓库。
     不要勾选 README / .gitignore / license，保持空仓库。

【2】把代码推上去（在 {ROOT} 目录下执行）：
     git remote add origin https://github.com/{owner}/{name}.git
     git branch -M main
     git push -u origin main

【3】填推送密钥：仓库 Settings → Secrets and variables → Actions → New repository secret
     Name: SERVERCHAN_KEY    Value: 在 https://sct.ftqq.com 扫码拿到的 SendKey
     （微信收推最省事；也可改用 PUSHPLUS_TOKEN / BARK_KEY 等，见 workflow 注释）
     可选：Variables 里加 SHOW_CITIES=beijing、PUSH_MODE=compact。

【4】手动跑一次验收：仓库 Actions 页 → 「演出日报」→ Run workflow
     手机应该立刻收到推送；以后每天北京时间 09:00 自动执行，电脑关机也照常。

 可选：想让推送里带「网页版完整日报」链接，去 Settings → Pages，
     Source 选 main 分支的 /docs 目录并保存，然后在 Variables 里加
     PAGES_URL=https://{owner}.github.io/{name}/
     网页版地址同样是 https://{owner}.github.io/{name}/
""")
    print("记喜好：推送里的「⭐ 记一笔」链接，标题写「我买了 音乐剧」或「想看 xxx演出名」")
    print("提交即可；次日云端会把它计入偏好（只排序、不过滤）。")
    print("\n注意：GitHub 定时任务偶有 5～30 分钟延迟；仓库长期无活动会停，")
    print("但本任务每天自动回写提交，会一直保持活跃。海外 IP 抓豆瓣若被风控，")
    print("日报抬头会提示数据未更新，这时可改用 WorkBuddy 云端任务：")
    print("  python cloud_setup.py --workbuddy   （打印可直接粘贴的提示词）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
