"""
Inspect and replay (or discard) messages from the dead.letter queue.

Usage:
  python manage.py requeue_dead_letters --list
  python manage.py requeue_dead_letters --replay
  python manage.py requeue_dead_letters --replay --limit 20
  python manage.py requeue_dead_letters --purge
"""
import json

from django.core.management.base import BaseCommand
from kombu import Connection


# Maps task name → queue (mirrors celery.py task_routes)
_TASK_QUEUE = {
    "articles.tasks.scrape_source": "scrape.scheduled",
    "articles.tasks.scrape_playwright_source": "scrape.playwright",
    "articles.tasks.refresh_source": "scrape.ondemand",
    "articles.tasks.validate_source": "scrape.ondemand",
    "articles.tasks.process_article_image": "media.process",
    "articles.tasks.poll_live_articles": "live.poll",
}


class Command(BaseCommand):
    help = "Inspect and replay/discard messages from the dead.letter DLQ"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--list",   action="store_true", help="Print all dead messages without consuming them")
        group.add_argument("--replay", action="store_true", help="Re-publish dead messages to their original queue")
        group.add_argument("--purge",  action="store_true", help="Discard all dead messages permanently")
        parser.add_argument("--limit", type=int, default=None, help="Max messages to process (default: all)")
        parser.add_argument("--broker", default="amqp://guest:guest@rabbitmq:5672//",
                            help="RabbitMQ broker URL")

    def handle(self, *args, **options):
        from django.conf import settings
        broker_url = options["broker"] or getattr(settings, "CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//")

        with Connection(broker_url) as conn:
            if options["list"]:
                self._list(conn, options["limit"])
            elif options["replay"]:
                self._replay(conn, options["limit"])
            elif options["purge"]:
                self._purge(conn, options["limit"])

    # ── list ──────────────────────────────────────────────────────────────

    def _list(self, conn, limit):
        channel = conn.channel()
        msgs = []
        max_read = limit if limit is not None else 1000
        while len(msgs) < max_read:
            msg = channel.basic_get("dead.letter", no_ack=False)
            if msg is None:
                break
            msgs.append(msg)

        for i, msg in enumerate(msgs, 1):
            self._print_message(i, msg)
            channel.basic_reject(msg.delivery_tag, requeue=True)

        if not msgs:
            self.stdout.write(self.style.SUCCESS("dead.letter queue is empty."))
        else:
            note = " (capped — use --limit to see more)" if limit is None and len(msgs) == 1000 else ""
            self.stdout.write(f"\n{len(msgs)} message(s) in dead.letter queue.{note}")

    # ── replay ────────────────────────────────────────────────────────────

    def _replay(self, conn, limit):
        channel = conn.channel()
        replayed = 0
        skipped = 0

        while True:
            if limit is not None and replayed >= limit:
                break
            msg = channel.basic_get("dead.letter", no_ack=False)
            if msg is None:
                break

            task_name = self._task_name(msg)

            # Try x-death header first (set by RabbitMQ native DLQ routing)
            x_death = (msg.properties.get("application_headers") or {}).get("x-death", [])
            target_queue = x_death[0].get("queue") if x_death else None

            # Fall back to task_routes lookup (for manually published DLQ messages)
            if not target_queue:
                target_queue = _TASK_QUEUE.get(task_name)

            if not target_queue:
                self.stdout.write(self.style.WARNING(
                    f"  [skip] Unknown queue for task {task_name!r} — discarding"
                ))
                channel.basic_ack(msg.delivery_tag)
                skipped += 1
                continue

            channel.basic_publish(msg, exchange="", routing_key=target_queue)
            channel.basic_ack(msg.delivery_tag)
            replayed += 1
            self.stdout.write(self.style.SUCCESS(
                f"  [replayed → {target_queue}] {task_name}"
            ))

        self.stdout.write(f"\n{replayed} replayed, {skipped} skipped.")

    # ── purge ─────────────────────────────────────────────────────────────

    def _purge(self, conn, limit):
        channel = conn.channel()
        if limit is None:
            count = channel.queue_purge("dead.letter")
            self.stdout.write(self.style.WARNING(f"Purged {count} message(s) from dead.letter."))
            return

        purged = 0
        while purged < limit:
            msg = channel.basic_get("dead.letter", no_ack=True)
            if msg is None:
                break
            purged += 1
        self.stdout.write(self.style.WARNING(f"Purged {purged} message(s) from dead.letter."))

    # ── helpers ───────────────────────────────────────────────────────────

    def _task_name(self, msg):
        try:
            return json.loads(msg.body).get("task", "unknown")
        except Exception:
            return "unknown"

    def _print_message(self, idx, msg):
        try:
            body = json.loads(msg.body)
            task = body.get("task", "?")
            args = body.get("args", [])
            x_death = (msg.properties.get("application_headers") or {}).get("x-death", [])
            origin = x_death[0].get("queue", "?") if x_death else _TASK_QUEUE.get(task, "?")
            reason = x_death[0].get("reason", "?") if x_death else body.get("error", "?")
            self.stdout.write(
                f"  [{idx}] task={task}  queue={origin}  reason={reason}\n"
                f"       args={args}"
            )
        except Exception as e:
            self.stdout.write(f"  [{idx}] <unparseable: {e}>")
