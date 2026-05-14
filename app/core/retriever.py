from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient
from typing import List, Dict, Any

class ContextRetriever:
    def __init__(self, neo4j_client: Neo4jClient, vector_client: VectorClient):
        self.graph = neo4j_client
        self.vector = vector_client

    def retrieve_context(self, intent: Dict[str, Any]) -> str:
        """
        Retrieves schema context based on extracted intent.
        """
        relevant_tables = set()
        
        # 1a. Expand entities using Ontology (Neo4j)
        expanded_entities = set(intent.get("entities", []))
        for entity in intent.get("entities", []):
            synonym_matches = self.graph.query(
                """
                MATCH (s:Synonym {name: $name})-[:IS_SYNONYM_FOR]->(con:Concept)-[:MAPS_TO]->(t:Table)
                RETURN t.name as table_name
                """,
                {"name": entity}
            )
            for m in synonym_matches:
                relevant_tables.add(m["table_name"])
            
            concept_matches = self.graph.query(
                """
                MATCH (con:Concept {name: $name})-[:MAPS_TO]->(t:Table)
                RETURN t.name as table_name
                """,
                {"name": entity}
            )
            for m in concept_matches:
                relevant_tables.add(m["table_name"])

        # 1b. Search for relevant tables/columns using Vector DB
        search_terms = list(expanded_entities) + intent.get("metrics", [])
        search_query = " ".join(search_terms)
        
        vector_results = self.vector.search(search_query, n_results=5)
        
        for meta in vector_results['metadatas'][0]:
            if meta['type'] == 'table':
                relevant_tables.add(meta['name'])
            elif meta['type'] == 'column':
                relevant_tables.add(meta['table'])

        # 2. Get relationships from Neo4j for these tables
        context_parts = []
        context_parts.append("### Relevant Schema Context")
        
        for table in relevant_tables:
            # Get columns for table from context (or query graph)
            # For simplicity, let's assume we want to describe the table structure
            context_parts.append(f"- Table: {table}")
            
            # Query Neo4j for columns and relationships
            columns = self.graph.query(
                "MATCH (t:Table {name: $name})-[:HAS_COLUMN]->(c) RETURN c.name as name, c.type as type",
                {"name": table}
            )
            if columns:
                col_str = ", ".join([f"{c['name']} ({c['type']})" for c in columns])
                context_parts.append(f"  Columns: {col_str}")

        # 3. Find join paths between relevant tables using Graph Reasoning
        if len(relevant_tables) > 1:
            tables_list = list(relevant_tables)
            context_parts.append("\n### Suggested Join Paths (Graph Discovery)")
            
            # Query Neo4j for shortest paths between relevant tables
            for i in range(len(tables_list)):
                for j in range(i + 1, len(tables_list)):
                    t1, t2 = tables_list[i], tables_list[j]
                    
                    # Find join path via columns (including semantic ones)
                    paths = self.graph.query(
                        """
                        MATCH (t1:Table {name: $t1}), (t2:Table {name: $t2})
                        MATCH path = shortestPath((t1)-[:REFERENCES_TABLE|HAS_COLUMN|REFERENCES*1..3]-(t2))
                        RETURN path
                        """,
                        {"t1": t1, "t2": t2}
                    )
                    
                    if paths:
                        # Extract the logic of the path for the LLM
                        # This is a simplified extraction of the path logic
                        context_parts.append(f"- Join Discovery: Found relationship between {t1} and {t2}")
                        
                        # Add specific join columns if found
                        join_cols = self.graph.query(
                            """
                            MATCH (t1:Table {name: $t1})-[:HAS_COLUMN]->(c1)-[:REFERENCES]-(c2)<-[:HAS_COLUMN]-(t2:Table {name: $t2})
                            RETURN c1.name as col1, c2.name as col2
                            """,
                            {"t1": t1, "t2": t2}
                        )
                        for jc in join_cols:
                            context_parts.append(f"  * Join Condition: {t1}.{jc['col1']} = {t2}.{jc['col2']}")

        return "\n".join(context_parts)

if __name__ == "__main__":
    print("ContextRetriever loaded.")
