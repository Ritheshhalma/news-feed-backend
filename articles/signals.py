import json

from django.db.models.signals import post_save
from django.dispatch import receiver
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from articles.models import ArticleSource


def register_source_schedule(source: ArticleSource) -> None:
    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=source.scrape_interval_minutes,
        period=IntervalSchedule.MINUTES,
    )
    task_name = (
        "articles.tasks.scrape_playwright_source"
        if source.parser_mode == "js/playwright"
        else "articles.tasks.scrape_source"
    )
    PeriodicTask.objects.update_or_create(
        name=f"scrape_source_{source.id}",
        defaults={
            "task": task_name,
            "interval": schedule,
            "args": json.dumps([str(source.id)]),
            "enabled": source.status == "active",
        },
    )


@receiver(post_save, sender=ArticleSource)
def on_article_source_save(sender, instance, **kwargs):
    register_source_schedule(instance)
