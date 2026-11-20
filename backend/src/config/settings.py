import os,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CONFIG=json.loads(Path(os.getenv('PROJECT_FILE',str(ROOT/'project.json'))).read_text())
DATABASE_URL=os.getenv('DATABASE_URL','sqlite+pysqlite:///:memory:')
REDIS_URL=os.getenv('REDIS_URL','redis://redis:6379/0')
SECRET=os.getenv('JWT_SECRET','cost-apportion-local-dev-secret')
