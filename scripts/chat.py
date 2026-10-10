"""Interactive terminal for the current ConversationAgent backend."""
import argparse
from dataclasses import replace
from pathlib import Path

from lodestar.agent.conversation import ConversationAgent
from lodestar.config import load_config
from lodestar.chat_settings import PROFILES, command_settings, configure, describe
from lodestar.context import Workspace
from lodestar.llm import LLMClient, LLMError


HELP = '''/settings 查看当前配置；/models 查看本地档位；/models remote 查询服务模型目录
/profile fast|balanced|deep 切换预算/思考预设（可绑定不同模型）
/model 模型ID 切换同一服务的模型；/thinking on [预算]、/thinking off
/thinking effort [low|high|max]（兼容服务）；/thinking adaptive [low|medium|high|max]
/new 新对话；/exit 退出；/help 查看帮助
切换不清空上下文，不写入 .env；深度思考会增加延迟和费用。
参数是否受支持由服务端决定，不会静默降级为关闭思考。'''


def switch_config(agent, config):
    """Construct before swapping so a failed switch leaves the old client usable."""
    replacement = LLMClient(config)
    previous = agent.llm
    agent.llm, agent.ws.config = replacement, config
    previous.close()


def add_model_arguments(parser):
    parser.add_argument('--profile', choices=PROFILES)
    parser.add_argument('--model', help='Model ID on the currently configured provider')
    parser.add_argument('--thinking', choices=('on', 'off', 'effort', 'adaptive'))
    parser.add_argument('--thinking-budget', type=int)
    parser.add_argument('--max-tokens', type=int)
    parser.add_argument('--operations', type=int)
    parser.add_argument('--effort', choices=('low', 'medium', 'high', 'max'))


def argument_settings(cfg, args):
    return configure(cfg, profile=args.profile, model=args.model, thinking=args.thinking,
        thinking_budget=args.thinking_budget, max_tokens=args.max_tokens,
        operations=args.operations, effort=args.effort)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_model_arguments(parser)
    args = parser.parse_args()
    try:
        cfg = argument_settings(load_config(), args)
    except ValueError as error:
        parser.error(str(error))
    root = Path(__file__).resolve().parents[1] / 'workspace' / 'interactive-test'
    cfg = replace(cfg, db_path=root / 'chat.db', workspace_dir=root,
                  demo_replay=False)
    ws = Workspace(cfg)
    try:
        agent = ConversationAgent(ws, LLMClient(cfg))
        session = agent.start()
        print(f'Lodestar 本地对话测试 | {cfg.llm_mode} | {cfg.model}', flush=True)
        print(describe(cfg), flush=True)
        print('输入问题后回车。/help 查看命令；/new 新对话，/exit 退出。\n'
              '此入口直接调用新后端；测试记录保存在 workspace/interactive-test。', flush=True)
        while True:
            try:
                message = input('\n你 > ').strip()
            except (EOFError, KeyboardInterrupt):
                break
            if message == '/exit':
                break
            if message == '/new':
                session = agent.start()
                print('已开始新对话。', flush=True)
                continue
            if message == '/models remote':
                try:
                    print('\n'.join(agent.llm.list_models()), flush=True)
                    print('以上是服务目录，最多100条；具体模型和思考参数仍需实际请求验证。', flush=True)
                except LLMError:
                    print('当前服务未能读取模型目录。仍可用 /model 模型ID 切换。', flush=True)
                continue
            if message in ('/help', '/settings', '/models'):
                if message == '/help':
                    print(HELP, flush=True)
                elif message == '/settings':
                    print(describe(cfg), flush=True)
                else:
                    for name in PROFILES:
                        print(f'{name}: {cfg.model_profiles.get(name) or cfg.model}'
                              + ('（已配置映射）' if name in cfg.model_profiles else '（未绑定其他模型，沿用当前模型）'), flush=True)
                    print('这里只展示本地配置，未验证服务端可用性；也可 /model 输入模型ID。', flush=True)
                continue
            if message.startswith('/'):
                try:
                    updated = command_settings(cfg, message)
                    switch_config(agent, updated)
                    cfg = updated
                    print(describe(cfg) + '\n配置已更新；下一轮由服务端验证模型/思考支持情况。', flush=True)
                except (ValueError, LLMError) as error:
                    print('未切换：' + (str(error) if isinstance(error, ValueError)
                          else '模型客户端初始化失败，请检查服务配置。'), flush=True)
                continue
            if not message:
                continue
            print('正在深度思考…' if cfg.llm_thinking else '正在分析问题…', flush=True)
            streamed = False
            def show_text(text):
                nonlocal streamed
                if not streamed:
                    print('\nLodestar > ', end='', flush=True)
                    streamed = True
                print(text, end='', flush=True)
            labels = {'search_web': '正在搜索网页', 'search_papers': '正在检索论文',
                      'discover_papers': '正在查找近期论文', 'read_paper': '正在读取论文',
                      'read_webpage': '正在读取网页', 'verify_paper': '正在核查发表信息',
                      'project_context': '正在读取项目上下文'}
            def show_progress(action):
                print(('\n' if streamed else '') + labels.get(action, '正在准备资料') + '…', flush=True)
            result = agent.turn(session, message, on_text=show_text,
                on_progress=show_progress)
            if streamed:
                print(flush=True)
                if result['status'] == 'error':
                    print('\n[回答未完成] ' + result['error_message'], flush=True)
            else:
                print('\nLodestar > ' + result.get('answer', result.get('error', '未返回答案')), flush=True)
    finally:
        if 'agent' in locals():
            agent.llm.close()
        ws.close()


if __name__ == '__main__':
    main()
