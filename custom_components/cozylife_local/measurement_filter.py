"""Optional median-window outlier rejection in native measurement units."""

from collections import deque
from statistics import median


class MeasurementFilter:
    """Compare against preceding raw samples, including rejected outliers.

    Retaining raw samples lets a sustained real change move the median rather
    than locking the output to an old accepted value indefinitely.
    """

    def __init__(self, window_size=4):
        self.window_size = window_size
        self.windows = {}
        self.sequences = deque(maxlen=256)

    def filter(self, sequence, values, radii):
        if sequence is None or sequence == "":
            # Unidentified frames cannot safely advance a confirmation window.
            return {}
        sequence = str(sequence)
        if sequence in self.sequences:
            return {}
        self.sequences.append(sequence)
        output = dict(values)
        for key, radius in radii.items():
            if key not in values:
                continue
            value = values[key]
            window = self.windows.setdefault(key, deque(maxlen=self.window_size))
            if not window:
                # Seed the baseline so a single startup outlier cannot shift
                # a two-value median halfway toward the rejected spike.
                window.extend([value] * self.window_size)
            if window and abs(value - median(window)) > radius:
                output.pop(key, None)
            window.append(value)
        return output
