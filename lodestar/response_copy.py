"""Friendly conversation copy, with actionable and truthful failure reasons."""

STOPPED = '好，lolo 先停在这里。已经写下的内容给你留着～'
ERRORS = {
    'action_incomplete': '这次 lolo 只说要查，却没完成工具调用。这轮没有算作查完，可以点“重新生成”再试一次。',
    'timeout': '这次等得有点久，模型服务没有及时返回完整回复。可以点“重新生成”再试一次。',
    'connection': '这次没连上模型服务。你的问题还在，检查网络或服务地址后再试试。',
    'stream_interrupted': '这轮对话中途断线了，已经收到的内容会保留。点“重新生成”，lolo 再来一次。',
    'upstream_stream_error': '模型服务在回复途中报错了，已生成的内容会保留。可以重新生成。',
    'authentication': '模型服务没有放行这次请求。先检查密钥、权限或服务配置，再叫 lolo 试一次。',
    'rate_limit': '模型服务现在有些忙，触发了限流。稍等一会儿再试，lolo 不会偷偷重复请求。',
    'request_rejected': '这次请求被模型服务拦住了。可以重新生成，或把问题换一种说法；这不一定是密钥失效。',
    'quota_exhausted': '这个模型的免费额度用完啦。请到模型设置换一个可用模型；不会自动转为付费调用。',
    'model_unavailable': '这个模型目前不可用。去模型设置选一个可用的，我们再继续。',
    'configuration': '模型与推理配置没有对上。请到模型设置检查一下，再重新生成。',
    'truncated': '这次写到输出长度上限了，回答还没完成。已收到的内容会保留，可以补充问题继续聊。',
    'invalid_json': '模型返回的数据格式没对上，自动修复也没成功。可以重新生成再试一次。',
    'tool_budget': '这轮可用的检索次数已经用完，结论还没整理好。可以缩小问题范围后再试。',
    'http_error': '模型服务返回了错误，这轮没能完成。可以检查模型配置，再重新生成。',
}


def error_message(diagnostic):
    text = ERRORS.get(diagnostic.get('code'), '这次没能顺利接上。你的问题还在，可以重新生成，让 lolo 再试一次。')
    status = diagnostic.get('http_status')
    if status is not None:
        text += f'（HTTP {status}）'
    return text
