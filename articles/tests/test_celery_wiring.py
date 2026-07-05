from config.celery import app as celery_app


def test_celery_app_uses_rabbitmq_broker_from_settings():
    assert celery_app.conf.broker_url.startswith("amqp://")


def test_celery_task_routes_cover_all_five_queues():
    routes = celery_app.conf.task_routes
    queues = {v["queue"] for v in routes.values()}
    assert queues == {
        "scrape.scheduled", "scrape.ondemand", "scrape.playwright",
        "media.process", "live.poll",
    }


def test_playwright_source_task_routes_to_playwright_queue():
    routes = celery_app.conf.task_routes
    assert routes["articles.tasks.scrape_playwright_source"]["queue"] == "scrape.playwright"


def test_celery_acks_late_and_prefetch_one_are_set():
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
