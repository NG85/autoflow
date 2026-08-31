import enum
from typing import Annotated, Any, Optional
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
    UFILE = "ufile"


class VisitRecordFormType(str, enum.Enum):
    SIMPLE = "simple"      # 简易版表单
    COMPLETE = "complete"  # 完整版表单


class WritebackMode(str, enum.Enum):
    """CRM 回写网关变体枚举（**拜访记录**定时/管理端回写用）。

    - **拜访记录回写**：``Settings.CRM_WRITEBACK_DEFAULT_MODE`` 为 ``None`` 表示关闭（不注册 Beat）；
      非 ``None`` 时作为定时任务 / 管理端未显式传 ``writeback_mode`` 时的默认变体。
    - **Review 商机回写**：由 ``Settings.CRM_WRITEBACK_REVIEW_ENABLED`` 控制；网关 URL/包体统一，不由本枚举区分。
    """

    CBG = "CBG"  # CBG 纷享销客
    APAC = "APAC"  # APAC Salesforce
    OLM = "OLM"  # OLM 销售易
    CHAITIN = "CHAITIN"  # CHAITIN 长亭自研
    WEBEYE = "WEBEYE"  # WEBEYE 网眼简道云
    FENBEITONG = "FENBEITONG"  # 分贝通 纷享销客
    LIEPIN = "LIEPIN"  # 猎聘 CRM
    WYWJ = "WYWJ"  # 网眼云捷（飞书多维表格）
    ZHIPU = "ZHIPU"  # 智谱 纷享销客


class WritebackFrequency(str, enum.Enum):
    """CRM 拜访记录回写的数据窗口策略（与 ``CRM_WRITEBACK_CRON`` 调度解耦）。

    - weekly / daily：日历天窗口
    - interval：滚动分钟窗口（``CRM_WRITEBACK_LOOKBACK_MINUTES``）

    注意：多维表格使用独立的 ``FEISHU_BTABLE_SYNC_FREQUENCY``，勿与本枚举混用。
    """

    WEEKLY = "weekly"  # 按周回写（默认回写上一周：上周日~本周六）
    DAILY = "daily"  # 按天回写（默认回写昨天）
    INTERVAL = "interval"  # 滚动分钟窗口（默认回写最近 LOOKBACK 分钟）


class BitableSyncFrequency(str, enum.Enum):
    """多维表格回写的数据窗口策略（与 ``FEISHU_BTABLE_SYNC_CRON`` 解耦；不含 interval）。"""

    WEEKLY = "weekly"  # 上周日~本周六
    DAILY = "daily"  # 按 FEISHU_BTABLE_SYNC_CRON 时刻与 buffer 滚动 24h 窗口


class CRMWeeklyFollowupWeekPreset(str, enum.Enum):
    """周跟进总结统计周界预设（Python weekday：周一=0 … 周日=6）。"""

    SAT_FRI = "sat_fri"  # 周六~周五
    SUN_SAT = "sun_sat"  # 周日~周六
    MON_SUN = "mon_sun"  # 周一~周日


