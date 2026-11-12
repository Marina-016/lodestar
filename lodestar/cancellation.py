"""Interrupt local waiting for read-only IO; callbacks stay on the caller thread."""
import queue
import threading
import time


def interruptible(operation, *, check_cancel, on_text=None, timeout_s=120, on_finished=None):
    """Only network/read-only work belongs in operation, never SQLite or host tools.

    A provider may finish an already-issued request after local cancellation.
    Its late output is discarded and cannot execute tools or write conversation data.
    """
    from lodestar.llm import LLMError
    events = queue.Queue(maxsize=128)
    abandoned = threading.Event()

    def put(kind, value):
        while not abandoned.is_set():
            try:
                events.put((kind, value), timeout=.05)
                return
            except queue.Full:
                pass
        raise LLMError('Local wait ended', code='cancelled', role='conversation')

    def work():
        try:
            result = operation(lambda text: put('text', text))
            put('result', result)
        except Exception as error:
            if not abandoned.is_set():
                try:
                    put('error', error)
                except LLMError:
                    pass
        finally:
            if on_finished:
                on_finished()

    try:
        check_cancel()
    except Exception:
        if on_finished:
            on_finished()
        raise
    worker = threading.Thread(target=work, name='lodestar-read-io', daemon=True)
    try:
        worker.start()
    except Exception:
        if on_finished:
            on_finished()
        raise
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            check_cancel()
            if time.monotonic() >= deadline:
                raise LLMError('Response wait exceeded budget', code='timeout', role='conversation')
            try:
                kind, value = events.get(timeout=.05)
            except queue.Empty:
                continue
            check_cancel()
            if kind == 'text':
                if on_text:
                    on_text(value)
            elif kind == 'error':
                raise value
            else:
                return value
    finally:
        abandoned.set()
