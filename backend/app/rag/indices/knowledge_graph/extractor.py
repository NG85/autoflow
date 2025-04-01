import logging
from copy import deepcopy
import pandas as pd
import dspy
from dspy.functional import TypedPredictor
from typing import Mapping, Optional, List
from llama_index.core.schema import BaseNode

from app.rag.indices.knowledge_graph.schema import (
    Entity,
    Relationship,
    KnowledgeGraph,
    EntityCovariateInput,
    EntityCovariateOutput,
)
from app.models.enums import GraphType
from app.rag.indices.knowledge_graph.extract_template import (
    EXTRACTION_TEMPLATE,
    COVARIATE_TEMPLATE,
)

logger = logging.getLogger(__name__)


class ExtractGraphTriplet(dspy.Signature):
    """Carefully analyze the provided text from insurance documentation and related materials to thoroughly identify all entities related to insurance business, including both general concepts and specific details.
    
    # 核心提取原则
    1. 保险要素优先：
       - 识别保险五要素：投保人、被保险人、保险标的、保险责任、保险期间
       - 发现三金关系：保费、保额、现金价值
       - 捕捉两期：犹豫期、等待期
    
    2. 灵活文档处理：
       a. 对已知文档类型应用预设规则（见下文）
       b. 对未知类型执行：
          - 识别文档功能属性（产品设计/销售支持/合规管理）
          - 提取功能相关实体（如营销材料提取卖点话术与条款对应关系）
          - 建立跨文档知识关联
    
    # 保险文档智能分类矩阵
    | 文档特征                | 处理策略                          | 示例实体                     |
    |-------------------------|-----------------------------------|-----------------------------|
    | 含产品代码+条款版本      | 按产品手册处理                    | 产品代码、条款条目           |
    | 出现费率表+年龄梯度      | 按精算资料处理                    | 基准费率、核保系数           |
    | 包含医院列表+等级        | 按医疗服务网络处理                | 医疗机构编码、服务范围       |
    | 涉及话术+案例           | 按销售支持材料处理                | 异议处理模板、客户案例       |
    | 含监管文号+合规要求      | 按合规文件处理                    | 监管文件编号、生效日期       |
    
    # 通用保险关系模板
    1. 产品结构关系：
       - (主险)-[组合]->(附加险)
       - (产品)-[版本迭代]->(历史版本)
    
    2. 精算关系：
       - (年龄区间)-[对应]->(费率系数)
       - (职业类别)-[影响]->(风险评级)
    
    3. 服务关系：
       - (理赔类型)-[需要]->(材料清单)
       - (增值服务)-[覆盖]->(医疗机构)
    
    4. 监管关系：
       - (产品备案)-[依据]->(监管文件)
       - (条款描述)-[受限]->(合规要求)
    
    # 弹性处理机制
    1. 新实体发现：
       - 若遇未识别实体类型，根据上下文推断分类
         - 示例：在再保合约中识别"分保比例"为【再保险参数】
    
    2. 跨文档推理：
       - 通过产品代码关联分散在不同文档中的信息
         - 示例：投保单中的HL-XGXS-2023-V2自动关联产品手册
    
    3. 模糊匹配：
       - 对非标表述智能归一化
         - 将"3甲医院"→"三级甲等医院"
         - "保监[2023]8号"→"银保监金规[2023]8号文"
    
    # 质量保障措施
    1. 数据校验：
       - 产品代码校验：校验版本号连续性（V2必须在V1之后）
       - 时间逻辑校验：生效日期早于失效日期
       - 地域代码校验：符合GB/T 2260标准
    
    2. 冲突解决：
       - 同一实体多来源时，按文档优先级处理：
         条款文件 > 费率表 > 产品手册 > 培训材料
    
    3. 溯源机制：
       - 记录实体来源文档的元信息：
         - 文件名、页码、更新时间
    
    # 典型复杂案例处理
    案例：某互联网保险平台的FAQ文档
    原文：
    "Q：安康医疗2024版（HL-AK2024）的智能核保是否支持甲状腺结节？
    A：根据2024年核保手册，TI-RADS 3类以下且直径＜1cm可承保"
    
    提取结果：
    Entities:
      - 产品: HL-AK2024
      - 疾病: 甲状腺结节
      - 核保标准: TI-RADS 3类/直径<1cm
    Relationships:
      - (HL-AK2024)-[支持核保]->(甲状腺结节)
      - (甲状腺结节)-[需满足]->(TI-RADS 3类/直径<1cm)
    Metadata:
      - HL-AK2024:
          核保类型: "智能核保"
          依据文件: "2024核保手册"
    
    # 输出规范
    - 一定要返回json格式
    - 严格遵循保险行业数据标准
    - 未知实体类型用【通用保险概念】标签
    - 保持原始文档上下文关联性
    
    """

    text = dspy.InputField(
        desc="a paragraph of text to extract entities and relationships to form a knowledge graph"
    )
    knowledge: KnowledgeGraph = dspy.OutputField(
        desc="Graph representation of the knowledge extracted from the text."
    )


