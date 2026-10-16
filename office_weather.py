"""Bounded, in-memory weather feed for the user's selected office city.

Coordinates: Open-Meteo geocoding Uijeongbu-si (KR), 37.7415, 127.0474.
API and WMO codes: https://open-meteo.com/en/docs
"""
import datetime
import json
import math
import threading
import time
import urllib.request

URL = ('https://api.open-meteo.com/v1/forecast?latitude=37.7415&longitude=127.0474'
       '&current=temperature_2m,apparent_temperature,weather_code,is_day'
       '&timezone=Asia%2FSeoul&forecast_days=1')
_LOCK = threading.Lock()
_GOOD = None
_FETCHED = 0.0
_ATTEMPT = None
_TTL = 600


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _number(value, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('Invalid weather measurement')
    return value


def _view(current_time):
    if _GOOD is None:
        return {'location': '의정부', 'status': 'unavailable', 'source': 'Open-Meteo'}
    return {**_GOOD, 'status': 'ok' if current_time - _FETCHED < _TTL else 'stale'}


def snapshot():
    """One fixed destination; one fetch per TTL, 60s failure backoff, no disk writes."""
    global _GOOD, _FETCHED, _ATTEMPT
    current_time = time.monotonic()
    if _GOOD is not None and current_time - _FETCHED < _TTL:
        return _view(current_time)
    if not _LOCK.acquire(blocking=False):
        return _view(current_time)
    try:
        current_time = time.monotonic()
        if (_GOOD is not None and current_time - _FETCHED < _TTL) or (_ATTEMPT is not None and current_time - _ATTEMPT < 60):
            return _view(current_time)
        _ATTEMPT = current_time
        try:
            request = urllib.request.Request(URL, headers={'Accept': 'application/json', 'User-Agent': 'AI-Office/1.0'})
            with urllib.request.build_opener(_NoRedirect).open(request, timeout=5) as response:
                raw = response.read(32769)
            if len(raw) > 32768:
                raise ValueError('Weather response too large')
            result = json.loads(raw)
            current = result['current']
            temperature = _number(current['temperature_2m'], -100, 70)
            apparent = _number(current['apparent_temperature'], -120, 100)
            code = current['weather_code']
            if type(code) is not int or code not in {0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}:
                raise ValueError('Invalid weather code')
            if type(current['is_day']) is not int or current['is_day'] not in (0, 1):
                raise ValueError('Invalid daylight flag')
            observed = datetime.datetime.fromisoformat(current['time'])
            if result.get('timezone') != 'Asia/Seoul' or result['current_units'].get('temperature_2m') != '°C':
                raise ValueError('Unexpected weather units or timezone')
            _GOOD = {'location': '의정부', 'temperature': temperature, 'apparent_temperature': apparent,
                     'weather_code': code, 'is_day': bool(current['is_day']),
                     'observed_at': observed.isoformat(timespec='minutes'),
                     'fetched_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
                     'source': 'Open-Meteo'}
            _FETCHED = time.monotonic()
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            pass
        return _view(time.monotonic())
    finally:
        _LOCK.release()
