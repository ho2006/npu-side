POLICY = {
    "id": "cn-digital-2026-v1",
    "accessed_at": "2026-10-13",
    "source": "https://www.mofcom.gov.cn/zfxxgk/fdzdgknr/ztfl/gnmygl/art/2025/art_b0f88c7df21c4d7aa74d7408e84f8860.html",
    "source_title": "商务部等5部门关于做好2026年家电以旧换新、数码和智能产品购新补贴工作的通知",
    "facts": {"categories": ["手机", "平板", "智能手表（手环）", "智能眼镜"],
              "price_limit": 6000, "subsidy_percent": 15, "subsidy_cap": 500,
              "per_category_limit": 1},
    "stimuli": {
        "A0": "2026年，个人消费者购买手机、平板、智能手表（手环）、智能眼镜，可享购新补贴。单件销售价格不超过6000元，补贴为扣除各环节优惠后最终销售价格的15%，每人每类限1件，每件最高500元。具体申领按当地实施细则执行。",
        "A1": "2026年数码和智能产品购新补贴\n①对象：个人消费者。\n②品类：手机、平板、智能手表（手环）、智能眼镜。\n③价格条件：单件销售价格不超过6000元。\n④计算基数：扣除各环节优惠后的最终销售价格。\n⑤补贴比例：15%。\n⑥补贴上限：每件500元。\n⑦数量限制：每人每类1件。\n⑧申领方式：按当地实施细则执行。",
    },
}

SURVEY_PROMPT = """请仅依据你的已有知识与本次社交环境回答；不确定的数值填 null，品类不知道填 []。
不查阅外部资料，也不要假设题目暗示了正确答案。仅输出顶层 JSON 对象，以{开始、以}结束，禁止数组，不要 Markdown或额外字段。
问题：2026年数码和智能产品购新补贴适用哪些品类？单件价格上限、补贴百分比、每件金额上限、每人每类件数分别是多少？
假设你已有更换设备需求，你现在表达的购买意向是多少（1=完全不考虑，2=较低，3=中立，4=较高，5=非常高）？原因是什么？
JSON 字段：categories（中文字符串数组）、price_limit（元）、subsidy_percent（百分数）、subsidy_cap（元）、per_category_limit（件）、intention（1到5整数）、reason（原始解释）。"""


import copy
import hashlib
import json


POLICIES = {POLICY["id"]: POLICY}
previous = copy.deepcopy(POLICY)
previous.update(id="cn-digital-2025-v1", accessed_at="2026-10-16",
                source="https://www.mofcom.gov.cn/zcjd/gnmy/art/2025/art_511f2cdf302f41f0b58d28840acde32d.html",
                source_title="商务部2025年手机等数码产品购新补贴组织实施工作专题新闻发布会")
previous["facts"]["categories"] = ["手机", "平板", "智能手表（手环）"]
previous["stimuli"] = {key: text.replace("2026年", "2025年").replace("、智能眼镜", "")
                       for key, text in previous["stimuli"].items()}
POLICIES[previous["id"]] = previous


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def get_policy(policy_id=POLICY["id"]):
    if policy_id not in POLICIES:
        raise ValueError("不支持的政策版本")
    return copy.deepcopy(POLICIES[policy_id])


def policy_for(config):
    return copy.deepcopy(config.get("policy_snapshot") or get_policy(config.get("policy_id", POLICY["id"])))


def survey_prompt(policy):
    return SURVEY_PROMPT if policy["id"] == POLICY["id"] else SURVEY_PROMPT.replace("2026年", "2025年")


def material_manifest(policy):
    return {"policy_id": policy["id"], "policy_sha256": fingerprint(policy),
            "material_sha256": fingerprint(policy["stimuli"]), "survey_sha256": fingerprint(survey_prompt(policy))}
