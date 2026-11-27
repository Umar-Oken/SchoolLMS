"""Small, synthetic P084 staging acceptance profile.

It intentionally uses only a bearer token supplied at runtime.  No account,
token, or student content belongs in this file.  The submit request is an
idempotent retry of an already accepted synthetic submission, so concurrent
users exercise the real submit path without creating artificial attempts.
"""

import os

from locust import HttpUser, between, task


TOKEN = os.environ['STAGING_BEARER_TOKEN']
SUBMIT_PATH = os.environ['STAGING_SUBMIT_PATH']
SUBMIT_DRAFT_VERSION = os.environ['STAGING_SUBMIT_DRAFT_VERSION']
SUBMIT_KEY = os.environ['STAGING_SUBMIT_KEY']


class _AuthenticatedUser(HttpUser):
    abstract = True
    wait_time = between(0.05, 0.15)

    def on_start(self):
        self.client.headers.update({'Authorization': f'Bearer {TOKEN}'})


class ReadUser(_AuthenticatedUser):
    weight = 4

    @task
    def courses(self):
        self.client.get('/api/v1/courses/', name='GET /api/v1/courses/')


class IdempotentSubmitUser(_AuthenticatedUser):
    weight = 1

    @task
    def retry_submit(self):
        with self.client.post(
            SUBMIT_PATH,
            json={'draft_version': int(SUBMIT_DRAFT_VERSION)},
            headers={'Idempotency-Key': SUBMIT_KEY},
            name='POST /api/v1/assignments/:id/submit/ (idempotent)',
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure(f'unexpected status {response.status_code}')
