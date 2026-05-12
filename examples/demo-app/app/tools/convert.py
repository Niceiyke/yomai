"""Unit conversion tool."""
from __future__ import annotations

from yomai import tool


# Simple conversion table: key = "from_to", value = multiplier
_CONVERSIONS: dict[str, float] = {
    "km_mi": 0.621371,
    "mi_km": 1.60934,
    "kg_lb": 2.20462,
    "lb_kg": 0.453592,
    "c_f": 1.8,
    "f_c": 0.5556,
    "m_ft": 3.28084,
    "ft_m": 0.3048,
    "g_oz": 0.035274,
    "oz_g": 28.3495,
}


@tool
def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Convert between common units of measurement.

    Args:
        value: The numeric value to convert.
        from_unit: Unit to convert from (e.g., km, mi, kg, lb, c, f).
        to_unit: Unit to convert to (same abbreviations).
    """
    from_unit = from_unit.lower().strip()
    to_unit = to_unit.lower().strip()

    if from_unit == to_unit:
        return f"{value} {to_unit}"

    key = f"{from_unit}_{to_unit}"
    factor = _CONVERSIONS.get(key)

    if factor is None:
        return f"Conversion not supported: {from_unit} → {to_unit}"

    result = round(value * factor, 6)
    return f"{value} {from_unit} = {result} {to_unit}"