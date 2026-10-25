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
