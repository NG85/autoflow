import logging
from copy import deepcopy
import pandas as pd
import dspy
from typing import Mapping, Optional, List

from dspy import Predict
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
    """专门针对劳动法领域文档（法规/案例/指南）构建知识图谱的实体关系提取指令

    分析步骤：

    1. 实体提取（劳动法专项）：
       A. 基础法律实体：
          - 法律条款：精确到条/款/项（如《劳动合同法》第38条第1款第2项）
          - 法律主体：用人单位（区分注册地/经营地）、劳动者（细分类型：女职工/工伤职工等）
          - 法律行为：解除/终止/调岗/降薪/经济补偿
          
       B. 案例特征实体：
          - 案号格式：(2023)沪01民终123号
          - 裁判要点：违法解除赔偿2N、程序瑕疵认定标准
          - 赔偿金额：精确数字+计算方式（如2×月工资×工作年限）
          
       C. 风险要素实体：
          - 时效节点：仲裁时效（1年）、工伤认定时效（30日/1年）
          - 程序要件：民主程序（职工代表大会通过）、公示签收
          - 证据类型：考核表（需两次）、培训记录、工资条
          
       D. 地域差异实体：
          - 地方性法规：如《广东省工伤保险条例》
          - 地区特征：社平工资（分省/市）、高温补贴标准
          - 特殊政策：自贸区用工政策、港澳台员工特殊规定

    2. 元数据标注（劳动法特征）：
       - 每个实体必须包含：
         ① 法律效力级别：法律/行政法规/地方性法规/司法解释
         ② 生效/废止时间：2024-01-01至2025-12-31
         ③ 地域适用范围：全国/省/市（如仅适用于上海市）
         ④ 关联条款：引用其他法律条款（如《劳动合同法》第40条引用第26条）
         ⑤ 风险等级：高（2N赔偿）/中（程序补正）/低（协商空间）
       - 证据类实体需标注：
         ① 证据形式：书证/电子数据/证人证言
         ② 举证责任：用人单位/劳动者
         ③ 证明对象：劳动关系/工资标准/工作时间

    3. 关系建立（劳动法逻辑）：
       A. 法律引用关系：
          - 引用依据：$法律条款 是 $案例判决 的裁判依据 → (条款)-[AS_LEGAL_BASIS]->(案例)
          - 补充规定：$地方性法规 细化 $法律条款 → (地方法规)-[SUPPLEMENT]->(法律)
          
       B. 程序依赖关系：
          - 前置条件：$民主程序 是 $规章制度 生效前提 → (民主程序)-[PRECEDURE_FOR]->(制度)
          - 时序关系：$第一次考核 必须在 $培训 之后 → (考核)-[AFTER]->(培训)
          
       C. 风险关联关系：
          - 风险成因：$未签收制度 导致 $违法解除 → (未签收)-[CAUSES]->(违法解除)
          - 风险缓解：$工会通知 降低 $程序瑕疵风险 → (工会通知)-[MITIGATES]->(程序瑕疵)
          
       D. 计算逻辑关系：
          - 计算基准：$月工资 影响 $经济补偿金 → (月工资)-[BASIS_FOR]->(补偿金)
          - 封顶规则：$社平工资三倍 限制 $补偿金上限 → (社平工资)-[CAPS]->(补偿金)

    关键提取原则：
    1. 时效性处理：
       - 并列时效：标注最早/最晚时效（如工伤认定：单位30日/个人1年）
       - 计算基准日：区分争议发生日/离职日/仲裁提起日
       
    2. 地域冲突解决：
       - 注册地vs工作地：标注"以较高标准为准"的冲突解决规则
       - 政策过渡期：标注新旧法规交替期的适用规则
       
    3. 证据链构建：
       - 必须形成闭环：制度公示 → 考核标准 → 考核结果 → 解除依据
       - 时间戳验证：培训记录时间应早于考核时间
       
    4. 赔偿计算：
       - 区分N、N+1、2N适用场景
       - 工作年限精确到日（满6个月按0.5年计）

    请以JSON格式输出知识图谱，确保符合以下劳动法特征：
    - 实体名称包含法律术语标准表述
    - 关系方向体现法律逻辑（如用人单位→劳动者）
    - 元数据字段完整包含法律要素
    """

    text = dspy.InputField(
        desc="a paragraph of text to extract entities and relationships to form a knowledge graph"
    )
    knowledge: KnowledgeGraph = dspy.OutputField(
        desc="Graph representation of the knowledge extracted from the text."
    )


