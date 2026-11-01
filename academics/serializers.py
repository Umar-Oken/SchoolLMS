from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from academics.models import (
    AcademicStatus,
    AcademicYear,
    CourseOffering,
    SchoolClass,
    Subject,
    Term,
)


class SubjectReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ("code", "title")


class SchoolClassReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolClass
        fields = ("grade_level", "section")


class SchoolClassOptionSerializer(serializers.ModelSerializer):
    year_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = SchoolClass
        fields = ("id", "year_id", "grade_level", "section")


class SubjectOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ("id", "code", "title")


class CourseOfferingWriteSerializer(serializers.ModelSerializer):
    school_class = serializers.PrimaryKeyRelatedField(
        queryset=SchoolClass.objects.all(),
    )
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.all(),
    )

    class Meta:
        model = CourseOffering
        fields = ("id", "school_class", "subject")
        read_only_fields = ("id",)

    def validate(self, attrs):
        instance = (
            self.instance
            if isinstance(self.instance, CourseOffering)
            else None
        )
        school_class = attrs.get("school_class")
        subject = attrs.get("subject")

        if instance:
            school_class = school_class or instance.school_class
            subject = subject or instance.subject
            changed_fields = []
            if school_class.pk != instance.school_class_id:
                changed_fields.append("school_class")
            if subject.pk != instance.subject_id:
                changed_fields.append("subject")

            if changed_fields and (
                instance.teaching_assignments.exists()
                or instance.pupil_enrollments.exists()
                or instance.topics.exists()
            ):
                raise serializers.ValidationError({
                    field: (
                        "Tarixiy biriktirish, yozilish yoki mavzu mavjud "
                        "kursning ota ma’lumotini o‘zgartirib bo‘lmaydi."
                    )
                    for field in changed_fields
                })

        if school_class is None:
            raise serializers.ValidationError({
                "school_class": "Sinf tanlanishi kerak."
            })
        if subject is None:
            raise serializers.ValidationError({
                "subject": "Fan tanlanishi kerak."
            })

        duplicate = CourseOffering.objects.filter(
            school_class=school_class,
            subject=subject,
        )
        if instance:
            duplicate = duplicate.exclude(pk=instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError({
                "subject": "Bu sinfda fan kursi allaqachon mavjud."
            })

        return attrs


class AcademicYearReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = ("label",)


class TermCatalogReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Term
        fields = ("id", "order", "start_date", "end_date", "status")
        read_only_fields = fields


class AcademicYearCatalogReadSerializer(serializers.ModelSerializer):
    terms = TermCatalogReadSerializer(many=True, read_only=True)

    class Meta:
        model = AcademicYear
        fields = ("id", "label", "start_date", "end_date", "status", "terms")
        read_only_fields = fields


class TermAggregateSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False)
    order = serializers.IntegerField(min_value=1)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    status = serializers.ChoiceField(
        choices=AcademicStatus.choices,
        required=False,
    )

    def to_internal_value(self, data):
        allowed_fields = {"id", "order", "start_date", "end_date", "status"}
        if hasattr(data, "keys"):
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: "Bu maydon qabul qilinmaydi."
                    for name in extra_fields
                })
        return super().to_internal_value(data)


