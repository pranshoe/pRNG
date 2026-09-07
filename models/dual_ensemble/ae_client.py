import os
import json
import hashlib
import asyncio
import requests
from typing import Dict, Any, List, Optional
import numpy as np
from dual_ensemble.config import AE_API_URL, AE_API_KEY, AE_CACHE_FILE, LSTM_EMBED_DIM

class TiramisuAEClient:
    def __init__(
        self,
        api_url: str = AE_API_URL,
        api_key: str = AE_API_KEY,
        cache_file: str = AE_CACHE_FILE,
        embed_dim: int = LSTM_EMBED_DIM
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.cache_file = cache_file
        self.embed_dim = embed_dim
        self.cache: Dict[str, List[float]] = self._load_cache()
        self.is_offline = not self._check_server_online()

    def _check_server_online(self) -> bool:
        if not self.api_url or "localhost" in self.api_url or "127.0.0.1" in self.api_url:
            try:
                resp = requests.get(self.api_url.rsplit("/", 1)[0] + "/health", timeout=0.5)
                return resp.status_code == 200
            except Exception:
                return False
        return True

    def _load_cache(self) -> Dict[str, List[float]]:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self) -> None:
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f)
        except Exception:
            pass

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key
        return headers

    def _get_cache_key(self, payload: Dict[str, Any]) -> str:
        serialized = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _fallback_embedding(self, cache_key: str) -> np.ndarray:
        seed = int(cache_key[:8], 16)
        rng = np.random.RandomState(seed)
        return rng.normal(0, 1, size=self.embed_dim).astype(np.float32)

    def get_embedding_sync(self, program_payload: Dict[str, Any]) -> np.ndarray:
        cache_key = self._get_cache_key(program_payload)
        if cache_key in self.cache:
            return np.array(self.cache[cache_key], dtype=np.float32)

        if self.is_offline:
            emb = self._fallback_embedding(cache_key)
            self.cache[cache_key] = emb.tolist()
            return emb

        try:
            resp = requests.post(
                self.api_url,
                json=program_payload,
                headers=self._get_headers(),
                timeout=2
            )
            resp.raise_for_status()
            embedding = resp.json()["embedding"]
        except Exception:
            self.is_offline = True
            embedding = self._fallback_embedding(cache_key).tolist()

        self.cache[cache_key] = embedding
        return np.array(embedding, dtype=np.float32)

    async def get_embedding_async(self, session, program_payload: Dict[str, Any]) -> np.ndarray:
        cache_key = self._get_cache_key(program_payload)
        if cache_key in self.cache:
            return np.array(self.cache[cache_key], dtype=np.float32)

        if self.is_offline:
            emb = self._fallback_embedding(cache_key)
            self.cache[cache_key] = emb.tolist()
            return emb

        try:
            async with session.post(
                self.api_url,
                json=program_payload,
                headers=self._get_headers(),
                timeout=2
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                embedding = data["embedding"]
        except Exception:
            self.is_offline = True
            embedding = self._fallback_embedding(cache_key).tolist()

        self.cache[cache_key] = embedding
        return np.array(embedding, dtype=np.float32)

    async def get_batch_embeddings_async(self, payloads: List[Dict[str, Any]]) -> np.ndarray:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            tasks = [self.get_embedding_async(session, p) for p in payloads]
            results = await asyncio.gather(*tasks)
            self._save_cache()
            return np.array(results, dtype=np.float32)
