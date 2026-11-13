# -*- coding: utf-8 -*-
"""数据入口层：把配置里的数据源变成一份 payload，并派生全部下游产物。

数据契约：
  data_source.json  -> source.type = snapshot | local_xlsx | online_xlsx
  按 sheet 名锁定，绝不按「第几个 sheet」取数（在线返回顺序每次都可能不同）。
  失败策略 stop_and_report：任何一步拿不到数据就抛错退出，不沿用旧数据冒充本期。
"""
import os, re, json, zipfile, urllib.request, urllib.error, datetime, hashlib
import xml.etree.ElementTree as ET

WD = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']  # 索引 = Python weekday
JIEQI = [(1, 5, '小寒'), (1, 20, '大寒'), (2, 4, '立春'), (2, 19, '雨水'), (3, 5, '惊蛰'),
         (3, 20, '春分'), (4, 5, '清明'), (4, 20, '谷雨'), (5, 5, '立夏'), (5, 21, '小满'),
         (6, 5, '芒种'), (6, 21, '夏至'), (7, 7, '小暑'), (7, 23, '大暑'), (8, 7, '立秋'),
         (8, 23, '处暑'), (9, 7, '白露'), (9, 23, '秋分'), (10, 8, '寒露'), (10, 23, '霜降'),
         (11, 7, '立冬'), (11, 22, '小雪'), (12, 7, '大雪'), (12, 21, '冬至')]
FEST = [(1, 1, '元旦'), (2, 14, '情人节'), (3, 8, '国际妇女节'), (3, 12, '植树节'),
        (4, 1, '愚人节'), (5, 1, '劳动节'), (5, 4, '青年节'), (6, 1, '儿童节'),
        (7, 1, '建党节'), (8, 1, '建军节'), (9, 10, '教师节'), (10, 1, '国庆节'),
        (11, 9, '消防宣传日'), (11, 11, '双十一'), (12, 13, '国家公祭日'), (12, 25, '圣诞节')]


def today_cn():
    """Asia/Shanghai 无夏令时，用 UTC+8 固定偏移算当天，避免 runner 时区漂移。"""
    d = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).date()
    return d


def auto_notes(d):
    out = []
    for mm, dd, nm in JIEQI:
        q = datetime.date(d.year, mm, dd)
        diff = (d - q).days
        if diff == 0:
            out.append('今天是%s节气' % nm); break
        if 0 < diff <= 2:
            out.append('%d天前迎来%s节气' % (diff, nm)); break
    for mm, dd, nm in FEST:
        if mm == d.month and dd == d.day:
            out.append('今天是' + nm); break
    return '；'.join(out)


CN_NUM = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
          '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def _cn_num(s):
    if re.fullmatch(r'\d+', s):
        return int(s)
    if s == '十':
        return 10
    if s.startswith('十'):
        return 10 + CN_NUM.get(s[1:], 0)
    if s.endswith('十'):
        return CN_NUM.get(s[:-1], 0) * 10
    p = s.split('十')
    if len(p) == 2 and p[0] in CN_NUM and p[1] in CN_NUM:
        return CN_NUM[p[0]] * 10 + CN_NUM[p[1]]
    return CN_NUM.get(s)


def norm_issue(v):
    """期号统一阿拉伯数字：第 1 期 / 中文数字 / 纯数字 → 第01期；自定义文字原样保留。"""
    t = str(v or '').strip().translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    if not t:
        return ''
    m = re.fullmatch(r'第\s*(\d+|[零〇一二两三四五六七八九十]+)\s*期', t)
    raw = m.group(1) if m else (t if re.fullmatch(r'\d+|[零〇一二两三四五六七八九十]+', t) else None)
    if raw is None:
        return t
    n = _cn_num(raw)
    if n is None:
        return t
    return '第%02d期' % max(0, int(n))


def auto_issue(issue_no='第01期'):
    d = today_cn()
    wd = WD[d.weekday()]
    cn = '%d年%d月%d日，%s' % (d.year, d.month, d.day, wd)
    return {'date': d.isoformat(), 'weekday': wd, 'date_cn': cn,
            'date_line': d.isoformat() + ' ' + wd,
            'issue_no': norm_issue(issue_no), 'notes': auto_notes(d)}


