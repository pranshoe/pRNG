#!/usr/bin/env python3
"""
inspect_looperset.py

Step 0 before building any feature extractor: pull a handful of real
LOOPerSet rows and print their actual structure. Field names below in
program_annotation / schedules_list are UNVERIFIED guesses based on the
paper's description -- do not build an extractor until you've run this
and confirmed the real keys.

Requires: pip install datasets
"""

import json
from datasets import load_dataset

# LOOPerSet requires an explicit config name. Available configs:
#   full, full_compact, pact25_split, pact25_split_compact
# Starting with full_compact since it's likely the smaller/leaner variant --
# switch to "full" if you need every field.
CONFIG_NAME = "full_compact"

ds = load_dataset("Mascinissa/LOOPerSet", CONFIG_NAME, split="train", streaming=True)

print("=== First 3 raw examples ===\n")
for i, example in enumerate(ds):
    if i >= 3:
        break
    print(f"--- Example {i} ---")
    print(json.dumps(example, indent=2)[:3000])  # truncate huge fields
    print()

print("=== Top-level keys of example 0 ===")
first = next(iter(ds))
print(list(first.keys()))

# If program_annotation and schedules_list exist, drill in one level
if "program_annotation" in first:
    print("\n=== program_annotation keys ===")
    print(list(first["program_annotation"].keys())
          if isinstance(first["program_annotation"], dict) else type(first["program_annotation"]))

if "schedules_list" in first:
    print("\n=== schedules_list[0] FULL content ===")
    sched = first["schedules_list"]
    if isinstance(sched, list) and sched:
        print(json.dumps(sched[0], indent=2))
    print("\n=== transformations_list values across first 5 schedules ===")
    for s in sched[:5]:
        print(s.get("transformations_list"))
    print("\n=== execution_times values across first 5 schedules ===")
    for s in sched[:5]:
        print(s.get("execution_times"))