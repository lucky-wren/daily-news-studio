# -*- coding: utf-8 -*-
"""发布前硬校验：只读产物，不调用生成器；任一失败即 rc=1，让 Actions 直接红灯。"""
import os, re, json, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = []


def ck(name, ok, detail=''):
    CHECKS.append({'name': name, 'ok': bool(ok), 'detail': str(detail)})
    if not ok:
        print('FAIL', name, detail)


def load(p):
    with open(os.path.join(ROOT, p), encoding='utf-8') as f:
        return json.load(f)


def main():
    yml = open(os.path.join(ROOT, '.github', 'workflows', 'daily.yml'), encoding='utf-8').read()
    ck('workflow 无 schedule', 'schedule:' not in yml)
    ck('workflow 无 cron 字面量', not re.search(r"cron\\s*:", yml))
    ck('保留 workflow_dispatch', 'workflow_dispatch:' in yml)
    html = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    for bad in ['id="schedMode"', 'id="ymFixed"', 'id="cronFixed"', '固定 cron', '定时重建安排']:
        ck('页面无定时残留 ' + bad, bad not in html)

    bs = load('BUILD_STATUS.json')
    ck('BUILD_STATUS ok', bs.get('ok'), bs.get('errors'))
    ck('分段计时齐全', all(k in bs.get('timings', {}) for k in
                          ['fetch_s', 'build_s', 'render_cover_s', 'tts_s', 'mux_s']), bs.get('timings'))
    ck('构建版本标记', bs.get('version') == '1.0', bs.get('version'))

    data = load('data.json')
    ct = load('cover_theme.json')
    cc = int(ct.get('count', 10))
    ck('封面条数可设置且合法', 1 <= cc <= 10, cc)
    js = open(os.path.join(ROOT, 'data.js'), encoding='utf-8').read()
    m = re.search(r'window\.__LIVE_DATA__=(\{.*\});', js, re.S)
    ck('data.js 可解析', bool(m))
    if m:
        live = json.loads(m.group(1))
        ck('data.js 与 data.json 标题逐字一致',
           [x['title'] for x in live.get('news', [])] == [x['title'] for x in data.get('news', [])])
        ck('data.js 封面基础数据 10 条', len(live.get('cover', [])) == 10)
        ck('data.js 显示条数同源', int(live.get('cover_count', cc)) == cc, live.get('cover_count'))
    ck('data.json 含 rebuilt_at', 'rebuilt_at' in data, data.get('rebuilt_at'))
    ck('页面初版标记', '初版 · 每日要闻工作台' in html)
    ck('页面无迭代话术', not any(x in html for x in
                            ['原文未' + '改', 'v' + '12.', 'v' + '13-', '沿用' + ' v',
                             '产品' + '版' + ' v', '修复' + '说明']))
    ck('片尾无「部分为预测或时点数据」', '部分为预测' not in html and '部分为预测' not in data.get('close', ''))
    _iss = data.get('issue_cfg', {})
    ck('开场按当天且封面日期同源',
       bool(_iss.get('date_cn')) and _iss['date_cn'] in data.get('open', '')
       and _iss.get('date') in data.get('date', ''), (_iss.get('date_cn'), data.get('open', '')[:60]))
    ck('口播文稿开场与数据同源', _iss.get('date_cn', 'X') in
       open(os.path.join(ROOT, '口播文稿.md'), encoding='utf-8').read()[:400])
    ck('data_source issue 同源', load('data_source.json').get('issue', {}).get('date') == _iss.get('date'))

    # ---- 封面/水印/页面初始日期与开场同源 ----
    _cti = ct.get('issue') or {}
    ck('cover_theme 日期与开场同源',
       _cti.get('date') == _iss.get('date') and _cti.get('date_cn') == _iss.get('date_cn')
       and _cti.get('date_line') == _iss.get('date_line'), (_cti.get('date'), _iss.get('date')))
    ck('水印文本日期与开场同源', _iss.get('date', 'X') in str((ct.get('wm') or {}).get('text', '')),
       (ct.get('wm') or {}).get('text'))
    ck('页面初始 meta 由本期日期运行时覆盖',
       "textContent=S.issue.date_line" in html and 'id="hdMeta"' in html, '')
    _dsct = load('data_source.json').get('cover_theme') or {}
    ck('data_source 内嵌封面日期与开场同源',
       (_dsct.get('issue') or {}).get('date') == _iss.get('date')
       and _iss.get('date', 'X') in str((_dsct.get('wm') or {}).get('text', '')),
       ((_dsct.get('issue') or {}).get('date'), (_dsct.get('wm') or {}).get('text')))
    _mfct = ((load('render_manifest.json').get('config') or {}).get('cover') or {})
    ck('manifest 封面日期与开场同源',
       (_mfct.get('issue') or {}).get('date') == _iss.get('date'),
       (_mfct.get('issue') or {}).get('date'))

    # ---- 期号：全链路阿拉伯数字，且中文旧值会被规范化 ----
    import source as SRC
    ck('norm_issue 中文数字/无补零/纯数字 → 第NN期',
       SRC.norm_issue('第\u4e00期') == '第01期' and SRC.norm_issue('第3期') == '第03期'
       and SRC.norm_issue('12') == '第12期' and SRC.norm_issue('第11期') == '第11期'
       and SRC.norm_issue('第十\u4e00期') == '第11期'
       and SRC.norm_issue('创刊号') == '创刊号',
       (SRC.norm_issue('第\u4e00期'), SRC.norm_issue('第3期'), SRC.norm_issue('第十\u4e00期')))
    ck('source 默认期号为第01期',
       SRC.auto_issue()['issue_no'] == '第01期', SRC.auto_issue()['issue_no'])
    _issno = str(_iss.get('issue_no') or '')
    ck('本期期号为阿拉伯数字格式', bool(re.fullmatch(r'第\d{2}期', _issno)), _issno)
    ck('data.json / data.js 顶层期号同源且为阿拉伯数字',
       data.get('issue') == _issno and bool(re.search(r'"issue":\s*"第\d{2}期"', js)),
       (data.get('issue'), _issno))
    ck('各数据契约期号同源',
       all(str((o.get('issue') or {}).get('issue_no') or o.get('issue_no') or '') == _issno
           for o in [ct, load('data_source.json'), load('data_source.json').get('cover_theme') or {}]),
       (ct.get('issue', {}).get('issue_no'), (load('data_source.json').get('issue') or {}).get('issue_no')))
    ck('manifest 期号与本期同源',
       str((load('render_manifest.json').get('issue') or {}).get('issue_no') or '') == _issno,
       ((load('render_manifest.json').get('issue') or {}).get('issue_no')))
    _bad_vals = []
    for _f in ['index.html', 'data.js', 'data.json', 'data_source.json', 'render_manifest.json',
               'cover_theme.json']:
        _t = open(os.path.join(ROOT, _f), encoding='utf-8').read()
        if any(x in _t for x in ['"issue": "第一期"', '"issue_no": "第一期"',
                                 'value="第一期"', '>第一期<']):
            _bad_vals.append(_f)
    ck('配置默认值无中文期号残留', not _bad_vals, _bad_vals)
    ck('Python 默认值字符串无中文期号',
       "'第一期'" not in open(os.path.join(ROOT, 'scripts', 'source.py'), encoding='utf-8').read()
       and "'第一期'" not in open(os.path.join(ROOT, 'scripts', 'render.py'), encoding='utf-8').read())
    ck('页面状态迁移会升级旧期号',
       'S.issue.issue_no=normIssue' in html and "STATE_VER='l1-3'" in html
       and "function normIssue(" in html)
    ck('页面期号输入失焦会规范化', "$('issueNo').onchange" in html and 'normIssue($' in html)
    # ---- 报头日期间距：前后端同值且已拉开 ----
    ck('页面报头日期已拉开', 'HEAD_DATE_GAP=46' in html and 'yy+HEAD_DATE_GAP*u' in html)
    _rend = open(os.path.join(ROOT, 'scripts', 'render.py'), encoding='utf-8').read()
    ck('服务端报头日期与页面同值',
       'HEAD_DATE_GAP = 46' in _rend and 'HEAD_RULE_AT = 64' in _rend
       and _rend.count('HEAD_DATE_GAP * u') >= 1 and _rend.count('HEAD_RULE_AT * u') >= 2
       and 'def _date_badge(' in _rend and 'def _issue_badge(' in _rend)
    ck('服务端无旧的贴标题日期', "yy + 8 * u" not in _rend)
    # 日期牌 / 期号胶囊：实底白字，前后端同一套配色，不再用浅字淡底
    ck('服务端日期牌与期号胶囊为实底白字',
       'date_box=date_box' in _rend and 'badge_red=badge_red' in _rend
       and "co['date_box']" in _rend and "co['badge_red']" in _rend
       and "co['date_ink']" in _rend and "co['badge_ink']" in _rend
       and "txt(d, (pad, y + int(40 * u)), dl, f, fg" not in _rend)
    ck('页面报头标签与服务端同配色',
       'dateBox:T.dark?mixHex' in html and 'badgeRed:T.dark?T.acc' in html
       and 'co.badgeRed' in html and 'co.dateBox' in html
       and "co.chip;rr(ctx,bx,yy-2,bw,bh" not in html)

    mf = load('render_manifest.json')
    ck('manifest 显示条数同源', mf.get('cover_count') == cc
       and mf['config']['cover'].get('count') == cc, (mf.get('cover_count'), cc))
    ck('data_source 显示条数同源', load('data_source.json').get('cover_count') == cc
       and load('data_source.json').get('cover_theme', {}).get('count') == cc)
    ck('封面清单条数 = 设置条数',
       len(re.findall(r'^\d+\. ', open(os.path.join(ROOT, '封面清单.md'), encoding='utf-8').read(), re.M)) == cc)
    nseg = len(mf['segments'])
    ck('句子/卡片数量一致', nseg == len(mf['cards']) + 2 +
       (1 if any(s['kind'] == 'window' for s in mf['segments']) else 0),
       '%d 句 / %d 卡' % (nseg, len(mf['cards'])))
    ck('封面 N 条 = 正文前 N 条标题',
       [c['title'] for c in data['cover'][:cc]] == [n['title'] for n in data['news'][:cc]], cc)
    ck('所有资讯卡带来源 URL',
       all(s.get('url') for c in mf['cards'] for s in c['sources'] if s.get('platform')))
    sub = mf['config']['subtitle']
    ck('字幕默认烧录（Chrome 可见）', sub.get('burn_in') is True)
    ck('字幕保留软轨', sub.get('soft_track') is True)
    ck('字幕开关存在', 'on' in sub)
    ck('打字机契约存在', 'on' in mf['config']['typewriter'])
    ck('打字机默认开启', mf['config']['typewriter'].get('on') is True)
    ck('打字机作用位置合法', mf['config']['typewriter'].get('target') in ('subtitle', 'frame', 'both'))
    ck('导出尺寸合法', mf.get('export_size') in ('portrait', 'landscape'))
    ck('无定时契约', mf['config'].get('trigger', {}).get('mode') == 'workflow_dispatch')
    ds = load('data_source.json')
    ck('资讯窗口声明', ds.get('news_window', {}).get('start') == '昨日 20:00'
       and ds.get('news_window', {}).get('end') == '今日 20:00', ds.get('news_window'))
    ck('data_source 无 fixed_cron', 'fixed_cron' not in ds and 'schedule_default' not in ds)

    ck('页面 30 独立编辑框容器', 'id="bodyEditors"' in html and 'renderBodyEditors' in html)
    ck('页面全屏按钮', 'id="btnFull"' in html and 'requestFullscreen' in html)
    ck('页面字幕设置齐备', all(('id="%s"' % x) in html for x in
                          ['subOn', 'subBurn', 'subSoft', 'subSize', 'subPos', 'subTypewriter']))
    ck('页面单一视频导出按钮', 'id="btnExpVideo"' in html and 'id="btnExpV"' not in html
       and 'id="btnExpH"' not in html)
    ck('播放器 rAF 连续推进', 'requestAnimationFrame(tick)' in html and 'function startPlay' in html)
    ck('TTS 并发 6 路', 'VOICE_CONCURRENCY=6' in html)
    ck('流程条 8 项定位', 'function goSec(' in html and html.count("['sec-") >= 8)

    vs = load('VIDEO_STATUS.json')
    ck('VIDEO_STATUS ok', vs.get('ok'), vs.get('errors'))
    ck('只出一版视频', len(vs.get('versions', {})) == 1, list(vs.get('versions', {})))
    ck('导出尺寸与 manifest 一致', vs.get('export_size') == mf.get('export_size'))
    ck('字幕已烧录', vs.get('subtitle_burned') is True)
    for k, v in vs.get('versions', {}).items():
        ck(k + ' 视频轨+音轨', v.get('video') and v.get('audio'), v.get('streams'))
        ck(k + ' 尺寸', v.get('size') in ([1080, 1920], [1920, 1080]), v.get('size'))
        ck(k + ' 体积字段', isinstance(v.get('bytes'), int) and v['bytes'] > 1024, v.get('bytes'))
    ck('字幕条数=句子数', vs.get('subtitle_count') == nseg if vs.get('subtitle_on') else True,
       vs.get('subtitle_count'))
    ck('卡片数=资讯条数', vs.get('card_count') == len(mf['cards']))
    ck('成品时长与音频一致',
       abs(next(iter(vs['versions'].values()))['duration'] - vs['audio']['duration']) <= 1.5
       if vs.get('versions') else False)

    esz = mf.get('export_size')
    ename = '竖版9比16' if esz == 'portrait' else '横版16比9'
    esz0 = mf.get('export_size')
    meta_path = os.path.join(ROOT, '.cache', 'cover_meta_%s.json' % esz0)
    if os.path.isfile(meta_path):
        cm = load(os.path.join('.cache', 'cover_meta_%s.json' % esz0))
        ck('封面渲染条数 = 设置条数', cm.get('count') == cc and len(cm.get('boxes', [])) == cc,
           (cm.get('count'), len(cm.get('boxes', []))))
        ck('封面内容不越界', cm.get('used', 0) <= cm.get('cell_h', 0) + 1,
           (cm.get('used'), cm.get('cell_h')))
    for f in ['media/今日播报.mp3', 'media/今日要闻.srt', 'media/timeline.json',
              'media/今日要闻_%s.mp4' % ename, 'media/封面_%s.png' % ename]:
        ck('成品存在 ' + f, os.path.isfile(os.path.join(ROOT, f))
           and os.path.getsize(os.path.join(ROOT, f)) > 512)

    # 三栏工作台 / 逐句音轨 / 封面留白 / 片尾帧 结构性断言
    prod = open(os.path.join(ROOT, 'scripts', 'produce.py'), encoding='utf-8').read()
    rend = open(os.path.join(ROOT, 'scripts', 'render.py'), encoding='utf-8').read()
    ck('三栏工作台骨架', 'class="workbench"' in html and 'id="propsBody"' in html
       and 'id="navrail"' in html and 'id="stage"' in html)
    ck('八个工具栏固定在右侧同一位置', html.count('<section class="panel"') == 8
       and 'function applyTab(' in html and 'function switchTab(' in html)
    ck('点导航切内容、不滚动定位', 'function goSec(' in html and 'window.scrollTo' not in html)
    ck('仅顶部两个折叠按钮', 'id="btnFoldL"' in html and 'id="btnFoldR"' in html
       and 'id="btnFoldL2"' not in html and 'id="btnFoldR2"' not in html
       and "$('btnFoldL2').onclick" not in html)
    ck('封面字号不随条数放大（服务端）', 'while title_fs < max_fs' not in rend and 'fixed_font' in rend)
    ck('封面资讯整体垂直居中（前后端）', "'centered': True" in rend
       and 'centered:true' in html and 'whitespace:Math.round' in html)
    ck('内置曲库已移除', 'id="bgmLib"' not in html and 'id="btnBgmStop"' not in html
       and '"bgm_library":' not in open(os.path.join(ROOT, 'data.js'), encoding='utf-8').read()
       and 'bgm_library_note' not in open(os.path.join(ROOT, 'data.js'), encoding='utf-8').read()
       and not os.path.isdir(os.path.join(ROOT, 'assets', 'bgm')))
    ck('试听为单按钮 toggle', 'id="btnBgmPreview"' in html and 'id="btnBgmStop"' not in html
       and "a.pause();a.currentTime=0;" in html and 'bgmPreviewing=true' in html)
    ck('播放器四控件齐备', all(('id="%s"' % x) in html for x in
                          ('btnPlay', 'btnReplay', 'btnFull', 'btnSubToggle')))
    ck('本期状态条与画幅条已移除', 'id="stSize"' not in html and 'id="prodBar"' not in html
       and 'class="statusbox"' not in html)
    ck('期号胶囊无描边（服务端/页面）', 'outline=cap_edge' not in rend
       and "ctx.strokeStyle='rgba(255,255,255,.65)'" not in html)
    ck('开场/片尾帧按主题反色', 'ctx.fillStyle=T.txt' in html and 'th = R.THEMES.get' in prod)
    ck('片尾帧不再写死白底白字', "ctx.fillStyle='#fff';ctx.font='800 44px" not in html)
    ck('逐句合成写进真实音轨', 'VOICE_TRACK[k]=' in html and 'function buildMergedTrack(' in html)
    ck('播放由真实音频 currentTime 驱动', 'mergedAudio.play' in html and 'a.currentTime' in html)
    ck('预览与导出共用同一份音频缓冲', 'MERGED.url' in html and 'btnExportVoiceTrack' in html)
    ck('浏览器内按帧录制导出', 'btnRecordWebM' in html and 'captureStream' in html)
    ck('文稿变更使音轨失效', 'function invalidateVoice(' in html)
    tts_src = open(os.path.join(ROOT, 'scripts', 'tts.py'), encoding='utf-8').read()
    ck('人声响度归一化', 'loudnorm' in prod)
    ck('混音关闭自动衰减', 'normalize=0' in prod)
    ck('逐字字幕 ASS 契约', 'build_ass' in tts_src and 'build_ass' in prod)
    ck('成品音量已记录', isinstance(vs.get('audio', {}).get('mean_db'), (int, float)))
    ck('静帧编码 fps=8', 'fps=8' in prod)
    ck('静帧编码 crf=30', "'-crf', '30'" in prod)

    # ---- L1 语义编排（表格可选列驱动） ----
    src_py = open(os.path.join(ROOT, 'scripts', 'source.py'), encoding='utf-8').read()
    sem = data.get('semantic') or {}
    ck('L1 契约存在且标记为表格列驱动', sem.get('mode') == 'L1' and 'columns' in sem, sem.get('mode'))
    ck('L1 可选列 12 项', len(sem.get('columns', [])) == 12, len(sem.get('columns', [])))
    ck('L1 列解析只读表头（无正则抽取/词表）',
       'SEM_COLS' in src_py and 'sem_index(' in src_py and 'build_semantic(' in src_py
       and 'BOARD_TABLE' not in src_py and 'extract_hero' not in src_py)
    ck('L1 头条数值来自列', bool((sem.get('hero') or {}).get('value')),
       (sem.get('hero') or {}).get('value'))
    ck('L1 专题按专题ID分组（>=2 条）',
       all(len(t.get('items', [])) >= 2 for t in sem.get('topics', [])),
       [(t.get('id'), len(t.get('items', []))) for t in sem.get('topics', [])])
    ck('L1 板块去重统计', sem.get('stats', [{}] * 3)[1].get('num')
       == '%02d' % len(sem.get('boards', [])), sem.get('boards'))
    ck('L1 N 条全部有归属',
       sem.get('count') == 1 + sem.get('topic_count', 0) + sem.get('rest_count', 0),
       (sem.get('count'), sem.get('topic_count'), sem.get('rest_count')))
    ck('data.js 与 data.json 语义块同源',
       (m and json.loads(m.group(1)).get('semantic', {}).get('mode') == 'L1'))
    ck('data_source 声明 L1 列',
       ds.get('semantic_mode') == 'L1' and len(ds.get('semantic_columns', [])) == 12)
    ck('inline_payload 封面带 sem',
       all('sem' in c for c in ds.get('inline_payload', {}).get('cover', [])))
    ck('manifest 带语义块与列契约',
       mf.get('semantic', {}).get('mode') == 'L1'
       and mf.get('config', {}).get('semantic', {}).get('mode') == 'L1')
    ck('封面四套模板（含 semantic）',
       all(x in html for x in ("semantic:'语义编排'", 'rank:' + "'榜单卡片'"))
       and "cfg.get('tpl') == 'semantic'" in rend
       and 'if(S.cover.tpl===\'semantic\'' in html)
    ck('无可选列走等权列表、有可选列走杂志版，均不回退榜单',
       'def _render_equal_cover(' in rend and 'function drawEqualCover(' in html
       and "if not sem['enabled']" in rend and 'if(!sem.enabled)' in html
       and "return render_cover(root, payload, dict(cfg, tpl='rank')" not in rend)
    ck('等权列表：统计条隐藏、字号固定、整组居中',
       "'stats_hidden': True" in rend and "'fixed_font': True" in rend
       and "'centered': True" in rend and 'stats_hidden:true' in html
       and 'baseFs=(land?34:38)' in html)
    ck('等权列表：条目卡片化且三档间距真实生效',
       "SPACING = {'compact': (12, 16), 'standard': (16, 22), 'airy': (18, 26)}" in rend
       and 'card_gap' in rend and "'card_gap': round(gap, 2)" in rend
       and "SP={compact:[12,16],standard:[16,22],airy:[18,26]}" in html
       and 'card_gap:Math.round(gap)' in html
       and '\n    gap = 10 * u\n' not in rend and 'gap=10*u' not in html)
    ck('统计条只在表格填了板块时绘制',
       "if sem['boards']:" in rend and 'sem.boards.length?semStats' in html)
    ck('杂志版其余要闻单列、字号按内容自适应（不重叠）',
       'def total_at(fs)' in rend and 'const totalAt=' in html
       and "cols = 2 if len(sem['rest']) >= 4 else 1" not in rend
       and "cols=sem.rest.length>=4?2:1" not in html)
    ck('前后端语义块同逻辑',
       'function buildSemantic(' in html and 'def build_semantic(' in src_py
       and 'function drawSemanticCover(' in html and 'def render_semantic_cover(' in rend)
    ck('默认封面模板为 semantic', ct.get('tpl') == 'semantic', ct.get('tpl'))
    if os.path.isfile(meta_path):
        cmv = load(os.path.join('.cache', 'cover_meta_%s.json' % esz0))
        if cmv.get('semantic'):
            ck('语义封面渲染条数 = 设置条数', cmv.get('count') == cc, cmv.get('count'))
            ck('封面版式标识正确', cmv.get('layout') in ('equal', 'magazine'), cmv.get('layout'))
            ck('版式与本次表格素材一致',
               cmv.get('layout') == ('magazine' if cmv.get('enabled') else 'equal'),
               (cmv.get('layout'), cmv.get('enabled')))
            ck('语义封面头条数值同源',
               cmv.get('layout') == 'equal'
               or cmv.get('hero_value') == (sem.get('hero') or {}).get('value'),
               cmv.get('hero_value'))
            ck('语义封面专题/其余条数同源',
               cmv.get('layout') == 'equal'
               or (cmv.get('topic_count') == sem.get('topic_count')
                   and cmv.get('rest_count') == sem.get('rest_count')))

    ck('stillimage 调优', "'-tune', 'stillimage'" in prod)
    ck('音频 64k 单声道', "'-b:a', '64k'" in prod and "'-ac', '1'" in prod)

    bad = [c for c in CHECKS if not c['ok']]
    print('共 %d 项，失败 %d 项' % (len(CHECKS), len(bad)))
    with open(os.path.join(ROOT, 'RELEASE_CHECK.json'), 'w', encoding='utf-8') as f:
        json.dump({'checks': CHECKS, 'failed': len(bad)}, f, ensure_ascii=False, indent=2)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
