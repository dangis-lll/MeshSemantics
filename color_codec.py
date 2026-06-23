from __future__ import annotations

import numpy as np


def pack_rgb(r: int, g: int, b: int) -> int:
    """Pack one RGB triplet into one lossless 24-bit integer."""
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    return (r << 16) | (g << 8) | b


def unpack_rgb(color: int) -> tuple[int, int, int]:
    """Unpack one 24-bit integer back to an RGB triplet."""
    value = int(color)
    r = (value >> 16) & 255
    g = (value >> 8) & 255
    b = value & 255
    return r, g, b


def pack_rgb_array(rgb: np.ndarray) -> np.ndarray:
    """Pack an N x 3 RGB array into an N-length uint32 array."""
    values = np.asarray(rgb, dtype=np.uint32).reshape(-1, 3)
    values = np.clip(values, 0, 255).astype(np.uint32, copy=False)
    return ((values[:, 0] << 16) | (values[:, 1] << 8) | values[:, 2]).astype(np.uint32)


def unpack_rgb_array(colors: np.ndarray) -> np.ndarray:
    """Unpack an N-length integer array into an N x 3 uint8 RGB array."""
    values = np.asarray(colors, dtype=np.uint32).reshape(-1)
    rgb = np.empty((values.size, 3), dtype=np.uint8)
    rgb[:, 0] = ((values >> 16) & 255).astype(np.uint8)
    rgb[:, 1] = ((values >> 8) & 255).astype(np.uint8)
    rgb[:, 2] = (values & 255).astype(np.uint8)
    return rgb


if __name__ == "__main__":
    original = (120, 45, 200)
    packed = pack_rgb(*original)
    restored = unpack_rgb(packed)
    print("single:", original, "->", packed, "->", restored)

    rgb_array = np.array(
        [
            [255, 0, 0],
            [0, 255, 0],
            [0, 0, 255],
            [120, 45, 200],
        ],
        dtype=np.uint8,
    )
    packed_array = pack_rgb_array(rgb_array)
    restored_array = unpack_rgb_array(packed_array)
    print("array packed:", packed_array.tolist())
    print("array restored:")
    print(restored_array)
