import json
import os
from typing import Dict, Any, List, Union
import numpy as np
import torch
import torch.nn as nn
from dual_ensemble.config import VOCAB_PATH, LOOPER_WEIGHTS_PATH, DEVICE, LSTM_INPUT_DIM, LSTM_HIDDEN_DIM, LSTM_NUM_LAYERS, LSTM_EMBED_DIM

class ExpressionLSTM(nn.Module):
    def __init__(self, vocab_size: int, embed_dim: int = LSTM_EMBED_DIM, hidden_dim: int = LSTM_HIDDEN_DIM, num_layers: int = LSTM_NUM_LAYERS):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.lstm = nn.LSTM(
            input_size=embed_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass returning final hidden state hn[-1].
        x: (batch_size, seq_len)
        Returns: (batch_size, hidden_dim)
        """
        embeds = self.embedding(x)
        _, (hn, _) = self.lstm(embeds)
        return hn[-1]

def flatten_expression_tree(node: Union[Dict[str, Any], List, str, int, float]) -> List[str]:
    """
    Recursively flattens an expression_representation / AST tree into a sequence of node tokens.
    """
    tokens = []
    if isinstance(node, dict):
        if "type" in node:
            tokens.append(str(node["type"]))
        elif "op" in node:
            tokens.append(str(node["op"]))
        elif "name" in node:
            tokens.append(str(node["name"]))
            
        for k, v in node.items():
            if k not in ("type", "op", "name"):
                tokens.extend(flatten_expression_tree(v))
    elif isinstance(node, list):
        for item in node:
            tokens.extend(flatten_expression_tree(item))
    elif node is not None:
        tokens.append(str(node))
    return tokens

class ExprLSTMEncoder:
    def __init__(
        self,
        vocab_path: str = VOCAB_PATH,
        weights_path: str = LOOPER_WEIGHTS_PATH,
        device: str = DEVICE
    ):
        self.device = device
        self.vocab = self._load_vocab(vocab_path)
        vocab_size = max(len(self.vocab), 1000)
        self.model = ExpressionLSTM(vocab_size=vocab_size).to(self.device)

        if os.path.exists(weights_path):
            state_dict = torch.load(weights_path, map_location=self.device)
            self.model.load_state_dict(state_dict)

        self.model.eval()
        for param in self.model.parameters():
            param.requires_grad = False

    def _load_vocab(self, vocab_path: str) -> Dict[str, int]:
        if os.path.exists(vocab_path):
            with open(vocab_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"<pad>": 0, "<unk>": 1}

    def tokenize(self, expr_tree: Any, max_len: int = 256) -> List[int]:
        tokens = flatten_expression_tree(expr_tree)
        token_ids = [self.vocab.get(tok, self.vocab.get("<unk>", 1)) for tok in tokens]
        if len(token_ids) == 0:
            token_ids = [0]
        if len(token_ids) < max_len:
            token_ids = token_ids + [self.vocab.get("<pad>", 0)] * (max_len - len(token_ids))
        else:
            token_ids = token_ids[:max_len]
        return token_ids

    @torch.no_grad()
    def get_embedding(self, expr_tree: Any) -> np.ndarray:
        token_ids = self.tokenize(expr_tree)
        tensor_in = torch.tensor([token_ids], dtype=torch.long, device=self.device)
        emb = self.model(tensor_in)
        return emb.squeeze(0).cpu().numpy().astype(np.float32)

    @torch.no_grad()
    def get_batch_embeddings(self, expr_trees: List[Any]) -> np.ndarray:
        token_batch = [self.tokenize(t) for t in expr_trees]
        tensor_in = torch.tensor(token_batch, dtype=torch.long, device=self.device)
        embs = self.model(tensor_in)
        return embs.cpu().numpy().astype(np.float32)
