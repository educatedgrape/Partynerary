#!/usr/bin/env python3
"""Build the vector artifact from the city dataset.

Generates data/vectors.json containing:
  - model: the embedding model id
  - dim: vector dimension
  - built: build date
  - tokens: {token: unit-normalized vector} for every token in the dataset
            plus a curated expansion list of travel vocabulary

Usage:
    python tools/build_vectors.py --dataset data/cities.json --out data/vectors.json

Requires sentence-transformers (install into a build-time virtualenv):
    python -m venv .venv-build
    .venv-build/Scripts/pip install -r tools/requirements-build.txt

There is NO fallback. If the model cannot be loaded, this build fails
loudly: a hash-based placeholder path once shipped a plausible-looking
artifact carrying zero semantic relationship.

BUILD TIME ONLY. Never imported from src/. The committed data/vectors.json
is the runtime artifact; the build tool is disposable.
"""

import argparse
import json
import math
import pathlib
import re
import sys
from datetime import date


# Curated expansion list — the words people type that appear in NO city text.
# Without this the query embedder has nothing to look up and silently degrades
# to keyword matching. Every word here must embed to a non-zero vector.
EXPANSION_TOKENS = [
    # vibes people type
    "chill", "chilled", "unwind", "buzzy", "buzzing", "foodie", "hungover",
    "relax", "relaxing", "adventure", "adventurous", "backpacking",
    "luxury", "budget", "cheap", "romantic", "honeymoon", "family",
    "solo", "group", "friends", "couple",
    # nights
    "party", "partying", "clubbing", "clubs", "nightlife", "drinking",
    "bars", "wine", "beer", "cocktails", "karaoke",
    # activities
    "hiking", "trekking", "climbing", "diving", "snorkeling", "surf",
    "surfing", "skiing", "snowboard", "swimming", "yoga", "camping",
    # culture
    "culture", "history", "museum", "museums", "gallery", "galleries",
    "architecture", "temple", "temples", "shrine", "shrines", "palace",
    "heritage", "old", "ancient",
    # nature
    "nature", "wildlife", "safari", "jungle", "forest", "mountain",
    "mountains", "volcano", "waterfall", "lake", "countryside",
    # coast
    "beach", "beaches", "ocean", "sea", "coast", "island", "islands",
    "tropical", "sunny",
    # food
    "food", "street", "local", "authentic", "organic", "ramen", "noodles",
    "sushi", "seafood", "bbq", "hotpot", "dim", "sum", "cafe", "cafes",
    "coffee", "bakery",
    # pop culture
    "kpop", "kdrama", "anime", "manga",
    # shopping / wellness / events
    "shopping", "market", "markets", "mall", "boutique",
    "spa", "wellness", "meditation", "retreat",
    "festival", "music", "concert", "art",
    # weather
    "hot", "cold", "warm", "rainy", "dry", "snow",
]

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384


def _tokenize(text):
    """Split text into lowercase tokens."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _extract_tokens(cities):
    """Extract every unique token from the city dataset.

    Covers every textual field — keywords, vibes, phrases, aliases — plus
    city names and country names, plus the expansion vocabulary.
    """
    tokens = set()
    for city in cities:
        for text in city.get("keywords", []):
            tokens.update(_tokenize(text))
        for text in city.get("vibes", []):
            tokens.update(_tokenize(text))
        for text in city.get("phrases", []):
            tokens.update(_tokenize(text))
        for text in city.get("aliases", []):
            tokens.update(_tokenize(text))
        tokens.update(_tokenize(city.get("cityName", "")))
        tokens.update(_tokenize(city.get("country", "")))
    # Add expansion tokens
    tokens.update(EXPANSION_TOKENS)
    return sorted(tokens)


def _embed_with_model(model_id, tokens_list):
    """Embed using sentence-transformers. Raises on failure — no fallback."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        sys.exit(
            "sentence-transformers is not installed. Build refuses to "
            "produce hash placeholders. Install into a build-time "
            "virtualenv:\n"
            "    python -m venv .venv-build\n"
            "    .venv-build/Scripts/pip install -r tools/requirements-build.txt\n"
            "underlying error: %s" % exc)
    model = SentenceTransformer(model_id)
    embeddings = model.encode(tokens_list, show_progress_bar=True,
                              normalize_embeddings=True)
    return {
        token: [round(float(x), 6) for x in vec]
        for token, vec in zip(tokens_list, embeddings)
    }


def _normalize(vec):
    """Normalize a vector to unit length."""
    mag = math.sqrt(sum(v * v for v in vec))
    if mag > 0:
        return [v / mag for v in vec]
    return vec


def build(dataset_path, out_path, model_id=MODEL_ID):
    """Build the vector artifact with the real embedding model.

    Raises/exits if the model is unavailable — there is no stand-in path.
    """
    # Load dataset
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    cities = data.get("cities", data) if isinstance(data, dict) else data

    # Extract all tokens
    all_tokens = _extract_tokens(cities)
    print("Tokens to embed: %d" % len(all_tokens))

    # Embed with the real model — fail loudly if unavailable
    token_vectors = _embed_with_model(model_id, all_tokens)

    # Build output
    result = {
        "model": model_id,
        "dim": DIM,
        "built": str(date.today()),
        "tokens": token_vectors,
    }

    # Verify all unit-normalized
    for token, vec in result["tokens"].items():
        assert len(vec) == DIM, "Vector for %r has dim %d, expected %d" % (
            token, len(vec), DIM)
        mag = math.sqrt(sum(v * v for v in vec))
        assert abs(mag - 1.0) < 1e-4, "Vector for %r not unit length: %.6f" % (
            token, mag)

    # Write
    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f)

    print("Generated %s" % out)
    print("  Model:  %s" % model_id)
    print("  Dim:    %d" % DIM)
    print("  Tokens: %d" % len(result["tokens"]))
    return result


def main():
    parser = argparse.ArgumentParser(description="Build vector artifact")
    parser.add_argument("--dataset", default="data/cities.json",
                        help="Path to cities.json")
    parser.add_argument("--out", default="data/vectors.json",
                        help="Output path for vectors.json")
    parser.add_argument("--model", default=MODEL_ID,
                        help="Embedding model id")
    args = parser.parse_args()

    build(args.dataset, args.out, args.model)


if __name__ == "__main__":
    main()
