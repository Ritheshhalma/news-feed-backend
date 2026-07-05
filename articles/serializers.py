from rest_framework import serializers
from articles.models import Article, ArticleRealTimeState, MSTArticlePortal, MSTArticleCategory, MSTTag, ArticleSource


class MSTArticlePortalSerializer(serializers.ModelSerializer):
    class Meta:
        model = MSTArticlePortal
        fields = ["id", "name"]


class MSTArticleCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MSTArticleCategory
        fields = ["id", "name"]


class MSTTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = MSTTag
        fields = ["id", "name"]


class ArticleSerializer(serializers.ModelSerializer):
    portal = MSTArticlePortalSerializer(read_only=True)
    category = MSTArticleCategorySerializer(read_only=True)
    full_image_url = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    author = serializers.SerializerMethodField()
    live_data = serializers.SerializerMethodField()

    class Meta:
        model = Article
        fields = [
            "id", "title", "source_url", "content", "thumbnail_url", "full_image_url",
            "published_at", "created_at", "updated_at", "is_live", "poll_type",
            "portal", "category", "tags", "author", "live_data",
        ]

    def get_full_image_url(self, obj):
        media = getattr(obj, "articlemedia", None)
        return media.url if media else None

    def get_tags(self, obj):
        return [
            {"id": str(m.tag.id), "name": m.tag.name}
            for m in obj.articletagmap_set.all()
            if m.tag is not None
        ]

    def get_author(self, obj):
        return obj.author.name if obj.author else None

    def get_live_data(self, obj):
        if not obj.is_live:
            return None
        state = ArticleRealTimeState.objects.filter(article=obj).first()
        return state.current_data if state else None


class ArticleSourceSerializer(serializers.ModelSerializer):
    portal_name = serializers.CharField(source="portal.name", read_only=True)

    class Meta:
        model = ArticleSource
        fields = [
            "id", "url", "source_type", "parser_mode", "status", "scrape_interval_minutes",
            "last_fetched_at", "last_success_at", "error_message", "portal", "portal_name",
        ]
        read_only_fields = ["status", "last_fetched_at", "last_success_at", "error_message"]
