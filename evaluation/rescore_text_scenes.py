"""Re-score saved model content without constructing an inference client."""
import argparse
import json
from pathlib import Path

from evaluation.literal_evidence import audit_literal_response, name_list, violation_metrics
from evaluation.text_scenes import benchmark_metrics, scene_lines
from glossary import load_glossary
from structure import write_json


def rescore(units, original, glossary):
    if units["source_hash"] != original["source_hash"]:
        raise ValueError("Response/source fingerprint mismatch")
    scenes = {s["id"]: s for s in units["scenes"]}
    rows = []
    for row in original["rows"]:
        item = dict(row)
        if row["status"] == "executed":
            scene = scenes[row["scene_id"]]
            lines = scene_lines(scene, units)
            item["original_audit"] = row["audit"]
            item["audit"] = audit_literal_response(row["raw_response"], scene, lines, name_list(lines, glossary))
        rows.append(item)
    return dict(source_hash=units["source_hash"], model_calls=0, original_metrics=original["metrics"],
                metrics={**benchmark_metrics(rows), **violation_metrics(rows)}, rows=rows, glossary=glossary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = rescore(json.loads(args.units.read_text(encoding="utf-8")),
                     json.loads(args.original.read_text(encoding="utf-8")), load_glossary())
    write_json(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("rows", "glossary")}, indent=2))


if __name__ == "__main__":
    main()
