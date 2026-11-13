# -*- coding: utf-8 -*-
"""把当前目录作为仓库根目录原子提交到 GitHub 默认分支。

不读取、不写入任何业务密钥；GitHub PAT 只需 repo + workflow 权限，
用环境变量 GITHUB_TOKEN 传入（deploy.bat 里 set /p 后仅设为当前进程环境变量）。
只上传白名单文件，排除成品媒体、缓存、旧实现残留与本地验收产物。
"""
import os, sys, json, base64, urllib.request, urllib.error, argparse

ROOT = os.path.dirname(os.path.abspath(__file__))
EXCLUDE_DIRS = {'.git', '.cache', '__pycache__', '_site', 'build', 'frames', 'out', 'data', 'config', '.github'}
# .github 里的 workflow 要上传，单独放行
INCLUDE = [
    'index.html', 'data.js', 'data.json', 'data_source.json', 'cover_theme.json',
    'video_card_theme.json', 'render_manifest.json', 'VIDEO_STATUS.json', 'source.xlsx',
    'BUILD_STATUS.json', '口播文稿.md', '封面清单.md',
    # 说明/验收/证据类文件不上传仓库
    '.github/workflows/daily.yml', 'deploy.py', 'deploy.bat',
]
INCLUDE += ['scripts/' + x for x in ['source.py', 'render.py', 'tts.py', 'produce.py',
                                     'daily_update.py', 'verify_release.py', '__init__.py']]
INCLUDE += ['assets/fonts/wqy-microhei.ttc']


def api(method, path, token, body=None):
    url = 'https://api.github.com' + path
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'User-Agent': 'daily-news-studio-deploy',
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError('GitHub API %s %s -> %d %s' % (method, path, e.code, e.read().decode()[:400]))


def collect():
    files = {}
    for rel in INCLUDE:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            if rel.startswith('assets/'):
                print('  ~ 跳过（增量包，线上已有）', rel)
                continue
            raise RuntimeError('必填文件缺失：' + rel)
        files[rel] = open(p, 'rb').read()
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--token', default=os.environ.get('GITHUB_TOKEN', ''))
    ap.add_argument('--owner', default=os.environ.get('GITHUB_OWNER', ''))
    ap.add_argument('--repo', default=os.environ.get('GITHUB_REPO', ''))
    ap.add_argument('--branch', default='main')
    ap.add_argument('--dry-run', action='store_true', help='只做本地清单核查，不联网')
    ap.add_argument('--incremental', action='store_true',
                    help='增量包模式：不重复上传线上已有的字体等资源')
    a = ap.parse_args()
    global INCLUDE
    if a.incremental:
        INCLUDE = [x for x in INCLUDE if not x.startswith('assets/')]
    files = collect()
    print('待提交 %d 个文件，合计 %.2f MiB' % (len(files), sum(len(v) for v in files.values()) / 1048576))
    if a.dry_run:
        print('DRY-RUN OK'); return 0
    if not (a.token and a.owner and a.repo):
        print('缺少 token / owner / repo', file=sys.stderr); return 2
    base = '/repos/%s/%s' % (a.owner, a.repo)
    ref = api('GET', base + '/git/ref/heads/' + a.branch, a.token)['object']['sha']
    commit = api('GET', base + '/git/commits/' + ref, a.token)
    tree = commit['tree']['sha']
    entries = []
    for rel, data in sorted(files.items()):
        blob = api('POST', base + '/git/blobs', a.token,
                   {'content': base64.b64encode(data).decode(), 'encoding': 'base64'})
        entries.append({'path': rel, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
        print('  +', rel, len(data))
    nt = api('POST', base + '/git/trees', a.token, {'base_tree': tree, 'tree': entries})['sha']
    nc = api('POST', base + '/git/commits', a.token,
             {'message': 'chore: 每日要闻工作台初版落地', 'tree': nt, 'parents': [ref]})['sha']
    api('PATCH', base + '/git/refs/heads/' + a.branch, a.token, {'sha': nc})
    print('SUCCESS 已提交 %d 个文件，commit %s' % (len(entries), nc[:12]))
    print('下一步：Actions → Run workflow 一次 → Ctrl+F5')
    return 0


if __name__ == '__main__':
    sys.exit(main())
