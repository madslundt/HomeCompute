"""Weather evidence must meet every stay day's limits before supplier checks."""
import copy
import importlib.util
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlparse

SPEC = importlib.util.spec_from_file_location('travel_supplier_fixtures',
        Path(__file__).with_name('travel-supplier-price-test.py'))
supplier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supplier)


def forecast_fixture():
    context, _ = supplier.fixture()
    context['weatherMode'] = 'forecast'
    context['weatherDays'] = ['2026-11-01', '2026-11-02']
    daily = {'time': context['weatherDays'].copy(), 'temperature_2m_max': [25, 27],
             'sunshine_duration': [21600, 25200], 'precipitation_sum': [1, 0],
             'wind_speed_10m_max': [30, 20], 'precipitation_probability_max': [30, 10]}
    response = {'latitude': 27.2, 'longitude': 33.8, 'daily': daily,
                'daily_units': {'temperature_2m_max': '°C', 'sunshine_duration': 's',
                                'precipitation_sum': 'mm', 'wind_speed_10m_max': 'km/h',
                                'precipitation_probability_max': '%'}}
    return context, response


def historical_fixture():
    context, response = forecast_fixture()
    context['weatherMode'] = 'historical'
    response['daily'] = {'time': [f'{year}-11-{day:02}' for year in range(2021, 2026) for day in [1, 2]],
                         'temperature_2m_max': [25] * 10, 'sunshine_duration': [21600] * 10,
                         'precipitation_sum': [0] * 10, 'wind_speed_10m_max': [30] * 10}
    response['daily_units'].pop('precipitation_probability_max')
    return context, response


def check(context=None, response=None):
    default_context, default_response = forecast_fixture()
    return supplier.run_code('check-weather.js', [{'json': response or default_response}],
                            {'Prepare supplier checks': [{'json': context or default_context}]})


def prepare(context):
    context['offer']['redirect_url'] = '/charterredirect?id=synthetic&age=42-42-8-4'
    raw = {'offers': [context['offer']], 'n_search_results': 1, 'dataLayer': context['dataLayer']}
    return supplier.run_code('prepare-supplier-checks.js', [{'json': raw}],
                            {'Build exact age searches': [{'json': context['request']}]})


