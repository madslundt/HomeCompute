"""Operator-owned website profiles. Model inputs never configure a browser.

Profiles are private trusted configuration, not tool arguments or tool results.
Qualification records mode-specific implementation review, not account sign-in;
a separate deployed-worker gate must pass before execution is available.
"""
from __future__ import annotations

import copy
from datetime import date
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from openclaw_bitwarden import read_private

SCOPES = frozenset({'search', 'prepare_booking', 'prepare_purchase', 'read'})
OPERATIONS = frozenset({'search', 'prepare', 'read'})
LOGIN_MODES = frozenset({'automatic', 'owner'})
ACTIONS = frozenset({'navigate', 'login', 'fill', 'submit_search', 'read', 'prepare'})
HOST_PATTERN = r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?'
FORBIDDEN_FIELDS = frozenset({'url', 'uri', 'targeturl', 'selector', 'selectors', 'headers', 'cookie', 'cookies',
                              'password', 'credential', 'credentials', 'credentialid', 'secret', 'secretid',
                              'token', 'accesstoken', 'config', 'driver', 'javascript', 'script', 'command',
                              'username', 'login', 'loginmode', 'email'})
HOTEL_PARAMETERS = {'destination': {'type': 'string', 'minLength': 1, 'maxLength': 160},
                    'check_in': {'type': 'string', 'format': 'date', 'maxLength': 10},
                    'check_out': {'type': 'string', 'format': 'date', 'maxLength': 10},
                    'adults': {'type': 'integer', 'minimum': 1, 'maximum': 32},
                    'rooms': {'type': 'integer', 'minimum': 1, 'maximum': 16}}
DEFAULT_HOTELS = {'account': 'hotels.com', 'label': 'Hotels.com', 'driver': 'hotels',
                  'domains': ['hotels.com', 'www.hotels.com', 'da.hotels.com'],
                  'scopes': ['search', 'prepare_booking', 'read'], 'actions': sorted(ACTIONS),
                  'operations': {'search': {'scope': 'search', 'parameters': HOTEL_PARAMETERS,
                                             'required': list(HOTEL_PARAMETERS)},
                                 'prepare': {'scope': 'prepare_booking', 'parameters': HOTEL_PARAMETERS,
                                             'required': list(HOTEL_PARAMETERS)},
                                 'read': {'scope': 'read', 'parameters': {}, 'required': []}},
                  'driver_config': {'login_url': 'https://da.hotels.com/login'},
                  'login_mode': 'owner', 'qualified': False}


class SiteRefused(ValueError):
    """Fixed diagnostics only; profile and parameter values stay out of errors."""


def account_name(value: object) -> str:
    if (not isinstance(value, str) or not re.fullmatch(HOST_PATTERN, value)
            or value.endswith(('.localhost', '.local', '.internal', '.lan', '.home', '.arpa', '.ts.net'))):
        raise SiteRefused('Website identifier refused')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise SiteRefused('Website identifier refused')


def text(value: object, maximum: int) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise SiteRefused('Website profile refused')
    return value


def field_name(value: object) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', value)
            or value.replace('_', '') in FORBIDDEN_FIELDS):
        raise SiteRefused('Operation parameter field refused')
    return value


def parameter_envelope(value: object) -> dict:
    """Finite transport envelope only; the backend applies the registered schema."""
    if not isinstance(value, dict) or len(value) > 16 or len(json.dumps(value).encode()) > 4096:
        raise SiteRefused('Website operation parameters refused')
    for name, entry in value.items():
        field_name(name)
        if isinstance(entry, str):
            if (len(entry) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in entry)
                    or re.search(r'[A-Za-z][A-Za-z0-9+.-]*://|javascript:|data:', entry, re.I)):
                raise SiteRefused('Website operation parameters refused')
        elif type(entry) is not bool and (type(entry) is not int or not -100000 <= entry <= 100000):
            raise SiteRefused('Website operation parameters refused')
    return copy.deepcopy(value)


