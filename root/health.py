from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse


def live(request):
    """Liveness faqat process so'rovni qabul qilayotganini bildiradi."""
    return JsonResponse({'status': 'ok'})


def ready(request):
    """Readiness DB ulanishi va barcha migrationlar qo'llanganini tekshiradi."""
    try:
        connection.ensure_connection()
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception:
        return JsonResponse({'status': 'not_ready'}, status=503)

    if plan:
        return JsonResponse({'status': 'not_ready'}, status=503)
    return JsonResponse({'status': 'ok'})
