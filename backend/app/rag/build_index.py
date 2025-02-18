import logging
from typing import List, Optional, Type

from llama_index.core import VectorStoreIndex
from llama_index.core.embeddings.utils import EmbedType
from llama_index.core.llms.llm import LLM
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TransformComponent

from sqlmodel import Session
from app.models.knowledge_base import (
    IndexMethod,
    ChunkSplitter,
    ChunkingMode,
    KnowledgeBase,
    SentenceSplitterOptions,
    GeneralChunkingConfig,
    ChunkSplitterConfig,
    MarkdownNodeParserOptions,
    AdvancedChunkingConfig,
)
from app.rag.knowledge_base.index_store import (
    get_kb_tidb_vector_store,
    get_kb_tidb_graph_store,
)
from app.rag.indices.knowledge_graph import KnowledgeGraphIndex
from app.models import Document, Chunk, Entity, Relationship
from app.rag.node_parser.file.markdown import MarkdownNodeParser
from app.types import MimeTypes
from app.utils.dspy import get_dspy_lm_by_llama_llm
from app.models.enums import GraphType
from app.rag.indices.knowledge_graph.graph_store.helpers import get_entity_description_embedding, get_entity_metadata_embedding, get_relationship_description_embedding

logger = logging.getLogger(__name__)


