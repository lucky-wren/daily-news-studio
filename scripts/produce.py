# -*- coding: utf-8 -*-
"""合成层：按封面设置只出一版视频；字幕默认烧录 + 可选软轨 + 独立 SRT；
视频帧与字幕均可选打字机逐字显现；静帧编码参数针对体积优化。

所有 ffmpeg 调用检查返回码；失败即抛错，不静默报成功、不删旧片换空文件。
成品状态由 ffprobe 读真实流判定，不是「文件存在就算有轨」。
"""
import os, json, re, subprocess, math
from PIL import Image, ImageDraw
import render as R
import tts as T


def run(cmd, tag):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError('%s 失败 rc=%d\nCMD: %s\nSTDERR: %s' % (
            tag, p.returncode, ' '.join(map(str, cmd))[:900], p.stderr[-1800:]))
    return p


def probe(path):
    p = run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path], 'ffprobe ' + path)
    return json.loads(p.stdout)


def ff_dur(info):
    try:
        return float(info['format']['duration'])
    except (KeyError, ValueError, TypeError):
        return 0.0


def mean_db(path):
    """读真实平均电平；静音占位返回 -91，避免对无信号轨做归一化。"""
    p = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', path,
                        '-af', 'volumedetect', '-f', 'null', '-'],
                       capture_output=True, text=True)
    m = re.findall(r'mean_volume:\s*(-?[\d.]+) dB', p.stderr)
    return float(m[-1]) if m else -91.0


# ------------------------------------------------------------------ 音频
def build_audio(root, voice, manifest):
    cfg = manifest['config']
    media = os.path.join(root, 'media')
    os.makedirs(media, exist_ok=True)
    cache = os.path.join(root, '.cache'); os.makedirs(cache, exist_ok=True)
    vol = float(cfg['voice'].get('volume', 1.0))
    gain_db = float(cfg['voice'].get('master_gain_db', 0.0))
    files = [s['file'] for s in voice['segments']]
    parts, labels = [], []
    cmd = ['ffmpeg', '-y', '-loglevel', 'error']
    for f in files:
        cmd += ['-i', f]
    for i in range(len(files)):
        parts.append('[%d:a]volume=%.3f[a%d]' % (i, vol, i)); labels.append('[a%d]' % i)
    parts.append(''.join(labels) + 'concat=n=%d:v=0:a=1[raw]' % len(files))
    raw = os.path.join(cache, 'voice_raw.wav')
    cmd += ['-filter_complex', ';'.join(parts), '-map', '[raw]', '-ar', '24000', '-ac', '1',
            '-c:a', 'pcm_s16le', raw]
    run(cmd, '人声拼接')

    # TTS 输出电平普遍偏低；统一归一化到广播/播客 -16 LUFS，再叠加用户总增益，最后限幅。
    # 静音占位（dry-run）无信号，跳过 loudnorm，避免对空轨做无意义处理。
    vwav = os.path.join(cache, 'voice_track.wav')
    if mean_db(raw) > -70.0:
        run(['ffmpeg', '-y', '-loglevel', 'error', '-i', raw,
             '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11,volume=%.3fdB,alimiter=limit=0.95' % gain_db,
             '-ar', '24000', '-ac', '1', '-c:a', 'pcm_s16le', vwav], '人声响度归一化')
    else:
        run(['ffmpeg', '-y', '-loglevel', 'error', '-i', raw,
             '-af', 'volume=%.3fdB' % gain_db,
             '-ar', '24000', '-ac', '1', '-c:a', 'pcm_s16le', vwav], '人声增益')

    out = os.path.join(media, '今日播报.mp3')
    bgm_rel = cfg['bgm'].get('file')
    if bgm_rel:
        bgm = os.path.join(root, bgm_rel)
        if not os.path.isfile(bgm):
            raise RuntimeError('配置的 BGM 不存在：' + bgm)
        b = float(cfg['bgm'].get('volume', .33))
        # normalize=0：amix 默认会把每路各乘 0.5（约 -6dB），这是总音量偏小的主因。
        run(['ffmpeg', '-y', '-loglevel', 'error', '-i', vwav, '-stream_loop', '-1', '-i', bgm,
             '-filter_complex',
             '[1:a]volume=%.3f,alimiter=limit=0.30[b];'
             '[0:a][b]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,'
             'alimiter=limit=0.95[m]' % b,
             '-map', '[m]', '-c:a', 'libmp3lame', '-b:a', '96k', '-ac', '1', out], '混音 BGM')
    else:
        run(['ffmpeg', '-y', '-loglevel', 'error', '-i', vwav,
             '-c:a', 'libmp3lame', '-b:a', '96k', '-ac', '1', out], '编码 MP3')
    info = probe(out)
    return out, ff_dur(info), {'streams': [s['codec_type'] for s in info.get('streams', [])]}


