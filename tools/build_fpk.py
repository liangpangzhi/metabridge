#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build metabridge .fpk packages (x86 / arm / all).

FPK layout (verified against a working fnOS package):
  app.tgz  cmd/  config/  ICON.PNG  ICON_256.PNG  manifest
app.tgz contains:
  metabridge/  ui/  config/
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = ROOT / "src" / "metabridge"
APP_DIR = APP / "app"
DIST = ROOT / "dist"
FPK_ITEMS = ["app.tgz", "cmd", "config", "ICON.PNG", "ICON_256.PNG", "manifest"]
VALID = ("x86", "arm", "all")


def excluded(name):
    base = os.path.basename(name)
    return base in ("__pycache__", "data", "dist") or base.endswith((".pyc", ".log", ".bak", ".tmp"))


def is_exec(path):
    p = str(path).replace("\\", "/")
    return p.endswith(".sh") or ("/cmd/" in p)


def add_tree(tf, path, arcname):
    path = Path(path)
    if excluded(str(path)):
        return
    ti = tf.gettarinfo(str(path), arcname=arcname)
    if ti.isdir():
        ti.name = ti.name.rstrip("/")
        ti.mode = 0o755
        tf.addfile(ti)
        for child in sorted(path.iterdir(), key=lambda x: x.name):
            add_tree(tf, child, arcname + "/" + child.name)
    else:
        ti.mode = 0o755 if is_exec(path) else 0o644
        with open(path, "rb") as fh:
            tf.addfile(ti, fh)


def build_app_tgz(out):
    if out.exists():
        out.unlink()
    with tarfile.open(str(out), "w:gz", format=tarfile.GNU_FORMAT) as tf:
        for item in ("metabridge", "ui"):
            add_tree(tf, APP_DIR / item, item)
        add_tree(tf, APP / "config", "config")
    return hashlib.md5(out.read_bytes()).hexdigest()


def render_manifest(platform, checksum, version):
    text = (APP / "manifest").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^version\s*=.*$", "version=%s" % version, text)
    text = re.sub(r"(?m)^platform\s*=.*$", "platform=%s" % platform, text)
    text = re.sub(r"(?m)^checksum\s*=.*\n?", "", text)
    if not text.endswith("\n"):
        text += "\n"
    return text + "checksum=%s\n" % checksum


def build_fpk(out, app_tgz, manifest_tmp):
    if out.exists():
        out.unlink()
    with tarfile.open(str(out), "w:gz", format=tarfile.GNU_FORMAT) as tf:
        for item in FPK_ITEMS:
            src = app_tgz if item == "app.tgz" else (manifest_tmp if item == "manifest" else APP / item)
            add_tree(tf, src, item)
    return hashlib.sha256(out.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description="打包 metabridge FPK")
    ap.add_argument("version", nargs="?", default="1.0.0")
    ap.add_argument("--platform", default="x86,arm", help="x86 / arm / all / x86,arm")
    ns = ap.parse_args()
    plats = [x.strip() for x in ns.platform.split(",") if x.strip()]
    for p in plats:
        if p not in VALID:
            ap.error("platform 只能是 %s" % ", ".join(VALID))

    DIST.mkdir(parents=True, exist_ok=True)
    app_tgz = APP / "app.tgz"
    print("1) 打包 app.tgz …")
    ck = build_app_tgz(app_tgz)
    print("   %d 字节, md5=%s" % (app_tgz.stat().st_size, ck))

    tmp = Path(tempfile.mkdtemp(prefix="metabridge-fpk-"))
    for plat in plats:
        out = DIST / ("metabridge-%s-%s.fpk" % (ns.version, plat))
        man = tmp / ("manifest-%s" % plat)
        man.write_text(render_manifest(plat, ck, ns.version), encoding="utf-8")
        sha = build_fpk(out, app_tgz, man)
        (DIST / (out.name + ".sha256")).write_text("%s  %s\n" % (sha, out.name), encoding="utf-8")
        print("2) %s  %d 字节" % (out, out.stat().st_size))
        print("   sha256=%s" % sha)
        with tarfile.open(str(out), "r:gz") as tf:
            print("   包内：", ", ".join(tf.getnames()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
