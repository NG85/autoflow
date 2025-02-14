import logging
from typing import List, Tuple
from llama_index.core.schema import NodeWithScore

from app.rag.chat.retrieve.retrieve_flow import RetrieveFlow
from app.rag.retrievers.knowledge_graph.schema import (
    KnowledgeGraphRetrievalResult,
)
from app.rag.retrievers.knowledge_graph.fusion_retriever import (
    KnowledgeGraphRetrieverConfig,
)
from app.models.enums import GraphType

logger = logging.getLogger(__name__)

class PlaybookQuestionTemplate:
    ORIGINAL = "Original Question: {question}"
    FEATURES = "Related Features: {features}"
    CONTEXT = "Knowledge Graph Context: {context}"
    INSTRUCTION = "Please provide information considering all the above context."


class PlaybookRetrieveFlow(RetrieveFlow):
    """Specialized retriever flow for playbook knowledge.
    
    This class extends the base RetrieveFlow to add playbook-specific functionality
    while maintaining compatibility with future base class changes.
    
    Key design principles:
    1. Minimize overrides of base class methods
    2. Use composition over inheritance where possible
    3. Keep playbook-specific logic isolated
    4. Make dependencies explicit
    """
    
    def retrieve(self, user_question: str) -> List[NodeWithScore]:
        """Override base retrieve to add playbook-specific enhancements.
        
        The method maintains the base class's core retrieval flow while adding
        playbook-specific context and feature enhancement.
        """
        # Get knowledge graph context using base class method
        base_kg_result = super().search_knowledge_graph(user_question)
        
        # Add playbook-specific enhancements
        enhanced_question = self._enhance_question_with_playbook_context(
            user_question=user_question,
            knowledge_graph_result=base_kg_result
        )
        
        # Use base class method for final retrieval
        return super().search_relevant_chunks(user_question=enhanced_question)

    def _enhance_question_with_playbook_context(
        self,
        user_question: str,
        knowledge_graph_result: Tuple[KnowledgeGraphRetrievalResult, str]
    ) -> str:
        """Enhance the question with playbook-specific context.
        
        This method is isolated from the base class and only handles
        playbook-specific enhancement logic.
        """
        knowledge_graph, knowledge_graph_context = knowledge_graph_result
        features = self._extract_playbook_features(knowledge_graph)
        
        return self._format_enhanced_question(
            user_question=user_question,
            features=features,
            knowledge_graph_context=knowledge_graph_context
        )
        
    def _extract_playbook_features(
        self,
        knowledge_graph: KnowledgeGraphRetrievalResult
    ) -> List[str]:
        """Extract playbook-specific features from knowledge graph.
        
        Isolated method for feature extraction that can be modified
        without affecting the base class.
        """
        try:
            return [
                entity.name 
                for entity in knowledge_graph.entities
                if entity.metadata and entity.metadata.get("topic") == "feature"
            ]
        except Exception as e:
            logger.error(f"Failed to extract playbook features: {e}")
            return []

    def _format_enhanced_question(
        self,
        user_question: str,
        features: List[str],
        knowledge_graph_context: str
    ) -> str:
        """Format the enhanced question with playbook context.
        
        Isolated method for question formatting that can be modified
        without affecting the base class.
        """
        parts = [
            PlaybookQuestionTemplate.ORIGINAL.format(question=user_question)
        ]
        
        if features:
            parts.append(
                PlaybookQuestionTemplate.FEATURES.format(features=", ".join(features))
            )
            
        if knowledge_graph_context:
            parts.append(
                PlaybookQuestionTemplate.CONTEXT.format(context=knowledge_graph_context)
            )
            
        parts.append(PlaybookQuestionTemplate.INSTRUCTION)        
        return "\n".join(parts)

    def _get_kg_retriever_config(
        self, 
        base_config: KnowledgeGraphRetrieverConfig
    ) -> KnowledgeGraphRetrieverConfig:
        """Override to add playbook-specific configuration.
        
        This method allows for playbook-specific configuration while
        maintaining base configuration compatibility.
        """
        config = super()._get_kg_retriever_config(base_config)
        config.graph_type = GraphType.playbook
        return config