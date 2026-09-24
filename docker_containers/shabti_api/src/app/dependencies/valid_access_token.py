from fastapi import Depends, HTTPException
from fastapi.security import OAuth2AuthorizationCodeBearer
from typing import Annotated
from shabti_keycloak import server_url, get_keycloak_client
from jwcrypto.jwt import JWTExpired
from shabti_types import Service
from .required_services import RequiredServices

oauth_2_scheme = OAuth2AuthorizationCodeBearer(
    tokenUrl=f"{server_url()}/realms/shabti/protocol/openid-connect/token",
    authorizationUrl=f"{server_url()}/realms/shabti/protocol/openid-connect/auth",
    refreshUrl=f"{server_url()}/realms/shabti/protocol/openid-connect/token",
)


async def valid_access_token(
    # every route that takes a token needs Keycloak to check it, so this is where that's required.
    # ahead of the scheme, which only reads the header
    _: Annotated[None, Depends(RequiredServices(Service.KEYCLOAK))],
    access_token: Annotated[str, Depends(oauth_2_scheme)],
):
    try:
        client = get_keycloak_client()
        client.decode_token(access_token)
        return access_token
    except JWTExpired:
        raise HTTPException(status_code=401, detail="Token expired")
