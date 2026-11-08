"""Optional Auth0 access-token validation for dashboard operator commands."""
from dataclasses import dataclass
import os
import re


OPERATOR_PERMISSION = 'sorter:operate'


class AuthError(Exception):
    def __init__(self, status=401):
        self.status = status


@dataclass(frozen=True)
class Auth0Config:
    domain: str
    client_id: str
    audience: str

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        values = [env.get(name, '').strip() for name in
                  ('AUTH0_DOMAIN', 'AUTH0_CLIENT_ID', 'AUTH0_AUDIENCE')]
        if not any(values):
            return None
        if not all(values):
            raise ValueError('Set AUTH0_DOMAIN, AUTH0_CLIENT_ID, and AUTH0_AUDIENCE together')
        domain, client_id, audience = values
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}[A-Za-z0-9]', domain) or '..' in domain:
            raise ValueError('AUTH0_DOMAIN must be a hostname without https:// or a path')
        if not client_id or not audience:
            raise ValueError('Auth0 client ID and API audience must be nonempty')
        return cls(domain, client_id, audience)

    def public(self):
        return {'enabled': True, 'domain': self.domain, 'clientId': self.client_id,
                'audience': self.audience}


class Auth0Verifier:
    def __init__(self, config):
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError('Install bridge/auth0-requirements.txt before enabling Auth0') from exc
        self.jwt = jwt
        self.config = config
        self.jwks = jwt.PyJWKClient(f'https://{config.domain}/.well-known/jwks.json',
                                     cache_jwk_set=True, lifespan=300, timeout=3)

    def verify(self, authorization):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
            raise AuthError()
        token = authorization[7:]
        if not token or len(token) > 8192 or any(char.isspace() for char in token):
            raise AuthError()
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = self.jwt.decode(token, key, algorithms=['RS256'],
                                     audience=self.config.audience,
                                     issuer=f'https://{self.config.domain}/',
                                     options={'require': ['exp', 'iat', 'sub']}, leeway=30)
        except Exception as exc:
            raise AuthError() from exc
        permissions = claims.get('permissions')
        if not isinstance(permissions, list) or OPERATOR_PERMISSION not in permissions:
            raise AuthError(403)
        return claims
