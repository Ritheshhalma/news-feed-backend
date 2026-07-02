from django.contrib import admin
from articles.models import MSTArticlePortal, MSTArticleCategory, MSTTag, MSTAuthor, Article, ArticleMedia, ArticleTagMap, ArticleSource, SourceFetchLog, ArticleRealTimeState

admin.site.register(MSTArticlePortal)
admin.site.register(MSTArticleCategory)
admin.site.register(MSTTag)
admin.site.register(MSTAuthor)
admin.site.register(Article)
admin.site.register(ArticleMedia)
admin.site.register(ArticleTagMap)
admin.site.register(ArticleSource)
admin.site.register(SourceFetchLog)
admin.site.register(ArticleRealTimeState)