def https_url(value: object, domains: list[str], *, post: bool = False) -> str:
    value = text(value, 2048)
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname not in domains or parsed.username or parsed.password
            or parsed.netloc != parsed.hostname or parsed.fragment or '\\' in value or '{' in value or '}' in value
            or any(c.isspace() for c in value) or post and bool(parsed.query)):
        raise SiteRefused('Website endpoint refused')
    return value


def selector(value: object) -> str:
    value = text(value, 240)
    if any(fragment in value.lower() for fragment in ('javascript:', '<script', 'eval(', '=>')):
        raise SiteRefused('Website selector refused')
    return value


def parameter_rule(rule: object) -> dict:
    if (not isinstance(rule, dict) or not isinstance(rule.get('type'), str)
            or rule['type'] not in {'string', 'integer', 'boolean'}):
        raise SiteRefused('Operation parameter schema refused')
    kind = rule['type']
    if kind == 'string':
        if set(rule) - {'type', 'minLength', 'maxLength', 'enum', 'format'}:
            raise SiteRefused('Operation parameter schema refused')
        low, high = rule.get('minLength', 0), rule.get('maxLength', 256)
        if type(low) is not int or type(high) is not int or not 0 <= low <= high <= 512:
            raise SiteRefused('Operation string bounds refused')
        if 'format' in rule and rule['format'] != 'date':
            raise SiteRefused('Operation string format refused')
        if 'enum' in rule and (not isinstance(rule['enum'], list) or not 1 <= len(rule['enum']) <= 32
                               or any(not isinstance(v, str) or not low <= len(v) <= high for v in rule['enum'])):
            raise SiteRefused('Operation string choices refused')
    elif kind == 'integer':
        if set(rule) - {'type', 'minimum', 'maximum'}:
            raise SiteRefused('Operation parameter schema refused')
        low, high = rule.get('minimum', -100000), rule.get('maximum', 100000)
        if type(low) is not int or type(high) is not int or not -100000 <= low <= high <= 100000:
            raise SiteRefused('Operation integer bounds refused')
    elif set(rule) != {'type'}:
        raise SiteRefused('Operation parameter schema refused')
    return copy.deepcopy(rule)


def driver_configuration(profile: dict) -> None:
    config, domains = profile['driver_config'], profile['domains']
    if not isinstance(config, dict) or set(config) - {'login_url', 'login', 'operations', 'network_posts'}:
        raise SiteRefused('Website driver configuration refused')
    if profile['login_mode'] == 'owner' and 'login_url' not in config:
        raise SiteRefused('Owner sign-in requires a registered login endpoint')
    if 'login_url' in config:
        https_url(config['login_url'], domains)
    if 'login' in config:
        login = config['login']
        required = {'username_selector', 'password_selector', 'submit_selector', 'success_selector'}
        if (not isinstance(login, dict) or not required <= set(login)
                or set(login) - required - {'username_continue_selector'} or 'login_url' not in config):
            raise SiteRefused('Website login configuration refused')
        for value in login.values():
            selector(value)
        if not {'navigate', 'login', 'fill'} <= set(profile['actions']):
            raise SiteRefused('Website login action policy differs')
    posts = config.get('network_posts', {})
    if not isinstance(posts, dict) or len(posts) > 32:
        raise SiteRefused('Website network policy refused')
    for url, purposes in posts.items():
        https_url(url, domains, post=True)
        if (not isinstance(purposes, list) or not purposes or any(not isinstance(v, str) for v in purposes)
                or len(set(purposes)) != len(purposes)
                or any(value not in {'LOGIN', 'READ'} for value in purposes)):
            raise SiteRefused('Website network policy refused')
    operations = config.get('operations', {})
    if not isinstance(operations, dict) or set(operations) - set(profile['operations']):
        raise SiteRefused('Website form operations refused')
    if profile['driver'] == 'form' and set(operations) != set(profile['operations']):
        raise SiteRefused('Complete reviewed form operations required')
    for operation, specification in operations.items():
        if (not isinstance(specification, dict) or not {'url', 'fields'} <= set(specification)
                or set(specification) - {'url', 'fields', 'submit_selector', 'result_selector'}):
            raise SiteRefused('Website form operation refused')
        https_url(specification['url'], domains)
        fields = specification['fields']
        if not isinstance(fields, dict) or set(fields) - set(profile['operations'][operation]['parameters']):
            raise SiteRefused('Website form fields refused')
        if operation == 'read' and fields:
            raise SiteRefused('Read operation cannot fill a form')
        for value in fields.values():
            selector(value)
        if 'submit_selector' in specification:
            if operation != 'search' or 'submit_search' not in profile['actions']:
                raise SiteRefused('Preparation cannot submit a transaction')
            selector(specification['submit_selector'])
        if 'result_selector' in specification:
            selector(specification['result_selector'])
        required_actions = {'navigate', 'read'} | ({'fill'} if fields else set())
        if not required_actions <= set(profile['actions']):
            raise SiteRefused('Website action policy differs')


