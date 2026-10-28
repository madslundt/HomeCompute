"""Reviewed form recipes. No model-supplied selectors, URLs or browser code."""
from __future__ import annotations

import copy
from datetime import date
import re
from urllib.parse import urlencode

from openclaw_bitwarden import Login


class SiteStopped(RuntimeError):
    """Fixed status only; never use browser exceptions or content as messages."""
    def __init__(self, status: str):
        self.status = status
        super().__init__(status)


CURRENT_INPUT = 'input[autocomplete="current-password"]'
HOTELS = {
    'login_url': 'https://da.hotels.com/login',
    'login': {'username_selector': 'input[type="email"]',
              'username_continue_selector': 'button[type="submit"]',
              'password_selector': CURRENT_INPUT,
              'submit_selector': 'button[type="submit"]',
              # A missing positive signed-in marker requires human attention;
              # the presence or disappearance of a password field is not proof.
              'success_selector': 'a[href*="/logout"],a[href*="/signout"]'},
    'operations': {}, 'network_posts': {},
}
CHALLENGE = ('iframe[src*="captcha" i],[data-sitekey],input[autocomplete="one-time-code"],'
             'input[autocomplete="new-password"],input[name*="otp" i],'
             'input[name*="verificationCode" i],input[name="authenticationCode" i],'
             'input[name="securityCode" i],input[name="passcode" i],input[name*="terms" i],'
             '[data-testid*="mfa-challenge" i],[data-testid*="push-challenge" i]')
MFA_PROMPT = re.compile(r'^\s*(?:enter (?:the |your |a )?(?:verification|authentication|one.time|security) code'
                        r'|check your (?:phone|device|authenticator)|approve (?:the |this )?(?:sign.in|login)'
                        r'|(?:two.(?:step|factor)|multi.factor) (?:verification|authentication)'
                        r'|verify your identity|indtast (?:din |den )?(?:bekræftelses|engangs|sikkerheds)kode'
                        r'|godkend (?:dit |dette )?login|bekræft din identitet)\b', re.I)


def challenge_present(page) -> bool:
    """Return a boolean only; never extract a challenge, code or page text."""
    for frame in page.frames[:32]:
        for locator in (frame.locator(CHALLENGE), frame.get_by_text(MFA_PROMPT)):
            for index in range(min(locator.count(), 32)):
                if locator.nth(index).is_visible():
                    return True
    return False


def credential_input(element, kind: str) -> bool:
    """Check structural attributes only; never read field values or OTP data."""
    attributes = {key: (element.get_attribute(key) or '').lower()
                  for key in ('type', 'autocomplete', 'name', 'id')}
    attributes['type'] = attributes['type'] or 'text'
    tokens = attributes['autocomplete'].split()
    names = [re.sub(r'[^a-z0-9]', '', attributes[key]) for key in ('name', 'id')]
    if ('one-time-code' in tokens or 'new-password' in tokens
            or any(re.search(r'otp|totp|onetime|mfa|verification|authenticator|recovery|backupcode|seed|secret|passcode|code|newpassword|confirmpassword|repeatpassword', name)
                   or re.fullmatch(r'pin\d*', name) for name in names)):
        return False
    if kind == 'password':
        return attributes['type'] == 'password'
    return (attributes['type'] == 'email' or attributes['type'] == 'text'
            and (bool({'username', 'email'} & set(tokens)) or bool(set(names) &
                 {'email', 'username', 'emailaddress', 'userid'})))


def validated_parameters(profile: dict, operation: str, parameters: object) -> dict:
    from openclaw_site_registry import parameter_envelope
    parameters = parameter_envelope(parameters)
    if operation not in {'search', 'prepare', 'read'} or operation not in profile['operations']:
        raise SiteStopped('unavailable')
    definition = profile['operations'][operation]
    if (not isinstance(parameters, dict) or set(parameters) - set(definition['parameters'])
            or not set(definition['required']) <= set(parameters)):
        raise SiteStopped('unavailable')
    for name, value in parameters.items():
        rule = definition['parameters'][name]
        kind = rule['type']
        if kind == 'string':
            if (not isinstance(value, str) or not rule.get('minLength', 0) <= len(value) <= rule.get('maxLength', 256)
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                raise SiteStopped('unavailable')
            if rule.get('format') == 'date' and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)
                                                 or date.fromisoformat(value).isoformat() != value):
                raise SiteStopped('unavailable')
        elif kind == 'integer':
            if type(value) is not int or not rule.get('minimum', 0) <= value <= rule.get('maximum', 100000):
                raise SiteStopped('unavailable')
        elif kind == 'boolean':
            if type(value) is not bool:
                raise SiteStopped('unavailable')
        else:
            raise SiteStopped('unavailable')
        if 'enum' in rule and value not in rule['enum']:
            raise SiteStopped('unavailable')
    if 'check_in' in parameters and 'check_out' in parameters:
        if date.fromisoformat(parameters['check_out']) <= date.fromisoformat(parameters['check_in']):
            raise SiteStopped('unavailable')
    return copy.deepcopy(parameters)


