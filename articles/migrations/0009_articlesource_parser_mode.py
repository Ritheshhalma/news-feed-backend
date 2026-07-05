from django.db import migrations, models


def backfill_parser_mode(apps, schema_editor):
    ArticleSource = apps.get_model("articles", "ArticleSource")
    ArticleSource.objects.filter(source_type="rss").update(parser_mode="rss")
    ArticleSource.objects.filter(source_type="html").update(parser_mode="html/multistage")


class Migration(migrations.Migration):

    dependencies = [
        ("articles", "0008_article_poll_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="articlesource",
            name="parser_mode",
            field=models.CharField(
                choices=[
                    ("rss", "RSS Feed"),
                    ("html", "HTML listing only (card data)"),
                    ("html/multistage", "HTML multi-stage (trafilatura)"),
                    ("js/playwright", "JavaScript / Playwright"),
                ],
                default="rss",
                max_length=20,
            ),
        ),
        migrations.RunPython(backfill_parser_mode, migrations.RunPython.noop),
    ]
