import pytest
from channels.testing import WebsocketCommunicator
from config.asgi import application

pytestmark = pytest.mark.django_db


@pytest.mark.asyncio
async def test_feed_consumer_accepts_connection_and_receives_group_message():
    communicator = WebsocketCommunicator(application, "/ws/feed/")
    connected, _ = await communicator.connect()
    assert connected

    from channels.layers import get_channel_layer
    layer = get_channel_layer()
    await layer.group_send("feed", {"type": "feed.update", "data": {"new_count": 3}})

    message = await communicator.receive_json_from()
    assert message == {"new_count": 3}
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_live_article_consumer_joins_its_own_article_group():
    communicator = WebsocketCommunicator(application, "/ws/live/abc-123/")
    connected, _ = await communicator.connect()
    assert connected

    from channels.layers import get_channel_layer
    layer = get_channel_layer()
    await layer.group_send("live_article_abc-123", {"type": "live.update", "data": {"rate": 83.5}})

    message = await communicator.receive_json_from()
    assert message == {"rate": 83.5}
    await communicator.disconnect()
