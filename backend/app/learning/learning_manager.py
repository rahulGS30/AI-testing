from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


class LearningStoreCorruptionError(RuntimeError):
    pass


class JSONLearningManager:
    SCHEMA_VERSION = 1

    def __init__(self, learning_directory: str | Path | None = None) -> None:
        root = (
            Path(learning_directory)
            if learning_directory
            else Path(__file__).resolve().parents[3] / "learning"
        )
        self.learning_directory = root
        self.experiences_directory = root / "experiences"
        self.runs_directory = root / "runs"
        self.strategies_directory = root / "strategies"
        for directory in (
            self.experiences_directory,
            self.runs_directory,
            self.strategies_directory,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.experiences_path = self.experiences_directory / "experiences.json"
        self.index_path = self.experiences_directory / "experience_index.json"
        self.strategy_catalog_path = self.strategies_directory / "strategy_catalog.json"
        self._lock = threading.RLock()
        self._initialize()

    def _initialize(self) -> None:
        for path, default in (
            (self.experiences_path, {"schema_version": self.SCHEMA_VERSION, "experiences": []}),
            (self.index_path, {"schema_version": self.SCHEMA_VERSION, "entries": []}),
            (self.strategy_catalog_path, {"schema_version": self.SCHEMA_VERSION, "strategies": []}),
        ):
            if not path.exists():
                self._write_atomic(path, default)
        database = self._read_json(
            self.experiences_path,
            {"schema_version": self.SCHEMA_VERSION, "experiences": []},
        )
        if not isinstance(database.get("experiences"), list):
            raise LearningStoreCorruptionError(
                "The experience database has an invalid 'experiences' collection."
            )
        try:
            index = self._read_json(self.index_path, {})
            valid_index = isinstance(index.get("entries"), list)
        except LearningStoreCorruptionError:
            valid_index = False
        if not valid_index:
            self._rebuild_index(database["experiences"])

    def _read_json(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return default
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise LearningStoreCorruptionError(
                f"Could not read {path.name}; existing data was preserved."
            ) from exc
        if not isinstance(payload, dict):
            raise LearningStoreCorruptionError(
                f"{path.name} must contain a JSON object; existing data was preserved."
            )
        return payload

    @contextmanager
    def _file_lock(self, path: Path) -> Iterator[None]:
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+b") as lock_file:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                unlock = lambda: msvcrt.locking(
                    lock_file.fileno(), msvcrt.LK_UNLCK, 1
                )
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                unlock = lambda: fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            try:
                yield
            finally:
                unlock()

    def _write_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def validate_experience(self, experience: dict[str, Any]) -> bool:
        required = {
            "experience_id", "run_id", "persona", "journey_objective",
            "attempt_number", "strategy_id", "strategy_description",
            "page_context", "application_context", "observed_state",
            "evidence_references", "previous_failure_analysis", "action_details",
            "execution_result", "failure_category", "root_cause",
            "learning_eligible", "outcome", "confidence", "validation_status",
            "timestamp",
        }
        return required.issubset(experience)

    def store_experience(self, experience: dict[str, Any]) -> dict[str, Any]:
        record = dict(experience)
        record.setdefault("experience_id", "EXP-" + uuid.uuid4().hex[:12])
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        record.setdefault("schema_version", self.SCHEMA_VERSION)
        if not self.validate_experience(record):
            raise ValueError("Experience does not match the required schema.")

        with self._lock, self._file_lock(self.experiences_path):
            database = self._read_json(
                self.experiences_path,
                {"schema_version": self.SCHEMA_VERSION, "experiences": []},
            )
            experiences = database.get("experiences")
            if not isinstance(experiences, list):
                raise LearningStoreCorruptionError(
                    "The experience database has an invalid collection; data was preserved."
                )
            existing = next(
                (
                    item
                    for item in experiences
                    if item.get("experience_id") == record["experience_id"]
                    or (
                        item.get("run_id") == record["run_id"]
                        and item.get("attempt_number") == record["attempt_number"]
                    )
                ),
                None,
            )
            if existing is None:
                experiences.append(record)
            else:
                record["experience_id"] = existing["experience_id"]
                existing.update(record)
                record = existing
            database["schema_version"] = self.SCHEMA_VERSION
            self._write_atomic(self.experiences_path, database)
            self._rebuild_index(experiences)
        return record

    def _index_entry(self, experience: dict[str, Any]) -> dict[str, Any]:
        application = experience.get("application_context", {})
        action = experience.get("action_details", {})
        page = experience.get("page_context", {})
        if isinstance(action, list):
            actions = action
        else:
            actions = [action]
        return {
            "experience_id": experience.get("experience_id"),
            "application_id": application.get("application_id") if isinstance(application, dict) else application,
            "website": application.get("website", "") if isinstance(application, dict) else "",
            "page_url": page.get("url", "") if isinstance(page, dict) else str(page),
            "page_title": page.get("title", "") if isinstance(page, dict) else "",
            "journey_objective": experience.get("journey_objective", ""),
            "action_types": [item.get("action") for item in actions if isinstance(item, dict)],
            "locator_strategies": [item.get("locator_strategy") for item in actions if isinstance(item, dict) and item.get("locator_strategy")],
            "failure_category": experience.get("failure_category"),
            "strategy_id": experience.get("strategy_id"),
            "outcome": experience.get("outcome"),
            "validation_status": experience.get("validation_status"),
            "learning_eligible": experience.get("learning_eligible", False),
        }

    def _rebuild_index(self, experiences: list[dict[str, Any]]) -> None:
        entries = [self._index_entry(item) for item in experiences]
        with self._file_lock(self.index_path):
            self._write_atomic(
                self.index_path,
                {"schema_version": self.SCHEMA_VERSION, "entries": entries},
            )

    def _indexed_candidates(self) -> list[dict[str, Any]]:
        try:
            index = self._read_json(self.index_path, {"entries": []})
            entries = index.get("entries", [])
            if isinstance(entries, list):
                return [item for item in entries if isinstance(item, dict)]
        except LearningStoreCorruptionError:
            pass
        experiences = self.get_experiences()
        self._rebuild_index(experiences)
        return [self._index_entry(item) for item in experiences]

    def get_experiences(self) -> list[dict[str, Any]]:
        database = self._read_json(
            self.experiences_path,
            {"schema_version": self.SCHEMA_VERSION, "experiences": []},
        )
        experiences = database.get("experiences", [])
        if not isinstance(experiences, list):
            raise LearningStoreCorruptionError(
                "The experience database has an invalid collection; data was preserved."
            )
        return experiences

    @staticmethod
    def _matches_page(entry: dict[str, Any], page_context: str | dict[str, Any]) -> bool:
        if isinstance(page_context, str):
            query = page_context.casefold()
        else:
            query = " ".join(
                str(page_context.get(key, ""))
                for key in ("url", "title", "page_type", "component")
            ).casefold()
        if not query.strip():
            return True
        indexed = " ".join(
            str(entry.get(key, ""))
            for key in ("page_url", "page_title", "website")
        ).casefold()
        tokens = {token for token in query.split() if len(token) > 3}
        return not tokens or any(token in indexed for token in tokens)

    def search_experiences(
        self,
        application_id: str | None = None,
        journey_objective: str | None = None,
        page_context: str | dict[str, Any] | None = None,
        action_type: str | None = None,
        failure_category: str | None = None,
        strategy_id: str | None = None,
        outcome: str | None = None,
        validated_only: bool = True,
    ) -> list[dict[str, Any]]:
        candidates = []
        for entry in self._indexed_candidates():
            if application_id and entry.get("application_id") != application_id:
                continue
            if journey_objective and journey_objective.casefold() not in str(entry.get("journey_objective", "")).casefold():
                continue
            if page_context and not self._matches_page(entry, page_context):
                continue
            if action_type and action_type not in entry.get("action_types", []):
                continue
            if failure_category and entry.get("failure_category") != failure_category:
                continue
            if strategy_id and entry.get("strategy_id") != strategy_id:
                continue
            if outcome and entry.get("outcome") != outcome:
                continue
            if validated_only and entry.get("validation_status") != "VALIDATED":
                continue
            candidates.append(entry.get("experience_id"))
        selected = set(candidates)
        return [
            item for item in self.get_experiences()
            if item.get("experience_id") in selected
        ]

    def get_similar_experiences(
        self,
        current_failure: dict[str, Any],
        current_page_context: dict[str, Any] | str,
        current_action: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        application_id = current_failure.get("application_id")
        journey = current_failure.get("journey_objective")
        category = current_failure.get("failure_category")
        action_type = current_action.get("action") if current_action else None
        ranked = []
        for entry in self._indexed_candidates():
            score = 0
            if application_id and entry.get("application_id") == application_id:
                score += 8
            if journey and journey.casefold() in str(entry.get("journey_objective", "")).casefold():
                score += 5
            if category and entry.get("failure_category") == category:
                score += 4
            if action_type and action_type in entry.get("action_types", []):
                score += 2
            if self._matches_page(entry, current_page_context):
                score += 3
            if entry.get("outcome") == "SUCCESS" and entry.get("learning_eligible"):
                score += 2
            if entry.get("validation_status") == "VALIDATED":
                score += 2
            if score > 0:
                ranked.append((score, entry.get("experience_id")))
        ordered_ids = [
            experience_id
            for _, experience_id in sorted(ranked, reverse=True)[:max(0, limit)]
        ]
        selected = {
            item.get("experience_id"): item for item in self.get_experiences()
        }
        return [selected[experience_id] for experience_id in ordered_ids if experience_id in selected]

    def get_strategy_history(self, application_id: str, strategy_id: str) -> list[dict[str, Any]]:
        return self.search_experiences(
            application_id=application_id,
            strategy_id=strategy_id,
            validated_only=False,
        )

    def get_successful_recovery_strategies(
        self,
        application_id: str,
        journey_objective: str,
        page_context: str | dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        results = self.search_experiences(
            application_id=application_id,
            journey_objective=journey_objective,
            page_context=page_context,
            outcome="SUCCESS",
            validated_only=True,
        )
        return [item for item in results if item.get("learning_eligible")]

    def save_run(self, run_id: str, run_payload: dict[str, Any]) -> Path:
        path = self.runs_directory / f"run_{run_id}.json"
        with self._lock, self._file_lock(path):
            existing = self._read_json(path, {})
            merged = dict(existing)
            merged.update(run_payload)
            merged["schema_version"] = self.SCHEMA_VERSION
            merged.setdefault("run_id", run_id)
            old_attempts = existing.get("attempts", [])
            new_attempts = run_payload.get("attempts", [])
            if isinstance(old_attempts, list) and isinstance(new_attempts, list):
                attempts_by_number = {
                    item.get("attempt_number"): dict(item)
                    for item in old_attempts
                    if isinstance(item, dict) and item.get("attempt_number") is not None
                }
                for item in new_attempts:
                    if not isinstance(item, dict):
                        continue
                    number = item.get("attempt_number")
                    previous = attempts_by_number.get(number, {})
                    previous.update(item)
                    attempts_by_number[number] = previous
                merged["attempts"] = [attempts_by_number[key] for key in sorted(attempts_by_number)]
            self._write_atomic(path, merged)
        return path

    def update_experience(
        self, experience_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        with self._lock, self._file_lock(self.experiences_path):
            database = self._read_json(
                self.experiences_path,
                {"schema_version": self.SCHEMA_VERSION, "experiences": []},
            )
            experiences = database.get("experiences", [])
            if not isinstance(experiences, list):
                raise LearningStoreCorruptionError(
                    "The experience database has an invalid collection; data was preserved."
                )
            for experience in experiences:
                if experience.get("experience_id") == experience_id:
                    experience.update(updates)
                    self._write_atomic(self.experiences_path, database)
                    self._rebuild_index(experiences)
                    return experience
        return None
