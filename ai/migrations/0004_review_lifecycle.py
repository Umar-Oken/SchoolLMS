import django.db.models.deletion
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('ai', '0003_aijobresultdraft_citation'), ('assessments', '0009_attemptgrade_attemptgradeaudit_and_more')]
    operations = [
        migrations.AddField(model_name='aijob', name='draft_version', field=models.PositiveSmallIntegerField(default=1)),
        migrations.AddField(model_name='aijobresultdraft', name='approved_question', field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='ai_origin_draft', to='assessments.question')),
    ]
