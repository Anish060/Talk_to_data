import json
from app.utils.plan_helper import parse_plan
sample = '''### LOGICAL PLAN
```json
{"steps":["Step1"],"tables_involved":["TableA"],"join_logic":"join A"}
```
### GENERATED SQL
```sql
SELECT * FROM TableA;
```'''
print(parse_plan(sample))
