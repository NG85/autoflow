import enum

from typing import List
from pydantic import BaseModel


class LLMProvider(str, enum.Enum):
    OPENAI = "openai"
    GEMINI = "gemini"
    VERTEX_AI = "vertex_ai"
    ANTHROPIC_VERTEX = "anthropic_vertex"  # Deprecated, use VERTEX_AI instead
    OPENAI_LIKE = "openai_like"
    BEDROCK = "bedrock"
    OLLAMA = "ollama"
    GITEEAI = "giteeai"
    AZURE_OPENAI = "azure_openai"


class LLMProviderOption(BaseModel):
    provider: LLMProvider
    provider_display_name: str | None = None
    provider_description: str | None = None
    provider_url: str | None = None
    default_llm_model: str
    llm_model_description: str
    default_config: dict = {}
    config_description: str = ""
    default_credentials: str | dict = ""
    credentials_display_name: str
    credentials_description: str
    credentials_type: str = "str"


llm_provider_options: List[LLMProviderOption] = [
    LLMProviderOption(
        provider=LLMProvider.OPENAI,
        provider_display_name="OpenAI",
        provider_description="The OpenAI API provides a simple interface for developers to create an intelligence layer in their applications, powered by OpenAI's state of the art models.",
        provider_url="https://platform.openai.com",
        default_llm_model="gpt-4o",
        llm_model_description="",
        credentials_display_name="OpenAI API Key",
        credentials_description="The API key of OpenAI, you can find it in https://platform.openai.com/api-keys",
        credentials_type="str",
        default_credentials="sk-****",
    ),
    LLMProviderOption(
        provider=LLMProvider.OPENAI_LIKE,
        provider_display_name="OpenAI Like",
        default_llm_model="",
        llm_model_description="",
        default_config={
            "api_base": "https://openrouter.ai/api/v1/",
            "is_chat_model": True,
        },
        config_description=(
            "`api_base` is the API base URL of the third-party OpenAI-like service, such as OpenRouter; "
            "`is_chat_model` indicates whether the model is chat model; "
            "`context_window` is the maximum number of input tokens and output tokens; "
        ),
        credentials_display_name="API Key",
        credentials_description="The API key of the third-party OpenAI-like service, such as OpenRouter, you can find it in their official website",
        credentials_type="str",
        default_credentials="sk-****",
    ),
    LLMProviderOption(
        provider=LLMProvider.GEMINI,
        provider_display_name="Gemini",
        provider_description="The Gemini API and Google AI Studio help you start working with Google's latest models. Access the whole Gemini model family and turn your ideas into real applications that scale.",
        provider_url="https://ai.google.dev/gemini-api",
        default_llm_model="models/gemini-2.0-flash",
        llm_model_description="Find the model code at https://ai.google.dev/gemini-api/docs/models/gemini",
        credentials_display_name="Google API Key",
        credentials_description="The API key of Google AI Studio, you can find it in https://aistudio.google.com/app/apikey",
        credentials_type="str",
        default_credentials="AIza****",
    ),
    LLMProviderOption(
        provider=LLMProvider.VERTEX_AI,
        provider_display_name="Vertex AI",
        provider_description="Vertex AI is a fully-managed, unified AI development platform for building and using generative AI.",
        provider_url="https://cloud.google.com/vertex-ai",
        default_llm_model="gemini-1.5-flash",
        llm_model_description="Find more in https://cloud.google.com/model-garden",
        credentials_display_name="Google Credentials JSON",
        credentials_description="The JSON Object of Google Credentials, refer to https://cloud.google.com/docs/authentication/provide-credentials-adc#on-prem",
        credentials_type="dict",
        default_credentials={
            "type": "service_account",
            "project_id": "****",
            "private_key_id": "****",
        },
    ),
    LLMProviderOption(
        provider=LLMProvider.OLLAMA,
        provider_display_name="Ollama",
        provider_description="Ollama is a lightweight framework for building and running large language models.",
        provider_url="https://ollama.com",
        default_llm_model="llama3.2",
        llm_model_description="Find more in https://ollama.com/library",
        default_config={
            "base_url": "http://localhost:11434",
            "context_window": 8192,
            "request_timeout": 60 * 10,
        },
        config_description=(
            "`base_url` is the base URL of the Ollama server, ensure it can be accessed from this server; "
            "`context_window` is the maximum number of input tokens and output tokens; "
            "`request_timeout` is the maximum time to wait for a generate response."
        ),
        credentials_display_name="Ollama API Key",
        credentials_description="Ollama doesn't require an API key, set a dummy string here is ok",
        credentials_type="str",
        default_credentials="dummy",
    ),
    LLMProviderOption(
        provider=LLMProvider.GITEEAI,
        provider_display_name="Gitee AI",
        provider_description="Gitee AI is a third-party model provider that offers ready-to-use cutting-edge model APIs for AI developers.",
        provider_url="https://ai.gitee.com",
        default_llm_model="Qwen2.5-72B-Instruct",
        default_config={
            "is_chat_model": True,
            "context_window": 131072,
        },
        config_description=(
            "`is_chat_model` indicates whether the model is chat model; "
            "`context_window` is the maximum number of input tokens and output tokens; "
        ),
        llm_model_description="Find more in https://ai.gitee.com/serverless-api",
        credentials_display_name="Gitee AI API Key",
        credentials_description="The API key of Gitee AI, you can find it in https://ai.gitee.com/dashboard/settings/tokens",
        credentials_type="str",
        default_credentials="****",
    ),
    LLMProviderOption(
        provider=LLMProvider.BEDROCK,
        provider_display_name="Bedrock",
        provider_description="Amazon Bedrock is a fully managed foundation models service.",
        provider_url="https://docs.aws.amazon.com/bedrock/",
        default_llm_model="anthropic.claude-3-7-sonnet-20250219-v1:0",
        llm_model_description="",
        credentials_display_name="AWS Bedrock Credentials JSON",
        credentials_description="The JSON Object of AWS Credentials, refer to https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html#cli-configure-files-global",
        credentials_type="dict",
        default_credentials={
            "aws_access_key_id": "****",
            "aws_secret_access_key": "****",
            "aws_region_name": "us-west-2",
        },
    ),
    LLMProviderOption(
        provider=LLMProvider.AZURE_OPENAI,
        provider_display_name="Azure OpenAI",
        provider_description="Azure OpenAI is a cloud-based AI service that provides access to OpenAI's advanced language models.",
        provider_url="https://azure.microsoft.com/en-us/products/ai-services/openai-service",
        default_llm_model="gpt-4o",
        llm_model_description="",
        config_description="Refer to this document https://learn.microsoft.com/en-us/azure/ai-services/openai/quickstart to have more information about the Azure OpenAI API.",
        default_config={
            "azure_endpoint": "https://<your-resource-name>.openai.azure.com/",
            "api_version": "<your-api-version>",
            "engine": "<your-deployment-name>",
        },
        credentials_display_name="Azure OpenAI API Key",
        credentials_description="The API key of Azure OpenAI",
        credentials_type="str",
        default_credentials="****",
    ),
]
