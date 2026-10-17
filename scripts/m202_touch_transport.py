"""Bridge completed M202 taps across the slow MCU/debugger transport.

The firmware emits a point on contact, another point at liftoff and -1,-1.
An already released host tap must reach the PC as a completed gesture instead
of several seconds of holding. Acknowledge retained native frames locally;
only the PC acknowledgement for the final release reaches the MCU later.
Real held contacts keep their original streaming behavior.

The game handles one received frame per serial event: a second frame in the
same burst waits until more bytes arrive, seconds later in an idle menu. The
firmware itself sends its next frame only after the PC's acknowledgement, so
touch frames for the PC leave one at a time from an outbox (next_frame), each
after the PC acknowledged the previous one.

A point the PC already has while the finger was still down is released as
soon as the host finger leaves (host_release). The firmware reports that
liftoff seconds late; until then the game holds the touched button and its
auto-repeat (600-800 ms) clicks it again. The firmware's own late liftoff
frames of that contact are acknowledged locally and not forwarded.

Touches for the service program go to its own object, and the firmware reports
only the contact and liftoff points there, never -1,-1; the game clicks into
the service for each point. Retained points whose liftoff is not followed by
a release within LIFTOFF_GRACE go to the PC alone, and that target never gets
a synthetic release (it would click into the screen corner).

M90_M202_TAP_MODE=point_only is a live diagnostic: the PC receives the
retained point(s) without the -1,-1 release, which is acknowledged locally.
It tells whether the game counts the release as a separate action.

M90_M202_TAP_MODE=point_release_gap sends the release a finger's contact time
after the point (M90_M202_TAP_GAP_MS, default 150) instead of right behind it.
"""
from collections import deque
import os
from threading import Lock
import time

PREFIX = b'\x01\x02\x33\x00'
ACK = b'\x06\x03\x00\x00'
RELEASE = b'\xff\xff\xff\xff'
TAP_MODES = ('point_release', 'point_only', 'point_release_gap')
# A frame the PC did not acknowledge in this time no longer holds the next one.
ACK_TIMEOUT = 0.5
# The firmware sends -1,-1 right behind the liftoff point where it sends one.
LIFTOFF_GRACE = 0.4


class M202TouchTransport:
    def __init__(self, mode=None, clock=time.monotonic):
        mode = mode or os.environ.get('M90_M202_TAP_MODE') or 'point_release'
        if mode not in TAP_MODES:
            raise ValueError(f'unknown M202 tap mode {mode!r}')
        self.mode = mode
        self.release_gap = (int(os.environ.get('M90_M202_TAP_GAP_MS', '150')) / 1000.0
                            if mode == 'point_release_gap' else 0.0)
        self.clock = clock
        self.points = []
        self.target = None
        self.live = None   # point of this contact the PC already has
        self.released = None   # target released by host_release, firmware frames still due
        self.liftoff_at = None   # when the liftoff point of retained points arrived
        self.no_release = set()   # targets whose firmware sends no -1,-1 (service program)
        self.outbox = deque()
        self.awaiting = 0   # frames sent to the PC and not acknowledged yet
        self.sent_at = None
        self.lock = Lock()
        self.skip_acks = 0
        self.reply = bytearray()

    def frame(self, frame: bytes, *, host_released: bool):
        """Return (frame for the PC now, locally supplied MCU acknowledgement bytes).

        Further frames for the PC wait in the outbox for next_frame().
        """
        if len(frame) != 11 or not frame.startswith(PREFIX) or frame[-1] != 4:
            return frame, b''
        target = frame[4:6]
        if target == self.released:
            # Late liftoff point(s) and -1,-1 of a contact the PC has released.
            if frame[6:10] == RELEASE:
                self.released = None
            return b'', ACK
        if self.target is not None and target != self.target:
            return frame, b''
        if frame[6:10] == RELEASE:
            points, self.points = self.points, []
            self.live = self.target = self.liftoff_at = None
            self.no_release.discard(target)
            if not points:
                return self._send(frame), b''
            with self.lock:
                # All retained native point frames have already been ACKed.
                # Forward only the PC's final-release ACK to the native MCU.
                self.skip_acks += len(points)
            if self.mode == 'point_only':
                return self._send(*points), ACK
            return self._send(*points, frame), b''
        if self.points or host_released:
            self.target = target
            # The liftoff point repeats the contact point: one the PC already
            # received while the finger was down is not sent a second time.
            if frame != (self.points[-1] if self.points else self.live):
                self.points.append(frame)
            elif self.points:
                # Liftoff: -1,-1 follows at once where this target sends one.
                if target in self.no_release:
                    self._flush_points()
                    return self.next_frame(), ACK
                if self.liftoff_at is None:
                    self.liftoff_at = self.clock()
            return b'', ACK
        self.live = frame
        return self._send(frame), b''

    def _flush_points(self):
        """Send retained points without a release: this target sends none."""
        points, self.points = self.points, []
        self.no_release.add(self.target)
        self.target = self.liftoff_at = None
        with self.lock:
            self.skip_acks += len(points)   # their native frames were ACKed locally
        self.outbox.extend(points)

    def host_release(self):
        """Queue the release of a contact the PC holds, when the finger left."""
        if self.mode == 'point_only' or self.live is None:
            return False
        target = self.live[4:6]
        if target in self.no_release:
            self.live = self.target = None
            return False
        self.live = self.target = None
        self.points = []
        self.released = target
        with self.lock:
            self.skip_acks += 1   # the PC's ACK of this synthetic release
        self.outbox.append(PREFIX + target + RELEASE + b'\x04')
        return True

    def _send(self, *frames):
        self.outbox.extend(frames)
        return self.next_frame()

    def next_frame(self):
        """Return the next frame for the PC once it acknowledged the previous one."""
        now = self.clock()
        if self.points and self.liftoff_at is not None and now - self.liftoff_at >= LIFTOFF_GRACE:
            self._flush_points()
        if not self.outbox:
            return b''
        with self.lock:
            if self.awaiting and now - self.sent_at < ACK_TIMEOUT:
                return b''
            if (self.release_gap and self.outbox[0][6:10] == RELEASE
                    and self.sent_at is not None and now - self.sent_at < self.release_gap):
                return b''
            self.awaiting = 1
            self.sent_at = now
        return self.outbox.popleft()

    def receive(self, data: bytes):
        """Remove only reserved full ACK cycles, across arbitrary TCP chunks."""
        forwarded = bytearray()
        with self.lock:
            for byte in data:
                if not (self.skip_acks or self.awaiting or self.reply):
                    forwarded.append(byte)
                    continue
                self.reply.append(byte)
                while self.reply and not ACK.startswith(self.reply):
                    forwarded.append(self.reply.pop(0))
                if bytes(self.reply) == ACK:
                    self.reply.clear()
                    self.awaiting = 0
                    if self.skip_acks:
                        self.skip_acks -= 1
                    else:
                        forwarded.extend(ACK)
        return bytes(forwarded)
