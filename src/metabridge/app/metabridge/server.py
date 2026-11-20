#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""metabridge HTTP server — small stdlib-only API + single page UI."""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
WWW = HERE / "www"
sys.path.insert(0, str(HERE))

import core  # noqa: E402

VAR = os.environ.get("TRIM_PKGVAR", os.environ.get("METABRIDGE_VAR", str(HERE / "data")))
os.makedirs(VAR, exist_ok=True)
CFG_PATH = os.path.join(VAR, "config.json")
JOB = core.Job()


def _db_info(cfg):
    db = core.discover_db_path(cfg)
    info = {"path": db, "exists": os.path.isfile(db),
            "img_roots": core.discover_img_roots(cfg)}
    if info["exists"]:
        try:
            conn = core.connect(db)
            cur = conn.execute("SELECT type, count(*) AS n FROM item GROUP BY type")
            info["items"] = {r["type"]: r["n"] for r in cur.fetchall()}
            conn.close()
        except Exception as exc:
            info["error"] = str(exc)
    return info


def _auth_ok(handler):
    cfg = core.load_config(CFG_PATH)
    token = cfg.get("access_token") or ""
    if not token:
        return True
    got = handler.headers.get("X-Token") or parse_qs(
        urlparse(handler.path).query).get("token", [""])[0]
    return got == token


class Handler(BaseHTTPRequestHandler):
    server_version = "metabridge/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        if not _auth_ok(self):
            return self._send(401, {"ok": False, "error": "token 无效"})
        if path in ("/", "/index.html"):
            p = WWW / "index.html"
            return self._send(200, p.read_bytes(), "text/html; charset=utf-8")
        if path == "/app.js":
            return self._send(200, (WWW / "app.js").read_bytes(),
                              "application/javascript; charset=utf-8")
        if path == "/style.css":
            return self._send(200, (WWW / "style.css").read_bytes(),
                              "text/css; charset=utf-8")
        if path == "/api/status":
            cfg = core.load_config(CFG_PATH)
            return self._send(200, {
                "ok": True, "version": "1.0.3", "config": cfg,
                "db": _db_info(cfg), "job": JOB.snapshot(120),
            })
        if path == "/api/job":
            snap = JOB.snapshot(int(qs.get("tail", ["200"])[0]))
            with JOB.lock:
                snap["preview"] = list(JOB.plan_preview)
                snap["groups"] = list(JOB.groups)
            return self._send(200, {"ok": True, **snap})
        if path == "/api/image":
            q = qs.get("path", [""])[0]
            cfg = core.load_config(CFG_PATH)
            roots = core.discover_img_roots(cfg) + [
                r for r in (cfg.get("media_roots") or []) if r]
            try:
                target = Path(q).resolve()
                if not target.is_file() or target.suffix.lower() not in core.IMAGE_EXTS:
                    raise ValueError("not an image")
                if not any(str(target).startswith(os.path.normpath(r) + os.sep) for r in roots):
                    raise ValueError("outside allowed roots")
                ctype = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".webp": "image/webp", ".bmp": "image/bmp",
                }.get(target.suffix.lower(), "application/octet-stream")
                return self._send(200, target.read_bytes(), ctype)
            except Exception:
                return self._send(404, b"", "image/png")
        if path == "/api/browse":
            q = qs.get("path", ["/"])[0] or "/"
            try:
                p = Path(q)
                if not p.is_dir():
                    p = Path("/")
                dirs, files = [], []
                for child in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                    if child.name.startswith("."):
                        continue
                    if child.is_dir():
                        dirs.append(str(child))
                    elif child.suffix.lower() in core.VIDEO_EXTS:
                        files.append(str(child))
                return self._send(200, {"ok": True, "path": str(p),
                                        "parent": str(p.parent), "dirs": dirs[:300],
                                        "files": files[:100]})
            except Exception as exc:
                return self._send(200, {"ok": False, "error": str(exc),
                                        "path": "/", "parent": "/", "dirs": [], "files": []})
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        if not _auth_ok(self):
            return self._send(401, {"ok": False, "error": "token 无效"})
        body = self._body()
        cfg = core.load_config(CFG_PATH)
        if path == "/api/config":
            cfg.update({k: v for k, v in body.items() if k in core.DEFAULT_CONFIG})
            core.save_config(CFG_PATH, cfg)
            return self._send(200, {"ok": True, "config": cfg})
        if path == "/api/scan":
            opts = {k: body.get(k, cfg.get(k)) for k in
                    ("only_movies", "only_tv", "title_filter", "limit")}
            if not JOB.start(lambda job: core.run_scan(job, cfg, opts)):
                return self._send(409, {"ok": False, "error": "已有任务在运行"})
            return self._send(200, {"ok": True})
        if path == "/api/export":
            opts = {k: body.get(k, cfg.get(k)) for k in
                    ("only_movies", "only_tv", "title_filter", "limit")}
            if "keys" in body:
                opts["keys"] = body["keys"]
            opts["var_dir"] = VAR
            if not JOB.start(lambda job: core.run_export(job, cfg, opts)):
                return self._send(409, {"ok": False, "error": "已有任务在运行"})
            return self._send(200, {"ok": True})
        if path == "/api/stop":
            JOB.stop()
            return self._send(200, {"ok": True})
        if path == "/api/clearlog":
            with JOB.lock:
                JOB.lines = []
            return self._send(200, {"ok": True})
        return self._send(404, {"ok": False, "error": "not found"})


def main():
    port = int(os.environ.get("METABRIDGE_PORT") or core.load_config(CFG_PATH)["port"] or 22690)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    print("metabridge listening on 0.0.0.0:%d (var=%s)" % (port, VAR), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
