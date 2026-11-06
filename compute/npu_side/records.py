from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HumanAnswer(StrictModel):
    categories: list[str] = Field(max_length=10)
    price_limit: int | None = Field(ge=0, le=100000)
    subsidy_percent: int | None = Field(ge=0, le=100)
    subsidy_cap: int | None = Field(ge=0, le=100000)
    per_category_limit: int | None = Field(ge=0, le=100)
    intention: int | None = Field(ge=1, le=5)
    missing_fields: list[Literal["categories", "price_limit", "subsidy_percent", "subsidy_cap", "per_category_limit", "intention"]] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def no_identifiers(self):
        allowed = {"手机", "平板", "智能手表", "智能手表（手环）", "智能手表(手环)", "智能手环", "手环", "智能眼镜", "电脑", "电视", "其他"}
        if any(v not in allowed for v in self.categories):
            raise ValueError("品类仅接受已标准化的选项；原始自由文本请受控留存，不导入身份信息")
        if len(set(self.missing_fields)) != len(self.missing_fields):
            raise ValueError("缺失字段不能重复")
        for name in self.missing_fields:
            if getattr(self, name) not in (None, []):
                raise ValueError("主动跳过的字段必须为空，不能同时提供答案")
        if self.intention is None and "intention" not in self.missing_fields:
            raise ValueError("缺失意向须明确标记，不能补成中立")
        return self


class Participant(StrictModel):
    participant_id: str = Field(pattern=r"^p_[0-9a-f]{16,32}$")
    condition: Literal["A0", "A1"]
    consented: bool
    eligible: bool
    split: Literal["development", "heldout"]
    status: Literal["completed", "incomplete", "withdrawn"]
    pre: HumanAnswer | None
    post: HumanAnswer | None

    @model_validator(mode="after")
    def completeness(self):
        if (not self.consented or not self.eligible or self.status == "withdrawn") and (self.pre or self.post):
            raise ValueError("未同意/不符合资格/已撤回者不得附带问卷答案")
        if self.status == "completed" and (not self.pre or not self.post or self.pre.missing_fields or self.post.missing_fields):
            raise ValueError("不完整或主动跳过的问卷不能标记为completed")
        return self


class CalibrationData(StrictModel):
    source_kind: Literal["fixture", "human"]
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    survey_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    participants: list[Participant] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_people(self):
        ids = [p.participant_id for p in self.participants]
        if len(ids) != len(set(ids)):
            raise ValueError("参与者ID重复；不得重复计数")
        return self
