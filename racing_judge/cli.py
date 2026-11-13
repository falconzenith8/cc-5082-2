"""Command line entry: run scenarios, verify replays, inspect evidence.

The CLI is a supplementary inspection path.  Interactive judging — driving a
vehicle, opening a penalty's raw segment, scrubbing a replay — lives in the
in-game web interface, not here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .diagnostics import analyze_replay, recompute_segment
from .model import Track
from .replay import Replay, verify_replay
from .scenarios import SCENARIOS
from .results import rank_vehicles


def _print_judge_report(snap: dict) -> None:
    for vid, data in snap["vehicles"].items():
        summ = data["summary"]
        print(f"\n=== {vid} ===")
        print(f"  lap {summ['lap']}/{summ['total_laps']}  "
              f"finished={summ['finished']}  finish_t={summ['finish_t']}")
        print(f"  valid driving : {summ['valid_driving_s']:8.3f}s")
        print(f"  penalty total : {summ['penalty_s']:8.3f}s")
        print(f"  total time    : {summ['total_time_s']:8.3f}s")
        print(f"  valid crossings: {summ['valid_crossings']}  "
              f"next expected: {summ['expected_next']}")
        if data["penalties"]:
            print("  penalty ledger:")
            for p in data["penalties"]:
                print(f"    t={p['t']:7.3f}  {p['source']:<16} "
                      f"+{p['seconds']:5.1f}s  zone={p['zone_id']}")
        print("  crossing evidence (zone / dir / valid / hit / invalidation):")
        for e in data["evidence"]:
            hit = ", ".join(f"{c:.2f}" for c in e["hit"])
            print(f"    t={e['t']:7.3f}  {e['zone_id']:<10} dir={e['direction']:+d} "
                  f"valid={str(e['valid']):<5} hit=({hit}) "
                  f"{e['invalidation'] or ''}")
    if snap["ranking"]:
        print("\n=== ranking ===")
        for row in snap["ranking"]:
            rank = row["rank"] if row["rank"] is not None else f"DNF({row['dnf_rank']})"
            print(f"  {rank!s:<8} {row['vehicle_id']:<6} "
                  f"laps={row['laps']} total={row['total_time_s']:.3f}s "
                  f"drive={row['valid_driving_s']:.3f}s "
                  f"penalty={row['penalty_s']:.3f}s status={row['status']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="3D racing race judge CLI")
    parser.add_argument("--track", default="data/tracks/fork_loop.json")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scenarios", help="list built-in scenarios")

    run_p = sub.add_parser("run", help="run a scenario")
    run_p.add_argument("scenario", choices=sorted(SCENARIOS))
    run_p.add_argument("--replay-dir", default="data/replays")
    run_p.add_argument("--max-seconds", type=float, default=400.0)
    run_p.add_argument("--json", action="store_true")

    ver_p = sub.add_parser("verify", help="verify a saved replay")
    ver_p.add_argument("replay_path")

    diag_p = sub.add_parser(
        "diagnose",
        help="multi-interval segment diagnostics for a saved replay",
    )
    diag_p.add_argument("replay_path")
    diag_p.add_argument("--current-track",
                        help="on-disk track id for version comparison")
    diag_p.add_argument("--strides", default="1,2,4,8",
                        help="comma-separated sample strides")
    diag_p.add_argument("--segment", type=int, default=None,
                        help="recompute one raw segment on demand and exit")
    diag_p.add_argument("--vehicle", default=None)

    list_p = sub.add_parser("list-replays", help="list replay files")
    list_p.add_argument("--replay-dir", default="data/replays")

    args = parser.parse_args(argv)

    if args.cmd == "scenarios":
        for name, fn in sorted(SCENARIOS.items()):
            print(f"{name:<12} {fn.__doc__.strip().splitlines()[0]}")
        return 0

    if args.cmd == "list-replays":
        if not os.path.isdir(args.replay_dir):
            print("(no replay directory)")
            return 0
        for name in sorted(os.listdir(args.replay_dir)):
            print(name)
        return 0

    if args.cmd == "verify":
        replay = Replay.load(args.replay_path)
        report = verify_replay(replay)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["matches"] else 1

    if args.cmd == "diagnose":
        replay = Replay.load(args.replay_path)
        if args.segment is not None:
            vid = args.vehicle or replay.vehicles[0]
            out = recompute_segment(replay, vid, args.segment)
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return 0
        current = None
        if args.current_track:
            current = Track.load(
                os.path.join(os.path.dirname(args.track) or ".",
                             args.current_track + ".json")
                if not os.path.exists(args.current_track)
                else args.current_track
            )
        strides = [int(x) for x in args.strides.split(",") if x.strip()]
        report = analyze_replay(replay, current_track=current, strides=strides)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "run":
        track = Track.load(args.track)
        session = SCENARIOS[args.scenario](track)
        snap = session.run_until_finish(max_seconds=args.max_seconds)
        path = session.replay.save(args.replay_dir)
        original = {
            vid: {
                "laps": d["summary"]["lap"] - (0 if d["summary"]["finished"] else 1),
                "valid_crossings": d["summary"]["valid_crossings"],
                "penalty_s": d["summary"]["penalty_s"],
                "total_time_s": d["summary"]["total_time_s"],
            }
            for vid, d in snap["vehicles"].items()
        }
        report = verify_replay(session.replay, original)
        if args.json:
            print(json.dumps({"snapshot": snap, "replay": path,
                              "verification": report}, indent=2, ensure_ascii=False))
        else:
            _print_judge_report(snap)
            print(f"\nreplay saved: {path}")
            print(f"replay verification: {'MATCH' if report['matches'] else 'MISMATCH'}")
        return 0 if report["matches"] else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
