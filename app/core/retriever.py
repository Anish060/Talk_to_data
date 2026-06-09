import json
from typing import List, Dict, Any
from sqlalchemy import create_engine, text
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient
from app.core.ontology_resolver import OntologyResolver
from concurrent.futures import ThreadPoolExecutor, as_completed


class ContextRetriever:
    def __init__(self, neo4j_client: Neo4jClient, vector_client: VectorClient):
        self.graph  = neo4j_client
        self.vector = vector_client
        self.info_db_path = "data/info.db"
        self.engine = create_engine(f"sqlite:///{self.info_db_path}")

        # Ontology Catalogue resolver — used as primary resolution path in
        # _resolve_ontology() before falling back to legacy Concept/Synonym nodes.
        self._ontology_resolver = OntologyResolver(neo4j_client)

    # ──────────────────────────────────────────────────────────────────────────
    # Logging helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _log_neo4j(self, label: str, result):
        try:
            compact = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            compact = str(result)
        print(f"[NEO4J] {label} → {compact}")

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 1 resolvers
    # ──────────────────────────────────────────────────────────────────────────

    def _resolve_ontology(self, entity: str) -> tuple[set[str], list[str]]:
        """
        Resolve an entity/concept string to table names and grounding mappings.

        Priority order:
          1. OntologyCatalogue (OntologyConcept nodes from ontology_catalogue.json)
             — returns column-level aliases and pre-built SQL expressions.
          2. Legacy Concept/Synonym → Table path (pre-catalogue behaviour).

        Returns (tables: set[str], mappings: list[str]).
        Mappings are plain strings formatted for injection into the LLM context.
        """
        tables   = set()
        mappings = []

        # ── 1. Ontology Catalogue (new primary path) ──────────────────────────
        try:
            resolution = self._ontology_resolver.resolve(entity)
        except Exception as e:
            print(f"[ContextRetriever] OntologyResolver error for '{entity}': {e}")
            resolution = None

        if resolution and resolution.resolved:
            for tbl in resolution.tables:
                tables.add(tbl)
            mappings.append(resolution.to_grounding_string())
            return tables, mappings

        # ── 2. Legacy Concept/Synonym → Table ────────────────────────────────
        try:
            synonym_matches = self.graph.query(
                """
                MATCH (s:Synonym)
                WHERE replace(toLower(s.name), ' ', '') = replace(toLower($name), ' ', '')
                  AND NOT (s)-[:IS_SYNONYM_FOR]->(:OntologyConcept)
                MATCH (s)-[:IS_SYNONYM_FOR]->(con:Concept)-[:MAPS_TO]->(t:Table)
                RETURN t.name as table_name, con.name as concept
                """,
                {"name": entity},
            )
            self._log_neo4j("ontology_legacy", synonym_matches)
            if synonym_matches:
                for m in synonym_matches:
                    tables.add(m["table_name"])
                    mappings.append(
                        f"Business Term: '{entity}' maps to Table '{m['table_name']}'"
                    )
        except Exception as e:
            print(f"[ContextRetriever] Legacy ontology query failed for '{entity}': {e}")

        return tables, mappings

    def _resolve_vector_main(self, intent: Dict[str, Any]) -> tuple[set[str], list[str]]:
        tables   = set()
        mappings = []
        search_terms = (
            list(intent.get("entities", []))
            + intent.get("metrics", [])
            + intent.get("filters", [])
            + intent.get("dimensions", [])
        )
        if not search_terms:
            return tables, mappings
        search_query = " ".join(search_terms)

        vector_results = self.vector.search(search_query, n_results=15)
        if vector_results and "metadatas" in vector_results and vector_results["metadatas"]:
            for meta in vector_results["metadatas"][0]:
                if meta["type"] == "table":
                    tables.add(meta["name"])
                elif meta["type"] == "column":
                    tables.add(meta["table"])
                    mappings.append(
                        f"Field Match: Field '{meta['name']}' found in Table '{meta['table']}'"
                    )
                elif meta["type"] == "value":
                    tables.add(meta["table"])
                    mappings.append(
                        f"Value Resolution: Found '{meta['value']}' "
                        f"in {meta['table']}.{meta['column']}"
                    )
        return tables, mappings

    def _resolve_sqlite_value(self, term: str) -> tuple[set[str], list[str]]:
        """Exact and fuzzy value lookup in the Info DB meta_values table."""
        tables   = set()
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
                res = conn.execute(
                    sql, {"t": term_nospace, "t_like": f"%{term_nospace}%"}
                ).fetchall()
                for r in res:
                    tables.add(r[0])
                    mappings.append(
                        f"Value Lock: Term '{term}' maps to Exact DB Value "
                        f"'{r[2]}' in {r[0]}.{r[1]}"
                    )
        except Exception as e:
            print(f"⚠️ SQLite lookup failed for term '{term}': {e}")
        return tables, mappings

    def _resolve_deep_column(self, term: str) -> tuple[set[str], list[str]]:
        tables   = set()
        mappings = []
        col_search = self.vector.search(f"database object named {term}", n_results=3)
        if col_search and "metadatas" in col_search and col_search["metadatas"]:
            for meta in col_search["metadatas"][0]:
                if meta["type"] == "column":
                    tables.add(meta["table"])
                    mappings.append(
                        f"Deep Resolve: Term '{term}' matches Column "
                        f"'{meta['name']}' in Table '{meta['table']}'"
                    )
        return tables, mappings

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 2 — Multi-hop join reasoning
    # ──────────────────────────────────────────────────────────────────────────

    def _find_shortest_path(self, t1: str, t2: str) -> list[str]:
        path_results = self.graph.query(
            """
            MATCH p = shortestPath(
                (t1:Table {name: $t1})-[:REFERENCES_TABLE*..5]-(t2:Table {name: $t2})
            )
            RETURN [node in nodes(p) | node.name] as path_nodes
            """,
            {"t1": t1, "t2": t2},
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
            RETURN ca.name as col_a, cb.name as col_b,
                   (r1 IS NOT NULL) as ta_is_detail
            """,
            {"ta": ta, "tb": tb},
        )
        self._log_neo4j("join_details", join_cols)
        paths = []
        if join_cols:
            for jc in join_cols:
                col_a        = jc["col_a"]
                col_b        = jc["col_b"]
                ta_is_detail = jc["ta_is_detail"]
                if ta_is_detail:
                    paths.append(
                        f"- To join {ta} and {tb}: Use `{ta}.{col_a} = {tb}.{col_b}`"
                    )
                    paths.append(
                        f"  * CONSTRAINT: '{tb}' is a Header table relative to "
                        f"the Detail table '{ta}'. Do not SUM() aggregate columns "
                        f"from '{tb}' directly after joining."
                    )
                else:
                    paths.append(
                        f"- To join {tb} and {ta}: Use `{tb}.{col_b} = {ta}.{col_a}`"
                    )
                    paths.append(
                        f"  * CONSTRAINT: '{ta}' is a Header table relative to "
                        f"the Detail table '{tb}'. Do not SUM() aggregate columns "
                        f"from '{ta}' directly after joining."
                    )
        return paths

    # ──────────────────────────────────────────────────────────────────────────
    # Relevance check (used by the legacy routes.py relevance scorer)
    # ──────────────────────────────────────────────────────────────────────────

    def check_relevance(self, intent: Dict[str, Any]) -> tuple[float, list[str], list[str]]:
        concepts = []
        for key in ["entities", "metrics", "filters", "dimensions"]:
            if key in intent and isinstance(intent[key], list):
                concepts.extend(intent[key])

        unique_concepts = []
        for c in concepts:
            c_clean = str(c).strip()
            if c_clean and c_clean not in unique_concepts:
                unique_concepts.append(c_clean)

        if not unique_concepts:
            return 0.0, [], []

        matched   = []
        unmatched = []

        from app.state import get_catalogue, USE_CLIENT_DOC
        trigger_phrases: set = set()
        if USE_CLIENT_DOC:
            catalogue = get_catalogue()
            if catalogue and "rules" in catalogue:
                for rule in catalogue["rules"]:
                    for phrase in rule.get("trigger_phrases", []):
                        trigger_phrases.add(phrase.lower())

        ANALYTICAL_KEYWORDS = {
            "percentage", "percent", "contribution", "share", "proportion", "ratio", "rate",
            "total", "sum", "average", "avg", "count", "number of", "distinct",
            "by", "each", "per", "group by", "sort by", "order by", "top", "limit",
            "increase", "decrease", "growth", "trend", "change", "diff", "difference",
            "monthly", "yearly", "quarterly", "daily", "annual", "ranking", "rank",
        }

        def is_word_in_schema(word: str) -> bool:
            word_nospace = "".join(word.split()).lower()
            if not word_nospace:
                return False
            if word_nospace in ANALYTICAL_KEYWORDS:
                return True
            try:
                with self.engine.connect() as conn:
                    if conn.execute(
                        text("SELECT name FROM meta_tables WHERE replace(LOWER(name),' ','')=:t LIMIT 1"),
                        {"t": word_nospace},
                    ).fetchone():
                        return True
                    if conn.execute(
                        text("SELECT name FROM meta_columns WHERE replace(LOWER(name),' ','')=:t LIMIT 1"),
                        {"t": word_nospace},
                    ).fetchone():
                        return True
            except Exception:
                pass
            try:
                neo4j_res = self.graph.query(
                    """
                    MATCH (n)
                    WHERE (n:Table OR n:Column OR n:Concept OR n:Synonym OR n:OntologyConcept)
                      AND replace(toLower(n.name), ' ', '') = replace(toLower($name), ' ', '')
                    RETURN n.name LIMIT 1
                    """,
                    {"name": word},
                )
                if neo4j_res:
                    return True
            except Exception:
                pass
            return False

        for concept in unique_concepts:
            concept_lower = concept.lower()
            is_matched    = False

            # A. Ontology catalogue (new — highest priority)
            if not is_matched:
                try:
                    resolution = self._ontology_resolver.resolve(concept)
                    if resolution.resolved:
                        is_matched = True
                except Exception:
                    pass

            # B. Analytical keywords
            if not is_matched and concept_lower in ANALYTICAL_KEYWORDS:
                is_matched = True

            # C. Client doc trigger phrases
            if not is_matched and trigger_phrases:
                for phrase in trigger_phrases:
                    if concept_lower == phrase or concept_lower in phrase or phrase in concept_lower:
                        is_matched = True
                        break

            # D. Neo4j (Tables, Columns, Concepts, Synonyms, OntologyConcepts)
            if not is_matched:
                try:
                    neo4j_res = self.graph.query(
                        """
                        MATCH (n)
                        WHERE (n:Table OR n:Column OR n:Concept OR n:Synonym OR n:OntologyConcept)
                          AND replace(toLower(n.name), ' ', '') = replace(toLower($name), ' ', '')
                        RETURN n.name LIMIT 1
                        """,
                        {"name": concept},
                    )
                    if neo4j_res:
                        is_matched = True
                except Exception as e:
                    print(f"Neo4j relevance check failed for '{concept}': {e}")

            # E. SQLite metadata
            if not is_matched:
                concept_nospace = "".join(concept.split()).lower()
                try:
                    with self.engine.connect() as conn:
                        if conn.execute(
                            text("SELECT name FROM meta_tables WHERE replace(LOWER(name),' ','')=:t LIMIT 1"),
                            {"t": concept_nospace},
                        ).fetchone():
                            is_matched = True
                        if not is_matched and conn.execute(
                            text("SELECT name FROM meta_columns WHERE replace(LOWER(name),' ','')=:t LIMIT 1"),
                            {"t": concept_nospace},
                        ).fetchone():
                            is_matched = True
                        if not is_matched and conn.execute(
                            text("SELECT value FROM meta_values WHERE replace(LOWER(value),' ','')=:t OR replace(LOWER(value),' ','') LIKE :tl LIMIT 1"),
                            {"t": concept_nospace, "tl": f"%{concept_nospace}%"},
                        ).fetchone():
                            is_matched = True
                except Exception as e:
                    print(f"SQLite relevance check failed for '{concept}': {e}")

            # F. Vector DB
            if not is_matched:
                try:
                    vector_results = self.vector.search(concept, n_results=5)
                    if vector_results and "metadatas" in vector_results and vector_results["metadatas"]:
                        for meta in vector_results["metadatas"][0]:
                            meta_name = (meta.get("name") or meta.get("value") or "").lower()
                            if meta_name and (meta_name in concept_lower or concept_lower in meta_name):
                                is_matched = True
                                break
                except Exception as e:
                    print(f"Vector relevance check failed for '{concept}': {e}")

            # G. Multi-word decomposition
            if not is_matched and " " in concept_lower:
                words = [w.strip() for w in concept_lower.split() if w.strip()]
                if words and all(is_word_in_schema(w) for w in words):
                    is_matched = True

            if is_matched:
                matched.append(concept)
            else:
                unmatched.append(concept)

        score = len(matched) / len(unique_concepts) if unique_concepts else 0.0
        return score, matched, unmatched

    # ──────────────────────────────────────────────────────────────────────────
    # Primary entry point
    # ──────────────────────────────────────────────────────────────────────────

    def retrieve_context(self, intent: Dict[str, Any]) -> tuple[str, list]:
        """
        Retrieves schema context and returns (context_string, mappings_list).

        The context_string is injected directly into the LLM prompt and contains:
          - ONTOLOGY COLUMN ALIASES  (new — authoritative column-level mappings)
          - SEMANTIC VALUE MAPPINGS  (existing — categorical value locks)
          - CANONICAL SCHEMA DEFINITIONS  (DDL + sample rows)
          - SUGGESTED JOIN PATHS AND CONSTRAINTS  (Neo4j shortest-path results)
        """
        relevant_tables    = set()
        grounding_mappings = []

        # ── Phase 1: Concurrent resolution ────────────────────────────────────
        futures = []
        with ThreadPoolExecutor() as executor:

            # a. Concept resolution via OntologyCatalogue → legacy fallback
            #    Must cover entities, metrics, AND dimensions so that
            #    column-level aliases (e.g. "customer name" → Customers.ContactName)
            #    reach the ONTOLOGY COLUMN ALIASES section of the LLM context.
            ontology_concepts = dict.fromkeys(
                list(intent.get("entities", []))
                + intent.get("metrics", [])
                + intent.get("dimensions", [])
            )  # dict.fromkeys preserves order and deduplicates
            for concept in ontology_concepts:
                futures.append(executor.submit(self._resolve_ontology, concept))

            # b. Fuzzy semantic search (Vector DB)
            futures.append(executor.submit(self._resolve_vector_main, intent))

            # c. Categorical value matching (Info DB)
            all_terms = set(
                list(intent.get("entities", []))
                + intent.get("metrics", [])
                + intent.get("filters", [])
                + intent.get("dimensions", [])
            )
            for term in all_terms:
                futures.append(executor.submit(self._resolve_sqlite_value, term))

            # d. Deep column search for metrics / dimensions
            for term in intent.get("metrics", []) + intent.get("dimensions", []):
                futures.append(executor.submit(self._resolve_deep_column, term))

            for future in as_completed(futures):
                try:
                    tables, mappings = future.result()
                    relevant_tables.update(tables)
                    grounding_mappings.extend(mappings)
                except Exception as e:
                    print(f"Error in candidate lookup thread: {e}")

        # Deduplicate grounding mappings while preserving insertion order
        seen = set()
        deduped_mappings = []
        for m in grounding_mappings:
            if m not in seen:
                seen.add(m)
                deduped_mappings.append(m)
        grounding_mappings = deduped_mappings

        # ── Phase 2: Multi-hop join reasoning ─────────────────────────────────
        join_paths        = []
        additional_tables = set()

        if len(relevant_tables) > 1:
            tables_list   = list(relevant_tables)
            path_futures  = []

            with ThreadPoolExecutor() as executor:
                for i in range(len(tables_list)):
                    for j in range(i + 1, len(tables_list)):
                        path_futures.append(
                            executor.submit(
                                self._find_shortest_path, tables_list[i], tables_list[j]
                            )
                        )

                adjacent_pairs: set = set()
                for future in as_completed(path_futures):
                    try:
                        path_nodes = future.result()
                        if path_nodes:
                            for node in path_nodes:
                                additional_tables.add(node)
                            for k in range(len(path_nodes) - 1):
                                ta, tb = path_nodes[k], path_nodes[k + 1]
                                adjacent_pairs.add(tuple(sorted([ta, tb])))
                    except Exception as e:
                        print(f"Error in Neo4j pathfinding thread: {e}")

                join_futures = {}
                for ta, tb in adjacent_pairs:
                    join_futures[executor.submit(self._find_join_details, ta, tb)] = (ta, tb)

                for future in as_completed(join_futures):
                    try:
                        details = future.result()
                        join_paths.extend(details)
                    except Exception as e:
                        ta, tb = join_futures[future]
                        print(f"Error in Neo4j join column lookup for {ta}-{tb}: {e}")

            relevant_tables.update(additional_tables)

        # ── Phase 3: Build context string ─────────────────────────────────────
        context_parts = []

        # Section A: Ontology Column Aliases (NEW — must appear first and prominently)
        ontology_aliases = [
            m for m in grounding_mappings
            if m.startswith("Column Alias")
            or m.startswith("Metric Alias")
            or m.startswith("Entity Alias")
        ]
        if ontology_aliases:
            context_parts.append(
                "### ONTOLOGY COLUMN ALIASES (MANDATORY — DO NOT SUBSTITUTE)\n"
                "These mappings are authoritative. For each user term listed below,\n"
                "you MUST use exactly the specified column(s) or SQL expression.\n"
                "You are FORBIDDEN from substituting any other column from the same table,\n"
                "even if the column name appears more similar to the user's term."
            )
            for alias in ontology_aliases:
                context_parts.append(f"- {alias}")

        # Section B: Semantic value mappings (existing)
        non_ontology_mappings = [
            m for m in grounding_mappings
            if not m.startswith("Column Alias")
            and not m.startswith("Metric Alias")
            and not m.startswith("Entity Alias")
        ]
        context_parts.append("\n### SEMANTIC VALUE MAPPINGS (MANDATORY)")
        context_parts.append(
            "The following mappings resolve your query terms to exact database strings. "
            "Use the 'Exact Value' in your SQL filters:"
        )
        for gm in non_ontology_mappings:
            context_parts.append(f"- {gm}")

        # Section C: Schema DDL
        context_parts.append("\n### CANONICAL SCHEMA DEFINITIONS")

        with self.engine.connect() as conn:
            for table_name in relevant_tables:
                res = conn.execute(
                    text(
                        "SELECT name, is_view, samples FROM meta_tables "
                        "WHERE LOWER(name) = LOWER(:t)"
                    ),
                    {"t": table_name},
                ).fetchone()
                if not res:
                    continue

                table_type = "VIEW" if res[1] else "TABLE"
                cols = conn.execute(
                    text("SELECT name, type FROM meta_columns WHERE table_name = :t"),
                    {"t": table_name},
                ).fetchall()

                ddl_lines = [f"CREATE {table_type} {table_name} ("]
                col_defs  = [f"    {c[0]} {c[1]}" for c in cols]
                ddl_lines.append(",\n".join(col_defs))
                ddl_lines.append(");")

                samples = json.loads(res[2])
                if samples:
                    ddl_lines.append(
                        f"-- Sample Row: {json.dumps(samples[0], ensure_ascii=False)}"
                    )

                context_parts.append("\n".join(ddl_lines))

        # Section D: Join paths
        if join_paths:
            context_parts.append("\n### SUGGESTED JOIN PATHS AND CONSTRAINTS")
            context_parts.extend(join_paths)

        return "\n".join(context_parts), grounding_mappings
