"""Lädt das fastembed-Embedding-Modell zur BUILD-Zeit in den Image-Cache.

Grund: fastembed 0.8.0 lädt das ONNX-Modell zur Laufzeit über hf-xet; im Cluster
schlug das fehl (model.onnx landete nie → Index leer, Incident). Stattdessen wird
das Modell hier beim ``docker build`` heruntergeladen und ins Image gebacken; zur
Laufzeit ist kein Download mehr nötig.

- ``HF_HUB_DISABLE_XET`` erzwingt den klassischen HuggingFace-Download (kein Xet).
- Ein Test-Embed verifiziert, dass das Modell wirklich LÄDT und die erwartete
  Dimension liefert – schlägt das fehl, bricht der Build ab (fail-safe).
"""
import os

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from fastembed import TextEmbedding  # noqa: E402 – nach Env-Setup importieren

CACHE = os.environ.get("EMBEDDING_CACHE_DIR", "/opt/models")
MODEL = os.environ.get("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
EXPECTED_DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))

model = TextEmbedding(model_name=MODEL, cache_dir=CACHE)
vector = next(iter(model.embed(["test"])))
dim = len(vector)
assert dim == EXPECTED_DIM, f"unerwartete Embedding-Dimension: {dim} (erwartet {EXPECTED_DIM})"
print(f"Modell '{MODEL}' gebacken nach {CACHE}, Dimension {dim}")
