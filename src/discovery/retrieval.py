"""Free text in, ranked city QUERIES out. The semantic layer.

    "beach and relax"  ->  Phuket, Bali, Koh Samui
    "kimchi and kpop"  ->  Seoul, Busan

WHY THIS IS NOT A MODEL DECIDING WHERE YOU GO
---------------------------------------------
  * It emits city QUERIES, never itineraries, prices, or dates. Atlas is asked
    afterwards, and Atlas's answer is what gets shown.
  * It never sees a price and never calls Atlas. It runs before the first call,
    so there is no fare in scope for it to leak or invent.
  * Embeddings are a static committed artifact. No model in the process, no
    network call, no per-run variation.

The worst a bad vector can do is cause the group to be shown flights to a city
they did not ask about - at real prices, with real seats, with the match reason
printed underneath. It cannot invent a fare or a route.
"""

import re
from dataclasses import dataclass, field

from src.discovery import dataset, vectors


WEIGHTS = {"dense": 0.70, "sparse": 0.30}

# Below this, a clause has not matched anything and must not be treated as a
# weak preference. Dense retrieval always returns *something*; this is what
# stops "something" becoming "a confident answer".
MIN_SIMILARITY = 0.35

NEGATORS = ("no ", "not ", "without ", "avoid ", "hate ", "dislike ",
            "rather not ", "anything but ", "except ")

_SPLIT = re.compile(r"[,;.!?]|\band\b|\bbut\b|\bthough\b|\bhowever\b|\bplus\b"
                    r"|\bwith\b|\balso\b")

# "anywhere but Bangkok" is one opinion, not two. `but` is a clause separator
# everywhere else, so splitting on it turns an exclusion into an endorsement:
# the trailing clause reads as a bare "bangkok" and the place being ruled OUT
# comes back top of the list. Rewrite the idiom BEFORE the split rather than
# teaching the splitter about context.
_EXCEPT = re.compile(r"\b(?:any|every|some)(?:where|thing|place)\s+"
                     r"(?:but|except|apart from|other than)\s+\S+")


@dataclass(frozen=True)
class Clause:
    """One clause of a member's preference text."""
    text: str
    negated: bool
    vector: list


@dataclass
class Match:
    """One city the group's own words point at, and why.

    Carries NO price, fare, amount, cost, total, or saving field. It runs before
    the first Atlas call; if it cannot hold a fare it cannot leak an invented
    one, and a test enforces the absence.
    """
    city_id: str
    city_name: str
    country: str
    vibe_score: float
    dense: float
    sparse: float
    named: int              # +1 named outright, -1 ruled out, 0 inferred
    matched: tuple = ()

    @property
    def why(self):
        """The denominator, in words. Never a score on its own."""
        parts = []
        if self.named > 0:
            parts.append("you named it")
        elif self.named < 0:
            parts.append("ruled out")

        if self.matched:
            parts.append("matches %s" % ", ".join(self.matched))

        if not parts:
            return "inferred from your description"
        return " · ".join(parts)

    def as_dict(self):
        """Dict representation — carries no price/fare/amount field."""
        return {
            "cityId": self.city_id,
            "cityName": self.city_name,
            "country": self.country,
            "vibeScore": round(self.vibe_score, 4),
            "dense": round(self.dense, 4),
            "sparse": round(self.sparse, 4),
            "named": self.named,
            "matched": list(self.matched),
            "why": self.why,
        }


def clauses(text):
    """Split, detect negation, embed.

    Negation is tagged BEFORE embedding, so a negated clause subtracts rather
    than adds.
    """
    tokens_table, dim = vectors.load_tokens()

    # Rewrite "anywhere/everywhere/somewhere but X" before splitting
    rewritten = _EXCEPT.sub(lambda m: "no " + m.group(0).split()[-1], text)

    raw_parts = [p.strip() for p in _SPLIT.split(rewritten) if p.strip()]

    result = []
    for part in raw_parts:
        lower = part.lower().strip()
        negated = any(lower.startswith(neg) for neg in NEGATORS)

        # For negated clauses, strip the negator for embedding so we embed the
        # entity being negated, not the negator word
        embed_text = lower
        if negated:
            for neg in sorted(NEGATORS, key=len, reverse=True):
                if lower.startswith(neg):
                    embed_text = lower[len(neg):].strip()
                    break

        vec = vectors.embed(embed_text, tokens_table, dim)
        result.append(Clause(text=lower, negated=negated, vector=vec))

    return result


