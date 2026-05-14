from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

load_dotenv()

def seed_database():
    db_url = os.getenv("DATABASE_URL", "sqlite:///data/test_sample.db")
    engine = create_engine(db_url)
    
    with engine.connect() as conn:
        # Clear existing data
        conn.execute(text("DELETE FROM orders"))
        conn.execute(text("DELETE FROM users"))
        
        # Insert Users
        conn.execute(text("INSERT INTO users (id, name, email) VALUES (1, 'John Doe', 'john@example.com')"))
        conn.execute(text("INSERT INTO users (id, name, email) VALUES (2, 'Jane Smith', 'jane@example.com')"))
        
        # Insert Orders
        conn.execute(text("INSERT INTO orders (id, user_id, amount) VALUES (1, 1, 100)"))
        conn.execute(text("INSERT INTO orders (id, user_id, amount) VALUES (2, 1, 250)"))
        conn.execute(text("INSERT INTO orders (id, user_id, amount) VALUES (3, 2, 500)"))
        
        conn.commit()
        print("Database seeded with sample data.")

if __name__ == "__main__":
    seed_database()
