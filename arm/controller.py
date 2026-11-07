"""ArmController: the team's arm contract on top of the SO-101.

Commands: HOME, SORT RECYCLE, SORT TRASH, STOP.
States:   READY, BUSY, DONE, ERROR.

Rules (from PLAN.md and issue #13, kept identical for the bridge in #16):
- The arm starts in ERROR ("not homed"). An operator sends HOME once to reach READY.
- SORT is accepted only while READY and only for a known bin. HOME is accepted unless BUSY.
- An accepted command reports BUSY, then DONE when its motion finishes, then READY.
- STOP cancels the remaining motion, holds the arm where it is, and reports ERROR.
  Only HOME clears ERROR.
- A rejected command never moves the arm. Any motion failure holds the arm and reports ERROR.

Threading: home(), sort() and stop() may be called from any thread. home() and sort() return at once
(True if accepted) and the motion runs on a worker thread. stop() interrupts the motion within one
control step and returns after the arm holds and ERROR has been reported.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from enum import Enum

from .backend import Backend, Positions
from .config import BINS, GRIPPER, ArmConfig
from .motion import MotionError, MotionStopped, hold, move_to, pause
from .poses import Poses

logger = logging.getLogger(__name__)


class ArmState(str, Enum):
    READY = "READY"
    BUSY = "BUSY"
    DONE = "DONE"
    ERROR = "ERROR"


# A step is ("pose", name), ("gripper", "open" | "closed"), or ("pause", seconds).
Step = tuple[str, object]
StateListener = Callable[[ArmState, "str | None"], None]


class ArmController:
    def __init__(self, backend: Backend, poses: Poses, config: ArmConfig | None = None):
        poses.validate()
        self._backend = backend
        self._poses = poses
        self._config = config or ArmConfig()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._state = ArmState.ERROR
        self._detail: str | None = "not homed: send HOME"
        self._listeners: list[StateListener] = []
        self._goal: Positions | None = None  # last commanded goal for all joints
        self._bus_lock = threading.Lock()  # held by the worker for a whole command, and by poll()
        self._connected = True
        self._done_seq = 0
        self.last_done: tuple[int, str] | None = None
        self.last_rejection: str | None = None

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

    def poll(self) -> bool:
        """Check that the arm still answers, and return `connected`. Never blocks a move.

        While a command runs, the move itself reports failures, so this returns the last known
        value without touching the bus. While idle it reads the joints once; on failure the arm
        enters ERROR ("connection lost: ...").
        """
        if not self._bus_lock.acquire(blocking=False):
            return self._connected
        try:
            self._backend.read()
        except Exception as exc:
            self._connection_lost(exc)
        else:
            self._connected = True
        finally:
            self._bus_lock.release()
        return self._connected

    def on_state(self, listener: StateListener) -> None:
        """Call `listener(state, detail)` on every state change.

        Called from the controller's worker thread (or the caller's thread for STOP while idle).
        Keep listeners quick and do not block in them.
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
        """Cancel any motion, hold position, and enter ERROR. Always accepted."""
        self._stop.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            if worker is not threading.current_thread():
                worker.join(timeout=self._config.settle_timeout_s + 2.0)
            if worker.is_alive():
                self._set_state(ArmState.ERROR, "stopped; arm not responding")
            # Otherwise the worker already reported ERROR.
        else:
            self._set_state(ArmState.ERROR, "stopped")
        return True

    def close(self) -> None:
        """Stop any motion and disconnect. On the real arm this turns torque off (the arm goes limp)."""
        if self._worker is not None and self._worker.is_alive():
            self.stop()
        self._backend.close()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until the current command finishes. Returns False on timeout."""
        worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()

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
        self.last_rejection = reason
        logger.info("rejected: %s", reason)
        return False

    def _start(self, command: str, plan: list[Step], allowed: tuple[ArmState, ...]) -> bool:
        with self._lock:
            busy = self._worker is not None and self._worker.is_alive()
            if busy or self._state not in allowed:
                state = ArmState.BUSY.value if busy else self._state.value
                reason = f"{command} rejected while {state}"
                if self._state is ArmState.ERROR and command.startswith("SORT"):
                    reason += " (send HOME first)"
                accepted = False
            else:
                self._stop.clear()
                self._state = ArmState.BUSY
                self._detail = command
                self._worker = threading.Thread(
                    target=self._run, args=(command, plan), name="arm-controller", daemon=True
                )
                self._worker.start()
                accepted = True
        if not accepted:
            return self._reject(reason)
        self.last_rejection = None
        return True

    def _run(self, command: str, plan: list[Step]) -> None:
        with self._bus_lock:
            self._run_locked(command, plan)

    def _run_locked(self, command: str, plan: list[Step]) -> None:
        self._notify(ArmState.BUSY, command)
        try:
            for step in plan:
                self._execute(step)
        except MotionStopped:
            self._goal = None
            self._set_state(ArmState.ERROR, "stopped")
            return
        except MotionError as exc:  # the arm answered but a joint did not reach its goal
            logger.warning("%s failed: %s", command, exc)
            self._goal = None
            self._set_state(ArmState.ERROR, f"{command} failed: {exc}")
            return
        except Exception as exc:  # serial or USB failure, most likely
            logger.exception("%s failed", command)
            self._goal = None
            try:
                hold(self._backend)
            except Exception:
                logger.exception("could not hold position after failure")
            self._connection_lost(exc)
            return

        self._connected = True
        if self._stop.is_set():
            self._set_state(ArmState.ERROR, "stopped")
        else:
            with self._lock:
                self._done_seq += 1
                self.last_done = (self._done_seq, command)
            self._set_state(ArmState.DONE, command)
            self._set_state(ArmState.READY, None)

    def _connection_lost(self, exc: Exception) -> None:
        self._connected = False
        self._set_state(ArmState.ERROR, f"connection lost: {exc}")

    def _execute(self, step: Step) -> None:
        kind, arg = step
        if kind == "pause":
            pause(float(arg), self._stop)
            return
        if self._goal is None:
            self._goal = self._backend.read()
        if kind == "pose":
            target = {**self._goal, **self._poses.body(str(arg))}
        elif kind == "gripper":
            target = {**self._goal, GRIPPER: self._poses.grip(str(arg))}
        else:
            raise ValueError(f"unknown step {step!r}")
        move_to(self._backend, target, self._config, self._stop)
        self._goal = target

    def _set_state(self, state: ArmState, detail: str | None) -> None:
        with self._lock:
            self._state = state
            self._detail = detail
        self._notify(state, detail)

    def _notify(self, state: ArmState, detail: str | None) -> None:
        for listener in list(self._listeners):
            try:
                listener(state, detail)
            except Exception:
                logger.exception("state listener failed")
