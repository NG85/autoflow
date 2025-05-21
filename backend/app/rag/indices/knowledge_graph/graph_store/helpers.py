import json
from typing import List, Tuple, Mapping, Any

from llama_index.embeddings.openai import OpenAIEmbedding, OpenAIEmbeddingModelType
from llama_index.core.base.embeddings.base import BaseEmbedding, Embedding
from sqlalchemy import and_, or_

# The configuration for the weight coefficient
# format: ((min_weight, max_weight), coefficient)
DEFAULT_WEIGHT_COEFFICIENT_CONFIG = [
    ((0, 100), 0.01),
    ((100, 1000), 0.001),
    ((1000, 10000), 0.0001),
    ((10000, float("inf")), 0.00001),
]

# The configuration for the range search
# format: ((min_distance, max_distance), seach_ratio)
# The sum of search ratio should be 1 except some case we want to search as many as possible relationships.
# In this case, we set the search ratio to 1, and the other search ratio sum should be 1
DEFAULT_RANGE_SEARCH_CONFIG = [
    ((0.0, 0.25), 1),
    ((0.25, 0.35), 0.7),
    ((0.35, 0.45), 0.2),
    ((0.45, 0.55), 0.1),
]

DEFAULT_DEGREE_COEFFICIENT = 0.001


def get_weight_score(
    weight: int, weight_coefficient_config: List[Tuple[Tuple[int, int], float]]
) -> float:
    weight_score = 0.0
    remaining_weight = weight

    for weight_range, coefficient in weight_coefficient_config:
        if remaining_weight <= 0:
            break
        lower_bound, upper_bound = weight_range
        applicable_weight = min(upper_bound - lower_bound, remaining_weight)
        weight_score += applicable_weight * coefficient
        remaining_weight -= applicable_weight

    return weight_score


def get_degree_score(in_degree: int, out_degree: int, degree_coefficient) -> float:
    return (in_degree - out_degree) * degree_coefficient


def calculate_relationship_score(
    embedding_distance: float,
    weight: int,
    in_degree: int,
    out_degree: int,
    alpha: float,
    weight_coefficient_config: List[
        Tuple[Tuple[int, int], float]
    ] = DEFAULT_WEIGHT_COEFFICIENT_CONFIG,
    degree_coefficient: float = DEFAULT_DEGREE_COEFFICIENT,
    with_degree: bool = False,
) -> float:
    weighted_score = get_weight_score(weight, weight_coefficient_config)
    degree_score = 0
    if with_degree:
        degree_score = get_degree_score(in_degree, out_degree, degree_coefficient)
    return alpha * (1 / embedding_distance) + weighted_score + degree_score


def get_default_embed_model() -> BaseEmbedding:
    return OpenAIEmbedding(model=OpenAIEmbeddingModelType.TEXT_EMBED_3_SMALL)


def get_query_embedding(query: str, embed_model: BaseEmbedding = None) -> Embedding:
    if not embed_model:
        embed_model = get_default_embed_model()
    return embed_model.get_query_embedding(query)


def get_text_embedding(text: str, embed_model: BaseEmbedding = None) -> Embedding:
    if not embed_model:
        embed_model = get_default_embed_model()
    return embed_model.get_text_embedding(text)


def get_entity_description_embedding(
    name: str, description: str, embed_model: BaseEmbedding = None
) -> Embedding:
    combined_text = f"{name}: {description}"
    return get_text_embedding(combined_text, embed_model)


def get_entity_metadata_embedding(
    metadata: Mapping[str, Any], embed_model: BaseEmbedding = None
) -> Embedding:
    combined_text = json.dumps(metadata, ensure_ascii=False)
    return get_text_embedding(combined_text, embed_model)


def get_relationship_description_embedding(
    source_entity_name: str,
    source_entity_description,
    target_entity_name: str,
    target_entity_description: str,
    relationship_desc: str,
    embed_model: BaseEmbedding = None,
):
    combined_text = (
        f"{source_entity_name}({source_entity_description}) -> "
        f"{relationship_desc} -> {target_entity_name}({target_entity_description}) "
    )
    return get_text_embedding(combined_text, embed_model)


