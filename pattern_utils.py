"""
Hex pattern matching for /spattern.

Three pattern styles, auto-detected:

1. Substring match (pattern length 1-5, hex digits only e.g. "ABC")
   Matches any 6-digit hex color that CONTAINS this substring anywhere.

2. Positional wildcard match (pattern length exactly 6, e.g. "4x5x0x")
   Each character is matched against the hex color at the same position:
     - 0-9 / A-F  -> literal, that position must equal this digit
     - x or X     -> wildcard, "don't care" what this position is
     Mixing literals and 'x' in any order is fine.

3. Grouped-variable match (pattern length exactly 6, e.g. "WYWYWY")
   Any letter other than A-F/X acts as a placeholder variable. Every
   position sharing the same letter must resolve to the SAME hex digit
   within a given color (different letters are not required to differ).
   "WYWYWY" matches 565656 and 787878, but not 567812.

   Literal digits/letters (0-9, A-F) and 'x' wildcards can be freely
   mixed with grouped variables in the same 6-character pattern.
"""
HEX_DIGITS = set("0123456789ABCDEF")


class PatternError(ValueError):
    pass


def validate_pattern(pattern: str) -> str:
    """Normalize and validate a pattern. Raises PatternError with a user-facing message."""
    p = pattern.strip().lstrip("#").upper()
    if not p:
        raise PatternError("Pattern can't be empty.")
    if not p.isalnum():
        raise PatternError("Pattern can only contain letters and digits — no spaces or symbols.")
    if len(p) > 6:
        raise PatternError("Pattern can't be longer than 6 characters (a hex color is RRGGBB).")

    has_special = any(ch == "X" or ch not in HEX_DIGITS for ch in p)
    if has_special and len(p) != 6:
        raise PatternError(
            "Patterns using `x` wildcards or letter groups (like `WYWYWY`) must be exactly "
            "6 characters long — one per hex digit position. For a plain substring search "
            "(like `ABC`), use only digits 0-9 and letters A-F, shorter than 6 characters."
        )
    return p


def matches(pattern: str, hex_color: str) -> bool:
    """pattern must already be normalized via validate_pattern()."""
    hex_color = hex_color.upper()

    if len(pattern) != 6:
        # Substring mode
        return pattern in hex_color

    # Positional mode (literal / wildcard / grouped-variable)
    groups: dict[str, str] = {}
    for i, ch in enumerate(pattern):
        if ch == "X":
            continue
        if ch in HEX_DIGITS:
            if hex_color[i] != ch:
                return False
        else:
            if ch in groups:
                if hex_color[i] != groups[ch]:
                    return False
            else:
                groups[ch] = hex_color[i]
    return True