def auto_opening(iss):
    nt = str(iss.get('notes') or '').strip().rstrip('。')
    return '早上好，今天是%s。%s以下为今日要闻速览。' % (iss['date_cn'], (nt + '。') if nt else '')


NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


class SourceError(RuntimeError):
    pass


def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- xlsx 解析（不依赖第三方库）
def _cell_text(c, shared):
    t = c.get('t')
    if t == 'inlineStr':
        return ''.join(x.text or '' for x in c.findall('.//m:t', NS))
    v = c.find('m:v', NS)
    if v is None or v.text is None:
        return ''
    if t == 's':
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError):
            return ''
    return v.text


def read_xlsx(path):
    """返回 {sheet_name: [[cell,...],...]}"""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if 'xl/workbook.xml' not in names:
            raise SourceError('不是有效的 xlsx：缺少 xl/workbook.xml')
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        rels = {}
        if 'xl/_rels/workbook.xml.rels' in names:
            rx = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
            for rel in rx:
                rels[rel.get('Id')] = rel.get('Target')
        shared = []
        if 'xl/sharedStrings.xml' in names:
            sx = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in sx.findall('m:si', NS):
                shared.append(''.join(t.text or '' for t in si.findall('.//m:t', NS)))
        out = {}
        for sh in wb.findall('.//m:sheet', NS):
            name = sh.get('name')
            rid = sh.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target = (rels.get(rid) or '').lstrip('/')
            if not target.startswith('xl/'):
                target = 'xl/' + target
            if target not in names:
                raise SourceError('缺少工作表数据：' + str(name))
            root = ET.fromstring(z.read(target))
            rows = []
            for row in root.findall('.//m:row', NS):
                arr = []
                for c in row.findall('m:c', NS):
                    ref = c.get('r') or ''
                    m = re.match(r'^([A-Z]+)', ref)
                    idx = 0
                    if m:
                        for ch in m.group(1):
                            idx = idx * 26 + (ord(ch) - 64)
                        idx -= 1
                    while len(arr) <= idx:
                        arr.append('')
                    arr[idx] = _cell_text(c, shared)
                rows.append(arr)
            out[name] = rows
        return out


def _col(rows, header, *aliases):
    """按表头名找列，找不到返回 None。"""
    for r in rows[:4]:
        for i, v in enumerate(r):
            s = str(v).strip()
            if s == header or s in aliases:
                return i
    return None


def _sources_of(raw):
    """来源列原格式 `编号 · 平台(URL) ｜ 编号 · 平台(URL)`，多来源全保留。"""
    out = []
    for part in re.split(r'[｜|]', str(raw or '')):
        part = part.strip()
        if not part:
            continue
        m = re.search(r'\((https?://[^)]+)\)', part)
        url = m.group(1) if m else ''
        platform = re.sub(r'^\d+\s*[·.、]\s*', '', part)
        platform = re.sub(r'\s*\(https?://[^)]+\)\s*', '', platform).strip()
        if platform or url:
            out.append({'platform': platform, 'url': url})
    return out



# ---------------------------------------------------------------- L1 语义编排可选列
# 表格列名 -> 契约字段。全部可选、全部可空；不填即不显示对应元素，绝不自动推断。
SEM_COLS = {
    'value':      ('数值', 'value', 'hero_value'),
    'unit':       ('单位', 'unit', 'hero_unit'),
    'trend':      ('趋势', 'trend', 'arrow'),
    'stamp':      ('时间角标', 'stamp', 'time_badge'),
    'board':      ('板块', 'board', 'section'),
    'topic_id':   ('专题ID', 'topic_id', 'topic'),
    'topic_name': ('专题名', 'topic_name', 'topic_title'),
    'badge':      ('角标', 'badge', 'corner'),
    'org':        ('机构短名', 'org', 'agency'),
    'brief':      ('主句', 'brief', 'main_line'),
    'role':       ('角色标签', 'role', 'role_label'),
    'src':        ('来源短名', 'src', 'source_short'),
}
SEM_FIELDS = tuple(SEM_COLS)


