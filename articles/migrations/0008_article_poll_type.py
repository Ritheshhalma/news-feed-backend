from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("articles", "0007_register_live_poll_beat"),
    ]

    operations = [
        migrations.AddField(
            model_name="article",
            name="poll_type",
            field=models.CharField(
                blank=True,
                choices=[("forex", "Forex"), ("stock", "Stock")],
                default="forex",
                max_length=20,
            ),
        ),
    ]
