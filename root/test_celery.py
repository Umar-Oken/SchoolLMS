from root.celery import app
from root.tasks import diagnostic_ping
from outbox.tasks import consume_outbox_event


def test_celery_declares_default_scans_and_ai_queues():
    assert {
        queue.name for queue in app.conf.task_queues
    } == {'default', 'scans', 'ai'}
    assert app.conf.task_default_queue == 'default'


def test_celery_discovers_safe_diagnostic_task_on_default_queue():
    assert app.tasks['root.tasks.diagnostic_ping'].name == diagnostic_ping.name
    route = app.amqp.router.route(
        {},
        'root.tasks.diagnostic_ping',
        args=(),
        kwargs={},
    )
    assert route['queue'].name == 'default'


def test_celery_routes_future_scanning_and_ai_tasks_to_separate_queues():
    scans_route = app.amqp.router.route(
        {},
        'scans.inspect_upload',
        args=(),
        kwargs={},
    )
    ai_route = app.amqp.router.route(
        {},
        'ai.prepare_draft',
        args=(),
        kwargs={},
    )

    assert scans_route['queue'].name == 'scans'
    assert ai_route['queue'].name == 'ai'


def test_celery_uses_distinct_redis_databases_for_broker_and_results():
    assert app.conf.broker_url.endswith('/0')
    assert app.conf.result_backend.endswith('/1')


def test_beat_periodically_dispatches_outbox_events_to_default_queue():
    schedule = app.conf.beat_schedule['dispatch-pending-outbox-events']

    assert schedule['task'] == 'outbox.tasks.dispatch_pending_outbox_events'
    assert schedule['schedule'] == 30.0
    assert schedule['options']['queue'] == 'default'
    assert app.tasks[schedule['task']].name == schedule['task']
    assert consume_outbox_event.acks_late is True
    assert consume_outbox_event.reject_on_worker_lost is True


def test_beat_checks_assignment_deadlines_every_five_minutes():
    schedule = app.conf.beat_schedule[
        'create-assignment-deadline-reminders'
    ]

    assert schedule['task'] == 'notifications.tasks.create_deadline_reminders'
    assert schedule['schedule'] == 300.0
    assert schedule['options']['queue'] == 'default'
    assert app.tasks[schedule['task']].name == schedule['task']
