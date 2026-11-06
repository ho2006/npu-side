from npu_side.policy import POLICY


def score_answer(answer, policy=None):
    policy = policy or POLICY
    aliases = {"智能手表": "智能手表（手环）", "智能手环": "智能手表（手环）",
               "智能手表(手环)": "智能手表（手环）", "手环": "智能手表（手环）"}
    categories = {aliases.get(item, item) for item in answer["categories"]}
    checks = {"categories": categories == set(policy["facts"]["categories"])}
    checks.update({key: answer[key] == value for key, value in policy["facts"].items() if key != "categories"})
    return {"items": checks, "correct": sum(checks.values()), "total": len(checks), "all_correct": all(checks.values())}
