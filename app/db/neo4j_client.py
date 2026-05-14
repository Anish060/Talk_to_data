from neo4j import GraphDatabase
import os
from dotenv import load_dotenv

load_dotenv()

class Neo4jClient:
    def __init__(self, uri=None, user=None, password=None):
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self.driver = None
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        except Exception as e:
            print(f"Failed to connect to Neo4j: {e}")

    def close(self):
        if self.driver:
            self.driver.close()

    def query(self, cypher, parameters=None):
        if not self.driver:
            return None
        with self.driver.session() as session:
            result = session.run(cypher, parameters)
            return [record for record in result]

    def clear_database(self):
        self.query("MATCH (n) DETACH DELETE n")

    def ingest_schema(self, schema_data):
        """
        Ingests the extracted schema into Neo4j using batch processing.
        """
        self.clear_database()
        
        # 1. Create Tables
        tables = [{"name": t["name"]} for t in schema_data["tables"]]
        self.query(
            "UNWIND $tables AS table CREATE (t:Table {name: table.name})",
            {"tables": tables}
        )
        
        # 2. Create Columns and link to Tables
        all_columns = []
        for table in schema_data["tables"]:
            for col in table["columns"]:
                all_columns.append({
                    "table_name": table["name"],
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col["nullable"]
                })
        
        self.query(
            """
            UNWIND $columns AS col
            MATCH (t:Table {name: col.table_name})
            CREATE (c:Column {name: col.name, type: col.type, nullable: col.nullable})
            CREATE (t)-[:HAS_COLUMN]->(c)
            """,
            {"columns": all_columns}
        )

        # 3. Create Foreign Key Relationships
        all_fks = []
        for table in schema_data["tables"]:
            for fk in table["foreign_keys"]:
                referred_table = fk["referred_table"]
                for i, col in enumerate(fk["constrained_columns"]):
                    referred_col = fk["referred_columns"][i]
                    all_fks.append({
                        "from_table": table["name"],
                        "from_col": col,
                        "to_table": referred_table,
                        "to_col": referred_col
                    })
        
        self.query(
            """
            UNWIND $fks AS fk
            MATCH (t1:Table {name: fk.from_table})-[:HAS_COLUMN]->(c1:Column {name: fk.from_col})
            MATCH (t2:Table {name: fk.to_table})-[:HAS_COLUMN]->(c2:Column {name: fk.to_col})
            CREATE (c1)-[:REFERENCES]->(c2)
            MERGE (t1)-[:REFERENCES_TABLE]->(t2)
            """,
            {"fks": all_fks}
        )
        print(f"Schema ingestion complete. Ingested {len(tables)} tables, {len(all_columns)} columns, and {len(all_fks)} relationships.")

    def ingest_ontology(self, ontology_data: List[Dict[str, Any]]):
        """
        Links business concepts and synonyms to table nodes in Neo4j.
        """
        for item in ontology_data:
            table_name = item["table_name"]
            concept_name = item["concept"]
            synonyms = item.get("synonyms", [])
            
            # Create Concept node and link to Table
            self.query(
                """
                MATCH (t:Table {name: $table_name})
                MERGE (con:Concept {name: $concept})
                MERGE (con)-[:MAPS_TO]->(t)
                WITH con
                UNWIND $synonyms AS syn
                MERGE (s:Synonym {name: syn})
                MERGE (s)-[:IS_SYNONYM_FOR]->(con)
                """,
                {
                    "table_name": table_name,
                    "concept": concept_name,
                    "synonyms": synonyms
                }
            )
        print(f"Ontology ingestion complete. Mapped {len(ontology_data)} concepts.")

if __name__ == "__main__":
    # Test script (requires Neo4j running)
    import json
    try:
        with open("data/schema_summary.json", "r") as f:
            data = json.load(f)
        
        client = Neo4jClient()
        client.ingest_schema(data)
        client.close()
    except Exception as e:
        print(f"Skipping ingestion test: {e}")
