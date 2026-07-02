import json

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


class FeedConsumer(AsyncWebsocketConsumer):
    GROUP = "feed"

    async def connect(self):
        await self.channel_layer.group_add(self.GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)

    async def feed_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))


class LiveArticleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.article_id = self.scope["url_route"]["kwargs"]["article_id"]
        self.group_name = f"live_article_{self.article_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        # Push current state immediately so the client doesn't wait for the next poll cycle
        current = await self._get_current_state()
        if current:
            await self.send(text_data=json.dumps(current))

    @sync_to_async
    def _get_current_state(self):
        from articles.models import ArticleRealTimeState  # local import avoids app-registry issues
        try:
            state = ArticleRealTimeState.objects.filter(article_id=self.article_id).first()
            return state.current_data if state else None
        except Exception:
            return None

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def live_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))
