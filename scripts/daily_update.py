# -*- coding: utf-8 -*-
"""主编排 CLI：数据入口 → 数据契约 → 封面 → 人声 → MP3/SRT/横竖两版 MP4。

用法：
  python scripts/daily_update.py --source snapshot --tts dry-run     # 本地冒烟（静音占位）
  python scripts/daily_update.py --source local_xlsx --tts libretts  # 云端/本机真出片
  python scripts/daily_update.py --no-media                         # 只做取数+文稿+封面
失败一律非 0 退出；不静默降级、不沿用上期成品冒充本期。
"""
import os, sys, json, time, argparse, traceback, math

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import source as SRC
import render as R
import tts as T


def log(*a):
    print(*a, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default=None, help='snapshot | inline | local_xlsx | online_xlsx（缺省读 data_source.json）')
    ap.add_argument('--tts', default='dry-run', help='auto(按配置) | libretts | upload | dry-run')
    ap.add_argument('--no-media', action='store_true')
    ap.add_argument('--root', default=ROOT)
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    t0 = time.time()
    status = {'ok': False, 'version': '1.0', 'timings': {}, 'errors': []}
    try:
        # 干净包里不含 .cache/，这里必须自己建（否则封面 meta 写入会 FileNotFoundError）
        os.makedirs(os.path.join(root, '.cache'), exist_ok=True)
        os.makedirs(os.path.join(root, 'media'), exist_ok=True)
        cfg_path = os.path.join(root, 'data_source.json')
        cfg = SRC.load_json(cfg_path) if os.path.isfile(cfg_path) else {}
        # 页面导出的两个独立主题文件优先于 data_source.json 内嵌值
        for fname, key in (('cover_theme.json', 'cover_theme'), ('video_card_theme.json', 'video_card_theme')):
            fpath = os.path.join(root, fname)
            if os.path.isfile(fpath):
                try:
                    loaded = SRC.load_json(fpath)
                    merged = dict(cfg.get(key) or {})
                    merged.update(loaded if isinstance(loaded, dict) else {})
                    cfg[key] = merged
                except Exception as e:
                    print('WARN 忽略损坏的 %s：%r' % (fname, e))
        if args.source:
            cfg['source_type'] = args.source
        src_cfg = {
            'type': cfg.get('source_type', 'snapshot'),
            'url': cfg.get('url'), 'docs_url': cfg.get('docs_url'),
            'xlsx_url': cfg.get('xlsx_url'), 'local_path': cfg.get('local_path'),
            'sheet_script': cfg.get('sheet_script', '处理后的数据'),
            'sheet_cover': cfg.get('sheet_cover', '排名最靠前的10条'),
            'timeout_s': cfg.get('timeout_s', 20),
            'issue': cfg.get('issue'), 'cover_header': cfg.get('cover_header'),
            'opening': cfg.get('opening'), 'closing': cfg.get('closing'),
            'window_on': cfg.get('window_on', True),
            'voice': cfg.get('voice'), 'bgm': cfg.get('bgm'), 'mix': cfg.get('mix'),
            'subtitle': cfg.get('subtitle', True),
            'typewriter': cfg.get('typewriter') or {},
            'export_size': cfg.get('export_size') or 'portrait',
            'issue_mode': cfg.get('issue_mode', 'auto'),
            'cover_theme': cfg.get('cover_theme') or {},
            'cover_count': cfg.get('cover_count') or (cfg.get('cover_theme') or {}).get('count') or 10,
            'video_card_theme': cfg.get('video_card_theme') or {},
             'inline_payload': cfg.get('inline_payload'),
             'docs_url': cfg.get('docs_url'),
            'rebuilt_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()),
        }
        base = SRC.load_json(os.path.join(root, 'data.json'))
        t = time.time()
        p, origin = SRC.fetch_payload(root, src_cfg, base)
        status['timings']['fetch_s'] = round(time.time() - t, 3)
        status['data_origin'] = origin
        log('数据入口：%s · 口播 %d 条 / 封面 %d 条' % (origin, len(p['news']), len(p['cover'])))

        t = time.time()
        manifest = SRC.build_outputs(root, p, src_cfg)
        status['timings']['build_s'] = round(time.time() - t, 3)
        log('数据契约：%d 句 / %d 卡 / %d 来源，硬闸 %d 条' %
            (len(manifest['segments']), len(manifest['cards']), manifest['source_count'], len(manifest['gates'])))

        # 页面同步读取的活数据（index.html 字节不变）
        live = dict(p, rebuilt_at=src_cfg['rebuilt_at'], data_origin=origin)
        with open(os.path.join(root, 'data.js'), 'w', encoding='utf-8') as f:
            f.write('window.__LIVE_DATA__=' + json.dumps(live, ensure_ascii=False) + ';\n')

        t = time.time()
        cv = dict(src_cfg['cover_theme'])
        cv.setdefault('theme', 'deep_blue'); cv.setdefault('tpl', 'rank'); cv.setdefault('gap', 'airy')
        cv.setdefault('wm', {'on': False})
        cv.setdefault('count', src_cfg.get('cover_count') or 10)
        cv['count'] = int(cv['count'])
        cv['issue'] = src_cfg.get('issue') or p.get('issue_cfg')
        cv['cover_header'] = src_cfg.get('cover_header') or p.get('cover_header')
        esize = src_cfg.get('export_size') or 'portrait'
        ename = '竖版9比16' if esize == 'portrait' else '横版16比9'
        R.render_cover(root, p, dict(cv, size=esize),
                       os.path.join(root, 'media', '封面_%s.png' % ename),
                       os.path.join(root, '.cache', 'cover_meta_%s.json' % esize))
        status['timings']['render_cover_s'] = round(time.time() - t, 3)
        status['export_size'] = esize
        log('封面：%s（%s），显示 %d 条 / %d 行，报头三项=%s' %
            (ename, R.SIZES[esize], cv['count'], math.ceil(cv['count'] / (2 if esize == 'landscape' and cv['count'] >= 2 else 1)), cv['cover_header']))

        if not args.no_media:
            import produce as PD
            mode = args.tts
            if mode == 'auto':
                vcfg = src_cfg['voice'] or {}
                mode = 'upload' if vcfg.get('upload') else (vcfg.get('mode') or 'libretts')
            t = time.time()
            voice = T.build_voice(root, manifest, mode)
            status['timings']['tts_s'] = round(time.time() - t, 3)
            status['voice'] = {'mode': voice['mode'], 'real_voice': voice.get('real_voice'),
                               'total_s': round(voice['total'], 2)}
            log('人声：%s · real_voice=%s · %.1fs' % (voice['mode'], voice.get('real_voice'), voice['total']))
            t = time.time()
            vst = PD.produce(root, manifest, voice, src_cfg['video_card_theme'] or {'tpl': 'frame_card', 'theme': 'deep_blue'})
            # produce 会按拼接后真实音频总长缩放逐句时长，状态文件同步回写，避免日志与成品差 1~2 秒
            status['voice']['total_s'] = round(voice['total'], 2)
            status['timings']['mux_s'] = round(time.time() - t, 3)
            status['video'] = vst
            vv = vst['versions'][esize]
            log('成品：MP3 %.1fs / %s %s / 三轨 %s / 字幕 %d（烧录=%s） / %.2f MiB / %d 帧' %
                (vst['audio']['duration'], ename, vv['size'], vv['streams'],
                 vst['subtitle_count'], vst['subtitle_burned'],
                 vv['bytes'] / 1048576.0, vst.get('frame_count', 0)))

        status['timings']['total_s'] = round(time.time() - t0, 3)
        status['ok'] = True
    except Exception as e:
        status['errors'].append('%s: %s' % (type(e).__name__, e))
        status['traceback'] = traceback.format_exc()[-2500:]
        log('ERROR', status['errors'][-1])
    finally:
        with open(os.path.join(root, 'BUILD_STATUS.json'), 'w', encoding='utf-8') as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
    return 0 if status['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
