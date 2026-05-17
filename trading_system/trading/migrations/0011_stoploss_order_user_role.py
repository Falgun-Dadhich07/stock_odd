from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trading', '0010_marketcontrol'),
    ]

    operations = [
        migrations.AddField(
            model_name='stoploss_order',
            name='user_role',
            field=models.CharField(
                choices=[
                    ('TRADER', 'Trader'),
                    ('MARKET_MAKER', 'Market Maker'),
                    ('ADMIN', 'Admin'),
                ],
                default='MARKET_MAKER',
                max_length=30,
            ),
        ),
    ]