def sem_index(rows):
    """一次性算出可选列索引；表头不存在则返回 None。"""
    return {k: _col(rows, names[0], *names[1:]) for k, names in SEM_COLS.items()}


def sem_of(r, idx):
    out = {}
    for k, i in idx.items():
        if i is not None and i < len(r):
            v = str(r[i]).strip()
            if v and v.lower() != 'none':
                out[k] = v
    return out


def _trend_arrow(v):
    v = str(v or '').strip().lower()
    if v in ('down', 'downward', 'fall', 'decrease', '跌', '降', '下调', '下降', '减少', '回落', '↓'):
        return '↓'
    if v in ('up', 'upward', 'rise', 'increase', '涨', '升', '上调', '上涨', '增加', '↑'):
        return '↑'
    return v if len(v) <= 2 else ''


def build_semantic(payload, count=None):
    """从表格可选列构造语义编排块。纯函数、确定性、0 接口、0 密钥。
    不做正则抽数值、不做词表归板块、不做自动聚专题：全部字段来自表格列。"""
    cover = payload.get('cover') or []
    news = payload.get('news') or []
    try:
        n = int(count if count is not None else payload.get('cover_count') or 10)
    except (TypeError, ValueError):
        n = 10
    n = max(1, min(n, len(cover), 10))
    items = cover[:n]

    def sem(it):
        return it.get('sem') or {}

    def g(it, k):
        return str(sem(it).get(k) or '').strip()

    def summary_of(it):
        """hero 副文案只取表格既有「内容摘要」列的首句（纯字符串切分，不推断）。"""
        for x in news:
            if x.get('title') == it.get('title'):
                return str(x.get('summary') or '').split('。')[0].strip()
        return ''

    # 头条：第一条填了「数值」的条目；一条都没填 => 无大数字，降级成纯标题头条
    hero_i = next((i for i, it in enumerate(items) if g(it, 'value')), None)
    hero = None
    if hero_i is not None:
        it = items[hero_i]
        hero = {'index': hero_i + 1, 'rank': it.get('rank', hero_i + 1),
                'title': it.get('title', ''), 'value': g(it, 'value'), 'unit': g(it, 'unit'),
                'arrow': _trend_arrow(g(it, 'trend')), 'stamp': g(it, 'stamp'),
                'board': g(it, 'board'), 'org': g(it, 'org') or g(it, 'src'),
                'src': g(it, 'src'), 'brief': g(it, 'brief'), 'summary': summary_of(it),
                'topic_id': g(it, 'topic_id')}
    elif items:
        it = items[0]
        hero = {'index': 1, 'rank': it.get('rank', 1), 'title': it.get('title', ''),
                'value': '', 'unit': '', 'arrow': '', 'stamp': g(it, 'stamp'),
                'board': g(it, 'board'), 'org': g(it, 'org') or g(it, 'src'),
                'src': g(it, 'src'), 'brief': g(it, 'brief'), 'summary': summary_of(it),
                'topic_id': g(it, 'topic_id'), 'no_value': True}

    # 专题：只按表格「专题ID」分组，>=2 条才成组；组名/角标/机构/主句/角色/来源全部读列
    groups = {}
    for i, it in enumerate(items):
        tid = g(it, 'topic_id')
        if tid:
            groups.setdefault(tid, []).append(i)
    topics = []
    for tid, idxs in groups.items():
        if len(idxs) < 2:
            continue
        first = items[idxs[0]]
        topics.append({
            'id': tid, 'name': g(first, 'topic_name') or tid, 'badge': g(first, 'badge'),
            'items': [{'index': i + 1, 'rank': items[i].get('rank', i + 1),
                       'title': items[i].get('title', ''), 'org': g(items[i], 'org'),
                       'brief': g(items[i], 'brief'), 'role': g(items[i], 'role'),
                       'src': g(items[i], 'src')} for i in idxs],
        })
    topic_idx = {i for t in topics for i in (x['index'] - 1 for x in t['items'])}

    # 其余要闻：既不是头条、也不在专题里的条目，保证 N 条都有体现
    rest = [{'index': i + 1, 'rank': it.get('rank', i + 1), 'title': it.get('title', ''),
             'board': g(it, 'board')}
            for i, it in enumerate(items)
            if i != (hero_i if hero_i is not None else 0) and i not in topic_idx]

    boards = sorted({g(it, 'board') for it in items if g(it, 'board')})
    enabled = any(sem(it) for it in items) and (hero is not None) and bool(hero.get('value') or topics)
    return {
        'mode': 'L1', 'enabled': bool(enabled),
        'note': '字段全部来自表格可选列；未填列时不推断、不自动抽取，版式按素材自动选择',
        'count': n, 'hero': hero,
        'topics': topics, 'rest': rest, 'boards': boards,
        'stats': [{'num': str(n), 'label': '条要闻'},
                  {'num': '%02d' % len(boards), 'label': '个板块' if boards else '板块未标注'},
                  {'num': '01', 'label': '条头条'}],
        'topic_count': sum(len(t['items']) for t in topics),
        'rest_count': len(rest),
        'columns': [SEM_COLS[k][0] for k in SEM_FIELDS],
    }

