"""Offline validation of public HTTP(S) links, without DNS or session inspection."""

import ipaddress
import re
from urllib.parse import urlsplit


def normalize_public_http_url(value: str | None) -> str | None:
    if value is None or not value.strip(" "):
        return None
    if any(c.isspace() or c == '\\' or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('URL contains whitespace, backslash or control characters')
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as error:
        raise ValueError('malformed HTTP(S) URL') from error
    if parsed.scheme.lower() not in {'http', 'https'} or not hostname:
        raise ValueError('URL must be absolute public HTTP(S) with a hostname')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('URL credentials/userinfo are forbidden')
    if parsed.netloc.endswith(":"):
        raise ValueError("malformed empty URL port")
    # IDNA folds Unicode dots/digits and ignored characters before target checks.
    try:
        host = hostname.encode('idna').decode('ascii').lower().rstrip('.')
    except UnicodeError as error:
        raise ValueError('malformed hostname') from error
    if host == 'localhost' or host.endswith('.localhost'):
        raise ValueError('localhost URL is forbidden')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Reject malformed literal IPs and malformed DNS syntax without resolving DNS.
        if ':' in host or re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)(?:\.(?:[0-9]+|0x[0-9a-f]+))*', host):
            raise ValueError('malformed literal IP hostname')
        if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label)
               or len(label) > 63 for label in host.split('.')) or len(host) > 253:
            raise ValueError('malformed hostname')
    else:
        if not address.is_global:
            raise ValueError('non-global literal IP URL is forbidden')
    return value
