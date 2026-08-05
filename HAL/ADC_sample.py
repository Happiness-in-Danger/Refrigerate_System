from collections import deque

class SmoothedADC:
    def __init__(self, read_fn, window=16):
        self._read = read_fn
        self._buf = deque((), window)

    def sample(self):
        self._buf.append(self._read())

    def value(self):
        if len(self._buf) == 0:
            return None
        return sum(self._buf) // len(self._buf)