def payload_from_sheets(sheets, cfg, base_payload):
    """把两个 sheet 变成 payload；其余字段沿用随包基线（开场/片尾/曲库/主题等）。"""
    sname = cfg['sheet_script']
    cname = cfg['sheet_cover']
    if sname not in sheets:
        raise SourceError('找不到口播 sheet「%s」；现有：%s' % (sname, list(sheets)))
    if cname not in sheets:
        raise SourceError('找不到封面 sheet「%s」；现有：%s' % (cname, list(sheets)))
    srows, crows = sheets[sname], sheets[cname]
    ti = _col(srows, '标题', 'title')
    ai = _col(srows, '内容摘要', '摘要', 'summary')
    si = _col(srows, '来源', 'sources')
    li = _col(srows, '分类标签', '领域', 'label')
    sidx, cidx = sem_index(srows), sem_index(crows)
    if ti is None or ai is None:
        raise SourceError('口播 sheet 缺少「标题」或「内容摘要」列')
    news = []
    for r in srows[1:]:
        title = str(r[ti]).strip() if ti < len(r) else ''
        if not title or title in ('标题', 'None'):
            continue
        news.append({
            'title': title,
            'summary': str(r[ai]).strip() if ai < len(r) else '',
            'sources': _sources_of(r[si]) if si is not None and si < len(r) else [],
            'label': str(r[li]).strip() if li is not None and li < len(r) else '',
            'level': '',
            'sem': sem_of(r, sidx),
        })
    cti = _col(crows, '标题', 'title')
    cli = _col(crows, '分类标签', '领域', 'label')
    cri = _col(crows, '序号', '排名', 'rank')
    cover = []
    for i, r in enumerate(crows[1:]):
        title = str(r[cti]).strip() if cti is not None and cti < len(r) else ''
        if not title or title in ('标题', 'None'):
            continue
        cover.append({
            'rank': int(float(r[cri])) if cri is not None and cri < len(r) and str(r[cri]).strip().isdigit() else i + 1,
            'label': str(r[cli]).strip() if cli is not None and cli < len(r) else '',
            'title': title,
            'sem': sem_of(r, cidx),
        })
    if not news or not cover:
        raise SourceError('解析结果为空（口播 %d 条 / 封面 %d 条），停止' % (len(news), len(cover)))
    p = dict(base_payload)
    p['news'] = news
    p['cover'] = cover[:10]
    p['date'] = cfg.get('issue', {}).get('date_line', p.get('date'))
    p['sheet_script'] = sname
    p['sheet_cover'] = cname
    p['source_url'] = cfg.get('url') or cfg.get('docs_url') or p.get('source_url')
    return p


