from rest_framework import serializers

from academics.models import AcademicYear, CourseOffering, SchoolClass, Subject


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
