# -*- coding: utf-8 -*-
"""人声层：在线 LibreTTS（微软神经音色）/ edge-tts 直连 / 自定义上传真人录音 / dry-run 占位。

失败策略 stop_and_report：在线接口失败就抛错，不静默塞静音、不沿用上期音轨。
dry-run 只用于本地冒烟链路，会在状态文件里如实标注，绝不冒充真人声。

在线接口按 POST JSON → GET t/v/r/p → GET text/voice 三种契约顺序兼容，
错误响应体写进异常，便于定位。
"""
import os, json, time, subprocess, urllib.request, urllib.parse, urllib.error, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed


class VoiceError(RuntimeError):
    pass


CPS = 4.6  # 中文播报估算字/秒，仅用于 dry-run 与预览时间轴


def estimate(text):
    return max(1.6, len(str(text)) / CPS)


def _wav_silence(path, dur):
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'anullsrc=r=24000:cl=mono', '-t', '%.3f' % dur,
                    '-c:a', 'pcm_s16le', path], check=True)
    return dur


def _probe_dur(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'default=nw=1:nk=1', path], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def _audio_ok(data, ctype):
    """挡住把错误页 / JSON / HTML 当音频写盘。"""
    if not data or len(data) < 512:
        return False
    h = data[:16]
    if h.startswith(b'ID3') or h[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2', b'\xff\xf1'):
        return True
    if h.startswith((b'RIFF', b'OggS', b'fLaC')):
        return True
    if h[:1] in (b'{', b'<') or h.startswith(b'<!'):
        return False
    return bool(ctype and ('audio/' in ctype or 'octet-stream' in ctype))


def _normalize(raw, out_path, tag):
    """服务端可能给 mp3/wav/ogg/opus，统一转 24k 单声道 mp3 再交给拼接与时间轴。"""
    tmp = out_path + '.raw'
    with open(tmp, 'wb') as f:
        f.write(raw)
    p = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', tmp,
                        '-ar', '24000', '-ac', '1', '-c:a', 'libmp3lame', '-b:a', '96k', out_path],
                       capture_output=True, text=True)
    try:
        os.remove(tmp)
    except OSError:
        pass
    if p.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) < 512:
        raise VoiceError('%s 返回内容不是可解码音频：%s' % (tag, p.stderr[-300:]))
    return out_path


