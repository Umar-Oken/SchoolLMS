import hashlib

from django.core.cache import caches

from scheduling.models import TimetableLock


TIMETABLE_CACHE_ALIAS = 'timetable'
CACHE_MISS = object()


def timetable_version():
    return (
        TimetableLock.objects.filter(pk='school')
        .values_list('schedule_version', flat=True)
        .first()
        or 1
    )


def timetable_cache_key(*, user, course_ids, from_date, to_date, version):
    course_scope = ','.join(str(course_id) for course_id in sorted(course_ids))
    scope_digest = hashlib.sha256(course_scope.encode()).hexdigest()[:16]
    return (
        f'timetable:v1:user:{user.pk}:role:{user.role}:courses:{scope_digest}:'
        f'from:{from_date.isoformat()}:to:{to_date.isoformat()}:version:{version}'
    )


def get_timetable_payload(key):
    try:
        return caches[TIMETABLE_CACHE_ALIAS].get(key, CACHE_MISS)
    except Exception:
        return CACHE_MISS


def set_timetable_payload(key, payload, timeout):
    try:
        caches[TIMETABLE_CACHE_ALIAS].set(key, payload, timeout=timeout)
    except Exception:
        pass