class AcademicYearAggregateSerializer(serializers.ModelSerializer):
    terms = TermAggregateSerializer(many=True, required=False, allow_empty=False)

    class Meta:
        model = AcademicYear
        fields = ("id", "label", "start_date", "end_date", "status", "terms")
        read_only_fields = ("id",)

    def to_internal_value(self, data):
        allowed_fields = {"label", "start_date", "end_date", "status", "terms"}
        if hasattr(data, "keys"):
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: "Bu maydon qabul qilinmaydi."
                    for name in extra_fields
                })
        return super().to_internal_value(data)

    def validate(self, attrs):
        instance = self.instance
        start_date = attrs.get(
            "start_date",
            instance.start_date if instance else None,
        )
        end_date = attrs.get(
            "end_date",
            instance.end_date if instance else None,
        )
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError({
                "start_date": "O‘quv yili boshlanishi tugash sanasidan keyin bo‘lishi mumkin emas."
            })

        submitted_terms = attrs.get("terms")
        if instance is None and submitted_terms is None:
            raise serializers.ValidationError({
                "terms": "Yangi o‘quv yili uchun choraklar ro‘yxati kerak."
            })

        terms_by_id = {}
        if instance:
            terms_by_id = {
                term.pk: term
                for term in instance.terms.all()
            }

        resulting_terms = []
        seen_ids = set()
        if submitted_terms is not None:
            for term_data in submitted_terms:
                term_id = term_data.get("id")
                if instance is None and term_id is not None:
                    raise serializers.ValidationError({
                        "terms": "Yangi o‘quv yiliga mavjud chorak IDsi berilmaydi."
                    })
                if term_id is not None:
                    term = terms_by_id.get(term_id)
                    if term is None:
                        raise serializers.ValidationError({
                            "terms": "Chorak shu o‘quv yiliga tegishli bo‘lishi kerak."
                        })
                    if term_id in seen_ids:
                        raise serializers.ValidationError({
                            "terms": "Bir chorak IDsi takroran berilgan."
                        })
                    seen_ids.add(term_id)
                    values = {
                        "order": term_data["order"],
                        "start_date": term_data["start_date"],
                        "end_date": term_data["end_date"],
                        "status": term_data.get(
                            "status",
                            terms_by_id[term_id].status,
                        ),
                    }
                else:
                    values = {
                        "order": term_data["order"],
                        "start_date": term_data["start_date"],
                        "end_date": term_data["end_date"],
                        "status": term_data.get(
                            "status",
                            AcademicStatus.UPCOMING,
                        ),
                    }
                resulting_terms.append(values)

        if instance:
            for term in terms_by_id.values():
                if term.pk not in seen_ids:
                    resulting_terms.append({
                        "order": term.order,
                        "start_date": term.start_date,
                        "end_date": term.end_date,
                        "status": term.status,
                    })

        orders = [term["order"] for term in resulting_terms]
        if len(orders) != len(set(orders)):
            raise serializers.ValidationError({
                "terms": "Bir o‘quv yilidagi chorak tartibi takrorlanmasligi kerak."
            })

        for term in resulting_terms:
            if term["start_date"] > term["end_date"]:
                raise serializers.ValidationError({
                    "terms": "Chorak boshlanish sanasi tugash sanasidan keyin bo‘lishi mumkin emas."
                })
            if term["start_date"] < start_date or term["end_date"] > end_date:
                raise serializers.ValidationError({
                    "terms": "Chorak sanalari o‘quv yili chegarasidan tashqarida."
                })

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        terms_data = validated_data.pop("terms")
        year = AcademicYear.objects.create(**validated_data)
        for term_data in terms_data:
            term_data.pop("id", None)
            Term.objects.create(year=year, **term_data)
        return year

    @transaction.atomic
    def update(self, instance, validated_data):
        terms_data = validated_data.pop("terms", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        try:
            instance.full_clean()
            instance.save()

            if terms_data is not None:
                existing_terms = {
                    term.pk: term
                    for term in Term.objects.select_for_update().filter(
                        year=instance,
                    )
                }
                requested_orders = {
                    term_data["order"]
                    for term_data in terms_data
                }
                changed_orders = {
                    term_data["id"]: term_data["order"]
                    for term_data in terms_data
                    if term_data.get("id") is not None
                    and existing_terms[term_data["id"]].order
                    != term_data["order"]
                }
                unavailable_orders = {
                    term.order for term in existing_terms.values()
                } | requested_orders
                temporary_orders = [
                    order
                    for order in range(32767, 0, -1)
                    if order not in unavailable_orders
                ]
                if len(temporary_orders) < len(changed_orders):
                    raise serializers.ValidationError({
                        "terms": "Chorak tartibini xavfsiz almashtirib bo‘lmadi."
                    })
                for (term_id, _), temporary_order in zip(
                    changed_orders.items(),
                    temporary_orders[:len(changed_orders)],
                    strict=True,
                ):
                    term = existing_terms[term_id]
                    term.order = temporary_order
                    term.save(update_fields=["order"])

                for term_data in terms_data:
                    term_id = term_data.pop("id", None)
                    if term_id is None:
                        term = Term(year=instance, **term_data)
                        term.full_clean()
                        term.save()
                        continue

                    term = existing_terms[term_id]
                    for field, value in term_data.items():
                        setattr(term, field, value)
                    term.full_clean()
                    term.save()

            return instance
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc


class CourseReadSerializer(serializers.ModelSerializer):
    subject = SubjectReadSerializer(read_only=True)
    school_class = SchoolClassReadSerializer(read_only=True)
    teacher_display = serializers.SerializerMethodField()
    year = AcademicYearReadSerializer(source="school_class.year", read_only=True)

    class Meta:
        model = CourseOffering
        fields = ("id", "subject", "school_class", "teacher_display", "year")

    @staticmethod
    def get_teacher_display(course: CourseOffering) -> str:
        assignments = getattr(course, "active_assignments", ())
        names = [
            assignment.teacher.get_full_name() or assignment.teacher.username
            for assignment in assignments
        ]
        return ", ".join(names)
