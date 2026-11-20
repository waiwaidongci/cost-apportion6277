import json,random
from src.config.database import Base,engine,Session
from src.config.settings import CONFIG
from src.models.identity import User
from src.models.source import Source,Policy,Period
from src.models import run,worktime
from src.utils.security import password_hash
from src.services.validation import validate_source
from src.services import ledger

def demo_rows():
 rng=random.Random(42);rows=[]
 for i in range(240):
  rows.append({'period':'2026-'+('10' if i<120 else '11'),'department':rng.choice(['公共','公共','光学研究','材料研究','工艺研究']),'category':rng.choice(['仪器维护','试验耗材','研究工时']),'amount_cents':rng.randint(1000,500000)})
 return rows

def demo_work_entries():
 """固定 seed 的起止工时：含跨日、同人同日重叠、同人报给多部门。"""
 rng=random.Random(42);people=['林岚','沈阔','周砚','许澄','何叙','苏禾']
 departments=['光学研究','材料研究','工艺研究']
 rows=[];n=0
 for day in ('2026-10-06','2026-10-07','2026-10-08','2026-11-03','2026-11-04','2026-11-05'):
  for person in people:
   start=rng.randint(8,10);hours=rng.randint(2,5)
   rows.append({'person':person,'department':departments[people.index(person)%3],
                'start':f'{day}T{start:02d}:00','end':f'{day}T{start+hours:02d}:00','tag':f'seed-day-{n}'});n+=1
  # 同一人同日重叠申报给另一部门：均分而不是重复计时
  rows.append({'person':'林岚','department':'材料研究','start':f'{day}T09:00','end':f'{day}T12:00','tag':f'seed-overlap-{n}'});n+=1
 # 跨日：23:00 至次日 02:30，必须拆到两个人日
 rows.append({'person':'周砚','department':'工艺研究','start':'2026-11-05T23:00','end':'2026-11-06T02:30','tag':'seed-crossday'})
 rows.append({'person':'沈阔','department':'光学研究','start':'2026-10-08T22:00','end':'2026-10-09T01:00','tag':'seed-crossday-2'})
 return rows

def seed():
 Base.metadata.create_all(engine)
 with Session() as db:
  for role in ('admin','analyst','viewer'):
   if not db.query(User).filter_by(email=role+'@demo.local').first():db.add(User(email=role+'@demo.local',role=role,password=password_hash('Demo2026!')))
  rows=demo_rows()
  for name in sorted(set(r['period'] for r in rows)):
   if not db.query(Period).filter_by(name=name).first():db.add(Period(name=name,description='合成演示业务期间，seed=42'))
  for i,data in enumerate(rows):
   validate_source(data);key='demo-'+str(i+1)
   if not db.query(Source).filter_by(natural_key=key).first():db.add(Source(natural_key=key,data=data))
  if not db.query(Policy).filter_by(version=CONFIG['policy']['version']).first():db.add(Policy(version=CONFIG['policy']['version'],data={'version':CONFIG['policy']['version']}))
  db.commit()
  for part in demo_work_entries():
   entry_id='demo-'+part['tag']
   if db.query(worktime.WorkEntryOp).filter_by(entry_id=entry_id).first():continue
   ledger.append(db,'seed','create',entry_id,{k:part[k] for k in ('person','department','start','end')},op_id=entry_id)
  db.commit()
  ledger.apply_pending(db,actor='seed')
  print(json.dumps({'users':db.query(User).count(),'sources':db.query(Source).count(),'policies':db.query(Policy).count(),'periods':db.query(Period).count(),'work_entries':db.query(worktime.WorkEntryOp).filter_by(kind='create').count(),'ledger_gen':ledger.ledger_view(db)['gen']},ensure_ascii=False))
if __name__=='__main__':seed()
