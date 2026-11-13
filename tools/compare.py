#!/usr/bin/env python3
"""Apply the published formula to comparable LOCAL baseline/candidate reports."""
import argparse
from decimal import Decimal, ROUND_HALF_UP
import json
import statistics
from pathlib import Path


def compare(base, candidate):
    for label, report in (("baseline", base), ("candidate", candidate)):
        assert report["passed"] and len(report["rounds"]) == 3, f"{label}: three passing rounds required"
        assert all(r["passed"] for r in report["rounds"]), label
        for r in report["rounds"]:
            assert min(r["query"]["recall"].values()) >= .95
            for stage in ("write", "query"):
                assert r[stage].get("host_swap_io_delta", {}).get("pswpout", 0) == 0, "swap activity invalidates memory comparison"
    assert base["workload"] == candidate["workload"], "different workload/seed/mode"
    assert base["environment"] == candidate["environment"], "different host/runtime/resources"
    assert base["measurement"] == candidate["measurement"], "different measurement boundaries"
    scores = []
    for r in candidate["rounds"]:
        stage_scores = {}
        for stage in ("write", "query"):
            value = 0
            for key, epsilon, weight in (("T_seconds", .001, .6), ("B_bytes", 1048576, .2), ("M_bytes", 1048576, .2)):
                reference = statistics.median(br[stage][key] for br in base["rounds"])
                actual = r[stage][key]
                assert reference >= 0 and actual >= 0
                ratio = (reference+epsilon)/(actual+epsilon)
                value += weight*ratio/(1+ratio)
            stage_scores[stage] = value
        scores.append({"round": r["round"], **stage_scores,
                       "E": (stage_scores["write"]+2*stage_scores["query"])/3})
    median = Decimal(str(statistics.median(r["E"] for r in scores))).quantize(Decimal(".000001"), rounding=ROUND_HALF_UP)
    return {"scope": "local relative E; no official reference or rank available",
            "rounds": scores, "median_E": str(median)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("baseline", type=Path)
    p.add_argument("candidate", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    result = compare(json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text()))
    text = json.dumps(result, indent=2)+"\n"
    if args.output:
        with args.output.open("x") as out: out.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
