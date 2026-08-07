from typing import Any, List, Optional
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

DEFAULT_API_URL = "https://open.bigmodel.cn/api/paas/v4/rerank"
# 智谱单条 document 上限 4096 字符，单次最多 128 条
_MAX_DOCUMENT_CHARS = 4096
_MAX_DOCUMENTS = 128


class ZhipuRerank(BaseNodePostprocessor):
    """智谱 BigModel 文本重排序（``POST /paas/v4/rerank``）。"""

    api_key: str = Field(default="", description="Zhipu API key.")
    api_url: str = Field(default=DEFAULT_API_URL, description="Zhipu rerank API url.")
    model: str = Field(
        default="rerank",
        description="The model to use when calling Zhipu rerank API",
    )
    top_n: int = Field(description="Top N nodes to return.")

    _session: Any = PrivateAttr()

    def __init__(
        self,
        top_n: int = 10,
        model: str = "rerank",
        api_key: str = "",
        api_url: str = DEFAULT_API_URL,
    ):
        super().__init__(top_n=top_n, model=model)
        self.api_key = api_key
        self.api_url = api_url
        self.model = model
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        )

    @classmethod
    def class_name(cls) -> str:
        return "ZhipuRerank"

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
            # API 限制：最多 128 条；超出时先截断再排序
            candidate_nodes = nodes[:_MAX_DOCUMENTS]
            texts = [
                node.node.get_content(metadata_mode=MetadataMode.EMBED)[
                    :_MAX_DOCUMENT_CHARS
                ]
                for node in candidate_nodes
            ]
            payload = {
                "model": self.model,
                "query": query_bundle.query_str,
                "documents": texts,
                "return_documents": False,
            }
            if self.top_n and self.top_n > 0:
                payload["top_n"] = self.top_n

            resp = self._session.post(self.api_url, json=payload)
            try:
                resp.raise_for_status()
            except requests.HTTPError as e:
                raise RuntimeError(
                    f"Zhipu rerank HTTP error: {resp.status_code} {resp.text}"
                ) from e

            resp_json = resp.json()
            results = resp_json.get("results")
            if not isinstance(results, list):
                raise RuntimeError(f"Got error from Zhipu reranker: {resp_json}")

            new_nodes: List[NodeWithScore] = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                score = item.get("relevance_score")
                if index is None or score is None:
                    continue
                if not isinstance(index, int) or index < 0 or index >= len(candidate_nodes):
                    continue
                new_nodes.append(
                    NodeWithScore(node=candidate_nodes[index].node, score=float(score))
                )

            if self.top_n and self.top_n > 0:
                new_nodes = new_nodes[: self.top_n]

            event.on_end(payload={EventPayload.NODES: new_nodes})

        dispatcher.event(ReRankEndEvent(nodes=new_nodes))
        return new_nodes
