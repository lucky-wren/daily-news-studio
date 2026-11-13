# -*- coding: utf-8 -*-
"""渲染层：封面（6 主题 × 3 模板 × 2 尺寸 × 3 留白 × 水印）与视频卡片帧（3 模板）。

画布比例锁死：竖 1080×1920(9:16)、横 1920×1080(16:9)，内容只在画布内自适应。
三层分工：封面只有 序号/标题（不显示分类标签）；视频卡片才有 标题/摘要/来源(平台+完整地址)。
"""
import os, json, math
import source as SRC   # 期号规范化与各处共享
from PIL import Image, ImageDraw, ImageFont

THEMES = {
    'deep_blue': {'n': '深墨蓝', 'bg': '#0d1b2a', 'txt': '#f2f6fb', 'sub': '#a9bdd4', 'line': 'rgba(255,255,255,.10)', 'card': 'rgba(255,255,255,.045)', 'acc': '#e6432f', 'dark': True},
    'obsidian':  {'n': '曜石黑', 'bg': '#14161a', 'txt': '#f5f6f8', 'sub': '#a7adb8', 'line': 'rgba(255,255,255,.09)', 'card': 'rgba(255,255,255,.04)',  'acc': '#e6432f', 'dark': True},
    'indigo':    {'n': '靛蓝夜', 'bg': '#101a38', 'txt': '#f2f5ff', 'sub': '#a9b8de', 'line': 'rgba(255,255,255,.10)', 'card': 'rgba(255,255,255,.045)', 'acc': '#e6432f', 'dark': True},
    'daylight':  {'n': '晨光白', 'bg': '#f7f9fc', 'txt': '#16283f', 'sub': '#5a6c82', 'line': 'rgba(16,40,63,.13)',  'card': 'rgba(16,40,63,.035)',  'acc': '#c8321f', 'dark': False},
    'paper':     {'n': '暖米纸', 'bg': '#faf5ea', 'txt': '#2a2118', 'sub': '#756454', 'line': 'rgba(42,33,24,.13)',  'card': 'rgba(42,33,24,.035)',  'acc': '#c03a24', 'dark': False},
    'ice':       {'n': '浅冰蓝', 'bg': '#f0f6fc', 'txt': '#12263d', 'sub': '#5a7390', 'line': 'rgba(18,38,61,.12)',  'card': 'rgba(18,38,61,.035)',  'acc': '#c8321f', 'dark': False},
}
SIZES = {'portrait': (1080, 1920), 'landscape': (1920, 1080)}
GAPS = {'compact': {'gap': .18, 'pad': .55}, 'standard': {'gap': .62, 'pad': .80}, 'airy': {'gap': 1.0, 'pad': 1.0}}
AVOID_HEAD = set('，。；：！？、）》」』】,.!?;:)]}')


HEAD_DATE_GAP = 46   # 日期基线距标题底部；与 index.html 的 HEAD_DATE_GAP 同值
HEAD_RULE_AT = 64   # 日期之后分隔线位置；与页面同值


def cover_count(cfg, payload):
    """显示条数：来自 cover_theme.json / data_source.json / data.json，缺省 10。"""
    v = cfg.get('count', payload.get('cover_count', 10))
    try:
        v = int(v)
    except (TypeError, ValueError):
        v = 10
    return max(1, min(v, len(payload.get('cover') or []) or 10))

_FONT_CACHE = {}