# ------------------------------------------------------------------ 帧
def simple_frame(root, text, size, out, acc_hex='#e6432f', reveal=1.0, theme='deep_blue'):
    """开场 / 窗口说明 / 片尾帧：按封面主题自动反色，不再写死深底白字。"""
    W, H = R.SIZES[size]
    u = W / 1080.0
    th = R.THEMES.get(theme, R.THEMES['deep_blue'])
    base = R.rgb(th['bg']); fg = R.rgb(th['txt'])
    sub = R.rgb(th['sub']); line_c = R.rgba_color(th['line'], base)
    acc = R.rgb(acc_hex or th['acc'])
    im = Image.new('RGB', (W, H), base)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, max(2, int(6 * u))], fill=acc)
    pad = int(80 * u)
    R.txt(d, (pad, int(86 * u)), '今日要闻速览', R.F(int(22 * u), root), sub)
    rv = max(0.0, min(1.0, float(reveal)))
    shown = text if rv >= 1 else text[:max(0, int(round(len(text) * rv)))]
    f = R.F(int((56 if size == 'portrait' else 44) * u), root, True)
    maxw = W - 2 * pad
    lines = R.wrap(d, shown, f, maxw)
    y = H * 0.36
    for ln in lines:
        R.txt(d, (pad, y), ln, f, fg, True); y += int(72 * u)
    d.rectangle([pad, y + int(18 * u), pad + int(110 * u), y + int(24 * u)], fill=acc)
    # 信息卡：与页面预览同源，避免开场/片尾帧只剩一行字
    by = int(H * 0.54); bh = int(H * 0.21)
    d.rounded_rectangle([pad, by, W - pad, by + bh], radius=int(14 * u),
                        outline=line_c, width=1)
    R.txt(d, (pad + int(22 * u), by + int(42 * u)), '资讯窗口', R.F(int(20 * u), root), sub)
    R.txt(d, (pad + int(22 * u), by + int(82 * u)), '昨日 20:00 — 今日 20:00',
          R.F(int(25 * u), root, True), fg)
    R.txt(d, (pad + int(22 * u), by + bh - int(24 * u)),
          '以官方发布为准 · 内容依据公开信息整理', R.F(int(19 * u), root), sub)
    d.line([pad, H - int(110 * u), W - pad, H - int(110 * u)], fill=line_c, width=1)
    R.txt(d, (pad, H - int(68 * u)), '内容依据公开信息整理 · 以官方发布为准', R.F(int(22 * u), root), fg)
    R.txt(d, (pad, H - int(36 * u)), '仅供参考 · 不构成投资、医疗或法律建议', R.F(int(19 * u), root), sub)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    im.save(out)


def tw_targets(tw):
    on = bool(tw.get('on'))
    target = tw.get('target', 'subtitle')
    return (on and target in ('subtitle', 'both'), on and target in ('frame', 'both'))


def frames_for(root, manifest, voice, size, tw):
    """返回 [(png, dur), ...]，与句子/打字机子帧严格一致。"""
    outdir = os.path.join(root, '.cache', 'frames_' + size)
    os.makedirs(outdir, exist_ok=True)
    cards_dir = os.path.join(root, '.cache', 'cards_' + size)
    segs = manifest['segments']
    vs = voice['segments']
    if len(vs) == len(segs):
        durs = [s['dur'] for s in vs]
    else:
        total = voice['total']; weights = [max(1, len(s['text'])) for s in segs]
        durs = [total * w / sum(weights) for w in weights]
    _, tw_frame = tw_targets(tw)
    steps = max(2, min(int(tw.get('max_steps', 8)), 12))
    cfg = dict(manifest['config'].get('video_card') or {}, size=size)
    theme = (manifest['config'].get('cover') or {}).get('theme', 'deep_blue')
    acc_hex = R.THEMES.get(theme, R.THEMES['deep_blue'])['acc']
    out = []
    seq = 0
    for i, s in enumerate(segs):
        du = durs[i]
        if not tw_frame:
            seq += 1
            p = os.path.join(outdir, 'f%03d.png' % seq)
            if s['kind'] == 'news' and s['card']:
                src = os.path.join(cards_dir, 'c%03d.png' % s['card'])
                if not os.path.isfile(src):
                    raise RuntimeError('缺少资讯卡片帧：' + src)
                Image.open(src).save(p)
            else:
                simple_frame(root, s['text'], size, p, acc_hex, 1.0, theme)
            out.append((p, du))
            continue
        card = next((c for c in manifest['cards'] if c['index'] == s['card']), None) if s['card'] else None
        for j in range(1, steps + 1):
            seq += 1
            p = os.path.join(outdir, 'f%03d.png' % seq)
            ratio = j / steps
            if card:
                R.render_card(root, card, cfg, p, reveal=ratio)
            else:
                simple_frame(root, s['text'], size, p, acc_hex, ratio, theme)
            out.append((p, du / steps))
    return out


