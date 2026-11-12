"""Persistent, transactional episode and nightly-session queue."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA_VERSION = 1
EPISODE_STATES = {"PENDING", "ANALYZING", "ANALYZED", "READY_TO_RENDER", "RENDERING", "VALIDATING",
                  "COMPLETED", "INTERRUPTED", "FAILED_RETRYABLE", "FAILED_FINAL", "SKIPPED", "SOURCE_CHANGED"}
ACTIVE_SESSION_STATES = ("RUNNING", "PAUSED")


class ActiveSessionError(RuntimeError):
    pass


class QueueSchemaError(RuntimeError):
    pass


def normalized_source_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def source_id(path: Path) -> str:
    return hashlib.sha256(normalized_source_path(path).encode("utf-8")).hexdigest()


def natural_sort_key(value: str | Path):
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(value)))


def source_fingerprint(path: Path, chunk_size: int = 64 * 1024) -> tuple[int, int, str]:
    stat = path.stat()
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str(stat.st_size).encode())
    digest.update(str(stat.st_mtime_ns).encode())
    with path.open("rb") as stream:
        digest.update(stream.read(chunk_size))
        if stat.st_size > chunk_size:
            stream.seek(max(0, stat.st_size - chunk_size))
            digest.update(stream.read(chunk_size))
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


class QueueStore:
    def __init__(self, database: Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = None
        try:
            self.connection = sqlite3.connect(self.database, timeout=30, isolation_level="IMMEDIATE", check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA busy_timeout=30000")
            self._initialize()
            self.recover_interrupted()
        except QueueSchemaError:
            if self.connection:
                self.connection.close()
            raise
        except sqlite3.DatabaseError as exc:
            if self.connection:
                self.connection.close()
            raise QueueSchemaError(f"Queue database could not be opened safely: {exc}. The database was preserved at {self.database}.") from exc

    def _initialize(self):
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise QueueSchemaError(f"Queue database schema {version} is newer than supported schema {SCHEMA_VERSION}.")
        if version == 0:
            with self.connection:
                self.connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE episodes(
                        id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
                        source_size INTEGER NOT NULL, source_mtime_ns INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                        observed_fingerprint TEXT, queue_position INTEGER NOT NULL, status TEXT NOT NULL,
                        last_successful_stage TEXT, validated_shorts INTEGER NOT NULL DEFAULT 0,
                        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                        first_discovered_at TEXT NOT NULL, last_processed_at TEXT,
                        source_changed INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE sessions(
                        id TEXT PRIMARY KEY, session_date TEXT NOT NULL, folder TEXT NOT NULL, owner_pid INTEGER NOT NULL,
                        target INTEGER NOT NULL, max_seconds INTEGER NOT NULL, batch_size INTEGER NOT NULL,
                        semantic_enabled INTEGER NOT NULL, status TEXT NOT NULL,
                        started_at TEXT NOT NULL, ended_at TEXT, elapsed_seconds REAL NOT NULL DEFAULT 0,
                        clips_prepared INTEGER NOT NULL DEFAULT 0, summary_path TEXT
                    );
                    CREATE TABLE clips(
                        id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(id),
                        source_fingerprint TEXT NOT NULL, candidate_fingerprint TEXT NOT NULL UNIQUE,
                        start REAL NOT NULL, end REAL NOT NULL, heuristic_score REAL,
                        semantic_score REAL, semantic_category TEXT, selected INTEGER NOT NULL,
                        render_state TEXT NOT NULL, validation_state TEXT NOT NULL,
                        mp4_path TEXT, srt_path TEXT, error TEXT, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE session_clips(
                        session_id TEXT NOT NULL REFERENCES sessions(id),
                        clip_id TEXT NOT NULL REFERENCES clips(id),
                        added_at TEXT NOT NULL, PRIMARY KEY(session_id, clip_id)
                    );
                    CREATE INDEX episodes_order_idx ON episodes(queue_position, path);
                    CREATE INDEX episodes_state_idx ON episodes(status, queue_position);
                    CREATE UNIQUE INDEX one_active_session ON sessions((1)) WHERE status IN ('RUNNING','PAUSED');
                    PRAGMA user_version=1;
                    COMMIT;
                """)

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def recover_interrupted(self):
        active = self.connection.execute("SELECT owner_pid FROM sessions WHERE status IN ('RUNNING','PAUSED') ORDER BY started_at DESC LIMIT 1").fetchone()
        if active:
            try:
                os.kill(active["owner_pid"], 0)
                return
            except PermissionError:
                return
            except (OSError, ProcessLookupError):
                pass
        now = self._now()
        with self.connection:
            self.connection.execute("UPDATE episodes SET status='INTERRUPTED', last_error=COALESCE(last_error,'Application stopped before episode completion') WHERE status IN ('ANALYZING','RENDERING','VALIDATING')")
            self.connection.execute("UPDATE sessions SET status='INTERRUPTED', ended_at=COALESCE(ended_at,?) WHERE status='RUNNING'", (now,))

    def scan(self, folder: Path, videos: list[Path]) -> list[dict]:
        normalized = {}
        for path in videos:
            resolved = Path(path).resolve(strict=False)
            normalized.setdefault(normalized_source_path(resolved), resolved)
        ordered = sorted(normalized.values(), key=lambda p: natural_sort_key(p.relative_to(folder)))
        now = self._now()
        with self.connection:
            for offset, path in enumerate(ordered):
                size, mtime, fingerprint = source_fingerprint(path)
                sid = source_id(path)
                row = self.connection.execute("SELECT * FROM episodes WHERE id=?", (sid,)).fetchone()
                if row is None:
                    self.connection.execute("INSERT INTO episodes(id,path,filename,source_size,source_mtime_ns,fingerprint,queue_position,status,first_discovered_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                            (sid, normalized_source_path(path), path.name, size, mtime, fingerprint,
                                             offset, "PENDING", now))
                elif row["fingerprint"] != fingerprint:
                    self.connection.execute("UPDATE episodes SET filename=?, observed_fingerprint=?, source_size=?, source_mtime_ns=?, source_changed=1, status='SOURCE_CHANGED', last_error='Source file changed; explicit reprocess is required' WHERE id=?",
                                            (path.name, fingerprint, size, mtime, sid))
                self.connection.execute("UPDATE episodes SET queue_position=?,filename=? WHERE id=?",
                                        (offset, path.name, sid))
            return self.episodes()

    def episodes(self, statuses: set[str] | None = None) -> list[dict]:
        if statuses:
            if not statuses <= EPISODE_STATES:
                raise ValueError("Unknown episode status")
            marks = ",".join("?" for _ in statuses)
            rows = self.connection.execute(f"SELECT * FROM episodes WHERE status IN ({marks}) ORDER BY queue_position,path", tuple(sorted(statuses)))
        else:
            rows = self.connection.execute("SELECT * FROM episodes ORDER BY queue_position,path")
        return [dict(row) for row in rows]

    def counts(self, folder: Path | None = None) -> dict[str, int]:
        if folder is None:
            values = {row["status"]: row["count"] for row in self.connection.execute("SELECT status,COUNT(*) AS count FROM episodes GROUP BY status")}
        else:
            root = Path(folder).resolve(strict=False)
            values = {}
            for row in self.episodes():
                if Path(row["path"]).resolve(strict=False).is_relative_to(root):
                    values[row["status"]] = values.get(row["status"], 0) + 1
        return {state: values.get(state, 0) for state in sorted(EPISODE_STATES)}

    def create_session(self, folder: Path, target: int = 5, max_seconds: int = 21600,
                       batch_size: int = 2, semantic_enabled: bool = False) -> str:
        if not 1 <= target <= 10 or not 60 <= max_seconds <= 86400 or not 1 <= batch_size <= 4:
            raise ValueError("Target must be 1–10, time limit 1 minute–24 hours, and micro-batch size 1–4")
        sid = uuid.uuid4().hex
        now = self._now()
        try:
            with self.connection:
                self.connection.execute("INSERT INTO sessions(id,session_date,folder,owner_pid,target,max_seconds,batch_size,semantic_enabled,status,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                        (sid, time.strftime("%Y-%m-%d", time.localtime()), normalized_source_path(folder), os.getpid(),
                                         target, max_seconds, batch_size, int(semantic_enabled), "RUNNING", now))
        except sqlite3.IntegrityError as exc:
            raise ActiveSessionError("Another nightly session is already running or paused.") from exc
        return sid

    def session(self, session_id: str) -> dict:
        row = self.connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        return dict(row)

    def unreported_interrupted_sessions(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM sessions WHERE status='INTERRUPTED' AND summary_path IS NULL ORDER BY started_at")]

    def update_session(self, session_id: str, status: str, elapsed_seconds: float | None = None,
                       clips_prepared: int | None = None, summary_path: Path | None = None):
        if status not in {"RUNNING", "PAUSED", "COMPLETED", "TIME_LIMIT", "STOPPED", "INTERRUPTED", "FAILED"}:
            raise ValueError(status)
        fields, values = ["status=?"], [status]
        if elapsed_seconds is not None:
            fields.append("elapsed_seconds=?"); values.append(max(0, elapsed_seconds))
        if clips_prepared is not None:
            fields.append("clips_prepared=?"); values.append(clips_prepared)
        if summary_path is not None:
            fields.append("summary_path=?"); values.append(str(summary_path))
        if status not in ACTIVE_SESSION_STATES:
            fields.append("ended_at=?"); values.append(self._now())
        values.append(session_id)
        with self.connection:
            self.connection.execute(f"UPDATE sessions SET {','.join(fields)} WHERE id=?", values)

    def set_episode_status(self, episode_id: str, status: str, last_successful_stage: str | None = None,
                           error: str | None = None, increment_attempt: bool = False,
                           validated_shorts: int | None = None):
        if status not in EPISODE_STATES:
            raise ValueError(status)
        with self.connection:
            self.connection.execute("UPDATE episodes SET status=?, last_successful_stage=COALESCE(?,last_successful_stage), last_error=?, attempts=attempts+?, last_processed_at=?, validated_shorts=COALESCE(?,validated_shorts) WHERE id=?",
                                    (status, last_successful_stage, error, int(increment_attempt), self._now(), validated_shorts, episode_id))

    def retry_episode(self, episode_id: str):
        row = self.connection.execute("SELECT status FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not row or row["status"] not in {"FAILED_RETRYABLE", "FAILED_FINAL", "INTERRUPTED"}:
            raise ValueError("Only failed or interrupted episodes can be retried")
        self.set_episode_status(episode_id, "PENDING", error=None)

    def requeue_source_changed(self, episode_id: str):
        row = self.connection.execute("SELECT path,observed_fingerprint,source_size,source_mtime_ns FROM episodes WHERE id=? AND status='SOURCE_CHANGED'", (episode_id,)).fetchone()
        if not row:
            raise ValueError("Episode is not marked SOURCE_CHANGED")
        size, mtime, fingerprint = source_fingerprint(Path(row["path"]))
        with self.connection:
            self.connection.execute("UPDATE episodes SET fingerprint=?,observed_fingerprint=NULL,source_size=?,source_mtime_ns=?,source_changed=0,status='PENDING',last_error=NULL,validated_shorts=0 WHERE id=?", (fingerprint, size, mtime, episode_id))
            self.connection.execute("UPDATE clips SET selected=0, render_state='STALE', validation_state='STALE', error='Source changed; output must be regenerated' WHERE episode_id=?", (episode_id,))

    def record_candidates(self, episode_id: str, source_fingerprint_value: str, candidates: list[dict]):
        now = self._now()
        with self.connection:
            for candidate in candidates:
                start, end = round(candidate["start"], 3), round(candidate["end"], 3)
                identity = hashlib.sha256(f"{episode_id}:{source_fingerprint_value}:{start}:{end}".encode()).hexdigest()
                clip_id = identity[:32]
                self.connection.execute("""INSERT INTO clips(id,episode_id,source_fingerprint,candidate_fingerprint,start,end,heuristic_score,semantic_score,semantic_category,selected,render_state,validation_state,mp4_path,srt_path,error,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(candidate_fingerprint) DO UPDATE SET heuristic_score=excluded.heuristic_score, semantic_score=excluded.semantic_score, semantic_category=excluded.semantic_category, selected=excluded.selected, render_state=CASE WHEN clips.render_state='COMPLETED' THEN 'COMPLETED' ELSE excluded.render_state END, validation_state=CASE WHEN clips.validation_state='VALIDATED' THEN 'VALIDATED' ELSE 'PENDING' END, updated_at=excluded.updated_at""",
                    (clip_id, episode_id, source_fingerprint_value, identity, start, end,
                     candidate.get("heuristic_score", candidate.get("score")), candidate.get("semantic_score"),
                     candidate.get("semantic_category"), int(bool(candidate.get("selected"))),
                     "READY" if candidate.get("selected") else "REJECTED", "PENDING", None, None, None, now))

    def mark_episode_render_failed(self, episode_id: str, error: str):
        with self.connection:
            self.connection.execute("UPDATE clips SET render_state='FAILED',validation_state='FAILED',error=?,updated_at=? WHERE episode_id=? AND selected=1 AND render_state!='COMPLETED'",
                                    (error, self._now(), episode_id))

    def record_validated_clip(self, session_id: str, episode_id: str, source_fingerprint_value: str,
                              candidate: dict, mp4: Path, srt: Path):
        start, end = round(candidate["start"], 3), round(candidate["end"], 3)
        identity = hashlib.sha256(f"{episode_id}:{source_fingerprint_value}:{start}:{end}".encode()).hexdigest()
        clip_id = identity[:32]
        now = self._now()
        with self.connection:
            self.connection.execute("""INSERT INTO clips(id,episode_id,source_fingerprint,candidate_fingerprint,start,end,heuristic_score,semantic_score,semantic_category,selected,render_state,validation_state,mp4_path,srt_path,error,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,1,'COMPLETED','VALIDATED',?,?,NULL,?) ON CONFLICT(candidate_fingerprint) DO UPDATE SET render_state='COMPLETED',validation_state='VALIDATED',mp4_path=excluded.mp4_path,srt_path=excluded.srt_path,error=NULL,updated_at=excluded.updated_at""",
                (clip_id, episode_id, source_fingerprint_value, identity, start, end,
                 candidate.get("heuristic_score", candidate.get("score")), candidate.get("semantic_score"),
                 candidate.get("semantic_category"), str(mp4), str(srt), now))
            self.connection.execute("INSERT OR IGNORE INTO session_clips(session_id,clip_id,added_at) VALUES(?,?,?)", (session_id, clip_id, now))
            return self.connection.execute("SELECT changes()").fetchone()[0] == 1

    def adopt_existing_clip(self, episode_id: str, source_fingerprint_value: str, candidate: dict, mp4: Path, srt: Path):
        start, end = round(candidate["start"], 3), round(candidate["end"], 3)
        identity = hashlib.sha256(f"{episode_id}:{source_fingerprint_value}:{start}:{end}".encode()).hexdigest()
        with self.connection:
            self.connection.execute("""INSERT INTO clips(id,episode_id,source_fingerprint,candidate_fingerprint,start,end,heuristic_score,semantic_score,semantic_category,selected,render_state,validation_state,mp4_path,srt_path,error,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,1,'COMPLETED','VALIDATED',?,?,NULL,?) ON CONFLICT(candidate_fingerprint) DO UPDATE SET render_state='COMPLETED',validation_state='VALIDATED',mp4_path=excluded.mp4_path,srt_path=excluded.srt_path,error=NULL,updated_at=excluded.updated_at""",
                (identity[:32], episode_id, source_fingerprint_value, identity, start, end,
                 candidate.get("heuristic_score", candidate.get("score")), candidate.get("semantic_score"),
                 candidate.get("semantic_category"), str(mp4), str(srt), self._now()))

    def session_clip_count(self, session_id: str) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM session_clips WHERE session_id=?", (session_id,)).fetchone()[0]

    def ready_episodes(self, folder: Path, limit: int) -> list[dict]:
        return self._eligible(folder, limit, ("ANALYZED", "READY_TO_RENDER"))

    def pending_episodes(self, folder: Path, limit: int, include_interrupted: bool = True,
                         excluded: set[str] | None = None) -> list[dict]:
        states = ("PENDING", "INTERRUPTED", "FAILED_RETRYABLE") if include_interrupted else ("PENDING", "FAILED_RETRYABLE")
        return [row for row in self._eligible(folder, limit + len(excluded or ()), states)
                if row["id"] not in (excluded or set())][:limit]

    def has_interrupted(self, folder: Path) -> bool:
        root = Path(folder).resolve(strict=False)
        return any(Path(row["path"]).resolve(strict=False).is_relative_to(root)
                   for row in self.episodes({"INTERRUPTED"}))

    def _eligible(self, folder: Path, limit: int, statuses: tuple[str, ...]) -> list[dict]:
        marks = ",".join("?" for _ in statuses)
        rows = self.connection.execute(f"SELECT * FROM episodes WHERE status IN ({marks}) ORDER BY CASE status WHEN 'ANALYZED' THEN 0 WHEN 'READY_TO_RENDER' THEN 0 ELSE 1 END, queue_position,path",
                                       tuple(statuses))
        root = Path(folder).resolve(strict=False)
        return [dict(row) for row in rows if Path(row["path"]).resolve(strict=False).is_relative_to(root)][:limit]

    def summary(self, session_id: str) -> dict:
        session = self.session(session_id)
        episodes = self.session_episode_counts(session)
        return {"session": session, "queue_counts": self.counts(Path(session["folder"])),
                **episodes, "clips_prepared": self.session_clip_count(session_id)}

    def session_episode_counts(self, session: dict) -> dict[str, int]:
        root = Path(session["folder"])
        processed = [row for row in self.episodes()
                     if Path(row["path"]).resolve(strict=False).is_relative_to(root.resolve(strict=False))
                     and row["last_processed_at"] and row["last_processed_at"] >= session["started_at"]
                     and row["last_processed_at"] <= (session.get("ended_at") or self._now())]
        count = lambda states: sum(1 for row in processed if row["status"] in states)
        return {"episodes_processed": len(processed), "episodes_skipped": count({"SKIPPED"}),
                "episodes_failed": count({"FAILED_RETRYABLE", "FAILED_FINAL"}),
                "episodes_interrupted": count({"INTERRUPTED"})}

    def export_daily_summary(self, session_id: str, output_root: Path, status: str,
                             elapsed_seconds: float, batch_timings: list[dict] | None = None,
                             discovery_seconds: float | None = None,
                             reconciliation_seconds: float | None = None) -> tuple[Path, Path]:
        session = self.session(session_id)
        folder = output_root / "Daily_Sessions" / session["session_date"]
        folder.mkdir(parents=True, exist_ok=True)
        json_path = folder / f"{session_id}.json"
        md_path = folder / f"{session_id}.md"
        clips = [dict(row) for row in self.connection.execute("""SELECT c.*, e.filename AS source_filename FROM session_clips sc
            JOIN clips c ON c.id=sc.clip_id JOIN episodes e ON e.id=c.episode_id WHERE sc.session_id=? ORDER BY e.queue_position,c.start""", (session_id,))]
        episode_counts = self.session_episode_counts(session)
        data = {"session_id": session_id, "date": session["session_date"], "status": status,
                "target": session["target"], "elapsed_seconds": round(elapsed_seconds, 1), "clips": clips,
                **episode_counts, "queue_counts": self.counts(Path(session["folder"])),
                "microbatches": batch_timings or [], "discovery_seconds": discovery_seconds,
                "reconciliation_seconds": reconciliation_seconds}
        temp = json_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(json_path)
        lines = [f"# Nightly Clipper session {session_id}", "", f"- Date: {data['date']}",
                 f"- Status: {status}", f"- Shorts target: {session['target']}",
                 f"- Validated Shorts prepared: {len(clips)}", f"- Episodes processed: {episode_counts['episodes_processed']}",
                 f"- Episodes skipped: {episode_counts['episodes_skipped']}",
                 f"- Episodes interrupted: {episode_counts['episodes_interrupted']}",
                 f"- Pending episodes: {data['queue_counts'].get('PENDING', 0)}",
                 f"- Failed episodes: {data['queue_counts'].get('FAILED_RETRYABLE', 0) + data['queue_counts'].get('FAILED_FINAL', 0)}",
                 f"- Elapsed: {elapsed_seconds/3600:.2f} hours", "",
                 "## Validated clips", ""]
        lines.extend(f"- {clip['source_filename']} · {clip['start']:.2f}–{clip['end']:.2f}s · {clip['semantic_category'] or 'heuristic'} · heuristic {clip['heuristic_score'] or 0:.2f} · semantic {clip['semantic_score'] or 0:.2f} · validated · [{Path(clip['mp4_path']).name}]({clip['mp4_path']}) · SRT: {clip['srt_path']}"
                     for clip in clips)
        md_temp = md_path.with_suffix(".md.tmp")
        md_temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        md_temp.replace(md_path)
        return json_path, md_path

    def reconcile(self, folder: Path, output_root: Path, validator) -> dict:
        checked = adopted = invalid = 0
        for episode in self.episodes():
            if Path(episode["path"]).parent != Path(folder).resolve() and Path(folder).resolve() not in Path(episode["path"]).resolve().parents:
                continue
            output_folder = _episode_output_folder(Path(episode["path"]), output_root)
            for temporary in output_folder.glob("clip_*.partial.*"):
                if temporary.is_file() and temporary.suffix in {".mp4", ".srt", ".ass"}:
                    temporary.unlink(missing_ok=True)
            manifest_path = output_folder / "analysis.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if episode["source_changed"] or manifest.get("source_size") != episode["source_size"] or manifest.get("source_mtime_ns") != episode["source_mtime_ns"]:
                continue
            valid_clips = []
            for clip in manifest.get("clips", []):
                filename = clip.get("filename") if isinstance(clip, dict) else None
                if not isinstance(filename, str) or Path(filename).name != filename or Path(filename).suffix.lower() != ".mp4":
                    invalid += 1
                    continue
                mp4 = manifest_path.parent / filename
                if mp4.resolve().parent != manifest_path.parent.resolve():
                    invalid += 1
                    continue
                srt = mp4.with_suffix(".srt")
                checked += 1
                try:
                    validator(mp4, float(clip["duration"]), bool(manifest.get("metadata", {}).get("has_audio")), srt)
                    valid_clips.append((clip, mp4, srt))
                except Exception:
                    invalid += 1
            for clip, mp4, srt in valid_clips:
                self.adopt_existing_clip(episode["id"], episode["fingerprint"], clip, mp4, srt)
                adopted += 1
            selected_count = manifest.get("candidate_selection", {}).get("selected_count", 0)
            if selected_count == 0:
                self.set_episode_status(episode["id"], "SKIPPED", "selection", error=None)
            elif len(valid_clips) < selected_count or len(valid_clips) != len(manifest.get("clips", [])):
                next_stage = "READY_TO_RENDER" if manifest.get("clips") or manifest.get("queue_stage") == "READY_TO_RENDER" else "ANALYZED"
                self.set_episode_status(episode["id"], next_stage, "analysis")
                if manifest.get("queue_stage") != next_stage:
                    manifest["queue_stage"] = next_stage
                    _write_json_atomic(manifest_path, manifest)
            elif valid_clips:
                with self.connection:
                    self.connection.execute("UPDATE episodes SET status='COMPLETED',validated_shorts=?,last_successful_stage='validation' WHERE id=?",
                                            (len(valid_clips), episode["id"]))
        return {"checked": checked, "adopted": adopted, "invalid": invalid}


def _episode_output_folder(source: Path, output_root: Path) -> Path:
    suffix = hashlib.sha1(str(source.resolve()).casefold().encode()).hexdigest()[:8]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem)
    return output_root / f"{name}_{suffix}"


def _write_json_atomic(path: Path, data: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