class ExtractCovariate(dspy.Signature):
    """Please carefully review the provided text and insurance-related entities list which are already identified in the text. Focusing on identifying detailed covariates associated with each insurance entity provided.
    
    # 核心结构要求
    1. 每个实体的首个字段必须为topic，取值如下：
       - 保险产品
       - 保险条款
       - 费率规则
       - 医疗机构
       - 监管文件
       - 通用概念（默认）

    2. 层级结构：
       {
         "entity_name": {
           "topic": "保险产品",  // 必须首位
           "insurance_attributes": {  // 保险专用字段集
             "product_code": "HL-XGXS-2023-V2",
             "clause_version": "CI-2024-008",
             "effective_date": "2024-03-01"
           },
           "system_metadata": {  // 系统管理字段
             "confidence": 0.97,
             "source_doc": "2024产品手册.pdf"
           }
         }
       }

    # 保险专用字段规则
    1. 产品类实体（topic=保险产品）：
       - 必填字段：product_code, clause_version
       - 选填字段：sales_region, premium_table

    2. 条款类实体（topic=保险条款）：
       - 必填字段：clause_number, related_product
       - 示例："clause_number": "第5.2条"

    3. 医疗机构（topic=医疗机构）：
       - 必填字段：hospital_code, grade
       - 编码标准：卫健委22位编码

    # 示例
    输入文本：
    "欣享一生（HL-XGXS-2023-V2）2023年3月1日生效，匹配2024版费率表"
    
    提取结果：
    {
      "HL-XGXS-2023-V2": {
        "topic": "保险产品",
        "insurance_attributes": {
          "product_code": "HL-XGXS-2023-V2",
          "effective_date": "2023-03-01",
          "rate_table_version": "2024"
        },
        "system_metadata": {
          "confidence": 0.96,
          "source_context": "产品手册第5页"
        }
      }
    }

    # 校验规则
    1. 产品代码校验：
       - 符合[类型代码]-[产品缩写]-[年份]-V[版本]格式
       - 示例：HL-XGXS-2023-V2

    2. 日期格式：
       - 严格遵循YYYY-MM-DD
       - 时间相关字段后缀需带_unit，如"waiting_period_unit": "天"

    3. 版本追溯：
       - 使用@符号表示关联，如"rate_table_version": "2024@HL-XGXS-2023-V2"
       
    Please only response in JSON format.
    """

    text = dspy.InputField(
        desc="a paragraph of text to extract covariates to claim the entities."
    )

    entities: List[EntityCovariateInput] = dspy.InputField(
        desc="List of entities identified in the text."
    )
    covariates: List[EntityCovariateOutput] = dspy.OutputField(
        desc="Graph representation of the knowledge extracted from the text."
    )


def get_relation_metadata_from_node(node: BaseNode):
    metadata = deepcopy(node.metadata)
    for key in [
        "_node_content",
        "_node_type",
        "excerpt_keywords",
        "questions_this_excerpt_can_answer",
        "section_summary",
    ]:
        metadata.pop(key, None)
    metadata["chunk_id"] = node.node_id
    return metadata


