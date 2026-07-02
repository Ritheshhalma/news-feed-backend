from django.urls import path
from rest_framework.routers import DefaultRouter

from articles.views import ArticleViewSet, MSTArticleCategoryViewSet, MSTTagViewSet, ArticleSourceViewSet, JobStatusView

router = DefaultRouter()
router.register(r"articles", ArticleViewSet, basename="article")
router.register(r"categories", MSTArticleCategoryViewSet, basename="category")
router.register(r"tags", MSTTagViewSet, basename="tag")
router.register(r"sources", ArticleSourceViewSet, basename="source")

urlpatterns = router.urls + [
    path("jobs/<str:task_id>/", JobStatusView.as_view(), name="job-status"),
]
