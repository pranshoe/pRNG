"""
looperset_features.py

Extracts a scalar feature row from one LOOPerSet program's
`program_annotation`, mirroring the role your LoopFeatureExtractor LLVM
pass played for the vectorization MVP -- but for the tiling/fusion/etc.
models.

Confirmed against real LOOPerSet (full_compact) samples:
  program_annotation = {
    memory_size, iterators, buffers, computations
  }
"""


def _safe_bound(value) -> float | None:
    """
    Parse a loop bound that should be numeric. Some LOOPerSet loops have
    parametric (triangular) bounds -- e.g. an inner loop's lower bound
    given as "i1+1" or "(i2 - 1)+1" rather than a plain integer, when the
    bound depends on an outer iterator (common in triangular/stencil loop
    nests like Cholesky or LU decomposition). Returns None when the bound
    isn't a plain number, so callers can fall back gracefully instead of
    crashing on "could not convert string to float: 'i2'".
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _loop_depth(iterators: dict) -> int:
    """Max nesting depth across all iterator chains."""
    depth = 0
    for it in iterators.values():
        d, cur = 1, it
        while cur.get("parent_iterator"):
            d += 1
            cur = iterators[cur["parent_iterator"]]
        depth = max(depth, d)
    return depth


def _total_trip_count(iterators: dict) -> tuple[float, bool]:
    """
    Product of (upper_bound - lower_bound) across the deepest chain.
    Most bounds are exact compile-time constants (no confidence field
    needed, unlike the LLVM/SCEV case) -- but some loops have parametric
    bounds that depend on an outer iterator (e.g. "i1+1"), which can't be
    reduced to a single number without knowing the outer iterator's value.

    Returns (total_trip_count, has_parametric_bounds). When a bound can't
    be parsed, that level is skipped from the product (treated as extent
    1, i.e. multiplicatively neutral) rather than crashing, and the flag
    is set so this is visible as its own feature rather than silently
    producing a misleadingly-small trip count.
    """
    leaves = [it_id for it_id, it in iterators.items() if not it["child_iterators"]]
    if not leaves:
        return 0.0, False

    total = 1.0
    has_parametric = False
    cur_id = leaves[0]
    while cur_id is not None:
        it = iterators[cur_id]
        upper = _safe_bound(it["upper_bound"])
        lower = _safe_bound(it["lower_bound"])
        if upper is None or lower is None:
            has_parametric = True
        else:
            extent = upper - lower
            total *= max(extent, 1.0)
        cur_id = it["parent_iterator"]
    return total, has_parametric


def _max_access_offset(computations: dict) -> int:
    """
    Scan every access_matrix's constant column (last entry per row) for
    the largest absolute offset -- a proxy for dependency distance /
    stencil-like access patterns (e.g. A[i-1], A[i+1]).
    """
    max_offset = 0
    for comp in computations.values():
        for access in comp.get("accesses", []):
            matrix = access.get("access_matrix", [])
            for row in matrix:
                if row:
                    max_offset = max(max_offset, abs(row[-1]))
    return max_offset


def _count_reduction_computations(computations: dict) -> int:
    return sum(1 for c in computations.values() if c.get("comp_is_reduction"))


def extract_program_features(program_annotation: dict) -> dict:
    iterators = program_annotation["iterators"]
    computations = program_annotation["computations"]
    buffers_info = program_annotation["buffers"]["buffers_info"]

    total_accesses = sum(
        len(c.get("accesses", [])) for c in computations.values()
    )

    total_trip_count, has_parametric_bounds = _total_trip_count(iterators)

    return {
        "memory_size": float(program_annotation["memory_size"]),
        "loop_depth": _loop_depth(iterators),
        "total_trip_count": total_trip_count,
        "has_parametric_bounds": int(has_parametric_bounds),
        "num_computations": len(computations),
        "num_reduction_computations": _count_reduction_computations(computations),
        "num_buffers": len(buffers_info),
        "total_accesses": total_accesses,
        "max_access_offset": _max_access_offset(computations),
    }


if __name__ == "__main__":
    # Quick smoke test against one real example
    from datasets import load_dataset

    ds = load_dataset("Mascinissa/LOOPerSet", "full_compact", split="train", streaming=True)
    example = next(iter(ds))
    features = extract_program_features(example["program_annotation"])
    print(example["program_name"], "->", features)