from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class TaskStatus:
    task_id: str
    kind: str
    user_id: str
    percent: float = 0.0
    message: str = ""
    state: str = "running"  # running|done|error
    error: Optional[str] = None
    started_at: float = 0.0
    finished_at: Optional[float] = None


class TaskManager:
    def __init__(self, *, ttl_seconds: float = 60 * 30) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskStatus] = {}

    def start(self, *, kind: str, user_id: str, target: Callable[[Callable[[float, str], None]], None]) -> str:
        task_id = uuid.uuid4().hex
        status = TaskStatus(task_id=task_id, kind=kind, user_id=user_id, started_at=time.time())

        def set_progress(p: float, msg: str) -> None:
            with self._lock:
                s = self._tasks.get(task_id)
                if not s or s.state != "running":
                    return
                s.percent = max(0.0, min(100.0, float(p)))
                s.message = msg

        def runner() -> None:
            try:
                set_progress(0.0, "Démarrage…")
                target(set_progress)
                set_progress(100.0, "Terminé")
                with self._lock:
                    s = self._tasks.get(task_id)
                    if s:
                        s.state = "done"
                        s.finished_at = time.time()
            except Exception as e:
                with self._lock:
                    s = self._tasks.get(task_id)
                    if s:
                        s.state = "error"
                        s.error = str(e)
                        s.finished_at = time.time()

        with self._lock:
            self._tasks[task_id] = status

        thread = threading.Thread(target=runner, name=f"task-{kind}-{task_id}", daemon=True)
        thread.start()

        # best-effort cleanup
        self.cleanup()
        return task_id

    def get(self, task_id: str) -> Optional[TaskStatus]:
        with self._lock:
            return self._tasks.get(task_id)

    def cleanup(self) -> None:
        cutoff = time.time() - self._ttl_seconds
        with self._lock:
            to_delete = [tid for tid, s in self._tasks.items() if (s.finished_at or s.started_at) < cutoff]
            for tid in to_delete:
                self._tasks.pop(tid, None)