def font_path(root):
    for p in [os.path.join(root, 'assets', 'fonts', 'wqy-microhei.ttc'),
              '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
              '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if os.path.isfile(p):
            return p
    return None


def F(size, root, bold=False):
    key = (int(size), bold)
    if key not in _FONT_CACHE:
        p = font_path(root)
        try:
            _FONT_CACHE[key] = ImageFont.truetype(p, int(size)) if p else ImageFont.load_default()
        except OSError:
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def rgb(hexs):
    h = hexs.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(bg, fg, a):
    return tuple(int(bg[i] * (1 - a) + fg[i] * a) for i in range(3))


def rgba_color(s, base):
    """把 'rgba(r,g,b,a)' 混到纯色底上，避免半透明直接写进 RGB 底图丢 alpha。"""
    if not s.startswith('rgba'):
        return rgb(s)
    nums = s[s.find('(') + 1:s.find(')')].split(',')
    fg = tuple(int(float(x)) for x in nums[:3])
    a = float(nums[3])
    return mix(base, fg, a)


def wrap(draw, text, font, maxw, avoid=True):
    """按像素宽度断行；闭标点不落在行首；不截断文本。"""
    lines, cur = [], ''
    for ch in text:
        if ch == '\n':
            lines.append(cur); cur = ''; continue
        t = cur + ch
        if cur and draw.textlength(t, font=font) > maxw:
            if avoid and ch in AVOID_HEAD and cur:
                # 把闭标点挤到上一行末尾（允许轻微超宽）
                lines.append(cur + ch); cur = ''
                continue
            lines.append(cur); cur = ch
        else:
            cur = t
    if cur:
        lines.append(cur)
    # 末行避免孤字：从上一行末尾挪 1-2 字下来，不截断、不超宽
    if len(lines) >= 2 and len(lines[-1]) <= 2:
        prev = lines[-2]
        mv = 3 - len(lines[-1])
        if len(prev) > mv + 1:
            lines[-2] = prev[:-mv]
            lines[-1] = prev[-mv:] + lines[-1]
    return lines


def txt(draw, xy, s, font, fill, bold=False, anchor=None):
    # 小字号不再加描边：文泉驿的横画密集字（如「量」）会被 1px 描边糊成方块
    sw = 1 if bold and getattr(font, 'size', 0) >= 64 else 0
    if sw:
        draw.text(xy, s, font=font, fill=fill, anchor=anchor, stroke_width=sw, stroke_fill=fill)
    else:
        draw.text(xy, s, font=font, fill=fill, anchor=anchor)


# ------------------------------------------------------------------ 封面
def render_cover(root, payload, cfg, out_path, meta_path=None):
    if cfg.get('tpl') == 'semantic':
        return render_semantic_cover(root, payload, cfg, out_path, meta_path)
    theme = THEMES.get(cfg.get('theme', 'deep_blue'), THEMES['deep_blue'])
    tpl = cfg.get('tpl', 'rank')
    size = cfg.get('size', 'portrait')
    gapmode = cfg.get('gap', 'airy')
    wm = cfg.get('wm') or {}
    head = cfg.get('cover_header') or payload.get('cover_header') or {}
    issue = cfg.get('issue') or payload.get('issue_cfg') or {}
    W, H = SIZES.get(size, SIZES['portrait'])
    # 横竖共用短边缩放：16:9 与 9:16 字号一致，不再按宽度放大
    u = min(W, H) / 1080.0
    base = rgb(theme['bg'])
    line_c = rgba_color(theme['line'], base)
    acc = rgb(theme['acc'])
    fg = rgb(theme['txt'])
    sub = rgb(theme['sub'])
    dark = theme['dark']

    # 04 起用反相高对比实色块：深底→浅块深字，浅底→深块白字
    chip_bg = rgb('#dbe8f8') if dark else rgb('#16283f')
    soft_ink = rgb('#101f33') if dark else (255, 255, 255)
    edge = chip_bg
    num_soft = soft_ink

    im = Image.new('RGB', (W, H), base)
    d = ImageDraw.Draw(im)
    boxes = []

    # 顶部朱红细线
    d.rectangle([0, 0, W, max(3, int(6 * u))], fill=acc)

    pad = int(58 * u)
    y = int(86 * u)
    show_title = head.get('show_title', True)
    show_date = head.get('show_date', True)
    show_issue = head.get('show_issue', True)

    if show_title:
        fs = int(100 * u)
        f = F(fs, root, True)
        title = head.get('title', '每日要闻速览')
        txt(d, (pad, y), title, f, fg, True)
        y += int(fs * 1.22)
        d.rectangle([pad, y + int(14 * u), pad + int(96 * u), y + int(20 * u)], fill=acc)
        y += int(52 * u)
    else:
        y += int(24 * u)

    # 榜单模板与语义版共用报头标签：日期深色实底+朱红竖条，期号朱红实底，均白字。
    rco = _sem_palette(theme)
    if show_date:
        f = F(int(30 * u), root, True)
        dl = issue.get('date_line') or payload.get('date', '')
        # 榜单版日期中心 y+40*u，与语义版同几何（中心 = 顶 + 34*u）
        _date_badge(d, pad, y + int(6 * u), dl, f, rco, u)
    if show_issue:
        f = F(int(30 * u), root, True)
        s = SRC.norm_issue(issue.get('issue_no') or payload.get('issue') or '第01期')
        _issue_badge(d, W - pad, y + int(10 * u), s, f, rco, u)
    y += int(96 * u) if (show_date or show_issue) else int(30 * u)
    head_bottom = y

    # 内容区：条数可设置；竖版单列 N 行，横版 2 列（前 ceil(N/2) 左、其余右）
    n = cover_count(cfg, payload)
    items = payload['cover'][:n]
    cols = 2 if (size == 'landscape' and n >= 2) else 1
    rows = math.ceil(n / cols)
    bottom_pad = int(58 * u)
    avail = H - head_bottom - bottom_pad
    colw = (W - 2 * pad - (cols - 1) * int(30 * u)) / cols
    fill = {'compact': .99, 'standard': .97, 'airy': .95}.get(gapmode, .95)
    # 行距由留白档位给定上限，条数多时自动收紧但不撑满；
    # 整组资讯作为一个块在内容区上下居中，字号不因条数少而放大。
    wanted_gap = int({'compact': 12, 'standard': 28, 'airy': 46}.get(gapmode, 46) * u)
    min_gap = max(2, int(3 * u))
    cap = avail * (.99 if rows >= 8 else fill) / rows

    k = 1.0 if size == 'portrait' else .86
    badge = int(50 * k * u)
    num_fs = int(30 * k * u)
    nf = F(num_fs, root, True)
    # 字号固定，不随条数放大：条数少时 cell_h 变大，内容在行内垂直居中，
    # 多出来的高度全部变成行间留白；只在确实放不下时才缩小。
    min_fs = int(24 * u)
    title_fs = int((44 if size == 'portrait' else 38) * u)
    line_factor = 1.18
    tx_off = int(20 * u) + badge + int(20 * u)
    maxw = colw - tx_off - int(22 * u)

    def _measure(fs):
        f = F(fs, root, True)
        out = []
        for it in items:
            ls = wrap(d, it['title'], f, maxw)
            th = len(ls) * int(fs * line_factor)
            out.append((ls, max(badge, th), th))
        return f, out

    tf, blocks = _measure(title_fs)

    def _fits(bs):
        return max(b[1] for b in bs) + min_gap <= cap

    while title_fs > min_fs and not _fits(blocks):
        title_fs -= max(1, int(1 * u))
        tf, blocks = _measure(title_fs)

    max_bh = max(b[1] for b in blocks)
    row_gap = min(wanted_gap, max(min_gap, int(cap - max_bh)))
    row_h = max_bh + row_gap
    block_h = rows * row_h
    top = head_bottom + max(0, (avail - block_h) / 2)
    cell_h = row_h  # 兼容旧校验字段：每行高度

    band = mix(base, (255, 255, 255) if dark else (0, 0, 0), .075)
    plain_num = (tpl == 'list')
    for i, it in enumerate(items):
        c = i // rows
        r = i % rows
        x0 = pad + c * (colw + int(30 * u))
        y0 = top + r * row_h
        x1, y1 = x0 + colw, y0 + row_h
        boxes.append([x0, y0, x1, y1])
        ls, bh, th = blocks[i]
        cy = y0 + (row_h - bh) / 2
        by = cy + (bh - badge) / 2

        # 三套模板统一隔行底色
        if r % 2 == 1:
            d.rounded_rectangle([x0, y0 + 2 * u, x1, y1 - 2 * u],
                                radius=int(10 * u), fill=band)
        # 结构差异：榜单卡片=逐行圆角卡；极简列表=顶部细线+纯文字序号；报头重点=首条头条块
        if tpl == 'rank':
            d.rounded_rectangle([x0, y0 + 3 * u, x1, y1 - 3 * u],
                                radius=int(10 * u), outline=line_c, width=1)
            if i < 3:
                d.rectangle([x0, y0 + 3 * u, x0 + 6 * u, y1 - 3 * u], fill=acc)
        elif tpl == 'list':
            d.line([x0, y0, x1, y0], fill=line_c, width=1)
        else:
            if i == 0:
                d.rounded_rectangle([x0, y0 + 2 * u, x1, y1 - 2 * u],
                                    radius=int(10 * u), fill=mix(base, acc, .16))
                d.rectangle([x0, y0 + 2 * u, x0 + 10 * u, y1 - 2 * u], fill=acc)
                txt(d, (x0 + tx_off, y0 + 6 * u), '头条', F(int(20 * u), root, True), acc, True)
            else:
                d.line([x0, y1 - 1, x1, y1 - 1], fill=line_c, width=1)

        strong = i < 3
        bx = x0 + int(20 * u)
        if plain_num:
            txt(d, (bx, cy + bh / 2), '%02d' % it['rank'], nf,
                acc if strong else fg, True, 'lm')
        else:
            if strong:
                d.rounded_rectangle([bx, by, bx + badge, by + badge], radius=int(11 * u), fill=acc)
                numc = (255, 255, 255)
            else:
                d.rounded_rectangle([bx, by, bx + badge, by + badge], radius=int(11 * u),
                                    fill=chip_bg, outline=edge, width=max(1, int(2 * u)))
                numc = num_soft
            txt(d, (bx + badge / 2, by + badge / 2), '%02d' % it['rank'], nf, numc, True, 'mm')

        tx = x0 + (int(20 * u) + int(70 * u) if plain_num else tx_off)
        ty = cy + (bh - th) / 2
        for ln in ls:
            txt(d, (tx, ty), ln, tf, fg, True)
            ty += int(title_fs * line_factor)

    # 水印：只画在底部预留区或内容之下，不压正文
    if wm.get('on'):
        a = float(wm.get('alpha', .12))
        wf = F(int(30 * u), root)
        s = str(wm.get('text', ''))
        pos = wm.get('pos', 'bottom_right')
        wcol = mix(base, fg, a)
        if pos in ('bottom_right', 'bottom_left'):
            wy = H - int(30 * u)
            tw = d.textlength(s, font=wf)
            wx = W - pad - tw if pos == 'bottom_right' else pad
            txt(d, (wx, wy), s, wf, wcol, anchor='lm')
        elif pos == 'center':
            txt(d, (W / 2, H / 2), s, F(int(96 * u), root), wcol, anchor='mm')
        else:
            for yy in range(int(120 * u), H, int(220 * u)):
                for xx in range(int(40 * u), W, int(420 * u)):
                    txt(d, (xx, yy), s, wf, wcol)

    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    im.save(out_path)
    meta = {'type': 'cover', 'w': W, 'h': H, 'theme': cfg.get('theme'), 'tpl': tpl,
            'size': size, 'gap': gapmode, 'boxes': boxes,
            'used': max(b[1] for b in blocks), 'avail': avail,
            'head_bottom': head_bottom, 'watermark': bool(wm.get('on')),
            'count': n, 'cols': cols, 'rows': rows,
            'items': len(items), 'title_fs': title_fs, 'cell_h': round(cell_h, 2),
            'row_h': round(row_h, 2), 'row_gap': row_gap,
            'centered': True, 'top_gap': round(top - head_bottom, 2),
            'bottom_gap': round(H - bottom_pad - (top + block_h), 2),
            'whitespace': round((avail - block_h) / 2, 2),
            'fixed_font': True, 'label_chip': False, 'divider': tpl != 'list',
            'alternate_bands': True, 'template_structure': tpl}
    if meta_path:
        os.makedirs(os.path.dirname(meta_path) or '.', exist_ok=True)
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


# ------------------------------------------------------------------ 视频卡片帧
def render_card(root, card, cfg, out_path, meta_path=None, reveal=1.0):
    theme = THEMES.get(cfg.get('theme', 'deep_blue'), THEMES['deep_blue'])
    tpl = cfg.get('tpl', 'frame_card')
    size = cfg.get('size', 'portrait')
    W, H = SIZES.get(size, SIZES['portrait'])
    u = min(W, H) / 1080.0
    base = rgb(theme['bg']); fg = rgb(theme['txt']); sub = rgb(theme['sub'])
    acc = rgb(theme['acc']); line_c = rgba_color(theme['line'], base)
    card_bg = rgba_color(theme['card'], base)
    im = Image.new('RGB', (W, H), base)
    d = ImageDraw.Draw(im)
    pad = int(72 * u)

    title_fs = int((70 if size == 'portrait' else 60) * u)
    sum_fs = int((46 if size == 'portrait' else 40) * u)   # 摘要放大，与页面同尺度
    tf = F(title_fs, root, True); sf = F(sum_fs, root)

    maxw = W - 2 * pad
    rv = max(0.0, min(1.0, float(reveal)))
    ttl = card['title'] if rv >= 1 else card['title'][:max(0, int(round(len(card['title']) * rv)))]
    smy = card['summary'] if rv >= 1 else card['summary'][:max(0, int(round(len(card['summary']) * rv)))]
    tlines = wrap(d, ttl, tf, maxw)
    slines = wrap(d, smy, sf, maxw)

    src_fs = int(30 * u)

    def _src_lines(fs):
        rf = F(fs, root)
        return [(str(s.get('platform', '')),
                 wrap(d, str(s.get('url', '')), rf, maxw - int(30 * u)))
                for s in card.get('sources', [])]

    def _block_h(sl, fs):
        return (len(tlines) * int(title_fs * 1.32) + int(56 * u)
                + len(slines) * int(sum_fs * 1.5) + int(48 * u)
                + sum((1 + len(ls)) * int(fs * 1.42) + int(12 * u) for _, ls in sl))

    src_lines = _src_lines(src_fs)
    # 来源必须完整且不越出画面：字号自动收敛，仍放不下就报错而不是截断
    while src_fs > int(16 * u) and _block_h(src_lines, src_fs) > H - int(220 * u):
        src_fs -= int(1 * u)
        src_lines = _src_lines(src_fs)
    rf = F(src_fs, root)
    block_h = _block_h(src_lines, src_fs)
    y = max(int(110 * u), (H - block_h) / 2)  # 垂直居中
    header_y = int(56 * u)

    if tpl == 'frame_card':
        d.rounded_rectangle([pad / 2, header_y + int(70 * u), W - pad / 2, H - int(80 * u)],
                            radius=int(20 * u), fill=card_bg, outline=line_c, width=1)
        badge = int(56 * u)
        d.rounded_rectangle([pad, header_y, pad + badge, header_y + badge], radius=int(10 * u), fill=acc)
        txt(d, (pad + badge / 2, header_y + badge / 2), '%02d' % card['index'], F(int(26 * u), root, True), (255, 255, 255), True, 'mm')
    elif tpl == 'frame_minimal':
        d.line([pad, header_y + int(60 * u), W - pad, header_y + int(60 * u)], fill=line_c, width=1)
        txt(d, (pad, header_y), '%02d / 30' % card['index'], F(int(24 * u), root), sub)
    else:  # frame_headline
        d.rectangle([0, 0, W, int(150 * u)], fill=acc)
        txt(d, (pad, int(52 * u)), str(card.get('label') or '今日要闻'), F(int(34 * u), root, True), (255, 255, 255), True)
        d.rectangle([pad, H - int(150 * u), W - pad, H - int(146 * u)], fill=line_c)

    for ln in tlines:
        txt(d, (pad, y), ln, tf, fg, True); y += int(title_fs * 1.32)
    y += int(56 * u)   # 标题与摘要拉开
    for ln in slines:
        txt(d, (pad, y), ln, sf, sub); y += int(sum_fs * 1.5)
    y += int(48 * u)
    for platform, urls in src_lines:
        txt(d, (pad, y), '来源 · ' + platform, rf, acc, True); y += int(src_fs * 1.42)
        for ln in urls:
            txt(d, (pad + int(18 * u), y), ln, rf, sub); y += int(src_fs * 1.42)
        y += int(12 * u)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    im.save(out_path)
    meta = {'type': 'card', 'index': card['index'], 'w': W, 'h': H, 'tpl': tpl,
            'reveal': rv, 'block_bottom': y, 'title_lines': len(tlines), 'summary_lines': len(slines),
            'sources': len(src_lines), 'src_fs': src_fs,
            'overflow': y > H - int(40 * u)}
    if meta_path:
        os.makedirs(os.path.dirname(meta_path) or '.', exist_ok=True)
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


# ================================================================== L1 语义编排封面
def _sem_palette(theme):
    bg = rgb(theme['bg'])
    fg = rgb(theme['txt'])
    sub = rgb(theme['sub'])
    acc = rgb(theme['acc'])
    line = rgba_color(theme['line'], bg)
    if theme['dark']:
        card = mix(bg, (255, 255, 255), .062)
        chip = mix(bg, (255, 255, 255), .10)
    else:
        card = mix(bg, (0, 0, 0), .045)
        chip = mix(bg, (0, 0, 0), .075)
    # 报头标签实底：日期=深色块(白字+朱红竖条)，期号=朱红块(白字)。
    # 深主题用提亮 bg 做日期底，浅主题直接用主文字色做深底，白字对比均 >8:1。
    date_box = mix(bg, (255, 255, 255), .16) if theme['dark'] else fg
    badge_red = acc if theme['dark'] else mix(acc, (0, 0, 0), .12)
    return dict(bg=bg, fg=fg, sub=sub, acc=acc, line=line, card=card, chip=chip,
                date_box=date_box, badge_red=badge_red,
                date_ink=(255, 255, 255), badge_ink=(255, 255, 255),
                dark=theme['dark'])


def _date_badge(d, x0, yy, date_cn, f, co, u):
    """日期牌：深色实底 + 朱红竖条 + 白色粗字。
    文字中心仍以 HEAD_DATE_GAP 为基准，块完整落在分隔线 HEAD_RULE_AT 之前。"""
    s = str(date_cn).replace('，', ' · ')
    tw = d.textlength(s, font=f)
    bar_x, bar_w = 10 * u, 6 * u
    tx = x0 + 32 * u
    bw = 32 * u + tw + 22 * u
    bh = 48 * u
    by = yy + 10 * u
    d.rounded_rectangle([x0, by, x0 + bw, by + bh], radius=9 * u, fill=co['date_box'])
    d.rounded_rectangle([x0 + bar_x, by + 9 * u, x0 + bar_x + bar_w, by + bh - 9 * u],
                        radius=3 * u, fill=co['acc'])
    txt(d, (tx, yy + HEAD_DATE_GAP * u - 12 * u), s, f, co['date_ink'], True, 'lm')
    return bw


def _issue_badge(d, x1, y_top, s, f, co, u):
    """期号胶囊：朱红实底 + 白字，无描边（描边此前已明确去掉）。"""
    bw = d.textlength(s, font=f) + 54 * u
    bh = 56 * u
    bx = x1 - bw
    by = y_top - 4 * u
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh / 2, fill=co['badge_red'])
    txt(d, (bx + bw / 2, by + bh / 2), s, f, co['badge_ink'], True, 'mm')
    return bx, by, bw, bh


