"""Pure scoring functions for entity linking, event typing and story clustering.

Every function takes gold labels keyed by row id and predictions keyed the same way. Scoring runs over
the gold ids; a gold id missing from the predictions counts as an empty link set, a wrong event type
(``MISSING``) or a singleton cluster. Prediction ids not in gold are ignored.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations

MISSING = "<missing>"


def _ratio(num: int, den: int) -> float:
    """num/den, with an empty denominator scoring 1.0 (nothing claimed, nothing wrong)."""
    return num / den if den else 1.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


@dataclass(frozen=True)
class LinkingScore:
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


def score_linking(pred: Mapping[str, Iterable[str]], gold: Mapping[str, Iterable[str]]) -> LinkingScore:
    """Micro precision/recall over (row id, instrument) pairs."""
    tp = fp = fn = 0
    for row_id, gold_items in gold.items():
        g = set(gold_items)
        p = set(pred.get(row_id, ()))
        tp += len(p & g)
        fp += len(p - g)
        fn += len(g - p)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return LinkingScore(precision, recall, _f1(precision, recall), tp, fp, fn)


@dataclass(frozen=True)
class EventTypeScore:
    accuracy: float
    correct: int
    n: int
    # confusion[gold_type][predicted_type] = count
    confusion: dict[str, dict[str, int]]


def score_event_type(pred: Mapping[str, str], gold: Mapping[str, str]) -> EventTypeScore:
    """Accuracy plus a gold-by-predicted confusion matrix."""
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    correct = 0
    for row_id, g in gold.items():
        p = pred.get(row_id, MISSING)
        confusion[g][p] += 1
        correct += int(p == g)
    n = len(gold)
    return EventTypeScore(
        accuracy=correct / n if n else 1.0,
        correct=correct,
        n=n,
        confusion={g: dict(row) for g, row in confusion.items()},
    )


@dataclass(frozen=True)
class ClusteringScore:
    pairwise_precision: float
    pairwise_recall: float
    pairwise_f1: float
    bcubed_precision: float
    bcubed_recall: float
    bcubed_f1: float


def _groups(labels: Mapping[str, str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for item, label in labels.items():
        out[label].add(item)
    return out


def _pairs(labels: Mapping[str, str]) -> set[tuple[str, str]]:
    return {pair for members in _groups(labels).values() for pair in combinations(sorted(members), 2)}


def score_clustering(pred: Mapping[str, str], gold: Mapping[str, str]) -> ClusteringScore:
    """Pairwise precision/recall/F1 over same-cluster pairs, plus B-cubed."""
    # Restrict to gold ids; a missing prediction becomes its own singleton.
    predicted = {i: (f"pred:{pred[i]}" if i in pred else f"{MISSING}:{i}") for i in gold}

    gold_pairs = _pairs(gold)
    pred_pairs = _pairs(predicted)
    both = len(gold_pairs & pred_pairs)
    pp = _ratio(both, len(pred_pairs))
    pr = _ratio(both, len(gold_pairs))

    gold_groups = _groups(gold)
    pred_groups = _groups(predicted)
    bp_sum = br_sum = 0.0
    for item in gold:
        g = gold_groups[gold[item]]
        p = pred_groups[predicted[item]]
        overlap = len(g & p)
        bp_sum += overlap / len(p)
        br_sum += overlap / len(g)
    n = len(gold)
    bp = bp_sum / n if n else 1.0
    br = br_sum / n if n else 1.0
    return ClusteringScore(pp, pr, _f1(pp, pr), bp, br, _f1(bp, br))
