"""Isolated citation UI preview. Queries occur only when its button is clicked."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from lodestar.config import Config
from lodestar.web.server import WebApp, make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8125)
    args = parser.parse_args()
    with TemporaryDirectory(prefix='lodestar-impact-preview-') as directory:
        root = Path(directory)
        app = WebApp(Config(llm_mode='mock', search_mode='mock', db_path=root/'web.db', workspace_dir=root))
        app.paper_library({'title': 'The state of OA: a large-scale analysis of the prevalence and impact of Open Access articles',
                           'url': 'https://doi.org/10.7717/peerj.4375', 'published_year': 2018})
        app.paper_library({'title': '新论文提示 · 独立界面验收示例（非论文评价）',
                           'url': 'https://arxiv.org/abs/2611.00001', 'published_year': 2026})
        server = make_server(app, args.port)
        try:
            print(f'Citation UI preview: http://127.0.0.1:{server.server_port}/memory?tab=papers', flush=True)
            server.serve_forever()
        finally:
            app.close()
            server.server_close()


if __name__ == '__main__':
    main()
