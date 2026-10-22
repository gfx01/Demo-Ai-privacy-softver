

def test_resolve_overlaps_matches_pairwise_reference():
    """resolve_overlaps (O(n log n)) gives the same spans as the original pairwise version."""
    import random

    from privacy_hub.masking import Span, resolve_overlaps

    def reference(spans):
        valid = [s for s in spans if s.end > s.start]
        contained = {
            id(s) for s in valid for o in valid
            if o is not s and o.start <= s.start and s.end <= o.end and (o.end - o.start) > (s.end - s.start)
        }
        ranked = sorted(valid, key=lambda s: (id(s) in contained, -s.score, -(s.end - s.start), s.start))
        kept = []
        for s in ranked:
            if all(s.end <= k.start or s.start >= k.end for k in kept):
                kept.append(s)
        return sorted(kept, key=lambda s: s.start)

    rng = random.Random(11)
    for _ in range(500):
        spans = []
        for _ in range(rng.randint(0, 30)):
            start = rng.randint(0, 40)
            spans.append(Span(rng.choice(["PERSON", "LOCATION"]), start, start + rng.randint(0, 9), rng.choice([0.4, 0.5, 0.85])))
        key = lambda s: (s.entity_type, s.start, s.end, s.score)  # noqa: E731
        assert list(map(key, resolve_overlaps(spans))) == list(map(key, reference(spans)))
