import os
import json
import math
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional

def extract_enriched_program_features(
    data: Dict[str, Any],
    max_depth: int = 5
) -> Dict[str, float]:
    """
    Extracts Rank 1 (Stride/Cache), Rank 2 (Invariant Pooling), and Rank 3 (Disjointness/Aliasing)
    features from a program annotation dictionary.
    """
    features = {
        # Base ops
        'op_add': 0.0, 'op_sub': 0.0, 'op_mul': 0.0, 'op_div': 0.0, 'op_other': 0.0,
        'access_matrices_count': 0.0,
        
        # Rank 1: Memory Stride & Cache Lines
        'min_stride_bytes': 0.0,
        'max_stride_bytes': 0.0,
        'has_unit_stride': 0.0,
        'has_stride_zero': 0.0,
        'has_strided_hazard': 0.0,
        'avg_stride_bytes': 0.0,
        
        # Rank 2: Halide-Style Invariant Loop Pooling
        'innermost_extent': 1.0,
        'outermost_extent': 1.0,
        'inner_two_extent_product': 1.0,
        'geometric_mean_extent': 1.0,
        'extent_ratio_inner_outer': 1.0,
        
        # Rank 3: Buffer Disjointness & Aliasing Markers
        'has_shared_read_write_buffers': 0.0,
        'num_distinct_buffers': 1.0,
        'is_in_place_reduction': 0.0
    }
    
    # 1. Fixed extent slots for backward compatibility
    for i in range(max_depth):
        features[f'loop_extent_{i}'] = 1.0

    if not data:
        return features

    try:
        # -------------------------------------------------------------
        # RANK 2: Loop Iterators & Invariant Pooling Extents
        # -------------------------------------------------------------
        iters = data.get('iterators', {})
        extents = []
        if isinstance(iters, dict):
            # Sort iterators by numeric index if possible (i0, i1, ...)
            sorted_iter_keys = sorted(
                iters.keys(),
                key=lambda k: int(''.join(filter(str.isdigit, k))) if any(c.isdigit() for c in k) else 0
            )
            for idx, k in enumerate(sorted_iter_keys):
                v = iters[k]
                if isinstance(v, dict):
                    try:
                        ub = int(float(v.get('upper_bound', 1)))
                        lb = int(float(v.get('lower_bound', 0)))
                        ext = max(1, ub - lb)
                    except (ValueError, TypeError):
                        ext = 1
                    extents.append(ext)
                    if idx < max_depth:
                        features[f'loop_extent_{idx}'] = float(ext)

        if len(extents) > 0:
            features['innermost_extent'] = float(extents[-1])
            features['outermost_extent'] = float(extents[0])
            features['inner_two_extent_product'] = float(extents[-1] * extents[-2]) if len(extents) >= 2 else float(extents[-1])
            features['geometric_mean_extent'] = float(np.prod(np.array(extents, dtype=np.float64)) ** (1.0 / len(extents)))
            features['extent_ratio_inner_outer'] = float(extents[-1] / max(1, extents[0]))

        # -------------------------------------------------------------
        # RANK 1 & RANK 3: Computations, Access Matrices, Strides & Buffers
        # -------------------------------------------------------------
        comps = data.get('computations', {})
        write_buf_ids = set()
        read_buf_ids = set()
        strides = []
        
        for cname, cval in comps.items():
            if not isinstance(cval, dict):
                continue
            
            # DataType element size (8 bytes for float64/int64, 4 bytes for float32/int32)
            dtype_str = str(cval.get('data_type', 'float32'))
            elem_size = 8 if ('64' in dtype_str or 'double' in dtype_str) else 4
            
            w_buf = cval.get('write_buffer_id')
            if w_buf is not None:
                write_buf_ids.add(w_buf)
                
            is_red = bool(cval.get('comp_is_reduction', False))
            if is_red:
                features['is_in_place_reduction'] = 1.0

            # Access matrix stride extraction
            accs = cval.get('accesses', [])
            if isinstance(accs, list):
                for a in accs:
                    if not isinstance(a, dict):
                        continue
                    r_buf = a.get('buffer_id')
                    if r_buf is not None:
                        read_buf_ids.add(r_buf)
                        
                    amat = a.get('access_matrix', [])
                    if amat and isinstance(amat, list) and len(amat) > 0:
                        last_row = amat[-1]
                        if len(last_row) >= 2:
                            # Column [-2] corresponds to the innermost loop iterator
                            inner_coeff = abs(float(last_row[-2]))
                            stride_b = inner_coeff * elem_size
                            strides.append(stride_b)

        if len(strides) > 0:
            features['min_stride_bytes'] = float(min(strides))
            features['max_stride_bytes'] = float(max(strides))
            features['avg_stride_bytes'] = float(np.mean(strides))
            features['has_unit_stride'] = 1.0 if any(s in (4.0, 8.0) for s in strides) else 0.0
            features['has_stride_zero'] = 1.0 if any(s == 0.0 for s in strides) else 0.0
            features['has_strided_hazard'] = 1.0 if any(s > 64.0 for s in strides) else 0.0

        # Rank 3 Buffer Disjointness
        if len(write_buf_ids.intersection(read_buf_ids)) > 0:
            features['has_shared_read_write_buffers'] = 1.0
            
        all_bufs = write_buf_ids.union(read_buf_ids)
        features['num_distinct_buffers'] = float(max(1, len(all_bufs)))

        # Expression tree op traversal
        def traverse(node):
            if isinstance(node, dict):
                expr_type = str(node.get('expr_type', ''))
                if 'add' in expr_type:
                    features['op_add'] += 1.0
                elif 'sub' in expr_type:
                    features['op_sub'] += 1.0
                elif 'mul' in expr_type:
                    features['op_mul'] += 1.0
                elif 'div' in expr_type:
                    features['op_div'] += 1.0
                elif expr_type and expr_type != 'access' and expr_type != 'val':
                    features['op_other'] += 1.0

                for k, v in node.items():
                    if k == 'accesses' and isinstance(v, list):
                        features['access_matrices_count'] += len(v)
                    traverse(v)
            elif isinstance(node, list):
                for item in node:
                    traverse(item)

        traverse(comps)

    except Exception:
        pass

    return features