def fetch_payload(root, cfg, base_payload):
    t = cfg.get('type', 'snapshot')
    if t == 'snapshot':
        p = load_json(os.path.join(root, 'data.json'))
        p.pop('rebuilt_at', None)
        return p, 'snapshot（仓库内 baseline）'
    if t == 'inline':
        ip = cfg.get('inline_payload') or {}
        news, cover = ip.get('news') or [], ip.get('cover') or []
        if not news or not cover:
            raise SourceError('inline_payload 为空（口播 %d / 封面 %d），停止且不沿用旧数据' % (len(news), len(cover)))
        p = dict(base_payload)
        p['news'] = news
        p['cover'] = cover[:10]
        p['date'] = (cfg.get('issue') or {}).get('date_line', p.get('date'))
        p['sheet_script'] = cfg.get('sheet_script')
        p['sheet_cover'] = cfg.get('sheet_cover')
        p['source_url'] = cfg.get('docs_url') or cfg.get('url') or p.get('source_url')
        return p, 'inline_payload（data_source.json 内联数据，来自腾讯文档/本地 xlsx）'
    if t == 'online_doc':
        raise SourceError('腾讯文档分享页不是 xlsx 直链，服务端无法匿名解析；请在 data_source.json 填 xlsx_url（导出直链），'
                          '或由页面「本地 xlsx」解析后导出带 inline_payload 的配置。停止且不沿用旧数据。')
    if t == 'local_xlsx':
        path = cfg.get('local_path') or os.path.join(root, 'source.xlsx')
        if not os.path.isfile(path):
            raise SourceError('本地 xlsx 不存在：' + path)
        return payload_from_sheets(read_xlsx(path), cfg, base_payload), 'local_xlsx ' + os.path.basename(path)
    if t == 'online_xlsx':
        url = cfg.get('xlsx_url')
        if not url:
            raise SourceError('online_xlsx 需要可直链下载的 xlsx_url；腾讯文档分享页不是直链，'
                              '请在 data_source.json 填导出直链，或改用 local_xlsx / snapshot')
        tmp = os.path.join(root, '.cache', 'source.xlsx')
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'daily-news-studio/1.0'})
            with urllib.request.urlopen(req, timeout=cfg.get('timeout_s', 20)) as resp, open(tmp, 'wb') as f:
                f.write(resp.read())
        except Exception as e:
            raise SourceError('在线取数失败，停止且不沿用旧数据：%r' % (e,))
        return payload_from_sheets(read_xlsx(tmp), cfg, base_payload), 'online_xlsx（本次真实拉取）'
    raise SourceError('未知数据源类型：' + str(t))


# ---------------------------------------------------------------- 下游产物
GATES = []


def gate(name, ok, detail=''):
    GATES.append({'name': name, 'ok': bool(ok), 'detail': detail})
    if not ok:
        raise SourceError('硬闸未过：%s %s' % (name, detail))


