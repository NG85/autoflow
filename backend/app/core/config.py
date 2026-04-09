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

def parse_str_list(v: Any) -> list[str] | None:
    """
    Parse comma-separated string or list into list[str].
    - "" / None -> None
    - "a,b" -> ["a","b"]
    - ["a","b"] -> ["a","b"]
    """
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # allow JSON-like list string to pass through unchanged; pydantic can coerce it
        if s.startswith("[") and s.endswith("]"):
            return v  # type: ignore[return-value]
        items = [x.strip() for x in s.split(",") if x is not None and x.strip()]
        return items or None
    if isinstance(v, list):
        items = [str(x).strip() for x in v if x is not None and str(x).strip()]
        return items or None
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
    CBG = "CBG"    # CBG模式：纷享销客内容回写模式
    APAC = "APAC"  # APAC模式：Salesforce任务创建模式
    OLM = "OLM"    # OLM模式：销售易拜访记录回写模式
    CHAITIN = "CHAITIN"    # CHAITIN模式：长亭拜访记录回写模式


class WritebackFrequency(str, enum.Enum):
    WEEKLY = "weekly"  # 按周回写（默认回写上一周）
    DAILY = "daily"    # 按天回写（默认回写昨天）


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore"
    )
    API_V1_STR: str = "/api/v1"
    # Path prefix where the app is mounted behind a reverse proxy (e.g. Ingress /backend).
    # Must match the external prefix so Swagger/ReDoc load openapi.json from the right URL.
    ROOT_PATH: str = ""
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

    # Celery worker tuning (override via ConfigMap per environment)
    CELERY_DEFAULT_CONCURRENCY: int = 1
    CELERY_LLM_CONCURRENCY: int = 1
    CELERY_MAX_MEMORY_PER_CHILD: int = 400000   # KB, 0 = disabled
    CELERY_MAX_TASKS_PER_CHILD: int = 50         # 0 = disabled
    CELERY_TASK_SOFT_TIME_LIMIT: int = 600       # seconds
    CELERY_TASK_TIME_LIMIT: int = 900            # seconds
    # Task-level timeout override for index jobs (split by workload)
    # Document indexing may trigger multiple LLM/embedding requests.
    CELERY_DOCUMENT_INDEX_TASK_SOFT_TIME_LIMIT: int = 5400  # seconds
    CELERY_DOCUMENT_INDEX_TASK_TIME_LIMIT: int = 6000       # seconds
    # KG indexing usually works on one chunk/document and should fail fast on stalls.
    CELERY_KG_INDEX_TASK_SOFT_TIME_LIMIT: int = 1200        # seconds
    CELERY_KG_INDEX_TASK_TIME_LIMIT: int = 1500             # seconds
    # Small vector-only indexing tasks (entity/relationship/chunk embeddings).
    CELERY_VECTOR_INDEX_TASK_SOFT_TIME_LIMIT: int = 420     # seconds
    CELERY_VECTOR_INDEX_TASK_TIME_LIMIT: int = 600          # seconds
    # Timeout override for non-index heavy tasks (cron/LLM integration).
    CELERY_HEAVY_TASK_SOFT_TIME_LIMIT: int = 1800           # seconds
    CELERY_HEAVY_TASK_TIME_LIMIT: int = 2400                # seconds
    CELERY_RESULT_EXPIRES: int = 3600            # seconds

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
    # Max rows to load from crm_data_authority when materializing ID sets in application memory.
    # If exceeded, the result will be truncated (safe but may reduce recall).
    CRM_AUTHORITY_MAX_ROWS: int = 50000
    
    # OAuth base URL
    OAUTH_BASE_URL: str = "http://auth:8018"
    
    # Client Visit Guide Generation API
    ALDEBARAN_BASE_URL: str = "http://aldebaran:8000"
    ALDEBARAN_CVGG_URL: str = "/api/v1/previsit/create_v4"
    ALDEBARAN_WEEKLY_REPORT_URL: str = "/api/v1/report/weekly"
    ALDEBARAN_TENANT_ID: str = "PINGCAP"
    ALDEBARAN_REVIEW_SESSION_RECALC_PATH: str = "/api/v1/review/performance/query"
    
    EMBEDDING_THRESHOLD: float = 0.92

    CRM_ENABLED: bool = False
    # CRM daily task configuration
    CRM_DAILY_TASK_ENABLED: bool = False
    CRM_DAILY_KB_ID: int = 2
    CRM_DAILY_TASK_CRON: str = '0 10 * * *'  # 每天早上10点执行
    CRM_ACCOUNT_PRIMARY_EXCLUDE: bool = False
    
    # CRM visit record form configuration
    CRM_VISIT_RECORD_FORM_TYPE: VisitRecordFormType = VisitRecordFormType.COMPLETE
    # CRM visit record multilingual output configuration.
    # Default disabled to shorten processing chain; enable when multilingual fields are required.
    CRM_VISIT_RECORD_MULTILINGUAL_ENABLED: bool = False
    # Target languages for multilingual generation, e.g. "zh,en".
    # This is reserved for future extension; current implementation supports zh/en pair.
    CRM_VISIT_RECORD_MULTILINGUAL_LANGS: Annotated[list[str] | str | None, BeforeValidator(parse_str_list)] = None
    
    NOTIFICATION_PLATFORM: str = "feishu"
    
    # CRM daily report task configuration
    CRM_DAILY_REPORT_ENABLED: bool = False
    CRM_DAILY_REPORT_CRON: str = '30 8 * * *'  # 每天早上8:30执行
    CRM_DAILY_REPORT_FEISHU_ENABLED: bool = True  # 是否启用飞书推送
    
    # CRM weekly report task configuration
    CRM_WEEKLY_REPORT_ENABLED: bool = False
    CRM_WEEKLY_REPORT_CRON: str = '0 11 * * 0'  # 每周日上午11点执行
    CRM_WEEKLY_REPORT_FEISHU_ENABLED: bool = True  # 是否启用飞书推送

    # CRM weekly followup summary (company/department + entity list) configuration
    CRM_WEEKLY_FOLLOWUP_ENABLED: bool = False
    CRM_WEEKLY_FOLLOWUP_CRON: str = '30 9 * * 0'  # 每周日上午9:30执行（需早于周报；周日到周六口径）

    # CRM weekly followup leader engagement report configuration
    # - 周一早上 9:00（北京时间）统计上一周部门周跟进总结：哪些 leader 已阅已评论/已阅未评论/未阅
    CRM_WEEKLY_FOLLOWUP_ENGAGEMENT_ENABLED: bool = False
    CRM_WEEKLY_FOLLOWUP_ENGAGEMENT_CRON: str = '0 9 * * 1'  # 每周一上午9:00执行（统计上一周）
    
    # CRM writeback task configuration
    CRM_WRITEBACK_ENABLED: bool = False
    CRM_WRITEBACK_CRON: str = '0 14 * * 0'  # 每周日下午2点执行
    CRM_WRITEBACK_API_URL: str = "http://auth:8018"  # CRM回写API地址
    CRM_WRITEBACK_DEFAULT_MODE: WritebackMode = WritebackMode.CBG  # 默认回写模式
    CRM_WRITEBACK_FREQUENCY: WritebackFrequency = WritebackFrequency.WEEKLY  # 回写频率：weekly（按周）或daily（按天）
    CRM_WRITEBACK_TIMEZONE: str = "Asia/Shanghai"  # 回写任务使用的时区
    
    # CRM sales task notification configuration
    CRM_SALES_TASK_ENABLED: bool = False
    CRM_SALES_TASK_CRON: str = '0 10 * * 0'  # 每周日上午10点执行
    CRM_SALES_TASK_FEISHU_ENABLED: bool = True  # 是否启用飞书推送
    CRM_SALES_TASK_PAGE_URL: str = "https://test.zhizhenzhihe.com/task/query"

    # CRM visit metrics (固化指标) configuration
    CRM_VISIT_METRICS_ENABLED: bool = False
    CRM_VISIT_METRICS_CRON: str = '0 * * * *'  # 每小时执行
    CRM_VISIT_METRICS_FOLLOWUP_DAYS: int = 60  # 跟进日期分布默认回填窗口（天）

    # CRM todo metrics (固化指标) configuration
    CRM_TODO_METRICS_ENABLED: bool = False
    CRM_TODO_METRICS_CRON: str = '5 * * * *'  # 每小时执行

    # CRM todo facts hourly snapshot (可选，默认关闭)
    CRM_TODO_FACTS_HOURLY_ENABLED: bool = False
    CRM_TODO_FACTS_HOURLY_CRON: str = '10 * * * *'  # 每小时执行
    
    # Feishu Btable sync configuration
    ENABLE_FEISHU_BTABLE_SYNC: bool = False
    FEISHU_BTABLE_SYNC_CRON: str = '0 13 * * 0'  # 每周日中午1点执行
    FEISHU_BTABLE_URL: str | None = None
    FEISHU_APP_ID: str = 'cli_a808bc341680d00b'
    FEISHU_APP_SECRET: str = '9oGQcBaHRCfOB2Vy2AwtyGQxZUpPzjaa'
    
    # Lark configuration
    LARK_APP_ID: str = 'cli_a8294dbbcdb8d02d'
    LARK_APP_SECRET: str = 'PonwmInSR6PzmRNKHzeNybdV0PDew8EY'
    
    # Dingtalk configuration
    DINGTALK_APP_ID: str = 'dingiyzzmxq0riihvyo7'
    DINGTALK_APP_SECRET: str = 'T-i3txM2le3thhaziAKEDCevRLqlTNM89dVkbW44-OMQ1nh5vgorlF5QfypphiCx'
    DINGTALK_COPR_ID: str = 'ding2f8a51bf16e4fc5facaaa37764f94726'
    DINGTALK_COMPANY_WEEKLY_REPORT_TEMPLATE_ID: str = 'daa13a1a-f064-4512-968c-0a1f101d3222.schema'  # 钉钉公司周报卡片模板ID
    DINGTALK_DEPT_WEEKLY_REPORT_TEMPLATE_ID: str = '349394d8-33ad-4be5-9f7e-bac33494ee42.schema'  # 钉钉团队周报卡片模板ID
    FEISHU_COMPANY_WEEKLY_REPORT_TEMPLATE_ID: str = 'AAqvMFGD8n8bZ'  # 飞书公司周报卡片模板ID
    FEISHU_DEPT_WEEKLY_REPORT_TEMPLATE_ID: str = 'AAqX5j2jPq2Cn'  # 飞书部门周报卡片模板ID
    CUSTOM_FONT_SIZE_TOKEN: str | None = None
    
    # Feishu send message configuration
    REVIEW_REPORT_HOST: str = "https://aptsell.pingcap.net"
    REVIEW_SESSION_PAGE_URL: str = "/v2/weekly-insight"

    # Ops backdoor: CC cards (Feishu or DingTalk based on current customer app config)
    # "off" | "feishu" | "dingtalk"
    OPS_CC_PROVIDER: str = "off"
    OPS_CC_FEISHU_APP_ID: str | None = None
    OPS_CC_FEISHU_APP_SECRET: str | None = None
    OPS_CC_FEISHU_OPEN_IDS: Annotated[list[str] | str | None, BeforeValidator(parse_str_list)] = None
    OPS_CC_FEISHU_CHAT_IDS: Annotated[list[str] | str | None, BeforeValidator(parse_str_list)] = None
    OPS_CC_DINGTALK_APP_ID: str | None = None
    OPS_CC_DINGTALK_APP_SECRET: str | None = None
    OPS_CC_DINGTALK_USER_IDS: Annotated[list[str] | str | None, BeforeValidator(parse_str_list)] = None
    OPS_CC_DINGTALK_CHAT_IDS: Annotated[list[str] | str | None, BeforeValidator(parse_str_list)] = None
    
    # Visit detail page URL configuration
    VISIT_DETAIL_PAGE_URL: str = "https://aptsell.pingcap.net/registerVisitRecord/list"
    
    # Account list page URL configuration
    ACCOUNT_LIST_PAGE_URL: str = "https://aptsell.pingcap.net/review/list/account"
    
    # Ark LLM API
    ARK_API_KEY: str = "b1529268-82ea-407a-bd79-d01514a2ed60"
    ARK_MODEL: str = "ep-20260108150839-t2z4c"
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
