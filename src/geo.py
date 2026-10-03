"""Géométrie simple en WGS84 (SRID 4326) sans dépendance SIG côté Python.

Les géométries sont produites en EWKT ('SRID=4326;POLYGON(...)'), que PostGIS
accepte directement à l'insertion (COPY).
"""
from __future__ import annotations

import math

import numpy as np

KM_PAR_DEGRE_LAT = 111.32


def km_par_degre_lon(lat: float) -> float:
    return KM_PAR_DEGRE_LAT * math.cos(math.radians(lat))


def rectangle(lon_c: float, lat_c: float, aire_km2: float, ratio: float) -> tuple[float, float, float, float]:
    """Rectangle (lon_min, lat_min, lon_max, lat_max) centré, d'aire ≈ aire_km2, largeur/hauteur = ratio."""
    h_km = math.sqrt(aire_km2 / ratio)
    w_km = aire_km2 / h_km
    dlat = h_km / KM_PAR_DEGRE_LAT / 2
    dlon = w_km / km_par_degre_lon(lat_c) / 2
    return (lon_c - dlon, lat_c - dlat, lon_c + dlon, lat_c + dlat)


def ewkt_polygone(b: tuple[float, float, float, float]) -> str:
    x0, y0, x1, y1 = (round(v, 6) for v in b)
    return f"SRID=4326;POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def ewkt_point(lon: float, lat: float) -> str:
    return f"SRID=4326;POINT({round(lon, 6)} {round(lat, 6)})"


def bande(b: tuple[float, float, float, float], fraction: float, cote: int) -> tuple[float, float, float, float]:
    """Bande d'un côté du rectangle couvrant `fraction` de sa surface (surface rendue)."""
    x0, y0, x1, y1 = b
    if cote == 0:
        return (x0, y0, x0 + (x1 - x0) * fraction, y1)
    if cote == 1:
        return (x1 - (x1 - x0) * fraction, y0, x1, y1)
    if cote == 2:
        return (x0, y0, x1, y0 + (y1 - y0) * fraction)
    return (x0, y1 - (y1 - y0) * fraction, x1, y1)


def point_dans(b: tuple[float, float, float, float], rng: np.random.Generator, marge: float = 0.05) -> tuple[float, float]:
    x0, y0, x1, y1 = b
    mx, my = (x1 - x0) * marge, (y1 - y0) * marge
    return float(rng.uniform(x0 + mx, x1 - mx)), float(rng.uniform(y0 + my, y1 - my))


def distance_km(lon1, lat1, lon2, lat2):
    """Distance haversine (km), vectorisée."""
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))
