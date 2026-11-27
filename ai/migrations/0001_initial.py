# Generated manually for the P085 AI job boundary.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('accounts', '0004_alter_accountlifecycleaudit_actor'),
        ('academics', '0005_subject_archived_at'),
        ('learning', '0006_announcement'),
    ]

    operations = [
        migrations.CreateModel(
            name='AIJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('resource_version', models.PositiveSmallIntegerField()),
                ('state', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('review', 'Review'), ('failed', 'Failed'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='queued', max_length=10)),
                ('prompt_version', models.PositiveSmallIntegerField(default=1)),
                ('provider_model', models.CharField(max_length=200)),
                ('tokens', models.PositiveIntegerField(default=0)),
                ('error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ai_jobs', to='accounts.user')),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ai_jobs', to='academics.courseoffering')),
                ('resource', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ai_jobs', to='learning.resource')),
            ],
            options={'ordering': ('-created_at', '-pk')},
        ),
        migrations.CreateModel(
            name='AIJobResultDraft',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('position', models.PositiveSmallIntegerField()),
                ('question_text', models.TextField()),
                ('choices', models.JSONField(default=list)),
                ('correct_choice_index', models.PositiveSmallIntegerField()),
                ('explanation', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='result_drafts', to='ai.aijob')),
            ],
            options={'ordering': ('position', 'pk')},
        ),
        migrations.AddConstraint(
            model_name='aijob',
            constraint=models.CheckConstraint(condition=models.Q(('state__in', ('queued', 'running', 'review', 'failed', 'approved', 'rejected'))), name='ai_job_state_valid'),
        ),
        migrations.AddConstraint(
            model_name='aijob',
            constraint=models.CheckConstraint(condition=models.Q(('resource_version__gte', 1)), name='ai_job_resource_version_positive'),
        ),
        migrations.AddConstraint(
            model_name='aijob',
            constraint=models.CheckConstraint(condition=models.Q(('prompt_version__gte', 1)), name='ai_job_prompt_version_positive'),
        ),
        migrations.AddConstraint(
            model_name='aijob',
            constraint=models.CheckConstraint(condition=models.Q(('tokens__gte', 0)), name='ai_job_tokens_nonnegative'),
        ),
        migrations.AddConstraint(
            model_name='aijobresultdraft',
            constraint=models.UniqueConstraint(fields=('job', 'position'), name='ai_result_draft_job_position_unique'),
        ),
    ]
