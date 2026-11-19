"""本机 Web 控制台的子进程任务管理。

设计取舍：
* 只用标准库 ``subprocess`` + 线程，不引入任务队列或 Celery；
* 每个任务保留最后 ``MAX_LINES`` 行，溢出丢最早的，但序号连续，
  前端凭 ``since`` 增量拉取，不会重复也不会错位；
* 日志在入队时就地脱敏，密钥不会经由子进程 stdout 泄漏到页面。
"""

from __future__ import annotations

import os
import subprocess
import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_LINES = 4000
"""单个任务保留的日志行数上限。"""


def _clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


@dataclass
class Job:
    """一个正在跑或已结束的 CLI 阶段。"""

    id: str
    stage: str
    label: str
    argv: list[str]
    cwd: Path
    started_at: str = field(default_factory=_clock)
    status: str = "running"  # running | succeeded | failed | stopped
    returncode: int | None = None
    ended_at: str | None = None
    emitted: int = 0
    lines: deque[dict[str, object]] = field(default_factory=lambda: deque(maxlen=MAX_LINES))
    proc: subprocess.Popen[str] | None = None
    pumps: list[threading.Thread] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- 日志 ---------------------------------------------------------- #
    def add_line(self, stream: str, text: str, scrub: Callable[[str], str]) -> None:
        with self.lock:
            self.emitted += 1
            self.lines.append({"n": self.emitted, "stream": stream, "text": scrub(text)})

    def snapshot(self, since: int = 0) -> dict[str, object]:
        with self.lock:
            rows = [row for row in self.lines if int(row["n"]) > since]
            return {
                "id": self.id,
                "stage": self.stage,
                "label": self.label,
                "command": " ".join(self.argv),
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "status": self.status,
                "returncode": self.returncode,
                "cursor": self.emitted,
                "dropped": max(0, self.emitted - len(self.lines)),
                "lines": rows,
            }

    def summary(self) -> dict[str, object]:
        with self.lock:
            tail = self.lines[-1]["text"] if self.lines else ""
        return {
            "id": self.id,
            "stage": self.stage,
            "label": self.label,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "returncode": self.returncode,
            "lines": self.emitted,
            "tail": tail,
        }


class JobManager:
    """进程内任务表。只服务本机单用户，因此不做持久化。"""

    def __init__(self, scrub: Callable[[str], str] | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._scrub = scrub or (lambda text: text)

    # ---- 查询 ---------------------------------------------------------- #
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 12) -> list[dict[str, object]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)
        return [job.summary() for job in jobs[:limit]]

    def running(self, stage: str | None = None) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [j for j in jobs if j.status == "running" and (stage is None or j.stage == stage)]

    def is_running(self, stage: str) -> bool:
        return bool(self.running(stage))

    # ---- 启停 ---------------------------------------------------------- #
    def start(
        self,
        *,
        stage: str,
        label: str,
        argv: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        on_done: Callable[[Job], None] | None = None,
    ) -> Job:
        """启动一个 CLI 子进程；调用方保证 argv 已白名单校验。"""
        job = Job(id="job_" + uuid.uuid4().hex[:10], stage=stage, label=label,
                  argv=list(argv), cwd=Path(cwd))
        with self._lock:
            self._jobs[job.id] = job

        child_env = dict(os.environ if env is None else env)
        child_env.setdefault("PYTHONUNBUFFERED", "1")
        proc = subprocess.Popen(  # noqa: S603 - 参数由白名单阶段表构造，无 shell
            job.argv,
            cwd=str(job.cwd),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            errors="replace",
        )
        job.proc = proc

        for pipe, stream in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
            if pipe is not None:
                pump = threading.Thread(
                    target=self._pump, args=(job, pipe, stream), daemon=True
                )
                job.pumps.append(pump)
                pump.start()
        threading.Thread(target=self._wait, args=(job, on_done), daemon=True).start()
        return job

    def stop(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.proc is None or job.status != "running":
            return False
        job.proc.terminate()
        threading.Timer(3.0, self._kill_if_alive, args=(job,)).start()
        return True

    @staticmethod
    def _kill_if_alive(job: Job) -> None:
        if job.proc is not None and job.proc.poll() is None:
            job.proc.kill()

    # ---- 内部线程 ------------------------------------------------------ #
    def _pump(self, job: Job, pipe, stream: str) -> None:
        try:
            for raw in iter(pipe.readline, ""):
                job.add_line(stream, raw.rstrip("\n"), self._scrub)
        finally:
            try:
                pipe.close()
            except OSError:  # pragma: no cover - 关闭失败不影响任务状态
                pass

    def _wait(self, job: Job, on_done: Callable[[Job], None] | None = None) -> None:
        assert job.proc is not None
        rc = job.proc.wait()
        for pump in job.pumps:
            pump.join(timeout=2.0)  # 管道 EOF 后读取线程自行退出，这里只等它写完最后一行
        with job.lock:
            job.returncode = rc
            job.ended_at = _clock()
            if job.status != "stopped":
                job.status = "succeeded" if rc == 0 else "failed"
        if on_done is not None:
            try:
                on_done(job)
            except Exception:  # noqa: BLE001 - 回调失败不能影响任务状态
                pass


__all__ = ["Job", "JobManager", "MAX_LINES"]
