from django.utils import timezone


def assignment_time_status(assignment, *, now=None):
    now = timezone.now() if now is None else now
    if now < assignment.available_at:
        return {
            'availability_status': 'upcoming',
            'can_submit': False,
        }
    if now <= assignment.due_at:
        return {
            'availability_status': 'on_time',
            'can_submit': True,
        }
    if now <= assignment.closes_at:
        return {
            'availability_status': 'late',
            'can_submit': True,
        }
    return {
        'availability_status': 'closed',
        'can_submit': False,
    }
