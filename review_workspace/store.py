"""Local SQLite autosave and portable JSON export for human annotations."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from . import SCHEMA, SCHEMA_VERSION

DECISIONS = {"keep", "maybe", "reject"}
CATEGORIES = {
    "Important dialogue", "Memorable phrase", "Fight/action", "Plot revelation",
    "Emotional moment", "Comedy", "Suspense", "Dramatic reaction", "Other",
    "Negative example", "Unclassified",
}
RATINGS = {
    "plot_importance", "entertainment_value", "hook", "context", "coherence",
    "impact", "payoff", "clarity", "standalone_quality",
}


def source_fingerprint(source: str | Path) -> str:
    path = Path(source).resolve()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_dataset(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported human review schema")
    source = data.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("fingerprint"), str) or len(source["fingerprint"]) != 64:
        raise ValueError("Dataset needs a source fingerprint")
    duration_ms = source.get("duration_ms")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms <= 0:
        raise ValueError("Dataset needs a positive source duration")
    moments = data.get("moments")
    if not isinstance(moments, list):
        raise ValueError("Dataset moments must be an array")
    ids: set[str] = set()
    for row in moments:
        if not isinstance(row, dict) or not isinstance(row.get("moment_id"), str) or row["moment_id"] in ids:
            raise ValueError("Moments need unique IDs")
        ids.add(row["moment_id"])
        point = row.get("kind") == "point"
        rough_start, rough_end = row.get("rough_start_ms"), row.get("rough_end_ms")
        if not isinstance(rough_start, int) or not isinstance(rough_end, int) or rough_start < 0 or rough_end > duration_ms:
            raise ValueError("Moment rough bounds are outside the source")
        if (point and rough_start != rough_end) or (not point and rough_end <= rough_start):
            raise ValueError("Moment rough bounds are invalid")
        if row.get("category") not in CATEGORIES:
            raise ValueError("Unknown human category")
        decision = row.get("decision")
        if decision is not None and decision not in DECISIONS:
            raise ValueError("Unknown reviewer decision")
        ratings = row.get("ratings", {})
        if not isinstance(ratings, dict) or any(key not in RATINGS or isinstance(value, bool) or not isinstance(value, int) or value not in range(1, 6)
                                                for key, value in ratings.items()):
            raise ValueError("Ratings must use known fields and integer values from 1 to 5")
        refined_start, refined_end = row.get("refined_start_ms"), row.get("refined_end_ms")
        if refined_start is not None or refined_end is not None:
            if not isinstance(refined_start, int) or not isinstance(refined_end, int) or refined_start < 0 or refined_end > duration_ms or refined_end <= refined_start:
                raise ValueError("Refined bounds are invalid")
    ranks = [row.get("final_rank") for row in moments if row.get("final_rank") is not None]
    if len(ranks) > 2 or len(ranks) != len(set(ranks)) or any(rank not in (1, 2) for rank in ranks):
        raise ValueError("Final ranking may contain at most two unique positions")
    return data


class ReviewStore:
    def __init__(self, path: str | Path, source: dict[str, Any], reviewer: str = ""):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self._schema()
        saved_schema = self.connection.execute("SELECT value FROM session WHERE key='schema'").fetchone()
        if saved_schema and json.loads(saved_schema["value"]) != SCHEMA:
            self.close()
            raise ValueError("Saved database uses a different annotation schema")
        saved = self.connection.execute("SELECT value FROM session WHERE key='source'").fetchone()
        source_json = json.dumps(source, ensure_ascii=False, sort_keys=True)
        if saved and saved["value"] != source_json:
            self.close()
            raise ValueError("Saved session source metadata does not match the selected video")
        now = time.time()
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO session(key,value) VALUES('source',?)", (source_json,))
            self.connection.execute("INSERT OR IGNORE INTO session(key,value) VALUES('schema',?)", (json.dumps(SCHEMA),))
            self.connection.execute("INSERT OR IGNORE INTO session(key,value) VALUES('schema_version',?)", (json.dumps(SCHEMA_VERSION),))
            self.connection.execute("INSERT OR IGNORE INTO session(key,value) VALUES('reviewer',?)", (json.dumps(reviewer),))
            self.connection.execute("INSERT OR IGNORE INTO session(key,value) VALUES('phase',?)", (json.dumps("pass1"),))
            self.connection.execute("INSERT OR IGNORE INTO session(key,value) VALUES('created_at',?)", (json.dumps(now),))
            self.connection.execute("INSERT OR IGNORE INTO session(key,value) VALUES('active_start_ms',?)", (json.dumps(None),))
            self.connection.execute("INSERT OR IGNORE INTO session(key,value) VALUES('pass1_complete',?)", (json.dumps(False),))

    def _schema(self) -> None:
        with self.connection:
            self.connection.execute("CREATE TABLE IF NOT EXISTS session(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS moments(moment_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS coverage(start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,PRIMARY KEY(start_ms,end_ms))")

    def get(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute("SELECT value FROM session WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def set(self, key: str, value: Any) -> None:
        with self.connection:
            self.connection.execute("INSERT INTO session(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                    (key, json.dumps(value, ensure_ascii=False)))

    def moments(self) -> list[dict[str, Any]]:
        return [json.loads(row["data"]) for row in self.connection.execute("SELECT data FROM moments ORDER BY json_extract(data,'$.rough_start_ms'), rowid")]

    def save_moment(self, row: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute("INSERT INTO moments(moment_id,data) VALUES(?,?) ON CONFLICT(moment_id) DO UPDATE SET data=excluded.data",
                                    (row["moment_id"], json.dumps(row, ensure_ascii=False)))

    def delete_moment(self, moment_id: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM moments WHERE moment_id=?", (moment_id,))

    def coverage(self) -> list[tuple[int, int]]:
        return [(row["start_ms"], row["end_ms"]) for row in self.connection.execute("SELECT start_ms,end_ms FROM coverage ORDER BY start_ms")]

    def save_coverage(self, intervals: list[tuple[int, int]]) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM coverage")
            self.connection.executemany("INSERT INTO coverage(start_ms,end_ms) VALUES(?,?)", intervals)

    def dataset(self) -> dict[str, Any]:
        source = self.get("source")
        moments = self.moments()
        data = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "session_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source['fingerprint']}:{self.path.resolve()}")),
            "reviewer": self.get("reviewer", ""),
            "source": source,
            "created_at": self.get("created_at"),
            "updated_at": time.time(),
            "phase": self.get("phase", "pass1"),
            "pass1_complete": self.get("pass1_complete", False),
            "player_runtime": self.get("player_runtime"),
            "selected_audio_track": self.get("selected_audio_track"),
            "coverage_intervals": [{"start_ms": a, "end_ms": b} for a, b in self.coverage()],
            "moments": moments,
            "point_markers": [row for row in moments if row.get("kind") == "point"],
        }
        return validate_dataset(data)

    def export_json(self, target: str | Path) -> Path:
        data = self.dataset()
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return path

    def close(self) -> None:
        if getattr(self, "connection", None) is not None:
            self.connection.close()
            self.connection = None
