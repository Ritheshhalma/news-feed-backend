import django_filters
from articles.models import Article


class ArticleFilter(django_filters.FilterSet):
    category_id = django_filters.UUIDFilter(field_name="category_id")
    tag_id = django_filters.UUIDFilter(field_name="articletagmap__tag_id")
    source = django_filters.UUIDFilter(field_name="portal_id")
    published_after = django_filters.IsoDateTimeFilter(field_name="published_at", lookup_expr="gte")
    published_before = django_filters.IsoDateTimeFilter(field_name="published_at", lookup_expr="lte")
    search = django_filters.CharFilter(field_name="title", lookup_expr="icontains")
    is_live = django_filters.BooleanFilter(field_name="is_live")

    class Meta:
        model = Article
        fields = ["category_id", "tag_id", "source", "published_after", "published_before", "search", "is_live"]
