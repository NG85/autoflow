from typing import Any, List, Literal, Optional
import requests

from llama_index.core.bridge.pydantic import Field, PrivateAttr
from llama_index.core.callbacks import CBEventType, EventPayload
from llama_index.core.instrumentation import get_dispatcher
from llama_index.core.instrumentation.events.rerank import (
    ReRankEndEvent,
    ReRankStartEvent,
)
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import MetadataMode, NodeWithScore, QueryBundle

dispatcher = get_dispatcher(__name__)

# 默认按阿里云百炼 MaaS DashScope 原生接口（替换 WorkspaceId）
DEFAULT_API_URL = (
    "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com"
    "/api/v1/services/rerank/text-rerank/text-rerank"
)
ApiStyle = Literal["compatible", "dashscope"]


class CloudRerank(BaseNodePostprocessor):
    """通用云平台 Rerank，兼容 Cohere-like / DashScope 两类协议。

    - ``dashscope``（默认）：``{model, input:{query,documents}, parameters}``，
      对应百炼示例
      ``/api/v1/services/rerank/text-rerank/text-rerank``
      （如 ``gte-rerank-v2`` / ``qwen3-vl-rerank`` 文本调用）。
    - ``compatible``：扁平 ``{model, query, documents, top_n}``，
      适用于 ``/compatible-api/v1/reranks``（如 ``qwen3-rerank``）。
    """

    api_key: str = Field(default="", description="Cloud platform API key.")
    api_url: str = Field(default=DEFAULT_API_URL, description="Rerank API url.")
    api_style: str = Field(
        default="dashscope",
        description="Request/response style: compatible | dashscope",
    )
    model: str = Field(
        default="gte-rerank-v2",
        description="The model to use when calling cloud rerank API",
    )
    instruct: Optional[str] = Field(
        default=None,
        description="Optional instruct for compatible/qwen3-rerank style APIs",
    )
    top_n: int = Field(description="Top N nodes to return.")

    _session: Any = PrivateAttr()

    def __init__(
        self,
        top_n: int = 10,
        model: str = "gte-rerank-v2",
        api_key: str = "",
        api_url: str = DEFAULT_API_URL,
        api_style: ApiStyle = "dashscope",
        instruct: Optional[str] = None,
    ):
        super().__init__(top_n=top_n, model=model)
        style = (api_style or "dashscope").strip().lower()
        if style not in ("compatible", "dashscope"):
            raise ValueError(
                f"Unsupported api_style={api_style!r}; expected 'compatible' or 'dashscope'"
            )
        self.api_key = api_key
        self.api_url = api_url
        self.api_style = style
        self.model = model
        self.instruct = instruct
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        )

    @classmethod
    def class_name(cls) -> str:
        return "CloudRerank"

    def _build_payload(self, query: str, documents: List[str]) -> dict:
        if self.api_style == "compatible":
            payload: dict[str, Any] = {
                "model": self.model,
                "query": query,
                "documents": documents,
            }
            if self.top_n and self.top_n > 0:
                payload["top_n"] = self.top_n
            if self.instruct:
                payload["instruct"] = self.instruct
            return payload

        parameters: dict[str, Any] = {"return_documents": False}
        if self.top_n and self.top_n > 0:
            parameters["top_n"] = self.top_n
        return {
            "model": self.model,
            "input": {
                "query": query,
                "documents": documents,
            },
            "parameters": parameters,
        }

    @staticmethod
    def _extract_results(resp_json: dict) -> List[dict]:
        # compatible / Cohere-like: results at top level
        results = resp_json.get("results")
        if isinstance(results, list):
            return results
        # dashscope native: output.results
        output = resp_json.get("output")
        if isinstance(output, dict):
            nested = output.get("results")
            if isinstance(nested, list):
                return nested
        return []

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        dispatcher.event(
            ReRankStartEvent(
                query=query_bundle,
                nodes=nodes,
                top_n=self.top_n,
                model_name=self.model,
            )
        )

        if query_bundle is None:
            raise ValueError("Missing query bundle in extra info.")
        if len(nodes) == 0:
            return []

        with self.callback_manager.event(
            CBEventType.RERANKING,
            payload={
                EventPayload.NODES: nodes,
                EventPayload.MODEL_NAME: self.model,
                EventPayload.QUERY_STR: query_bundle.query_str,
                EventPayload.TOP_K: self.top_n,
            },
        ) as event:
            texts = [
                node.node.get_content(metadata_mode=MetadataMode.EMBED)
                for node in nodes
            ]
            payload = self._build_payload(query_bundle.query_str, texts)
            resp = self._session.post(self.api_url, json=payload)
            try:
                resp.raise_for_status()
            except requests.HTTPError as e:
                raise RuntimeError(
                    f"Cloud rerank HTTP error: {resp.status_code} {resp.text}"
                ) from e

            resp_json = resp.json()
            if resp_json.get("code") and resp_json.get("message"):
                # DashScope 失败体常带 code/message，且无 results
                raise RuntimeError(f"Got error from cloud reranker: {resp_json}")

            results = self._extract_results(resp_json)
            if not results:
                raise RuntimeError(f"Got error from cloud reranker: {resp_json}")

            new_nodes: List[NodeWithScore] = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                score = item.get("relevance_score")
                if index is None or score is None:
                    continue
                if not isinstance(index, int) or index < 0 or index >= len(nodes):
                    continue
                new_nodes.append(
                    NodeWithScore(node=nodes[index].node, score=float(score))
                )

            if self.top_n and self.top_n > 0:
                new_nodes = new_nodes[: self.top_n]

            event.on_end(payload={EventPayload.NODES: new_nodes})

        dispatcher.event(ReRankEndEvent(nodes=new_nodes))
        return new_nodes