def places_named(text):
    """{cityId: +1|-1} by exact alias, longest-match-first.

    Separate from similarity on purpose. Somebody who writes "kimchi" has told
    you where they want to go; that is not a similarity question, and forcing it
    through one lets a cheap beach outrank a named country.
    """
    index = dataset.alias_index()
    lower = text.lower()
    result = {}

    for alias, city_id in index:
        if alias not in lower:
            continue

        is_negated = False

        # Check "anywhere/everywhere/somewhere but|except X" — the full
        # phrase with the entity captured in the regex match
        for m in _EXCEPT.finditer(lower):
            phrase = m.group(0)
            # The entity is the last word(s) after the connector
            parts = re.split(
                r"\b(?:but|except|apart\s+from|other\s+than)\b", phrase)
            if len(parts) == 2:
                excluded = parts[1].strip()
                if alias == excluded or alias in excluded:
                    is_negated = True
                    break

        # Check direct negation: "no bangkok", "not bangkok", "avoid bangkok"
        if not is_negated:
            for neg in NEGATORS:
                pattern = neg.strip() + alias
                if pattern in lower:
                    is_negated = True
                    break

        # Check "... but X" where X is the alias and the preceding word
        # is a somewhere-type word (handles "anywhere but bangkok" even
        # when _EXCEPT didn't fire)
        if not is_negated:
            but_pattern = r"\b(?:where|thing|place)\s+but\s+" + re.escape(alias) + r"\b"
            if re.search(but_pattern, lower):
                is_negated = True

        if city_id not in result:
            result[city_id] = -1 if is_negated else 1

    return result


def unrecognised(text):
    """Tokens that produced no vector and named no place."""
    tokens_table, _ = vectors.load_tokens()
    words = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]

    # Tokens that have no vector
    unknown = [w for w in words if w not in tokens_table]

    # Also remove tokens that are part of a named alias
    named_aliases = set()
    index = dataset.alias_index()
    lower = text.lower()
    for alias, _ in index:
        if alias in lower:
            for w in re.split(r"[^a-z0-9]+", alias.lower()):
                if w:
                    named_aliases.add(w)

    # Stopwords to exclude from the unrecognised report
    stopwords = {"and", "but", "the", "a", "an", "to", "in", "on", "at",
                 "for", "of", "is", "it", "with", "also", "or", "no", "not",
                 "any", "every", "some", "where", "thing", "place", "but",
                 "except", "apart", "from", "other", "than", "however",
                 "though", "plus", "rather", "anything", "everything",
                 "somewhere", "anywhere", "everywhere", "avoid", "hate",
                 "dislike", "without", "like"}

    return [w for w in unknown if w not in named_aliases and w not in stopwords]


def _city_vectors(city):
    """Collect all text vectors for a city from its keywords, vibes, phrases.

    Returns list of (category, vector_list) pairs. Each category maps to
    its embedded vectors for max-pooling.
    """
    tokens_table, dim = vectors.load_tokens()

    result = {"keywords": [], "vibes": [], "phrases": []}

    for kw in city.keywords:
        vec = vectors.embed(kw, tokens_table, dim)
        if not vectors.is_zero(vec):
            result["keywords"].append(vec)

    for vibe in city.vibes:
        vec = vectors.embed(vibe, tokens_table, dim)
        if not vectors.is_zero(vec):
            result["vibes"].append(vec)

    for phrase in city.phrases:
        vec = vectors.embed(phrase, tokens_table, dim)
        if not vectors.is_zero(vec):
            result["phrases"].append(vec)

    return result


