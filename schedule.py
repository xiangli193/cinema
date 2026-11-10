# -*- coding: utf-8 -*-
"""把「每天推送」装进系统定时任务（Windows 任务计划 / macOS·Linux cron）。

  python schedule.py install     安装（默认读取 config.json 里的 push_time）
  python schedule.py install --time 08:30
  python schedule.py status      查看
  python schedule.py run         立刻跑一次（测试用）
  python schedule.py uninstall   卸载
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
TASK_NAME = "DamaiShowDailyPush"
PY = Path(sys.executable)
PYW = PY.with_name("pythonw.exe")
PYTHON = PYW if PYW.exists() else PY
SCRIPT = ROOT / "daily_push.py"
IS_WIN = platform.system().lower().startswith("win")


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=60)
        raw = (p.stdout or b"") + (p.stderr or b"")
        for enc in ("gbk", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                text = raw.decode("utf-8", "replace")
        return p.returncode, text.strip()
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def _time(cfg_time: str) -> str:
    t = (cfg_time or "09:00").strip()
    try:
        datetime.strptime(t, "%H:%M")
    except ValueError:
        t = "09:00"
    return t


def build_xml(when: str) -> str:
    hh, mm = when.split(":")
    start = datetime.combine(date.today(), datetime.min.time()).replace(hour=int(hh), minute=int(mm))
    if start <= datetime.now():
        start += timedelta(days=1)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>每天汇总话剧/音乐剧/演唱会/音乐会等全部演出，按喜好排序后推送到手机</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start.strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(PYTHON))}</Command>
      <Arguments>"{escape(str(SCRIPT))}"</Arguments>
      <WorkingDirectory>{escape(str(ROOT))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""


def install(when: str) -> int:
    if not IS_WIN:
        print("非 Windows：请把下面这行加入 crontab（crontab -e），把路径换成你自己的：")
        print(f"{when.split(':')[1]} {when.split(':')[0]} * * * {PY} {SCRIPT}")
        return 0
    xml_path = ROOT / "logs" / "task.xml"
    xml_path.parent.mkdir(exist_ok=True)
    xml_path.write_text(build_xml(when), encoding="utf-16")
    code, out = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"])
    if code == 0:
        print(f"✅ 已安装：每天 {when} 自动抓取并推送（若当时电脑没开，登录后会自动补跑一次）")
        print(f"   任务名 {TASK_NAME}｜执行 {PYTHON.name} {SCRIPT}")
        return 0
    print(f"XML 方式安装失败（{out}），改用基础方式重试…")
    tr = f'"{PYTHON}" "{SCRIPT}"'
    code, out = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
                      "/SC", "DAILY", "/ST", when, "/F"])
    if code == 0:
        print(f"✅ 已安装：每天 {when} 自动推送（基础模式）")
        return 0
    print(f"❌ 安装失败：{out}")
    print("可手动创建：任务计划程序 → 创建基本任务 → 每天 → 启动程序，程序填上面的 python 路径，参数填 daily_push.py 完整路径。")
    print("或直接用免权限方案：双击 daemon.bat 保持窗口开着，到点自动推送（开机晚也会补跑）。")
    return 1


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from app.core import load_config
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["install", "uninstall", "status", "run"])
    ap.add_argument("--time", default=None)
    args = ap.parse_args()
    when = _time(args.time or load_config().get("push_time", "09:00"))

    if not IS_WIN:
        if args.command == "install":
            return install(when)
        print("非 Windows 请用 crontab 管理；运行 python schedule.py install 查看建议配置。")
        return 0

    if args.command == "install":
        return install(when)
    if args.command == "uninstall":
        code, out = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        print("✅ 已卸载定时推送" if code == 0 else f"未找到任务或卸载失败：{out}")
        return code
    if args.command == "status":
        code, out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
        print(out if code == 0 else f"未安装（{out}）")
        return code
    if args.command == "run":
        code, out = _run(["schtasks", "/Run", "/TN", TASK_NAME])
        print("✅ 已触发一次，稍后查看 logs/push.log" if code == 0 else f"触发失败：{out}")
        return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
