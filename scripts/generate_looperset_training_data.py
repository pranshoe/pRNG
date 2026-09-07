#!/usr/bin/env python3
"""
generate_looperset_training_data.py

Full pipeline from raw LOOPerSet -> training_data_<transform>.csv,
matching the shape of your PolyBench MVP pipeline but for v2 targets
(tiling, fusion, unrolling, parallelization, skewing, interchange).

Usage:
  python3 generate_looperset_training_data.py --transform tiling --limit 5000
  python3 generate_looperset_training_data.py --transform tiling --limit 5000 --allow-prefix interchange
"""

import argparse
import csv
import math
import statistics
import sys
from datasets import load_dataset
from tqdm import tqdm

from looperset_features import extract_program_features


def robust_time(times) -> float:
    """
    Median with light outlier trimming: drop the top/bottom 10% of samples
    before taking the median, since LOOPerSet's raw execution_times show
    occasional spikes (system noise / scheduling jitter).

    Handles schema variants seen across LOOPerSet's ~220K programs:
      - a flat list of numeric samples (the common case)
      - a dict keyed by hardware platform name (e.g. "xeon_e5_2695_v2"),
        where each value is itself a list of samples, or sometimes a
        single scalar value
    Also tolerates string-typed numeric samples either way.
    """
    times = _flatten_time_value(times)
    if not times:
        raise ValueError("empty execution_times")
    s = sorted(times)
    n = len(s)
    trim = max(1, int(n * 0.1))
    trimmed = s[trim: n - trim] if n > 2 * trim else s
    return statistics.median(trimmed)


def _flatten_time_value(value) -> list:
    """
    Normalize a timing field into a flat list of floats, regardless of
    whether LOOPerSet stored it as:
      - a single number (int/float/numeric string)
      - a flat list of numbers
      - a dict keyed by hardware platform, whose values are themselves
        either a list of samples or a single scalar

    This same normalization is needed for both `execution_times` (per
    schedule) and `initial_execution_time` (per program) -- both fields
    have shown up in either shape across the dataset, and hardcoding a
    fix for just one of them (as an earlier version of this script did)
    just means the other one breaks the same way on a different scan.
    """
    if isinstance(value, dict):
        flattened = []
        for v in value.values():
            if isinstance(v, list):
                flattened.extend(v)
            elif isinstance(v, (int, float, str)):
                flattened.append(v)
        return [float(t) for t in flattened]
    elif isinstance(value, list):
        return [float(t) for t in value]
    else:
        return [float(value)]


