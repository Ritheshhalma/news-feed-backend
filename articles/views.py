from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from rest_framework import serializers
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from articles.adapters.html import HTMLAdapter
from articles.adapters.rss import RSSAdapter
from articles.filters import ArticleFilter
from articles.models import Article, ArticleRealTimeState, MSTArticleCategory, MSTTag, ArticleSource, MSTArticlePortal, SourceFetchLog
from articles.serializers import (
    ArticleSerializer, MSTArticleCategorySerializer, MSTTagSerializer, ArticleSourceSerializer,
)
from articles.tasks import scrape_source

_ADAPTERS = {"rss": RSSAdapter, "html": HTMLAdapter}


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


class ArticleSourceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ArticleSource.objects.select_related("portal").order_by("-last_fetched_at")
    serializer_class = ArticleSourceSerializer
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        portal, _ = MSTArticlePortal.objects.get_or_create(name=request.data["portal_name"])
        source = ArticleSource.objects.create(
            url=request.data["url"], source_type=request.data["source_type"], portal=portal,
        )
        try:
            _ADAPTERS[source.source_type](source).fetch()
            source.status = "active"
        except Exception as exc:
            source.status = "failed"
            source.error_message = str(exc)
        source.save(update_fields=["status", "error_message"])
        return Response(ArticleSourceSerializer(source).data, status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def refresh(self, request, pk=None):
        source = self.get_object()
        task = scrape_source.delay(str(source.id), trigger_type="on_demand")
        return Response({"task_id": task.id}, status=http_status.HTTP_202_ACCEPTED)


class SourceFetchLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceFetchLog
        fields = [
            "id", "task_id", "trigger_type", "attempt", "status", "started_at",
            "finished_at", "duration_ms", "articles_found", "articles_created",
            "articles_updated", "articles_unchanged", "error_message",
        ]


class JobStatusView(APIView):
    def get(self, request, task_id):
        logs = SourceFetchLog.objects.filter(task_id=task_id).order_by("-attempt")
        return Response(SourceFetchLogSerializer(logs, many=True).data)
