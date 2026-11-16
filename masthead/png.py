from __future__ import annotations

import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_BIT_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
_MAX_PIXELS = 64_000_000


def _unpack_table(bit_depth):
    per_byte = 8 // bit_depth
    mask = (1 << bit_depth) - 1
    return [bytes((byte >> (8 - bit_depth * (index + 1))) & mask for index in range(per_byte)) for byte in range(256)]


_UNPACK = {depth: _unpack_table(depth) for depth in (1, 2, 4)}


def _unfilter(raw, position, stride, count, bytes_per_pixel):
    previous = bytearray(stride)
    lines = []
    for _ in range(count):
        if position + 1 + stride > len(raw):
            raise ValueError("truncated PNG image data")
        filter_type = raw[position]
        line = bytearray(raw[position + 1:position + 1 + stride])
        position += 1 + stride
        if filter_type == 1:
            for index in range(bytes_per_pixel, stride):
                line[index] = (line[index] + line[index - bytes_per_pixel]) & 255
        elif filter_type == 2:
            line = bytearray([(value + above) & 255 for value, above in zip(line, previous)])
        elif filter_type == 3:
            for index in range(min(bytes_per_pixel, stride)):
                line[index] = (line[index] + (previous[index] >> 1)) & 255
            for index in range(bytes_per_pixel, stride):
                line[index] = (line[index] + ((line[index - bytes_per_pixel] + previous[index]) >> 1)) & 255
        elif filter_type == 4:
            for index in range(min(bytes_per_pixel, stride)):
                line[index] = (line[index] + previous[index]) & 255
            for index in range(bytes_per_pixel, stride):
                left = line[index - bytes_per_pixel]
                above = previous[index]
                upper_left = previous[index - bytes_per_pixel]
                distance_left = abs(above - upper_left)
                distance_above = abs(left - upper_left)
                distance_upper_left = abs(left + above - 2 * upper_left)
                if distance_left <= distance_above and distance_left <= distance_upper_left:
                    predictor = left
                elif distance_above <= distance_upper_left:
                    predictor = above
                else:
                    predictor = upper_left
                line[index] = (line[index] + predictor) & 255
        elif filter_type != 0:
            raise ValueError(f"invalid PNG filter type {filter_type}")
        lines.append(line)
        previous = line
    return lines, position


def _eight_bit_samples(line, sample_count, bit_depth):
    if bit_depth == 8:
        return bytes(line[:sample_count])
    if bit_depth == 16:
        return bytes(line[0::2][:sample_count])
    table = _UNPACK[bit_depth]
    return b"".join([table[byte] for byte in line])[:sample_count]


