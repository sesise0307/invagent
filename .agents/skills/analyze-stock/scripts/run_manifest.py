#!/usr/bin/env python3
"""Write an atomic, auditable manifest for a multi-step stock preparation run."""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
from invagent.core.files import atomic_write

def write_manifest(path: Path, run_id: str, steps: list[dict]) -> None:
    payload={"schema_version":1,"run_id":run_id,"finished_at":datetime.now(timezone.utc).isoformat(),"steps":steps}
    atomic_write(path,json.dumps(payload,ensure_ascii=False,indent=2)+"\n")

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("path",type=Path); p.add_argument("--run-id",required=True); p.add_argument("--step",action="append",default=[],help="name=status[:elapsed_ms]"); a=p.parse_args(argv)
    steps=[]
    for raw in a.step:
        name,_,rest=raw.partition("="); status,_,elapsed=rest.partition(":")
        steps.append({"name":name,"status":status,"elapsed_ms":int(elapsed or 0)})
    write_manifest(a.path,a.run_id,steps); return 0
if __name__ == "__main__": raise SystemExit(main())
