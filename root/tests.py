import io
import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.utils import timezone
from django.urls import clear_url_caches
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from assignments.services import StaleDraftVersion
from files.models import Attachment
from root.errors import api_exception_handler, csrf_failure
from root.logging import RequestJsonFormatter
from root.middleware import RequestIdMiddleware
from outbox.models import OutboxEvent


def assert_error_envelope(response, expected_status, expected_code):
    assert response.status_code == expected_status
    assert response.data['code'] == expected_code
    assert isinstance(response.data['message'], str)
    assert isinstance(response.data['fields'], dict)
    assert response.data['request_id']
    assert response['X-Request-ID'] == response.data['request_id']


@pytest.mark.django_db
def test_drf_error_contract_is_stable_for_400_403_404_and_415():
    user = User.objects.create_user(
        username='error-contract-teacher',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    client = APIClient()
    client.force_authenticate(user=user)

    invalid_window = client.get('/api/v1/schedule/?from=invalid&to=2026-11-16')
    forbidden = client.post('/api/v1/admin/rooms/', {'name': '101'}, format='json')
    not_found = client.get('/api/v1/courses/999999/')
    unsupported_media = client.post(
        '/api/v1/attachments/',
        b'<attachment />',
        content_type='application/xml',
    )

    assert_error_envelope(
        invalid_window,
        status.HTTP_400_BAD_REQUEST,
        'validation_error',
    )
    assert 'from_date' in invalid_window.data['fields']
    assert_error_envelope(
        forbidden,
        status.HTTP_403_FORBIDDEN,
        'permission_denied',
    )
    assert_error_envelope(not_found, status.HTTP_404_NOT_FOUND, 'not_found')
    assert_error_envelope(
        unsupported_media,
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        'unsupported_media_type',
    )


def test_custom_conflict_csrf_and_internal_errors_use_safe_envelope():
    request = RequestFactory().post('/api/v1/example/')
    request.request_id = 'test-request-id'

    conflict = api_exception_handler(StaleDraftVersion(), {'request': request})
    csrf = csrf_failure(request)
    internal = api_exception_handler(RuntimeError('secret traceback'), {'request': request})

    assert conflict.status_code == status.HTTP_409_CONFLICT
    assert conflict.data == {
        'code': 'stale_draft_version',
        'message': 'Draft boshqa versiyada yangilangan; uni qayta yuklang.',
        'fields': {},
        'request_id': 'test-request-id',
    }
    assert csrf.status_code == status.HTTP_403_FORBIDDEN
    assert json.loads(csrf.content)['code'] == 'csrf_failed'
    assert internal.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert internal.data['code'] == 'internal_error'
    assert 'secret traceback' not in internal.data['message']


@override_settings(
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    CSRF_COOKIE_SECURE=True,
)
def test_forwarded_https_keeps_csrf_cookie_secure():
    client = APIClient(enforce_csrf_checks=True)

    response = client.get(
        '/api/v1/auth/csrf/',
        HTTP_X_FORWARDED_PROTO='https',
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.wsgi_request.is_secure()
    assert response.data['csrfToken']
    assert response.cookies['csrftoken']['secure'] is True


def test_openapi_schema_uses_error_envelope_without_generation_errors():
    clear_url_caches()
    with NamedTemporaryFile(suffix='.yaml') as schema_file:
        call_command(
            'spectacular',
            '--validate',
            '--file',
            schema_file.name,
            stdout=io.StringIO(),
        )
        schema = Path(schema_file.name).read_text()

    assert 'ErrorEnvelope' in schema
    assert 'request_id' in schema


def test_health_liveness_is_independent_from_readiness_db_and_schema():
    client = APIClient()

    assert client.get('/health/live/').json() == {'status': 'ok'}

    with patch('root.health.connection.ensure_connection'):
        with patch('root.health.MigrationExecutor') as executor:
            executor.return_value.migration_plan.return_value = []
            ready = client.get('/health/ready/')

    with patch(
        'root.health.connection.ensure_connection',
        side_effect=RuntimeError('database password must not be logged'),
    ):
        database_down = client.get('/health/ready/')

    with patch('root.health.connection.ensure_connection'):
        with patch('root.health.MigrationExecutor') as executor:
            executor.return_value.migration_plan.return_value = [object()]
            schema_behind = client.get('/health/ready/')

    assert ready.status_code == status.HTTP_200_OK
    assert ready.json() == {'status': 'ok'}
    assert database_down.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert database_down.json() == {'status': 'not_ready'}
    assert schema_behind.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert schema_behind.json() == {'status': 'not_ready'}


def test_request_log_redacts_query_body_and_exception_text(caplog):
    factory = RequestFactory()
    middleware = RequestIdMiddleware(lambda request: HttpResponse(status=204))
    caplog.set_level(logging.INFO, logger='root.request')

    response = middleware(
        factory.post(
            '/api/v1/auth/login/?token=query-secret',
            data='password=body-secret',
            content_type='application/x-www-form-urlencoded',
            HTTP_COOKIE='sessionid=cookie-secret',
        )
    )

    record = caplog.records[-1]
    rendered = RequestJsonFormatter().format(record)
    assert response.status_code == 204
    assert record.request_id == response['X-Request-ID']
    assert record.path == '/api/v1/auth/login/'
    assert record.latency_ms >= 0
    assert 'query-secret' not in rendered
    assert 'body-secret' not in rendered
    assert 'cookie-secret' not in rendered


def test_request_error_log_uses_only_error_class(caplog):
    def explode(request):
        raise ValueError('password=body-secret')

    caplog.set_level(logging.ERROR, logger='root.request')
    with pytest.raises(ValueError):
        RequestIdMiddleware(explode)(RequestFactory().get('/health/live/'))

    record = caplog.records[-1]
    rendered = RequestJsonFormatter().format(record)
    assert record.error_class == 'ValueError'
    assert record.status_code == 500
    assert 'body-secret' not in rendered


@pytest.mark.django_db
def test_background_monitor_reports_broker_failure_without_outbox_payload(
    monkeypatch,
):
    OutboxEvent.objects.create(
        kind='test.effect',
        payload={'pupil_text': 'sensitive student content'},
        dedup_key='monitor-failed-event',
        status=OutboxEvent.Status.FAILED,
        next_run_at=timezone.now(),
    )
    monkeypatch.setattr(
        'root.monitoring._broker_status',
        lambda: 'unavailable',
    )
    monkeypatch.setattr(
        'root.monitoring._worker_status',
        lambda: {'status': 'unavailable', 'count': 0},
    )
    output = io.StringIO()

    call_command('monitor_background', stdout=output)
    payload = json.loads(output.getvalue())

    assert payload['broker'] == {'status': 'unavailable'}
    assert payload['workers'] == {'status': 'unavailable', 'count': 0}
    assert payload['outbox']['failed'] == 1
    assert payload['ai'] == {'status': 'not_configured'}
    assert 'sensitive student content' not in output.getvalue()


@pytest.mark.django_db
def test_restore_inventory_verifies_attachment_bytes_without_payload(tmp_path):
    attachment = Attachment.objects.create(
        owner=User.objects.create_user(
            username='restore-inventory-owner',
            password='safe-test-password',
            role=User.Role.PUPIL,
        ),
        original_name='restore.pdf',
        size=7,
        detected_mime='application/pdf',
        scan_status='clean',
    )
    storage = storages['private']
    storage.save(str(attachment.key), ContentFile(b'%PDF-1'))
    inventory = tmp_path / 'inventory.json'
    try:
        call_command('backup_inventory', output=str(inventory))
        output = io.StringIO()
        call_command('verify_restore', inventory=str(inventory), stdout=output)
        assert output.getvalue().strip() == 'restore verification passed'

        storage.delete(str(attachment.key))
        with pytest.raises(CommandError, match='private object reference'):
            call_command('verify_restore', inventory=str(inventory))
    finally:
        storage.delete(str(attachment.key))
