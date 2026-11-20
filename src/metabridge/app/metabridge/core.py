#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""metabridge core: read fnOS 影视 (trim.media) database and emit Emby/Kodi
compatible NFO + artwork beside the original media files.

Only Python standard library is used.  The database is always opened read-only.
No video file is ever modified, moved or deleted.
"""

import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

MARKER = "metabridge:generated"
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv",
    ".webm", ".rmvb", ".rm", ".m4v", ".mpg", ".mpeg", ".iso", ".strm",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# 飞牛影视 item_tag 使用自有 genre 数字 ID（不是 TMDB id）
FN_GENRE_ZH = {
    "1": "动作", "2": "冒险", "3": "科幻", "4": "喜剧", "5": "犯罪",
    "6": "纪录片", "7": "剧情", "8": "家庭", "9": "奇幻", "10": "战争",
    "11": "恐怖", "12": "音乐", "13": "悬疑", "14": "爱情", "16": "动画",
    "17": "惊悚", "18": "历史", "19": "西部",
}
# 兼容部分版本直接存 TMDB id
TMDB_GENRE_ZH = {
    "28": "动作", "12": "冒险", "16": "动画", "35": "喜剧", "80": "犯罪",
    "99": "纪录片", "18": "剧情", "10751": "家庭", "14": "奇幻", "36": "历史",
    "27": "恐怖", "10402": "音乐", "9648": "悬疑", "10749": "爱情", "878": "科幻",
    "10770": "电视电影", "53": "惊悚", "10752": "战争", "37": "西部",
}
GENRE_ZH = {**TMDB_GENRE_ZH, **FN_GENRE_ZH}


def image_filename(src, base):
    """Keep the real extension: 飞牛图片缓存多为 .webp."""
    ext = Path(src).suffix.lower()
    return base + (ext if ext in IMAGE_EXTS else ".jpg")

DEFAULT_CONFIG = {
    "db_path": "/usr/local/apps/@appdata/trim.media/database/trimmedia.db",
    "img_root": "",                 # empty = auto detect /vol*/@appmeta/trim.media/img
    "media_roots": [],              # optional filter, empty = all paths from DB
    "write_images": True,
    "overwrite_nfo": False,
    "overwrite_images": False,
    "overwrite_only_ours": True,    # when overwrite_nfo, only rewrite files with our marker
    "write_season_nfo": True,
    "write_episode_thumb": True,
    "include_remote_art": False,
    "write_movie_nfo_alias": False, # also write movie.nfo next to video
    "episode_nfo_style": "same",    # same = <video stem>.nfo
    "limit": 0,
    "title_filter": "",
    "only_movies": True,
    "only_tv": True,
    "access_token": "",
    "port": 22690,
}

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def log(msg, emit=None):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    if emit:
        emit(line)
    return line


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    except Exception:
        pass
    return cfg


def save_config(path, cfg):
    clean = {k: v for k, v in cfg.items() if k in DEFAULT_CONFIG}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def discover_db_path(cfg):
    candidates = [cfg.get("db_path", "")]
    candidates += sorted(glob.glob("/usr/local/apps/@appdata/trim.media/database/trimmedia.db"))
    candidates += sorted(glob.glob("/vol*/@appdata/trim.media/database/trimmedia.db"))
    candidates += sorted(glob.glob("/vol*/@appdata/trim.media/*/trimmedia.db"))
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return candidates[0] if candidates and candidates[0] else DEFAULT_CONFIG["db_path"]


def discover_img_roots(cfg):
    roots = []
    explicit = (cfg.get("img_root") or "").strip()
    if explicit:
        roots.extend([x.strip() for x in explicit.split(os.pathsep) if x.strip()])
    roots += sorted(glob.glob("/vol*/@appmeta/trim.media/img"))
    roots += sorted(glob.glob("/usr/local/apps/@appdata/trim.media/img"))
    roots += sorted(glob.glob("/vol*/@appmeta/trim.media/*/img"))
    seen, out = set(), []
    for r in roots:
        r = os.path.normpath(r)
        if r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def columns(conn, table):
    try:
        return {row[1] for row in conn.execute('PRAGMA table_info("%s")' % table)}
    except Exception:
        return set()


def tables(conn):
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    except Exception:
        return set()


def pick(row, *names, default=None):
    """Get first present / non-empty column from a sqlite Row or dict."""
    if row is None:
        return default
    keys = row.keys() if hasattr(row, "keys") else row
    for name in names:
        if name in keys:
            v = row[name] if not isinstance(row, dict) else row.get(name)
            if v not in (None, "", [], {}):
                return v
    return default


def as_text(v):
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", "ignore")
    return str(v).strip()


def parse_jsonish(value):
    """Normalise DB fields that may be JSON, comma separated or plain text."""
    if value is None or value == "":
        return []
    s = as_text(value)
    if s[:1] in "[{":
        try:
            data = json.loads(s)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        except Exception:
            pass
    return [x.strip() for x in s.split(",") if x.strip()]


def first_value(value, *keys):
    for item in parse_jsonish(value):
        if isinstance(item, str) and item:
            return item
        if isinstance(item, dict):
            for k in keys:
                if item.get(k) not in (None, ""):
                    return str(item[k])
    return ""


def all_values(value, *keys):
    out = []
    for item in parse_jsonish(value):
        if isinstance(item, str) and item:
            out.append(item)
        elif isinstance(item, dict):
            for k in keys:
                if item.get(k) not in (None, ""):
                    out.append(str(item[k]))
                    break
    return out


def date_text(value):
    s = as_text(value)
    if not s:
        return ""
    if s.isdigit():
        try:
            n = int(s)
            if n > 1e11:
                n /= 1000.0
            return datetime.datetime.fromtimestamp(n).strftime("%Y-%m-%d")
        except Exception:
            return s
    return s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else s


def year_of(row):
    for src in (pick(row, "year", default=None),
                pick(row, "release_date", "first_air_date", "air_date", "premiered", default=None)):
        s = as_text(src)
        m = re.search(r"(\d{4})", s)
        if m and 1900 <= int(m.group(1)) <= 2199:
            return m.group(1)
    return ""


def runtime_of(row, cols=None):
    v = pick(row, "runtime", "duration", "episode_run_time", "run_time", default=None)
    vals = parse_jsonish(v) if isinstance(v, str) and v[:1] == "[" else [v]
    for item in vals:
        if isinstance(item, dict):
            item = next(iter(item.values()), None)
        try:
            n = int(float(str(item).strip()))
            if 0 < n < 100000:
                return str(n)
        except Exception:
            continue
    return ""


def is_ours(path):
    try:
        with open(path, "rb") as fh:
            return MARKER.encode("ascii") in fh.read(2048)
    except Exception:
        return False


def write_text_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".metabridge-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# image resolution
# ---------------------------------------------------------------------------

class ImageFinder:
    """Resolve the image hashes/URLs stored by 飞牛影视 to local files."""

    def __init__(self, roots, emit=None):
        self.roots = roots
        self.emit = emit
        self._index = None
        self._cache = {}

    def _build_index(self):
        if self._index is not None:
            return
        idx = {}
        for root in self.roots:
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    if Path(name).suffix.lower() in IMAGE_EXTS:
                        idx.setdefault(name, os.path.join(dirpath, name))
        self._index = idx
        log("图片索引建立：%d 个文件" % len(idx), self.emit)

    def resolve(self, value):
        """Return local image path or ''."""
        raw = as_text(value)
        if not raw:
            return ""
        if raw in self._cache:
            return self._cache[raw]
        candidates = []
        for item in parse_jsonish(raw):
            if isinstance(item, dict):
                item = first_value(item, "url", "file_path", "path", "hash", "name")
            s = as_text(item)
            if not s:
                continue
            s = re.sub(r"^https?://[^/]+", "", s)
            s = s.split("?")[0].strip("/")
            candidates.append(s)
        found = ""
        for c in candidates:
            base = os.path.basename(c)
            for root in self.roots:
                for rel in (c, base, os.path.join("poster", base), os.path.join("thumb", base)):
                    p = os.path.join(root, rel.replace("/", os.sep))
                    if os.path.isfile(p):
                        found = p
                        break
                if found:
                    break
            if not found:
                self._build_index()
                found = self._index.get(os.path.basename(c), "")
            if found:
                break
        self._cache[raw] = found
        return found


# ---------------------------------------------------------------------------
# NFO builders
# ---------------------------------------------------------------------------

def _add(parent, tag, value):
    s = as_text(value)
    if s:
        ET.SubElement(parent, tag).text = s


def _pretty(root, as_movie=False):
    xml = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(xml).toprettyxml(indent="  ", encoding="UTF-8")
    text = pretty.decode("utf-8")
    if as_movie:
        text = text.replace("<movie>", "<movie>\n  <!-- %s -->" % MARKER, 1)
    elif root.tag == "tvshow":
        text = text.replace("<tvshow>", "<tvshow>\n  <!-- %s -->" % MARKER, 1)
    elif root.tag == "episodedetails":
        text = text.replace("<episodedetails>", "<episodedetails>\n  <!-- %s -->" % MARKER, 1)
    else:
        text = text.replace("<%s>" % root.tag, "<%s>\n  <!-- %s -->" % (root.tag, MARKER), 1)
    return text


def _ids(parent, row):
    tmdb = pick(row, "tmdb_id", "tmdbId", "tmdbid", "themoviedb_id", default="")
    imdb = pick(row, "imdb_id", "imdbId", "imdbid", default="")
    tvdb = pick(row, "tvdb_id", "tvdbId", "tvdbid", default="")
    if tmdb:
        e = ET.SubElement(parent, "uniqueid", type="tmdb", default="true")
        e.text = as_text(tmdb).split("/")[-1]
    if imdb:
        ET.SubElement(parent, "uniqueid", type="imdb").text = as_text(imdb)
    if tvdb:
        ET.SubElement(parent, "uniqueid", type="tvdb").text = as_text(tvdb)


def _common(parent, row, artwork=None, person_rows=None):
    _add(parent, "title", pick(row, "title", "name", default=""))
    _add(parent, "originaltitle", pick(row, "original_title", "originaltitle",
                                       "original_name", default=""))
    _add(parent, "plot", pick(row, "overview", "plot", "description", "summary", default=""))
    _add(parent, "tagline", pick(row, "tagline", default=""))
    _ids(parent, row)
    vote = pick(row, "vote_average", "rating", "score", default="")
    if vote:
        try:
            ET.SubElement(parent, "rating").text = "%.1f" % (float(vote) * 1.0)
        except Exception:
            _add(parent, "rating", vote)
    _add(parent, "votes", pick(row, "vote_count", "votes", default=""))
    for g in all_values(pick(row, "genres", "genre", "genre_ids", default=""), "name", "genre"):
        _add(parent, "genre", g)
    for t in all_values(pick(row, "keywords", "tags", "tag", default=""), "name", "tag"):
        _add(parent, "tag", t)
    for s in all_values(pick(row, "production_countries", "countries", "country", default=""),
                        "name", "iso_3166_1", "country"):
        _add(parent, "country", s)
    for s in all_values(pick(row, "studios", "production_companies", "studio", default=""),
                        "name", "studio"):
        _add(parent, "studio", s)
    mpaa = pick(row, "content_ratings", "certification", "mpaa", default="")
    _add(parent, "mpaa", first_value(mpaa, "rating", "release_certification", "name") or as_text(mpaa))
    for pr in (person_rows or []):
        actor = ET.SubElement(parent, "actor")
        _add(actor, "name", pick(pr, "name", "character", "person_name", default=""))
        _add(actor, "role", pick(pr, "role", "character_name", "department", default=""))
        # 飞牛 person.profile_path 是应用内部缓存路径，Emby 无法直接使用，故不写入。
    if artwork:
        art = ET.SubElement(parent, "art")
        for key in ("poster", "fanart", "thumb", "banner", "clearart", "logo"):
            if artwork.get(key):
                ET.SubElement(art, key).text = artwork[key]


def build_movie_nfo(row, artwork=None, persons=None, cfg=None):
    root = ET.Element("movie")
    _common(root, row, artwork, persons)
    y = year_of(row)
    _add(root, "year", y)
    _add(root, "premiered", date_text(pick(row, "release_date", "air_date", "premiered", default="")))
    _add(root, "runtime", runtime_of(row))
    _add(root, "status", pick(row, "status", default=""))
    return _pretty(root)


def build_tvshow_nfo(row, artwork=None, persons=None, cfg=None):
    root = ET.Element("tvshow")
    _common(root, row, artwork, persons)
    _add(root, "premiered", date_text(pick(row, "first_air_date", "release_date", default="")))
    _add(root, "status", pick(row, "status", default=""))
    _add(root, "seasoncount", pick(row, "number_of_seasons", "season_count", default=""))
    _add(root, "episodecount", pick(row, "number_of_episodes", "episode_count", default=""))
    return _pretty(root)


def build_season_nfo(row, season_num, artwork=None):
    root = ET.Element("season")
    _add(root, "seasonnumber", season_num)
    _add(root, "title", pick(row, "title", "name", default=""))
    _add(root, "plot", pick(row, "overview", "plot", default=""))
    _add(root, "premiered", date_text(pick(row, "first_air_date", "air_date", default="")))
    if artwork:
        art = ET.SubElement(root, "art")
        for key in ("poster", "thumb", "fanart"):
            if artwork.get(key):
                ET.SubElement(art, key).text = artwork[key]
    return _pretty(root)


def build_episode_nfo(row, season_num, ep_num, artwork=None, persons=None, cfg=None):
    root = ET.Element("episodedetails")
    _common(root, row, artwork, persons)
    _add(root, "season", season_num)
    _add(root, "episode", ep_num)
    _add(root, "aired", date_text(pick(row, "air_date", "release_date", "first_air_date", default="")))
    _add(root, "runtime", runtime_of(row))
    return _pretty(root)


# ---------------------------------------------------------------------------
# database access
# ---------------------------------------------------------------------------

def connect(db_path):
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_items(conn, kind, title_filter=""):
    cols = columns(conn, "item")
    if not cols:
        return []
    type_col = "type" if "type" in cols else None
    if not type_col:
        return []
    sql = "SELECT * FROM item WHERE lower(type)=lower(?)"
    args = [kind]
    if title_filter:
        if "title" in cols:
            sql += " AND title LIKE ?"
        elif "name" in cols:
            sql += " AND name LIKE ?"
        else:
            sql += " AND 1=1"
        args.append("%" + title_filter + "%")
    orders = []
    for c in ("title", "sort_num", "season_number", "episode_number"):
        if c in cols:
            orders.append(c)
    if orders:
        sql += " ORDER BY " + ", ".join(orders)
    try:
        return conn.execute(sql, args).fetchall()
    except Exception:
        return []


def fetch_children(conn, parent_guid, kind):
    if not parent_guid:
        return []
    cols = columns(conn, "item")
    if "parent_guid" not in cols:
        return []
    try:
        return conn.execute(
            "SELECT * FROM item WHERE parent_guid=? AND lower(type)=lower(?) "
            "ORDER BY season_number, episode_number, sort_num",
            (parent_guid, kind)).fetchall()
    except Exception:
        try:
            return conn.execute(
                "SELECT * FROM item WHERE parent_guid=? AND lower(type)=lower(?)",
                (parent_guid, kind)).fetchall()
        except Exception:
            return []


def fetch_media_paths(conn, item_guid):
    if not item_guid:
        return []
    tbls = tables(conn)
    out = []
    for table in ("item_media", "media", "item_file"):
        if table not in tbls:
            continue
        cols = columns(conn, table)
        gcol = next((c for c in ("item_guid", "item_id", "guid_parent") if c in cols), None)
        pcol = next((c for c in ("path", "file_path", "file", "filepath") if c in cols), None)
        if not gcol or not pcol:
            continue
        try:
            rows = conn.execute("SELECT * FROM %s WHERE %s=?" % (table, gcol), (item_guid,)).fetchall()
        except Exception:
            continue
        for r in rows:
            p = as_text(r[pcol])
            if not p:
                continue
            if Path(p).suffix.lower() in VIDEO_EXTS:
                out.append(p)
    # de-duplicate, keep order
    seen = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def enrich_row(conn, row, item_guid):
    """Add genres from item_tag when the item row itself has no genre field."""
    d = dict(row)
    if d.get("genres") or d.get("genre") or d.get("genre_ids"):
        return d
    try:
        tags = conn.execute(
            "SELECT tag FROM item_tag WHERE item_guid=? AND lower(type)='genre'",
            (item_guid,)).fetchall()
    except Exception:
        tags = []
    names = []
    for t in tags:
        name = GENRE_ZH.get(as_text(t[0]))
        if name and name not in names:
            names.append(name)
    if names:
        d["genres"] = json.dumps(names, ensure_ascii=False)
    return d


def fetch_persons(conn, item_guid):
    """Best-effort actor extraction; silently skipped when schema differs."""
    tbls = tables(conn)
    link = None
    for t in ("item_person", "item_actor", "item_credit", "media_person"):
        if t not in tbls:
            continue
        c = columns(conn, t)
        if "item_guid" in c:
            gcols = [x for x in ("person_guid", "actor_guid", "people_guid", "guid") if x in c]
            if gcols:
                link = (t, "item_guid", gcols[0])
                break
    if not link:
        return []
    t, icol, pcol = link
    try:
        rows = conn.execute("SELECT * FROM %s WHERE %s=?" % (t, icol), (item_guid,)).fetchall()
    except Exception:
        return []
    if not rows:
        return []
    ptable = None
    for cand in ("person", "people", "actor"):
        if cand in tbls:
            ptable = cand
            break
    out = []
    if ptable:
        pcols = columns(conn, ptable)
        idcol = "guid" if "guid" in pcols else ("id" if "id" in pcols else None)
        if idcol:
            wanted = {as_text(r[pcol]) for r in rows}
            q = "SELECT * FROM %s WHERE %s IN (%s)" % (
                ptable, idcol, ",".join("?" * len(wanted)))
            try:
                person_rows = conn.execute(q, tuple(wanted)).fetchall()
            except Exception:
                person_rows = []
            by_id = {as_text(r[idcol]): dict(r) for r in person_rows}
            def order_key(lk):
                try:
                    return int(lk["order"]) if "order" in lk.keys() else 0
                except Exception:
                    return 0
            for lk in sorted(rows, key=order_key):
                d = by_id.get(as_text(lk[pcol]))
                if not d:
                    continue
                for field in ("role", "job", "department"):
                    if field in lk.keys() and lk[field]:
                        d[field] = as_text(lk[field])
                out.append(d)
    if not out:
        out = [dict(r) for r in rows]
    return out[:40]


# ---------------------------------------------------------------------------
# planning & writing
# ---------------------------------------------------------------------------

SEASON_RE = re.compile(r"(?:season|season\s*|s|第)\s*0?(\d{1,2})\s*(?:季)?", re.I)


def season_number_from_path(path, fallback=None):
    parts = Path(path).parts
    for part in reversed(parts[:-1]):
        m = SEASON_RE.search(part)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
    return fallback


def infer_show_dir(video_paths):
    """Find the show root from episode paths (supports Season 01 and flat layouts)."""
    dirs = [str(Path(p).parent) for p in video_paths if p]
    if not dirs:
        return ""
    if len(dirs) == 1:
        d = dirs[0]
        if SEASON_RE.search(Path(d).name):
            return str(Path(d).parent)
        return d
    common = os.path.commonpath(dirs)
    if common and os.path.basename(common) and not SEASON_RE.search(os.path.basename(common)):
        return common
    # all under one season dir -> parent is show root
    if all(os.path.dirname(d) == os.path.dirname(dirs[0]) for d in dirs):
        return os.path.dirname(dirs[0])
    return common or dirs[0]


def _art_for(path_finder, row, cfg, names=None, remote_ok=False):
    """Build art dict with locally copied file names."""
    art = {}
    poster = path_finder.resolve(pick(row, "posters", "poster", "poster_path", "thumb", default=""))
    fanart = path_finder.resolve(pick(row, "backdrops", "backdrop", "backdrop_path", "fanart", default=""))
    if poster:
        art["poster"] = poster
    if fanart:
        art["fanart"] = fanart
    if remote_ok and not poster:
        u = first_value(pick(row, "posters", "poster", default=""), "url", "file_path")
        if u:
            art["poster"] = u
    return art


def summary_meta(row, artwork=None, persons=None, cfg=None):
    """Compact metadata for the preview cards (no XML parsing in the browser)."""
    ids = {}
    for key, field in (("tmdb", "tmdb_id"), ("imdb", "imdb_id"), ("tvdb", "tvdb_id")):
        v = pick(row, field, default="")
        if v:
            ids[key] = as_text(v).split("/")[-1]
    try:
        rating = "%.1f" % float(pick(row, "vote_average", "rating", "score", default=""))
    except Exception:
        rating = as_text(pick(row, "vote_average", "rating", "score", default=""))
    actors = []
    for pr in (persons or [])[:8]:
        name = as_text(pick(pr, "name", "person_name", default=""))
        if name:
            actors.append({
                "name": name,
                "role": as_text(pick(pr, "role", "character_name", default="")),
            })
    genres = [g for g in all_values(
        pick(row, "genres", "genre", default=""), "name", "genre") if g]
    cover = ""
    if artwork:
        cover = artwork.get("poster") or artwork.get("fanart") or ""
    return {
        "title": as_text(pick(row, "title", "name", default="")),
        "original": as_text(pick(row, "original_title", "original_name", default="")),
        "year": year_of(row),
        "premiered": date_text(pick(row, "release_date", "first_air_date", "air_date", default="")),
        "rating": rating,
        "votes": as_text(pick(row, "vote_count", "votes", default="")),
        "runtime": runtime_of(row),
        "genres": genres[:6],
        "plot": as_text(pick(row, "overview", "plot", "description", "summary", default=""))[:600],
        "ids": ids,
        "actors": actors,
        "cover": cover,
        "cover_name": image_filename(cover, "poster") if cover else "",
    }


def group_preview(plan, max_items=500):
    """Aggregate write operations into one card per movie / TV series."""
    groups = {}
    for op in plan:
        key = op.get("group_key") or op["nfo"]
        g = groups.setdefault(key, {
            "key": key,
            "kind": "tv" if op["kind"] != "movie" else "movie",
            "title": op.get("group_title") or op["title"],
            "meta": op.get("group_meta") or {},
            "count": 0,
            "actions": {},
            "files": [],
            "dirs": set(),
        })
        action = op.get("nfo_action", "create")
        g["count"] += 1
        g["actions"][action] = g["actions"].get(action, 0) + 1
        g["files"].append({
            "kind": op["kind"], "title": op["title"], "nfo": op["nfo"],
            "action": action, "video": op.get("video", ""),
        })
        g["dirs"].add(os.path.dirname(op["nfo"]))
    out = []
    for g in list(groups.values())[:max_items]:
        g["dirs"] = sorted(g["dirs"])[:6]
        g["files"] = g["files"][:400]
        out.append(g)
    return out


def build_plan(conn, cfg, opts, emit=None):
    """Return list of write operations.  Each op: {nfo, text, images, title, kind}."""
    finder = ImageFinder(discover_img_roots(cfg), emit)
    tf = as_text(opts.get("title_filter") or cfg.get("title_filter") or "")
    plan = []
    roots = [os.path.normpath(r) for r in (cfg.get("media_roots") or []) if r]

    def allowed(path):
        if not roots:
            return True
        p = os.path.normpath(path)
        return any(p == r or p.startswith(r + os.sep) for r in roots)

    counters = {"movie": 0, "tv": 0, "episode": 0, "skipped": 0}

    # ---- movies ----
    if opts.get("only_movies", cfg.get("only_movies", True)):
        for raw in fetch_items(conn, "Movie", tf):
            row_guid = as_text(pick(raw, "guid", "id", default=""))
            row = enrich_row(conn, raw, row_guid)
            paths = [p for p in fetch_media_paths(conn, row_guid) if allowed(p)]
            if not paths:
                counters["skipped"] += 1
                continue
            persons = fetch_persons(conn, row_guid)
            artwork = _art_for(finder, row, cfg, remote_ok=cfg.get("include_remote_art", False))
            bindir = {}
            if cfg.get("write_images"):
                if artwork.get("poster"):
                    bindir[image_filename(artwork["poster"], "poster")] = artwork["poster"]
                if artwork.get("fanart"):
                    bindir[image_filename(artwork["fanart"], "fanart")] = artwork["fanart"]
            movie_meta = summary_meta(row, artwork, persons, cfg)
            text = build_movie_nfo(row, {
                "poster": next((k for k in bindir if k.startswith("poster.")), artwork.get("poster", "")),
                "fanart": next((k for k in bindir if k.startswith("fanart.")), artwork.get("fanart", "")),
            }, persons, cfg)
            for p in paths:
                video = Path(p)
                if not video.is_file():
                    counters["skipped"] += 1
                    continue
                nfo = video.with_suffix(".nfo")
                imgs = {nfo.parent / k: v for k, v in bindir.items()}
                if cfg.get("write_movie_nfo_alias"):
                    imgs[nfo.parent / "movie.nfo"] = None
                plan.append({
                    "kind": "movie", "title": as_text(pick(row, "title", default="")),
                    "nfo": str(nfo), "text": text, "images": imgs, "video": p,
                    "_group": ("movie:" + row_guid,
                               as_text(pick(row, "title", default="")), movie_meta),
                })
                counters["movie"] += 1

    # ---- TV ----
    if opts.get("only_tv", cfg.get("only_tv", True)):
        for raw_tv in fetch_items(conn, "TV", tf):
            tv_guid = as_text(pick(raw_tv, "guid", "id", default=""))
            tv = enrich_row(conn, raw_tv, tv_guid)
            eps = []
            for season in fetch_children(conn, tv_guid, "Season"):
                s_guid = as_text(pick(season, "guid", "id", default=""))
                for ep in fetch_children(conn, s_guid, "Episode"):
                    eps.append((season, ep))
            if not eps:
                # some schemas attach episodes directly to the show
                for ep in fetch_children(conn, tv_guid, "Episode"):
                    eps.append((None, ep))
            videos = []
            for season, ep in eps:
                eguid = as_text(pick(ep, "guid", "id", default=""))
                for p in fetch_media_paths(conn, eguid):
                    videos.append((season, ep, p))
            videos = [(s, e, p) for (s, e, p) in videos if allowed(p)]
            if not videos:
                counters["skipped"] += 1
                continue
            show_dir = infer_show_dir([p for _s, _e, p in videos])
            artwork = _art_for(finder, tv, cfg, remote_ok=cfg.get("include_remote_art", False))
            bindir = {}
            if cfg.get("write_images"):
                if artwork.get("poster"):
                    bindir[image_filename(artwork["poster"], "poster")] = artwork["poster"]
                if artwork.get("fanart"):
                    bindir[image_filename(artwork["fanart"], "fanart")] = artwork["fanart"]
            tv_persons = fetch_persons(conn, tv_guid)
            tv_meta = summary_meta(tv, artwork, tv_persons, cfg)
            tv_text = build_tvshow_nfo(tv, {
                "poster": next((k for k in bindir if k.startswith("poster.")), artwork.get("poster", "")),
                "fanart": next((k for k in bindir if k.startswith("fanart.")), artwork.get("fanart", "")),
            }, tv_persons, cfg)
            plan.append({
                "kind": "tvshow", "title": as_text(pick(tv, "title", default="")),
                "nfo": os.path.join(show_dir, "tvshow.nfo"), "text": tv_text,
                "images": {Path(show_dir) / k: v for k, v in bindir.items()},
                "video": show_dir,
                "_group": ("tv:" + tv_guid, as_text(pick(tv, "title", default="")), tv_meta),
            })
            counters["tv"] += 1
            handled_seasons = set()
            for season, ep, p in videos:
                video = Path(p)
                if not video.is_file():
                    counters["skipped"] += 1
                    continue
                sraw = pick(season, "season_number", default=None) if season else None
                eraw = pick(ep, "episode_number", default=None)
                sdir = str(video.parent)
                s_num = int(float(sraw)) if sraw not in (None, "") else (
                    season_number_from_path(p) or 1)
                try:
                    e_num = int(float(eraw))
                except Exception:
                    counters["skipped"] += 1
                    continue
                if cfg.get("write_season_nfo") and season is not None and sdir not in handled_seasons:
                    handled_seasons.add(sdir)
                    sa = {}
                    sposter = finder.resolve(pick(season, "posters", "poster", "poster_path", default=""))
                    simgs = {}
                    if cfg.get("write_images") and sposter:
                        sname = image_filename(sposter, "season%02d" % s_num)
                        simgs[Path(sdir) / sname] = sposter
                        sa["poster"] = sname
                    plan.append({
                        "kind": "season", "title": as_text(pick(season, "title", default="")),
                        "nfo": os.path.join(sdir, "season.nfo"),
                        "text": build_season_nfo(season, s_num, sa),
                        "images": simgs, "video": p,
                        "_group": ("tv:" + tv_guid, as_text(pick(tv, "title", default="")), tv_meta),
                    })
                ep_art = {}
                eimgs = {}
                still = finder.resolve(pick(ep, "still_path", "thumb", "poster", default=""))
                if cfg.get("write_episode_thumb") and cfg.get("write_images") and still:
                    tname = image_filename(still, video.stem + "-thumb")
                    eimgs[video.with_name(tname)] = still
                    ep_art["thumb"] = tname
                text = build_episode_nfo(ep, s_num, e_num, ep_art,
                                         fetch_persons(conn, ep["guid"] if "guid" in ep.keys() else ""), cfg)
                plan.append({
                    "kind": "episode", "title": as_text(pick(ep, "title", default="")),
                    "nfo": str(video.with_suffix(".nfo")), "text": text,
                    "images": eimgs, "video": p,
                    "_group": ("tv:" + tv_guid, as_text(pick(tv, "title", default="")), tv_meta),
                })
                counters["episode"] += 1

    for op in plan:
        gk, gt, gm = op.pop("_group", ("", "", {}))
        op["group_key"] = gk
        op["group_title"] = gt
        op["group_meta"] = gm

    for op in plan:
        if cfg.get("overwrite_nfo"):
            op["nfo_action"] = "overwrite"
        elif not os.path.exists(op["nfo"]):
            op["nfo_action"] = "create"
        elif cfg.get("overwrite_only_ours", True) and is_ours(op["nfo"]):
            op["nfo_action"] = "refresh"
        else:
            op["nfo_action"] = "skip"

    limit = int(opts.get("limit") or cfg.get("limit") or 0)
    if limit > 0:
        plan = plan[:limit]
    return plan, counters


def write_plan(plan, cfg, emit=None, stop_event=None, progress_cb=None):
    stats = {"created": 0, "updated": 0, "skipped": 0, "images": 0, "errors": 0, "files": []}
    for idx, op in enumerate(plan):
        if stop_event is not None and stop_event.is_set():
            log("已收到停止信号", emit)
            break
        nfo = Path(op["nfo"])
        action = op.get("nfo_action", "create")
        try:
            if action == "skip":
                stats["skipped"] += 1
            else:
                write_text_atomic(nfo, op["text"])
                stats["created" if action == "create" else "updated"] += 1
                stats["files"].append(str(nfo))
            if cfg.get("write_images"):
                for dst, src in (op.get("images") or {}).items():
                    dst = Path(dst)
                    if src is None:  # alias NFO
                        if not dst.exists() and nfo.exists():
                            shutil.copy2(nfo, dst)
                            os.chmod(dst, 0o644)
                            stats["created"] += 1
                        continue
                    if not os.path.isfile(src):
                        continue
                    if dst.exists() and not cfg.get("overwrite_images", False):
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    os.chmod(dst, 0o644)
                    stats["images"] += 1
        except Exception as exc:
            stats["errors"] += 1
            log("写入失败 %s: %s" % (nfo, exc), emit)
        if (idx + 1) % 20 == 0 or idx + 1 == len(plan):
            log("进度 %d/%d" % (idx + 1, len(plan)), emit)
            if progress_cb:
                progress_cb(idx + 1)
    return stats


# ---------------------------------------------------------------------------
# job runner
# ---------------------------------------------------------------------------

class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.lines = []
        self.state = "idle"
        self.progress = 0
        self.total = 0
        self.stats = {}
        self.plan_preview = []
        self.groups = []
        self.mode = "scan"          # scan | export | delete
        self.stop_event = threading.Event()
        self.thread = None
        self.started = 0

    def emit(self, line):
        with self.lock:
            self.lines.append(line)
            if len(self.lines) > 3000:
                del self.lines[:500]

    def snapshot(self, tail=200):
        with self.lock:
            return {
                "state": self.state, "progress": self.progress, "total": self.total,
                "stats": self.stats, "lines": self.lines[-tail:],
                "started": self.started, "mode": self.mode,
            }

    def start(self, fn):
        if self.thread and self.thread.is_alive():
            return False
        self.stop_event = threading.Event()
        self.started = time.time()
        with self.lock:
            self.lines = []
            self.state = "running"
            self.progress = 0
            self.total = 0
            self.stats = {}
            self.groups = []

        def runner():
            try:
                result = fn(self)
                with self.lock:
                    self.stats = result.get("stats", {})
                    self.state = result.get("state", "done")
            except Exception as exc:
                self.emit("任务异常：%s" % exc)
                with self.lock:
                    self.state = "error"

        self.thread = threading.Thread(target=runner, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self.state = "stopping"

    def set_progress(self, n):
        with self.lock:
            self.progress = n


def run_export(job, cfg, opts):
    with job.lock:
        job.mode = "export"
    db = discover_db_path(cfg)
    job.emit("数据库：%s" % db)
    if not os.path.isfile(db):
        job.emit("数据库不存在，请检查影视应用是否已安装并完成刮削")
        return {"state": "error", "stats": {}}
    conn = connect(db)
    try:
        job.emit("开始扫描元数据…")
        plan, counters = build_plan(conn, cfg, opts, job.emit)
        keys = opts.get("keys")
        if keys:
            if isinstance(keys, str):
                keys = [keys]
            wanted = set(keys)
            plan = [op for op in plan if op.get("group_key") in wanted]
            job.emit("按预览勾选导出：%d 个写入项" % len(plan))
        job.emit("扫描完成：电影 %d / 剧集 %d / 分集 %d / 跳过 %d" % (
            counters["movie"], counters["tv"], counters["episode"], counters["skipped"]))
        with job.lock:
            job.total = len(plan)
            job.groups = group_preview(plan)
            job.plan_preview = [{
                "kind": p["kind"], "title": p["title"], "nfo": p["nfo"],
                "action": p.get("nfo_action"), "video": p.get("video", ""),
            } for p in plan[:100]]
        stats = write_plan(plan, cfg, job.emit, job.stop_event,
                           job.set_progress)
        job.emit("完成：新建 %d / 更新 %d / 跳过 %d / 图片 %d / 错误 %d" % (
            stats["created"], stats["updated"], stats["skipped"],
            stats["images"], stats["errors"]))
        outdir = opts.get("var_dir", "")
        if outdir:
            try:
                with open(os.path.join(outdir, "last_export.json"), "w", encoding="utf-8") as fh:
                    json.dump({"time": time.time(), "config": cfg, "stats": stats,
                               "files": stats.get("files", [])[:5000]}, fh, ensure_ascii=False, indent=2)
            except Exception:
                pass
        return {"state": "done" if not job.stop_event.is_set() else "stopped", "stats": stats}
    finally:
        conn.close()


def run_scan(job, cfg, opts):
    with job.lock:
        job.mode = "scan"
    db = discover_db_path(cfg)
    job.emit("数据库：%s" % db)
    if not os.path.isfile(db):
        job.emit("数据库不存在")
        return {"state": "error", "stats": {}}
    conn = connect(db)
    try:
        plan, counters = build_plan(conn, cfg, opts, job.emit)
        with job.lock:
            job.groups = group_preview(plan)
            job.plan_preview = [{
                "kind": p["kind"], "title": p["title"], "nfo": p["nfo"],
                "action": p.get("nfo_action"), "video": p.get("video", ""),
                "sample": p["text"][:1200],
            } for p in plan[:200]]
        job.emit("预览：共 %d 个写入项（电影 %d / 剧集 %d / 分集 %d）" % (
            len(plan), counters["movie"], counters["tv"], counters["episode"]))
        return {"state": "done", "stats": {"plan": len(plan), **counters}}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# delete (cleanup) operations — reverse of write_plan, safety first
# ---------------------------------------------------------------------------

def build_delete_plan(conn, cfg, opts, emit=None):
    """Find metabridge-generated files that exist on disk and can be removed.

    Reuses build_plan so the exact set of generated paths is known.  A file is
    only a delete candidate when:
      * it is an NFO carrying our MARKER, or
      * it is an image/alias produced for a work whose NFO carries our MARKER.
    Video files are never candidates.  When media_roots is configured, only
    paths under those roots are considered.

    Returns (groups, counters).  Each group mirrors group_preview's shape but
    its `files` entries are {path, kind, size} describing files to remove.
    """
    plan, counters = build_plan(conn, cfg, opts, emit)
    roots = [os.path.normpath(r) for r in (cfg.get("media_roots") or []) if r]

    def allowed(path):
        if not roots:
            return True
        norm = os.path.normpath(path)
        return any(norm == r or norm.startswith(r + os.sep) for r in roots)

    groups = {}
    seen = set()
    for op in plan:
        nfo = Path(op["nfo"])
        marked = nfo.is_file() and is_ours(str(nfo))
        files = []
        if marked and allowed(str(nfo)):
            files.append({"path": str(nfo), "kind": "nfo", "size": nfo.stat().st_size})
        # images / alias are only ours when the paired NFO is ours
        if marked:
            for dst in (op.get("images") or {}):
                dst = Path(dst)
                if not dst.is_file() or not allowed(str(dst)):
                    continue
                suffix = dst.suffix.lower()
                if suffix in VIDEO_EXTS:
                    continue
                if suffix == ".nfo" and not is_ours(str(dst)):
                    continue
                if suffix == ".nfo":
                    kind = "nfo"
                elif suffix in IMAGE_EXTS:
                    kind = "image"
                else:
                    continue
                try:
                    size = dst.stat().st_size
                except OSError:
                    continue
                files.append({"path": str(dst), "kind": kind, "size": size})
        fresh = [f for f in files if f["path"] not in seen]
        for f in fresh:
            seen.add(f["path"])
        if not fresh:
            continue
        key = op.get("group_key") or str(nfo)
        g = groups.setdefault(key, {
            "key": key,
            "kind": "tv" if op["kind"] != "movie" else "movie",
            "title": op.get("group_title") or op["title"],
            "meta": op.get("group_meta") or {},
            "count": 0,
            "files": [],
            "dirs": set(),
        })
        for f in fresh:
            g["files"].append(f)
            g["dirs"].add(os.path.dirname(f["path"]))
            g["count"] += 1
    for g in groups.values():
        g["dirs"] = sorted(g["dirs"])[:6]
    return list(groups.values()), counters


def delete_files(files, emit=None, stop_event=None, progress_cb=None):
    """Delete the given generated files with a final safety check on each."""
    stats = {"deleted": 0, "nfo": 0, "images": 0, "skipped": 0, "errors": 0, "files": []}
    for idx, f in enumerate(files):
        if stop_event is not None and stop_event.is_set():
            log("已收到停止信号", emit)
            break
        path = Path(f["path"])
        try:
            suffix = path.suffix.lower()
            if suffix in VIDEO_EXTS:
                stats["skipped"] += 1
                log("保护视频文件，跳过 %s" % path, emit)
                continue
            if not path.is_file():
                stats["skipped"] += 1
                continue
            if suffix == ".nfo" and not is_ours(str(path)):
                stats["skipped"] += 1
                log("非本工具 NFO，跳过 %s" % path, emit)
                continue
            path.unlink()
            stats["deleted"] += 1
            stats["nfo" if suffix == ".nfo" else "images"] += 1
            stats["files"].append(str(path))
            log("已删除 %s" % path, emit)
        except Exception as exc:
            stats["errors"] += 1
            log("删除失败 %s: %s" % (path, exc), emit)
        if (idx + 1) % 20 == 0 or idx + 1 == len(files):
            log("进度 %d/%d" % (idx + 1, len(files)), emit)
            if progress_cb:
                progress_cb(idx + 1)
    return stats


def run_delete_scan(job, cfg, opts):
    with job.lock:
        job.mode = "delete"
    db = discover_db_path(cfg)
    job.emit("数据库：%s" % db)
    if not os.path.isfile(db):
        job.emit("数据库不存在")
        return {"state": "error", "stats": {}}
    conn = connect(db)
    try:
        groups, counters = build_delete_plan(conn, cfg, opts, job.emit)
        total = sum(g["count"] for g in groups)
        with job.lock:
            job.groups = groups
            job.plan_preview = [{"kind": g["kind"], "title": g["title"], "nfo": "",
                                 "files": g["files"]} for g in groups[:200]]
        job.emit("可清理：%d 部作品 / %d 个文件（电影 %d / 剧集 %d / 分集 %d）" % (
            len(groups), total, counters["movie"], counters["tv"], counters["episode"]))
        return {"state": "done", "stats": {"groups": len(groups), "files": total, **counters}}
    finally:
        conn.close()


def run_delete(job, cfg, opts):
    with job.lock:
        job.mode = "delete"
    db = discover_db_path(cfg)
    job.emit("数据库：%s" % db)
    if not os.path.isfile(db):
        job.emit("数据库不存在")
        return {"state": "error", "stats": {}}
    conn = connect(db)
    try:
        groups, counters = build_delete_plan(conn, cfg, opts, job.emit)
        keys = opts.get("keys")
        if keys:
            if isinstance(keys, str):
                keys = [keys]
            wanted = set(keys)
            groups = [g for g in groups if g["key"] in wanted]
            job.emit("按勾选清理：%d 部作品" % len(groups))
        files = [f for g in groups for f in g["files"]]
        with job.lock:
            job.total = len(files)
            job.groups = groups
        job.emit("开始删除 %d 个元文件（视频文件受保护，不会被删除）" % len(files))
        stats = delete_files(files, job.emit, job.stop_event, job.set_progress)
        job.emit("完成：删除 %d 个（NFO %d / 图片 %d / 跳过 %d / 失败 %d）" % (
            stats["deleted"], stats["nfo"], stats["images"],
            stats["skipped"], stats["errors"]))
        outdir = opts.get("var_dir", "")
        if outdir:
            try:
                with open(os.path.join(outdir, "last_delete.json"), "w", encoding="utf-8") as fh:
                    json.dump({"time": time.time(), "stats": stats,
                               "files": stats.get("files", [])[:5000]}, fh,
                              ensure_ascii=False, indent=2)
            except Exception:
                pass
        return {"state": "done" if not job.stop_event.is_set() else "stopped", "stats": stats}
    finally:
        conn.close()