def _stem(token):
    """Light suffix stripping so 'beaches' matches the keyword 'beach'.

    Deliberately crude: this feeds the sparse overlap score only, where a
    false stem costs a fraction of a point but an unmatched plural blanks
    an entire preference. Sibilant plurals drop 'es' (beaches -> beach),
    plain plurals drop 's' (temples -> temple) so both land on the same
    stem as the dataset's singular keyword.
    """
    if token.endswith(("ches", "shes", "ses", "xes", "zes")):
        return token[:-2]
    if token.endswith("ing") and len(token) > 5:
        return token[:-3]
    if token.endswith("ed") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _sparse_score(city, clause_text):
    """Keyword token overlap between a clause and the city's keywords.

    Returns a score in [0, 1] — the fraction of clause tokens found in the
    city's keyword set. Both sides are stemmed, so 'beaches' counts as
    'beach' — a member who types a plural must not silently lose the
    match on a token the dataset stores in the singular. Matched tokens
    are reported in the member's OWN wording, not the stem.
    """
    clause_tokens = {}
    for t in re.split(r"[^a-z0-9]+", clause_text.lower()):
        if t:
            clause_tokens.setdefault(_stem(t), t)

    if not clause_tokens:
        return 0.0, ()

    city_tokens = set()
    for kw in city.keywords:
        for t in re.split(r"[^a-z0-9]+", kw.lower()):
            if t:
                city_tokens.add(_stem(t))
    for vibe in city.vibes:
        for t in re.split(r"[^a-z0-9]+", vibe.lower()):
            if t:
                city_tokens.add(_stem(t))

    overlap = set(clause_tokens) & city_tokens
    if not overlap:
        return 0.0, ()

    score = len(overlap) / len(clause_tokens)
    # Report the member's own wording for each stemmed match
    return score, tuple(sorted(clause_tokens[stem] for stem in overlap))


def score_city(city, clause_list):
    """(dense, sparse, matched). Max-pooled across the city's vectors.

    Per-clause scores are normalised by the NUMBER of positive clauses (the
    mean, not the sum). Summing saturates: a member with three clauses can
    outscore every member with one, and every city with several weak matches
    piles up to the clamp, leaving the board pinned at 1.0 with nothing left
    to rank by. Negated clauses subtract, and one clamp happens at the end.

    A positive clause counts as a dense match only when its best cosine
    clears MIN_SIMILARITY. Dense retrieval always returns *something*; below
    the floor that "something" is noise, not a preference. Sparse keyword
    overlap still counts as a hard match on its own.
    """
    city_vecs = _city_vectors(city)
    all_city_vecs = []
    for cat_vecs in city_vecs.values():
        all_city_vecs.extend(cat_vecs)

    total_dense = 0.0
    total_sparse = 0.0
    all_matched = set()
    n_positive = 0

    for cl in clause_list:
        if vectors.is_zero(cl.vector):
            continue

        # Dense: max-pool cosine against city's vectors
        best = vectors.max_pool(cl.vector, all_city_vecs)

        # Sparse: keyword overlap
        sp, matched_tokens = _sparse_score(city, cl.text)

        if cl.negated:
            # Negated clauses SUBTRACT
            total_dense -= best
            total_sparse -= sp
        else:
            # Below MIN_SIMILARITY the clause has not matched this city;
            # do not bank noise as a weak preference.
            if best < MIN_SIMILARITY:
                best = 0.0
            total_dense += best
            total_sparse += sp
            all_matched.update(matched_tokens)
            n_positive += 1

    # Normalise by the number of positive clauses, THEN clamp once.
    if n_positive:
        total_dense = total_dense / n_positive
        total_sparse = total_sparse / n_positive
    total_dense = max(0.0, min(1.0, total_dense))
    total_sparse = max(0.0, min(1.0, total_sparse))

    return (total_dense, total_sparse, tuple(sorted(all_matched)))