def _build_converter(color_type, bit_depth, palette, transparency):
    if color_type == 3:
        reds = bytes(palette[index][0] if index < len(palette) else 0 for index in range(256))
        greens = bytes(palette[index][1] if index < len(palette) else 0 for index in range(256))
        blues = bytes(palette[index][2] if index < len(palette) else 0 for index in range(256))
        alphas = bytes(transparency[index] if index < len(transparency) else 255 for index in range(256))
    elif color_type == 0:
        maximum = (1 << min(bit_depth, 8)) - 1
        grey_scale = bytes(min(255, value * 255 // maximum) for value in range(256))
        grey_key = int.from_bytes(transparency[:2], "big") if len(transparency) >= 2 else None
        key_alphas = bytes(0 if value == grey_key else 255 for value in range(256))
    elif color_type == 2:
        rgb_key = tuple(int.from_bytes(transparency[index:index + 2], "big") for index in (0, 2, 4)) if len(transparency) >= 6 else None
    else:
        rgb_key = None

    def exact_alphas(line, width):
        channels = _CHANNELS[color_type]
        values = struct.unpack(f">{width * channels}H", bytes(line[:width * channels * 2]))
        if color_type == 0:
            return bytes(0 if value == grey_key else 255 for value in values)
        return bytes(0 if values[index:index + 3] == rgb_key else 255 for index in range(0, len(values), 3))

    def convert(line, width):
        channels = _CHANNELS[color_type]
        samples = _eight_bit_samples(line, width * channels, bit_depth)
        row = bytearray(width * 4)
        if color_type == 6:
            return bytearray(samples)
        if color_type == 3:
            row[0::4] = samples.translate(reds)
            row[1::4] = samples.translate(greens)
            row[2::4] = samples.translate(blues)
            row[3::4] = samples.translate(alphas)
        elif color_type == 0:
            grey = samples.translate(grey_scale)
            row[0::4] = row[1::4] = row[2::4] = grey
            if grey_key is None:
                row[3::4] = b"\xff" * width
            else:
                row[3::4] = exact_alphas(line, width) if bit_depth == 16 else samples.translate(key_alphas)
        elif color_type == 4:
            grey = samples[0::2]
            row[0::4] = row[1::4] = row[2::4] = grey
            row[3::4] = samples[1::2]
        else:
            reds_, greens_, blues_ = samples[0::3], samples[1::3], samples[2::3]
            row[0::4], row[1::4], row[2::4] = reds_, greens_, blues_
            if rgb_key is None:
                row[3::4] = b"\xff" * width
            elif bit_depth == 16:
                row[3::4] = exact_alphas(line, width)
            else:
                row[3::4] = bytes(0 if (red, green, blue) == rgb_key else 255 for red, green, blue in zip(reds_, greens_, blues_))
        return row

    return convert


def decode_png(data):
    """Decode any PNG into (width, height, rows), each row being `width * 4` bytes of RGBA (16-bit samples are reduced to 8 bits)."""
    if not data.startswith(_SIGNATURE):
        raise ValueError("not a PNG file")
    position = len(_SIGNATURE)
    header = None
    palette = []
    transparency = b""
    compressed = []
    while position + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        body = data[position + 8:position + 8 + length]
        position += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[index:index + 3]) for index in range(0, len(body) - 2, 3)]
        elif kind == b"tRNS":
            transparency = body
        elif kind == b"IDAT":
            compressed.append(body)
        elif kind == b"IEND":
            break
    if header is None:
        raise ValueError("PNG without IHDR chunk")
    width, height, bit_depth, color_type, compression, filter_method, interlace = header
    if color_type not in _CHANNELS or bit_depth not in _BIT_DEPTHS[color_type] or compression != 0 or filter_method != 0 or interlace not in (0, 1):
        raise ValueError(f"invalid PNG header (bit depth {bit_depth}, color type {color_type}, interlace {interlace})")
    if width == 0 or height == 0 or width * height > _MAX_PIXELS:
        raise ValueError(f"unsupported PNG size {width}x{height}")
    if color_type == 3 and not palette:
        raise ValueError("palette PNG without PLTE chunk")
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(b"".join(compressed), width * height * 8 + height * 16 + 1024)
        if decompressor.unconsumed_tail:
            raise ValueError("PNG image data is larger than its header declares")
    except zlib.error as error:
        raise ValueError(f"corrupt PNG image data: {error}") from error
    bits_per_pixel = _CHANNELS[color_type] * bit_depth
    bytes_per_pixel = max(1, bits_per_pixel // 8)
    convert = _build_converter(color_type, bit_depth, palette, transparency)
    passes = _ADAM7 if interlace else ((0, 0, 1, 1),)
    rows = [bytearray(width * 4) for _ in range(height)] if interlace else []
    position = 0
    for x_start, y_start, x_step, y_step in passes:
        pass_width = (width - x_start + x_step - 1) // x_step if width > x_start else 0
        pass_height = (height - y_start + y_step - 1) // y_step if height > y_start else 0
        if pass_width == 0 or pass_height == 0:
            continue
        stride = (pass_width * bits_per_pixel + 7) // 8
        lines, position = _unfilter(raw, position, stride, pass_height, bytes_per_pixel)
        pass_rows = [convert(line, pass_width) for line in lines]
        if not interlace:
            rows = pass_rows
            continue
        for index, pass_row in enumerate(pass_rows):
            target = rows[y_start + index * y_step]
            for channel in range(4):
                target[x_start * 4 + channel::x_step * 4] = pass_row[channel::4]
    return width, height, rows
