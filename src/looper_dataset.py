import torch
from torch.utils.data import Dataset, DataLoader
import numpy as np
from sklearn.preprocessing import StandardScaler
import os
import sys

# Ensure looper_cost_model is safely in the path using absolute resolution
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)
looper_model_dir = os.path.join(current_dir, 'looper_cost_model')
if looper_model_dir not in sys.path:
    sys.path.append(looper_model_dir)

from looper_cost_model.data.dataset import extract_features, build_batches

class LOOPerDataset(Dataset):
    def __init__(self, jsonl_path, scaler=None, max_batch_size=256, nb_processes=4, is_train=True, max_programs=None):
        print(f"Loading dataset from {jsonl_path}...")
        
        base_name = os.path.basename(jsonl_path).replace(".jsonl.gz", "").replace(".jsonl", "").replace(".json", "")
        if max_programs is not None:
            base_name += f"_p{max_programs}"
        cache_folder = os.path.join(current_dir, "cache_repr", base_name)
        os.makedirs(cache_folder, exist_ok=True)
        
        extract_features(jsonl_path, cache_dir=cache_folder, nb_workers=nb_processes, max_programs=max_programs)
        batches = build_batches(cache_dir=cache_folder, batch_size=max_batch_size, seed=42 if is_train else 0)
        
        self.X = batches.X
        self.scaler = scaler
        
        # Clean positive speedup tensors for official MAPE objective
        self.Y = [y_batch.clone().detach().float() if isinstance(y_batch, torch.Tensor) else torch.tensor(y_batch, dtype=torch.float32) for y_batch in batches.Y]
            
    def __len__(self):
        return len(self.X)
        
    def __getitem__(self, idx):
        return self.X[idx], self.Y[idx]
        
def custom_collate_fn(batch):
    return batch[0][0], batch[0][1]

def get_dataloader(jsonl_path, scaler=None, batch_size=1, is_train=True, nb_processes=4, max_batch_size=256, max_programs=None):
    dataset = LOOPerDataset(jsonl_path, scaler=scaler, max_batch_size=max_batch_size, is_train=is_train, nb_processes=nb_processes, max_programs=max_programs)
    dataloader = DataLoader(dataset, batch_size=1, collate_fn=custom_collate_fn, shuffle=is_train)
    return dataloader, dataset.scaler

if __name__ == "__main__":
    print("Testing LOOPerDataset with mini dataset...")
    test_path = os.path.join(looper_model_dir, ".testdata", "mini.jsonl.gz")
    loader, scaler = get_dataloader(test_path, is_train=True, nb_processes=2, max_batch_size=32)
    for X, Y in loader:
        print(f"X tuple elements: {len(X)}")
        print(f"comps first_part shape: {X[1].shape}")
        print(f"comps vectors shape: {X[2].shape}")
        print(f"comps third_part shape: {X[3].shape}")
        print(f"loops shape: {X[4].shape}")
        print(f"expr shape: {X[5].shape}")
        print(f"Y scaled log_speedup shape: {Y.shape}")
        break
    print("LOOPerDataset verified successfully!")
