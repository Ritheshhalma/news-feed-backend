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
from kombu.simple import SimpleQueue


class Command(BaseCommand):
    help = "Inspect and replay/discard messages from the dead.letter DLQ"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--list",   action="store_true", help="Print all dead messages without consuming them")
        group.add_argument("--replay", action="store_true", help="Re-publish dead messages to their original queue")
        group.add_argument("--purge",  action="store_true", help="Discard all dead messages permanently")
        parser.add_argument("--limit", type=int, default=None, help="Max messages to process (default: all)")
        parser.add_argument("--broker", default="amqp://guest:guest@localhost:5672//",
                            help="RabbitMQ broker URL (default: amqp://guest:guest@localhost:5672//)")

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
        # Collect all messages first so we can requeue after printing (avoids infinite loop)
        msgs = []
        max_read = limit or 1000
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
            self.stdout.write(f"\n{len(msgs)} message(s) in dead.letter queue.")

    # ── replay ────────────────────────────────────────────────────────────

    def _replay(self, conn, limit):
        channel = conn.channel()
        replayed = 0
        while True:
            if limit and replayed >= limit:
                break
            msg = channel.basic_get("dead.letter", no_ack=False)
            if msg is None:
                break

            # RabbitMQ sets x-death header with original queue/exchange info
            x_death = msg.properties.get("application_headers", {}).get("x-death", [])
            original_queue = x_death[0].get("queue") if x_death else None

            if not original_queue:
                self.stdout.write(self.style.WARNING(
                    f"  [skip] Cannot determine original queue for message — discarding"
                ))
                msg.ack()
                continue

            # Re-publish to the original queue
            channel.basic_publish(
                msg.body,
                exchange="",
                routing_key=original_queue,
                properties=msg.properties,
            )
            msg.ack()
            replayed += 1
            self.stdout.write(self.style.SUCCESS(f"  [replayed → {original_queue}] {self._task_name(msg)}"))

        self.stdout.write(f"\n{replayed} message(s) replayed.")

    # ── purge ─────────────────────────────────────────────────────────────

    def _purge(self, conn, limit):
        channel = conn.channel()
        if limit is None:
            count = channel.queue_purge("dead.letter")
            self.stdout.write(self.style.WARNING(f"Purged {count} message(s) from dead.letter."))
            return

        purged = 0
        while True:
            if purged >= limit:
                break
            msg = channel.basic_get("dead.letter", no_ack=True)
            if msg is None:
                break
            purged += 1
        self.stdout.write(self.style.WARNING(f"Purged {purged} message(s) from dead.letter."))

    # ── helpers ───────────────────────────────────────────────────────────

    def _task_name(self, msg):
        try:
            body = json.loads(msg.body)
            return body.get("task", "unknown")
        except Exception:
            return "unknown"

    def _print_message(self, idx, msg):
        try:
            body = json.loads(msg.body)
            task = body.get("task", "?")
            args = body.get("args", [])
            x_death = msg.properties.get("application_headers", {}).get("x-death", [])
            origin = x_death[0].get("queue", "?") if x_death else "?"
            reason = x_death[0].get("reason", "?") if x_death else "?"
            self.stdout.write(
                f"  [{idx}] task={task}  origin={origin}  reason={reason}\n"
                f"       args={args}"
            )
        except Exception as e:
            self.stdout.write(f"  [{idx}] <unparseable: {e}>")