class ExtractCovariate(dspy.Signature):
    """劳动法实体协变量专项提取指令

    协变量提取规范：

    1. 法律条款类实体：
       - 必须包含：
         ① 法律效力级别（法律/行政法规/地方性法规）
         ② 生效/废止时间（精确到日）
         ③ 关联条款（引用的其他法律条款）
         ④ 地域适用范围（全国/省/市）
         ⑤ 风险等级（高/中/低）
       - 示例：
         "topic": "法律条款",
         "法律效力": "行政法规",
         "生效日期": "2023-05-01",
         "关联条款": ["劳动合同法第38条"],
         "地域适用": ["江苏省"]

    2. 案例类实体：
       - 必须包含：
         ① 案号与法院层级（基层/中院/高院）
         ② 裁判要点摘要（核心法律观点）
         ③ 赔偿计算方式（N/2N/赔偿基数）
         ④ 证据链完整性评估
         ⑤ 程序瑕疵类型（如有）
       - 示例：
         "topic": "类案参考",
         "案号": "(2023)沪01民终1234号",
         "裁判要点": "未履行民主程序的制度不得作为解除依据",
         "赔偿计算": "2N（月工资×工作年限×2）"

    3. 风险要素类实体：
       - 必须包含：
         ① 风险触发条件（如超时效/程序缺失）
         ② 法律后果（赔偿金额/恢复劳动关系）
         ③ 风险规避措施
         ④ 举证责任方
         ⑤ 时效计算基准日
       - 示例：
         "topic": "仲裁时效",
         "时效期限": "1年",
         "起算日": "劳动关系终止之日",
         "超期后果": "丧失胜诉权",
         "中断事由": ["书面催告记录"]

    4. 计算类实体：
       - 必须包含：
         ① 计算公式（数学表达式）
         ② 计算参数来源（月工资标准确定方式）
         ③ 封顶规则（如社平工资三倍）
         ④ 年限折算规则（满6个月按0.5年）
         ⑤ 地区差异参数
       - 示例：
         "topic": "经济补偿金",
         "公式": "N×月工资",
         "参数定义": {
           "N": "工作年限（精确到月）",
           "月工资": "离职前12个月平均工资"
         },
         "封顶规则": "不超过当地社平工资三倍"

    提取要求：
    1. 证据类实体必须形成证据链：
       - 时间顺序：制度公示→培训记录→考核结果→解除依据
       - 逻辑闭环：每个环节需有对应证据支撑
       
    2. 赔偿计算需区分场景：
       - 正常解除（N）
       - 违法解除（2N）
       - 协商解除（N+1）
       
    3. 程序要件必须标注：
       - 民主程序（通过比例/公示方式）
       - 通知程序（提前30日书面）
       - 工会程序（通知时间/反馈处理）

    请确保所有协变量：
    - 源自文本可验证内容
    - 数值型数据附带计算方式
    - 时间信息精确到日
    - 法律条款完整标注条款号

    输出要求：严格的JSON格式
    """

    text = dspy.InputField(
        desc="待分析的劳动法文本内容（法规条款/案例文书/风险指南）"
    )

    entities: List[EntityCovariateInput] = dspy.InputField(
        desc="已识别的劳动法实体列表，需补充协变量信息"
    )
    covariates: List[EntityCovariateOutput] = dspy.OutputField(
        desc="结构化协变量数据，符合劳动法领域规范",
        format="json"
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
        self.prog_graph = Predict(ExtractGraphTriplet)
        self.prog_covariates = Predict(ExtractCovariate)

    def forward(self, text):
        with dspy.settings.context(lm=self.dspy_lm):
            pred_graph = self.prog_graph(text=text)
                    
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

        # Ensure all entities have proper metadata dictionary structure
        for entity in pred.knowledge.entities:
            if entity.metadata is None or not isinstance(entity.metadata, dict):
                entity.metadata = {"topic": "Unknown", "status": "auto-generated"}

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
