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
