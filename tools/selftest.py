#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline self test: fake trimmedia.db -> NFO in original media folders."""
import json
import os
import sqlite3
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "metabridge" / "app" / "metabridge"))
import core  # noqa: E402


def make_db(path, movie_video, ep_video, img_src):
    con = sqlite3.connect(str(path))
    con.executescript("""
    CREATE TABLE item(guid TEXT PRIMARY KEY, type TEXT, parent_guid TEXT, title TEXT,
        original_title TEXT, overview TEXT, year INTEGER, release_date TEXT,
        first_air_date TEXT, runtime INTEGER, tmdb_id INTEGER, imdb_id TEXT,
        vote_average REAL, vote_count INTEGER, genres TEXT, posters TEXT,
        backdrops TEXT, season_number INTEGER, episode_number INTEGER,
        number_of_seasons INTEGER, number_of_episodes INTEGER, status TEXT);
    CREATE TABLE item_media(guid TEXT, item_guid TEXT, path TEXT, dir TEXT, sort_num INTEGER);
    CREATE TABLE person(guid TEXT, name TEXT, character TEXT, profile_path TEXT);
    CREATE TABLE item_person(item_guid TEXT, person_guid TEXT, character TEXT);
    """)
    con.execute("INSERT INTO item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "m1", "Movie", None, "测试电影", "Test Movie", "一段测试简介", 2024, "2024-05-01",
        None, 120, 12345, "tt1234567", 8.1, 900, '[{"name":"科幻"}]', "/poster.jpg",
        "/fanart.jpg", None, None, None, None, "Released"))
    con.execute("INSERT INTO item_media VALUES(?,?,?,?,?)",
                ("mm1", "m1", str(movie_video), str(movie_video.parent), 0))
    con.execute("INSERT INTO item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "t1", "TV", None, "测试剧集", "Test Show", "剧集简介", 2023, None, "2023-01-01",
        None, 777, None, 7.5, 100, '[{"name":"剧情"}]', "/tv.jpg", "/tvfan.jpg",
        None, None, 2, 20, "Returning Series"))
    con.execute("INSERT INTO item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "s1", "Season", "t1", "Season 1", None, None, None, None, None, None, None,
        None, None, None, None, "/season.jpg", None, 1, None, None, None, None))
    con.execute("INSERT INTO item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "e1", "Episode", "s1", "第一集", None, "首集简介", None, None, "2023-01-02",
        45, None, None, 7.9, 30, None, None, None, 1, 1, None, None, None))
    con.execute("INSERT INTO item_media VALUES(?,?,?,?,?)",
                ("em1", "e1", str(ep_video), str(ep_video.parent), 0))
    con.execute("INSERT INTO person VALUES(?,?,?,?)", ("p1", "张三", "主角", None))
    con.execute("INSERT INTO item_person VALUES(?,?,?)", ("m1", "p1", "张三角色"))
    con.commit()
    con.close()
    Path(img_src).write_bytes(b"\x89PNG\r\n\x1a\nfake")


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        movie_dir = root / "media" / "movies" / "测试电影 (2024)"
        tv_dir = root / "media" / "tv" / "测试剧集" / "Season 01"
        movie_dir.mkdir(parents=True)
        tv_dir.mkdir(parents=True)
        movie_video = movie_dir / "测试电影 (2024).mkv"
        ep_video = tv_dir / "测试剧集 S01E01.mkv"
        movie_video.write_bytes(b"fake")
        ep_video.write_bytes(b"fake")
        imgdir = root / "img"
        imgdir.mkdir()
        make_db(root / "trimmedia.db", movie_video, ep_video, imgdir / "poster.jpg")
        for name in ("fanart.jpg", "tv.jpg", "tvfan.jpg", "season.jpg"):
            (imgdir / name).write_bytes(b"\x89PNG\r\n\x1a\nfake")

        cfg = dict(core.DEFAULT_CONFIG)
        cfg.update({"db_path": str(root / "trimmedia.db"), "img_root": str(imgdir),
                    "write_images": True, "overwrite_nfo": True, "only_movies": True,
                    "only_tv": True})
        con = core.connect(str(root / "trimmedia.db"))
        plan, counts = core.build_plan(con, cfg, {})
        con.close()
        print("plan:", counts, "items:", len(plan))
        assert counts["movie"] == 1 and counts["episode"] == 1 and counts["tv"] == 1
        stats = core.write_plan(plan, cfg)
        print("stats:", {k: v for k, v in stats.items() if k != "files"})
        # release the SQLite handle before Windows cleans up the temp dir
        del con

        mnfo = movie_dir / "测试电影 (2024).nfo"
        enfo = tv_dir / "测试剧集 S01E01.nfo"
        tvnfo = tv_dir.parent / "tvshow.nfo"
        snfo = tv_dir / "season.nfo"
        for p in (mnfo, enfo, tvnfo, snfo):
            assert p.is_file(), "missing " + str(p)
            ET.parse(p)
        m = ET.parse(mnfo).getroot()
        assert m.findtext("title") == "测试电影"
        assert m.findtext("year") == "2024"
        assert m.findtext("actor/name") == "张三"
        assert m.find("uniqueid[@type='tmdb']").text == "12345"
        e = ET.parse(enfo).getroot()
        assert e.findtext("season") == "1" and e.findtext("episode") == "1"
        assert list(movie_dir.glob("poster.*")), "movie poster missing"
        assert list(tv_dir.parent.glob("poster.*")), "tvshow poster missing"
        assert list(tv_dir.glob("season01.*")), "season poster missing"
        assert movie_video.read_bytes() == b"fake" and ep_video.read_bytes() == b"fake"

        # ---- delete / cleanup verification ----
        # add a third-party NFO in the movie dir: it must never be deleted
        third = movie_dir / "第三方.nfo"
        third.write_text("<movie><title>other</title></movie>", encoding="utf-8")
        con = core.connect(str(root / "trimmedia.db"))
        dgroups, dcounts = core.build_delete_plan(con, cfg, {})
        con.close()
        allpaths = [f["path"] for g in dgroups for f in g["files"]]
        assert mnfo.exists() and any(p == str(mnfo) for p in allpaths), "marked movie NFO should be listed"
        assert str(third) not in allpaths, "third-party NFO must not be listed"
        assert not any(p.endswith(".mkv") for p in allpaths), "video files must not be listed"
        dstats = core.delete_files([f for g in dgroups for f in g["files"]])
        print("delete stats:", {k: v for k, v in dstats.items() if k != "files"})
        assert not mnfo.exists() and not enfo.exists() and not tvnfo.exists() and not snfo.exists()
        assert third.exists(), "third-party NFO must survive"
        assert movie_video.read_bytes() == b"fake" and ep_video.read_bytes() == b"fake"
        assert not list(movie_dir.glob("poster.*")), "movie poster should be deleted"
        print("SELFTEST OK")


if __name__ == "__main__":
    main()
