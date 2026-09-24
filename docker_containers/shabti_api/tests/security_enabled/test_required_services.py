from shabti_keycloak import get_keycloak_client
from ...src.app.functionality import status


async def offline():
    return False


async def test_keycloak_status_is_checked(shabti_client):
    assert await status.check_keycloak()


def test_authenticated_routes_need_keycloak(shabti_client, monkeypatch):
    token = get_keycloak_client().token("testadmin", "test")
    monkeypatch.setattr(status, "check_keycloak", offline)
    response = shabti_client.get(
        "/collections", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert response.status_code == 503
    assert response.json()["error_type"] == "ServiceUnavailableError"
    assert response.json()["services"] == ["keycloak"]