class IndexService:
    """
    Service class for building RAG indexes (vector index and knowledge graph index).
    """

    def __init__(
        self,
        llm: LLM,
        embed_model: Optional[EmbedType] = None,
        knowledge_base: Optional[KnowledgeBase] = None,
    ):
        self._llm = llm
        self._dspy_lm = get_dspy_lm_by_llama_llm(llm)
        self._embed_model = embed_model
        self._knowledge_base = knowledge_base

    # TODO: move to ./indices/vector_search
    def build_vector_index_for_document(
        self, session: Session, db_document: Type[Document]
    ):
        """
        Build vector index and graph index from document.

        Build vector index will do the following:
        1. Parse document into nodes.
        2. Extract metadata from nodes by applying transformations.
        3. embedding text nodes.
        4. Insert nodes into `chunks` table.
        """
        vector_store = get_kb_tidb_vector_store(session, self._knowledge_base)
        transformations = self._get_transformations(db_document)
        vector_index = VectorStoreIndex.from_vector_store(
            vector_store,
            embed_model=self._embed_model,
            transformations=transformations,
        )

        llama_document = db_document.to_llama_document()
        logger.info(f"Start building vector index for document #{db_document.id}.")
        vector_index.insert(llama_document, source_uri=db_document.source_uri)
        logger.info(f"Finish building vector index for document #{db_document.id}.")
        vector_store.close_session()

        return
   
    def _get_transformations(
        self, db_document: Type[Document]
    ) -> List[TransformComponent]:
        transformations = []

        chunking_config_dict = self._knowledge_base.chunking_config
        mode = (
            chunking_config_dict["mode"]
            if "mode" in chunking_config_dict
            else ChunkingMode.GENERAL
        )

        if mode == ChunkingMode.ADVANCED:
            chunking_config = AdvancedChunkingConfig.model_validate(
                chunking_config_dict
            )
            rules = chunking_config.rules
        else:
            chunking_config = GeneralChunkingConfig.model_validate(chunking_config_dict)
            rules = {
                MimeTypes.PLAIN_TXT: ChunkSplitterConfig(
                    splitter=ChunkSplitter.SENTENCE_SPLITTER,
                    splitter_options=SentenceSplitterOptions(
                        chunk_size=chunking_config.chunk_size,
                        chunk_overlap=chunking_config.chunk_overlap,
                        paragraph_separator=chunking_config.paragraph_separator,
                    ),
                ),
                MimeTypes.MARKDOWN: ChunkSplitterConfig(
                    splitter=ChunkSplitter.MARKDOWN_NODE_PARSER,
                    splitter_options=MarkdownNodeParserOptions(
                        chunk_size=chunking_config.chunk_size,
                    ),
                ),
            }

        # Chunking
        mime_type = db_document.mime_type
        if mime_type not in rules:
            raise RuntimeError(
                f"Can not chunking for the document in {db_document.mime_type} format"
            )

        rule = rules[mime_type]
        match rule.splitter:
            case ChunkSplitter.MARKDOWN_NODE_PARSER:
                options = MarkdownNodeParserOptions.model_validate(
                    rule.splitter_options
                )
                transformations.append(MarkdownNodeParser(**options.model_dump()))
            case ChunkSplitter.SENTENCE_SPLITTER:
                options = SentenceSplitterOptions.model_validate(rule.splitter_options)
                transformations.append(SentenceSplitter(**options.model_dump()))
            case _:
                raise ValueError(f"Unsupported chunking splitter type: {rule.splitter}")

        return transformations

    def build_vector_index_for_chunk(
        self, session: Session, db_chunk: Chunk
    ):
        """
        Build vector index from existing chunks.

        Build vector index will do the following:
        1. Convert existing chunk to TextNode.
        2. Insert TextNode into vector index.
        """
        if db_chunk.embedding is not None: 
            logger.info(f"embedding for chunk #{db_chunk.id} already exists, skip building vector index.")
            return
        
        # vector_store = get_kb_tidb_vector_store(session, self._knowledge_base)

        logger.info(f"Start building vector index for chunk #{db_chunk.id}.")
        
        # Generate embedding for chunk text
        embedding = self._embed_model.get_text_embedding(db_chunk.text)
        
        # Update chunk with new embedding
        db_chunk.embedding = embedding
        session.add(db_chunk)
        session.commit()

        
        logger.info(f"Finish building vector index for chunk #{db_chunk.id}.")
        # vector_store.close_session()

        return
  
    def build_vector_index_for_entity(
        self, session: Session, db_entity: Entity
    ):
        """
        Build vector embeddings for entity's description and meta fields.
        
        This will:
        1. Generate embeddings for description and meta text
        2. Update entity with new embeddings
        """
        logger.info(f"Start building vector embeddings for entity #{db_entity.id}")
        
        try:
            # Generate embedding for description using the same helper function
            if db_entity.name and db_entity.description:
                description_embedding = get_entity_description_embedding(
                    db_entity.name,
                    db_entity.description,
                    self._embed_model
                )
                db_entity.description_vec = description_embedding
            
            # Generate embedding for meta using the same helper function
            if db_entity.meta:
                meta_embedding = get_entity_metadata_embedding(
                    db_entity.meta,
                    self._embed_model
                )
                db_entity.meta_vec = meta_embedding
            
            # Update entity in database
            session.add(db_entity)
            session.commit()
            
            logger.info(f"Finished building vector embeddings for entity #{db_entity.id}")
        except Exception as e:
            logger.error(f"Failed to build vector embeddings for entity #{db_entity.id}: {str(e)}")
            raise
         

    def build_vector_index_for_relationship(
        self, session: Session, db_relationship: Relationship
    ):
        """
        Build vector embeddings for relationship's description field.
        
        This will:
        1. Generate embeddings for description text
        2. Update relationship with new embeddings
        """
        logger.info(f"Start building vector embeddings for relationship #{db_relationship.id}")
        
        try:
            # Generate embedding for description using the same helper function
            if db_relationship.description:
                description_embedding = get_relationship_description_embedding(
                    db_relationship.source_entity.name,
                    db_relationship.source_entity.description,
                    db_relationship.target_entity.name,
                    db_relationship.target_entity.description,
                    db_relationship.description,
                    self._embed_model
                )
                db_relationship.description_vec = description_embedding
            
            # Update relationship in database
            session.add(db_relationship)
            session.commit()
            
            logger.info(f"Finished building vector embeddings for relationship #{db_relationship.id}")
        except Exception as e:
            logger.error(f"Failed to build vector embeddings for relationship #{db_relationship.id}: {str(e)}")
            raise

    def build_kg_index_for_chunk(self, session: Session, db_chunk: Type[Chunk]):
        """Build knowledge graph index from chunk.

        Build knowledge graph index will do the following:
        1. load TextNode from `chunks` table.
        2. extract entities and relations from TextNode.
        3. insert entities and relations into `entities` and `relations` table.
        """

        graph_store = get_kb_tidb_graph_store(session, self._knowledge_base)
        graph_index: KnowledgeGraphIndex = KnowledgeGraphIndex.from_existing(
            dspy_lm=self._dspy_lm,
            kg_store=graph_store,
            graph_type=GraphType.general
        )

        node = db_chunk.to_llama_text_node()
        logger.info(f"Start building knowledge graph index for chunk #{db_chunk.id}.")
        graph_index.insert_nodes([node])
        logger.info(f"Finish building knowledge graph index for chunk #{db_chunk.id}.")
        graph_store.close_session()

        return

    def build_playbook_kg_index_for_chunk(self, session: Session, db_chunk: Type[Chunk]):
        """Build Playbook knowledge graph index from chunk.

        Build playbook knowledge graph index will do the following:
        1. load TextNode from `chunks` table.
        2. extract playbook customized entities and relations from TextNode.
        3. insert playbook customized entities and relations into `entities` and `relations` table.
        """

        graph_store = get_kb_tidb_graph_store(session, self._knowledge_base, graph_type=GraphType.playbook)
        graph_index: KnowledgeGraphIndex = KnowledgeGraphIndex.from_existing(
            dspy_lm=self._dspy_lm,
            kg_store=graph_store,
            graph_type=GraphType.playbook
        )

        node = db_chunk.to_llama_text_node()
        logger.info(f"Start building playbook knowledge graph index for chunk #{db_chunk.id}.")
        graph_index.insert_nodes([node])
        logger.info(f"Finish building playbook knowledge graph index for chunk #{db_chunk.id}.")
        graph_store.close_session()

        return
    