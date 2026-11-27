import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ai', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AIDailyQuota',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.DateField()),
                ('jobs_reserved', models.PositiveSmallIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ai_daily_quotas', to='accounts.user')),
            ],
        ),
        migrations.AddField(
            model_name='aijob',
            name='idempotency_key',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='aijob',
            name='requested_question_count',
            field=models.PositiveSmallIntegerField(default=5),
        ),
        migrations.AddConstraint(
            model_name='aidailyquota',
            constraint=models.UniqueConstraint(fields=('actor', 'day'), name='ai_daily_quota_actor_day_unique'),
        ),
        migrations.AddConstraint(
            model_name='aidailyquota',
            constraint=models.CheckConstraint(condition=models.Q(('jobs_reserved__gte', 0)), name='ai_daily_quota_reserved_nonnegative'),
        ),
        migrations.AddConstraint(
            model_name='aijob',
            constraint=models.CheckConstraint(condition=models.Q(('requested_question_count', 5)), name='ai_job_question_count_is_five'),
        ),
        migrations.AddConstraint(
            model_name='aijob',
            constraint=models.UniqueConstraint(condition=models.Q(('idempotency_key', ''), _negated=True), fields=('actor', 'idempotency_key'), name='ai_job_actor_idempotency_key_unique'),
        ),
    ]