# ------------------------------------------------------------------ 字幕滤镜
def _ass_color(hexs, alpha='00'):
    h = str(hexs).lstrip('#')
    if len(h) != 6:
        h = 'FFFFFF'
    r, g, b = h[0:2], h[2:4], h[4:6]
    return '&H%s%s%s%s' % (alpha, b.upper(), g.upper(), r.upper())


def _escape_sub(path):
    """ffmpeg subtitles 滤镜路径转义（冒号/反斜杠/引号/逗号/方括号/分号）。"""
    p = os.path.abspath(path)
    for a, b in [('\\', '\\\\'), (':', '\\:'), ("'", "\\'"), (',', '\\,'),
                 ('[', '\\['), (']', '\\]'), (';', '\\;'), ('%', '\\%')]:
        p = p.replace(a, b)
    return p


def sub_style(sub, W, H):
    align = {'bottom': 2, 'center': 5, 'top': 8}.get(sub.get('position', 'bottom'), 2)
    fs = int(sub.get('font_size', 44))
    fs = max(16, int(fs * H / 1080.0))
    border = 3 if sub.get('background', True) else 1
    return ("FontName=%s,FontSize=%d,PrimaryColour=%s,OutlineColour=%s,"
            "BackColour=&H80000000,BorderStyle=%d,Outline=2,Shadow=0,Alignment=%d,MarginV=%d"
            % (sub.get('font', 'WenQuanYi Micro Hei'), fs,
               _ass_color(sub.get('color', '#FFFFFF')),
               _ass_color(sub.get('outline_color', '#0d1b2a')),
               border, align, int(H * 0.05)))


# ------------------------------------------------------------------ 视频
def build_video(root, manifest, frames, audio_path, size, srt_path, adur=None, ass_path=None):
    W, H = R.SIZES[size]
    media = os.path.join(root, 'media'); os.makedirs(media, exist_ok=True)
    lst = os.path.join(root, '.cache', 'concat_' + size + '.txt')
    with open(lst, 'w', encoding='utf-8') as f:
        for p, du in frames:
            f.write("file '%s'\nduration %.4f\n" % (p.replace("'", "'\\''"), du))
        f.write("file '%s'\n" % frames[-1][0].replace("'", "'\\''"))
    out = os.path.join(media, '今日要闻_%s.mp4' % ('竖版9比16' if size == 'portrait' else '横版16比9'))
    sub = manifest['config']['subtitle']
    burn = bool(sub.get('on')) and bool(sub.get('burn_in')) and bool(srt_path)
    soft = bool(sub.get('on')) and bool(sub.get('soft_track')) and bool(srt_path)
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', lst, '-i', audio_path]
    if soft:
        cmd += ['-i', srt_path]
    vf = ['scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=8'
          % (W, H, W, H)]
    if burn:
        if ass_path and os.path.isfile(ass_path):
            # ASS 自带逐字卡拉OK样式，不用 force_style 覆盖
            vf.append('subtitles=%s' % _escape_sub(ass_path))
        else:
            vf.append('subtitles=%s:force_style=\'%s\'' % (_escape_sub(srt_path), sub_style(sub, W, H)))
    cmd += ['-map', '0:v:0', '-map', '1:a:0']
    if soft:
        cmd += ['-map', '2:s:0']
    cmd += ['-vf', ','.join(vf),
            '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'stillimage',
            '-crf', '30', '-pix_fmt', 'yuv420p',
            '-x264-params', 'keyint=240:min-keyint=240:scenecut=0',
            '-c:a', 'aac', '-b:a', '64k', '-ac', '1']
    if soft:
        cmd += ['-c:s', 'mov_text', '-metadata:s:s:0', 'language=chi']
    cmd += ['-shortest']
    if adur and adur > 0:
        cmd += ['-t', '%.3f' % adur]
    cmd += ['-movflags', '+faststart', out]
    run(cmd, '出片 ' + size)
    info = probe(out)
    kinds = [s.get('codec_type') for s in info.get('streams', [])]
    return out, {'duration': ff_dur(info), 'streams': kinds,
                 'video': 'video' in kinds, 'audio': 'audio' in kinds,
                 'soft_subtitle': 'subtitle' in kinds,
                 'burned_subtitle': burn, 'size': [W, H],
                 'bytes': os.path.getsize(out)}


