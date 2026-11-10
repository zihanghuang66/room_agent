"""Wait briefly for transient UI contention; never remove locks or retry work."""
import time
from contextlib import contextmanager, ExitStack
from room_agent.storage import room_lock

@contextmanager
def conversation_lock(root,name,timeout=2):
    deadline=time.monotonic()+timeout
    with ExitStack() as stack:
        while True:
            try:
                stack.enter_context(room_lock(root,name))
                break
            except RuntimeError as exc:
                if str(exc)!='room is busy: '+name:raise
                if time.monotonic()>=deadline:
                    raise RuntimeError('This conversation is still processing another request. Wait for it to finish, then retry; no new operation was started.') from None
                time.sleep(.02)
        # Body exceptions must propagate, never trigger an acquisition retry.
        yield
