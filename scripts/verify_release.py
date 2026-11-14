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
    cc = int(ct.get('count', 8))
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
       'S.issue.issue_no=normIssue' in html and "STATE_VER='l1-4'" in html
       and "function normIssue(" in html)
    ck('页面期号输入失焦会规范化', "$('issueNo').onchange" in html and 'normIssue($' in html)
    # ---- 报头日期间距：前后端同值且已拉开 ----
    ck('页面报头日期已拉开且字号放大', 'HEAD_DATE_GAP=82' in html and 'DATE_FS=38' in html
       and 'yy+HEAD_DATE_GAP*u' in html and 'HEAD_RULE_AT=126' in html)
    _rend = open(os.path.join(ROOT, 'scripts', 'render.py'), encoding='utf-8').read()
    ck('服务端报头日期与页面同值',
       'HEAD_DATE_GAP = 82' in _rend and 'HEAD_RULE_AT = 126' in _rend
       and 'DATE_FS = 38' in _rend and 'ISSUE_FS = 38' in _rend
       and _rend.count('HEAD_DATE_GAP * u') >= 1 and _rend.count('HEAD_RULE_AT * u') >= 2
       and 'def _date_badge(' in _rend and 'def _issue_badge(' in _rend)
    ck('服务端无旧的贴标题日期', "yy + 8 * u" not in _rend)
    # 日期牌 / 期号胶囊：实底白字，前后端同一套配色，不再用浅字淡底
    ck('服务端日期牌与期号胶囊为实底白字',
       'date_box=date_box' in _rend and 'badge_red=badge_red' in _rend
       and "co['date_box']" in _rend and "co['badge_red']" in _rend
       and "co['date_ink']" in _rend and "co['badge_ink']" in _rend
       and "txt(d, (pad, y + int(40 * u)), dl, f, fg" not in _rend)
    ck('日期牌朱红竖杠已去掉（前后端）',
       'bar_x' not in _rend and 'x0+10*u,by+9*u' not in html
       and 'pad+10*u,by+9*u' not in html and 'co.acc;rr(ctx,x0+10*u' not in html)
    ck('日期牌与期号字号统一放大', 'DATE_BH = 62' in _rend and 'ISSUE_BH = 68' in _rend
       and 'DATE_BH=62' in html and 'ISSUE_BH=68' in html)
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
    ck('TTS 并发 16 路且可调', 'VOICE_CONCURRENCY=16' in html and 'id="vcConc"' in html
       and "min(16, max(1, int(cfg.get('concurrency', 16))))" in open(os.path.join(ROOT, 'scripts', 'tts.py'), encoding='utf-8').read())
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
       "SPACING = {'compact': (8, 10), 'standard': (11, 14), 'airy': (14, 18)}" in rend
       and 'card_gap' in rend and "'card_gap': round(gap, 2)" in rend
       and "SP={compact:[8,10],standard:[11,14],airy:[14,18]}" in html
       and 'card_gap:Math.round(gap)' in html
       and '\n    gap = 10 * u\n' not in rend and 'gap=10*u' not in html)
    ck('杂志版不画统计条（板块为空也不摆 00）',
       "'stats_hidden': True" in rend and 'stats_hidden:true' in html
       and "if sem['boards']:" not in rend
       and 'sem.boards.length?semStats' not in html)
    ck('杂志版精简为顺序行盒，文字块做相交自检',
       'def list_section(' in rend and 'const listSection=' in html
       and 'def draw_lines(' in rend and 'const drawLines=' in html
       and "'overlap_count': len(ov)" in rend and 'overlap_count:ov.length' in html
       and "cols = 2 if len(sem['rest']) >= 4 else 1" not in rend
       and "cols=sem.rest.length>=4?2:1" not in html)
    ck('杂志版删掉头条摘要/来源/角标与专题逐条来源',
       "'来源 · '" not in rend[rend.index('def render_semantic_cover'):]
       and "'来源 · '" not in html[html.index('function drawSemanticCover'):html.index('function drawCover')])
    ck('前后端语义块同逻辑',
       'function buildSemantic(' in html and 'def build_semantic(' in src_py
       and 'function drawSemanticCover(' in html and 'def render_semantic_cover(' in rend)
    ck('横版杂志版重排为报头横贯＋头条横带＋下半双栏',
       'def land_band(' in rend and 'land_fs, land_rh = land_band()' in rend
       and 'const ry0=496*u' in html and 'const landList=' in html
       and 'ly0, ly1 = head_bottom, H - footer_h' not in rend
       and 'lx1=W*.41' not in html)
    ck('横版资讯字号下限 30u 且两栏共用同一 fs',
       'fs = min(34 * u, cap)' in rend and 'let fs=Math.min(34*u,cap)' in html
       and 'max(26 * u, fs)' in rend and 'Math.max(26*u,fs)' in html)
    ck('默认封面模板为 semantic', ct.get('tpl') == 'semantic', ct.get('tpl'))
    if os.path.isfile(meta_path):
        cmv = load(os.path.join('.cache', 'cover_meta_%s.json' % esz0))
        if cmv.get('semantic'):
            _exp = ('equal' if not cmv.get('enabled')
                    else ('magazine_band' if cmv.get('size') == 'landscape' else 'magazine'))
            ck('语义封面渲染条数 = 设置条数', cmv.get('count') == cc, cmv.get('count'))
            ck('封面版式标识正确',
               cmv.get('layout') in ('equal', 'magazine', 'magazine_band'), cmv.get('layout'))
            ck('版式与本次表格素材一致', cmv.get('layout') == _exp,
               (cmv.get('layout'), _exp))
            if cmv.get('layout') == 'magazine_band':
                ck('横版资讯字号≥30 且两栏字号统一',
                   (cmv.get('land_fs') or 0) >= 30 and cmv.get('uniform_font') is True,
                   (cmv.get('land_fs'), cmv.get('uniform_font')))
                ck('横版双栏行高与导出字号同源',
                   (cmv.get('land_rh') or 0) >= 78
                   and abs((cmv.get('font_fs') or 0) - round(cmv.get('land_fs') or 0)) <= 1,
                   (cmv.get('land_rh'), cmv.get('font_fs'), cmv.get('land_fs')))
            ck('语义封面头条数值同源',
               cmv.get('layout') == 'equal'
               or cmv.get('hero_value') == (sem.get('hero') or {}).get('value'),
               cmv.get('hero_value'))
            ck('语义封面专题/其余条数同源',
               cmv.get('layout') == 'equal'
               or (cmv.get('topic_count') == sem.get('topic_count')
                   and cmv.get('rest_count') == sem.get('rest_count')))
            ck('杂志版文字块两两不相交',
               cmv.get('layout') == 'equal'
               or (cmv.get('overlap_count') == 0 and (cmv.get('text_boxes') or 0) >= 8),
               (cmv.get('overlap_count'), cmv.get('text_boxes'), cmv.get('overlaps')))

    closing_md = open(os.path.join(ROOT, '口播文稿.md'), encoding='utf-8').read()
    CLOSING = '以上是今日要闻速览，感谢收听，下期见。'
    ck('片尾只朗读这一句', data.get('close') == CLOSING
       and ds.get('closing') == CLOSING
       and mf['segments'][-1]['text'] == CLOSING
       and '# 片尾（可单独编辑）\n\n' + CLOSING in closing_md)
    ck('免责声明仅页面展示', '免责声明（仅页面展示' in html
       and '不构成投资' not in data.get('close', '')
       and '不构成投资' not in ds.get('closing', '')
       and 'disclaimer' in data)
    ck('开场/片尾帧不再写免责声明',
       "ctx.fillText('内容依据公开信息整理 · 以官方发布为准'" not in html)
    ck('字幕烧进视频画面', '.sublayer{display:none!important}' in html
       and 'function drawSubtitleCanvas(' in html
       and 'drawSubtitleCanvas(ctx,540,960' in html)
    ck('时间轴可拖动指针定位', all(x in html for x in
       ('id="pbarHead"', 'id="tlHead"', 'function seekToRatio(', 'function bindSeek(',
        'setPointerCapture')))
    ck('混声只有三项且前后端同源', all(('id="%s"' % x) in html for x in
       ('mixVoice', 'mixBgm', 'mixMaster'))
       and not any(('id="%s"' % x) in html for x in
                   ('mixDuck', 'mixDuckDb', 'mixFadeIn', 'mixFadeOut'))
       and 'function mixCfg(' in html and 'mix' in mf['config']
       and 'master_gain_db' in mf['config']['voice']
       and 'concurrency' in mf['config']['voice'])
    ck('默认响度提高、不再压低配乐与自动淡入淡出',
       float(ds.get('voice', {}).get('volume') or 0) > 1.0
       and float(ds.get('mix', {}).get('master_gain_db') or 0) > 0
       and ds.get('mix', {}).get('duck') is False
       and float(ds.get('mix', {}).get('fade_in', 1)) == 0
       and float(ds.get('mix', {}).get('fade_out', 1)) == 0
       and float(data.get('voice_volume_default') or 0) > 1.0
       and 'volume:1.35' in html and 'master_db:3' in html
       and 'function attachVoiceGain(' in html
       and "S.mix.duck=false;S.mix.fade_in=0;S.mix.fade_out=0" in html)
    ck('服务端混音 duck 与淡入淡出',
       'duck_db' in prod and 'afade=t=in' in prod and 'afade=t=out' in prod)
    ck('逐句合成增量复用与持久化缓存',
       'LAST_SIG' in html and 'indexedDB.open' in html
       and 'function cacheGet(' in html and '增量复用' in html)
    _SEPS = ('bands', 'cards', 'lines', 'bars', 'none')
    ck('条目分隔五套方案，前后端同源且写进配置',
       "const CV_SEPS={bands:'隔行底色',cards:'独立卡片',lines:'细线分隔',bars:'左侧色条',none:'纯留白'}" in html
       and "SEP_MODES = ('bands', 'cards', 'lines', 'bars', 'none')" in rend
       and 'function sepMode(' in html and 'function sepPaint(' in html
       and 'def _sep_mode(' in rend and 'def _sep_paint(' in rend
       and 'id="cvSeps"' in html and "chips($('cvSeps'),CV_SEPS" in html
       and 'sep:sepMode()' in html and "'sep': sep_mode" in rend
       and 'sepPaint(ctx,sepCo' in html and '_sep_paint(d,' in rend
       and ct.get('sep') in _SEPS
       and ds.get('cover_theme', {}).get('sep') in _SEPS
       and mf['config'].get('cover', {}).get('sep') in _SEPS
       and "const tpl=S.cover.tpl" in html
       and "plain_num = (tpl == 'list')" in rend)
    ck('语义列表序号独立、行盒顺序推进且不复用 y',
       'def num_chip(' in rend and 'const numChip=' in html
       and 'lines.append((rh, row))' in rend and 'lines.push([rh,y=>' in html
       and 'def draw_lines(' in rend and 'const drawLines=' in html
       and "'divider': False" in rend
       and "T.dark?'#dbe8f8':T.txt" in html and '(219, 232, 248)' in rend)
    ck('杂志版精简编排：无统计条/摘要/来源/角标，字号下限 22u',
       'def _draw_stats(' not in rend and 'statH=56*u' not in html
       and 'stat_h = int(56 * u)' not in rend
       and 'def hero_lines(' in rend and 'const heroLines=' in html
       and '22 * u' in rend and '22*u' in html
       and '先看这一条' not in rend and '先看这一条' not in html)
    ck('榜单卡片无边框线（纯留白方案下连结构线也不画）',
       "if(i<3&&!noDecor)" in html and "if i < 3 and not no_decor:" in rend
       and 'noDecor=sepMode()' in html)
    ck('报头重点头条角标放大且移到右上角',
       "const tagTxt='头条',tagFs=Math.round(28*u)" in html and "tag_f = F(int(28 * u)" in rend)
    ck('卡片来源拼成「来源：…」一行，柔和色不抢风头',
       "const srcTexts=(c.sources||[]).map(s=>'来源：'" in html
       and "ctx.fillStyle=T.sub;" in html
       and "ctx.fillText('来源',ix" not in html
       and "src_texts = ['来源：'" in rend
       and "txt(d, (pad, y), '来源'," not in rend
       and "'src_red': False" in rend and 'src_red:false' in html
       and "'来源 · '+s.platform" not in html and "'来源 · ' + platform" not in rend)
    ck('取消后可立即再合成、断点续合',
       'let voiceBusy=false' in html and 'voiceRun++' in html and '断点' in html
       and 'Promise.all(missing.map' in html and 'Promise.all(raw.map' in html)
    ck('等权封面整组居中且每侧留白封顶',
       'const MAX_SIDE=84*u' in html and 'MAX_SIDE = 84 * u' in rend)
    ck('视频卡摘要放大、来源不溢出',
       'Math.min(W,H)/1080' in html and 'bandBot' in html
       and 'u = min(W, H) / 1080.0' in rend
       and 'sum_fs = int((46 if' in rend
       and 'card_overflow' in prod
       and (not vs.get('ok') or vs.get('card_overflow') == []))
    ck('视频卡前后端均按短边缩放（横屏来源不溢出）',
       'Math.min(W,H)/1080' in html and 'u = min(W, H) / 1080.0' in rend
       and 'const scale=Math.min(W,H)/1080' in html
       and (not vs.get('ok') or vs.get('card_overflow') == []))

    ck('封面默认显示 8 条，且各源同源',
       "count:8" in html and 'value="8"' in html
       and "payload.get('cover_count', 8)" in rend
       and "payload.get('cover_count') or 8" in src_py
       and int(data.get('cover_count', 0)) == cc
       and int(ds.get('cover_count', 0)) == cc
       and int(ct.get('count', 0)) == cc)
    ck('语义列表不再逐条缩字号，整组统一字号',
       'uniform_lines' in rend and 'uniformLines' in html
       and 'uniform_font_per_group' in rend and 'uniform_font_per_group:true' in html
       and "while d.textlength(text, font=f) > maxw and fs > minfs" not in rend
       and "while(ctx.measureText(text).width>maxW&&fs>minFs)" not in html
       and "one_line(txt1" not in rend and 'oneLine(it.label' not in html)
    ck('标题右侧不再提前折行（头条角标只占第一条）',
       "const maxW0=maxW-(S.cover.tpl==='headline'?120*u:0)" in html
       and "(S.cover.tpl==='headline'&&i===0)?maxW0:maxW" in html
       and "maxw0 = maxw - (int(120 * u) if tpl == 'headline' else 0)" in rend
       and "mw = maxw0 if (tpl == 'headline' and idx == 0) else maxw" in rend)
    ck('语义编排条目间距三档前后端真实生效',
       "const RG={compact:6,standard:18,airy:34}" in html
       and "const GL={compact:2,standard:14,airy:28}" in html
       and "{'compact': 6, 'standard': 18, 'airy': 34}.get(gap_cfg" in rend
       and "{'compact': 2, 'standard': 14, 'airy': 28}.get(gap_cfg" in rend
       and 'gapL=GL*u' in html and 'gapL = land_gap' in rend
       and "const RH_GAP=34*u,LH_GAP=28*u" in html and 'rh_gap, lh_gap = 34 * u, 28 * u' in rend
       and 'row_gap:Math.round(rowGap)' in html and "'land_gap': round(land_gap, 2)" in rend)
    ck('刷新在线音色一律中文名，不再把英文 id 当标签',
       'const VOICE_CN0=' in html and 'function cnVoiceName(' in html
       and 'VOICE_ALIAS=[' in html and "o.textContent=cnVoiceName(o.value)" in html
       and "'<option value="'+esc(id)+'">'+esc(nm)+'</option>'" not in html)
    ck('stillimage 调优', "'-tune', 'stillimage'" in prod)
    ck('音频 64k 单声道', "'-b:a', '64k'" in prod and "'-ac', '1'" in prod)

    bad = [c for c in CHECKS if not c['ok']]
    print('共 %d 项，失败 %d 项' % (len(CHECKS), len(bad)))
    with open(os.path.join(ROOT, 'RELEASE_CHECK.json'), 'w', encoding='utf-8') as f:
        json.dump({'checks': CHECKS, 'failed': len(bad)}, f, ensure_ascii=False, indent=2)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