def validate_profile(value: object) -> dict:
    required = {'account', 'label', 'driver', 'domains', 'scopes', 'actions', 'operations', 'driver_config'}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {'qualified', 'login_mode'}:
        raise SiteRefused('Website profile refused')
    profile = copy.deepcopy(value)
    account_name(profile['account'])
    text(profile['label'], 80)
    if (not isinstance(profile['driver'], str) or profile['driver'] not in {'hotels', 'form'}
            or type(profile.get('qualified', False)) is not bool):
        raise SiteRefused('Website driver refused')
    profile.setdefault('qualified', False)
    profile.setdefault('login_mode', 'automatic')
    if not isinstance(profile['login_mode'], str) or profile['login_mode'] not in LOGIN_MODES:
        raise SiteRefused('Website login mode refused')
    for key, maximum in [('domains', 24), ('scopes', 4), ('actions', 6)]:
        entries = profile[key]
        if (not isinstance(entries, list) or not 1 <= len(entries) <= maximum
                or any(not isinstance(v, str) for v in entries) or len(set(entries)) != len(entries)):
            raise SiteRefused('Website policy list refused')
    for domain in profile['domains']:
        account_name(domain)
    if profile['account'] not in profile['domains']:
        raise SiteRefused('Website account domain must be registered')
    if not set(profile['scopes']) <= SCOPES or not set(profile['actions']) <= ACTIONS:
        raise SiteRefused('Website action policy refused')
    operations = profile['operations']
    if not isinstance(operations, dict) or not operations or set(operations) - OPERATIONS:
        raise SiteRefused('Website operations refused')
    for operation, specification in operations.items():
        if (not isinstance(specification, dict) or set(specification) != {'scope', 'parameters', 'required'}
                or specification['scope'] not in profile['scopes']
                or operation == 'search' and specification['scope'] != 'search'
                or operation == 'read' and specification['scope'] != 'read'
                or operation == 'prepare' and specification['scope'] not in {'prepare_booking', 'prepare_purchase'}):
            raise SiteRefused('Website operation scope refused')
        parameters, required_fields = specification['parameters'], specification['required']
        if (not isinstance(parameters, dict) or len(parameters) > 16 or not isinstance(required_fields, list)
                or any(not isinstance(name, str) for name in required_fields)
                or len(set(required_fields)) != len(required_fields) or not set(required_fields) <= set(parameters)):
            raise SiteRefused('Website operation fields refused')
        for name, rule in parameters.items():
            field_name(name)
            parameter_rule(rule)
    if {value['scope'] for value in operations.values()} != set(profile['scopes']):
        raise SiteRefused('Website scope has no registered operation')
    if profile['driver'] == 'hotels' and not {'navigate', 'login', 'fill', 'submit_search', 'read'} <= set(profile['actions']):
        raise SiteRefused('Hotels driver action policy differs')
    driver_configuration(profile)
    return profile


