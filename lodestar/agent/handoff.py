"""Small execution checks; the model still resolves topics and writes queries."""
import re

_NEGATED = re.compile(r'(?:不用|不要|无需|别|不必|不再)(?:再|重新|继续)?(?:搜|查|检索|联网)|不联网|\b(?:do not|don.t|no need to) (?:search|browse|look up)\b', re.I)
_REQUEST = re.compile(r'(?:搜|检索|查|找).{0,14}(?:论文|文献|资料|网页|新闻|消息|信息)|(?:去|帮我|请|你|再|先|现在|赶紧).{0,6}(?:搜|检索|查一下|查下)|\b(?:search for|look up|find (?:a |the |some )?papers?)\b', re.I)
_SHORT_SEARCH = re.compile(r'^(?:请|你|帮我|给我|再|先|现在|赶紧|去|那|就|继续|一下|呀|啊|吧|喵|哦|搜|查|检索|找|[\s，。！!?？])+$')
_AGREEMENT = re.compile(r'^(?:ok|okay|好|好的|嗯|嗯嗯|行|可以|继续|去吧)[呀啊吧呢～~!！。\s]*$', re.I)
_CORRECTION = re.compile(r'刚才|刚刚|我说|这个|那个|上面|前面|刚聊|就是')
_PROMISE = re.compile(r'(?:我|lolo|这就|马上|现在|先|接着|会|准备|正在).{0,22}(?:搜|查|检索|找.{0,5}(?:论文|文献)|读(?:一下|论文)|阅读|打开|换.{0,6}(?:路子|方法).{0,6}再试)|\b(?:I.ll|I will|let me|going to) (?:search|look|find|read|browse)', re.I)
_RESULT = re.compile(r'https?://|搜到了|查到了|检索结果|搜索结果|没有找到|没找到|未找到|未检索到|无法|失败|不支持|不能|\b(?:found|could not|cannot|failed|unavailable)\b', re.I)


def promise_only(text):
    return len(text) < 450 and bool(_PROMISE.search(text)) and not _RESULT.search(text)


def retrieval_requested(message, history=(), reference_text=''):
    """Recognize authorization, never turn quoted text into an executable ask."""
    if _NEGATED.search(message) or re.search(r'怎么搜|如何搜|怎么查|教我.{0,8}(?:搜|查)|能.{0,3}(?:搜索|联网).*吗', message):
        return False
    if re.search(r'记忆|任务进度|历史记录|聊天记录|本地文件|项目资料', message) and not re.search(r'论文|文献|网页|新闻|网上|联网|github', message, re.I):
        return False
    if _REQUEST.search(message) or (len(message) < 30 and re.search(r'搜|查|检索', message) and _SHORT_SEARCH.fullmatch(message)):
        return True
    if reference_text or re.search(r'翻译|什么意思|含义|怎么读|词义|定义', message):
        return False
    # A short correction retains the unfinished *user* task. A greeting or a
    # substantive new request ends this handoff instead of reviving old work.
    last = next((m.get('content', '') for m in reversed(history) if m.get('role') == 'assistant'), '')
    unresolved = not last or promise_only(last) or bool(re.search(r'[?？]|which|what topic', last, re.I))
    if len(message) < 80 and _CORRECTION.match(message.strip()) and unresolved:
        return any(_REQUEST.search(m.get('content', '')) for m in history[-6:] if m.get('role') == 'user')
    if _AGREEMENT.fullmatch(message.strip()):
        return promise_only(last)
    return False


def generic_paper_browse(message):
    """Only topic-free recommendations may start with the general AI feed."""
    return bool(re.fullmatch(r'(?:请|帮我|给我|你|再|能不能|可以|随便|新|换|推荐|介绍|找|挑|选|一篇|个|篇|论文|吧|呀|啊|吗|呢|一下|[\s，。！？?])+', message))


def clarification(text, history):
    # With no preceding exchange there is no topic to recover. Asking for it
    # is useful; asking again after the user supplied it is not execution.
    return not history and bool(re.search(r'什么(?:方向|主题)|哪个(?:方向|主题)|哪篇|which|what topic', text, re.I))
