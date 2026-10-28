"""Finite Geoapify and Open-Meteo reads; caller cannot select origins or headers."""
from __future__ import annotations

import json
import math
from typing import Any, Callable
import urllib.parse

GEO = 'https://api.geoapify.com'
WEATHER = 'https://api.open-meteo.com/v1/forecast'
CATEGORIES = {'restaurants': 'catering.restaurant', 'cafes': 'catering.cafe',
              'shops': 'commercial', 'parks': 'leisure.park',
              'attractions': 'tourism.attraction', 'hotels': 'accommodation.hotel'}
ATTRIBUTION = [{'name': 'Geoapify', 'url': 'https://www.geoapify.com/'},
               {'name': 'OpenStreetMap contributors', 'url': 'https://www.openstreetmap.org/copyright'}]


class AmbiguousPlace(ValueError):
    def __init__(self, candidates):
        super().__init__('More specific location required')
        self.candidates = candidates


def coordinates(row: Any) -> tuple[float, float]:
    if not isinstance(row, dict): raise ValueError('Place response refused')
    lat, lon = row.get('lat'), row.get('lon')
    if (type(lat) not in {int, float} or type(lon) not in {int, float}
            or not math.isfinite(lat) or not math.isfinite(lon)
            or not -90 <= lat <= 90 or not -180 <= lon <= 180):
        raise ValueError('Place coordinates refused')
    return lat, lon


def place(row: dict) -> dict:
    lat, lon = coordinates(row)
    result = {key: row[key] for key in ('name', 'formatted', 'city', 'country', 'categories', 'place_id') if key in row}
    result.update(latitude=lat, longitude=lon,
                  url='https://www.openstreetmap.org/?' + urllib.parse.urlencode({'mlat': lat, 'mlon': lon}))
    source = row.get('datasource')
    if isinstance(source, dict):
        result['datasource'] = {key: source[key] for key in ('sourcename', 'attribution', 'license', 'url') if key in source}
    return result


def read(name: str, args: dict, token: str, deadline: float, http: Callable) -> dict:
    def get(endpoint, parameters, *, auth=True):
        params = {**parameters, **({'apiKey': token} if auth else {})}
        raw, _ = http(endpoint + '?' + urllib.parse.urlencode(params), None, {}, deadline)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('error') or payload.get('statusCode', 200) != 200:
            raise ValueError('Places provider response refused')
        return payload

    def geocode(query):
        rows = get(GEO + '/v1/geocode/search', {'text': query, 'format': 'json', 'limit': 2})
        rows = rows.get('results')
        if not isinstance(rows, list) or not rows: raise ValueError('Location not resolved')
        choices = [place(row) for row in rows[:2]]
        confidence = rows[0].get('rank', {}).get('confidence')
        if type(confidence) not in {int, float} or not math.isfinite(confidence) or confidence < 0.8:
            raise AmbiguousPlace(choices)
        if len(choices) == 2:
            second = rows[1].get('rank', {}).get('confidence', 0)
            distance = abs(choices[0]['latitude'] - choices[1]['latitude']) + abs(choices[0]['longitude'] - choices[1]['longitude'])
            if type(second) not in {int, float} or not math.isfinite(second): raise ValueError('Location confidence refused')
            if distance > 0.05 and confidence - second < 0.1: raise AmbiguousPlace(choices)
        return choices[0]

    if name == 'places_search':
        location = geocode(args['query'])
        if 'category' not in args:
            return {'places': [location], 'attribution': ATTRIBUTION,
                    'completeness': 'Resolved named place or address. Supply a category and city/area query for nearby discovery.'}
        lon, lat = location['longitude'], location['latitude']
        params = {'categories': CATEGORIES[args['category']], 'filter': f'circle:{lon},{lat},5000',
                  'bias': f'proximity:{lon},{lat}', 'limit': 5, 'lang': 'da'}
        if 'name' in args: params['name'] = args['name']
        rows = get(GEO + '/v2/places', params).get('features')
        if not isinstance(rows, list): raise ValueError('Place list refused')
        return {'resolved_location': location, 'places': [place(row['properties']) for row in rows[:5]],
                'attribution': ATTRIBUTION, 'completeness': 'At most five places within 5 km; coverage and opening hours require verification.'}
    if name == 'places_route':
        origin, destination = geocode(args['origin']), geocode(args['destination'])
        result = get(GEO + '/v1/routing', {'waypoints': f"{origin['latitude']},{origin['longitude']}|{destination['latitude']},{destination['longitude']}",
                     'mode': 'drive' if args['mode'] == 'DRIVE' else 'walk', 'units': 'metric'})
        features = result.get('features')
        if not isinstance(features, list) or not features: raise ValueError('Route response refused')
        row = features[0]['properties']
        for key in ('distance', 'time'):
            if type(row.get(key)) not in {int, float} or not math.isfinite(row[key]) or row[key] < 0:
                raise ValueError('Route estimate refused')
        return {'origin': origin, 'destination': destination, 'mode': args['mode'],
                'distance_metres': row['distance'], 'duration_seconds': row['time'],
                'attribution': ATTRIBUTION, 'completeness': 'First route estimate; no live traffic or navigation.'}
    location = geocode(args['location'])
    params = {'latitude': location['latitude'], 'longitude': location['longitude'], 'timezone': 'auto'}
    if 'date' in args:
        params.update(start_date=args['date'], end_date=args['date'],
                      daily='temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code')
    else:
        params['current'] = 'temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m'
    result = get(WEATHER, params, auth=False)
    required = ('daily', 'daily_units') if 'date' in args else ('current', 'current_units')
    if any(not isinstance(result.get(key), dict) for key in required): raise ValueError('Weather response refused')
    if 'date' in args and result['daily'].get('time') != [args['date']]: raise ValueError('Weather date refused')
    return {'resolved_location': location,
            'weather': {key: result[key] for key in ('latitude', 'longitude', 'timezone', *required) if key in result},
            'attribution': [*ATTRIBUTION, {'name': 'Open-Meteo', 'url': 'https://open-meteo.com/',
                                         'license': 'https://creativecommons.org/licenses/by/4.0/'}],
            'completeness': 'Forecast/current conditions, not guaranteed weather. Dates use the resolved location timezone.'}
