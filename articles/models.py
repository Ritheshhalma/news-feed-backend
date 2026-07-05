import uuid
from django.db import models


class MSTArticlePortal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name


class MSTArticleCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)

    class Meta:
        verbose_name_plural = "Article categories"

    def __str__(self):
        return self.name


class MSTTag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name


class MSTAuthor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    short_name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name


class Article(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    source_url = models.TextField(unique=True)
    hashed_key = models.CharField(max_length=255, unique=True)
    content_hash = models.CharField(max_length=64, blank=True)
    content = models.TextField()
    thumbnail_url = models.TextField(blank=True, null=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    POLL_TYPE_CHOICES = [("forex", "Forex"), ("stock", "Stock")]
    is_live = models.BooleanField(default=False)
    poll_type = models.CharField(max_length=20, choices=POLL_TYPE_CHOICES, blank=True, default="forex")
    live_poll_url = models.TextField(blank=True, null=True)
    author = models.ForeignKey(MSTAuthor, on_delete=models.SET_NULL, null=True, blank=True)
    portal = models.ForeignKey(MSTArticlePortal, on_delete=models.CASCADE)
    category = models.ForeignKey(MSTArticleCategory, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-published_at"]
        indexes = [
            models.Index(fields=["category", "-published_at"]),
            models.Index(fields=["portal", "-published_at"]),
            models.Index(fields=["is_live"]),
        ]

    def __str__(self):
        return self.title


class ArticleMedia(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    url = models.TextField(blank=True, null=True)
    type = models.CharField(max_length=20, blank=True)
    article = models.OneToOneField(Article, on_delete=models.CASCADE)


class ArticleTagMap(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    tag = models.ForeignKey(MSTTag, on_delete=models.CASCADE, null=True, blank=True)


class ArticleSource(models.Model):
    SOURCE_TYPE_CHOICES = [("rss", "RSS"), ("html", "HTML")]
    STATUS_CHOICES = [
        ("pending_validation", "Pending validation"),
        ("active", "Active"),
        ("failed", "Failed"),
    ]
    PARSER_MODE_CHOICES = [
        ("rss",            "RSS Feed (summary only)"),
        ("rss/multistage", "RSS Feed + Stage 2 full body (trafilatura)"),
        ("html",           "HTML listing only (card data)"),
        ("html/multistage","HTML multi-stage (trafilatura)"),
        ("js/playwright",  "JavaScript / Playwright"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    url = models.CharField(max_length=2048)
    source_type = models.CharField(max_length=10, choices=SOURCE_TYPE_CHOICES)
    parser_mode = models.CharField(max_length=20, choices=PARSER_MODE_CHOICES, default="rss")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending_validation")
    scrape_interval_minutes = models.PositiveIntegerField(default=30)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    portal = models.ForeignKey(MSTArticlePortal, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f"{self.portal} ({self.source_type})"


class SourceFetchLog(models.Model):
    TRIGGER_CHOICES = [
        ("scheduled", "Scheduled"),
        ("on_demand", "On-demand"),
        ("validation", "Validation"),
    ]
    STATUS_CHOICES = [
        ("started", "Started"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("partial", "Partial"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(ArticleSource, on_delete=models.CASCADE, related_name="fetch_logs")
    task_id = models.CharField(max_length=255, blank=True)
    trigger_type = models.CharField(max_length=20, choices=TRIGGER_CHOICES)
    attempt = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    articles_found = models.PositiveIntegerField(default=0)
    articles_created = models.PositiveIntegerField(default=0)
    articles_updated = models.PositiveIntegerField(default=0)
    articles_unchanged = models.PositiveIntegerField(default=0)
    articles_failed = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    details = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["source", "-started_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["task_id"]),
        ]


class ArticleRealTimeState(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    article = models.OneToOneField(Article, on_delete=models.CASCADE, related_name="realtimestate")
    current_data = models.JSONField(default=dict)
    last_updated_at = models.DateTimeField(auto_now=True)
