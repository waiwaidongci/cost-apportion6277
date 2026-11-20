from pydantic import BaseModel, Field, field_validator


class Login(BaseModel):
    email: str
    password: str


class DataInput(BaseModel):
    data: dict


class RunInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    policy_id: int
    period: str = '全部'


class ReviewInput(BaseModel):
    note: str = Field(min_length=4, max_length=2000)


class ScheduleInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    run_id: int
    interval_seconds: int = Field(ge=5, le=86400)


class WorkEntryInput(BaseModel):
    person: str = Field(min_length=1, max_length=120)
    department: str = Field(min_length=1, max_length=120)
    start: str
    end: str
    entry_id: str | None = Field(default=None, max_length=64)
    base_rev: int = Field(default=0, ge=0)
    op_id: str | None = Field(default=None, max_length=64)

    @field_validator('person', 'department')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError('不能为空')
        return value.strip()

    @field_validator('person', 'department', 'start', 'end')
    @classmethod
    def not_empty(cls, value):
        if not str(value).strip():
            raise ValueError('不能为空')
        return str(value).strip()


class WorkEntryPatch(BaseModel):
    person: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    start: str | None = None
    end: str | None = None
    base_rev: int = Field(default=0, ge=0)
    op_id: str | None = Field(default=None, max_length=64)


class WorkWithdraw(BaseModel):
    base_rev: int = Field(default=0, ge=0)
    op_id: str | None = Field(default=None, max_length=64)
