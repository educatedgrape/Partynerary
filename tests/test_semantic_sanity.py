"""Semantic sanity — the test that catches placeholder embeddings.

A hash-based placeholder artifact once shipped carrying zero semantic
relationship: `beach` cosined closer to `temples` than to `coast`, and the
entire product was built on that noise. Every assertion here is a RELATIVE
ORDERING, not a threshold, and must pass on the committed artifact.
"""

import unittest

from src.discovery.vectors import cosine, embed, is_zero, load_tokens


# Words people type that appear in no city text. None of them may embed to
# the zero vector.
EXPANSION_VOCAB = [
    "chill", "unwind", "foodie", "kpop", "nightlife", "diving", "surf",
    "noodles", "hiking", "shrine", "ramen", "clubbing", "snorkeling",
    "trekking", "honeymoon", "cocktails", "seoul",
]


class SemanticSanityTest(unittest.TestCase):
    """Relative-ordering assertions against the committed vectors.json."""

    @classmethod
    def setUpClass(cls):
        cls.tokens, cls.dim = load_tokens()

    def _sim(self, a, b):
        va = embed(a, self.tokens, self.dim)
        vb = embed(b, self.tokens, self.dim)
        self.assertFalse(is_zero(va), "%r embedded to zero" % a)
        self.assertFalse(is_zero(vb), "%r embedded to zero" % b)
        return cosine(va, vb)

    def test_artifact_declares_real_model(self):
        """A placeholder artifact once shipped under 'hash-fallback'. The
        runtime now refuses it at load — verify the committed artifact
        declares the real model id."""
        import json
        import pathlib
        path = (pathlib.Path(__file__).resolve().parent.parent
                / "data" / "vectors.json")
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual(raw.get("model"),
                         "sentence-transformers/all-MiniLM-L6-v2")

    def test_beach_prefers_coast_over_temples(self):
        self.assertGreater(
            self._sim("beach", "coast"),
            self._sim("beach", "temples"),
            "beach must be closer to coast than to temples")

    def test_ramen_prefers_noodles_over_volcano(self):
        self.assertGreater(
            self._sim("ramen", "noodles"),
            self._sim("ramen", "volcano"),
            "ramen must be closer to noodles than to volcano")

    def test_kpop_prefers_seoul_over_jakarta(self):
        self.assertGreater(
            self._sim("kpop", "seoul"),
            self._sim("kpop", "jakarta"),
            "kpop must be closer to seoul than to jakarta")

    def test_expansion_vocab_never_embeds_to_zero(self):
        """Query-side words missing from every token table silently degrade
        retrieval to keyword matching. Every one must embed."""
        zero_words = []
        for word in EXPANSION_VOCAB:
            self.assertIn(word, self.tokens,
                          "%r missing from the token table" % word)
            if is_zero(self.tokens[word]):
                zero_words.append(word)
        self.assertEqual(zero_words, [],
                         "words embedding to the zero vector: %r" % zero_words)


if __name__ == "__main__":
    unittest.main()
