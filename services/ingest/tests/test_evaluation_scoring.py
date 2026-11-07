import pytest
from atlas_ingest.evaluation.scoring import MISSING, score_clustering, score_event_type, score_linking


def test_linking_micro_precision_recall_hand_calculated() -> None:
    gold = {"a": ["BTC-USDT"], "b": ["TSLA-USDT", "NVDA-USDT"], "c": []}
    pred = {"a": ["BTC-USDT", "ETH-USDT"], "b": ["TSLA-USDT"], "c": ["AAPL-USDT"]}
    # tp: a/BTC, b/TSLA = 2; fp: a/ETH, c/AAPL = 2; fn: b/NVDA = 1
    s = score_linking(pred, gold)
    assert (s.tp, s.fp, s.fn) == (2, 2, 1)
    assert round(s.precision, 4) == 0.5000
    assert round(s.recall, 4) == 0.6667
    assert round(s.f1, 4) == 0.5714


def test_linking_missing_prediction_is_empty_and_empty_sets_score_one() -> None:
    gold = {"a": ["XAU-USDT"], "b": []}
    s = score_linking({}, gold)
    assert (s.tp, s.fp, s.fn) == (0, 0, 1)
    assert s.precision == 1.0
    assert s.recall == 0.0
    assert score_linking({"b": []}, {"b": []}).f1 == 1.0


def test_event_type_accuracy_and_confusion_hand_calculated() -> None:
    gold = {"1": "earnings", "2": "earnings", "3": "guidance", "4": "listing", "5": "other", "6": "M&A"}
    pred = {"1": "earnings", "2": "guidance", "3": "guidance", "4": "listing", "5": "listing"}
    e = score_event_type(pred, gold)
    assert (e.correct, e.n) == (3, 6)
    assert round(e.accuracy, 4) == 0.5000
    assert e.confusion["earnings"] == {"earnings": 1, "guidance": 1}
    assert e.confusion["other"] == {"listing": 1}
    assert e.confusion["M&A"] == {MISSING: 1}


def test_clustering_pairwise_and_bcubed_hand_calculated() -> None:
    gold = {"a": "g1", "b": "g1", "c": "g1", "d": "g2", "e": "g2"}
    pred = {"a": "p1", "b": "p1", "c": "p2", "d": "p2", "e": "p3"}
    # gold pairs: ab ac bc de (4); pred pairs: ab cd (2); shared: ab (1)
    c = score_clustering(pred, gold)
    assert round(c.pairwise_precision, 4) == 0.5000
    assert round(c.pairwise_recall, 4) == 0.2500
    assert round(c.pairwise_f1, 4) == 0.3333
    # B-cubed precision: a 2/2, b 2/2, c 1/2, d 1/2, e 1/1 -> 4/5
    # B-cubed recall:    a 2/3, b 2/3, c 1/3, d 1/2, e 1/2 -> (5/3 + 1)/5 = 8/15
    assert round(c.bcubed_precision, 4) == 0.8000
    assert round(c.bcubed_recall, 4) == 0.5333
    assert round(c.bcubed_f1, 4) == 0.6400


def test_clustering_perfect_and_missing_predictions() -> None:
    gold = {"a": "x", "b": "x", "c": "y"}
    perfect = score_clustering({"a": "1", "b": "1", "c": "2"}, gold)
    assert perfect.pairwise_f1 == pytest.approx(1.0)
    assert perfect.bcubed_f1 == pytest.approx(1.0)
    # Missing ids become singletons: no predicted pairs, gold pair ab missed.
    missing = score_clustering({}, gold)
    assert missing.pairwise_precision == 1.0
    assert missing.pairwise_recall == 0.0
    assert missing.pairwise_f1 == 0.0
