import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    'path',
    ['/api/schema/', '/api/docs/', '/api/redoc/'],
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


def test_redoc_uses_local_assets_and_schema():
    redoc_check = '\n'.join(
        [
            'import django',
            'django.setup()',
            'from django.contrib.staticfiles.finders import find',
            'from django.test import Client',
            'from django.urls import resolve',
            'assert resolve("/api/redoc/").url_name == "redoc"',
            'client = Client(HTTP_HOST="127.0.0.1")',
            'redoc = client.get("/api/redoc/")',
            'assert redoc.status_code == 200',
            'assert "/static/drf_spectacular_sidecar/redoc/bundles/redoc.standalone.js" in redoc.content.decode()',
            'assert find("drf_spectacular_sidecar/redoc/bundles/redoc.standalone.js")',
            'schema = client.get("/api/schema/")',
            'assert schema.status_code == 200',
            'assert b"openapi:" in schema.content',
        ]
    )
    redoc_environment = os.environ.copy()
    redoc_environment['DJANGO_DEBUG'] = 'true'
    result = subprocess.run(
        [sys.executable, '-c', redoc_check],
        check=False,
        capture_output=True,
        env=redoc_environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
