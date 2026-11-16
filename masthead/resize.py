from __future__ import annotations

import math
import operator


def fit_size(width, height, max_width, max_height):
    """Size that fits the box keeping the aspect ratio. Never enlarges."""
    scale = min(max_width / width, max_height / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _spans(source, target):
    scale = source / target
    spans = []
    for index in range(target):
        start, end = index * scale, (index + 1) * scale
        first = int(start)
        last = min(math.ceil(end), source)
        weights = [min(end, position + 1) - max(start, position) for position in range(first, last)]
        spans.append((first, last, weights, sum(weights)))
    return spans


def area_resize(rows, width, height, new_width, new_height):
    """Shrink RGBA rows (bytes of width * 4) by exact area averaging, weighting colour by alpha so edges get no dark fringe."""
    columns = _spans(width, new_width)
    lines = _spans(height, new_height)
    reduced = []
    for row in rows:
        alpha = row[3::4]
        planes = [[value * weight for value, weight in zip(row[offset::4], alpha)] for offset in range(3)]
        reduced.append(
            [
                [sum(map(operator.mul, plane[first:last], weights)) / total for first, last, weights, total in columns]
                for plane in planes + [alpha]
            ]
        )
    output = []
    for first, last, weights, total in lines:
        channels = []
        for channel in range(4):
            stacked = zip(*[reduced[index][channel] for index in range(first, last)])
            channels.append([sum(map(operator.mul, column, weights)) / total for column in stacked])
        result = bytearray(new_width * 4)
        for x in range(new_width):
            alpha_value = channels[3][x]
            if alpha_value <= 0:
                continue
            for channel in range(3):
                result[x * 4 + channel] = min(255, round(channels[channel][x] / alpha_value))
            result[x * 4 + 3] = min(255, round(alpha_value))
        output.append(result)
    return output
