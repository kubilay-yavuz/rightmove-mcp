"""Pydantic models for the DEFRA UK-AIR Sensor Observation Service.

The upstream SOS API ships two shapes we care about:

* a **stations** GeoJSON-like list (``/api/v1/stations/``) where every
  record covers exactly one pollutant at one site and carries a single
  lat/lng; the label encodes the site name and pollutant;
* a **timeseries** JSON list (``/api/v1/timeseries/{id}/getData``) that
  returns ``{timestamp, value}`` pairs — ``timestamp`` is epoch-ms.

We normalise both shapes into station-centric pydantic models so the
climate-risk actor doesn't have to know about SOS / OGC semantics.

Pollutant vocabulary is stable across the live DEFRA label format
(``{Site}-{Pollutant} (air|aerosol)``); we canonicalise to a small
enum-ish set (``pm25``, ``pm10``, ``no2``, ``o3``, ``so2``, ``co``,
``benzene``, ``nox``, ``nom``, ``other``) to make joining across
stations trivial.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Pollutant = Literal[
    "pm25",
    "pm10",
    "no2",
    "nox",
    "nom",
    "o3",
    "so2",
    "co",
    "benzene",
    "other",
]


_BENZENE_START = re.compile(r"^\s*benzene\b", re.IGNORECASE)


def canonical_pollutant(label: str | None) -> Pollutant:
    """Map a DEFRA pollutant label fragment to a compact tag.

    The upstream labels are verbose (``Particulate matter less than 10
    micro m (aerosol)``). We keep a small canonical set so downstream
    consumers can join across sites without regex-matching every time.
    Unknown labels fall through to ``'other'``.

    Benzene disambiguation:

    * ``Benzene (air)`` → ``benzene`` (regulated AQS headline).
    * ``Ethyl benzene (air)`` / ``1,2,3-Trimethylbenzene`` →
      ``other`` (speciated VOCs — distinct compounds).

    We match ``benzene`` only when it's the **first word** of the
    pollutant label so compound VOC names don't get mis-attributed.
    """

    if not label:
        return "other"
    low = label.lower()
    if "particulate matter less than 2.5" in low or "pm2.5" in low or "pm25" in low:
        return "pm25"
    if "particulate matter less than 10" in low or "pm10" in low:
        return "pm10"
    if "nitrogen dioxide" in low:
        return "no2"
    if "nitrogen oxides" in low:
        return "nox"
    if "nitrogen monoxide" in low:
        return "nom"
    if "ozone" in low:
        return "o3"
    if "sulphur dioxide" in low or "sulfur dioxide" in low:
        return "so2"
    if "carbon monoxide" in low:
        return "co"
    if _BENZENE_START.match(low):
        return "benzene"
    return "other"


class AirQualityStation(BaseModel):
    """One DEFRA UK-AIR timeseries covering one pollutant at one site.

    The DEFRA ``/timeseries/`` feed ships one record per active
    pollutant measurement: the ``id`` field is the timeseries
    (needed for ``getData``) and ``station.properties.id`` is the
    parent site. We flatten both into one model because downstream
    consumers always want to ask "what's the latest NO2 at site X?"
    which is one timeseries, not one 'station'.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    timeseries_id: str = Field(
        description="DEFRA SOS timeseries id (path param for /getData).",
    )
    station_id: str | None = Field(
        default=None,
        description=(
            "Parent DEFRA station id — same physical site can host "
            "multiple timeseries (one per pollutant)."
        ),
    )
    site_name: str = Field(
        description="Human-readable site label with the pollutant suffix stripped.",
    )
    label: str = Field(
        description="Full DEFRA label ('{Site}-{Pollutant} (air|aerosol)').",
    )
    pollutant_raw: str | None = Field(
        default=None,
        description="Raw pollutant description parsed from the label.",
    )
    pollutant: Pollutant = Field(
        default="other",
        description="Canonical pollutant tag used for cross-site joins.",
    )
    unit: str | None = Field(
        default=None,
        description=(
            "Unit of measurement as reported by DEFRA "
            "('ug.m-3' / 'mg.m-3' / etc.)."
        ),
    )
    lat: float | None = Field(default=None, description="WGS-84 latitude.")
    lng: float | None = Field(default=None, description="WGS-84 longitude.")

    @field_validator("timeseries_id", "station_id", mode="before")
    @classmethod
    def _coerce_id(cls, value: object) -> object:
        if value is None:
            return value
        return str(value)

    @classmethod
    def from_timeseries_feature(
        cls, feature: dict[str, Any]
    ) -> AirQualityStation | None:
        """Normalise one entry from the DEFRA ``/timeseries/`` feed.

        Returns ``None`` when the payload is malformed (missing id or
        coordinates) rather than raising — the live feed is mostly
        clean but occasionally ships records with ``NaN`` coords that
        we must silently skip instead of poisoning the whole list.
        """

        ts_id = feature.get("id")
        if ts_id is None:
            return None
        station = feature.get("station") or {}
        props = station.get("properties") or {}
        geom = station.get("geometry") or {}
        label = str(props.get("label") or "").strip()
        if not label:
            return None
        site_name, pollutant_raw = _split_label(label)
        coords = geom.get("coordinates") or []
        lat = _as_float(coords[0]) if len(coords) > 0 else None
        lng = _as_float(coords[1]) if len(coords) > 1 else None
        station_id = props.get("id")
        return cls(
            timeseries_id=str(ts_id),
            station_id=str(station_id) if station_id is not None else None,
            site_name=site_name,
            label=label,
            pollutant_raw=pollutant_raw,
            pollutant=canonical_pollutant(pollutant_raw),
            unit=feature.get("uom"),
            lat=lat,
            lng=lng,
        )


