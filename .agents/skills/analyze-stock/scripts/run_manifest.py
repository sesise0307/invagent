#!/usr/bin/env python3
"""Write an atomic, auditable manifest for a multi-step stock preparation run."""
from __future__ import annotations
import argparse, json, os, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

def write_manifest(path: Path, run_id: str, steps: list[dict]) -> None:
    payload={"schema_version":1,"run_id":run_id,"finished_at":datetime.now(timezone.utc).isoformat(),"steps":steps}
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=path.name+".",dir=path.parent); os.close(fd)
    try: Path(tmp).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("path",type=Path); p.add_argument("--run-id",required=True); p.add_argument("--step",action="append",default=[],help="name=status[:elapsed_ms]"); a=p.parse_args(argv)
    steps=[]
    for raw in a.step:
        name,_,rest=raw.partition("="); status,_,elapsed=rest.partition(":")
        steps.append({"name":name,"status":status,"elapsed_ms":int(elapsed or 0)})
    write_manifest(a.path,a.run_id,steps); return 0
if __name__ == "__main__": raise SystemExit(main())
