"""Great-circle geometry helpers. Pure math, no db access — keeps this module
trivially testable and reusable from dip.py / nearby.py / panel.py.

All angles in/out are degrees on the public API; radians internally.
"""
from __future__ import annotations

import math
from typing import Optional

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * c


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing (degrees, 0=N clockwise to 360) travelling from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    theta = math.atan2(y, x)
    return (math.degrees(theta) + 360.0) % 360.0


def destination_point(lat: float, lon: float, bearing: float, distance_km: float) -> tuple[float, float]:
    """Point reached travelling `distance_km` from (lat, lon) along `bearing` degrees.

    Used by panel.map_figure to draw the offset-radius circle as a polygon.
    """
    delta = distance_km / EARTH_RADIUS_KM
    theta = math.radians(bearing)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)

    phi2 = math.asin(math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(phi2), (math.degrees(lambda2) + 540.0) % 360.0 - 180.0


def circle_polygon(lat: float, lon: float, radius_km: float, n_points: int = 64) -> list[tuple[float, float]]:
    """Polygon (list of (lat, lon)) approximating a circle of `radius_km` around a point."""
    return [destination_point(lat, lon, b, radius_km) for b in
            [i * (360.0 / n_points) for i in range(n_points + 1)]]


# --------------------------------------------------------------------------- #
# Convenience wrappers keyed by well_id (used throughout geo/*). Import here
# rather than in every caller to avoid repeating the db.get_well boilerplate.
# --------------------------------------------------------------------------- #
def haversine_km_wells(well_a: str, well_b: str) -> Optional[float]:
    from nwis import db
    A, B = db.get_well(well_a), db.get_well(well_b)
    if A is None or B is None:
        return None
    return haversine_km(A.lat, A.lon, B.lat, B.lon)


def bearing_deg_wells(well_a: str, well_b: str) -> Optional[float]:
    """Bearing travelling from well_a to well_b."""
    from nwis import db
    A, B = db.get_well(well_a), db.get_well(well_b)
    if A is None or B is None:
        return None
    return bearing_deg(A.lat, A.lon, B.lat, B.lon)