def classify_schedule(transformations_list, target_type, allow_prefixes):
    """
    Decide whether this schedule qualifies as a "single-transformation"
    example for the target type.

    Accepts:
      - transformations_list containing exactly one entry of target_type
      - transformations_list containing entries from allow_prefixes
        (e.g. "interchange") alongside exactly one entry of target_type,
        since interchange is often a required legality precondition and
        not itself the effect being measured

    Rejects:
      - transformations_list == [] (baseline; handled separately)
      - zero or multiple entries of target_type
      - any entry whose type is neither target_type nor in allow_prefixes
    """
    if not transformations_list:
        return False

    types_in_order = [t["type"] for t in transformations_list]

    target_positions = [i for i, t in enumerate(types_in_order) if t == target_type]
    if len(target_positions) != 1:
        return False

    other_types = [t for t in types_in_order if t != target_type]
    if any(t not in allow_prefixes for t in other_types):
        return False

    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transform", required=True,
                         help="Target transformation type, e.g. tiling, fusion, "
                              "unrolling, parallelization, skewing, interchange")
    parser.add_argument("--allow-prefix", action="append", default=[],
                         help="Transformation type(s) allowed to appear alongside "
                              "the target (e.g. interchange, if it's a legality "
                              "precondition rather than the effect being measured). "
                              "Can be passed multiple times.")
    parser.add_argument("--config", default="full_compact")
    parser.add_argument("--limit", type=int, default=5000,
                         help="Number of programs to scan (LOOPerSet is large; "
                              "streaming the whole thing is slow -- start small)")
    parser.add_argument("--out", default=None,
                         help="Output CSV path (default: training_data_<transform>.csv)")
    parser.add_argument("--checkpoint-every", type=int, default=2000,
                         help="Flush rows to disk every N programs scanned, so a "
                              "long high-limit scan can be resumed/inspected "
                              "if interrupted, instead of losing everything.")
    args = parser.parse_args()

    out_path = args.out or f"training_data_{args.transform}.csv"
    allow_prefixes = set(args.allow_prefix)

    ds = load_dataset("Mascinissa/LOOPerSet", args.config, split="train", streaming=True)

    feature_keys = None
    rows = []
    programs_scanned = 0
    programs_with_match = 0
    schedules_seen = 0
    schedules_matched = 0
    csv_writer = None
    csv_file = None

    def flush_rows():
        """Append buffered rows to disk and clear the in-memory buffer."""
        nonlocal rows, csv_writer, csv_file
        if not rows:
            return
        if csv_writer is None:
            fieldnames = list(rows[0].keys())
            csv_file = open(out_path, "w", newline="")
            csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            csv_writer.writeheader()
        csv_writer.writerows(rows)
        csv_file.flush()
        rows = []

    pbar = tqdm(ds, total=args.limit, desc="Scanning programs", unit="prog")
    for example in pbar:
        programs_scanned += 1
        if programs_scanned > args.limit:
            break

        try:
            prog_features = extract_program_features(example["program_annotation"])
        except Exception as e:
            print(f"[warn] feature extraction failed for {example.get('program_name')}: {e}",
                  file=sys.stderr)
            continue

        if feature_keys is None:
            feature_keys = list(prog_features.keys())

        try:
            initial_time_f = robust_time(example["initial_execution_time"])
        except Exception as e:
            print(f"[warn] labeling failed for {example.get('program_name')}: "
                  f"bad initial_execution_time: {e}", file=sys.stderr)
            continue

        matched_this_program = False

        for sched in example["schedules_list"]:
            schedules_seen += 1
            transformations_list = sched.get("transformations_list", [])

            if not classify_schedule(transformations_list, args.transform, allow_prefixes):
                continue

            try:
                sched_time = robust_time(sched["execution_times"])
                if sched_time <= 0 or initial_time_f <= 0:
                    continue
                log_speedup = math.log(initial_time_f / sched_time)
            except Exception as e:
                print(f"[warn] labeling failed for {example.get('program_name')}: {e}",
                      file=sys.stderr)
                continue

            target_entry = next(
                t for t in transformations_list if t["type"] == args.transform
            )
            row = dict(prog_features)
            row["program_name"] = example["program_name"]
            row["target_loop_levels"] = str(target_entry.get("loop_levels", []))
            row["target_parameters"] = str(target_entry.get("parameters", []))
            row["log_speedup"] = f"{log_speedup:.6f}"
            rows.append(row)
            schedules_matched += 1
            matched_this_program = True

        if matched_this_program:
            programs_with_match += 1

        if programs_scanned % args.checkpoint_every == 0:
            flush_rows()
            pbar.set_postfix({
                "matched_progs": programs_with_match,
                "matched_scheds": schedules_matched,
            })

    flush_rows()

    if csv_file is None:
        print(f"\nNo matching schedules found for transform='{args.transform}' "
              f"with allow_prefix={allow_prefixes}. Try adding allow-prefix "
              f"'interchange', or check that this transform type appears at all "
              f"in this config.", file=sys.stderr)
        sys.exit(1)

    csv_file.close()

    print(f"\nScanned {programs_scanned} programs, {schedules_seen} total schedules.")
    print(f"Matched {schedules_matched} schedules across {programs_with_match} programs "
          f"for transform='{args.transform}'.")
    print(f"Wrote results incrementally to {out_path}")


if __name__ == "__main__":
    main()