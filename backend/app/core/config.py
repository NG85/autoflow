import enum
from typing import Annotated, Any
from urllib.parse import quote

from pydantic import (
    AnyUrl,
    BeforeValidator,
    HttpUrl,
    MySQLDsn,
    SecretStr,
    computed_field,
    model_validator,
)
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Environment(str, enum.Enum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore"
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    DOMAIN: str = "localhost"
    ENVIRONMENT: Environment = Environment.LOCAL

    SESSION_COOKIE_NAME: str = "session"
    # 90 days
    SESSION_COOKIE_MAX_AGE: int = 3600 * 24 * 90
    SESSION_COOKIE_SECURE: bool = True

    BROWSER_ID_COOKIE_NAME: str = "bid"
    BROWSER_ID_COOKIE_MAX_AGE: int = 3600 * 24 * 365 * 2

    @computed_field  # type: ignore[misc]
    @property
    def server_host(self) -> str:
        # Use HTTPS for anything other than local development
        if self.ENVIRONMENT == Environment.LOCAL:
            return f"http://{self.DOMAIN}"
        return f"https://{self.DOMAIN}"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    PROJECT_NAME: str = "APTSELL.AI"
    SENTRY_DSN: HttpUrl | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 1.0
    SENTRY_PROFILES_SAMPLE_RATE: float = 1.0

    @model_validator(mode="after")
    def _validate_sentry_sample_rate(self) -> Self:
        if not self.SENTRY_DSN:
            return self
        if self.SENTRY_TRACES_SAMPLE_RATE < 0 or self.SENTRY_TRACES_SAMPLE_RATE > 1:
            raise ValueError("SENTRY_TRACES_SAMPLE_RATE must be between 0 and 1")
        if self.SENTRY_PROFILES_SAMPLE_RATE < 0 or self.SENTRY_PROFILES_SAMPLE_RATE > 1:
            raise ValueError("SENTRY_PROFILES_SAMPLE_RATE must be between 0 and 1")
        return self

    LOCAL_FILE_STORAGE_PATH: str = "/shared/data"

    TIDB_HOST: str = "127.0.0.1"
    TIDB_PORT: int = 4000
    TIDB_USER: str = "root"
    TIDB_PASSWORD: str = ""
    TIDB_DATABASE: str
    TIDB_SSL: bool = True

    ENABLE_QUESTION_CACHE: bool = False

    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"

    # TODO: move below config to `option` table, it should be configurable by staff in console
    TIDB_AI_CHAT_ENDPOINT: str = "https://af.aptsell.ai/api/v1/chats"
    TIDB_AI_API_KEY: SecretStr | None = None
 
    # TOS STS
    TOS_API_KEY: str = ""
    TOS_API_SECRET: str = ""
    TOS_API_HOST: str = "open.volcengineapi.com"
    TOS_REGION: str = "cn-beijing"
    TOS_ENDPOINT: str = f"tos-{TOS_REGION}.volces.com"
    TOS_BUCKET: str = "aptsell-dev"
    TOS_PATH_PREFIX: str = "data/customer-uploads/"
    
    # CRM authority API
    CRM_AUTHORITY_API_URL: str = "http://auth:8018/crm/authority/user"
    
    # Client Visit Guide Generation API
    ALDEBARAN_CVGG_URL: str = "http://aldebaran:8001/api/v1/previsit/create_v3"
    ALDEBARAN_TENANT_ID: str = "PINGCAP"
    
    EMBEDDING_THRESHOLD: float = 0.875
    
    COMPLIED_INTENT_ANALYSIS_PROGRAM_PATH: str | None = None
    COMPLIED_PREREQUISITE_ANALYSIS_PROGRAM_PATH: str | None = None

    # NOTICE: EMBEDDING_DIMS and EMBEDDING_MAX_TOKENS is deprecated and
    # will be removed in the future.
    EMBEDDING_DIMS: int = 1536
    EMBEDDING_MAX_TOKENS: int = 2048

    EVALUATION_OPENAI_API_KEY: str | None = None

    @computed_field  # type: ignore[misc]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> MySQLDsn:
        return MultiHostUrl.build(
            scheme="mysql+pymysql",
            username=self.TIDB_USER,
            # TODO: remove quote after following issue is fixed:
            # https://github.com/pydantic/pydantic/issues/8061
            password=quote(self.TIDB_PASSWORD),
            host=self.TIDB_HOST,
            port=self.TIDB_PORT,
            path=self.TIDB_DATABASE,
            query="ssl_verify_cert=true&ssl_verify_identity=true"
            if self.TIDB_SSL
            else None,
        )

    @computed_field  # type: ignore[misc]
    @property
    def SQLALCHEMY_ASYNC_DATABASE_URI(self) -> MySQLDsn:
        return MultiHostUrl.build(
            scheme="mysql+asyncmy",
            username=self.TIDB_USER,
            password=quote(self.TIDB_PASSWORD),
            host=self.TIDB_HOST,
            port=self.TIDB_PORT,
            path=self.TIDB_DATABASE,
        )

    @model_validator(mode="after")
    def _validate_secrets(self) -> Self:
        secret = self.SECRET_KEY
        if not secret:
            raise ValueError(
                "Please set a secret key using the SECRET_KEY environment variable."
            )

        min_length = 32
        if len(secret.encode()) < min_length:
            message = (
                "The SECRET_KEY is too short, "
                f"please use a longer secret, at least {min_length} characters."
            )
            raise ValueError(message)
        return self


settings = Settings()  # type: ignore