def produce(root, manifest, voice, card_cfg):
    """主编排：音频 → SRT/ASS → 单版视频 → 真实状态文件。"""
    audio, adur, ainfo = build_audio(root, voice, manifest)
    vsegs = voice['segments']
    if vsegs and len(vsegs) == len(manifest['segments']):
        s = sum(x['dur'] for x in vsegs)
        if s > 0 and abs(s - adur) > 0.01:
            k = adur / s
            for x in vsegs:
                x['dur'] = x['dur'] * k
            voice['total'] = adur

    sub = manifest['config']['subtitle']
    tw = manifest['config'].get('typewriter') or {}
    size = manifest.get('export_size', 'portrait')
    srt = os.path.join(root, 'media', '今日要闻.srt')
    srt_path = T.build_srt(root, voice, manifest, srt) if sub.get('on') else None
    tw_sub, tw_frame = tw_targets(tw)
    ass_path = None
    if srt_path and sub.get('burn_in') and tw_sub:
        ass_path = os.path.join(root, 'media', '今日要闻.ass')
        T.build_ass(root, voice, manifest, ass_path, R.SIZES[size][0], R.SIZES[size][1])

    cdir = os.path.join(root, '.cache', 'cards_' + size)
    os.makedirs(cdir, exist_ok=True)
    cc = dict(card_cfg, size=size)
    for c in manifest['cards']:
        R.render_card(root, c, cc, os.path.join(cdir, 'c%03d.png' % c['index']))

    status = {'ok': False, 'real_voice': voice.get('real_voice'), 'voice_mode': voice.get('mode'),
              'audio': {'path': 'media/今日播报.mp3', 'duration': adur, 'streams': ainfo['streams'],
                        'mean_db': round(mean_db(audio), 2)},
              'subtitle_on': bool(sub.get('on')), 'subtitle_burned': bool(sub.get('burn_in')) and bool(srt_path),
              'subtitle_count': len(vsegs) if srt_path else 0,
              'typewriter': bool(tw.get('on')), 'typewriter_target': tw.get('target', 'subtitle'),
              'subtitle_typewriter': bool(ass_path),
              'export_size': size,
              'card_count': len(manifest['cards']), 'segment_count': len(manifest['segments']),
              'versions': {}, 'errors': []}
    frames = frames_for(root, manifest, voice, size, tw)
    path, info = build_video(root, manifest, frames, audio, size, srt_path, adur, ass_path)
    status['versions'][size] = dict(info, path=os.path.relpath(path, root))
    if not (info['video'] and info['audio']):
        status['errors'].append('%s 缺视频轨或音轨' % size)
    if sub.get('soft_track') and srt_path and not info['soft_subtitle']:
        status['errors'].append('%s 缺软字幕轨' % size)
    if abs(info['duration'] - adur) > 1.5:
        status['errors'].append('%s 时长与音频不一致 %.2f vs %.2f' % (size, info['duration'], adur))
    status['frame_count'] = len(frames)
    status['ok'] = not status['errors']
    with open(os.path.join(root, 'VIDEO_STATUS.json'), 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    tl = {'source': '逐句音频 ffprobe 真实时长（已按拼接后 PCM 总长归一）',
          'total': round(adur, 3), 'unit': 's',
          'segments': [{'i': i + 1, 'dur': round(x['dur'], 3), 'text': x['text']}
                       for i, x in enumerate(vsegs)]}
    with open(os.path.join(root, 'media', 'timeline.json'), 'w', encoding='utf-8') as f:
        json.dump(tl, f, ensure_ascii=False, indent=2)
    status['timeline_file'] = 'media/timeline.json'
    with open(os.path.join(root, 'VIDEO_STATUS.json'), 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    if not status['ok']:
        raise RuntimeError('成品硬校验未过：' + '; '.join(status['errors']))
    return status
