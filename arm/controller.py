"""ArmController: the team's arm contract on top of the SO-101.

Commands: HOME, SORT <BIN>, STOP.
States:   READY, BUSY, DONE, ERROR.

Rules (from PLAN.md and issue #13, kept identical for the bridge in #16):
- The arm starts in ERROR ("not homed"). An operator sends HOME once to reach READY.
- SORT is accepted only while READY and only for a known bin. HOME is accepted unless BUSY.
- An accepted command reports BUSY, then DONE when its motion finishes, then READY.
- STOP cancels the remaining motion and freezes the arm: body joints hold where they are and the
  gripper keeps its last command, so a carried item is not dropped. It reports ERROR.
  Only HOME clears ERROR.
- A rejected command never moves the arm. Any motion failure freezes the arm and reports ERROR.

Threading: home(), sort(), stop() and poll() may be called from any thread, including from a
state listener. home() and sort() return at once (True if accepted); the motion runs on a worker
thread. When stop() returns, `state` is ERROR. Listeners are called in order on one notifier
thread, never on the worker.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from .backend import Backend, MockBackend, Positions, So101Backend
from .config import BINS, BODY_JOINTS, GRIPPER, ArmConfig
from .motion import MotionError, MotionStopped, hold, move_to, pause
from .poses import Poses, demo_poses, load

logger = logging.getLogger(__name__)


class ArmState(StrEnum):
    """str(state) and f"{state}" give the bare name, e.g. "READY"."""

    READY = "READY"
    BUSY = "BUSY"
    DONE = "DONE"
    ERROR = "ERROR"


# A step is ("pose", name), ("gripper", "open" | "closed"), or ("pause", seconds).
Step = tuple[str, object]
StateListener = Callable[[ArmState, "str | None"], None]

_STOP_NOTIFIER = object()


@dataclass(eq=False)
class _Command:
    name: str
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    finished: bool = False  # set under the controller lock with the command's final state


class _TrackedBackend:
    """Passes everything through, and remembers the last goal that was actually sent.

    A failure can strike in the middle of a move. The freeze that follows must keep the gripper at
    the last value really sent (e.g. part-way closed on an item), not at the previous move's goal,
    which would reopen the jaws.
    """

    def __init__(self, inner: Backend):
        self.inner = inner
        self.last_written: Positions | None = None

    def read(self) -> Positions:
        return self.inner.read()

    def write(self, goal: Positions) -> None:
        self.inner.write(goal)
        self.last_written = dict(goal)  # only after the write succeeded

    def set_torque(self, enabled: bool) -> None:
        self.inner.set_torque(enabled)

    def close(self) -> None:
        self.inner.close()


class ArmController:
    def __init__(self, backend: Backend, poses: Poses, config: ArmConfig | None = None):
        self._config = config or ArmConfig()
        self._config.validate()
        poses.validate()
        self._backend = _TrackedBackend(backend)
        self._poses = poses

        self._lock = threading.Lock()  # guards state, detail, current command, connected, last_done
        self._cmd: _Command | None = None
        self._cmd_seq = 0
        self._closed = False
        self._disconnected = False
        self._state = ArmState.ERROR
        self._detail: str | None = "not homed: send HOME"
        self._bus_lock = threading.Lock()  # held by a worker while it drives the arm, and by poll()
        self._connected = True
        self._goal: Positions | None = None  # last commanded goal for all joints (worker thread only)
        self._done_seq = 0
        self.last_done: tuple[int, str] | None = None
        self._local = threading.local()

        self._listeners: list[StateListener] = []
        self._events: queue.Queue = queue.Queue()
        self._notifier = threading.Thread(target=self._deliver, name="arm-notifier", daemon=True)
        self._notifier.start()

    @classmethod
    def from_config(cls, config: ArmConfig | None = None, *, mock: bool = False) -> "ArmController":
        """Build a controller in a safe order: check settings and poses before opening the port.

        With `mock=True` the arm is simulated; untaught poses are replaced by made-up demo poses.
        """
        config = config or ArmConfig.from_env()
        config.validate()
        poses = load(config.poses_path)
        if mock and poses.missing():
            logger.warning("poses.json is not fully taught; the mock uses made-up demo poses")
            poses = demo_poses()
        poses.validate()  # before opening the port
        if mock:
            backend: Backend = MockBackend(start={**poses.body("home"), GRIPPER: poses.grip("open")})
        else:
            backend = So101Backend(config)
        try:
            return cls(backend, poses, config)
        except BaseException:
            backend.close()
            raise

    # ----- public API -------------------------------------------------------------------------

    @property
    def state(self) -> ArmState:
        return self._state

    @property
    def detail(self) -> str | None:
        """Why the arm is in its current state (e.g. the error message), if known."""
        return self._detail

    @property
    def connected(self) -> bool:
        """False after the arm stopped answering (seen by a move or by poll())."""
        return self._connected

    @property
    def last_rejection(self) -> str | None:
        """Why this thread's last home()/sort() was rejected (None if it was accepted)."""
        return getattr(self._local, "rejection", None)

    def on_state(self, listener: StateListener) -> None:
        """Call `listener(state, detail)` on every state change, in order, on the notifier thread.

        Listeners may call home(), sort(), stop() and poll(), but not wait_idle(). Keep them short.
        """
        self._listeners.append(listener)

    def home(self) -> bool:
        """Move to the home pose, then open the gripper. Accepted unless BUSY."""
        return self._start("HOME", self.home_plan(), allowed=(ArmState.READY, ArmState.DONE, ArmState.ERROR))

    def sort(self, bin_name: str) -> bool:
        """Pick the item at the pickup spot and drop it in `bin_name`. Accepted only while READY."""
        name = str(bin_name).strip().upper()
        if name not in BINS:
            return self._reject(f"unknown bin {bin_name!r}; expected one of {', '.join(BINS)}")
        return self._start(f"SORT {name}", self.sort_plan(name), allowed=(ArmState.READY,))

    def stop(self) -> bool:
        """Cancel any motion, freeze the arm, and enter ERROR. Always accepted.

        When this returns, `state` is ERROR, unless a new command was accepted after the stopped one
        ended. The ERROR notification follows on the notifier thread.
        """
        with self._lock:
            cmd = self._cmd
            active = cmd is not None and not cmd.finished
            if active:
                cmd.cancel.set()
            else:
                # Idle: enter ERROR in the same locked step, so a command racing this STOP is rejected.
                self._transition(ArmState.ERROR, "stopped")
        if not active:
            return True

        if cmd.thread is not threading.current_thread():
            cmd.thread.join(timeout=self._config.settle_timeout_s + 2.0)
        with self._lock:
            # The worker reports "stopped" itself. If it is hung on the bus, report that now; it stays
            # cancelled, and new commands are refused until it ends. Leave alone a command accepted
            # after this one ended.
            if self._cmd is cmd and not cmd.finished:
                self._transition(ArmState.ERROR, "stopped; arm not responding")
        return True

    def poll(self) -> bool:
        """Check that the arm still answers, and return `connected`. Never raises.

        Skipped while a command is running (it reports its own failures). While idle it reads the
        joints once. A connection failure enters ERROR "connection lost: ..."; any other read
        failure enters ERROR "arm check failed: ...". A result that a newer command has overtaken
        is discarded.
        """
        if not self._bus_lock.acquire(blocking=False):
            return self._connected  # a command is using the bus
        try:
            with self._lock:
                if self._closed:
                    return False
                if self._state is ArmState.BUSY:
                    return self._connected  # accepted; its worker is about to take the bus
                seq = self._cmd_seq
            try:
                self._backend.read()
                error: BaseException | None = None
            except Exception as exc:
                error = exc
        finally:
            self._disconnect_if_closing()  # a close() that timed out while this poll held the bus
            self._bus_lock.release()

        with self._lock:
            if self._cmd_seq != seq or self._closed:
                return self._connected  # a newer command's own result wins
            if error is None:
                self._connected = True
            elif isinstance(error, ConnectionError):
                self._connected = False
                # The arm may sag or drop its item before it answers again: start fresh from where it is.
                # Safe without the bus lock: no command started (seq unchanged under _lock).
                self._goal = None
                self._backend.last_written = None
                self._transition(ArmState.ERROR, f"connection lost: {error}")
            else:
                logger.error("arm check failed: %r", error)
                self._transition(ArmState.ERROR, f"arm check failed: {error}")
            return self._connected

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until the current command finishes and its notifications are delivered.

        Returns False on timeout. Do not call from a state listener.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        cmd = self._cmd
        if cmd is not None and cmd.thread is not None:
            cmd.thread.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
            if cmd.thread.is_alive():
                return False
        return self._drain(deadline)

    def close(self, timeout: float | None = None) -> bool:
        """Stop any motion, refuse new commands, and disconnect once this controller owns the bus.

        Returns True when disconnected. If the bus is still held after `timeout` seconds (default
        settle_timeout_s + 2), e.g. by a write stuck on a hung bus, the backend is NOT touched:
        returns False, and the stuck operation disconnects as soon as it ends. Disconnecting never
        happens concurrently with a bus operation. On the real arm it turns torque off (limp arm).
        """
        with self._lock:
            if self._closed:
                return self._disconnected
            self._closed = True
            cmd = self._cmd
            active = cmd is not None and not cmd.finished
        if active:
            self.stop()
        if timeout is None:
            timeout = self._config.settle_timeout_s + 2.0
        if not self._bus_lock.acquire(timeout=timeout):
            logger.warning("close: the arm bus is still busy; it will disconnect when that operation ends")
            return False
        try:
            self._disconnect_if_closing()
        finally:
            self._bus_lock.release()
        if self._disconnected and threading.current_thread() is not self._notifier:
            self._notifier.join(timeout=1.0)  # let the last notifications (e.g. ERROR) be delivered
        return self._disconnected

    # ----- motion plans -----------------------------------------------------------------------

    def home_plan(self) -> list[Step]:
        return [("pose", "home"), ("gripper", "open")]

    def sort_plan(self, bin_name: str) -> list[Step]:
        cfg = self._config
        return [
            ("gripper", "open"),
            ("pose", "above_pick"),
            ("pose", "pick"),
            ("gripper", "closed"),
            ("pause", cfg.grip_dwell_s),
            ("pose", "above_pick"),
            ("pose", f"above_{bin_name.lower()}"),
            ("gripper", "open"),
            ("pause", cfg.release_dwell_s),
            ("pose", "home"),
        ]

    # ----- internals --------------------------------------------------------------------------

    def _reject(self, reason: str) -> bool:
        self._local.rejection = reason
        logger.info("rejected: %s", reason)
        return False

    def _start(self, command: str, plan: list[Step], allowed: tuple[ArmState, ...]) -> bool:
        with self._lock:
            previous = self._cmd
            if self._closed:
                reason = f"{command} rejected: the arm is closed"
            elif previous is not None and not previous.finished:
                reason = (f"{command} rejected while BUSY" if self._state is ArmState.BUSY else
                          f"{command} rejected: the stopped command has not ended (arm not responding)")
            elif self._state not in allowed:
                reason = f"{command} rejected while {self._state.value}"
                if self._state is ArmState.ERROR and command.startswith("SORT"):
                    reason += " (send HOME first)"
            else:
                cmd = _Command(command)
                cmd.thread = threading.Thread(target=self._run, args=(cmd, plan), name="arm-worker", daemon=True)
                self._cmd = cmd
                self._cmd_seq += 1
                self._transition(ArmState.BUSY, command)
                cmd.thread.start()
                reason = None
        if reason is not None:
            return self._reject(reason)
        self._local.rejection = None
        return True

    def _disconnect_if_closing(self) -> None:
        """Disconnect if close() was requested and no command is still running.

        Call only while holding _bus_lock, so the disconnect never overlaps a bus operation. The
        notifier stops after delivering everything already queued.
        """
        with self._lock:
            if not self._closed or self._disconnected:
                return
            if self._cmd is not None and not self._cmd.finished:
                return  # that command's worker disconnects after its final state change
            self._disconnected = True
        self._events.put(_STOP_NOTIFIER)
        try:
            self._backend.close()
        except Exception:
            logger.exception("could not disconnect cleanly")

    def _run(self, cmd: _Command, plan: list[Step]) -> None:
        try:
            self._finish(cmd, *self._drive(cmd, plan))
        finally:
            # A close() that could not get the bus (this worker held it) completes here, after this
            # command's final hold write and state change.
            with self._bus_lock:
                self._disconnect_if_closing()

    def _finish(self, cmd: _Command, failure: str | None, lost: bool) -> None:
        with self._lock:
            # Decided under the lock, so a concurrent stop() either lands before (-> ERROR here)
            # or after (stop() then sees the command finished and enters ERROR itself).
            cmd.finished = True
            if self._cmd is not cmd:
                return
            if lost:
                self._connected = False
            if failure is not None:
                self._transition(ArmState.ERROR, failure)
            elif cmd.cancel.is_set():
                self._transition(ArmState.ERROR, "stopped")
            else:
                self._connected = True
                self._done_seq += 1
                self.last_done = (self._done_seq, cmd.name)
                self._transition(ArmState.DONE, cmd.name)
                self._transition(ArmState.READY, None)

    def _drive(self, cmd: _Command, plan: list[Step]) -> tuple[str | None, bool]:
        """Run the plan on the bus. Returns (ERROR detail or None on success, connection lost)."""
        with self._bus_lock:
            try:
                for step in plan:
                    self._execute(step, cmd.cancel)
            except MotionStopped as exc:
                if exc.held is not None:
                    self._goal = exc.held
                return "stopped", False
            except MotionError as exc:  # the arm answered but a joint did not reach its goal
                logger.warning("%s failed: %s", cmd.name, exc)
                if exc.held is not None:
                    self._goal = exc.held
                return f"{cmd.name} failed: {exc}", False
            except ConnectionError as exc:  # USB, power, or bus failure
                logger.error("%s: connection lost: %s", cmd.name, exc)
                self._freeze()
                self._goal = None  # the arm may sag or be moved before it answers again
                self._backend.last_written = None
                return f"connection lost: {exc}", True
            except Exception as exc:  # a servo error, a bug, or a bad setting
                logger.exception("%s failed", cmd.name)
                self._freeze()
                return f"{cmd.name} failed: {exc}", False
        return None, False

    def _freeze(self) -> None:
        """Best-effort hold after an unexpected failure, keeping the gripper's last command.

        "Last command" is the last gripper value actually sent, which can be part-way through a
        move, not the previous move's goal (that would reopen a closing gripper).
        """
        sent = self._backend.last_written
        gripper = sent[GRIPPER] if sent else (self._goal[GRIPPER] if self._goal else None)
        try:
            self._goal = hold(self._backend, gripper)
        except Exception:
            logger.exception("could not hold position after failure")
            self._goal = None

    def _start_point(self) -> Positions:
        """Where the next move starts: the last commanded goal, where it still matches the arm.

        A body joint uses its commanded goal while it is within max_relative_target of where the
        joint really is (normal operation: no creep from gravity sag), else its present position
        (after a power cut, a push, or a fault: no jump to a stale goal). The gripper keeps its
        commanded value, so a gripper stalled on an item keeps squeezing.
        """
        present = self._backend.read()
        if self._goal is None:
            return present
        start = dict(present)
        for joint in BODY_JOINTS:
            if abs(self._goal[joint] - present[joint]) <= self._config.max_relative_target:
                start[joint] = self._goal[joint]
        start[GRIPPER] = self._goal[GRIPPER]
        return start

    def _execute(self, step: Step, cancel: threading.Event) -> None:
        kind, arg = step
        if kind == "pause":
            pause(float(arg), cancel)
            return
        start = self._start_point()
        if kind == "pose":
            target = {**start, **self._poses.body(str(arg))}
        elif kind == "gripper":
            target = {**start, GRIPPER: self._poses.grip(str(arg))}
        else:
            raise ValueError(f"unknown step {step!r}")
        self._goal = move_to(self._backend, start, target, self._config, cancel)

    def _transition(self, state: ArmState, detail: str | None) -> None:
        """Change state and queue the notification. Call with self._lock held."""
        if state is ArmState.ERROR and self._state is ArmState.ERROR and detail == self._detail:
            return  # repeated identical ERROR (e.g. a second STOP): nothing new to report
        self._state = state
        self._detail = detail
        self._events.put((state, detail))

    def _deliver(self) -> None:
        while True:
            event = self._events.get()
            try:
                if event is _STOP_NOTIFIER:
                    return
                state, detail = event
                for listener in list(self._listeners):
                    try:
                        listener(state, detail)
                    except Exception:
                        logger.exception("state listener failed")
            finally:
                self._events.task_done()

    def _drain(self, deadline: float | None) -> bool:
        """Wait until every queued notification has been delivered."""
        if threading.current_thread() is self._notifier or not self._notifier.is_alive():
            return True  # called from a listener, or after close(): nothing can deliver them
        while self._events.unfinished_tasks:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.002)
        return True
