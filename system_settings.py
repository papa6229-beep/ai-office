"""Bounded Windows Settings entry points and read-only association evidence.

Launching Settings dispatches navigation; it does not change any setting. The
caller must observe the resulting Windows UI, perform an authorized action, and
verify it separately. No registry values are written by this module.

URI reference: https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings
Application targeting: https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-default-apps-settings
"""

import hashlib
import os
from urllib.parse import quote


SECTIONS = {
    'default_apps': 'ms-settings:defaultapps',
    'display': 'ms-settings:display',
    'sound': 'ms-settings:sound',
    'notifications': 'ms-settings:notifications',
    'storage': 'ms-settings:storagesense',
    'apps': 'ms-settings:appsfeatures',
}
_REGISTERED = r'Software\RegisteredApplications'
_ASSOCIATIONS = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations'
_MAX_REGISTRY_VALUES = 512


def _registry(registry):
    if registry is not None:
        return registry
    if os.name != 'nt':
        return None
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def _safe_string(value, limit=256):
    return (isinstance(value, str) and 0 < len(value) <= limit
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _app_id(scope, name):
    return 'settings_app_' + hashlib.sha256((scope + '\0' + name).encode('utf-8')).hexdigest()[:24]


def registered_apps(*, registry=None):
    """Return named RegisteredApplications entries, bounded and read-only.

    Both registry views are checked so a 32-bit host can discover per-machine
    64-bit registrations. Identical scope/name entries are deduplicated. Values
    (capability registry paths) never leave this module.
    """
    reg = _registry(registry)
    if reg is None:
        return []
    found = {}
    views = list(dict.fromkeys([getattr(reg, 'KEY_WOW64_64KEY', 0),
                                getattr(reg, 'KEY_WOW64_32KEY', 0)]))
    for scope, root in [('user', reg.HKEY_CURRENT_USER), ('machine', reg.HKEY_LOCAL_MACHINE)]:
        for view in views:
            try:
                with reg.OpenKey(root, _REGISTERED, 0, reg.KEY_READ | view) as key:
                    for index in range(_MAX_REGISTRY_VALUES):
                        try:
                            name, value, value_type = reg.EnumValue(key, index)
                        except OSError:
                            break
                        if (not _safe_string(name) or not _safe_string(value, 2048)
                                or value_type not in (reg.REG_SZ, reg.REG_EXPAND_SZ)):
                            continue
                        identity = _app_id(scope, name)
                        found[identity] = {'id': identity, 'name': name, 'scope': scope}
            except OSError:
                continue
    return sorted(found.values(), key=lambda row: (row['name'].casefold(), row['scope']))


def status(*, registry=None):
    """Read configured HKCU HTTP(S) UserChoice evidence, not an effective-default claim.

    Policies, invalid UserChoice hashes, or OS fallback can affect actual routing.
    A missing/inaccessible value is unknown rather than an assumed browser.
    """
    reg = _registry(registry)
    associations = {}
    for scheme in ('http', 'https'):
        prog_id = None
        if reg is not None:
            try:
                with reg.OpenKey(reg.HKEY_CURRENT_USER, _ASSOCIATIONS + '\\' + scheme + r'\UserChoice',
                                 0, reg.KEY_READ) as key:
                    value, value_type = reg.QueryValueEx(key, 'ProgId')
                    if _safe_string(value) and value_type == reg.REG_SZ:
                        prog_id = value
            except OSError:
                pass
        associations[scheme] = {'prog_id': prog_id, 'known': prog_id is not None,
                                'source': 'current_user_userchoice'}
    return {
        'available': reg is not None,
        'sections': list(SECTIONS),
        'registered_apps': registered_apps(registry=reg) if reg is not None else [],
        'web_associations': associations,
        'effective_default_verified': False,
        'note': 'HTTP/HTTPS는 현재 사용자 UserChoice 기록입니다. 실제 적용 여부는 Windows 화면과 함께 확인해야 합니다.',
    }


def open_section(section, app_id=None, *, registry=None, opener=None):
    """Dispatch only a fixed Settings URI, optionally targeting a fresh catalog ID.

    App-specific default pages require a supported Windows 11 release. Windows
    can instead show the general page; the receipt deliberately cannot assert
    which screen appeared or that the requested setting has changed.
    """
    if not isinstance(section, str) or section not in SECTIONS:
        raise ValueError('지원되는 Windows 설정 항목을 선택해주세요.')
    application = None
    uri = SECTIONS[section]
    if app_id is not None:
        if section != 'default_apps' or not isinstance(app_id, str) or not app_id:
            raise ValueError('기본 앱 목록에서 확인한 앱 식별자를 사용해주세요.')
        application = next((row for row in registered_apps(registry=registry) if row['id'] == app_id), None)
        if application is None:
            raise ValueError('현재 Windows 기본 앱 목록에서 해당 앱을 찾을 수 없습니다.')
        parameter = 'registeredAppUser' if application['scope'] == 'user' else 'registeredAppMachine'
        uri += '?' + parameter + '=' + quote(application['name'], safe='')
    if opener is None:
        if os.name != 'nt' or not hasattr(os, 'startfile'):
            raise RuntimeError('Windows 설정 실행 기능을 사용할 수 없습니다.')
        opener = os.startfile
    try:
        dispatched = opener(uri)
        if dispatched is False:
            raise OSError('dispatch failed')
    except OSError:
        raise RuntimeError('Windows 설정 실행 요청에 실패했습니다. 화면 상태를 다시 확인해주세요.') from None
    return {'section': section, 'application': application, 'dispatched': True,
            'setting_changed': False, 'requires_ui_verification': True,
            'message': 'Windows 설정 화면 열기를 요청했습니다. 실제 화면을 읽고 변경과 결과 확인을 이어가세요.'}
