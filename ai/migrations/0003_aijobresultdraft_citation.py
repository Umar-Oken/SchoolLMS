from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('ai', '0002_aidailyquota_aijob_request_fields')]
    operations = [migrations.AddField(
        model_name='aijobresultdraft', name='citation',
        field=models.JSONField(default=dict),
    )]
