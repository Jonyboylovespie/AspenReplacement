"""One refresh coordinator per application process, including under Gunicorn."""
import logging
import threading

logger = logging.getLogger(__name__)


class RefreshRuntime:
    def __init__(self, interval, auto_resume=True):
        self.interval = interval
        self.auto_resume = auto_resume
        self.lock = threading.RLock()
        self.stores = {}
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True, name="aspen-refresh")
                self.thread.start()

    def add(self, subject, factory):
        with self.lock:
            if subject not in self.stores:
                store = factory()
                self.stores[subject] = store
                if self.auto_resume:
                    threading.Thread(target=store.resume_session, daemon=True,
                                     name="aspen-resume").start()
            return self.stores[subject]

    def tick(self):
        with self.lock:
            stores = list(self.stores.values())
        for store in stores:
            with store.lock:
                ready = store.client is not None and not store.needs_auth and not store.sign_in.view()["active"]
            if ready:
                store.start_refresh()

    def _run(self):
        while not self.stopped.wait(self.interval):
            try:
                self.tick()
            except Exception:
                logger.exception("Background Aspen refresh failed")

    def stop(self):
        self.stopped.set()
        with self.lock:
            stores = list(self.stores.values())
        for store in stores:
            store.sign_in.cancel()
        if self.thread:
            self.thread.join(timeout=5)