class Extractor(dspy.Module):
    def __init__(self, dspy_lm: dspy.LM):
        super().__init__()
        self.dspy_lm = dspy_lm
        with dspy.settings.context(instructions=EXTRACTION_TEMPLATE):
            self.prog_graph = TypedPredictor(ExtractGraphTriplet)

        with dspy.settings.context(instructions=COVARIATE_TEMPLATE):
            self.prog_covariates = TypedPredictor(ExtractCovariate)
    

    def get_llm_output_config(self):
        if "openai" in self.dspy_lm.provider.lower():
            return {
                "response_format": {"type": "json_object"},
            }
        elif "ollama" in self.dspy_lm.provider.lower():
            # ollama support set format=json in the top-level request config, but not in the request's option
            # https://github.com/ollama/ollama/blob/5e2653f9fe454e948a8d48e3c15c21830c1ac26b/api/types.go#L70
            return {}
        elif "bedrock" in self.dspy_lm.provider.lower():
            # Fix: add bedrock branch to fix 'Malformed input request' error
            # subject must not be valid against schema {"required":["messages"]}: extraneous key [response_mime_type] is not permitted
            return {"max_tokens": 8192}
        else:
            return {
                "response_mime_type": "application/json",
            }

    def forward(self, text):
        with dspy.settings.context(lm=self.dspy_lm):
            pred_graph = self.prog_graph(
                text=text,
                config=self.get_llm_output_config(),
            )
                    
            logger.debug(f"Debug: Predicted graph output: {pred_graph}")
            # extract the covariates
            entities_for_covariates = [
                EntityCovariateInput(
                    name=entity.name,
                    description=entity.description,
                )
                for entity in pred_graph.knowledge.entities
            ]

            try:
                pred_covariates = self.prog_covariates(
                    text=text,
                    entities=entities_for_covariates,
                    config=self.get_llm_output_config(),
                )
                logger.debug((f"Debug: prog_covariates output before JSON parsing: {pred_covariates}"))
            except Exception as e:
                logger.error(f"Error in prog_covariates: {e}")
                raise e

            # replace the entities with the covariates
            for entity in pred_graph.knowledge.entities:
                for covariate in pred_covariates.covariates:
                    if entity.name == covariate.name:
                        entity.metadata = covariate.covariates

            return pred_graph


class SimpleGraphExtractor:
    def __init__(
        self, dspy_lm: dspy.LM, complied_extract_program_path: Optional[str] = None, graph_type: GraphType = GraphType.general
    ):
        self.graph_type = graph_type
        self.extract_prog = Extractor(dspy_lm=dspy_lm)
        if complied_extract_program_path is not None:
            self.extract_prog.load(complied_extract_program_path)

    def extract(self, text: str, node: BaseNode):
        logger.info(f"Extracting {self.graph_type} knowledge graph from text")
        pred = self.extract_prog(text=text)
        logger.info(f"pred output: {pred}")
        metadata = get_relation_metadata_from_node(node)
        return self._to_df(
            pred.knowledge.entities, pred.knowledge.relationships, metadata
        )

    def _to_df(
        self,
        entities: list[Entity],
        relationships: list[Relationship],
        extra_meta: Mapping[str, str],
    ):
        # Create lists to store dictionaries for entities and relationships
        entities_data = []
        relationships_data = []

        # Iterate over parsed entities and relationships to create dictionaries
        for entity in entities:
            entity_dict = {
                "name": entity.name,
                "description": entity.description,
                "meta": entity.metadata,
            }
            entities_data.append(entity_dict)

        mapped_entities = {entity["name"]: entity for entity in entities_data}

        for relationship in relationships:
            source_entity_description = ""
            if relationship.source_entity not in mapped_entities:
                new_source_entity = {
                    "name": relationship.source_entity,
                    "description": (
                        f"Derived from from relationship: "
                        f"{relationship.source_entity} -> {relationship.relationship_desc} -> {relationship.target_entity}"
                    ),
                    "meta": {"status": "need-revised"},
                }
                entities_data.append(new_source_entity)
                mapped_entities[relationship.source_entity] = new_source_entity
                source_entity_description = new_source_entity["description"]
            else:
                source_entity_description = mapped_entities[relationship.source_entity][
                    "description"
                ]

            target_entity_description = ""
            if relationship.target_entity not in mapped_entities:
                new_target_entity = {
                    "name": relationship.target_entity,
                    "description": (
                        f"Derived from from relationship: "
                        f"{relationship.source_entity} -> {relationship.relationship_desc} -> {relationship.target_entity}"
                    ),
                    "meta": {"status": "need-revised"},
                }
                entities_data.append(new_target_entity)
                mapped_entities[relationship.target_entity] = new_target_entity
                target_entity_description = new_target_entity["description"]
            else:
                target_entity_description = mapped_entities[relationship.target_entity][
                    "description"
                ]

            relationship_dict = {
                "source_entity": relationship.source_entity,
                "source_entity_description": source_entity_description,
                "target_entity": relationship.target_entity,
                "target_entity_description": target_entity_description,
                "relationship_desc": relationship.relationship_desc,
                "meta": {
                    **extra_meta,
                },
            }
            relationships_data.append(relationship_dict)

        # Create DataFrames for entities and relationships
        entities_df = pd.DataFrame(entities_data)
        relationships_df = pd.DataFrame(relationships_data)
        return entities_df, relationships_df
