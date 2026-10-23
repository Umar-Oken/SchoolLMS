import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    'path',
    ['/api/schema/', '/api/docs/'],
)
def test_development_endpoints_are_absent_with_production_settings(path):
    production_environment = os.environ.copy()
    production_environment.update(
        {
            'DJANGO_DEBUG': 'false',
            'DJANGO_SECRET_KEY': 'synthetic-test-secret',
            'DJANGO_ALLOWED_HOSTS': 'localhost',
        }
    )
    production_check = '\n'.join(
        [
            'import django',
            'django.setup()',
            'from django.urls import resolve',
            'from django.urls.exceptions import Resolver404',
            f'path = {path!r}',
            'try:',
            '    resolve(path)',
            'except Resolver404:',
            '    pass',
            'else:',
            '    raise SystemExit(1)',
        ]
    )
    result = subprocess.run(
        [sys.executable, '-c', production_check],
        check=False,
        capture_output=True,
        env=production_environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
