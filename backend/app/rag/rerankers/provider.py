import enum
from typing import List
from pydantic import BaseModel


class RerankerProvider(str, enum.Enum):
    JINA = "jina"
    COHERE = "cohere"
    BAISHENG = "baisheng"
    LOCAL = "local"
    VLLM = "vllm"
    XINFERENCE = "xinference"
    BEDROCK = "bedrock"
    ZHIPU = "zhipu"
    CLOUD = "cloud"


class RerankerProviderOption(BaseModel):
    provider: RerankerProvider
    provider_display_name: str | None = None
    provider_description: str | None = None
    provider_url: str | None = None
    default_reranker_model: str
    reranker_model_description: str
    default_top_n: int = 10
    default_credentials: str | dict = ""
    default_config: dict = {}
    config_description: str = ""
    credentials_display_name: str
    credentials_description: str
    credentials_type: str = "str"


reranker_provider_options: List[RerankerProviderOption] = [
    RerankerProviderOption(
        provider=RerankerProvider.JINA,
        provider_display_name="Jina AI",
        provider_description="We provide best-in-class embeddings, rerankers, LLM-reader and prompt optimizers, pioneering search AI for multimodal data.",
        provider_url="https://jina.ai",
        default_reranker_model="jina-reranker-v2-base-multilingual",
        reranker_model_description="Reference: https://jina.ai/reranker/",
        default_top_n=10,
        credentials_display_name="Jina API Key",
        credentials_description="You can get one from https://jina.ai/reranker/",
        credentials_type="str",
        default_credentials="jina_****",
    ),
    RerankerProviderOption(
        provider=RerankerProvider.COHERE,
        provider_display_name="Cohere",
        provider_description="Cohere provides industry-leading large language models (LLMs) and RAG capabilities tailored to meet the needs of enterprise use cases that solve real-world problems.",
        provider_url="https://cohere.com/",
        default_reranker_model="rerank-multilingual-v3.0",
        reranker_model_description="Reference: https://docs.cohere.com/reference/rerank",
        default_top_n=10,
        credentials_display_name="Cohere API Key",
        credentials_description="You can get one from https://dashboard.cohere.com/api-keys",
        credentials_type="str",
        default_credentials="*****",
    ),
    RerankerProviderOption(
        provider=RerankerProvider.BAISHENG,
        provider_display_name="BaiSheng",
        default_reranker_model="bge-reranker-v2-m3",
        reranker_model_description="",
        default_top_n=10,
        default_config={
            "api_url": "http://api.chat.prd.yumc.local/chat/v1/reranker",
        },
        credentials_display_name="BaiSheng API Key",
        credentials_description="",
        credentials_type="str",
        default_credentials="*****",
    ),
    RerankerProviderOption(
        provider=RerankerProvider.LOCAL,
        provider_display_name="Local Reranker",
        provider_description="TIDB.AI's local reranker server, deployed on your own infrastructure and powered by sentence-transformers.",
        default_reranker_model="BAAI/bge-reranker-v2-m3",
        reranker_model_description="Find more models in huggingface.",
        default_top_n=10,
        default_config={
            "api_url": "http://local-embedding-reranker:5001/api/v1/reranker",
        },
        config_description="api_url is the url of the tidb ai local reranker server.",
        credentials_display_name="Local Reranker API Key",
        credentials_description="Local Reranker server doesn't require an API key, set a dummy string here is ok.",
        credentials_type="str",
        default_credentials="dummy",
    ),
    RerankerProviderOption(
        provider=RerankerProvider.VLLM,
        provider_display_name="vLLM",
        provider_description="vLLM is a fast and easy-to-use library for LLM inference and serving.",
        default_reranker_model="BAAI/bge-reranker-v2-m3",
        reranker_model_description="Reference: https://docs.vllm.ai/en/latest/models/supported_models.html#sentence-pair-scoring-task-score",
        default_top_n=10,
        default_config={
            "base_url": "http://localhost:8000",
        },
        config_description="base_url is the base url of the vLLM server, ensure it can be accessed from this server",
        credentials_display_name="vLLM API Key",
        credentials_description="vLLM doesn't require an API key, set a dummy string here is ok",
        credentials_type="str",
        default_credentials="dummy",
    ),
    RerankerProviderOption(
        provider=RerankerProvider.XINFERENCE,
        provider_display_name="Xinference Reranker",
        provider_description="Xorbits Inference (Xinference) is an open-source platform to streamline the operation and integration of a wide array of AI models.",
        default_reranker_model="bge-reranker-v2-m3",
        reranker_model_description="Reference: https://inference.readthedocs.io/en/latest/models/model_abilities/rerank.html",
        default_top_n=10,
        default_config={
            "base_url": "http://localhost:9997",
        },
        config_description="base_url is the url of the Xinference server, ensure it can be accessed from this server",
        credentials_display_name="Xinference API Key",
        credentials_description="Xinference doesn't require an API key, set a dummy string here is ok",
        credentials_type="str",
        default_credentials="dummy",
    ),
    RerankerProviderOption(
        provider=RerankerProvider.BEDROCK,
        provider_display_name="Bedrock Reranker",
        provider_description="Amazon Bedrock is a fully managed foundation models service.",
        provider_url="https://docs.aws.amazon.com/bedrock/",
        default_reranker_model="amazon.rerank-v1:0",
        reranker_model_description="Find more models in https://docs.aws.amazon.com/bedrock/latest/userguide/foundation-models-reference.html.",
        default_top_n=10,
        credentials_display_name="AWS Bedrock Credentials JSON",
        credentials_description="The JSON Object of AWS Credentials, refer to https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html#cli-configure-files-global",
        credentials_type="dict",
        default_credentials={
            "aws_access_key_id": "****",
            "aws_secret_access_key": "****",
            "aws_region_name": "us-west-2",
        },
    ),
    RerankerProviderOption(
        provider=RerankerProvider.ZHIPU,
        provider_display_name="Zhipu BigModel",
        provider_description="Zhipu is a Chinese AI company, providing a large model platform.",
        default_reranker_model="rerank",
        reranker_model_description="Reference: https://docs.bigmodel.cn/api-reference/",
        default_config={
            "api_url": "https://open.bigmodel.cn/api/paas/v4/rerank",
        },
        default_top_n=10,
        credentials_display_name="Zhipu API Key",
        credentials_description="Zhipu requires an API key",
        credentials_type="str",
        default_credentials="*****",
    ),
    RerankerProviderOption(
        provider=RerankerProvider.CLOUD,
        provider_display_name="Cloud Compatible",
        provider_description=(
            "Generic cloud rerank provider for DashScope-style or Cohere-compatible APIs, "
            "e.g. Alibaba Bailian / Model Studio."
        ),
        provider_url="https://help.aliyun.com/zh/model-studio/text-rerank-api",
        default_reranker_model="gte-rerank-v2",
        reranker_model_description=(
            "Bailian DashScope models: gte-rerank-v2 / qwen3-vl-rerank (text). "
            "Replace {WorkspaceId} in api_url with your MaaS workspace id."
        ),
        default_config={
            "api_url": (
                "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com"
                "/api/v1/services/rerank/text-rerank/text-rerank"
            ),
            "api_style": "dashscope",
        },
        config_description=(
            "api_url: full Bailian MaaS rerank endpoint, e.g. "
            "https://llm-xxxx.cn-beijing.maas.aliyuncs.com"
            "/api/v1/services/rerank/text-rerank/text-rerank. "
            "api_style: 'dashscope' (default) sends {model,input,parameters}; "
            "'compatible' sends flat {model,query,documents,top_n} for "
            "/compatible-api/v1/reranks. Optional instruct for compatible style."
        ),
        default_top_n=10,
        credentials_display_name="Cloud API Key",
        credentials_description="API key of the cloud platform (e.g. Bailian DASHSCOPE_API_KEY)",
        credentials_type="str",
        default_credentials="*****",
    ),
]
