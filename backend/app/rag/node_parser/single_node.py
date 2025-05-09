from app.utils.uuid6 import uuid7
from llama_index.core.node_parser import NodeParser
from llama_index.core.schema import BaseNode, MetadataMode, ObjectType, TextNode, RelatedNodeInfo, NodeRelationship
from typing import Callable, List, Optional, Sequence

class SingleNodeParser(NodeParser):
    """NodeParser that keeps the entire document as a single node without splitting."""
        
    def get_nodes_from_documents(self, documents: Sequence, show_progress: bool = False, **kwargs) -> List[TextNode]:
        """
        Convert documents to nodes without splitting.
        
        Args:
            documents: The sequence of documents to process.
            show_progress: Whether to show a progress bar.
            **kwargs: Other possible parameters.
        
        Returns:
            A list of nodes with each document as a single node.
        """
        nodes = []
        
        if show_progress:
            from tqdm import tqdm
            documents = tqdm(documents, desc="Processing documents")
            
        for doc in documents:
            document_id = getattr(doc, "doc_id", None)
            if document_id is None:
                document_id = getattr(doc, "id_", None)
             
            # Create a single node containing the entire document content.
            node_id = str(uuid7())
            relationships = {}
            if document_id:
                relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(
                    hash=getattr(doc, "hash", None),
                    metadata=doc.metadata,
                    node_id=str(document_id),
                    node_type=ObjectType.DOCUMENT
                )
            
            node = TextNode(
                text=doc.text,
                metadata=doc.metadata,
                id_=node_id,
                hash=getattr(doc, "hash", None),
                relationships=relationships,
            )
            print(f"node ref_doc_id: {node.ref_doc_id}, id_: {node.id_}")
            nodes.append(node)
        return nodes
        
    def _parse_nodes(self, documents, metadata=None, show_progress=False, **parse_kwargs):
        nodes = []
        
        if show_progress:
            from tqdm import tqdm
            documents = tqdm(documents, desc="Parsing nodes")
            
        for doc in documents:
            document_id = getattr(doc, "doc_id", None)
            if document_id is None:
                document_id = getattr(doc, "id_", None)
             
            # Create a single node containing the entire document content.
            node_id = str(uuid7())
            relationships = {}
            if document_id:
                relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(
                    hash=getattr(doc, "hash", None),
                    metadata=doc.metadata,
                    node_id=str(document_id),
                    node_type=ObjectType.DOCUMENT
                )
                
            node = TextNode(
                text=doc.text,
                metadata=doc.metadata,
                id_=node_id,
                hash=getattr(doc, 'hash', None),
                relationships=relationships,
                excluded_embed_metadata_keys=getattr(doc, "excluded_embed_metadata_keys", None),
                excluded_llm_metadata_keys=getattr(doc, "excluded_llm_metadata_keys", None),
            )
            nodes.append(node)
        return nodes