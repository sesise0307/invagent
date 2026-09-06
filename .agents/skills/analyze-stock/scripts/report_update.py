#!/usr/bin/env python3
"""Prepare a safe rolling-report update; refuse stale or conflicting sources."""
from __future__ import annotations
import argparse, hashlib, json, os, tempfile, sys
from pathlib import Path

def apply_update(source: Path, target: Path, update: Path, expected_sha256: str | None = None) -> dict:
    raw=source.read_bytes(); digest=hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256: raise ValueError("source changed since it was read")
    if target.exists() and target.resolve()!=source.resolve(): raise FileExistsError(f"target exists: {target}")
    text=raw.decode("utf-8"); addition=update.read_text(encoding="utf-8").strip()
    merged=text.rstrip()+"\n\n"+addition+"\n"
    target.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=target.name+".",dir=target.parent); os.close(fd)
    try: Path(tmp).write_text(merged,encoding="utf-8"); os.replace(tmp,target)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return {"source_sha256":digest,"target":str(target),"bytes":len(merged.encode())}

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--source",required=True,type=Path); p.add_argument("--target",required=True,type=Path); p.add_argument("--update",required=True,type=Path); p.add_argument("--expected-sha256"); p.add_argument("--json",action="store_true"); a=p.parse_args(argv)
    try: result=apply_update(a.source,a.target,a.update,a.expected_sha256)
    except (OSError,ValueError) as e: print(json.dumps({"ok":False,"error":str(e)},ensure_ascii=False)); return 2
    print(json.dumps({"ok":True,**result},ensure_ascii=False) if a.json else result["target"]); return 0
if __name__ == "__main__": sys.exit(main())
