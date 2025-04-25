import logging

from typing import Dict, List, Optional, Any, Union
from llama_index.core import QueryBundle
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import BaseNode, NodeWithScore
from llama_index.core.vector_stores.types import (
    MetadataFilter,
    MetadataFilters,
    FilterOperator,
    FilterCondition,
)


SimpleMetadataFilter = Dict[str, Any]


def simple_filter_to_metadata_filters(filters: SimpleMetadataFilter) -> MetadataFilters:
    simple_filters = []
    for key, value in filters.items():
        simple_filters.append(
            MetadataFilter(
                key=key,
                value=value,
                operator=FilterOperator.EQ,
            )
        )
    return MetadataFilters(filters=simple_filters)


logger = logging.getLogger(__name__)


class MetadataPostFilter(BaseNodePostprocessor):
    filters: Optional[MetadataFilters] = None

    def __init__(
        self,
        filters: Optional[Union[MetadataFilters, SimpleMetadataFilter]] = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        if isinstance(filters, MetadataFilters):
            self.filters = filters
        else:
            self.filters = simple_filter_to_metadata_filters(filters)

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        if self.filters is None:
            return nodes

        filtered_nodes = []
        for node in nodes:
            if self.match_all_filters(node.node):
                filtered_nodes.append(node)
        return filtered_nodes

    def match_all_filters(self, node: BaseNode) -> bool:
        logger.debug(f"Process chunk with match_all_filters: {self.filters}")
        if self.filters is None or not isinstance(self.filters, MetadataFilters):
            return True

        return self._evaluate_filters(node, self.filters)

    def _evaluate_filters(self, node: BaseNode, filters: MetadataFilters) -> bool:
        """Recursively evaluate filter conditions, supporting compound conditions (AND, OR) and multiple operators"""
        if not filters.filters:
            return True

        # Evaluate all sub-filter conditions based on the condition type (AND/OR)
        results = []
        for f in filters.filters:
            if isinstance(f, MetadataFilters):
                # Recursively process nested compound conditions
                result = self._evaluate_filters(node, f)
            else:
                # Process single filter conditions
                result = self._evaluate_single_filter(node, f)
            results.append(result)

        # Return results based on the condition type
        if filters.condition == FilterCondition.OR:
            return any(results)
        else:  # Default to AND
            return all(results)

    def _evaluate_single_filter(self, node: BaseNode, filter: MetadataFilter) -> bool:
        """Evaluate a single filter condition, supporting multiple operators"""
        if filter.key not in node.metadata:
            return False

        value = node.metadata[filter.key]
        filter_value = filter.value

        logger.debug(f"Evaluating filter: {filter.key} {filter.operator} {filter_value} against {value}")
        # Compare based on the operator type
        if filter.operator == FilterOperator.EQ:
            return value == filter_value
        elif filter.operator == FilterOperator.NE:
            return value != filter_value
        elif filter.operator == FilterOperator.GT:
            return value > filter_value
        elif filter.operator == FilterOperator.GTE:
            return value >= filter_value
        elif filter.operator == FilterOperator.LT:
            return value < filter_value
        elif filter.operator == FilterOperator.LTE:
            return value <= filter_value
        elif filter.operator == FilterOperator.IN:
            return value in filter_value
        elif filter.operator == FilterOperator.NIN:
            return value not in filter_value
        elif filter.operator == FilterOperator.CONTAINS:
            return filter_value in value
        elif filter.operator == FilterOperator.IS_EMPTY:
            return value is None or value == "" or (isinstance(value, list) and len(value) == 0)
        elif filter.operator == FilterOperator.TEXT_MATCH:
            return str(filter_value) in str(value)
        elif filter.operator == FilterOperator.TEXT_MATCH_INSENSITIVE:
            return str(filter_value).lower() in str(value).lower()
        elif filter.operator == FilterOperator.ANY:
            return any(item in value for item in filter_value)
        elif filter.operator == FilterOperator.ALL:
            return all(item in value for item in filter_value)
        else:
            # Default to the equal operator
            return value == filter_value

