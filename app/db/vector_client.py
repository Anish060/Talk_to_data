import chromadb
from chromadb.utils import embedding_functions
import os
from typing import List, Dict, Any

class VectorClient:
    def __init__(self, path="data/chroma"):
        self.client = chromadb.PersistentClient(path=path)
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        self.collection_name = "metadata_search"
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_fn
        )

    def clear(self):
        """Removes the current collection to ensure a fresh sync."""
        try:
            self.client.delete_collection(self.collection_name)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn
            )
            print(f"Cleared and recreated collection: {self.collection_name}")
        except Exception as e:
            print(f"Warning: Could not clear collection: {e}")

    def upsert_metadata(self, schema_data: Dict[str, Any]):
        """
        Embeds table and column metadata for semantic search.
        """
        ids = []
        documents = []
        metadatas = []

        for table in schema_data["tables"]:
            table_name = table["name"]
            # Table embedding
            ids.append(f"table_{table_name}")
            documents.append(f"Table: {table_name}. Contains columns: {', '.join([c['name'] for c in table['columns']])}")
            metadatas.append({"type": "table", "name": table_name})

            # Column embeddings
            for col in table["columns"]:
                col_name = col["name"]
                ids.append(f"col_{table_name}_{col_name}")
                documents.append(f"Column: {col_name} in table {table_name}. Type: {col['type']}")
                metadatas.append({"type": "column", "table": table_name, "name": col_name})

            # NEW: Value embeddings for semantic search
            if "unique_values" in table:
                for col_name, values in table["unique_values"].items():
                    for val in values:
                        if val:
                            ids.append(f"val_{table_name}_{col_name}_{val}")
                            documents.append(f"Value: {val}. Found in column {col_name} of table {table_name}")
                            metadatas.append({"type": "value", "table": table_name, "column": col_name, "value": str(val)})

        self.collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas
        )
        print(f"Upserted {len(ids)} metadata items to Vector DB.")

    def search(self, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )
        return results

if __name__ == "__main__":
    import json
    # Simple test
    try:
        with open("data/schema_summary.json", "r") as f:
            data = json.load(f)
        client = VectorClient()
        client.upsert_metadata(data)
        res = client.search("Show me customer revenue")
        print(f"Search results: {res['documents']}")
    except Exception as e:
        print(f"VectorClient test failed: {e}")
