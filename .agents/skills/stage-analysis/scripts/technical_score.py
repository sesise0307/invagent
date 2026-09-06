#!/usr/bin/env python3
"""Compute the objective portion of the eight-point technical checklist from OHLCV JSON."""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path

def score(rows: list[dict]) -> dict:
    closes=[float(r["close"]) for r in rows]; highs=[float(r.get("high",r["close"])) for r in rows]; lows=[float(r.get("low",r["close"])) for r in rows]
    if len(closes)<200: raise ValueError("at least 200 bars are required")
    sma=lambda n: sum(closes[-n:])/n
    ma50,ma150,ma200=sma(50),sma(150),sma(200); last=closes[-1]
    prior_ma200 = sum(closes[-400:-200]) / 200 if len(closes) >= 400 else None
    checks={"price_above_50d":last>ma50,"price_above_150d":last>ma150,"price_above_200d":last>ma200,"ma50_above_150d":ma50>ma150,"ma150_above_200d":ma150>ma200,"ma200_rising":ma200>prior_ma200 if prior_ma200 is not None else None,"near_52w_high":last>=0.75*max(highs[-252:]),"relative_strength":None}
    known=sum(v is not None for v in checks.values()); passed=sum(v is True for v in checks.values())
    return {"schema_version":1,"checks":checks,"known":known,"passed":passed,"technical_score":passed/known if known else 0.0,"unknown_checks":[k for k,v in checks.items() if v is None]}

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("input",type=Path); a=p.parse_args(argv)
    try: print(json.dumps(score(json.loads(a.input.read_text(encoding="utf-8"))),ensure_ascii=False,indent=2)); return 0
    except (OSError,ValueError,KeyError,TypeError) as e: print(json.dumps({"ok":False,"error":str(e)},ensure_ascii=False)); return 2
if __name__ == "__main__": sys.exit(main())
