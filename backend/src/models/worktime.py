from sqlalchemy import Column, Integer, String, JSON, Float, UniqueConstraint
from src.config.database import Base


class WorkEntryOp(Base):
    """工时更正操作日志：只追加，不原地覆盖。"""
    __tablename__ = 'workentryops'
    id = Column(Integer, primary_key=True)
    entry_id = Column(String(64), index=True, nullable=False)
    rev = Column(Integer, nullable=False)
    op_id = Column(String(64), unique=True, nullable=False)
    kind = Column(String(20), nullable=False)      # create / update / withdraw
    base_rev = Column(Integer, default=0)
    changes = Column(JSON, nullable=False)
    actor = Column(String(120))
    at = Column(Float)


class WorkLedger(Base):
    """人日台账：每条 = 一名人员在一个自然日内、一个部门的精确秒数。"""
    __tablename__ = 'workledger'
    id = Column(Integer, primary_key=True)
    person = Column(String(120), nullable=False)
    day = Column(String(10), nullable=False)          # YYYY-MM-DD，上海时区
    department = Column(String(120), nullable=False)
    seconds_num = Column(Integer, nullable=False)      # Fraction 分子
    seconds_den = Column(Integer, nullable=False)      # Fraction 分母
    gen = Column(Integer, nullable=False, default=0)   # 所属台账版本
    __table_args__ = (UniqueConstraint('gen', 'person', 'day', 'department', name='uq_ledger_cell'),)


class LedgerGen(Base):
    """台账版本：整批写满并校验后才切换 active。"""
    __tablename__ = 'ledgergens'
    id = Column(Integer, primary_key=True)
    gen = Column(Integer, unique=True, nullable=False)
    status = Column(String(20), nullable=False)       # building / active / retired / failed
    parent_gen = Column(Integer)
    scope = Column(JSON)                             # 本次重算的人日键列表
    checksum = Column(String(64))
    rows = Column(Integer, default=0)
    created = Column(Float)
    activated = Column(Float)


class WorkRecompute(Base):
    """一次增量重算的审计记录，用于核对全量/增量一致。"""
    __tablename__ = 'workrecomputes'
    id = Column(Integer, primary_key=True)
    gen = Column(Integer, nullable=False)
    parent_gen = Column(Integer)
    affected = Column(JSON)
    conflicts = Column(JSON)
    result = Column(String(20))                       # switched / noop / failed
    note = Column(String(250))
    created = Column(Float)


class LedgerCursor(Base):
    """已投影到的操作序号与当前生效台账版本。"""
    __tablename__ = 'ledgercursors'
    id = Column(Integer, primary_key=True)
    name = Column(String(40), unique=True, nullable=False)
    last_op_id = Column(Integer, default=0)
    active_gen = Column(Integer, default=0)
    full_checksum = Column(String(64))
    updated = Column(Float)
