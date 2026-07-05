from datetime import timedelta

from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from rest_framework import serializers
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from articles.filters import ArticleFilter
from articles.models import Article, ArticleRealTimeState, MSTArticleCategory, MSTTag, ArticleSource, MSTArticlePortal, SourceFetchLog
from articles.serializers import (
    ArticleSerializer, MSTArticleCategorySerializer, MSTTagSerializer, ArticleSourceSerializer,
)
from articles.services.detect import auto_detect_parser_mode
from articles.tasks import scrape_source, scrape_playwright_source, resync_source, _get_adapter


class ArticleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Article.objects.select_related("portal", "category", "author").prefetch_related("articletagmap_set__tag").all()
    serializer_class = ArticleSerializer
    filterset_class = ArticleFilter
    lookup_field = "id"

    @method_decorator(cache_page(90))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @method_decorator(cache_page(90))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @action(detail=True, methods=["get"], url_path="live_state")
    def live_state(self, request, *args, **kwargs):
        article = self.get_object()
        if not article.is_live:
            return Response({"detail": "Not a live article."}, status=http_status.HTTP_400_BAD_REQUEST)
        state = ArticleRealTimeState.objects.filter(article=article).first()
        return Response({"article_id": str(article.id), "data": state.current_data if state else {}})


class MSTArticleCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MSTArticleCategory.objects.order_by("name")
    serializer_class = MSTArticleCategorySerializer


class MSTTagViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MSTTag.objects.order_by("name")
    serializer_class = MSTTagSerializer


# Safety net only — a legitimate run should always finish (and mark its own
# SourceFetchLog success/failed/partial) well before this. It exists purely so
# a crashed/killed worker can't leave a source permanently un-refreshable.
_STALE_LOCK_MINUTES = 20


def _fetch_in_progress(source) -> bool:
    return SourceFetchLog.objects.filter(
        source=source,
        status="started",
        started_at__gte=timezone.now() - timedelta(minutes=_STALE_LOCK_MINUTES),
    ).exists()


def _in_progress_conflict(source) -> Response | None:
    """Return a 409 Response if a fetch is already running for source, else None."""
    if not _fetch_in_progress(source):
        return None
    return Response(
        {"detail": "A refresh is already in progress for this source."},
        status=http_status.HTTP_409_CONFLICT,
    )


class ArticleSourceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ArticleSource.objects.select_related("portal").order_by("-last_fetched_at")
    serializer_class = ArticleSourceSerializer
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        url = request.data.get("url", "").strip()
        portal_name = request.data.get("portal_name", "").strip()

        if not url or not portal_name:
            return Response(
                {"detail": "url and portal_name are required."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        # Auto-detect how this URL should be scraped
        try:
            parser_mode, effective_url = auto_detect_parser_mode(url)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=http_status.HTTP_400_BAD_REQUEST)

        source_type = "rss" if parser_mode == "rss" else "html"

        # Case-insensitive lookup so "bbc" / "BBC" / " BBC " all resolve to the
        # same portal instead of fragmenting into separate rows that split a
        # single outlet's articles across two different "portals".
        portal = MSTArticlePortal.objects.filter(name__iexact=portal_name).first()
        if portal is None:
            portal = MSTArticlePortal.objects.create(name=portal_name)
        source = ArticleSource.objects.create(
            url=effective_url,
            source_type=source_type,
            parser_mode=parser_mode,
            portal=portal,
        )

        # Validate the source is reachable and parseable
        try:
            _get_adapter(source).validate()
            source.status = "active"
        except Exception as exc:
            source.status = "failed"
            source.error_message = str(exc)

        source.save(update_fields=["status", "error_message"])
        return Response(ArticleSourceSerializer(source).data, status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def refresh(self, request, pk=None):
        source = self.get_object()
        force = bool(request.data.get("force", False))

        conflict = _in_progress_conflict(source)
        if conflict:
            return conflict

        if source.parser_mode == "js/playwright":
            task = scrape_playwright_source.delay(
                str(source.id), trigger_type="on_demand", force=force
            )
        else:
            task = scrape_source.delay(
                str(source.id), trigger_type="on_demand", force=force
            )

        return Response({"task_id": task.id}, status=http_status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def resync(self, request, pk=None):
        """Deep resync: re-fetch every stored article for this source's portal
        directly by its own source_url — reaches articles Refresh can't (ones
        that scrolled off the listing page, or stuck with bad/empty content).
        """
        source = self.get_object()

        conflict = _in_progress_conflict(source)
        if conflict:
            return conflict

        task = resync_source.delay(str(source.id))
        return Response({"task_id": task.id}, status=http_status.HTTP_202_ACCEPTED)


class SourceFetchLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceFetchLog
        fields = [
            "id", "task_id", "trigger_type", "attempt", "status", "started_at",
            "finished_at", "duration_ms", "articles_found", "articles_created",
            "articles_updated", "articles_unchanged", "articles_failed",
            "error_message", "details",
        ]


class JobStatusView(APIView):
    def get(self, request, task_id):
        logs = SourceFetchLog.objects.filter(task_id=task_id).order_by("-attempt")
        return Response(SourceFetchLogSerializer(logs, many=True).data)