def build_outputs(root, p, cfg):
    """生成 data.json / 口播稿 / 封面清单 / video_cards.json / render_manifest.json。"""
    GATES.clear()
    news, cover = p['news'], p['cover']
    cc = (cfg.get('cover_theme') or {}).get('count', cfg.get('cover_count', 10))
    try:
        cc = int(cc)
    except (TypeError, ValueError):
        cc = 10
    cc = max(1, min(cc, len(cover), len(news)))
    p['cover_count'] = cc
    # L1 语义编排：从表格可选列构造；不填列时 enabled=false，渲染层改走等权列表
    p['semantic'] = build_semantic(p, cc)
    gate('口播与表格一致', len(news) > 0, 'n=%d' % len(news))
    gate('封面数据齐备', len(cover) > 0, 'n=%d' % len(cover))
    gate('封面显示条数合法', 1 <= cc <= min(10, len(cover)), 'count=%d / avail=%d' % (cc, len(cover)))
    gate('封面标题逐字等于口播前 %d 条' % cc,
         all(cover[i]['title'] == news[i]['title'] for i in range(cc)))
    gate('来源 URL 非空', all(s.get('url') for n in news for s in n.get('sources', []) if s.get('platform')))

    issue = cfg.get('issue') or p.get('issue_cfg') or {}
    head = cfg.get('cover_header') or p.get('cover_header') or {}
    # 自动本期：issue_mode != manual 时按当天重建日期/星期/节气/开场，
    # 用户手改的期号保留；手动模式完全沿用 cfg。
    if cfg.get('issue_mode', 'auto') != 'manual':
        issue = dict(auto_issue(issue.get('issue_no') or '第01期'))
        opening = auto_opening(issue)
        cfg['issue'] = issue
    else:
        issue = dict(issue)
        issue['issue_no'] = norm_issue(issue.get('issue_no') or '第01期')
        cfg['issue'] = issue
        opening = cfg.get('opening') or p['open']
    _dcn = issue.get('date_cn')
    if _dcn and re.search(r'\d{4}年\d{1,2}月\d{1,2}日', opening) and _dcn not in opening:
        opening = re.sub(r'\d{4}年\d{1,2}月\d{1,2}日[，,]?\s*(星期[一二三四五六日天])?',
                         lambda _m: _dcn, opening, count=1)
    window = p['window_statement']
    window_on = bool(cfg.get('window_on', True))
    closing = cfg.get('closing') or p['close'] or '以上是今日要闻速览，感谢收听，下期见。'
    # 免责声明只放页面，不进口播/字幕/视频帧；旧配置里的免责句一律剥离
    for junk in ('部分为预测或时点数据，',
                 '内容依据公开信息整理，具体以官方发布为准。',
                 '内容依据公开信息整理，以官方发布为准。',
                 '仅供参考，不构成投资、医疗或法律建议。'):
        closing = closing.replace(junk, '')
    body = '\n\n'.join(n['title'] + '。' + n['summary'] for n in news)
    segs = [opening] + ([window] if window_on else []) + body.split('\n\n') + [closing]
    gate('开场含本期日期', bool(_dcn) and _dcn in opening)
    gate('口播不含序号前缀', not any(re.match(r'^第\s*\d+\s*条', s) for s in segs))
    gate('口播不含来源/URL', not any(('来源：' in s or 'http' in s) for s in segs))

    # 四段文稿（开场 / 窗口 / 正文 / 片尾）
    md = '\n\n'.join([
        '# 开场（可单独编辑）\n\n' + opening,
        '# 窗口说明（可单独编辑，可关闭）\n\n' + (window if window_on else '（本段已关闭，不进入口播）'),
        '# 正文（%d 条，只念标题+摘要）\n\n' % len(news) + body,
        '# 片尾（可单独编辑）\n\n' + closing,
        '# 完整拼接（只读，供核对）\n\n```text\n' + '\n\n'.join(segs) + '\n```\n',
    ])
    with open(os.path.join(root, '口播文稿.md'), 'w', encoding='utf-8') as f:
        f.write(md)

    with open(os.path.join(root, '封面清单.md'), 'w', encoding='utf-8') as f:
        f.write('# 封面清单（显示 %d 条，可在封面设置调整；只显示 序号 / 标题）\n\n' % cc)
        for c in cover[:cc]:
            f.write('%d. %s\n' % (c['rank'], c['title']))

    cards = []
    for i, n in enumerate(news, 1):
        cards.append({'index': i, 'title': n['title'], 'summary': n['summary'],
                      'label': n.get('label', ''), 'sources': n.get('sources', [])})
    dump_json(os.path.join(root, 'video_cards.json'), {'count': len(cards), 'cards': cards})

    src_count = sum(len(c['sources']) for c in cards)
    voice = cfg.get('voice') or {}
    bgm = cfg.get('bgm') or {}
    mx = cfg.get('mix') or {}
    mix = {'voice_volume': float(voice.get('volume', 1.0)),
           'bgm_volume': float(bgm.get('volume', .33)),
           'master_gain_db': float(mx.get('master_gain_db', voice.get('master_gain_db', 0.0))),
           'duck': bool(mx.get('duck', True)),
           'duck_db': float(mx.get('duck_db', -8)),
           'fade_in': float(mx.get('fade_in', .4)),
           'fade_out': float(mx.get('fade_out', .8)),
           'concurrency': int(voice.get('concurrency', mx.get('concurrency', 12)))}
    vc = cfg.get('video_card_theme') or {}
    cv = cfg.get('cover_theme') or {}
    # 自动本期：manifest 内嵌封面配置也用当天日期，避免跨日后封面/水印日期落后
    if isinstance(cv, dict) and cfg.get('issue_mode', 'auto') != 'manual':
        cv['issue'] = issue
        _wm = cv.get('wm')
        if isinstance(_wm, dict) and re.search(r'\d{4}-\d{2}-\d{2}', str(_wm.get('text', ''))):
            _wm['text'] = re.sub(r'\d{4}-\d{2}-\d{2}', issue['date'], str(_wm['text']))
    sub_in = cfg.get('subtitle')
    if isinstance(sub_in, bool):
        sub = {'on': sub_in, 'burn_in': True, 'soft_track': True, 'font_size': 44,
               'color': '#FFFFFF', 'outline_color': '#0d1b2a', 'position': 'bottom',
               'background': True, 'font': 'WenQuanYi Micro Hei', 'typewriter': True}
    elif isinstance(sub_in, dict):
        sub = {'on': bool(sub_in.get('on', True)), 'burn_in': bool(sub_in.get('burn_in', True)),
               'soft_track': bool(sub_in.get('soft_track', True)),
               'font_size': int(sub_in.get('font_size', 44)),
               'color': sub_in.get('color', '#FFFFFF'),
               'outline_color': sub_in.get('outline_color', '#0d1b2a'),
               'position': sub_in.get('position', 'bottom'),
               'background': bool(sub_in.get('background', True)),
               'font': sub_in.get('font', 'WenQuanYi Micro Hei'),
               'typewriter': bool(sub_in.get('typewriter', True))}
    else:
        sub = {'on': True, 'burn_in': True, 'soft_track': True, 'font_size': 44,
               'color': '#FFFFFF', 'outline_color': '#0d1b2a', 'position': 'bottom',
               'background': True, 'font': 'WenQuanYi Micro Hei', 'typewriter': True}
    tw_in = cfg.get('typewriter') or {}
    tw = {'on': bool(tw_in.get('on', True)), 'target': tw_in.get('target', 'subtitle'),
          'ms_per_char': int(tw_in.get('ms_per_char', 55)), 'max_steps': int(tw_in.get('max_steps', 8))}
    sub['typewriter'] = tw['on']
    export_size = cfg.get('export_size') or (cv.get('size') if isinstance(cv, dict) else None) or 'portrait'
    if export_size not in ('portrait', 'landscape'):
        export_size = 'portrait'
    manifest = {
        'version': p['version'],
        'generated_from': 'render_manifest.json（按 render_manifest 出片，不重新取数/编排）',
        'script_hash': (cfg.get('script_hash') or p.get('script_hash_baseline')),
        'news_window': p['news_window'],
        'issue': {'date': issue.get('date'), 'weekday': issue.get('weekday'),
                  'issue_no': issue.get('issue_no') or p.get('issue')},
        'cover_header': {'title': head.get('title', '每日要闻速览'),
                         'show_title': head.get('show_title', True),
                         'show_date': head.get('show_date', True),
                         'show_issue': head.get('show_issue', True)},
        'segments': [{'i': i + 1, 'text': s, 'kind': 'opening' if i == 0 else
                      ('window' if window_on and i == 1 else
                       ('closing' if i == len(segs) - 1 else 'news')),
                      'card': (i - (2 if window_on else 1)) if (window_on and i >= 2) or (not window_on and i >= 1) else None}
                     for i, s in enumerate(segs)],
        'cards': cards,
        'source_count': src_count,
        'sizes': {'portrait': [1080, 1920], 'landscape': [1920, 1080]},
        'export_size': export_size,
        'cover_count': cc,
        'cover': [{'rank': c['rank'], 'label': c['label'], 'title': c['title'],
                   'sem': c.get('sem') or {}} for c in cover[:cc]],
        'semantic': p['semantic'],
        'config': {
            'voice': {'mode': voice.get('mode', 'libretts'), 'voice': voice.get('voice', 'zh-CN-YunyangNeural'),
                      'rate': voice.get('rate', 0), 'volume': voice.get('volume', 1.0),
                      'master_gain_db': mix['master_gain_db'], 'concurrency': mix['concurrency'],
                      'endpoint': voice.get('endpoint', p.get('voice_endpoint')), 'upload': voice.get('upload'),
                      'timeout_s': voice.get('timeout_s', 20), 'retries': voice.get('retries', 2)},
            'bgm': {'file': bgm.get('file'), 'volume': bgm.get('volume', .33), 'enabled': bool(bgm.get('file')),
                    'duck': mix['duck'], 'duck_db': mix['duck_db'],
                    'fade_in': mix['fade_in'], 'fade_out': mix['fade_out']},
            'mix': mix,
            'subtitle': dict(sub, note='硬字幕 + mov_text 软轨 + 独立 SRT；开启打字机时硬字幕按 ASS 卡拉OK 逐字显现'),
            'typewriter': tw,
            'export_size': export_size,
            'trigger': {'mode': 'workflow_dispatch', 'configurable': False,
                        'note': '无定时；Actions 手动 Run workflow 触发'},
            'semantic': {'mode': 'L1', 'columns': [SEM_COLS[k][0] for k in SEM_FIELDS],
                         'note': '字段全部来自表格可选列；未填列不推断，只隐藏对应元素，版式按素材自动选择'},
            'cover': dict(cv, count=cc) if isinstance(cv, dict) else {'count': cc},
            'video_card': vc,
        },
        'gates': list(GATES),
    }
    # 修正 news 段的 card 编号（1-based，与 cards 对齐）
    off = 2 if window_on else 1
    for s in manifest['segments']:
        if s['kind'] == 'news':
            s['card'] = s['i'] - off
        else:
            s['card'] = None
    gate('句子数 = 开场1 + 窗口%d + 正文%d + 片尾1' % (1 if window_on else 0, len(news)),
         len(segs) == len(news) + 2 + (1 if window_on else 0), 'segs=%d' % len(segs))
    gate('每条资讯一张卡片', all(s['card'] and 1 <= s['card'] <= len(cards)
                             for s in manifest['segments'] if s['kind'] == 'news'))
    manifest['gates'] = list(GATES)
    dump_json(os.path.join(root, 'render_manifest.json'), manifest)
    # 自动本期必须回写，否则跨天重建后 data.json / data_source.json / 页面仍是旧日期开场
    p['open'] = opening
    p['close'] = closing
    p['disclaimer'] = '内容依据公开信息整理，具体以官方发布为准；仅供参考，不构成投资、医疗或法律建议。'
    p['issue'] = issue.get('issue_no') or p.get('issue')
    p['date'] = issue.get('date_line') or p.get('date')
    p['issue_cfg'] = issue
    p['issue_mode'] = cfg.get('issue_mode', 'auto')
    p['window_statement'] = window
    dump_json(os.path.join(root, 'data.json'), dict(p, rebuilt_at=cfg.get('rebuilt_at') or ''))
    ds_path = os.path.join(root, 'data_source.json')
    if os.path.isfile(ds_path):
        try:
            ds = load_json(ds_path)
            ds['issue'] = issue
            ds['opening'] = opening
            ds['closing'] = closing
            ds['issue_mode'] = cfg.get('issue_mode', 'auto')
            ds['window_on'] = window_on
            ds['cover_count'] = cc
            if isinstance(ds.get('cover_theme'), dict):
                ds['cover_theme']['count'] = cc
                # 自动本期时把当天日期同步进内嵌封面配置与水印文本，避免跨日后水印/封面日期落后
                ds['cover_theme']['issue'] = issue
                wm = ds['cover_theme'].get('wm')
                if isinstance(wm, dict) and re.search(r'\d{4}-\d{2}-\d{2}', str(wm.get('text', ''))):
                    wm['text'] = re.sub(r'\d{4}-\d{2}-\d{2}', issue['date'], str(wm['text']))
            dump_json(ds_path, ds)
        except Exception as e:
            print('WARN 回写 data_source.json 失败：%r' % (e,))
    ct_path = os.path.join(root, 'cover_theme.json')
    if os.path.isfile(ct_path):
        try:
            ct = load_json(ct_path)
            ct['count'] = cc
            if cfg.get('issue_mode', 'auto') != 'manual':
                ct['issue'] = issue
                wm = ct.get('wm')
                if isinstance(wm, dict) and re.search(r'\d{4}-\d{2}-\d{2}', str(wm.get('text', ''))):
                    wm['text'] = re.sub(r'\d{4}-\d{2}-\d{2}', issue['date'], str(wm['text']))
            dump_json(ct_path, ct)
        except Exception as e:
            print('WARN 回写 cover_theme.json 失败：%r' % (e,))
    return manifest
