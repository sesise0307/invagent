#!/usr/bin/env python3
"""Replace one marked monthly review block while preserving the source body."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

def upsert(existing: str, proposed: str, month: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("month must be YYYY-MM")
    begin = f"<!-- invagent-review:{month}:begin -->"
    end = f"<!-- invagent-review:{month}:end -->"
    block = f"{begin}\n{proposed.strip()}\n{end}"
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
    return pattern.sub(block, existing, count=1) if pattern.search(existing) else existing.rstrip() + "\n\n" + block + "\n"

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--existing",required=True,type=Path); p.add_argument("--proposed",required=True,type=Path); p.add_argument("--month",required=True); p.add_argument("--prepared-out",required=True,type=Path); p.add_argument("--json",action="store_true"); a=p.parse_args(argv)
    try:
        out=upsert(a.existing.read_text(encoding="utf-8"), a.proposed.read_text(encoding="utf-8"), a.month)
        a.prepared_out.parent.mkdir(parents=True,exist_ok=True); a.prepared_out.write_text(out,encoding="utf-8")
    except (OSError,ValueError) as e:
        print(json.dumps({"ok":False,"error":str(e)},ensure_ascii=False)); return 2
    print(json.dumps({"ok":True,"month":a.month,"replaced":f"invagent-review:{a.month}:begin" in a.existing.read_text(encoding="utf-8")},ensure_ascii=False) if a.json else str(a.prepared_out))
    return 0
if __name__ == "__main__": sys.exit(main())
