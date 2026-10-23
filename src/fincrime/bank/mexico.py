"""Mexican reference data used to calibrate the synthetic bank.

``STATE_POPULATION_2020`` is INEGI's 2020 census (checked against the published table).
The CURP state codes follow the official two-letter convention. ``STATE_TIER`` is an
**assumption**, not data: see ``docs/assumptions.md``.
"""

from __future__ import annotations

# CURP two-letter code -> (state name, 2020 census population). Sum = 126,014,024.
STATES: dict[str, tuple[str, int]] = {
    "AS": ("Aguascalientes", 1_425_607),
    "BC": ("Baja California", 3_769_020),
    "BS": ("Baja California Sur", 798_447),
    "CC": ("Campeche", 928_363),
    "CL": ("Coahuila", 3_146_771),
    "CM": ("Colima", 731_391),
    "CS": ("Chiapas", 5_543_828),
    "CH": ("Chihuahua", 3_741_869),
    "DF": ("Ciudad de México", 9_209_944),
    "DG": ("Durango", 1_832_650),
    "GT": ("Guanajuato", 6_166_934),
    "GR": ("Guerrero", 3_540_685),
    "HG": ("Hidalgo", 3_082_841),
    "JC": ("Jalisco", 8_348_151),
    "MC": ("México", 16_992_418),
    "MN": ("Michoacán", 4_748_846),
    "MS": ("Morelos", 1_971_520),
    "NT": ("Nayarit", 1_235_456),
    "NL": ("Nuevo León", 5_784_442),
    "OC": ("Oaxaca", 4_132_148),
    "PL": ("Puebla", 6_583_278),
    "QT": ("Querétaro", 2_368_467),
    "QR": ("Quintana Roo", 1_857_985),
    "SP": ("San Luis Potosí", 2_822_255),
    "SL": ("Sinaloa", 3_026_943),
    "SR": ("Sonora", 2_944_840),
    "TC": ("Tabasco", 2_402_598),
    "TS": ("Tamaulipas", 3_527_735),
    "TL": ("Tlaxcala", 1_342_977),
    "VZ": ("Veracruz", 8_062_579),
    "YN": ("Yucatán", 2_320_898),
    "ZS": ("Zacatecas", 1_622_138),
}

CENSUS_2020_TOTAL = 126_014_024

# Assumption: how strongly each state's digital-bank adoption departs from its population share.
HIGH_ADOPTION = ("DF", "NL", "BC", "BS", "SR", "CH", "CL", "QT", "AS", "JC", "QR", "TS")
LOW_ADOPTION = ("CS", "OC", "GR", "VZ", "TC", "MN", "PL", "HG")


def state_tier(code: str) -> str:
    """Adoption tier of a state: ``high``, ``mid`` or ``low`` (an assumption)."""
    if code in HIGH_ADOPTION:
        return "high"
    return "low" if code in LOW_ADOPTION else "mid"
