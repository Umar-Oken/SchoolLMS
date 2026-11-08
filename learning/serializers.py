from urllib.parse import urlsplit

from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from accounts.models import User
from academics.models import CourseOffering, Term
from academics.services import ensure_term_open
from academics.views import teacher_course_scope
from files.models import Attachment
from learning.models import Completion, Resource, Topic


class TopicCreateSerializer(serializers.ModelSerializer):
    course = serializers.PrimaryKeyRelatedField(
        queryset=CourseOffering.objects.all(),
    )
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())

    class Meta:
        model = Topic
        fields = (
            'id',
            'course',
            'term',
            'title',
            'objective',
            'order',
            'state',
            'published_at',
        )
        read_only_fields = ('id', 'state', 'published_at')

    def to_internal_value(self, data):
        allowed_fields = {'course', 'term', 'title', 'objective', 'order'}
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydon qabul qilinmaydi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)

    def validate_course(self, course):
        user = self.context['request'].user
        if not teacher_course_scope(user, active_only=True).filter(
            pk=course.pk,
        ).exists():
            raise NotFound()
        return course

    def validate(self, attrs):
        course = attrs['course']
        term = attrs['term']
        if term.year_id != course.school_class.year_id:
            raise serializers.ValidationError({
                'term': 'Term and course must belong to the same academic year.'
            })
        ensure_term_open(term)
        return attrs

    def to_representation(self, instance):
        return TopicReadSerializer(instance, context=self.context).data


class TopicUpdateSerializer(serializers.ModelSerializer):
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())

    class Meta:
        model = Topic
        fields = ('id', 'term', 'title', 'objective', 'order')
        read_only_fields = ('id',)

    def to_internal_value(self, data):
        allowed_fields = {'term', 'title', 'objective', 'order'}
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydonni o‘zgartirib bo‘lmaydi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)

    def validate(self, attrs):
        term = attrs.get('term', self.instance.term)
        if term.year_id != self.instance.course.school_class.year_id:
            raise serializers.ValidationError({
                'term': 'Term and course must belong to the same academic year.'
            })
        ensure_term_open(term)
        return attrs

    def to_representation(self, instance):
        return TopicReadSerializer(instance, context=self.context).data


class TopicReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = (
            'id',
            'course',
            'term',
            'title',
            'objective',
            'order',
            'state',
            'published_at',
            'archived_at',
        )
        read_only_fields = fields


class CompletionReadSerializer(serializers.ModelSerializer):
    kind = serializers.CharField(read_only=True)

    class Meta:
        model = Completion
        fields = ('topic', 'kind', 'completed_at')
        read_only_fields = fields


class TopicDetailReadSerializer(TopicReadSerializer):
    completion = serializers.SerializerMethodField()

    class Meta(TopicReadSerializer.Meta):
        fields = (*TopicReadSerializer.Meta.fields, 'completion')

    @extend_schema_field(CompletionReadSerializer(allow_null=True))
    def get_completion(self, topic) -> dict | None:
        request = self.context.get('request')
        if (
            request is None
            or getattr(request.user, 'role', None) != User.Role.PUPIL
        ):
            return None
        completion = topic.completions.filter(
            pupil=request.user,
            kind=Completion.Kind.READ,
        ).first()
        if completion is None:
            return None
        return CompletionReadSerializer(completion).data


class CompletionMarkSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not hasattr(data, 'keys'):
            raise serializers.ValidationError(
                'So‘rov tanasi bo‘sh JSON obyekt bo‘lishi kerak.'
            )
        if data:
            raise serializers.ValidationError({
                name: 'Bu maydon qabul qilinmaydi.'
                for name in data.keys()
            })
        return {}


def validate_clean_pdf_attachment(attachment, user):
    if attachment.owner_id != user.pk:
        raise serializers.ValidationError(
            'Fayl topilmadi yoki uni ishlatishga ruxsat yo‘q.'
        )
    if attachment.scan_status != Attachment.ScanStatus.CLEAN:
        raise serializers.ValidationError(
            'Faqat tekshiruvdan o‘tgan (clean) fayl biriktiriladi.'
        )
    if attachment.detected_mime != 'application/pdf':
        raise serializers.ValidationError(
            'PDF resurs uchun faqat PDF fayli biriktiriladi.'
        )
    return attachment


class ResourceCreateSerializer(serializers.ModelSerializer):
    attachment = serializers.PrimaryKeyRelatedField(
        queryset=Attachment.objects.all(),
        required=False,
        allow_null=True,
    )
    url = serializers.URLField(required=False, allow_null=True)

    class Meta:
        model = Resource
        fields = (
            'kind',
            'title',
            'description',
            'transcript_text',
            'attachment',
            'url',
        )

    def to_internal_value(self, data):
        allowed_fields = {
            'kind',
            'title',
            'description',
            'transcript_text',
            'attachment',
            'url',
        }
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydon qabul qilinmaydi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)

    def validate_attachment(self, attachment):
        if attachment is None:
            return None
        return validate_clean_pdf_attachment(
            attachment,
            self.context['request'].user,
        )

    def validate_url(self, url):
        if url is None:
            return None

        parsed_url = urlsplit(url)
        hostname = (parsed_url.hostname or '').lower().rstrip('.')
        try:
            port = parsed_url.port
        except ValueError as exc:
            raise serializers.ValidationError(
                'URL porti noto‘g‘ri.'
            ) from exc
        if (
            parsed_url.scheme.lower() != 'https'
            or parsed_url.username is not None
            or parsed_url.password is not None
            or port not in (None, 443)
        ):
            raise serializers.ValidationError(
                'Faqat ruxsat etilgan hostdagi HTTPS URL qabul qilinadi.'
            )

        allowed_hosts = getattr(settings, 'RESOURCE_URL_ALLOWED_HOSTS', ())
        if not any(
            hostname == allowed_host
            or hostname.endswith(f'.{allowed_host}')
            for allowed_host in allowed_hosts
        ):
            raise serializers.ValidationError(
                'URL hosti ruxsat etilgan providerlar ro‘yxatida yo‘q.'
            )
        return url

    def validate(self, attrs):
        kind = attrs['kind']
        attachment = attrs.get('attachment')
        url = attrs.get('url')

        if kind == Resource.Kind.PDF:
            if attachment is None or url is not None:
                raise serializers.ValidationError(
                    'PDF resursda clean attachment bo‘lishi va URL bo‘lmasligi kerak.'
                )
        elif kind in (Resource.Kind.VIDEO, Resource.Kind.LINK):
            if url is None or attachment is not None:
                raise serializers.ValidationError(
                    'Video/link resursda URL bo‘lishi, attachment bo‘lmasligi kerak.'
                )
        return attrs

    def to_representation(self, instance):
        return ResourceReadSerializer(instance, context=self.context).data


class ResourceReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resource
        fields = (
            'id',
            'topic',
            'kind',
            'title',
            'description',
            'attachment',
            'url',
            'transcript_text',
            'state',
            'version',
        )
        read_only_fields = fields