class TravelWeatherTests(unittest.TestCase):
    def assert_rejected(self, result):
        self.assertTrue('error' in result or result.get('value') == [], result)

    def test_forecast_accepts_limits_for_every_day_and_labels_evidence(self):
        weather = check()['value'][0]['json']['offer']['weather']
        self.assertTrue(weather['verified'])
        self.assertEqual(weather['basis'], 'forecast')
        self.assertEqual(weather['minDailyHigh'], 25)
        self.assertEqual(weather['maxDailyHigh'], 27)
        self.assertEqual(weather['minSunshineHours'], 6)
        self.assertEqual(weather['maxDailyRainMm'], 1)
        self.assertIsNone(weather['sampleYears'])

    def test_one_bad_forecast_day_rejects_whole_trip(self):
        for key, value in [('temperature_2m_max', 24.9), ('precipitation_probability_max', 31),
                           ('precipitation_sum', 2), ('sunshine_duration', 5.9 * 3600),
                           ('wind_speed_10m_max', 31)]:
            context, response = forecast_fixture(); response['daily'][key][1] = value
            with self.subTest(key=key, value=value):
                self.assert_rejected(check(context, response))

    def test_missing_or_null_daily_weather_cannot_qualify(self):
        for key in ['temperature_2m_max', 'precipitation_probability_max', 'precipitation_sum',
                    'sunshine_duration', 'wind_speed_10m_max']:
            context, response = forecast_fixture(); response['daily'][key][1] = None
            with self.subTest(key=key):
                self.assert_rejected(check(context, response))
        context, response = forecast_fixture(); response['daily']['time'].pop()
        self.assert_rejected(check(context, response))

    def test_wrong_units_or_location_and_provider_error_are_rejected(self):
        for key, value in [('temperature_2m_max', '°F'), ('sunshine_duration', 'h'),
                           ('precipitation_sum', 'inch'), ('wind_speed_10m_max', 'mph'),
                           ('precipitation_probability_max', 'fraction')]:
            context, response = forecast_fixture(); response['daily_units'][key] = value
            with self.subTest(key=key):
                self.assert_rejected(check(context, response))
        for changes in [{'latitude': 0}, {'longitude': 0}, {'error': True}]:
            context, response = forecast_fixture(); response.update(changes)
            self.assert_rejected(check(context, response))

    def test_five_year_history_is_explicitly_labelled_and_accepts_twenty_percent_wet_days(self):
        context, response = historical_fixture()
        response['daily']['precipitation_sum'][0] = 5  # One of five years, mean 1 mm.
        weather = check(context, response)['value'][0]['json']['offer']['weather']
        self.assertEqual(weather['basis'], 'historical')
        self.assertEqual(weather['sampleYears'], 5)
        self.assertEqual(weather['minDailyHigh'], 25)
        self.assertEqual(weather['maxDailyRainMm'], 1)

    def test_two_rainy_years_for_one_stay_day_fail_even_when_mean_rain_is_low(self):
        context, response = historical_fixture()
        response['daily']['precipitation_sum'][0] = 2
        response['daily']['precipitation_sum'][2] = 2  # Mean .8 mm, but wet fraction .4.
        self.assert_rejected(check(context, response))

    def test_missing_one_historical_year_or_cool_mean_fails(self):
        context, response = historical_fixture(); response['daily']['time'].pop()
        self.assert_rejected(check(context, response))
        context, response = historical_fixture(); response['daily']['temperature_2m_max'][0] = 24
        self.assert_rejected(check(context, response))

    def test_duplicate_dates_do_not_count_as_five_distinct_historical_years(self):
        context, response = historical_fixture()
        response['daily']['time'] = ['2025-11-01', '2025-11-02'] * 5
        self.assert_rejected(check(context, response))

    def test_prepare_uses_forecast_when_whole_stay_is_inside_fifteen_day_horizon(self):
        context, _ = supplier.fixture()
        context['offer']['origin']['dt'] = '2026-10-27T06:10:00Z'
        context['offer']['return']['dt'] = '2026-11-03T10:35:00Z'
        result = prepare(context)['value'][0]['json']
        self.assertEqual(result['weatherMode'], 'forecast')
        self.assertEqual(result['weatherDays'], ['2026-10-27', '2026-10-28', '2026-10-29',
                                                '2026-10-30', '2026-10-31', '2026-11-01', '2026-11-02'])
        url = urlparse(result['weatherUrl']); query = parse_qs(url.query)
        self.assertEqual(url.netloc, 'api.open-meteo.com')
        self.assertEqual(query['forecast_days'], ['16'])
        self.assertIn('precipitation_probability_max', query['daily'][0])

    def test_prepare_seasonal_archive_never_calls_it_a_forecast_and_can_be_disabled(self):
        context, _ = supplier.fixture()
        result = prepare(context)['value'][0]['json']
        self.assertEqual(result['weatherMode'], 'historical')
        url = urlparse(result['weatherUrl']); query = parse_qs(url.query)
        self.assertEqual(url.netloc, 'archive-api.open-meteo.com')
        self.assertEqual(query['start_date'], ['2021-12-04'])
        self.assertEqual(query['end_date'], ['2025-12-10'])
        self.assertEqual(query['models'], ['era5'])
        context['request']['preferences']['weatherMode'] = 'forecast_only'
        self.assertEqual(prepare(context)['value'], [])

    def test_queries_separate_near_return_horizon_from_seasonal_temperature_search(self):
        context, _ = supplier.fixture()
        preferences = context['request']['preferences']
        result = supplier.run_code('build-searches.js', [{'json': {'preferences': preferences}}])
        rows = result['value']
        self.assertEqual(len(rows), 2)
        queries = [parse_qs(urlparse(row['json']['url']).query) for row in rows]
        near = next(query for query in queries if 'lretdate' in query)
        seasonal = next(query for query in queries if 'temp_air_day' in query)
        self.assertEqual(near['edepdate'], ['2026-10-26'])
        self.assertEqual(near['ldepdate'], ['2026-11-02'])
        self.assertEqual(near['lretdate'], ['2026-11-09'])
        self.assertEqual(seasonal['ldepdate'], ['2026-12-24'])
        self.assertEqual(seasonal['temp_air_day'], ['25,42'])
        preferences = copy.deepcopy(preferences); preferences['weatherMode'] = 'forecast_only'
        rows = supplier.run_code('build-searches.js', [{'json': {'preferences': preferences}}])['value']
        self.assertEqual(len(rows), 1)
        query = parse_qs(urlparse(rows[0]['json']['url']).query)
        self.assertIn('lretdate', query)
        self.assertNotIn('temp_air_day', query)


if __name__ == '__main__':
    unittest.main()