def _spaced(d, x, y, text, font, fill, spacing=3):
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + spacing
    return x


def _fit(d, text, maxw, maxh, base, minsize, root, maxlines=3, bold=False, factor=1.28):
    """自适应字号：不截断文字，只缩字号 / 换行，直到装进给定宽高。"""
    size = int(base)
    lines, font = [], None
    while size >= max(10, int(minsize)):
        font = F(size, root, bold)
        lines = wrap(d, text, font, maxw)
        if len(lines) <= maxlines and len(lines) * size * factor <= maxh:
            return font, lines, size
        size -= 1
    font = F(max(10, int(minsize)), root, bold)
    return font, wrap(d, text, font, maxw), max(10, int(minsize))


def _para(d, x, y, text, font, fill, lh):
    # 已按宽度断好的行由调用方给；这里只画一整段
    for i, ln in enumerate(text):
        txt(d, (x, y + i * lh), ln, font, fill, getattr(font, 'size', 0) >= 64)
    return y + len(text) * lh


def _box(d, co, x0, y0, x1, y1, r=20, side=False):
    d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=co['card'], outline=co['line'], width=1)
    if side:
        d.rounded_rectangle([x0, y0, x0 + 6, y1], radius=3, fill=co['acc'])


def _rule(d, co, x0, x1, y):
    d.line([x0, y, x1, y], fill=co['line'], width=1)


