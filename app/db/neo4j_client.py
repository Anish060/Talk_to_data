from neo4j import GraphDatabase
import os
from dotenv import load_dotenv

load_dotenv()
import json
from app.utils.catalogue_loader import get_rule_for_field
from typing import List, Dict, Any

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

    def ingest_rules(self, schema_data: dict):
        """
        Ingest rule metadata from the catalogue into Neo4j.
        For each column, we look up a rule via get_rule_for_field.
        If a rule exists, we create a :Rule node with its id, type, and optional sql_rule.
        Then we connect the column node to the rule node via a :HAS_RULE relationship.
        """
        # Build a list of rule entries
        rule_entries = []
        for table in schema_data.get('tables', []):
            table_name = table.get('name')
            for col in table.get('columns', []):
                col_name = col.get('name')
                field = f"{table_name}.{col_name}"
                rule = get_rule_for_field(field)
                if rule:
                    rule_entries.append({
                        'table_name': table_name,
                        'col_name': col_name,
                        'rule_id': rule.get('id'),
                        'rule_type': rule.get('type'),
                        'sql_rule': json.dumps(rule.get('sql_rule')) if rule.get('sql_rule') is not None else None
                    })
        if not rule_entries:
            print("[Neo4j] No rule metadata to ingest.")
            return
        # Cypher to create Rule nodes and link them
        cypher = """
        UNWIND $rules AS r
        MATCH (t:Table {name: r.table_name})-[:HAS_COLUMN]->(c:Column {name: r.col_name})
        MERGE (rule:Rule {id: r.rule_id})
        SET rule.type = r.rule_type,
            rule.sql_rule = r.sql_rule
        MERGE (c)-[:HAS_RULE]->(rule)
        """
        self.query(cypher, {'rules': rule_entries})
        print(f"[Neo4j] Ingested {len(rule_entries)} rule nodes.")

    def ingest_recursive_rules(self):
        """
        Ingest catalogue rules of type "recursive" that apply to whole tables.
        Creates a :RecursiveRule node and links it to the corresponding :Table.
        """
        # Load all rules from the catalogue file via the utility
        try:
            from app.utils.catalogue_loader import all_rules
            rules = all_rules()
        except Exception as e:
            print(f"[Neo4j] Could not load recursive rules: {e}")
            return
        for rule in rules:
            if rule.get('type') != 'recursive':
                continue
            applies = rule.get('applies_to', [])
            for tbl in applies:
                # Create a RecursiveRule node and link to the table
                self.query(
                    """
                    MATCH (t:Table {name: $table_name})
                    MERGE (r:RecursiveRule {id: $rule_id})
                    SET r.type = $rule_type,
                        r.description = $desc,
                        r.cypher_template = $template
                    MERGE (t)-[:HAS_RECURSIVE_RULE]->(r)
                    """,
                    {
                        "table_name": tbl,
                        "rule_id": rule.get('id'),
                        "rule_type": rule.get('type'),
                        "desc": rule.get('description'),
                        "template": rule.get('cypher_template')
                    }
                )
        print(f"[Neo4j] Ingested {len([r for r in rules if r.get('type') == 'recursive'])} recursive rules.")

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
