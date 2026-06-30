"""
Color math helpers.

- hex_to_rgb / rgb to Lab (D65, sRGB) / CIE76 delta E
- "Abs" difference = Manhattan distance between RGB channels (matches the
  convention used in the original Seymour export, e.g. #001000 vs #000000
  is treated as 16 "off").
"""
import math
import re

HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def normalize_hex(value: str) -> str | None:
    """Return a clean 'RRGGBB' (no '#', uppercase) string, or None if invalid."""
    if not value:
        return None
    m = HEX_RE.match(value.strip())
    if not m:
        return None
    return m.group(1).upper()


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (v / 255.0 for v in rgb)

    def inv_gamma(c: float) -> float:
        return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92

    r, g, b = inv_gamma(r), inv_gamma(g), inv_gamma(b)

    # sRGB -> XYZ (D65)
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041

    # Normalize by D65 reference white
    x /= 0.95047
    y /= 1.00000
    z /= 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)

    fx, fy, fz = f(x), f(y), f(z)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b_ = 200 * (fy - fz)
    return (L, a, b_)


def delta_e_cie76(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    return math.sqrt(sum((c1 - c2) ** 2 for c1, c2 in zip(lab1, lab2)))


def abs_diff(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> int:
    return sum(abs(c1 - c2) for c1, c2 in zip(rgb1, rgb2))


def compare_hex(hex_a: str, hex_b: str) -> tuple[float, int]:
    """Returns (delta_e, abs_diff) between two 'RRGGBB' hex strings."""
    rgb_a, rgb_b = hex_to_rgb(hex_a), hex_to_rgb(hex_b)
    lab_a, lab_b = rgb_to_lab(rgb_a), rgb_to_lab(rgb_b)
    return delta_e_cie76(lab_a, lab_b), abs_diff(rgb_a, rgb_b)