class CRMDailyReportVisitDateField(str, enum.Enum):
    """日报（个人/团队/公司）拜访统计的日期口径。"""

    VISIT_COMMUNICATION_DATE = "visit_communication_date"  # 跟进日期
    LAST_MODIFIED_TIME = "last_modified_time"  # 最后修改时间（UTC，按北京自然日）


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
    TIDB_AI_CHAT_ENDPOINT: str = "https://test.zhizhenzhihe.com/api/v1/chats"
    TIDB_AI_API_KEY: SecretStr | None = None
 
    # Storage configuration
    STORAGE_TYPE: StorageType = StorageType.MINIO
    STORAGE_TENANT: str = "aptsell/data"
    CUSTOMER_UPLOADS_FOLDER: str = "/customer-uploads/"
    STORAGE_PATH_PREFIX: str = "aptsell/data/customer-uploads/"
    
    # TOS STS
    TOS_API_KEY: str = ""
    TOS_API_SECRET: str = ""
    TOS_API_HOST: str = "open.volcengineapi.com"
    TOS_REGION: str = "cn-beijing"
    TOS_ENDPOINT: str = f"tos-{TOS_REGION}.volces.com"
    TOS_BUCKET: str = "aptsell-dev"
    
    # MinIO configuration (SigV2 POST policy)
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    MINIO_BUCKET: str = "autoflow"

    # UFile S3-compatible configuration (SigV4 POST policy)
    UFILE_ENDPOINT: str = ""
    UFILE_ACCESS_KEY: str = ""
    UFILE_SECRET_KEY: str = ""
    UFILE_BUCKET: str = ""
    # Empty = infer from endpoint host, e.g. s3-cn-wlcb.ufileos.com -> cn-wlcb
    UFILE_REGION: str = ""
    
    # Max rows to load from crm_data_authority when materializing ID sets in application memory.
    # If exceeded, the result will be truncated (safe but may reduce recall).
    CRM_AUTHORITY_MAX_ROWS: int = 50000
    
    # OAuth base URL
    OAUTH_BASE_URL: str = "http://auth:8018"
    # Bearer token for OAuth /permission/* service-to-service calls (empty = no header)
    OAUTH_PERMISSION_API_TOKEN: str = ""
    # W6 chats：SIA 问答 / 客户拜访攻略列表按 OAuth data-scope 过滤
    # （enablement_sia_history / enablement_visit_guide_history；关闭时回退“仅本人”旧逻辑）
    CHAT_OAUTH_SCOPE_ENABLED: bool = True
    # W6 日报/周报：生成阶段团队统计口径按 OAuth org_scope（daily_report_team / weekly_report_team）
    # 以部门负责人（user_department_relation.is_leader）为锚点展开汇报链人群；
    # 关闭或解析失败时回退按部门成员表展开
    REPORT_OAUTH_SCOPE_ENABLED: bool = True
    # OAuth service connectivity (read-only; must not break /healthz liveness)
    OAUTH_HEALTH_PROBE_ENABLED: bool = True
    OAUTH_HEALTH_PROBE_TIMEOUT_SECONDS: float = 3.0
    OAUTH_CLIENT_DEFAULT_TIMEOUT_SECONDS: float = 30.0
    OAUTH_CLIENT_RETRY_ATTEMPTS: int = 1  # extra attempts after the first (transport errors only)
    OAUTH_CLIENT_RETRY_BACKOFF_SECONDS: float = 0.2
    OAUTH_SESSION_ME_TIMEOUT_SECONDS: float = 10.0
    # Optional BFF: POST /auth/login/oauth (POST /auth/login remains the primary form login)
    OAUTH_BFF_LOGIN_ENABLED: bool = True
    OAUTH_ACCESS_TOKEN_COOKIE_NAME: str = "oauth_access_token"
    OAUTH_ACCESS_TOKEN_COOKIE_SAMESITE: str = "none"  # lax | strict | none
    # Registration: prefer oauth; fallback to local UserRepository.create
    OAUTH_REGISTER_ENABLED: bool = True
    OAUTH_REGISTER_CHANNEL: str = "siaweb"
    OAUTH_BOOTSTRAP_VIA_OAUTH: bool = False
    # After POST /auth/login succeeds, issue oauth_access_token via session/issue
    AUTH_LEGACY_OAUTH_SHADOW_ENABLED: bool = True
    OAUTH_LOGIN_CHANNEL: str = ""  # empty → OAUTH_REGISTER_CHANNEL (default siaweb)
    OAUTH_SESSION_ISSUE_ENABLED: bool = True
    OAUTH_SESSION_ISSUE_SECRET: str = ""  # shared with aptsell-oauth SESSION_ISSUE_SERVICE_SECRET
    AUTH_LEGACY_OAUTH_SHADOW_PASSWORD_FALLBACK: bool = False  # fallback: session/login with form creds

    # Feishu paid API configuration
    FEISHU_PAID_API_BASE_URL: str = "http://feishu-paid-api:8000"
    FEISHU_PAID_API_TIMEOUT_SECONDS: float = 5.0
    FEISHU_PAID_API_RETRY_ATTEMPTS: int = 3
    FEISHU_PAID_API_RETRY_BASE_SECONDS: float = 0.5
    
    # Client Visit Guide Generation API
    ALDEBARAN_BASE_URL: str = "http://aldebaran:8000"
    ALDEBARAN_CVGG_URL: str = "/api/v1/previsit/create_v4"
    ALDEBARAN_WEEKLY_REPORT_URL: str = "/api/v1/report/weekly"
    ALDEBARAN_TENANT_ID: str = "APTSELL"
    ALDEBARAN_REVIEW_SESSION_RECALC_PATH: str = "/api/v1/review/performance/query"
    # 周跟进：批量查询商机 forecast_amount / expected_closing_date
    ALDEBARAN_OPPORTUNITY_QUERY_AMOUNT_PATH: str = "/api/v1/opportunity/query/amount"
    # Aldebaran 消息队列：拜访保存 / 联系人创建事件入队（POST /api/v1/messages/incoming）
    ALDEBARAN_MESSAGES_INCOMING_PATH: str = "/api/v1/messages/incoming"
    ALDEBARAN_MESSAGE_WEBHOOK_SECRET: str = ""
    ALDEBARAN_MESSAGE_SOURCE_SYSTEM: str = "crm"
    ALDEBARAN_VISIT_RECORD_MESSAGE_TYPE: str = "crm.visit_record.saved"
    ALDEBARAN_VISIT_RECORD_REVISED_MESSAGE_TYPE: str = "crm.visit_record.revised"
    ALDEBARAN_CONTACT_CREATED_MESSAGE_TYPE: str = "local.contact.saved"
    ALDEBARAN_MESSAGE_RETRY_ATTEMPTS: int = 3
    ALDEBARAN_MESSAGE_RETRY_BASE_SECONDS: float = 0.5
    # 关闭时走本地空任务推卡降级（便于本地/联调）
    ALDEBARAN_VISIT_RECORD_POST_PROCESS_ENABLED: bool = True
    ALDEBARAN_CONTACT_CREATED_ENABLED: bool = True
    
    EMBEDDING_THRESHOLD: float = 0.92

    CRM_ENABLED: bool = False
    CRM_BILLING_ENABLED: bool = False
    # True：额度预检失败仍放行业务（仍会查额度并打日志；用量上报照常）
    CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA: bool = False
    # CRM daily task configuration
    CRM_DAILY_TASK_ENABLED: bool = False
    CRM_DAILY_KB_ID: int = 2
    # Dedicated KB for review-session indexing/chat retrieval.
    CRM_REVIEW_KB_ID: int = 2
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
    # 拜访记录修改：按录入时间（last_modified_time 折算北京时间日期）限制可改范围
    # 0 = 仅当日录入（默认）；>0 = 录入日起连续 N 个自然日内可改（含录入当天）；<0 = 不限制
    CRM_VISIT_RECORD_REVISE_ENTRY_WINDOW_DAYS: int = 0
    # 拜访记录修改：每日截止时间（北京时间 HH:MM），到达该时刻起当日不可再修改；空表示不限制
    # 可与 FEISHU_BTABLE_SYNC_CRON 对齐（如多维表格 20:20 回写可设为 20:00）
    CRM_VISIT_RECORD_REVISE_DAILY_CUTOFF_TIME: str = ""
    
    # CRM daily report task configuration
    CRM_DAILY_REPORT_ENABLED: bool = False
    CRM_DAILY_REPORT_CRON: str = '30 8 * * *'  # 每天早上8:30执行
    CRM_DAILY_REPORT_FEISHU_ENABLED: bool = True  # 是否启用飞书推送
    # 日报拜访日期口径（个人/团队/公司共用）。Autoflow 用它筛拜访记录；
    # crm_account_opportunity_assessment / crm_department_daily_summary 由上游按同一口径写入，
    # 本服务仍按 assessment_date / report_date 读取。
    CRM_DAILY_REPORT_VISIT_DATE_FIELD: CRMDailyReportVisitDateField = (
        CRMDailyReportVisitDateField.VISIT_COMMUNICATION_DATE
    )
    
    # CRM weekly report task configuration
    CRM_WEEKLY_REPORT_ENABLED: bool = False
    CRM_WEEKLY_REPORT_CRON: str = '0 11 * * 0'  # 每周日上午11点执行
    CRM_WEEKLY_REPORT_FEISHU_ENABLED: bool = True  # 是否启用飞书推送

    # CRM weekly followup summary (company/department + entity list) configuration
    CRM_WEEKLY_FOLLOWUP_ENABLED: bool = False
    # 统计周界：预设 sat_fri | sun_sat | mon_sun；也可用 START/END_WEEKDAY 覆盖（0=周一 … 6=周日）
    CRM_WEEKLY_FOLLOWUP_WEEK_PRESET: CRMWeeklyFollowupWeekPreset = CRMWeeklyFollowupWeekPreset.SUN_SAT
    CRM_WEEKLY_FOLLOWUP_WEEK_START_WEEKDAY: Optional[int] = None
    CRM_WEEKLY_FOLLOWUP_WEEK_END_WEEKDAY: Optional[int] = None
    # 周日：部门+实体（上一完整周，周界见上）
    CRM_WEEKLY_FOLLOWUP_CRON: str = '30 9 * * 0'
    # 周日：公司总结（上一完整周，需早于 generate_crm_weekly_report）
    CRM_WEEKLY_FOLLOWUP_COMPANY_CRON: str = '40 9 * * 0'
    CRM_WEEKLY_FOLLOWUP_LLM_MAX_CONCURRENCY: int = 4
    # 周跟进输入规模控制：<=0 表示不限制（默认保留完整上下文）
    CRM_WEEKLY_FOLLOWUP_ENTITY_LLM_MAX_VISITS: int = 0
    CRM_WEEKLY_FOLLOWUP_ROLLUP_MAX_VISITS_PER_ENTITY: int = 0
    CRM_WEEKLY_FOLLOWUP_VISIT_CONTEXT_MAX_CHARS: int = 0

    # CRM weekly followup leader engagement report configuration
    # - 周一早上 9:00（北京时间）统计上一周（周六~周五）部门周跟进总结：哪些 leader 已阅已评论/已阅未评论/未阅
    CRM_WEEKLY_FOLLOWUP_ENGAGEMENT_ENABLED: bool = False
    CRM_WEEKLY_FOLLOWUP_ENGAGEMENT_CRON: str = '0 9 * * 1'  # 每周一上午9:00执行（统计上一周）
    
    # CRM writeback task configuration
    # 调度（何时触发）：仅认 5 段 cron；与下方 FREQUENCY（扫什么数据）解耦
    CRM_WRITEBACK_CRON: str = "0 14 * * 0"  # 例：每周日 14:00；分贝通可用 */30 * * * *
    CRM_WRITEBACK_API_URL: str = "http://salesforce:8080"  # CRM回写API地址
    # 拜访记录回写：None 表示关闭（不注册 Beat、执行层跳过）；非 None 为默认网关变体
    CRM_WRITEBACK_DEFAULT_MODE: Optional[WritebackMode] = None
    # Review 商机网关回写：为 True 时成员提交等路径会调用 CRM（与拜访回写独立；具体 CRM 由网关路由）
    CRM_WRITEBACK_REVIEW_ENABLED: bool = False
    # 数据窗口策略（仅 CRM 拜访回写）：weekly / daily / interval
    CRM_WRITEBACK_FREQUENCY: WritebackFrequency = WritebackFrequency.WEEKLY
    # interval 模式回溯分钟数；建议 = 调度间隔 + 少量重叠（如 30min 调度用 35）
    CRM_WRITEBACK_LOOKBACK_MINUTES: int = 35
    CRM_WRITEBACK_TIMEZONE: str = "Asia/Shanghai"  # 回写任务使用的时区
    # Review 商机回写：POST ``{CRM_WRITEBACK_API_URL}{CRM_WRITEBACK_REVIEW_PATH}``
    CRM_WRITEBACK_REVIEW_PATH: str = "/crm-custom/update-business-opportunity"
    # 网眼（简道云）拜访回写：是否回写 lead（线索）跟进；默认 False（不回写线索跟进）
    CRM_WEBEYE_WRITEBACK_LEAD_ENABLED: bool = False
    
    # CRM sales task notification configuration
    CRM_SALES_TASK_ENABLED: bool = False
    CRM_SALES_TASK_CRON: str = '0 10 * * 0'  # 每周日上午10点执行
    CRM_SALES_TASK_FEISHU_ENABLED: bool = True  # 是否启用飞书推送
    CRM_SALES_TASK_PAGE_URL: str = "/v2/task"

    # CRM visit metrics (固化指标) configuration
    CRM_VISIT_METRICS_ENABLED: bool = False
    CRM_VISIT_METRICS_CRON: str = '0 * * * *'  # 每小时执行
    CRM_VISIT_METRICS_FOLLOWUP_DAYS: int = 7  # 跟进日期分布默认回填窗口（天）

    # CRM todo metrics (固化指标) configuration
    CRM_TODO_METRICS_ENABLED: bool = False
    CRM_TODO_METRICS_CRON: str = '5 * * * *'  # 每小时执行

    # CRM todo facts hourly snapshot (可选，默认关闭)
    CRM_TODO_FACTS_HOURLY_ENABLED: bool = False
    CRM_TODO_FACTS_HOURLY_CRON: str = '10 * * * *'  # 每小时执行
    
    # Feishu Btable sync configuration
    ENABLE_FEISHU_BTABLE_SYNC: bool = False
    FEISHU_BTABLE_SYNC_CRON: str = '0 13 * * 0'  # 每周日中午1点执行
    # 多维表格窗口策略（与 CRM_WRITEBACK_FREQUENCY 完全独立）
    FEISHU_BTABLE_SYNC_FREQUENCY: BitableSyncFrequency = BitableSyncFrequency.WEEKLY
    # DAILY 模式下统计窗口截止时刻 = FEISHU_BTABLE_SYNC_CRON 中的时刻往前推该分钟数
    # 例如 cron 为 30 20 * * * 且 buffer=30 → 窗口 [昨天20:00, 今天20:00)
    FEISHU_BTABLE_SYNC_WINDOW_BUFFER_MINUTES: int = 30
    FEISHU_BTABLE_URL: str | None = None
    # 飞书 / Lark / 钉钉：不在代码库中写默认凭据，由部署环境（ConfigMap / Secret / .env）注入
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""

    # Lark configuration
    LARK_APP_ID: str = ""
    LARK_APP_SECRET: str = ""

    # Dingtalk configuration
    DINGTALK_APP_ID: str = ""
    DINGTALK_APP_SECRET: str = ""
    DINGTALK_COPR_ID: str = ""
    # 钉钉听记：写入预建 AI 表格后轮询读取「听记总结」（非逐字稿）
    DINGTALK_TRANSCRIBE_NOTABLE_URL: str | None = None
    DINGTALK_NOTABLE_OPERATOR_UNION_ID: str | None = None
    DINGTALK_TRANSCRIBE_LINK_FIELD: str = "链接"
    DINGTALK_TRANSCRIBE_CONTENT_FIELD: str = "听记总结"
    DINGTALK_TRANSCRIBE_POLL_INTERVAL_SEC: float = 3.0
    DINGTALK_TRANSCRIBE_POLL_TIMEOUT_SEC: float = 120.0
    CUSTOM_FONT_SIZE_TOKEN: str | None = None
    
    # Feishu send message configuration
    REVIEW_REPORT_HOST: str = "https://test.zhizhenzhihe.com"
    REVIEW_SESSION_PAGE_URL: str = "/v2/business/weekly-insight"
    CRM_REVIEW_SESSION_NOTIFICATION_ENABLED: bool = False

    # Ops backdoor: CC cards (Feishu or DingTalk per OPS_CC_PROVIDER)
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
    VISIT_DETAIL_PAGE_URL: str = "/v2/behavior"
    # 当日无跟进提醒：「立即录入跟进」路径，与 REVIEW_REPORT_HOST 拼接为完整 URL
    CRM_VISIT_FOLLOWUP_ENTRY_PAGE_URL: str = "/registerVisitRecord/register"
    
    # Ark LLM API
    ARK_API_KEY: str = "b1529268-82ea-407a-bd79-d01514a2ed60"
    ARK_MODEL: str = "ep-20260807151127-l88dw"
    ARK_API_URL: str = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    ARK_HTTP_CONNECT_TIMEOUT: float = 10.0
    ARK_HTTP_READ_TIMEOUT: float = 180.0
    
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
    def _validate_weekly_followup_week_boundary(self) -> Self:
        start = self.CRM_WEEKLY_FOLLOWUP_WEEK_START_WEEKDAY
        end = self.CRM_WEEKLY_FOLLOWUP_WEEK_END_WEEKDAY
        if (start is None) != (end is None):
            raise ValueError(
                "CRM_WEEKLY_FOLLOWUP_WEEK_START_WEEKDAY and CRM_WEEKLY_FOLLOWUP_WEEK_END_WEEKDAY "
                "must be set together or both omitted"
            )
        for name, val in (("START", start), ("END", end)):
            if val is not None and not (0 <= val <= 6):
                raise ValueError(
                    f"CRM_WEEKLY_FOLLOWUP_WEEK_{name}_WEEKDAY must be between 0 (Monday) and 6 (Sunday), got {val!r}"
                )
        return self

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
