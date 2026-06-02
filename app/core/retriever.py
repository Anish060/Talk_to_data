import json
from typing import List, Dict, Any
from sqlalchemy import create_engine, text
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient
from concurrent.futures import ThreadPoolExecutor, as_completed

class ContextRetriever:
    def __init__(self, neo4j_client: Neo4jClient, vector_client: VectorClient):
        self.graph = neo4j_client
        self.vector = vector_client
        self.info_db_path = "data/info.db"
        self.engine = create_engine(f"sqlite:///{self.info_db_path}")
    def _log_neo4j(self, label: str, result):
        """
        Formats a Neo4j query result for clean terminal output.
        """
        try:
            import json
            compact = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            compact = str(result)
        print(f"[NEO4J] {label} → {compact}")

    def _resolve_ontology(self, entity: str) -> tuple[set[str], list[str]]:
        tables = set()
        mappings = []
        synonym_matches = self.graph.query(
            """
            MATCH (s:Synonym)
            WHERE replace(toLower(s.name), ' ', '') = replace(toLower($name), ' ', '')
            MATCH (s)-[:IS_SYNONYM_FOR]->(con:Concept)-[:MAPS_TO]->(t:Table)
            RETURN t.name as table_name, con.name as concept
            """,
            {"name": entity}
        )
        self._log_neo4j("ontology", synonym_matches)
        if synonym_matches:
            for m in synonym_matches:
                tables.add(m["table_name"])
                mappings.append(f"Business Term: '{entity}' maps to Table '{m['table_name']}'")
        return tables, mappings

    def _resolve_vector_main(self, intent: Dict[str, Any]) -> tuple[set[str], list[str]]:
        tables = set()
        mappings = []
        search_terms = list(intent.get("entities", [])) + intent.get("metrics", []) + intent.get("filters", []) + intent.get("dimensions", [])
        if not search_terms:
            return tables, mappings
        search_query = " ".join(search_terms)
        
        vector_results = self.vector.search(search_query, n_results=15)
        if vector_results and 'metadatas' in vector_results and vector_results['metadatas']:
            for meta in vector_results['metadatas'][0]:
                if meta['type'] == 'table':
                    tables.add(meta['name'])
                elif meta['type'] == 'column':
                    tables.add(meta['table'])
                    mappings.append(f"Field Match: Field '{meta['name']}' found in Table '{meta['table']}'")
                elif meta['type'] == 'value':
                    tables.add(meta['table'])
                    mappings.append(f"Value Resolution: Found '{meta['value']}' in {meta['table']}.{meta['column']}")
        return tables, mappings

    def _resolve_sqlite_value(self, term: str) -> tuple[set[str], list[str]]:
        """Lookup exact semantic values in the info SQLite DB.
        If the ``meta_values`` table is missing (e.g., after a fresh sync), we simply
        return empty results instead of raising an exception, allowing the rest of
        the Neo4j‑based grounding to proceed.
        """
        tables = set()
        mappings = []
        term_nospace = "".join(term.split()).lower()
        sql = text("""
            SELECT table_name, column_name, value 
            FROM meta_values 
            WHERE replace(LOWER(value), ' ', '') = :t 
               OR replace(LOWER(value), ' ', '') LIKE :t_like 
            LIMIT 3
        """)
        try:
            with self.engine.connect() as conn:
                res = conn.execute(sql, {"t": term_nospace, "t_like": f"%{term_nospace}%"}).fetchall()
                for r in res:
                    tables.add(r[0])
                    mappings.append(f"Value Lock: Term '{term}' maps to Exact DB Value '{r[2]}' in {r[0]}.{r[1]}")
        except Exception as e:
            # ``meta_values`` may not exist; log and continue silently.
            print(f"⚠️ SQLite lookup failed for term '{term}': {e}")
        return tables, mappings

    def _resolve_deep_column(self, term: str) -> tuple[set[str], list[str]]:
        tables = set()
        mappings = []
        col_search = self.vector.search(f"database object named {term}", n_results=3)
        if col_search and 'metadatas' in col_search and col_search['metadatas']:
            for meta in col_search['metadatas'][0]:
                if meta['type'] == 'column':
                    tables.add(meta['table'])
                    mappings.append(f"Deep Resolve: Term '{term}' matches Column '{meta['name']}' in Table '{meta['table']}'")
        return tables, mappings

    def _find_shortest_path(self, t1: str, t2: str) -> list[str]:
        path_results = self.graph.query(
            """
            MATCH p = shortestPath((t1:Table {name: $t1})-[:REFERENCES_TABLE*..5]-(t2:Table {name: $t2}))
            RETURN [node in nodes(p) | node.name] as path_nodes
            """,
            {"t1": t1, "t2": t2}
        )
        self._log_neo4j("shortest_path", path_results)
        if path_results and path_results[0]["path_nodes"]:
            return path_results[0]["path_nodes"]
        return []

    def _find_join_details(self, ta: str, tb: str) -> list[str]:
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
        self._log_neo4j("join_details", join_cols)
        paths = []
        if join_cols:
            for jc in join_cols:
                col_a = jc["col_a"]
                col_b = jc["col_b"]
                ta_is_detail = jc["ta_is_detail"]
                
                if ta_is_detail:
                    paths.append(f"- To join {ta} and {tb}: Use `{ta}.{col_a} = {tb}.{col_b}`")
                    paths.append(f"  * CONSTRAINT: '{tb}' is a Header table relative to the Detail table '{ta}'. Do not SUM() aggregate columns from '{tb}' directly after joining.")
                else:
                    paths.append(f"- To join {tb} and {ta}: Use `{tb}.{col_b} = {ta}.{col_a}`")
                    paths.append(f"  * CONSTRAINT: '{ta}' is a Header table relative to the Detail table '{tb}'. Do not SUM() aggregate columns from '{ta}' directly after joining.")
        return paths

    def check_relevance(self, intent: Dict[str, Any]) -> tuple[float, list[str], list[str]]:
        """
        Check query relevance against the database schema and values.
        Returns a tuple: (relevance_score, matched_concepts, unmatched_concepts)
        """
        concepts = []
        for key in ["entities", "metrics", "filters", "dimensions"]:
            if key in intent and isinstance(intent[key], list):
                concepts.extend(intent[key])
        
        # Deduplicate and clean
        unique_concepts = []
        for c in concepts:
            c_clean = str(c).strip()
            if c_clean and c_clean not in unique_concepts:
                unique_concepts.append(c_clean)
                
        if not unique_concepts:
            return 0.0, [], []
            
        matched = []
        unmatched = []
        
        for concept in unique_concepts:
            is_matched = False
            
            # 1. Check Neo4j Synonyms
            try:
                synonym_matches = self.graph.query(
                    """
                    MATCH (s:Synonym)
                    WHERE replace(toLower(s.name), ' ', '') = replace(toLower($name), ' ', '')
                    MATCH (s)-[:IS_SYNONYM_FOR]->(con:Concept)-[:MAPS_TO]->(t:Table)
                    RETURN t.name as table_name LIMIT 1
                    """,
                    {"name": concept}
                )
                if synonym_matches:
                    is_matched = True
            except Exception as e:
                print(f"Neo4j relevance check failed for '{concept}': {e}")
                
            # 2. Check SQLite Metadata (tables, columns, exact/fuzzy values)
            if not is_matched:
                concept_nospace = "".join(concept.split()).lower()
                try:
                    with self.engine.connect() as conn:
                        # Check table name
                        tbl_res = conn.execute(
                            text("SELECT name FROM meta_tables WHERE replace(LOWER(name), ' ', '') = :t LIMIT 1"),
                            {"t": concept_nospace}
                        ).fetchone()
                        if tbl_res:
                            is_matched = True
                            
                        # Check column name
                        if not is_matched:
                            col_res = conn.execute(
                                text("SELECT name FROM meta_columns WHERE replace(LOWER(name), ' ', '') = :t LIMIT 1"),
                                {"t": concept_nospace}
                            ).fetchone()
                            if col_res:
                                is_matched = True
                                
                        # Check values
                        if not is_matched:
                            val_res = conn.execute(
                                text("SELECT value FROM meta_values WHERE replace(LOWER(value), ' ', '') = :t OR replace(LOWER(value), ' ', '') LIKE :t_like LIMIT 1"),
                                {"t": concept_nospace, "t_like": f"%{concept_nospace}%"}
                            ).fetchone()
                            if val_res:
                                is_matched = True
                except Exception as e:
                    print(f"SQLite relevance check failed for '{concept}': {e}")
            
            # 3. Check Vector DB
            if not is_matched:
                try:
                    vector_results = self.vector.search(concept, n_results=3)
                    if vector_results and 'metadatas' in vector_results and vector_results['metadatas']:
                        for meta in vector_results['metadatas'][0]:
                            if meta.get('type') == 'table' and meta.get('name', '').lower() == concept.lower():
                                is_matched = True
                                break
                            elif meta.get('type') == 'column' and meta.get('name', '').lower() == concept.lower():
                                is_matched = True
                                break
                            elif meta.get('type') == 'value' and meta.get('value', '').lower() == concept.lower():
                                is_matched = True
                                break
                except Exception as e:
                    print(f"Vector relevance check failed for '{concept}': {e}")
                    
            if is_matched:
                matched.append(concept)
            else:
                unmatched.append(concept)
                
        score = len(matched) / len(unique_concepts)
        return score, matched, unmatched

    def retrieve_context(self, intent: Dict[str, Any]) -> tuple[str, list]:
        """
        Retrieves schema context and returns (context_string, mappings_list).
        """
        relevant_tables = set()
        grounding_mappings = [] # To store [User Term -> Object]

        # 1. Concurrent Entity & Intent Resolution (Phase 1)
        futures = []
        with ThreadPoolExecutor() as executor:
            # a. Resolve via Ontology (Neo4j)
            for entity in intent.get("entities", []):
                futures.append(executor.submit(self._resolve_ontology, entity))

            # b. Resolve via Fuzzy Search (Vector DB)
            futures.append(executor.submit(self._resolve_vector_main, intent))

            # c. High-Confidence SQL Matching (Fuzzy SQL search for filters)
            all_terms = set(list(intent.get("entities", [])) + intent.get("metrics", []) + intent.get("filters", []) + intent.get("dimensions", []))
            for term in all_terms:
                futures.append(executor.submit(self._resolve_sqlite_value, term))

            # d. Recursive Deep Search for orphaned metrics/dimensions
            deep_search_terms = intent.get("metrics", []) + intent.get("dimensions", [])
            for term in deep_search_terms:
                futures.append(executor.submit(self._resolve_deep_column, term))

            # Gather results from Phase 1
            for future in as_completed(futures):
                try:
                    tables, mappings = future.result()
                    relevant_tables.update(tables)
                    grounding_mappings.extend(mappings)
                except Exception as e:
                    print(f"Error in candidate lookup thread: {e}")

        # Deduplicate grounding mappings while preserving order
        seen = set()
        deduped_mappings = []
        for m in grounding_mappings:
            if m not in seen:
                seen.add(m)
                deduped_mappings.append(m)
        grounding_mappings = deduped_mappings

        # 1.5. Multi-Hop Join Reasoning & Bridge Table Expansion (Neo4j) (Phase 2)
        join_paths = []
        discovered_joins = set()
        additional_tables = set()

        if len(relevant_tables) > 1:
            tables_list = list(relevant_tables)
            path_futures = []
            
            with ThreadPoolExecutor() as executor:
                # Parallelize shortest path finding queries
                for i in range(len(tables_list)):
                    for j in range(i + 1, len(tables_list)):
                        path_futures.append(executor.submit(self._find_shortest_path, tables_list[i], tables_list[j]))
                
                # Gather intermediate path nodes and adjacent pairs
                adjacent_pairs = set()
                for future in as_completed(path_futures):
                    try:
                        path_nodes = future.result()
                        if path_nodes:
                            for node in path_nodes:
                                additional_tables.add(node)
                            for k in range(len(path_nodes) - 1):
                                ta, tb = path_nodes[k], path_nodes[k+1]
                                pair = tuple(sorted([ta, tb]))
                                adjacent_pairs.add(pair)
                    except Exception as e:
                        print(f"Error in Neo4j pathfinding thread: {e}")
                
                # Concurrently run join detail lookups for adjacent pairs
                join_futures = {}
                for ta, tb in adjacent_pairs:
                    join_futures[executor.submit(self._find_join_details, ta, tb)] = (ta, tb)
                
                for future in as_completed(join_futures):
                    try:
                        details = future.result()
                        join_paths.extend(details)
                    except Exception as e:
                        ta, tb = join_futures[future]
                        print(f"Error in Neo4j join column lookup thread for {ta}-{tb}: {e}")

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
                
                table_type = "VIEW" if res[1] else "TABLE"
                
                # Fetch Columns (no unique_values lookup to save tokens)
                cols = conn.execute(text("SELECT name, type FROM meta_columns WHERE table_name = :t"), {"t": table_name}).fetchall()
                
                # Build clean, super-compact SQL DDL format
                ddl_lines = [f"CREATE {table_type} {table_name} ("]
                col_defs = [f"    {c[0]} {c[1]}" for c in cols]
                ddl_lines.append(",\n".join(col_defs))
                ddl_lines.append(");")
                
                # Format exactly 1 sample row as a SQL comment
                samples = json.loads(res[2])
                if samples:
                    ddl_lines.append(f"-- Sample Row: {json.dumps(samples[0], ensure_ascii=False)}")
                
                context_parts.append("\n".join(ddl_lines))

        # 3. Join Reasoning (Neo4j shortest paths)
        if join_paths:
            context_parts.append("\n### SUGGESTED JOIN PATHS AND CONSTRAINTS")
            context_parts.extend(join_paths)

        return "\n".join(context_parts), grounding_mappings

if __name__ == "__main__":
    pass
