#!/usr/bin/env python3
"""
dual_ensemble/tree_ast_encoder.py

Extracts 180-dimensional recursive structural AST + Loop Nest embeddings
from the trained LOOPerCostModel (Tree-LSTM / GNN).
Replaces the flat 1D sequence LSTM tokenizer.
"""

import os
import sys
import torch
import torch.nn as nn
import numpy as np
from typing import Tuple, Any, List

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
looper_model_pkg = os.path.join(parent_dir, 'looper_cost_model')
if looper_model_pkg not in sys.path:
    sys.path.insert(0, looper_model_pkg)

from looper_cost_model.model import LOOPerCostModel

class TreeASTEncoder:
    def __init__(
        self,
        weights_path: str = os.path.join(parent_dir, "runs", "pact25", "weights", "best_looper_cost_model.pt"),
        input_size: int = 846,
        device: str = "cuda" if torch.cuda.is_available() else "cpu"
    ):
        self.device = torch.device(device)
        self.model = LOOPerCostModel(
            input_size=input_size,
            comp_embed_layer_sizes=[600, 350, 200, 180],
            drops=[0.0, 0.0, 0.0, 0.0],
            device=self.device
        ).to(self.device)

        if os.path.exists(weights_path):
            print(f"[TreeASTEncoder] Loading trained Tree-LSTM weights from {weights_path}...")
            state_dict = torch.load(weights_path, map_location=self.device)
            self.model.load_state_dict(state_dict, strict=False)
        else:
            print(f"[TreeASTEncoder] Warning: Weights not found at {weights_path}, using randomly initialized model.")

        self.model.eval()
        for p in self.model.parameters():
            p.requires_grad = False

    @torch.no_grad()
    def get_embedding_from_tensors(self, tree_tensors: Tuple[Any, ...]) -> np.ndarray:
        """
        Extracts 180-dim structural embedding from pre-batched tree tensors.
        Returns: (batch_size, 180) float32 numpy array
        """
        tree, comps_first_part, comps_vectors, comps_third_part, loops_tensor, comps_expr_tree = tree_tensors
        device = self.device

        # 1. Expression embedding
        batch_size, num_comps, len_sequence, len_vector = comps_expr_tree.shape
        x = comps_expr_tree.view(batch_size * num_comps, len_sequence, len_vector).to(device)
        _, (expr_embedding, _) = self.model.exprs_embed(x)
        expr_embedding = expr_embedding.permute(1, 0, 2).reshape(batch_size * num_comps, -1)

        # 2. Static computation features
        first_part = comps_first_part.to(device).view(batch_size * num_comps, -1)
        third_part = comps_third_part.to(device).view(batch_size * num_comps, -1)

        # 3. Affine-transformation embedding
        vectors = self.model.encode_vectors(comps_vectors.to(device))
        _, (prog_embedding, _) = self.model.transformation_vectors_embed(vectors)
        prog_embedding = prog_embedding.permute(1, 0, 2).reshape(batch_size * num_comps, -1)

        # 4. Per-computation embedding
        x = torch.cat((first_part, prog_embedding, third_part, expr_embedding), dim=1)
        x = x.view(batch_size, num_comps, -1)
        for layer, norm, dropout in zip(self.model.comp_embedding_layers, self.model.comp_embedding_layer_norms, self.model.comp_embedding_dropouts):
            x = dropout(self.model.ELU(norm(layer(x))))
        comps_embeddings = x

        # 5. Recursive tree aggregation
        loops_tensor = loops_tensor.to(device)
        roots_list = [
            self.model.get_hidden_state(root, comps_embeddings, loops_tensor)
            for root in tree["roots"]
        ]
        roots_tensor = torch.cat(roots_list, 1)
        _, (roots_h_n, _) = self.model.roots_lstm(roots_tensor)
        x = roots_h_n.permute(1, 0, 2)

        # 6. Dense regression layers (extract 180D structural fingerprint)
        for layer, norm, dropout in zip(self.model.regression_layers, self.model.regression_layer_norms, self.model.regression_dropouts):
            x = dropout(self.model.ELU(norm(layer(x))))

        emb = x.squeeze(1).cpu().numpy().astype(np.float32)
        return emb

if __name__ == "__main__":
    encoder = TreeASTEncoder()
    print("TreeASTEncoder initialized successfully!")
