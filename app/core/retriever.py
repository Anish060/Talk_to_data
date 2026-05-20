import json
from typing import List, Dict, Any
from sqlalchemy import create_engine, text
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient

class ContextRetriever:
    def __init__(self, neo4j_client: Neo4jClient, vector_client: VectorClient):
        self.graph = neo4j_client
        self.vector = vector_client
        self.info_db_path = "data/info.db"
        self.engine = create_engine(f"sqlite:///{self.info_db_path}")

    def retrieve_context(self, intent: Dict[str, Any]) -> tuple[str, list]:
        """
        Retrieves schema context and returns (context_string, mappings_list).
        """
        relevant_tables = set()
        grounding_mappings = [] # To store [User Term -> Object]

        # 1. Entity & Intent Resolution
        # a. Resolve via Ontology (Neo4j)
        for entity in intent.get("entities", []):
            synonym_matches = self.graph.query(
                """
                MATCH (s:Synonym)
                WHERE replace(toLower(s.name), ' ', '') = replace(toLower($name), ' ', '')
                MATCH (s)-[:IS_SYNONYM_FOR]->(con:Concept)-[:MAPS_TO]->(t:Table)
                RETURN t.name as table_name, con.name as concept
                """,
                {"name": entity}
            )
            for m in synonym_matches:
                relevant_tables.add(m["table_name"])
                grounding_mappings.append(f"Business Term: '{entity}' maps to Table '{m['table_name']}'")

        # b. Resolve via Fuzzy Search (Vector DB)
        # We search for tables, columns, and actual data values
        search_terms = list(intent.get("entities", [])) + intent.get("metrics", []) + intent.get("filters", []) + intent.get("dimensions", [])
        search_query = " ".join(search_terms)
        
        vector_results = self.vector.search(search_query, n_results=15)
        for meta in vector_results['metadatas'][0]:
            if meta['type'] == 'table':
                relevant_tables.add(meta['name'])
            elif meta['type'] == 'column':
                relevant_tables.add(meta['table'])
                grounding_mappings.append(f"Field Match: Field '{meta['name']}' found in Table '{meta['table']}'")
            elif meta['type'] == 'value':
                relevant_tables.add(meta['table'])
                grounding_mappings.append(f"Value Resolution: Found '{meta['value']}' in {meta['table']}.{meta['column']}")

        # c. High-Confidence SQL Matching (Fuzzy SQL search for filters)
        # This is more reliable than Vector for finding exact category names or values
        all_terms = set(list(intent.get("entities", [])) + intent.get("metrics", []) + intent.get("filters", []) + intent.get("dimensions", []))
        for term in all_terms:
            with self.engine.connect() as conn:
                # Search for values that look like the term in the Info DB metastore, ignoring spaces and casing symmetrically
                term_nospace = "".join(term.split()).lower()
                sql = text("""
                    SELECT table_name, column_name, value 
                    FROM meta_values 
                    WHERE replace(LOWER(value), ' ', '') = :t 
                       OR replace(LOWER(value), ' ', '') LIKE :t_like 
                    LIMIT 3
                """)
                res = conn.execute(sql, {"t": term_nospace, "t_like": f"%{term_nospace}%"}).fetchall()
                for r in res:
                    relevant_tables.add(r[0])
                    grounding_mappings.append(f"Value Lock: Term '{term}' maps to Exact DB Value '{r[2]}' in {r[0]}.{r[1]}")

        # d. Recursive Deep Search for orphaned metrics/dimensions
        deep_search_terms = intent.get("metrics", []) + intent.get("dimensions", [])
        for term in deep_search_terms:
            col_search = self.vector.search(f"database object named {term}", n_results=3)
            for meta in col_search['metadatas'][0]:
                if meta['type'] == 'column':
                    relevant_tables.add(meta['table'])
                    grounding_mappings.append(f"Deep Resolve: Term '{term}' matches Column '{meta['name']}' in Table '{meta['table']}'")

        # 1.5. Multi-Hop Join Reasoning & Bridge Table Expansion (Neo4j)
        join_paths = []
        discovered_joins = set()
        additional_tables = set()

        if len(relevant_tables) > 1:
            tables_list = list(relevant_tables)
            for i in range(len(tables_list)):
                for j in range(i + 1, len(tables_list)):
                    t1, t2 = tables_list[i], tables_list[j]
                    # Find shortest path between t1 and t2 ignoring direction
                    path_results = self.graph.query(
                        """
                        MATCH p = shortestPath((t1:Table {name: $t1})-[:REFERENCES_TABLE*..5]-(t2:Table {name: $t2}))
                        RETURN [node in nodes(p) | node.name] as path_nodes
                        """,
                        {"t1": t1, "t2": t2}
                    )
                    if path_results and path_results[0]["path_nodes"]:
                        path_nodes = path_results[0]["path_nodes"]
                        # Expand relevant tables to include all intermediate bridge tables
                        for node in path_nodes:
                            additional_tables.add(node)
                        
                        # Add adjacent joins to our set
                        for k in range(len(path_nodes) - 1):
                            ta, tb = path_nodes[k], path_nodes[k+1]
                            # Store in a stable order to avoid duplicate processing
                            pair = tuple(sorted([ta, tb]))
                            if pair not in discovered_joins:
                                discovered_joins.add(pair)
                                
                                # Query the column-level join keys for this specific adjacent step
                                join_cols = self.graph.query(
                                    """
                                    MATCH (ta:Table {name: $ta})-[:HAS_COLUMN]->(ca)
                                    MATCH (tb:Table {name: $tb})-[:HAS_COLUMN]->(cb)
                                    OPTIONAL MATCH (ca)-[r1:REFERENCES]->(cb)
                                    OPTIONAL MATCH (cb)-[r2:REFERENCES]->(ca)
                                    WITH ca, cb, r1, r2
                                    WHERE r1 IS NOT NULL OR r2 IS NOT NULL
                                    RETURN ca.name as col_a, cb.name as col_b, (r1 IS NOT NULL) as ta_is_detail
                                    """,
                                    {"ta": ta, "tb": tb}
                                )
                                if join_cols:
                                    for jc in join_cols:
                                        col_a = jc["col_a"]
                                        col_b = jc["col_b"]
                                        ta_is_detail = jc["ta_is_detail"]
                                        
                                        if ta_is_detail:
                                            join_paths.append(f"- To join {ta} and {tb}: Use `{ta}.{col_a} = {tb}.{col_b}`")
                                            join_paths.append(f"  * CONSTRAINT: '{tb}' is a Header table relative to the Detail table '{ta}'. Do not SUM() aggregate columns from '{tb}' directly after joining.")
                                        else:
                                            join_paths.append(f"- To join {tb} and {ta}: Use `{tb}.{col_b} = {ta}.{col_a}`")
                                            join_paths.append(f"  * CONSTRAINT: '{ta}' is a Header table relative to the Detail table '{tb}'. Do not SUM() aggregate columns from '{ta}' directly after joining.")

            relevant_tables.update(additional_tables)

        # 2. Build Context from Info DB
        context_parts = []
        context_parts.append("### SEMANTIC VALUE MAPPINGS (MANDATORY)")
        context_parts.append("The following mappings resolve your query terms to exact database strings. Use the 'Exact Value' in your SQL filters:")
        for gm in grounding_mappings:
            context_parts.append(f"- {gm}")
        
        context_parts.append("\n### CANONICAL SCHEMA DEFINITIONS")
        
        with self.engine.connect() as conn:
            for table_name in relevant_tables:
                # Fetch Table metadata and samples (Case-insensitive)
                res = conn.execute(text("SELECT name, is_view, samples FROM meta_tables WHERE LOWER(name) = LOWER(:t)"), {"t": table_name}).fetchone()
                if not res: continue
                
                table_type = "View" if res[1] else "Table"
                context_parts.append(f"#### {table_type}: {table_name}")
                
                # Fetch Columns and their sample values
                cols = conn.execute(text("SELECT name, type, unique_values FROM meta_columns WHERE table_name = :t"), {"t": table_name}).fetchall()
                col_info = []
                for c in cols:
                    unique_str = f" (Example Values: {c[2]})" if c[2] and c[2] != '[]' else ""
                    col_info.append(f"  - {c[0]} ({c[1]}){unique_str}")
                
                context_parts.append("Columns:")
                context_parts.extend(col_info)
                
                # Add sample data rows
                samples = json.loads(res[2])
                if samples:
                    context_parts.append("Data Samples (Actual Rows):")
                    context_parts.append(json.dumps(samples[:2], indent=4).replace("\n", "\n  "))

        # 3. Join Reasoning (Neo4j shortest paths)
        if join_paths:
            context_parts.append("\n### SUGGESTED JOIN PATHS AND CONSTRAINTS")
            context_parts.extend(join_paths)

        return "\n".join(context_parts), grounding_mappings

if __name__ == "__main__":
    pass
