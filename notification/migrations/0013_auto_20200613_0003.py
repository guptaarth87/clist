# Generated by Django 2.2.13 on 2020-06-13 00:03

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notification', '0012_auto_20200612_2354'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='secret',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
