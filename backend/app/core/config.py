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


class StorageType(str, enum.Enum):
    TOS = "tos"
    MINIO = "minio"


class VisitRecordFormType(str, enum.Enum):
    SIMPLE = "simple"      # 简易版表单
    COMPLETE = "complete"  # 完整版表单


class WritebackMode(str, enum.Enum):
    CBG = "CBG"    # 内容回写模式
    APAC = "APAC"  # 任务创建模式


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore"
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    DOMAIN: str = "localhost"
    ENVIRONMENT: Environment = Environment.LOCAL
    LOG_LEVEL: str = "INFO"
    SQLALCHEMY_LOG_LEVEL: str = "WARNING"

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
    BACKEND_CORS_ORIGIN_REGEXP: str | None = None

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
 
    # Storage configuration
    STORAGE_TYPE: StorageType = StorageType.MINIO
    STORAGE_TENANT: str = "pingcap/data"
    CUSTOMER_UPLOADS_FOLDER: str = "/customer-uploads/"
    STORAGE_PATH_PREFIX: str = "pingcap/data/customer-uploads/"
    
    # TOS STS
    TOS_API_KEY: str = ""
    TOS_API_SECRET: str = ""
    TOS_API_HOST: str = "open.volcengineapi.com"
    TOS_REGION: str = "cn-beijing"
    TOS_ENDPOINT: str = f"tos-{TOS_REGION}.volces.com"
    TOS_BUCKET: str = "aptsell-dev"
    
    # MinIO configuration
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    MINIO_BUCKET: str = "autoflow"
    
    # CRM authority API
    CRM_AUTHORITY_API_URL: str = "http://auth:8018/crm/authority"
    
    # Client Visit Guide Generation API
    ALDEBARAN_BASE_URL: str = "http://aldebaran:8000"
    ALDEBARAN_CVGG_URL: str = f"{ALDEBARAN_BASE_URL}/api/v1/previsit/create_v4"
    ALDEBARAN_TENANT_ID: str = "PINGCAP"
    
    EMBEDDING_THRESHOLD: float = 0.92

    CRM_ENABLED: bool = False
    # CRM daily task configuration
    CRM_DAILY_TASK_ENABLED: bool = False
    CRM_DAILY_KB_ID: int = 2
    CRM_DAILY_TASK_CRON: str = '0 10 * * *'  # 每天早上10点执行
    CRM_ACCOUNT_PRIMARY_EXCLUDE: bool = False
    
    # CRM visit record form configuration
    CRM_VISIT_RECORD_FORM_TYPE: VisitRecordFormType = VisitRecordFormType.COMPLETE
    
    # CRM daily report task configuration
    CRM_DAILY_REPORT_ENABLED: bool = False
    CRM_DAILY_REPORT_CRON: str = '30 8 * * *'  # 每天早上8:30执行
    CRM_DAILY_REPORT_FEISHU_ENABLED: bool = True  # 是否启用飞书推送
    
    # CRM weekly report task configuration
    CRM_WEEKLY_REPORT_ENABLED: bool = False
    CRM_WEEKLY_REPORT_CRON: str = '0 11 * * 0'  # 每周日上午11点执行
    CRM_WEEKLY_REPORT_FEISHU_ENABLED: bool = True  # 是否启用飞书推送
    
    # CRM writeback task configuration
    CRM_WRITEBACK_ENABLED: bool = False
    CRM_WRITEBACK_CRON: str = '0 14 * * 0'  # 每周日下午2点执行
    CRM_WRITEBACK_API_URL: str = "http://auth:8018"  # CRM回写API地址
    CRM_WRITEBACK_DEFAULT_MODE: WritebackMode = WritebackMode.CBG  # 默认回写模式
    
    # Feishu Btable sync configuration
    ENABLE_FEISHU_BTABLE_SYNC: bool = False
    FEISHU_BTABLE_SYNC_CRON: str = '5 0 * * *'
    FEISHU_BTABLE_URL: str = 'https://pingcap-cn.feishu.cn/wiki/VWfHwGabtiHStUk1GvkcQQcFnjf?table=tblUoj9PFg92NYS8&view=vewsyBbD7L'
    FEISHU_APP_ID: str = 'cli_a74bce3ec73d901c'
    FEISHU_APP_SECRET: str = '1xC7zUP6PQpUoOMJte8tddgPm5zaqfoW'
    
    # Lark configuration
    LARK_APP_ID: str = 'cli_a8294dbbcdb8d02d'
    LARK_APP_SECRET: str = 'PonwmInSR6PzmRNKHzeNybdV0PDew8EY'
    
    # Feishu send message configuration
    REVIEW_REPORT_HOST: str = "https://aptsell.pingcap.net"
    
    # Visit detail page URL configuration
    VISIT_DETAIL_PAGE_URL: str = "https://aptsell.pingcap.net/registerVisitRecord/list"
    
    # Account list page URL configuration
    ACCOUNT_LIST_PAGE_URL: str = "https://aptsell.pingcap.net/review/list/account"
    
    # Ark LLM API
    ARK_API_KEY: str = "b1529268-82ea-407a-bd79-d01514a2ed60"
    ARK_MODEL: str = "ep-20250827204153-628mw"
    ARK_API_URL: str = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    
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
