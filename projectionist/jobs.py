"""Background jobs that can be called off.

The desktop app runs each tab's heavy request on a thread (App.run). When its answer is no longer wanted - the
tab asked again, another database was loaded, the window is closing - the job is cancelled. Python can't stop a
thread from outside, so the long loops in recommend, costars, insights and catalog call check() every so often:
in a job that has been cancelled, check() raises Cancelled and the work stops there. Outside a job (the tests,
the ask command line and server) check() finds no job and returns at once, so the loops pay next to nothing.

    job = Job()
    with running(job):          # (App.run does this on the job's thread)
        work()                  # raises Cancelled at its next check() once job.cancel() has been called
"""

from __future__ import annotations

import threading


class Cancelled(BaseException):
    """The job was called off. A BaseException, like KeyboardInterrupt, so that an `except Exception` meant for
    real errors doesn't swallow it and carry on working."""


class Job:
    """One background job. cancel() calls it off; `key` is what App.run was given (None if nothing)."""

    __slots__ = ("key", "cancelled", "ended", "_on_cancel")

    def __init__(self, key=None):
        self.key = key
        self.cancelled = False       # set by cancel(): the job stops at its next check()
        self.ended = False           # its answer has been handed over: too late to call it off
        self._on_cancel = None       # App.run's tidying up (the status line, its list of jobs)

    def cancel(self):
        """Call the job off: it stops at its next check point, and an answer that still comes is dropped. Does
        nothing once the job has ended. (For the app's jobs: call it on the window's thread.)"""
        if self.cancelled or self.ended:
            return
        self.cancelled = True
        callback, self._on_cancel = self._on_cancel, None
        if callback is not None:
            callback()

    def __repr__(self):
        state = "cancelled" if self.cancelled else "ended" if self.ended else "running"
        return f"<Job {self.key!r} {state}>"


class _Current(threading.local):
    job: Job | None = None          # the job this thread is doing, if any


_current = _Current()


def check():
    """Stop here if this thread's job has been cancelled (raises Cancelled). Cheap - a lookup and a test - but
    still a call: put it where a loop has done a few milliseconds' work (every film, every pass, every search),
    not in the innermost loop. Outside a job it does nothing."""
    job = _current.job
    if job is not None and job.cancelled:
        raise Cancelled()


def current() -> Job | None:
    """The job this thread is doing (None outside a job)."""
    return _current.job


def cancelled() -> bool:
    """True when this thread's job has been cancelled (for code that must tidy up rather than raise)."""
    job = _current.job
    return job is not None and job.cancelled


class running:
    """`with running(job):` - make `job` this thread's job for check() while the block runs."""

    def __init__(self, job: Job):
        self.job = job
        self._previous = None

    def __enter__(self) -> Job:
        self._previous = _current.job
        _current.job = self.job
        return self.job

    def __exit__(self, *exc):
        _current.job = self._previous
        return False
