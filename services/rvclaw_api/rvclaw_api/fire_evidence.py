"""Bounded storage for confirmed live fire-detection evidence."""

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


class FireEvidenceStore:
    def __init__(self, root: Path, retention_days: int = 30, max_records: int = 200):
        self.root = Path(root)
        self.index = self.root / "index.json"
        self.retention_days = retention_days
        self.max_records = max_records
        self._lock = threading.Lock()

    def _read(self):
        try:
            records = json.loads(self.index.read_text(encoding="utf-8"))
            return records if isinstance(records, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, records):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / "index.json.tmp"
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.index)

    def _clean(self, records, now):
        cutoff = now - timedelta(days=self.retention_days)
        kept = []
        for row in records:
            if not isinstance(row, dict):
                continue
            evidence_id = row.get("evidence_id")
            try:
                created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            except (KeyError, AttributeError, TypeError, ValueError):
                continue
            if (isinstance(evidence_id, str) and len(evidence_id) == 32
                    and all(char in "0123456789abcdef" for char in evidence_id)
                    and created.tzinfo is not None and created >= cutoff
                    and (self.root / f"{evidence_id}.jpg").is_file()):
                kept.append(row)
        kept.sort(key=lambda row: row["created_at"], reverse=True)
        kept = kept[:self.max_records]
        referenced = {row["evidence_id"] for row in kept}
        if self.root.is_dir():
            for image in self.root.glob("*.jpg"):
                if image.stem not in referenced:
                    image.unlink(missing_ok=True)
        return kept

    def list_records(self):
        with self._lock:
            records = self._read()
            kept = self._clean(records, datetime.now(timezone.utc))
            if kept != records:
                self._write(kept)
            return kept

    def save(self, image: bytes, metadata: dict):
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            evidence_id = uuid.uuid4().hex
            record = {
                "evidence_id": evidence_id,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "image_url": f"/api/fire-evidence/{evidence_id}/image.jpg",
                **metadata,
            }
            image_path = self.root / f"{evidence_id}.jpg"
            try:
                image_path.write_bytes(image)
                self._write(self._clean([record, *self._read()], datetime.now(timezone.utc)))
            except OSError:
                image_path.unlink(missing_ok=True)
                raise
            return record

    def read_image(self, evidence_id: str):
        if (not isinstance(evidence_id, str) or len(evidence_id) != 32
                or any(char not in "0123456789abcdef" for char in evidence_id)):
            return None
        with self._lock:
            records = self._read()
            kept = self._clean(records, datetime.now(timezone.utc))
            if kept != records:
                self._write(kept)
            if not any(row["evidence_id"] == evidence_id for row in kept):
                return None
            try:
                return (self.root / f"{evidence_id}.jpg").read_bytes()
            except OSError:
                return None

    def delete(self, evidence_id: str):
        if (not isinstance(evidence_id, str) or len(evidence_id) != 32
                or any(char not in "0123456789abcdef" for char in evidence_id)):
            return False
        with self._lock:
            records = self._read()
            kept = [row for row in records
                    if not isinstance(row, dict) or row.get("evidence_id") != evidence_id]
            if len(kept) == len(records):
                return False
            self._write(kept)
            (self.root / f"{evidence_id}.jpg").unlink(missing_ok=True)
            return True