def parse_mongo_style_filter(filters):
    """
    Parse MongoDB-style filters into SQLAlchemy conditions.
    
    Args:
        filters: A dictionary containing MongoDB-style filter conditions.
                Can include $or and $and for compound conditions.
    
    Returns:
        A list of tuples (field, operator, value) for simple conditions,
        or a tuple (operator, conditions) for compound conditions.
    """
    if filters is None:
        return []

    if not isinstance(filters, dict):
        return []
    
    conditions = []
    
    # Handle special operators $or and $and
    if "$or" in filters:
        or_conditions = []
        for condition in filters["$or"]:
            or_conditions.append(parse_mongo_style_filter(condition))
        return ("$or", or_conditions)
    
    if "$and" in filters:
        and_conditions = []
        for condition in filters["$and"]:
            and_conditions.append(parse_mongo_style_filter(condition))
        return ("$and", and_conditions)
    
    # Handle regular field conditions
    for field, condition in filters.items():
        if isinstance(condition, dict):
            for op, value in condition.items():
                conditions.append((field, op, value))
        else:
            conditions.append((field, "$eq", condition))
    
    return conditions

def _get_filter_condition(json_field, op, value):
    """
    Helper method to create SQLAlchemy filter conditions based on MongoDB-style operators.
    
    Args:
        json_field: The JSON field to filter on
        op: The operator to apply
        value: The value to compare against
        
    Returns:
        SQLAlchemy filter condition
    """
    if op == "$eq":
        return json_field == value
    elif op == "$ne":
        return json_field != value
    elif op == "$in":
        return json_field.in_(value)
    elif op == "$nin":
        return json_field.notin_(value)
    elif op == "$gt":
        return json_field > value
    elif op == "$gte":
        return json_field >= value
    elif op == "$lt":
        return json_field < value
    elif op == "$lte":
        return json_field <= value
    # TODO: support more operations
    
    return None


def apply_filter_condition(model_alias, condition):
    """
    Apply filter conditions recursively for nested compound conditions.
    
    Args:
        model_alias: The SQLAlchemy model alias to apply conditions to
        condition: The condition to apply, can be a tuple (operator, conditions) for compound conditions
                    or a list of (field, operator, value) tuples for simple conditions
    
    Returns:
        SQLAlchemy filter condition
    """
    if isinstance(condition, tuple) and condition[0] in ["$or", "$and"]:
        operator, conditions = condition
        if operator == "$or":
            # Handle OR conditions
            or_conditions = []
            for sub_condition in conditions:
                if isinstance(sub_condition, tuple) and sub_condition[0] in ["$or", "$and"]:
                    # Recursively handle nested compound conditions
                    nested_condition = apply_filter_condition(model_alias, sub_condition)
                    or_conditions.append(nested_condition)
                else:
                    # Handle simple conditions
                    for field, op, value in sub_condition:
                        json_field = model_alias.meta[field]
                        or_conditions.append(_get_filter_condition(json_field, op, value))
            
            if or_conditions:
                return or_(*or_conditions)
        elif operator == "$and":
            # Handle AND conditions
            and_conditions = []
            for sub_condition in conditions:
                if isinstance(sub_condition, tuple) and sub_condition[0] in ["$or", "$and"]:
                    # Recursively handle nested compound conditions
                    nested_condition = apply_filter_condition(model_alias, sub_condition)
                    and_conditions.append(nested_condition)
                else:
                    # Handle simple conditions
                    for field, op, value in sub_condition:
                        json_field = model_alias.meta[field]
                        and_conditions.append(_get_filter_condition(json_field, op, value))
            
            if and_conditions:
                return and_(*and_conditions)
    else:
        # Handle simple conditions
        conditions = []
        for field, op, value in condition:
            json_field = model_alias.meta[field]
            conditions.append(_get_filter_condition(json_field, op, value))
        
        if conditions:
            return and_(*conditions)
    
    return None