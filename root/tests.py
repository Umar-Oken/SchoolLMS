import io
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from django.core.management import call_command
from django.test import RequestFactory
from django.urls import clear_url_caches
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from assignments.services import StaleDraftVersion
from root.errors import api_exception_handler, csrf_failure


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
    assert 'secret traceback' not in internal.content.decode()


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