def _one_request(endpoint, method, params, text, voice, rate, timeout):
    """返回 (data, content_type)；HTTP 错误抛出带响应体的 VoiceError。"""
    headers = {'User-Agent': 'daily-news-studio/1.2', 'Accept': 'audio/mpeg,audio/*;q=0.9,*/*;q=0.5'}
    if method == 'POST':
        body = json.dumps(params, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(endpoint, data=body, method='POST',
                                     headers=dict(headers, **{'Content-Type': 'application/json; charset=utf-8'}))
    else:
        q = urllib.parse.urlencode(params)
        url = endpoint + ('&' if '?' in endpoint else '?') + q
        req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get('Content-Type', '')
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read()[:500].decode('utf-8', 'replace').strip()
        except Exception:
            pass
        raise VoiceError('HTTP %d %s%s' % (e.code, e.reason, '：' + body if body else ''))
    except Exception as e:
        raise VoiceError('%r' % (e,))


def libretts_fetch(endpoint, text, voice, rate, out_path, timeout=20, retries=2):
    """同一个服务按三种契约依次尝试；4xx 读响应体，不再只显示 'Bad Request'。

    顺序：POST JSON（LibreSpark 标准）→ GET t/v/r/p（Ciallo / xysele 旧契约）
          → GET text/voice/rate（Piper 风格自建实例）。
    POST 优先也避免长句 GET 触发 URL 长度限制。
    """
    r = int(rate or 0)
    attempts = [
        ('POST-json', 'POST', {'text': text, 'voice': voice, 'rate': r, 'pitch': 0, 'format': 'mp3'}),
        ('GET-t/v/r/p', 'GET', {'t': text, 'v': voice, 'r': r, 'p': 0}),
        ('GET-text/voice', 'GET', {'text': text, 'voice': voice, 'rate': r, 'format': 'mp3'}),
    ]
    errs = []
    for rnd in range(max(1, int(retries or 1))):
        for label, method, params in attempts:
            try:
                data, ctype = _one_request(endpoint, method, params, text, voice, r, timeout)
            except VoiceError as e:
                errs.append('%s %s' % (label, e))
                continue
            if not _audio_ok(data, ctype):
                errs.append('%s 非音频响应（%s / %dB / %r）' %
                            (label, ctype, len(data), data[:120]))
                continue
            _normalize(data, out_path, label)
            return label
        if rnd + 1 < max(1, int(retries or 1)):
            time.sleep(0.6)
    raise VoiceError('LibreTTS 调用失败（%d 轮 × %d 契约）；逐次原因：%s；停止出片，不静音占位、不沿用上期'
                     % (max(1, int(retries or 1)), len(attempts), ' | '.join(errs[-6:])))


def _edge_fetch(text, voice, rate, out_path):
    """可选通道：edge-tts 本地直连微软神经音色，不依赖第三方公共实例。"""
    try:
        import asyncio, edge_tts
    except ImportError:
        raise VoiceError('edge 模式需要 edge-tts：pip install edge-tts')
    r = int(rate or 0)

    async def go():
        c = edge_tts.Communicate(text, voice, rate='%+d%%' % r)
        await c.save(out_path)

    try:
        asyncio.run(go())
    except Exception as e:
        raise VoiceError('edge-tts 合成失败：%r；停止出片，不静音占位、不沿用上期' % (e,))
    if not os.path.isfile(out_path) or os.path.getsize(out_path) < 512:
        raise VoiceError('edge-tts 输出为空或过小')
    return out_path


def _cache_path(outdir, text, voice, rate):
    key = hashlib.md5((str(voice) + '|' + str(rate) + '|' + text).encode('utf-8')).hexdigest()[:20]
    return os.path.join(outdir, 'cache_' + key + '.mp3'), key


def _synth_parallel(root, manifest, cfg, segs, outdir, fetcher, tag):
    """6 路并发 + 本地缓存；任一句失败即整体抛错（stop_and_report）。"""
    workers = min(12, max(1, int(cfg.get('concurrency', 12))))
    results = [None] * len(segs)
    jobs = []
    for k, seg in enumerate(segs):
        p, key = _cache_path(outdir, seg['text'], cfg.get('voice', ''), cfg.get('rate', 0))
        jobs.append((k, seg, p, os.path.isfile(p) and os.path.getsize(p) > 512))

    def work(job):
        k, seg, p, hit = job
        if not hit:
            fetcher(seg['text'], p)
        if not os.path.isfile(p) or os.path.getsize(p) < 512:
            raise VoiceError('%s 第 %d 句输出为空' % (tag, k + 1))
        return k, {'file': p, 'dur': _probe_dur(p), 'text': seg['text'], 'cached': hit}

    errors = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, j): j for j in jobs}
        for f in as_completed(futs):
            try:
                k, item = f.result()
                results[k] = item
            except Exception as e:
                errors.append('%r' % (e,))
    if errors:
        raise VoiceError('%s 并发合成失败（%d/%d）：%s；停止出片，不静音占位' %
                         (tag, len(errors), len(jobs), ' | '.join(errors[:4])))
    cached = sum(1 for x in results if x['cached'])
    print('人声：%s %d 句 / %d 路并发 / 缓存复用 %d 句' % (tag, len(results), workers, cached), flush=True)
    return results


def build_voice(root, manifest, mode=None):
    cfg = manifest['config']['voice']
    mode = mode or cfg.get('mode') or ('upload' if cfg.get('upload') else 'libretts')
    outdir = os.path.join(root, '.cache', 'voice')
    os.makedirs(outdir, exist_ok=True)
    segs = manifest['segments']
    result = []

    if mode == 'upload':
        up = cfg.get('upload')
        if not up or not os.path.isfile(os.path.join(root, up)):
            raise VoiceError('自定义上传人声不存在：%r' % up)
        src = os.path.join(root, up)
        dur = _probe_dur(src)
        result = [{'file': src, 'dur': dur, 'text': '（整段上传真人录音）'}]
        return {'mode': 'upload', 'real_voice': True, 'total': dur, 'segments': result}

    if mode == 'dry-run':
        for s in segs:
            p = os.path.join(outdir, 's%03d.wav' % s['i'])
            # 冒烟用：每句压到 <=3s，避免把 30 条长稿编成十几分钟视频；真实模式不受影响
            dur = min(3.0, max(1.2, estimate(s['text'])))
            _wav_silence(p, dur)
            result.append({'file': p, 'dur': dur, 'text': s['text']})
        return {'mode': 'dry-run', 'real_voice': False, 'total': sum(x['dur'] for x in result),
                'segments': result,
                'note': '本地静音占位且每句压到<=3s，仅验混音/字幕/封装链路；不是真人声，也不是成品时长'}

    if mode not in ('libretts', 'edge'):
        raise VoiceError('未知人声模式：' + str(mode))

    if mode == 'edge':
        def ef(text, p):
            _edge_fetch(text, cfg.get('voice', 'zh-CN-YunyangNeural'), cfg.get('rate', 0), p)
        result = _synth_parallel(root, manifest, cfg, segs, outdir, ef, 'edge-tts')
        return {'mode': 'edge', 'real_voice': True, 'total': sum(x['dur'] for x in result),
                'segments': result, 'voice': cfg.get('voice'),
                'note': 'edge-tts 直连微软神经音色，6 路并发 + 本地缓存'}

    endpoint = cfg.get('endpoint')
    if not endpoint:
        raise VoiceError('未配置 LibreTTS endpoint')
    contracts = []

    def lf(text, p):
        label = libretts_fetch(endpoint, text, cfg.get('voice', 'zh-CN-YunyangNeural'),
                               cfg.get('rate', 0), p,
                               timeout=int(cfg.get('timeout_s', 20)), retries=int(cfg.get('retries', 2)))
        contracts.append(label)

    result = _synth_parallel(root, manifest, cfg, segs, outdir, lf, 'LibreTTS')
    return {'mode': 'libretts', 'real_voice': True, 'total': sum(x['dur'] for x in result),
            'segments': result, 'endpoint': endpoint, 'voice': cfg.get('voice'),
            'api_contract': contracts[0] if contracts else None,
            'note': '6 路并发 + 本地缓存，不再 33 次串行等待'}


