# -*- coding: utf-8 -*-
"""常驻定时守护（备选方案，不需要 Windows 任务计划程序 / 管理员权限）。

双击 daemon.bat 后保持窗口开着（可最小化），到点自动执行 daily_push.py。
若电脑在推送时间没开，启动后会立刻补跑当天的那一次，不会漏一天。

想开机自动运行：给 daemon.bat 创建快捷方式，放进
  C:\\Users\\<你的用户名>\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from app.core import load_config, log  # noqa: E402

STATE_FILE = ROOT / "logs" / "daemon_state.json"


def push_time() -> str:
    return str(load_config().get("push_time", "09:00")).strip()[:5]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print("演出雷达 · 常驻定时器已启动")
    print(f"每天 {push_time()} 自动抓取 + 生成日报 + 推送到手机；窗口保持开着即可（可最小化）")
    print("按 Ctrl+C 停止；改推送时间直接改 config.json 或运行 setup.py\n")

    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    while True:
        now = datetime.now()
        today = date.today().isoformat()
        t = push_time()
        due = now.strftime("%H:%M") >= t          # 开机晚了就补跑当天
        if due and state.get("last_date") != today:
            log(f"定时器触发每日任务（{now.strftime('%Y-%m-%d %H:%M')}，计划 {t}）")
            try:
                r = subprocess.run([sys.executable, str(ROOT / "daily_push.py")],
                                   cwd=str(ROOT), timeout=900)
                log(f"每日任务结束，退出码 {r.returncode}")
            except Exception as exc:  # noqa: BLE001
                log(f"每日任务异常：{exc}")
            state["last_date"] = today
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            time.sleep(75)
        else:
            time.sleep(20)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止定时器")
