#!/usr/bin/env python3
"""Only release the GitLab credential to exact HTTPS GitLab password prompts."""
import os
import re
import sys
from urllib.parse import urlsplit


def answer(prompt):
    match = re.fullmatch(r"(Username|Password) for '([^']+)':\s*", prompt)
    if not match:
        raise ValueError('Unexpected credential prompt')
    url = urlsplit(match[2])
    if url.scheme != 'https' or url.hostname != 'gitlab.com' or url.port not in (None, 443):
        raise ValueError('Unexpected credential host')
    if url.username not in (None, 'oauth2') or url.password is not None:
        raise ValueError('Unexpected credential username')
    if match[1] == 'Username':
        return 'oauth2'
    token = os.environ.get('MIRROR_GITLAB_TOKEN')
    if not token:
        raise ValueError('Missing credential')
    return token


if __name__ == '__main__':
    try:
        print(answer(sys.argv[1]))
    except (ValueError, IndexError):
        sys.exit(1)