def build_srt(root, voice, manifest, path):
    sub = manifest['config']['subtitle']
    if not sub.get('on'):
        return None
    t, lines = 0.0, []
    for i, s in enumerate(voice['segments'], 1):
        st, en = t, t + s['dur']
        t = en
        def fmt(x):
            ms = int(round(x * 1000))
            h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); sec, ms = divmod(ms, 1000)
            return '%02d:%02d:%02d,%03d' % (h, m, sec, ms)
        text = s['text'] if voice['mode'] != 'upload' else manifest['segments'][i - 1]['text']
        lines.append('%d\n%s --> %s\n%s\n' % (i, fmt(st), fmt(en), text))
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path

def build_ass(root, voice, manifest, path, W, H):
    """逐字显现硬字幕：ASS 卡拉OK（\k 按字分配厘秒），未显现的字用全透明 SecondaryColour。

    一句一条 Dialogue，总时长严格等于该句音频时长；软轨与独立 SRT 仍为整句可读文本。
    """
    sub = manifest['config']['subtitle']
    tw = manifest['config'].get('typewriter') or {}

    def color(hexs, alpha='00'):
        h = str(hexs).lstrip('#')
        if len(h) != 6:
            h = 'FFFFFF'
        return '&H%s%s%s%s' % (alpha, h[4:6].upper(), h[2:4].upper(), h[0:2].upper())

    def ts(x):
        cs = int(round(max(0.0, x) * 100))
        h, cs = divmod(cs, 360000); m, cs = divmod(cs, 6000); s, cs = divmod(cs, 100)
        return '%d:%02d:%02d.%02d' % (h, m, s, cs)

    def esc(c):
        return c.replace('\\', '／').replace('{', '(').replace('}', ')').replace('\n', ' ')

    fs = max(16, int(int(sub.get('font_size', 44)) * H / 1080.0))
    align = {'bottom': 2, 'center': 5, 'top': 8}.get(sub.get('position', 'bottom'), 2)
    border = 3 if sub.get('background', True) else 1
    margin_v = int(H * 0.05)
    primary = color(sub.get('color', '#FFFFFF'), '00')
    # &HFF...... = alpha FF = 完全透明，未「打」到的字不可见
    secondary = '&HFF000000'
    outline = color(sub.get('outline_color', '#0d1b2a'), '00')
    font = sub.get('font', 'WenQuanYi Micro Hei')

    lines = [
        '[Script Info]', 'ScriptType: v4.00+', 'WrapStyle: 2',
        'ScaledBorderAndShadow: yes', 'PlayResX: %d' % W, 'PlayResY: %d' % H, '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, '
        'BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, '
        'BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
        'Style: Default,%s,%d,%s,%s,%s,&H80000000,0,0,0,0,100,100,0,0,%d,2,0,%d,10,10,%d,1'
        % (font, fs, primary, secondary, outline, border, align, margin_v), '',
        '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    t = 0.0
    for i, s in enumerate(voice['segments'], 0):
        st, en = t, t + s['dur']
        t = en
        text = s['text'] if voice['mode'] != 'upload' else manifest['segments'][i]['text']
        chars = [c for c in str(text) if c not in ('\r', '\n')]
        if not chars:
            chars = [' ']
        total_cs = max(1, int(round(s['dur'] * 100)))
        # 每字均分厘秒，余数摊到前几个字，保证总和严格等于本句时长
        alloc = [total_cs // len(chars)] * len(chars)
        for k in range(total_cs - sum(alloc)):
            alloc[k % len(chars)] += 1
        body = ''.join('{\\k%d}%s' % (alloc[j], esc(c)) for j, c in enumerate(chars))
        lines.append('Dialogue: 0,%s,%s,Default,,0,0,0,,%s' % (ts(st), ts(en), body))
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return path
