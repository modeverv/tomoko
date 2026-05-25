from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from server.shared.models import (
    WorldObservationNormalizedBatch,
    WorldObservationParseIssue,
)
from server.world_observations.raw_markdown import read_raw_markdown
from server.world_observations.store import WorldObservationStore


class WorldObservationNormalizerProtocol(Protocol):
    async def normalize(self, document) -> WorldObservationNormalizedBatch: ...


@dataclass(frozen=True)
class IngestFileResult:
    path: Path
    action: str
    archived_path: Path | None = None
    failed_path: Path | None = None
    issues: tuple[WorldObservationParseIssue, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class IngestRunResult:
    processed_count: int
    archived_count: int
    failed_count: int
    skipped_count: int
    results: tuple[IngestFileResult, ...]


class WorldObservationIngestor:
    def __init__(
        self,
        *,
        store: WorldObservationStore,
        normalizer: WorldObservationNormalizerProtocol,
        archive_root: str | Path,
        failed_root: str | Path,
    ) -> None:
        self.store = store
        self.normalizer = normalizer
        self.archive_root = Path(archive_root)
        self.failed_root = Path(failed_root)

    async def ingest_path(
        self,
        path: str | Path,
        *,
        dry_run: bool = False,
    ) -> IngestFileResult:
        file_path = Path(path)
        document = read_raw_markdown(file_path)
        checksum = sha256_file(file_path)
        if not document.is_valid:
            if dry_run:
                return IngestFileResult(
                    path=file_path,
                    action="would_fail_validation",
                    issues=document.issues,
                )
            return self._move_failed(file_path, document.issues)

        if dry_run:
            return IngestFileResult(path=file_path, action="would_ingest")

        db_document, inserted = await self.store.import_raw_document_once(
            document,
            checksum=checksum,
        )
        if not inserted and db_document.status == "completed":
            archived_path = self._archive(file_path, document.metadata.observed_at)
            return IngestFileResult(
                path=file_path,
                action="duplicate_archived",
                archived_path=archived_path,
            )

        try:
            await self.store.mark_document_status(db_document.id, "normalizing")
            batch = await self.normalizer.normalize(document)
            if not batch.items:
                await self.store.mark_document_status(db_document.id, "failed")
                return self._move_failed(file_path, batch.trace.issues)
            await self.store.save_normalized_batch(db_document.id, batch)
        except Exception as exc:
            await self.store.mark_document_status(db_document.id, "failed")
            issue = WorldObservationParseIssue(
                field="ingest",
                message=f"{type(exc).__name__}: {exc}",
            )
            return self._move_failed(file_path, (issue,))

        archived_path = self._archive(file_path, document.metadata.observed_at)
        return IngestFileResult(
            path=file_path,
            action="archived",
            archived_path=archived_path,
        )

    async def ingest_directory(
        self,
        path: str | Path,
        *,
        dry_run: bool = False,
    ) -> IngestRunResult:
        files = sorted(Path(path).glob("*.md"))
        results = tuple(
            [await self.ingest_path(file_path, dry_run=dry_run) for file_path in files]
        )
        return IngestRunResult(
            processed_count=len(results),
            archived_count=sum(1 for result in results if result.archived_path),
            failed_count=sum(1 for result in results if result.failed_path),
            skipped_count=sum(1 for result in results if result.action.startswith("would")),
            results=results,
        )

    def _archive(self, file_path: Path, observed_at: datetime) -> Path:
        archive_dir = self.archive_root / observed_at.date().isoformat()
        archive_dir.mkdir(parents=True, exist_ok=True)
        destination = _unique_destination(archive_dir / file_path.name)
        shutil.move(str(file_path), destination)
        return destination

    def _move_failed(
        self,
        file_path: Path,
        issues: tuple[WorldObservationParseIssue, ...],
    ) -> IngestFileResult:
        failed_dir = self.failed_root / datetime.now(UTC).date().isoformat()
        failed_dir.mkdir(parents=True, exist_ok=True)
        destination = _unique_destination(failed_dir / file_path.name)
        shutil.move(str(file_path), destination)
        sidecar = destination.with_name(f"{destination.name}.error.json")
        sidecar.write_text(
            json.dumps(
                {"issues": [issue.to_json() for issue in issues]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return IngestFileResult(
            path=file_path,
            action="failed",
            failed_path=destination,
            issues=tuple(issues),
        )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create unique destination for {path}")
