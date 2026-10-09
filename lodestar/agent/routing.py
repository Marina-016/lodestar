"""Conservative natural-language routing. Ambiguity never grants write authority."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Route:
    intent: str
    reason: str
    supplement: bool = False


def route(message: str) -> Route:
    text = message.strip().casefold()
    if re.search(r'不要.*(检索|搜索|方案)|不用.*(检索|搜索|方案)|do not (search|generate)', text):
        return Route('followup', '用户限制动作；保守沿用上下文')
    if re.search(r'有什么新|最新|新进展|今天.*技术|重新检索|search.*new|latest|new papers', text):
        return Route('research', '用户要求获取新信息')
    if re.search(r'项目.*方案|生成.*方案|用在.*项目|apply.*project', text):
        return Route('plan', '用户要求结合项目')
    # Only standalone acknowledgements are feedback, not questions about meaning.
    if re.fullmatch(r'(我)?(懂了|理解了|明白了|了解了)[。！!\s]*', text):
        return Route('feedback', '用户自评；不代表已验证掌握')
    if re.search(r'具体|怎么做|如何实现|实验设置|局限|哪个章节|method|implement|limitation', text):
        return Route('followup', '用户询问方法细节，需要检查正文覆盖', True)
    return Route('followup', '保守沿用已有上下文；不自动写记忆')