def driver_config(profile: dict) -> dict:
    if profile['driver'] == 'hotels':
        if profile['account'] != 'hotels.com':
            raise SiteStopped('unavailable')
        config = copy.deepcopy(HOTELS)
        # Every override has already passed the trusted registry validator.
        config.update(profile.get('driver_config', {}))
        return config
    if profile['driver'] == 'form':
        return copy.deepcopy(profile['driver_config'])
    raise SiteStopped('unavailable')


class FormDriver:
    def __init__(self, profile: dict, page, guard):
        self.profile, self.page, self.guard = profile, page, guard
        self.config = driver_config(profile)

    def check_challenge(self):
        if self.guard.clock() >= self.guard.deadline:
            raise SiteStopped('expired')
        if challenge_present(self.page):
            raise SiteStopped('human_sign_in_required')
        self.guard.step()

    def locate(self, selector: str):
        self.guard.step()
        locator = self.page.locator(selector)
        if locator.count() != 1:
            raise SiteStopped('human_sign_in_required' if self.guard.phase == 'login' else 'unavailable')
        return locator

    def navigate(self, url: str):
        self.guard.require_action('navigate')
        self.guard.allowed(url, 'GET')
        self.page.goto(url, wait_until='domcontentloaded', timeout=self.guard.timeout())
        self.check_challenge()

    def login(self, login: Login):
        self.guard.phase = 'login'
        self.guard.require_action('login')
        recipe = self.config['login']
        self.navigate(self.config['login_url'])
        self.check_challenge()
        self.guard.require_action('fill')
        self.locate(recipe['username_selector']).fill(login.username, timeout=self.guard.timeout())
        password = self.page.locator(recipe['password_selector'])
        if not password.count() and recipe.get('username_continue_selector'):
            self.locate(recipe['username_continue_selector']).click(timeout=self.guard.timeout())
            self.check_challenge()
            password.wait_for(state='visible', timeout=self.guard.timeout())
        self.check_challenge()
        self.locate(recipe['password_selector']).fill(login.password, timeout=self.guard.timeout())
        self.locate(recipe['submit_selector']).click(timeout=self.guard.timeout())
        self.check_challenge()
        marker = self.page.locator(recipe['success_selector'])
        marker.wait_for(state='attached', timeout=self.guard.timeout())
        if marker.count() < 1:
            raise SiteStopped('human_sign_in_required')
        self.guard.step()

    def operate(self, operation: str, parameters: dict) -> int:
        self.guard.phase = operation
        self.check_challenge()
        if self.profile['driver'] == 'hotels':
            if operation == 'read':
                target = 'https://da.hotels.com/'
            else:
                query = {'destination': parameters['destination'], 'startDate': parameters['check_in'],
                         'endDate': parameters['check_out'], 'adults': parameters['adults'],
                         'rooms': parameters['rooms']}
                target = 'https://da.hotels.com/Hotel-Search?' + urlencode(query)
            self.navigate(target)
            result_selector = '[data-stid="lodging-card-responsive"]'
        else:
            recipe = self.config['operations'][operation]
            self.navigate(recipe['url'])
            for name, selector in recipe.get('fields', {}).items():
                if name not in parameters:
                    continue
                self.check_challenge()
                self.guard.require_action('fill')
                value = parameters[name]
                locator = self.locate(selector)
                if type(value) is bool:
                    locator.set_checked(value, timeout=self.guard.timeout())
                else:
                    locator.fill(str(value), timeout=self.guard.timeout())
            if operation == 'search' and recipe.get('submit_selector'):
                self.guard.require_action('submit_search')
                self.locate(recipe['submit_selector']).click(timeout=self.guard.timeout())
                self.check_challenge()
            # No other operation can click a submit control, including prepare.
            result_selector = recipe.get('result_selector')
        self.guard.require_action('prepare' if operation == 'prepare' else 'read')
        self.check_challenge()
        count = self.page.locator(result_selector).count() if result_selector else 0
        if type(count) is not int:
            raise SiteStopped('unavailable')
        self.guard.step()
        return min(max(count, 0), 100)