def _sec(d, co, x, y, name, meta, right, x2, root):
    d.rounded_rectangle([x, y + 3, x + 5, y + 33], radius=2, fill=co['acc'])
    f = F(34, root, True)
    txt(d, (x + 18, y), name, f, co['fg'], True)
    ox = x + 18 + d.textlength(name, font=f) + 14
    txt(d, (ox, y + 8), meta, F(21, root), co['sub'])
    if right:
        txt(d, (x2, y + 8), right, F(21, root), co['sub'], anchor='ra')


def _wrapped(d, text, font, maxw, maxlines):
    ls = wrap(d, text, font, maxw)
    return ls[:maxlines] if len(ls) > maxlines else ls


def _render_equal_cover(root, payload, cfg, out_path, meta_path, sem):
    """等权列表：表格未填可选列时使用。N 条一视同仁、整组上下居中、字号固定不放大；
    条目靠交替淡底与横向细线区分，不显示统计条，不设独立头条卡。"""
    theme = THEMES.get(cfg.get('theme', 'deep_blue'), THEMES['deep_blue'])
    size = cfg.get('size', 'portrait')
    W, H = SIZES.get(size, SIZES['portrait'])
    u = min(W, H) / 1080.0
    co = _sem_palette(theme)
    head = cfg.get('cover_header') or payload.get('cover_header') or {}
    issue = cfg.get('issue') or payload.get('issue_cfg') or {}
    wm = cfg.get('wm') or {}
    n = sem['count']
    items = (payload.get('cover') or [])[:n]
    land = size == 'landscape'
    pad = 58 * u
    footer_h = 52 * u
    im = Image.new('RGB', (W, H), co['bg'])
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, max(3, int(6 * u))], fill=co['acc'])

    show_t = head.get('show_title', True)
    show_d = head.get('show_date', True)
    show_i = head.get('show_issue', True)
    title = head.get('title', '每日要闻速览')
    yy = 54 * u
    d.rectangle([pad, yy + 12 * u, pad + 56 * u, yy + 17 * u], fill=co['acc'])
    _spaced(d, pad + 72 * u, yy, 'DAILY  BRIEF', F(24, root, True), co['acc'], 3 * u)
    if show_i:
        s = SRC.norm_issue(issue.get('issue_no') or payload.get('issue') or '第01期')
        _issue_badge(d, W - pad, yy, s, F(30 * u, root, True), co, u)
    yy += 60 * u
    if show_t:
        big = 80
        f = F(big * u, root, True)
        while d.textlength(title, font=f) > (W - pad * 2) and big > 40:
            big -= 2
            f = F(big * u, root, True)
        txt(d, (pad, yy), title, f, co['fg'], True)
        yy += big * u * 1.16
    else:
        yy += 20 * u
    date_cn = issue.get('date_cn') or issue.get('date_line') or payload.get('date', '')
    if show_d:
        _date_badge(d, pad, yy, date_cn, F(30 * u, root, True), co, u)
    yy += HEAD_RULE_AT * u
    _rule(d, co, pad, W - pad, yy)
    head_bottom = yy + 24 * u

    avail = H - head_bottom - footer_h
    cols = 2 if land else 1
    rows = math.ceil(n / cols)
    col_gap = 36 * u
    colw = (W - pad * 2 - (cols - 1) * col_gap) / cols
    numw = 64 * u
    tx_off = 34 * u + numw + 20 * u
    innerw = colw - tx_off - 24 * u
    # 留白三档：只改「卡片内上下留白」与「卡片之间的真空隙」，字号固定不放大。
    # 剩余高度不摊进卡间距，全部留给整组上下居中。
    SPACING = {'compact': (12, 16), 'standard': (16, 22), 'airy': (18, 26)}
    pad_v, gap = SPACING.get(cfg.get('gap', 'airy'), SPACING['airy'])
    pad_v, gap = pad_v * u, gap * u
    min_pad_v, min_gap = 10 * u, 14 * u
    base_fs = (34 if land else 38) * u
    min_fs = 30 * u

    def measure(sz):
        f = F(sz, root, True)
        out = []
        for it in items:
            ls = wrap(d, it.get('title') or '', f, innerw)
            out.append((ls, max(64 * u, len(ls) * sz * 1.22)))
        return out

    fs = base_fs
    blocks = measure(fs)
    maxbh = max(b[1] for b in blocks)
    def total():
        return rows * (maxbh + 2 * pad_v) + (rows - 1) * gap
    # 放不下时优先缩字号保间距，再缩卡内留白，最后才缩间距且有硬下限。
    while fs > min_fs and total() > avail:
        fs -= 1
        blocks = measure(fs)
        maxbh = max(b[1] for b in blocks)
    while pad_v > min_pad_v and total() > avail:
        pad_v -= 1
    while gap > min_gap and total() > avail:
        gap -= 1
    card_h = maxbh + 2 * pad_v
    row_h = card_h + gap
    block_h = rows * card_h + (rows - 1) * gap
    top = head_bottom + max(0, (avail - block_h) / 2)
    ink = (255, 255, 255) if co['dark'] else (0, 0, 0)
    stripe = mix(co['bg'], ink, .075)
    strong_line = mix(co['bg'], ink, .20)

    for i, it in enumerate(items):
        col, row = i // rows, i % rows
        x = pad + col * (colw + col_gap)
        y0 = top + row * row_h
        y1 = y0 + card_h
        if i % 2 == 1:
            d.rounded_rectangle([x, y0, x + colw, y1], radius=10 * u, fill=stripe)
        d.line([x, y1, x + colw, y1], fill=strong_line, width=1)
        bx = x + 34 * u
        by = y0 + (card_h - numw) / 2
        strong = i < 3
        d.rounded_rectangle([bx, by, bx + numw, by + numw], radius=numw * .21,
                            fill=co['acc'] if strong else co['chip'])
        txt(d, (bx + numw / 2, by + numw / 2), '%02d' % (i + 1),
            F(30 * u, root, True), (255, 255, 255) if strong else co['fg'], True, 'mm')
        f = F(fs, root, True)
        ty = y0 + (card_h - blocks[i][1]) / 2
        for k, ln in enumerate(blocks[i][0]):
            txt(d, (x + tx_off, ty + k * fs * 1.22), ln, f, co['fg'], True)

    foot = '%s · %s   %s' % (title, SRC.norm_issue(issue.get('issue_no') or payload.get('issue') or '第01期'),
                             str(issue.get('date') or payload.get('date') or '').replace('-', '.'))
    txt(d, (pad, H - 42 * u), foot, F(22 * u, root, True), co['sub'])
    txt(d, (W - pad, H - 42 * u), '1 / 1', F(22 * u, root), co['sub'], anchor='ra')

    if wm.get('on'):
        a = float(wm.get('alpha', .12))
        s = str(wm.get('text', ''))
        wf = F(26 * u, root)
        wcol = mix(co['bg'], co['fg'], a)
        pos = wm.get('pos', 'bottom_right')
        if pos in ('bottom_right', 'bottom_left'):
            tw = d.textlength(s, font=wf)
            txt(d, (W - pad - tw if pos == 'bottom_right' else pad, H - 14 * u),
                s, wf, wcol, anchor='lm')
        elif pos == 'center':
            txt(d, (W / 2, H / 2), s, F(90 * u, root), wcol, anchor='mm')
        else:
            for wy in range(int(120 * u), H, int(220 * u)):
                for wx in range(int(40 * u), W, int(420 * u)):
                    txt(d, (wx, wy), s, wf, wcol)

    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    im.save(out_path)
    boxes = [[pad + (i // rows) * (colw + col_gap), top + (i % rows) * row_h,
              pad + (i // rows) * (colw + col_gap) + colw,
              top + (i % rows) * row_h + card_h] for i in range(n)]
    meta = {'type': 'cover', 'tpl': 'semantic', 'semantic': True, 'mode': 'L1',
            'layout': 'equal', 'w': W, 'h': H, 'theme': cfg.get('theme'), 'size': size,
            'gap': cfg.get('gap'), 'count': n, 'items': n, 'boxes': boxes,
            'used': round(row_h, 2), 'cell_h': round(row_h, 2),
            'card_h': round(card_h, 2), 'card_gap': round(gap, 2),
            'card_pad': round(pad_v, 2), 'spacing': cfg.get('gap', 'airy'),
            'avail': round(avail, 2), 'head_bottom': round(head_bottom, 2),
            'watermark': bool(wm.get('on')), 'cols': cols, 'rows': rows,
            'centered': True, 'top_gap': round(top - head_bottom, 2),
            'bottom_gap': round(H - footer_h - (top + block_h), 2),
            'whitespace': round((avail - block_h) / 2, 2),
            'fixed_font': True, 'label_chip': False, 'divider': True,
            'font_fs': round(fs, 2), 'stats_hidden': True,
            'hero_value': '', 'topic_count': 0, 'rest_count': n,
            'boards': [], 'enabled': False}
    if meta_path:
        os.makedirs(os.path.dirname(meta_path) or '.', exist_ok=True)
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def render_semantic_cover(root, payload, cfg, out_path, meta_path=None):
    """杂志版：所有语义元素（数值/趋势/板块/专题/机构/主句/角标）只从表格可选列读。
    表格未填可选列时改走等权列表，两套版式共用报头、日期、期号、主题、水印与条数设置。"""
    import source as SRC
    theme = THEMES.get(cfg.get('theme', 'deep_blue'), THEMES['deep_blue'])
    size = cfg.get('size', 'portrait')
    W, H = SIZES.get(size, SIZES['portrait'])
    u = min(W, H) / 1080.0
    co = _sem_palette(theme)
    n = cover_count(cfg, payload)
    # 以本次条数重算，保证统计条与条数一致；不读 payload 里可能过期的缓存
    sem = SRC.build_semantic(payload, n)
    if not sem['enabled']:
        return _render_equal_cover(root, payload, cfg, out_path, meta_path, sem)

    head = cfg.get('cover_header') or payload.get('cover_header') or {}
    issue = cfg.get('issue') or payload.get('issue_cfg') or {}
    wm = cfg.get('wm') or {}
    acc, fg, sub, line = co['acc'], co['fg'], co['sub'], co['line']
    im = Image.new('RGB', (W, H), co['bg'])
    d = ImageDraw.Draw(im)
    boxes = []
    pad = 58 * u
    d.rectangle([0, 0, W, max(3, int(6 * u))], fill=acc)
    show_t = head.get('show_title', True)
    show_d = head.get('show_date', True)
    show_i = head.get('show_issue', True)
    title = head.get('title', '每日要闻速览')

    land = size == 'landscape'
    footer_h = 62 * u
    stat_h = int(118 * u)

    def _draw_header(x0, x1, y0, big):
        yy = y0
        d.rectangle([x0, yy + 12 * u, x0 + 56 * u, yy + 17 * u], fill=acc)
        _spaced(d, x0 + 72 * u, yy, 'DAILY  BRIEF', F(24, root, True), acc, 3 * u)
        if show_i:
            s = SRC.norm_issue(issue.get('issue_no') or payload.get('issue') or '第01期')
            _issue_badge(d, x1, yy, s, F(30 * u, root, True), co, u)
        yy += 60 * u
        if show_t:
            f = F(big * u, root, True)
            while d.textlength(title, font=f) > (x1 - x0) and big > 40:
                big -= 2
                f = F(big * u, root, True)
            txt(d, (x0, yy), title, f, fg, True)
            yy += big * u * 1.16
        else:
            yy += 20 * u
        date_cn = issue.get('date_cn') or issue.get('date_line') or payload.get('date', '')
        if show_d:
            _date_badge(d, x0, yy, date_cn, F(30 * u, root, True), co, u)
        yy += HEAD_RULE_AT * u
        _rule(d, co, x0, x1, yy)
        return yy + 24 * u

    def _draw_stats(x0, x1, y0, h):
        d.rounded_rectangle([x0, y0, x1, y0 + h], radius=14 * u,
                            fill=co['card'], outline=co['line'], width=1)
        cw = (x1 - x0) / 3
        for i, st in enumerate(sem['stats']):
            cx = x0 + cw * (i + .5)
            txt(d, (cx, y0 + 12 * u), st['num'], F(int(h * .40), root, True),
                acc if i == 0 else fg, True, 'ma')
            txt(d, (cx, y0 + h * .68), st['label'], F(int(h * .19), root), sub, anchor='ma')
            if i < 2:
                d.line([x0 + cw * (i + 1), y0 + 16 * u,
                        x0 + cw * (i + 1), y0 + h - 16 * u], fill=line, width=1)
        return y0 + h + 24 * u

    if not land:
        y = _draw_header(pad, W - pad, 62 * u, 88)
        head_bottom = _draw_stats(pad, W - pad, y, stat_h) if sem['boards'] else y
    else:
        head_bottom = 40 * u

    # 头条卡高度按内容算：数值行 + 标题行 + 摘要行 + 来源行，不按固定权重撑满画面
    avail = H - head_bottom - footer_h
    sec_pad = 14 * u
    hero = sem['hero']
    if hero:
        hx0, hx1 = pad + 34 * u, W - pad - 30 * u
        f, ls, fs = _fit(d, hero['title'], hx1 - hx0, 100 * u, 36 * u, 20 * u, root, 2, True)
        sm = hero.get('brief') or hero.get('summary') or ''
        fs2, ls2, sz2 = _fit(d, sm, hx1 - hx0, 78 * u, 25 * u, 16 * u, root, 2) if sm else (None, [], 0)
        need = (22 + 42 + (148 if hero.get('value') else 0)) * u
        need += len(ls) * fs * 1.30
        need += (12 * u + len(ls2) * sz2 * 1.40) if sm else 0
        need += 24 * u + 46 * u
        hero_h = min(avail * .42, max(240 * u, need))
    else:
        hero_h = 0
    topic_h = 0
    rest_h = 0
    rest_fs = 32 * u

    def _sec_head(yy, name, meta, right, x=None, x2=None):
        x = pad if x is None else x
        x2 = W - pad if x2 is None else x2
        _sec(d, co, x, yy, name, meta, right, x2, root)
        return yy + 48 * u

    # ---------------- 竖版 / 横版分区
    if land:
        # 横版：左卡（报头 + 头条 + 统计条） / 右栏（专题 + 其余要闻）
        lx0, lx1 = pad, W * .40
        rx0, rx1 = W * .42, W - pad
        ly0, ly1 = head_bottom, H - footer_h
        _box(d, co, lx0, ly0, lx1, ly1, 22 * u, True)
        hx0, hx1 = lx0 + 34 * u, lx1 - 30 * u
        yy = _draw_header(hx0, hx1, ly0 + 30 * u, 62)
        hero = sem['hero']
        if hero:
            txt(d, (hx0, yy), '今日头条 · ' + (hero.get('board') or '要闻'),
                F(25 * u, root, True), acc)
            yy += 44 * u
            if hero.get('value'):
                fa = F(80 * u, root, True)
                aw = d.textlength(hero.get('arrow') or '', font=fa)
                if hero.get('arrow'):
                    txt(d, (hx0, yy + 94 * u), hero['arrow'], fa, acc, True, 'ls')
                fv = F(106 * u, root, True)
                txt(d, (hx0 + aw + 18 * u, yy + 94 * u), hero['value'], fv, acc, True, 'ls')
                vw = d.textlength(hero['value'], font=fv)
                txt(d, (hx0 + aw + 28 * u + vw, yy + 70 * u), hero.get('unit') or '',
                    F(26 * u, root), acc)
                yy += 124 * u
            f, ls, fs = _fit(d, hero['title'], hx1 - hx0, 92 * u, 31 * u, 18 * u, root, 2, True)
            yy = _para(d, hx0, yy, ls, f, fg, fs * 1.3)
            sm = hero.get('brief') or hero.get('summary') or ''
            if sm:
                f2, ls2, fs2 = _fit(d, sm, hx1 - hx0, 70 * u, 22 * u, 15 * u, root, 2)
                yy = _para(d, hx0, yy + 10 * u, ls2, f2, sub, fs2 * 1.4)
            fy = ly1 - 170 * u
            _rule(d, co, hx0, hx1, fy - 10 * u)
            txt(d, (hx0, fy), '来源 · ' + (hero.get('src') or hero.get('org') or '公开信息'),
                F(20 * u, root), sub)
            if hero.get('stamp'):
                txt(d, (hx1, fy), hero['stamp'], F(20 * u, root, True), acc, anchor='ra')
            if sem['boards']:
                _draw_stats(hx0, hx1, ly1 - 132 * u, 100 * u)
            boxes.append([hx0, ly0, hx1, ly1])
        # 右栏
        ry = ly0 + 20 * u
        for t in sem['topics']:
            ry = _sec_head(ry, t['name'] or t['id'],
                           '%02d 条 · 合并专题' % len(t['items']), t.get('badge') or '',
                           x=rx0, x2=rx1)
            th = min(avail * .34, max(180 * u, 76 * u + len(t['items']) * 108 * u))
            _box(d, co, rx0, ry, rx1, ry + th, 18 * u)
            rowh = (th - 24 * u) / max(1, len(t['items']))
            for k, it in enumerate(t['items']):
                yy = ry + 12 * u + k * rowh
                f, ls, fs = _fit(d, it.get('org') or '', 210 * u, rowh * .48,
                                 25 * u, 16 * u, root, 2, True)
                _para(d, rx0 + 26 * u, yy, ls, f, fg, fs * 1.3)
                bx = rx0 + 240 * u
                bw = rx1 - bx - 26 * u
                f2, ls2, fs2 = _fit(d, it.get('brief') or it['title'], bw, rowh * .46,
                                    24 * u, 15 * u, root, 2)
                _para(d, bx, yy, ls2, f2, fg, fs2 * 1.35)
                tail = ' · '.join(x for x in (it.get('role'), '来源 · ' + (it.get('src') or ''))
                                  if x and x != '来源 · ')
                if tail:
                    txt(d, (bx, yy + rowh * .56), tail, F(18 * u, root), sub)
                if k < len(t['items']) - 1:
                    _rule(d, co, rx0 + 26 * u, rx1 - 26 * u, yy + rowh - 8 * u)
                boxes.append([rx0 + 26 * u, yy, rx1 - 26 * u, yy + rowh - 8 * u])
            ry += th + sec_pad
        if sem['rest']:
            ry = _sec_head(ry, '其余要闻', '%02d 条' % len(sem['rest']), '',
                           x=rx0, x2=rx1)
            rows = len(sem['rest'])
            colw = rx1 - rx0 - 52 * u
            numw, textw = 48 * u, colw - 48 * u - 24 * u
            avail_rest = H - footer_h - ry - 12 * u
            texts = [it['title'] for it in sem['rest']]

            def total_at(fs):
                f = F(fs, root)
                return sum(len(wrap(d, t, f, textw)) * fs * 1.30 + 8 * u
                           for t in texts)

            fs = 30 * u
            while fs > 20 * u and total_at(fs) + 24 * u > avail_rest:
                fs -= 1
            rest_fs = fs
            rh = min(avail_rest, total_at(fs) + 24 * u)
            _box(d, co, rx0, ry, rx1, ry + rh, 18 * u)
            f = F(fs, root)
            nf2 = F(int(20 * u), root, True)
            yy = ry + 12 * u
            for k, t in enumerate(texts):
                xx = rx0 + 26 * u
                rk = sem['rest'][k]['rank']
                strong = rk <= 3
                d.rounded_rectangle([xx, yy + 2 * u, xx + numw, yy + 2 * u + fs * 1.12],
                                    radius=int(6 * u), fill=co['acc'] if strong else co['chip'],
                                    outline=co['line'], width=1)
                txt(d, (xx + numw / 2, yy + 2 * u + fs * .56), '%02d' % rk, nf2,
                    (255, 255, 255) if strong else fg, True, 'mm')
                ls = wrap(d, t, f, textw)
                _para(d, xx + numw + 14 * u, yy, ls, f, fg, fs * 1.30)
                h = len(ls) * fs * 1.30
                if k < rows - 1:
                    _rule(d, co, xx, rx1 - 26 * u, yy + h + 3 * u)
                boxes.append([xx, yy, rx1 - 26 * u, yy + h + 3 * u])
                yy += h + 8 * u
    else:
        # 竖版：整块顺序 头条 -> 专题 -> 其余要闻
        cy = head_bottom
        hero = sem['hero']
        if hero:
            cy = _sec_head(cy, '今日头条', '01 条', '先看这一条')
            _box(d, co, pad, cy, W - pad, cy + hero_h, 20 * u, True)
            hx0, hx1 = pad + 34 * u, W - pad - 30 * u
            yy = cy + 22 * u
            txt(d, (hx0, yy), (hero.get('board') or '今日头条'), F(27 * u, root, True), acc)
            yy += 42 * u
            if hero.get('value'):
                fa = F(96 * u, root, True)
                aw = d.textlength(hero.get('arrow') or '', font=fa)
                if hero.get('arrow'):
                    txt(d, (hx0, yy + 110 * u), hero['arrow'], fa, acc, True, 'ls')
                fv = F(126 * u, root, True)
                txt(d, (hx0 + aw + 20 * u, yy + 110 * u), hero['value'], fv, acc, True, 'ls')
                vw = d.textlength(hero['value'], font=fv)
                txt(d, (hx0 + aw + 30 * u + vw, yy + 86 * u), hero.get('unit') or '',
                    F(30 * u, root), acc)
                yy += 148 * u
            f, ls, fs = _fit(d, hero['title'], hx1 - hx0, 100 * u, 36 * u, 20 * u, root, 2, True)
            yy = _para(d, hx0, yy, ls, f, fg, fs * 1.3)
            sm = hero.get('brief') or hero.get('summary') or ''
            if sm:
                f2, ls2, fs2 = _fit(d, sm, hx1 - hx0, 78 * u, 25 * u, 16 * u, root, 2)
                yy = _para(d, hx0, yy + 12 * u, ls2, f2, sub, fs2 * 1.4)
            fy = yy + 24 * u
            _rule(d, co, hx0, hx1, fy - 12 * u)
            txt(d, (hx0, fy + 10 * u), '来源 · ' + (hero.get('src') or hero.get('org') or '公开信息'),
                F(22 * u, root), sub)
            if hero.get('stamp'):
                txt(d, (hx1, fy + 10 * u), hero['stamp'], F(22 * u, root, True), acc, anchor='ra')
            boxes.append([pad, cy, W - pad, cy + hero_h])
            cy += hero_h + sec_pad

        for t in sem['topics']:
            cy = _sec_head(cy, t['name'] or t['id'],
                           '%02d 条 · 合并专题' % len(t['items']), t.get('badge') or '')
            th = min(avail * .34, max(200 * u, 76 * u + len(t['items']) * 112 * u))
            _box(d, co, pad, cy, W - pad, cy + th, 20 * u)
            # 专题名条
            d.rounded_rectangle([pad + 34 * u, cy + 20 * u, pad + 112 * u, cy + 58 * u],
                                radius=7 * u, fill=co['chip'])
            txt(d, (pad + 73 * u, cy + 39 * u), '专题', F(22 * u, root, True), acc, True, 'mm')
            txt(d, (pad + 126 * u, cy + 30 * u), t['name'] or t['id'], F(28 * u, root, True), fg)
            top = cy + 76 * u
            rowh = (cy + th - top - 14 * u) / max(1, len(t['items']))
            for k, it in enumerate(t['items']):
                yy = top + k * rowh
                f, ls, fs = _fit(d, it.get('org') or '', 250 * u, rowh * .5,
                                 27 * u, 17 * u, root, 2, True)
                _para(d, pad + 34 * u, yy, ls, f, fg, fs * 1.3)
                txt(d, (pad + 34 * u, yy + rowh * .52),
                    it.get('role') or '', F(19 * u, root), sub)
                bx = pad + 320 * u
                bw = W - pad - 34 * u - bx
                f2, ls2, fs2 = _fit(d, it.get('brief') or it['title'], bw, rowh * .52,
                                    26 * u, 16 * u, root, 2)
                _para(d, bx, yy, ls2, f2, fg, fs2 * 1.35)
                txt(d, (bx, yy + rowh * .60), '来源 · ' + (it.get('src') or ''),
                    F(19 * u, root), sub)
                if k < len(t['items']) - 1:
                    _rule(d, co, pad + 34 * u, W - pad - 34 * u, yy + rowh - 8 * u)
                boxes.append([pad + 34 * u, yy, W - pad - 34 * u, yy + rowh - 8 * u])
            cy += th + sec_pad

        if sem['rest']:
            cy = _sec_head(cy, '其余要闻', '%02d 条' % len(sem['rest']), '')
            rows = len(sem['rest'])
            colw = W - pad * 2 - 68 * u
            numw, textw = 52 * u, colw - 52 * u - 26 * u
            avail_rest = H - footer_h - cy - 10 * u
            texts = [it['title'] for it in sem['rest']]

            def total_at(fs):
                f = F(fs, root)
                return sum(len(wrap(d, t, f, textw)) * fs * 1.30 + 9 * u
                           for t in texts)

            fs = 32 * u
            while fs > 22 * u and total_at(fs) + 24 * u > avail_rest:
                fs -= 1
            rest_fs = fs
            rh = min(avail_rest, total_at(fs) + 24 * u)
            _box(d, co, pad, cy, W - pad, cy + rh, 20 * u)
            f = F(fs, root)
            nf2 = F(int(21 * u), root, True)
            yy = cy + 12 * u
            for k, t in enumerate(texts):
                xx = pad + 34 * u
                rk = sem['rest'][k]['rank']
                strong = rk <= 3
                d.rounded_rectangle([xx, yy + 2 * u, xx + numw, yy + 2 * u + fs * 1.12],
                                    radius=int(6 * u), fill=co['acc'] if strong else co['chip'],
                                    outline=co['line'], width=1)
                txt(d, (xx + numw / 2, yy + 2 * u + fs * .56), '%02d' % rk, nf2,
                    (255, 255, 255) if strong else fg, True, 'mm')
                ls = wrap(d, t, f, textw)
                _para(d, xx + numw + 14 * u, yy, ls, f, fg, fs * 1.30)
                h = len(ls) * fs * 1.30
                if k < rows - 1:
                    _rule(d, co, xx, W - pad - 34 * u, yy + h + 3 * u)
                boxes.append([xx, yy, W - pad - 34 * u, yy + h + 3 * u])
                yy += h + 9 * u
            cy += rh

    # 页脚
    foot = '%s · %s   %s' % (title, SRC.norm_issue(issue.get('issue_no') or payload.get('issue') or '第01期'),
                             str(issue.get('date') or payload.get('date') or '').replace('-', '.'))
    txt(d, (pad, H - 42 * u), foot, F(22 * u, root, True), sub)
    txt(d, (W - pad, H - 42 * u), '1 / 1', F(22 * u, root), sub, anchor='ra')

    # 水印：只画底部预留区，不压正文
    if wm.get('on'):
        a = float(wm.get('alpha', .12))
        s = str(wm.get('text', ''))
        wf = F(26 * u, root)
        wcol = mix(co['bg'], fg, a)
        pos = wm.get('pos', 'bottom_right')
        if pos in ('bottom_right', 'bottom_left'):
            tw = d.textlength(s, font=wf)
            txt(d, (W - pad - tw if pos == 'bottom_right' else pad, H - 14 * u),
                s, wf, wcol, anchor='lm')
        elif pos == 'center':
            txt(d, (W / 2, H / 2), s, F(90 * u, root), wcol, anchor='mm')
        else:
            for yy in range(int(120 * u), H, int(220 * u)):
                for xx in range(int(40 * u), W, int(420 * u)):
                    txt(d, (xx, yy), s, wf, wcol)

    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    im.save(out_path)
    used = max([b[3] - b[1] for b in boxes] or [1])
    meta = {'type': 'cover', 'tpl': 'semantic', 'semantic': True, 'mode': 'L1',
            'layout': 'magazine', 'w': W, 'h': H, 'theme': cfg.get('theme'), 'size': size,
            'gap': cfg.get('gap'), 'count': n, 'items': n, 'boxes': boxes[:n],
            'used': round(used, 2), 'cell_h': round(used, 2),
            'avail': round(avail, 2), 'head_bottom': round(head_bottom, 2),
            'watermark': bool(wm.get('on')), 'cols': 2 if size == 'landscape' else 1,
            'rows': len(boxes[:n]), 'centered': True, 'top_gap': 0, 'bottom_gap': 0,
            'whitespace': 0, 'fixed_font': True, 'label_chip': False, 'divider': True,
            'hero_value': (sem['hero'] or {}).get('value', ''),
            'topic_count': sem['topic_count'], 'rest_count': sem['rest_count'],
            'boards': sem['boards'], 'enabled': sem['enabled'],
            'stats_hidden': not bool(sem['boards']), 'font_fs': round(rest_fs, 2),
            'hero_h': round(hero_h, 2)}
    if meta_path:
        os.makedirs(os.path.dirname(meta_path) or '.', exist_ok=True)
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta

