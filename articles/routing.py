from django.urls import re_path

from articles.consumers import FeedConsumer, LiveArticleConsumer

websocket_urlpatterns = [
    re_path(r"^ws/feed/$", FeedConsumer.as_asgi()),
    re_path(r"^ws/live/(?P<article_id>[\w-]+)/$", LiveArticleConsumer.as_asgi()),
]