def shortlist(members, limit=14, pool=None):
    """The group's words -> the cities worth ASKING ATLAS ABOUT.

    `limit` is a rate-limit guardrail as much as a ranking one: a sweep is
    several calls per city per date. `pool` defaults to dataset.reachable() -
    never the full dataset, because proposing a city Atlas has never answered
    for spends a call to render a gap.

    `members` is a list of free-text preference strings (one per member).

    Scoring is PER MEMBER, then averaged: each member's clauses are scored
    against the city separately and the member scores are meaned. Pooling
    every clause into one list lets a member with more (or denser-matching)
    clauses dominate — e.g. "temples" + "beaches" used to return an
    all-temple board, because the beach member's single clause was drowned
    out. Averaging guarantees each member one equal vote on the shortlist.
    """
    if pool is None:
        pool = dataset.reachable()

    if not members:
        return []

    # Per-member clause lists + aggregate named/unrecognised across members
    member_clause_lists = []
    all_named = {}
    all_unrecognised = []

    for text in members:
        member_clauses = clauses(text)
        if member_clauses:
            member_clause_lists.append(member_clauses)
        named = places_named(text)
        for cid, sign in named.items():
            if cid not in all_named or sign < all_named[cid]:
                all_named[cid] = sign
        all_unrecognised.extend(unrecognised(text))

    n_members = len(member_clause_lists)

    # Score each city
    results = []
    for city in pool:
        # One equal vote per member: score the member's own clauses, then
        # average the member scores.
        dense_sum = 0.0
        sparse_sum = 0.0
        all_matched = set()
        for mclauses in member_clause_lists:
            dense, sparse, matched = score_city(city, mclauses)
            dense_sum += dense
            sparse_sum += sparse
            all_matched.update(matched)
        dense = dense_sum / n_members if n_members else 0.0
        sparse = sparse_sum / n_members if n_members else 0.0
        matched = tuple(sorted(all_matched))

        # Named tier. A named city keeps its REAL vibe_score and wins by
        # tier, not by overwriting the score to 1.0 — overwriting collapses
        # every named city onto one number and leaves nothing to rank by.
        # A ruled-out city is reported at 0.0 rather than dropped, so the
        # group can see it was heard.
        named_val = all_named.get(city.city_id, 0)

        # Compute vibe_score with fusion
        vibe_score = WEIGHTS["dense"] * dense + WEIGHTS["sparse"] * sparse

        if named_val < 0:
            vibe_score = 0.0
        elif named_val == 0 and vibe_score < MIN_SIMILARITY:
            # Below the floor the city's words were never matched. Never
            # named, never ruled out — drop it rather than rank noise.
            continue

        m = Match(
            city_id=city.city_id,
            city_name=city.city_name,
            country=city.country,
            vibe_score=vibe_score,
            dense=dense,
            sparse=sparse,
            named=named_val,
            matched=matched,
        )
        results.append(m)

    # Sort: named > 0 first (tier), then by vibe_score descending
    results.sort(key=lambda m: (m.named > 0, m.vibe_score), reverse=True)

    return results[:limit]


def group_vibe(members):
    """Aggregate vibe words from the group's preference texts.

    Returns a list of descriptive words that capture the group's combined
    preferences.
    """
    if not members:
        return []

    word_freq = {}
    stopwords = {"and", "but", "the", "a", "an", "to", "in", "on", "at",
                 "for", "of", "is", "it", "with", "also", "or"}

    for text in members:
        words = [w for w in re.split(r"[^a-z]+", text.lower())
                 if w and w not in stopwords and len(w) > 2]
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1

    # Sort by frequency descending
    ranked = sorted(word_freq.items(), key=lambda x: (-x[1], x[0]))
    return [w for w, _ in ranked[:10]]


def describe(members):
    """A plain-text summary of the group's combined preferences."""
    vibes = group_vibe(members)
    if not vibes:
        return "no preferences stated"
    return ", ".join(vibes[:5])
