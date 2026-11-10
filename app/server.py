# -*- coding: utf-8 -*-
"""本地 Web 应用服务：零依赖，双击 start.bat 即可打开演出雷达。"""
from __future__ import annotations

import json
import mimetypes
import socket
import sys
import threading
import urllib.parse
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in (None, ""):  # 允许直接 python app/server.py 运行
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.core import (
        CITY_NAMES, REPORTS, SHOWS_FILE, active_shows, apply_action, build_markdown,
        build_report_html, default_prefs, load_config, load_json, load_prefs, log,
        rank, save_config, save_json, split_messages, today_str,
    )
    from app.fetch import refresh_all
    from app.push import send_all
else:
    from .core import (
        CITY_NAMES, REPORTS, SHOWS_FILE, active_shows, apply_action, build_markdown,
        build_report_html, default_prefs, load_config, load_json, load_prefs, log,
        rank, save_config, save_json, split_messages, today_str,
    )
    from .fetch import refresh_all
    from .push import send_all

STATIC = Path(__file__).resolve().parent / "static"
PORT = 8765
_LOCK = threading.Lock()


def state_payload() -> dict:
    cfg = load_config()
    shows = active_shows(load_json(SHOWS_FILE, []) or [])
    prefs = load_prefs()
    ranked = rank(shows, prefs, today_str(), cfg)
    ranked["config"] = {
        "cities": cfg.get("cities", []),
        "city_names": CITY_NAMES,
        "push_time": cfg.get("push_time", "09:00"),
    }
    ranked["pref_summary"] = {
        "top_categories": sorted(prefs["weights"]["category"].items(),
                                 key=lambda x: -x[1])[:6],
        "top_tags": sorted(prefs["weights"]["tag"].items(), key=lambda x: -x[1])[:8],
        "top_venues": sorted(prefs["weights"]["venue"].items(), key=lambda x: -x[1])[:5],
        "purchases": prefs.get("purchases", [])[-20:][::-1],
        "actions_count": len(prefs.get("actions", [])),
        "updated_at": prefs.get("updated_at", ""),
    }
    return ranked


class Handler(BaseHTTPRequestHandler):
    server_version = "ShowRadar/1.0"

    def log_message(self, fmt, *args):
        pass  # 静默，避免刷屏；关键操作走 core.log

    def _json(self, obj, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str | None = None):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        ctype = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":
            return self._json(state_payload())
        if path == "/api/health":
            return self._json({"ok": True})
        if path == "/api/report":
            f = REPORTS / "latest.html"
            if not f.exists():
                html = build_report_html(rank(active_shows(load_json(SHOWS_FILE, []) or []),
                                              load_prefs(), today_str()))
                f.write_text(html, encoding="utf-8")
            return self._file(f, "text/html")
        if path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC / rel).resolve()
        if STATIC.resolve() not in target.parents and target != STATIC.resolve() / "index.html":
            return self.send_error(403)
        return self._file(target)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._body()

        if path == "/api/action":
            show_id, action = body.get("id"), body.get("action")
            with _LOCK:
                shows = load_json(SHOWS_FILE, []) or []
                show = next((s for s in shows if s.get("id") == show_id), None)
                if not show:
                    return self._json({"ok": False, "error": "找不到该演出"}, 404)
                apply_action(load_prefs(), show, action)
            log(f"记录行为：{action} · {show.get('title', '')[:30]}")
            return self._json({"ok": True, "action": action})

        if path == "/api/refresh":
            log("界面触发刷新采集")
            stats = refresh_all()
            stats["ok"] = True
            return self._json(stats)

        if path == "/api/config":
            cfg = load_config()
            cities = body.get("cities")
            if isinstance(cities, list):
                cfg["cities"] = [c for c in cities if c in CITY_NAMES][:5] or ["beijing"]
            if body.get("push_time"):
                cfg["push_time"] = str(body["push_time"])[:5]
            save_config(cfg)
            return self._json({"ok": True, "config": {
                "cities": cfg["cities"], "push_time": cfg["push_time"]}})

        if path == "/api/prefs/reset":
            with _LOCK:
                save_json(Path(__file__).resolve().parent.parent / "data" / "preferences.json",
                          default_prefs())
            log("偏好已清空")
            return self._json({"ok": True})

        if path == "/api/push/test":
            cfg = load_config()
            ranked = rank(active_shows(load_json(SHOWS_FILE, []) or []), load_prefs(), today_str(), cfg)
            md = build_markdown(ranked, cfg["push"].get("mode", "compact"))
            msgs = split_messages(md, int(cfg["push"].get("max_bytes_per_message", 3200)))
            results = send_all(cfg, msgs[:1], today_str())
            return self._json({"ok": True, "results": results, "messages": len(msgs)})

        return self._json({"error": "not found"}, 404)


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    global PORT
    if "--port" in sys.argv:
        try:
            PORT = int(sys.argv[sys.argv.index("--port") + 1])
        except (ValueError, IndexError):
            pass
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{PORT}"
    log(f"演出雷达已启动：{url}  ｜ 手机同一 WiFi 可访问 http://{lan_ip()}:{PORT}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