class AirQualityReading(BaseModel):
    """A single timestamped air-quality measurement at one station."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    timeseries_id: str
    site_name: str
    pollutant: Pollutant
    pollutant_raw: str | None = None
    timestamp_ms: int | None = Field(
        default=None,
        description="Epoch-ms timestamp as returned by DEFRA SOS.",
    )
    value: float | None = Field(
        default=None,
        description="Measured concentration. Units depend on pollutant.",
    )
    unit: str | None = Field(
        default=None,
        description="Unit hint. DEFRA SOS does not ship units on the list "
        "endpoint so this is filled from pollutant conventions when known.",
    )
    lat: float | None = None
    lng: float | None = None


class StationProximity(BaseModel):
    """A station with an attached distance-from-query-point (km).

    Used by ``stations_near`` so callers can pick the nearest ``N``
    stations without re-implementing haversine at every site.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    station: AirQualityStation
    distance_km: float = Field(ge=0.0, description="Great-circle km.")


def _split_label(label: str) -> tuple[str, str | None]:
    """Split 'Aberdeen Erroll Park-Nitrogen dioxide (air)' into site + pollutant.

    The DEFRA label format is ``{site}-{pollutant} (air|aerosol)``
    but both halves can contain ``-`` in rare cases:

    * site names: ``Stoke-on-Trent Centre-Carbon monoxide (air)``
    * pollutant names: ``Non-volatile PM2.5`` / ``1,2,3-Trimethylbenzene``

    Heuristic:

    1. Walk candidate split positions **from the right** (last ``-``
       first).
    2. Accept the first one whose tail **canonicalises** AND whose
       tail starts with uppercase / digit. The second condition drops
       tails like ``volatile PM10`` (a continuation of ``Non-volatile``)
       while still accepting pollutants that normally start with a
       capital letter (``Nitrogen``, ``Carbon``, ``Ozone``) or digit
       (``1,2,3-Trimethylbenzene``).
    3. If no position passes both checks but at least one canonicalises,
       accept the rightmost canonical tail regardless of case — this
       keeps legacy VOC speciations routable to ``other`` without
       losing the site name.
    4. Fall back to the first ``-`` so the raw pollutant string is
       preserved for downstream inspection on unknown formats.
    """

    if "-" not in label:
        return label, None
    positions: list[int] = []
    start = 0
    while True:
        idx = label.find("-", start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1

    canonical_hit: int | None = None
    for idx in reversed(positions):
        tail = label[idx + 1 :].strip()
        if not tail:
            continue
        if canonical_pollutant(tail) == "other":
            continue
        if canonical_hit is None:
            canonical_hit = idx
        first = tail[0]
        if first.isupper() or first.isdigit():
            site = label[:idx].rstrip(" -")
            return site or label, tail
    if canonical_hit is not None:
        tail = label[canonical_hit + 1 :].strip()
        site = label[:canonical_hit].rstrip(" -")
        return site or label, tail or None
    idx = positions[0]
    site = label[:idx].rstrip(" -")
    pollutant = label[idx + 1 :].strip()
    return site or label, pollutant or None


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


__all__ = [
    "AirQualityReading",
    "AirQualityStation",
    "Pollutant",
    "StationProximity",
    "canonical_pollutant",
]
