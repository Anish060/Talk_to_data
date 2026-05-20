import sqlite3
import pandas as pd
import os

db_path = "data/crm.sqlite"
if os.path.exists(db_path):
    os.remove(db_path)

conn = sqlite3.connect(db_path)

# Map each CSV under data/CRM
csv_files = {
    "accounts": "data/CRM/accounts.csv",
    "products": "data/CRM/products.csv",
    "sales_teams": "data/CRM/sales_teams.csv",
    "sales_pipeline": "data/CRM/sales_pipeline.csv"
}

pks = {
    "accounts": "account",
    "products": "product",
    "sales_teams": "sales_agent",
    "sales_pipeline": "opportunity_id"
}

for table_name, csv_path in csv_files.items():
    print(f"Loading {csv_path} to table {table_name}...")
    df = pd.read_csv(csv_path)
    
    pk_col = pks[table_name]
    
    # Infer column types to build a simple CREATE TABLE query
    col_def = []
    for col in df.columns:
        col_type = "TEXT"
        # Map pandas types to SQLite types
        if pd.api.types.is_integer_dtype(df[col]):
            col_type = "INTEGER"
        elif pd.api.types.is_float_dtype(df[col]):
            col_type = "REAL"
        
        # Clean col name if it has leading/trailing whitespaces
        col_name = col.strip()
        
        if col_name == pk_col:
            col_def.append(f'"{col_name}" {col_type} PRIMARY KEY')
        else:
            col_def.append(f'"{col_name}" {col_type}')
            
    create_ddl = f"CREATE TABLE \"{table_name}\" ({', '.join(col_def)})"
    conn.execute(create_ddl)
    
    # Clean the dataframe column names to match DB
    df.columns = [c.strip() for c in df.columns]
    
    # Insert data
    df.to_sql(table_name, conn, if_exists="append", index=False)

conn.commit()
conn.close()
print("CRM database created at data/crm.sqlite successfully!")

