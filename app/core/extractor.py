from sqlalchemy import create_engine, inspect
from typing import List, Dict, Any
import json

class SchemaExtractor:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        self.inspector = inspect(self.engine)

    def get_schema_summary(self) -> Dict[str, Any]:
        """
        Extracts a comprehensive summary of the database schema.
        """
        schema_data = {
            "tables": []
        }

        table_names = self.inspector.get_table_names()
        view_names = self.inspector.get_view_names()
        for table_name in (table_names + view_names):
            is_view = table_name in view_names
            table_info = {
                "name": table_name,
                "is_view": is_view,
                "columns": [],
                "primary_key": [],
                "foreign_keys": [],
                "indexes": []
            }

            try:
                if not is_view:
                    table_info["primary_key"] = self.inspector.get_pk_constraint(table_name).get("constrained_columns", [])
                    table_info["indexes"] = self.inspector.get_indexes(table_name)
                    table_info["foreign_keys"] = []
                    fks = self.inspector.get_foreign_keys(table_name)
                    for fk in fks:
                        table_info["foreign_keys"].append({
                            "constrained_columns": fk["constrained_columns"],
                            "referred_table": fk["referred_table"],
                            "referred_columns": fk["referred_columns"]
                        })
            except Exception as e:
                print(f"Warning: Could not fetch constraints for {table_name}: {e}")

            # Get columns (works for views too)
            columns = self.inspector.get_columns(table_name)
            for col in columns:
                table_info["columns"].append({
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col["nullable"],
                    "default": col.get("default")
                })

            # NEW: Get Sample Data
            try:
                with self.engine.connect() as conn:
                    from sqlalchemy import text
                    result = conn.execute(text(f"SELECT * FROM \"{table_name}\" LIMIT 3"))
                    samples = []
                    for row in result:
                        row_dict = dict(row._mapping)
                        # Clean non-serializable types (like bytes/BLOB)
                        cleaned_row = {}
                        for k, v in row_dict.items():
                            if isinstance(v, bytes):
                                cleaned_row[k] = "<binary data>"
                            elif hasattr(v, 'isoformat'): # Handle dates
                                cleaned_row[k] = v.isoformat()
                            else:
                                cleaned_row[k] = v
                        samples.append(cleaned_row)
                    table_info["samples"] = samples
            except Exception as e:
                print(f"Warning: Could not fetch samples for {table_name}: {e}")
                table_info["samples"] = []

            # NEW: Get Unique Values for Categorical Columns
            # We target string columns that might contain business entities (Names, Cities, etc.)
            table_info["unique_values"] = {}
            if not is_view:
                try:
                    with self.engine.connect() as conn:
                        for col in table_info["columns"]:
                            col_type_upper = col["type"].upper()
                            if "TEXT" in col_type_upper or "VARCHAR" in col_type_upper or "CHAR" in col_type_upper:
                                # Limit to top 100 unique values to avoid bloating the schema summary
                                v_res = conn.execute(text(f"SELECT DISTINCT \"{col['name']}\" FROM \"{table_name}\" WHERE \"{col['name']}\" IS NOT NULL LIMIT 100"))
                                table_info["unique_values"][col["name"]] = [str(row[0]) for row in v_res]
                except Exception as e:
                    print(f"Warning: Could not fetch unique values for {table_name}: {e}")

            schema_data["tables"].append(table_info)

        # Semantic Relationship Discovery (Fuzzy Joins)
        # Link columns with identical names across tables if no explicit FK exists
        all_pks = {} # {table_name: [pk_cols]}
        for table in schema_data["tables"]:
            if table["primary_key"]:
                all_pks[table["name"]] = table["primary_key"]

        for table in schema_data["tables"]:
            existing_referred = [fk["referred_table"] for fk in table["foreign_keys"]]
            for col in table["columns"]:
                col_name = col["name"]
                # If column name matches a PK in another table, suggest a relationship
                for other_table, pks in all_pks.items():
                    if other_table != table["name"] and col_name in pks:
                        if other_table not in existing_referred:
                            table["foreign_keys"].append({
                                "constrained_columns": [col_name],
                                "referred_table": other_table,
                                "referred_columns": [col_name],
                                "is_semantic": True
                            })
                            existing_referred.append(other_table)

        return schema_data

    def save_schema_to_json(self, filepath: str):
        schema = self.get_schema_summary()
        with open(filepath, 'w') as f:
            json.dump(schema, f, indent=4)
        print(f"Schema saved to {filepath}")

    def save_to_info_db(self, schema_data: Dict[str, Any], info_db_path: str = "data/info.db"):
        """
        Saves the extracted schema metadata into a dedicated relational Info DB.
        """
        from sqlalchemy import Table, Column, String, Integer, Boolean, MetaData, JSON, create_engine
        
        info_engine = create_engine(f"sqlite:///{info_db_path}")
        metadata = MetaData()

        # Define Meta Tables
        meta_tables = Table('meta_tables', metadata,
            Column('id', Integer, primary_key=True),
            Column('name', String, unique=True),
            Column('is_view', Boolean),
            Column('primary_key', JSON),
            Column('samples', JSON)
        )

        meta_columns = Table('meta_columns', metadata,
            Column('id', Integer, primary_key=True),
            Column('table_name', String),
            Column('name', String),
            Column('type', String),
            Column('nullable', Boolean),
            Column('unique_values', JSON)
        )

        meta_values = Table('meta_values', metadata,
            Column('id', Integer, primary_key=True),
            Column('table_name', String),
            Column('column_name', String),
            Column('value', String)
        )

        # Create tables
        metadata.drop_all(info_engine)
        metadata.create_all(info_engine)

        with info_engine.connect() as conn:
            # Insert Tables
            for table in schema_data["tables"]:
                conn.execute(meta_tables.insert().values(
                    name=table["name"],
                    is_view=table["is_view"],
                    primary_key=table["primary_key"],
                    samples=table["samples"]
                ))

                # Insert Columns
                for col in table["columns"]:
                    unique_vals = table.get("unique_values", {}).get(col["name"], [])
                    conn.execute(meta_columns.insert().values(
                        table_name=table["name"],
                        name=col["name"],
                        type=col["type"],
                        nullable=col["nullable"],
                        unique_values=unique_vals
                    ))
                    
                    # Insert into Meta Values for high-speed retrieval
                    for val in unique_vals:
                        conn.execute(meta_values.insert().values(
                            table_name=table["name"],
                            column_name=col["name"],
                            value=val
                        ))
            
            conn.commit()
        print(f"Metadata persisted to Info DB at {info_db_path}")

if __name__ == "__main__":
    # Example usage with sqlite
    import os
    
    # Create a dummy db if not exists for testing
    db_path = "sqlite:///data/test_sample.db"
    extractor = SchemaExtractor(db_path)
    
    # Create sample tables if DB is empty
    from sqlalchemy import Column, Integer, String, ForeignKey, MetaData, Table
    metadata = MetaData()
    
    users = Table('users', metadata,
        Column('id', Integer, primary_key=True),
        Column('name', String),
        Column('email', String, unique=True)
    )
    
    orders = Table('orders', metadata,
        Column('id', Integer, primary_key=True),
        Column('user_id', Integer, ForeignKey('users.id')),
        Column('amount', Integer)
    )
    
    metadata.create_all(extractor.engine)
    
    # Extract
    extractor.save_schema_to_json("data/schema_summary.json")
