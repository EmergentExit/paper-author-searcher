"""Workers: take queued jobs from the repository, run them, and store what each run leaves.

A worker is stateless between jobs: the job and its files live in the repository and the file store, so any number
of worker threads or processes (``paper-search-api worker``) can share one queue. Each reports a heartbeat; a
running job whose worker stops reporting is queued again by whichever worker notices first.
"""

import contextlib
import logging
import secrets
import shutil
import socket
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .model import JobStatus, now
from .ports import FileStore, JobRepository, SearchOutcome, SearchRunner

logger = logging.getLogger(__name__)

ARCHIVE_KEY = 'pdfs.zip'
IDENTITY_KEY = 'identity.json'


def job_key(job_id: str, name: str) -> str:
    return f'searches/{job_id}/{name}'


def paper_key(job_id: str, paper_id: str) -> str:
    return job_key(job_id, f'papers/{paper_id}.pdf')


class SearchWorker:
    def __init__(
        self,
        repository: JobRepository,
        runner: SearchRunner,
        files: FileStore,
        scratch: Path,
        poll_seconds: float = 2.0,
        heartbeat_seconds: float = 15.0,
        stale_after_seconds: float = 120.0,
        keep_run_directories: bool = False,
        progress_interval: float = 1.0,
        worker_id: str | None = None,
        maximum_attempts: int = 0,
        housekeeping: Callable[[], Any] | None = None,
        housekeeping_seconds: float = 6 * 3600.0,
        cancel_poll_seconds: float = 2.0,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.files = files
        self.scratch = Path(scratch)
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_after_seconds = stale_after_seconds
        self.keep_run_directories = keep_run_directories
        self.progress_interval = progress_interval
        self.maximum_attempts = maximum_attempts
        self.housekeeping = housekeeping
        self.housekeeping_seconds = housekeeping_seconds
        # How often a running job's status is read, so a search its owner stopped ends within about this long.
        self.cancel_poll_seconds = cancel_poll_seconds
        self._last_housekeeping = 0.0
        self.id = worker_id or f'{socket.gethostname()}-{secrets.token_hex(4)}'
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self._last_heartbeat = 0.0

    def run_forever(self) -> None:
        """Process jobs until ``stop()``; between jobs, report alive and requeue jobs of dead workers."""
        logger.info('Worker %s started', self.id)
        try:
            while not self.stopping.is_set():
                self._heartbeat(None, force=True)
                self._keep_house()
                for job_id in self.repository.requeue_stale(self.stale_after_seconds, self.maximum_attempts):
                    logger.warning('Search %s queued again: its worker stopped reporting', job_id)
                if not self.run_once():
                    self.wakeup.wait(self.poll_seconds)
                    self.wakeup.clear()
        finally:
            self.repository.forget_worker(self.id)
            logger.info('Worker %s stopped', self.id)

    def _keep_house(self) -> None:
        """Run housekeeping now and then; a failed sweep is logged and must not stop the worker taking searches."""
        moment = time.monotonic()
        if self.housekeeping is None or (
            self._last_housekeeping and moment - self._last_housekeeping < self.housekeeping_seconds
        ):
            return
        self._last_housekeeping = moment
        try:
            self.housekeeping()
        except Exception:
            logger.exception('Housekeeping failed')

    def stop(self) -> None:
        self.stopping.set()
        self.wakeup.set()

    def wake(self) -> None:
        """Look for work now instead of at the next poll (a job was just queued in this process)."""
        self.wakeup.set()

    def run_once(self) -> bool:
        """Claim and process one queued job; False when the queue was empty."""
        job = self.repository.claim_next(self.id)
        if job is None:
            return False
        logger.info('Search %s started (attempt %s)', job.id, job.attempts)
        self._heartbeat(job.id, force=True)
        last_stage, last_write = '', 0.0

        def progress(stage: str, completed: int | None = None, total: int | None = None) -> None:
            # Counters tick per paper; write a new stage at once, a counter at most every progress_interval s.
            nonlocal last_stage, last_write
            moment = time.monotonic()
            if stage == last_stage and moment - last_write < self.progress_interval:
                return
            last_stage, last_write = stage, moment
            self.repository.update(job.id, progress={'stage': stage, 'completed': completed, 'total': total})

        work = Path(tempfile.mkdtemp(prefix=f'{job.id}-', dir=self._scratch()))
        outcome: SearchOutcome | None = None
        # A beat of its own while the job runs: one slow step (a large download) must not look like a dead worker.
        done = threading.Event()
        stop = threading.Event()
        beat = threading.Thread(target=self._beat, args=(job.id, done, stop), name=f'heartbeat-{job.id}', daemon=True)
        beat.start()
        try:
            checkpoint = None
            if job.selection and job.identity_checkpoint:
                checkpoint = work / 'identity.json'
                if not self.files.fetch(job.identity_checkpoint, checkpoint):
                    raise RuntimeError('The identity checkpoint of this search is missing from the file store')
            outcome = self.runner.run(job, checkpoint, progress, stop)
            if stop.is_set() or self._cancelled(job.id):
                logger.info('Search %s stopped by its owner', job.id)
            else:
                self._finish(job.id, outcome, job.cost_usd, job.llm_cost_usd)
        except Exception as exc:
            logger.exception('Search %s failed', job.id)
            self.repository.transition(
                job.id,
                JobStatus.RUNNING,
                status=JobStatus.FAILED,
                progress=None,
                finished_at=now(),
                error={'code': 'internal_error', 'message': 'The search failed unexpectedly', 'details': [str(exc)]},
            )
        finally:
            done.set()
            beat.join()
            if not self.keep_run_directories:
                shutil.rmtree(work, ignore_errors=True)
                if outcome is not None and outcome.run_directory is not None:
                    shutil.rmtree(outcome.run_directory, ignore_errors=True)
                    # The job's folder of runs, once its last run is gone.
                    with contextlib.suppress(OSError):
                        outcome.run_directory.parent.rmdir()
            self._heartbeat(None, force=True)
        return True

    def _finish(self, job_id: str, outcome: SearchOutcome, spent_before: float, llm_spent_before: float = 0.0) -> None:
        """Store the run's files first, then record the outcome, so a caller never sees a link to a missing file."""
        checkpoint_key = None
        if outcome.identity_checkpoint is not None:
            checkpoint_key = job_key(job_id, IDENTITY_KEY)
            self.files.put(checkpoint_key, outcome.identity_checkpoint, 'application/json')
        if outcome.files:
            for paper_id, path in outcome.files.items():
                self.files.put(paper_key(job_id, paper_id), path, 'application/pdf')
            archive = self._archive(job_id, outcome.files)
            self.files.put(job_key(job_id, ARCHIVE_KEY), archive, 'application/zip')
            archive.unlink()
        changes: dict[str, Any] = {
            'status': outcome.status,
            'progress': None,
            'finished_at': now(),
            'result': outcome.result,
            'error': outcome.error,
            'cost_usd': round(spent_before + outcome.cost_usd, 6),
            'llm_cost_usd': round(llm_spent_before + outcome.llm_cost_usd, 6),
        }
        if outcome.candidates:
            changes['candidates'] = outcome.candidates
        if checkpoint_key:
            changes['identity_checkpoint'] = checkpoint_key
        # Only a job still running takes the outcome: one stopped meanwhile stays stopped.
        if self.repository.transition(job_id, JobStatus.RUNNING, **changes) is None:
            logger.info('Search %s was stopped before its outcome was recorded', job_id)
            return
        logger.info('Search %s finished as %s ($%.4f)', job_id, outcome.status, outcome.cost_usd)

    def _archive(self, job_id: str, files: dict[str, Path]) -> Path:
        handle, name = tempfile.mkstemp(prefix=f'{job_id}-', suffix='.zip', dir=self._scratch())
        archive = Path(name)
        with open(handle, 'wb') as raw, zipfile.ZipFile(raw, 'w', compression=zipfile.ZIP_STORED) as bundle:
            # PDFs are already compressed; one entry per distinct file (editions often share one).
            for path in dict.fromkeys(files.values()):
                bundle.write(path, path.name)
        return archive

    def _scratch(self) -> Path:
        self.scratch.mkdir(parents=True, exist_ok=True)
        return self.scratch

    def _beat(self, job_id: str, done: threading.Event, stop: threading.Event | None = None) -> None:
        """Report alive every heartbeat; in between, notice the owner stopping the job and set ``stop``."""
        last_beat = time.monotonic()
        while not done.wait(min(self.heartbeat_seconds, self.cancel_poll_seconds)):
            if stop is not None and not stop.is_set() and self._cancelled(job_id):
                logger.info('Search %s is being stopped', job_id)
                stop.set()
            if time.monotonic() - last_beat < self.heartbeat_seconds:
                continue
            last_beat = time.monotonic()
            try:
                self.repository.heartbeat(self.id, job_id)
            except Exception:
                logger.exception('Heartbeat of worker %s failed', self.id)

    def _cancelled(self, job_id: str) -> bool:
        try:
            job = self.repository.get(job_id)
        except Exception:
            logger.exception('Status of search %s could not be read', job_id)
            return False
        return job is not None and job.status == JobStatus.CANCELLED

    def _heartbeat(self, job_id: str | None, force: bool = False) -> None:
        moment = time.monotonic()
        if force or moment - self._last_heartbeat >= self.heartbeat_seconds:
            self._last_heartbeat = moment
            self.repository.heartbeat(self.id, job_id)
