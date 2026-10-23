"""Fake Mexican identifiers: right shape, wrong content.

CURP and CLABE are generated in the real *format* so queries and data checks behave like they
would on real data, but they can never belong to a real person: the CURP check digit and the
CLABE check digit are wrong on purpose, and the CLABE bank code is a synthetic one.
"""

from __future__ import annotations

import re

import numpy as np

SYNTHETIC_BANK_CODE = "999"  # not a real institution code: external banks are anonymised too

_CURP_RE = re.compile(r"^[A-Z]{4}\d{6}[HM][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[0-9A-Z]\d$")
_LETTERS = np.array(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
_CONSONANTS = np.array(list("BCDFGHJKLMNPQRSTVWXYZ"))
_ALNUM = np.array(list("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
_DIGITS = np.array(list("0123456789"))


def _pick(rng: np.random.Generator, alphabet: np.ndarray, n: int, length: int) -> np.ndarray:
    """``n`` random strings of ``length`` characters drawn from ``alphabet``."""
    chars = alphabet[rng.integers(0, len(alphabet), size=(n, length))]
    return np.array(["".join(row) for row in chars])


def make_curp(
    rng: np.random.Generator,
    birth_dates: np.ndarray,
    sex: np.ndarray,
    state_codes: np.ndarray,
) -> np.ndarray:
    """Build CURP-shaped strings consistent with birth date, sex and state of birth.

    The 17th character is random (homoclave) and the 18th is a digit that is *not* the real
    check digit computation, so the result is invalid by design.
    """
    n = len(birth_dates)
    dob = np.array([f"{d:%y%m%d}" for d in birth_dates])
    sex_letter = np.where(sex == "F", "M", "H")  # CURP uses H (hombre) / M (mujer)
    parts = (
        _pick(rng, _LETTERS, n, 4),
        dob,
        sex_letter,
        state_codes,
        _pick(rng, _CONSONANTS, n, 3),
        _pick(rng, _ALNUM, n, 1),
        _pick(rng, _DIGITS, n, 1),
    )
    out = parts[0].astype(object)
    for p in parts[1:]:
        out = out + p.astype(object)
    return out.astype(str)


def curp_format_ok(curp: str) -> bool:
    """True when the string has the shape of a CURP (the check digit is not verified)."""
    return bool(_CURP_RE.match(curp))


def clabe_check_digit(first17: str) -> int:
    """The CLABE check digit for the first 17 digits (weights 3, 7, 1 repeating, mod 10)."""
    weights = (3, 7, 1)
    total = sum((int(d) * weights[i % 3]) % 10 for i, d in enumerate(first17))
    return (10 - total % 10) % 10


def make_clabe(account_numbers: np.ndarray, plaza: np.ndarray) -> np.ndarray:
    """Build 18-digit CLABE-shaped strings with a deliberately wrong check digit."""
    out = []
    for acc, pl in zip(account_numbers, plaza, strict=True):
        first17 = f"{SYNTHETIC_BANK_CODE}{int(pl):03d}{int(acc):011d}"
        wrong = (clabe_check_digit(first17) + 1) % 10
        out.append(f"{first17}{wrong}")
    return np.array(out)


def clabe_is_valid(clabe: str) -> bool:
    """True when length, digits and the check digit are all correct (never true for ours)."""
    return len(clabe) == 18 and clabe.isdigit() and clabe_check_digit(clabe[:17]) == int(clabe[17])
