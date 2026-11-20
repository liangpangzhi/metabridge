#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only schema report. Run on the NAS if field mapping needs tuning.

    python3 inspect_db.py [db_path]
"""
import sqlite3
import sys
from pathlib import Path

DB = sys.argv[1] if len(sys.argv) > 1 else \
    "/usr/local/apps/@appdata/trim.media/database/trimmedia.db"


def main():
    p = Path(DB)
    print("db:", p, "exists:", p.is_file())
    if not p.is_file():
        return 1
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
    con.row_factory = sqlite3.Row
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % t)]
        n = con.execute('SELECT count(*) FROM "%s"' % t).fetchone()[0]
        print("\n[%s] rows=%d" % (t, n))
        print("  " + ", ".join(cols))
    for t in ("item", "item_media"):
        if t in tables:
            row = con.execute('SELECT * FROM "%s" LIMIT 1' % t).fetchone()
            if row:
                print("\nSAMPLE", t)
                for k in row.keys():
                    v = str(row[k])
                    print("  %-28s %s" % (k, v[:100]))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
