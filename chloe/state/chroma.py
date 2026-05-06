from __future__ import annotations

import os
from pathlib import Path

import chromadb

_client: chromadb.Client | None = None


def get_client() -> chromadb.Client:
    global _client
    if _client is None:
        chroma_path = os.environ.get("CHROMA_PATH")
        if chroma_path:
            _client = chromadb.PersistentClient(path=chroma_path)
        else:
            _client = chromadb.EphemeralClient()
    return _client


def get_collection(name: str = "memories_v2") -> chromadb.Collection:
    return get_client().get_or_create_collection(name)


def reset_client() -> None:
    global _client
    _client = None