class SiteRegistry:
    def __init__(self, profiles: list[dict] | None = None):
        profiles = [DEFAULT_HOTELS] if profiles is None else profiles
        if not isinstance(profiles, list) or len(profiles) > 32:
            raise SiteRefused('Website registry refused')
        self._profiles = {}
        for candidate in profiles:
            profile = validate_profile(candidate)
            if profile['account'] in self._profiles:
                raise SiteRefused('Duplicate website profile refused')
            self._profiles[profile['account']] = profile

    @classmethod
    def from_file(cls, path: Path):
        def unique(pairs):
            value = {}
            for key, entry in pairs:
                if key in value:
                    raise SiteRefused('Duplicate website configuration field refused')
                value[key] = entry
            return value
        config = json.loads(read_private(path.parent, path.name, 65536), object_pairs_hook=unique)
        if (not isinstance(config, dict) or set(config) != {'schema_version', 'sites'}
                or type(config['schema_version']) is not int or config['schema_version'] != 1):
            raise SiteRefused('Website registry refused')
        return cls(config['sites'])

    def accounts(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def profile(self, account: str) -> dict:
        account_name(account)
        if account not in self._profiles:
            raise SiteRefused('Website is not registered')
        return copy.deepcopy(self._profiles[account])

    def validate_scope(self, account: str, scope: str) -> str:
        profile = self.profile(account)
        if not isinstance(scope, str) or scope not in profile['scopes']:
            raise SiteRefused('Website scope is not registered')
        return scope

    def operation_scope(self, account: str, operation: str) -> str:
        profile = self.profile(account)
        if not isinstance(operation, str) or operation not in profile['operations']:
            raise SiteRefused('Website operation is not registered')
        return profile['operations'][operation]['scope']

    def allows_url(self, account: str, url: str) -> bool:
        try:
            https_url(url, self.profile(account)['domains'])
            return True
        except (ValueError, TypeError):
            return False

    def validate_parameters(self, account: str, operation: str, parameters: object) -> dict:
        profile = self.profile(account)
        self.operation_scope(account, operation)
        specification = profile['operations'][operation]
        if (not isinstance(parameters, dict) or not set(specification['required']) <= set(parameters)
                or set(parameters) - set(specification['parameters']) or len(json.dumps(parameters).encode()) > 4096):
            raise SiteRefused('Website operation parameters refused')
        for name, value in parameters.items():
            rule = specification['parameters'][name]
            if rule['type'] == 'string':
                if (not isinstance(value, str) or not rule.get('minLength', 0) <= len(value) <= rule.get('maxLength', 256)
                        or any(ord(c) < 32 or ord(c) == 127 for c in value)
                        or re.search(r'[A-Za-z][A-Za-z0-9+.-]*://|javascript:|data:', value, re.I)
                        or 'enum' in rule and value not in rule['enum']):
                    raise SiteRefused('Website text parameter refused')
                if rule.get('format') == 'date':
                    try:
                        if date.fromisoformat(value).isoformat() != value:
                            raise ValueError
                    except ValueError:
                        raise SiteRefused('Website date parameter refused') from None
            elif rule['type'] == 'integer':
                if type(value) is not int or not rule.get('minimum', -100000) <= value <= rule.get('maximum', 100000):
                    raise SiteRefused('Website integer parameter refused')
            elif type(value) is not bool:
                raise SiteRefused('Website boolean parameter refused')
        if 'check_in' in parameters and 'check_out' in parameters:
            start, end = date.fromisoformat(parameters['check_in']), date.fromisoformat(parameters['check_out'])
            if not 1 <= (end - start).days <= 365:
                raise SiteRefused('Website stay duration refused')
        return copy.deepcopy(parameters)
