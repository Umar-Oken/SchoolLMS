import hashlib

from academics.models import CourseOffering
from assessments.models import Attempt
from assignments.models import Grade, Submission
from files.models import Attachment


def attachment_key_digest():
    digest = hashlib.sha256()
    for key in Attachment.objects.order_by('key').values_list('key', flat=True):
        digest.update(str(key).encode())
        digest.update(b'\0')
    return digest.hexdigest()


def database_inventory():
    return {
        'course_offerings': CourseOffering.objects.count(),
        'submissions': Submission.objects.count(),
        'grades': Grade.objects.count(),
        'attempts': Attempt.objects.count(),
        'attachments': Attachment.objects.count(),
        'attachment_key_digest': attachment_key_digest(),
    }
