from django.core.files.uploadhandler import FileUploadHandler
from rest_framework.exceptions import APIException


MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024


class AttachmentTooLarge(APIException):
    status_code = 413
    default_detail = 'Fayl hajmi 10 MB limitdan oshmasligi kerak.'
    default_code = 'file_too_large'


class AttachmentSizeLimitUploadHandler(FileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.total_bytes = 0

    def receive_data_chunk(self, raw_data, start):
        self.total_bytes += len(raw_data)
        if self.total_bytes > MAX_ATTACHMENT_SIZE:
            raise AttachmentTooLarge()
        return raw_data

    def file_complete(self, file_size):
        return None
