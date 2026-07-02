import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


def test_swagger_ui_is_reachable():
    client = Client()
    response = client.get("/api/schema/swagger-ui/")
    assert response.status_code == 200
