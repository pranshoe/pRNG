import gzip
import json
from typing import Generator, Dict, Any

def stream_jsonl_gz(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """
    Streams a gzipped JSONL file line-by-line without extracting to disk.
    Parses JSON per line and yields the dictionary immediately.
    """
    with gzip.open(file_path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            yield item
            del item
            del line
