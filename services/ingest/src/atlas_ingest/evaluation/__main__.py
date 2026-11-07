"""``python -m atlas_ingest.evaluation report`` — score registered stages against the fixture."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from atlas_ingest.embedding import make_embedder, set_default_embedder
from atlas_ingest.evaluation import registry
from atlas_ingest.evaluation.fixture import (
    DEFAULT_FIXTURE_PATH,
    HeadlineRow,
    gold_clusters,
    gold_event_types,
    gold_linking,
    load_fixture,
)
from atlas_ingest.evaluation.scoring import score_clustering, score_event_type, score_linking

# Brief §7.2 targets.
TARGET_LINKING_PRECISION = 0.95
TARGET_EVENT_ACCURACY = 0.85
TARGET_CLUSTER_F1 = 0.90


def _mark(value: float, target: float) -> str:
    return "PASS" if value >= target else "BELOW TARGET"


def report_lines(rows: Sequence[HeadlineRow], *, confusion: bool = False) -> list[str]:
    lines = [f"fixture: {len(rows)} rows"]

    linker = registry.get_linker()
    if linker is None:
        lines.append("linking: not registered")
    else:
        pred_links = {r.id: set(linker(r)) for r in rows}
        s = score_linking(pred_links, gold_linking(rows))
        lines.append(
            f"linking: precision={s.precision:.4f} recall={s.recall:.4f} f1={s.f1:.4f} "
            f"tp={s.tp} fp={s.fp} fn={s.fn} "
            f"(target precision>={TARGET_LINKING_PRECISION:.2f}: "
            f"{_mark(s.precision, TARGET_LINKING_PRECISION)})"
        )

    classifier = registry.get_classifier()
    if classifier is None:
        lines.append("event_type: not registered")
    else:
        e = score_event_type({r.id: classifier(r) for r in rows}, gold_event_types(rows))
        lines.append(
            f"event_type: accuracy={e.accuracy:.4f} ({e.correct}/{e.n}) "
            f"(target>={TARGET_EVENT_ACCURACY:.2f}: {_mark(e.accuracy, TARGET_EVENT_ACCURACY)})"
        )
        if confusion:
            for gold_type in sorted(e.confusion):
                misses = {p: n for p, n in e.confusion[gold_type].items() if p != gold_type}
                if misses:
                    detail = ", ".join(f"{p}={n}" for p, n in sorted(misses.items()))
                    lines.append(f"  confused {gold_type} -> {detail}")

    clusterer = registry.get_clusterer()
    if clusterer is None:
        lines.append("clustering: not registered")
    else:
        c = score_clustering(dict(clusterer(rows)), gold_clusters(rows))
        lines.append(
            f"clustering: pairwise_f1={c.pairwise_f1:.4f} (p={c.pairwise_precision:.4f} "
            f"r={c.pairwise_recall:.4f}) bcubed_f1={c.bcubed_f1:.4f} (p={c.bcubed_precision:.4f} "
            f"r={c.bcubed_recall:.4f}) "
            f"(target pairwise_f1>={TARGET_CLUSTER_F1:.2f}: {_mark(c.pairwise_f1, TARGET_CLUSTER_F1)})"
        )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m atlas_ingest.evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report", help="score registered stages against the headline fixture")
    rep.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_PATH)
    rep.add_argument("--linker", help="module:attr, overrides the registered linker")
    rep.add_argument("--classifier", help="module:attr, overrides the registered classifier")
    rep.add_argument("--clusterer", help="module:attr, overrides the registered clusterer")
    rep.add_argument(
        "--embedder",
        choices=("auto", "real", "fake"),
        default="auto",
        help="embeddings for clustering: real model (downloaded once), fake hashing, or auto "
        "(real when the model is already cached)",
    )
    rep.add_argument("--confusion", action="store_true", help="also print event-type confusions")
    args = parser.parse_args(argv)

    registry.load_entry_points()
    for stage in registry.STAGES:
        target = getattr(args, stage)
        if target:
            registry.register_dotted(stage, target)

    embedder = make_embedder(args.embedder)
    set_default_embedder(embedder)
    print(f"embedder: {embedder.name}")
    for line in report_lines(load_fixture(args.fixture), confusion=args.confusion):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
