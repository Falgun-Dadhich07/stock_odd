from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trading', '0009_remove_orphaned_orders'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketControl',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('paused', models.BooleanField(default=False)),
                ('message', models.CharField(blank=True, max_length=255, null=True)),
            ],
        ),
    ]
