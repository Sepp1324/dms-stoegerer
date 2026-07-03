from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0008_merge_stufe4_0007'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='document',
            name='email_from',
        ),
        migrations.RemoveField(
            model_name='document',
            name='email_subject',
        ),
    ]
