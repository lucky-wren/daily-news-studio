/* 浏览器内出片：当前稿 → 逐句人声 → 字级事件帧编码 → 片段缓存 → 拼接下载。
 * 不登录、不写回仓库、不触发云端重建、不读取任何旧成品、不新增任何控件。
 * 帧只在画面变化（逐字跨字）时出现，帧时长持续到下一变化点；帧数由字数决定，与音频时长无关。
 * 口播使用完整文稿，标题与摘要都进字幕；每句一张静态画面。
 */
(function () {
  'use strict';

  var btn = document.getElementById('btnExpVideo');
  var note = document.getElementById('exportNote');
  var recState = document.getElementById('recState');
  var canvas = document.getElementById('plCanvas');
  var BUSY = false, PREPARING = false;   // BUSY=用户导出锁；PREPARING=后台备片锁，两者互不阻塞
  var FPS = 12;                       // 编码器标称帧率，仅用于码率估算
  var MIN_GAP = 1 / 60;              // 事件帧最小间隔
  var MAX_EVENTS = 360;              // 单句事件帧上限
  var CACHE = Object.create(null);   // sig -> {v, a, durUs, w, h, acodec, vcodec, vdesc, adesc}
  var MERGED_PCM = null;             // 整轨解码后的 PCM，按句切给 AudioEncoder
  var OFF = null;                    // 固定尺寸的离屏画布，帧尺寸不随预览画布变化

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] || c;
    });
  }
  function say(h) { if (note) note.innerHTML = h; }
  function state(t) { if (recState) recState.textContent = t; }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function dl(name, blob) {
    var u = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = u; a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function () { try { a.remove(); URL.revokeObjectURL(u); } catch (_) {} }, 4000);
  }

  /* ---------- 最小 MP4 容器写入（avc + aac/opus，非分片，纯字节拼接） ---------- */

  function box(type) {
    var parts = [], i, len = 8;
    for (i = 1; i < arguments.length; i++) parts.push(arguments[i]);
    for (i = 0; i < parts.length; i++) len += parts[i].length;
    var o = new Uint8Array(len), d = new DataView(o.buffer), p = 8, k;
    d.setUint32(0, len);
    for (k = 0; k < 4; k++) o[4 + k] = type.charCodeAt(k);
    for (i = 0; i < parts.length; i++) { o.set(parts[i], p); p += parts[i].length; }
    return o;
  }
  function u32be(n) { var o = new Uint8Array(4); new DataView(o.buffer).setUint32(0, n >>> 0); return o; }
  function u16be(n) { var o = new Uint8Array(2); new DataView(o.buffer).setUint16(0, n >>> 0); return o; }
  function u8be(n) { return new Uint8Array([n & 255]); }
  function zeros(n) { return new Uint8Array(n); }
  function cat() {
    var a = [], i, n = 0;
    for (i = 0; i < arguments.length; i++) { a.push(arguments[i]); n += arguments[i].length; }
    var o = new Uint8Array(n), p = 0;
    for (i = 0; i < a.length; i++) { o.set(a[i], p); p += a[i].length; }
    return o;
  }
  function str(s) { var o = new Uint8Array(s.length), i; for (i = 0; i < s.length; i++) o[i] = s.charCodeAt(i); return o; }
  function full(vf, flags) { return cat(u8be(vf), u8be((flags >>> 16) & 255), u8be((flags >>> 8) & 255), u8be(flags & 255)); }
  function table(arr) { var o = new Uint8Array(arr.length * 4), d = new DataView(o.buffer), i; for (i = 0; i < arr.length; i++) d.setUint32(i * 4, arr[i] >>> 0); return o; }

  function rle(durs) {
    var runs = [], i = 0;
    while (i < durs.length) {
      var v = durs[i], n = 1;
      while (i + n < durs.length && durs[i + n] === v) n++;
      runs.push([n, v]); i += n;
    }
    /* 直接写字节，不用 apply 展开：成片越长、时间条目越多，
       大参数展开会在深调用栈下触发 Maximum call stack size exceeded。 */
    var body = new Uint8Array(runs.length * 8), d = new DataView(body.buffer), p = 0, j;
    for (j = 0; j < runs.length; j++) {
      d.setUint32(p, runs[j][0] >>> 0);
      d.setUint32(p + 4, runs[j][1] >>> 0);
      p += 8;
    }
    return box('stts', full(0, 0), u32be(runs.length), body);
  }
  function stsz(sizes) { return box('stsz', full(0, 0), u32be(0), u32be(sizes.length), table(sizes)); }
  function stsc(n) { return box('stsc', full(0, 0), u32be(1), u32be(1), u32be(n), u32be(1)); }
  function stss(nums) { return box('stss', full(0, 0), u32be(nums.length), table(nums)); }
  function stco(offs) { return box('stco', full(0, 0), u32be(offs.length), table(offs)); }
  function dinf() {
    var url = box('url ', full(0, 1));
    return box('dinf', box('dref', full(0, 0), u32be(1), url));
  }
  function mdhd(timescale, dur) {
    return box('mdhd', full(0, 0), u32be(0), u32be(0), u32be(timescale), u32be(dur), u16be(0x55c4), u16be(0));
  }
  function hdlr(kind) {
    return box('hdlr', full(0, 0), u32be(0), str(kind), zeros(12), str('VideoHandler\0'));
  }
  function hdlrA() {
    return box('hdlr', full(0, 0), u32be(0), str('soun'), zeros(12), str('SoundHandler\0'));
  }
  function mvhd(totalMs) {
    return box('mvhd', full(0, 0), u32be(0), u32be(0), u32be(1000), u32be(totalMs),
      u32be(0x00010000), u16be(0x0100), u16be(0), zeros(8),
      table([0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000]),
      zeros(24), u32be(3));
  }
  function tkhd(id, durMs, w, h, isAudio) {
    return box('tkhd', full(0, 3), u32be(0), u32be(0), u32be(id), u32be(0), u32be(durMs),
      zeros(8), u16be(0), u16be(0), u16be(isAudio ? 0x0100 : 0), u16be(0),
      table([0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000]),
      u32be((w || 0) << 16), u32be((h || 0) << 16));
  }
  function avc1(w, h, avcc) {
    var head = cat(zeros(6), u16be(1), u16be(0), u16be(0), zeros(12),
      u16be(w), u16be(h), u32be(0x00480000), u32be(0x00480000), u32be(0),
      u16be(1), zeros(32), u16be(0x0018), u16be(0xffff));
    return box('avc1', head, avcc);
  }
  function dOps(sr) {
    return box('dOps', u8be(0), u8be(1), u16be(0), u32be(sr || 48000), u16be(0), u8be(0));
  }
  function opusEntry(sr) {
    var head = cat(zeros(6), u16be(1), u16be(0), u16be(0), u32be(0),
      u16be(1), u16be(16), u16be(0), u16be(0), u32be((sr || 48000) << 16));
    return box('Opus', head, dOps(sr));
  }
  function esds(asc) {
    var dsi = boxRaw(0x05, asc);
    var dcd = boxRaw(0x04, cat(u8be(0x40), u8be(0x15), zeros(3), u32be(0), u32be(0), dsi));
    var sl = boxRaw(0x06, u8be(0x02));
    var es = boxRaw(0x03, cat(u16be(0), u8be(0), dcd, sl));
    return box('esds', full(0, 0), es);
  }
  function boxRaw(tag, payload) {
    var len = payload.length, head;
    if (len < 128) head = new Uint8Array([tag, len]);
    else { head = new Uint8Array([tag, 0x80 | ((len >>> 21) & 7), 0x80 | ((len >>> 14) & 127), 0x80 | ((len >>> 7) & 127), len & 127]); }
    return cat(head, payload);
  }
  function mp4aEntry(asc, sr) {
    var head = cat(zeros(6), u16be(1), u16be(0), u16be(0), u32be(0),
      u16be(1), u16be(16), u16be(0), u16be(0), u32be((sr || 48000) << 16));
    return box('mp4a', head, esds(asc || zeros(2)));
  }

  /* chunks: {v:[{type,ts,dur,data}], a:[...]}, 全部已按全局时间重排 */
  function mux(chunks, meta) {
    var VS = 1000000, AS = 1000000;
    var vN = chunks.v.length, aN = chunks.a.length, i;
    var vDur = [], vSize = [], vKey = [], aDur = [], aSize = [];
    var vBytes = 0, aBytes = 0;
    for (i = 0; i < vN; i++) {
      vDur.push(chunks.v[i].dur || Math.round(1e6 / FPS));
      vSize.push(chunks.v[i].data.length); vBytes += chunks.v[i].data.length;
      if (chunks.v[i].type === 'key') vKey.push(i + 1);
    }
    for (i = 0; i < aN; i++) {
      aDur.push(chunks.a[i].dur || Math.round(1024 / (meta.sr || 48000) * 1e6));
      aSize.push(chunks.a[i].data.length); aBytes += chunks.a[i].data.length;
    }
    var totalUs = Math.max(
      vDur.reduce(function (x, y) { return x + y; }, 0),
      aDur.reduce(function (x, y) { return x + y; }, 0), 0);
    var totalMs = Math.round(totalUs / 1000);

    var vStsd = box('stsd', full(0, 0), u32be(1), avc1(meta.w, meta.h, box('avcC', meta.vdesc || zeros(0))));
    var aStsd = box('stsd', full(0, 0), u32be(1),
      meta.acodec === 'opus' ? opusEntry(meta.sr) : mp4aEntry(meta.adesc, meta.sr));

    function trak(id, isAudio, stsd, sizes, durs, keys, durUs) {
      var stbl = box('stbl', stsd, rle(durs),
        (!isAudio && keys.length ? stss(keys) : zeros(0)),
        stsc(sizes.length), stsz(sizes), stco([0]));
      var minf = box('minf', isAudio ? box('smhd', full(0, 0), u16be(0), u16be(0))
                                     : box('vmhd', full(0, 1), u16be(0), zeros(6)),
        dinf(), stbl);
      var mdia = box('mdia', mdhd(1000000, durUs), isAudio ? hdlrA() : hdlr('vide'), minf);
      return box('trak', tkhd(id, totalMs, isAudio ? 0 : meta.w, isAudio ? 0 : meta.h, isAudio), mdia);
    }
    var vTrak = trak(1, false, vStsd, vSize, vDur, vKey, totalUs);
    var aTrak = trak(2, true, aStsd, aSize, aDur, [], totalUs);

    var moov0 = box('moov', mvhd(totalMs), vTrak, aTrak);
    /* 真实布局：ftyp | moov | mdat */
    var ftyp = box('ftyp', str('isom'), u32be(0), str('isom'), str('iso2'), str('avc1'), str('mp41'));
    var moovSize = moov0.length;
    var mdatPayloadStart = ftyp.length + moovSize + 8;
    
    function setLastU32(arr, val) { var o = arr.slice(); new DataView(o.buffer).setUint32(o.length - 4, val >>> 0); return o; }
    vTrak = setLastU32(vTrak, mdatPayloadStart);
    aTrak = setLastU32(aTrak, mdatPayloadStart + vBytes);
    var moov = box('moov', mvhd(totalMs), vTrak, aTrak);
    /* 尺寸不因偏移数值改变，保持一致 */
    if (moov.length !== moovSize) throw new Error('容器尺寸计算不一致');

    var mdat = new Uint8Array(8 + vBytes + aBytes), p = 8;
    var d = new DataView(mdat.buffer); d.setUint32(0, mdat.length);
    mdat[4] = 109; mdat[5] = 100; mdat[6] = 97; mdat[7] = 116; // mdat
    for (i = 0; i < vN; i++) { mdat.set(chunks.v[i].data, p); p += chunks.v[i].data.length; }
    for (i = 0; i < aN; i++) { mdat.set(chunks.a[i].data, p); p += chunks.a[i].data.length; }

    return new Blob([ftyp, moov, mdat], { type: 'video/mp4' });
  }

  /* ---------- WebCodecs 能力探测（一次） ---------- */
  async function pickConfig(w, h, sr) {
    var vcodec = null, acodec = null, vcfg = null, acfg = null;
    if (!window.VideoEncoder || !window.AudioEncoder || !window.VideoFrame || !window.AudioData)
      return null;
    var vlist = ['avc1.42001f', 'avc1.42E01E'];
    for (var i = 0; i < vlist.length; i++) {
      try {
        var s = await VideoEncoder.isConfigSupported({ codec: vlist[i], width: w, height: h,
          bitrate: 1800000, framerate: FPS, latencyMode: 'quality', avc: { format: 'avc' } });
        if (s.supported) { vcodec = vlist[i]; vcfg = s.config; break; }
      } catch (_) {}
    }
    if (!vcodec) return null;
    for (var j = 0; j < ['mp4a.40.2', 'opus'].length; j++) {
      var c = ['mp4a.40.2', 'opus'][j];
      try {
        var t = await AudioEncoder.isConfigSupported({ codec: c, sampleRate: sr || 48000,
          numberOfChannels: 1, bitrate: 96000 });
        if (t.supported) { acodec = c; acfg = t.config; break; }
      } catch (_) {}
    }
    if (!acodec) return null;
    return { w: w, h: h, vcodec: vcodec, acodec: acodec, vcfg: vcfg, acfg: acfg,
      vdesc: null, adesc: null };
  }

  async function pickWebmConfig(w, h, sr) {
    if (!window.VideoEncoder || !window.AudioEncoder || !window.VideoFrame || !window.AudioData)
      return null;
    var list = ['vp09.00.10.08', 'vp8'], vcodec = null, vcfg = null, i;
    for (i = 0; i < list.length; i++) {
      try {
        var s = await VideoEncoder.isConfigSupported({ codec: list[i], width: w, height: h,
          bitrate: 1800000, framerate: FPS, latencyMode: 'quality' });
        if (s.supported) { vcodec = list[i]; vcfg = s.config; break; }
      } catch (_) {}
    }
    if (!vcodec) return null;
    var acfg = null;
    try {
      var t = await AudioEncoder.isConfigSupported({ codec: 'opus', sampleRate: sr || 48000,
        numberOfChannels: 1, bitrate: 96000 });
      if (t.supported) acfg = t.config;
    } catch (_) {}
    if (!acfg) return null;
    return { w: w, h: h, vcodec: vcodec, acodec: 'opus', vcfg: vcfg, acfg: acfg,
      vdesc: null, adesc: null };
  }

  /* 编码器自动回退：优先 H.264/AAC → MP4；浏览器无 H.264 时用 VP9/Opus
     离线编码 → WebM。两条都是离线编码（秒级），不会再退化成按成片时长实时录制。 */
  async function pickAny(w, h, sr) {
    var cfg = await pickConfig(w, h, sr);
    if (cfg) return { cfg: cfg, container: 'mp4' };
    cfg = await pickWebmConfig(w, h, sr);
    if (cfg) return { cfg: cfg, container: 'webm' };
    return null;
  }
  function readyKey(seg, container) { return readySig(seg) + '|' + container; }

  /* ---------- WebM 容器：EBML 最小实现（VP9 / VP8 + Opus） ---------- */
  function hx(s) {
    var o = new Uint8Array(s.length / 2), i;
    for (i = 0; i < o.length; i++) o[i] = parseInt(s.substr(i * 2, 2), 16);
    return o;
  }
  function ebmlSize(n, fixed) {
    var len = fixed || 1, max, o, v = n, i;
    for (;;) {
      max = Math.pow(2, 7 * len) - 2;
      if (n <= max || len >= 8) break;
      len++;
    }
    o = new Uint8Array(len); v = n;
    for (i = len - 1; i >= 0; i--) { o[i] = v & 255; v = Math.floor(v / 256); }
    o[0] |= 1 << (8 - len);
    return o;
  }
  function ebmlUint(n) {
    var len = 1, o, i, v = n;
    while (n >= Math.pow(256, len) && len < 8) len++;
    o = new Uint8Array(len); v = n;
    for (i = len - 1; i >= 0; i--) { o[i] = v & 255; v = Math.floor(v / 256); }
    return o;
  }
  function ebmlF64(x) { var o = new Uint8Array(8); new DataView(o.buffer).setFloat64(0, x); return o; }
  function ebmlEl(id, payload) { return cat(hx(id), ebmlSize(payload.length), payload); }
  function i16be(n) { return new Uint8Array([(n >> 8) & 255, n & 255]); }
  function opusHead(sr) {
    var o = new Uint8Array(19), d = new DataView(o.buffer), i;
    for (i = 0; i < 8; i++) o[i] = 'OpusHead'.charCodeAt(i);
    o[8] = 1; o[9] = 1;
    d.setUint16(10, 0, true); d.setUint32(12, sr >>> 0, true);
    d.setInt16(16, 0, true); o[18] = 0;
    return o;
  }
  function muxWebm(chunks, meta) {
    var w = meta.w, h = meta.h, sr = meta.sr || 48000, i, x;
    var vcodec = /vp8/i.test(meta.vcodec || '') ? 'V_VP8' : 'V_VP9';
    var blocks = [];
    for (i = 0; i < chunks.v.length; i++) {
      x = chunks.v[i];
      blocks.push({ t: Math.round(x.ts / 1000), k: x.type === 'key', tr: 1, data: x.data });
    }
    for (i = 0; i < chunks.a.length; i++) {
      x = chunks.a[i];
      blocks.push({ t: Math.round(x.ts / 1000), k: false, tr: 2, data: x.data });
    }
    blocks.sort(function (a, b) { return a.t - b.t; });
    var totalMs = blocks.length ? Math.max(blocks[blocks.length - 1].t, 1) : 1;
    var ebml = ebmlEl('1A45DFA3', cat(
      ebmlEl('4286', ebmlUint(1)), ebmlEl('42F7', ebmlUint(1)),
      ebmlEl('42F2', ebmlUint(4)), ebmlEl('42F3', ebmlUint(8)),
      ebmlEl('4282', str('webm')), ebmlEl('4287', ebmlUint(2)), ebmlEl('4285', ebmlUint(2))
    ));
    var info = ebmlEl('1549A966', cat(
      ebmlEl('2AD7B1', ebmlUint(1000000)),
      ebmlEl('4489', ebmlF64(totalMs)),
      ebmlEl('4D80', str('daily-news-studio')),
      ebmlEl('5741', str('daily-news-studio'))
    ));
    var vTrack = ebmlEl('AE', cat(
      ebmlEl('D7', ebmlUint(1)), ebmlEl('73C5', ebmlUint(1)), ebmlEl('83', ebmlUint(1)),
      ebmlEl('86', str(vcodec)),
      ebmlEl('E0', cat(ebmlEl('B0', ebmlUint(w)), ebmlEl('BA', ebmlUint(h))))
    ));
    var aTrack = ebmlEl('AE', cat(
      ebmlEl('D7', ebmlUint(2)), ebmlEl('73C5', ebmlUint(2)), ebmlEl('83', ebmlUint(2)),
      ebmlEl('86', str('A_OPUS')), ebmlEl('63A2', opusHead(sr)),
      ebmlEl('E1', cat(ebmlEl('B5', ebmlF64(sr)), ebmlEl('9F', ebmlUint(1))))
    ));
    var tracks = ebmlEl('1654AE6B', cat(vTrack, aTrack));
    var clusters = [], bucket = -1, cur = null;
    for (i = 0; i < blocks.length; i++) {
      var b = blocks[i], bk = Math.floor(b.t / 30000);
      if (bk !== bucket) {
        if (cur) clusters.push(ebmlEl('1F43B675', cur));
        bucket = bk; cur = ebmlEl('E7', ebmlUint(bk * 30000));
      }
      var rel = Math.max(-32768, Math.min(32767, b.t - bk * 30000));
      cur = cat(cur, ebmlEl('A3', cat(ebmlSize(b.tr, 1), i16be(rel),
        u8be(b.k ? 0x80 : 0), b.data)));
    }
    if (cur) clusters.push(ebmlEl('1F43B675', cur));
    var unknown = new Uint8Array([0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]);
    return new Blob([ebml, hx('18538067'), unknown, info, tracks].concat(clusters),
      { type: 'video/webm' });
  }

  function copyChunk(c) {
    var b = new Uint8Array(c.byteLength); c.copyTo(b);
    return b;
  }

  /* ---------- 字级事件帧：只在画面真正变化的时刻出帧 ---------- */
  /* 判重不用读像素：逐字显现是阶跃的，直接用「已显字数」做键，纯算术。
     逐字关闭时整句只出 1 帧。 */
  function frameKey(t, dur, ev) {
    var twOn = !!(S.typewriter && S.typewriter.on);
    var twFrm = false;
    if (!twOn) return 'still';
    if (ev && ev.ct) {
      var c = twFrm ? window.CharTimes.countAt(ev.ct, t) : window.CharTimes.subCountAt(ev.ct, t);
      return (ev.subMode ? 'sub:' : '') + c;
    }
    if (ev && ev.subMode) {
      /* 字幕块内逐字：以「已显字数」为键，跨字才出帧 */
      return 'sub:' + Math.floor(Math.max(0, Math.min(1, t / Math.max(.2, dur))) * ev.nSub);
    }
    if (!twFrm) return 'still';
    var rv;
    try { rv = frameReveal(t, dur, ev.leadT); } catch (_) { rv = { t: 0, s: 0 }; }
    var a = Math.floor(Math.max(0, Math.min(1, rv.t)) * ev.nLead);
    var b = Math.floor(Math.max(0, Math.min(1, rv.s)) * ev.nSub);
    return a + ':' + b;
  }

  /* 逐字显现是阶跃的：标题段 leadT 内显 nLead 字，摘要段 subT 内显 nSub 字。
     按字边界取候选时刻，再用画面指纹去重——逐字关闭时整句只剩 1 帧。 */
  function eventTimes(idx, dur) {
    try { S.playIdx = idx; S.playPos = 0; drawPlayer(); } catch (_) {}
    var ss = window.__subSync || {};
    var twOn = !!(S.typewriter && S.typewriter.on);
    var twFrm = false;
    var leadT = 0, subT = dur, nLead = 1, nSub = 1, subMode = false;
    var ts = [0], i;
    var ct = (window.CHAR_TIMES || [])[idx] || null;
    if (!twOn) return { ts: [0], leadT: 0, nLead: 1, nSub: 1, subMode: false, ct: null };
    if (ct) {
      /* 字级时刻表：播放、预览、导出共用；停顿处不推进，发声处按真实节奏推进 */
      subMode = !twFrm;
      nLead = ct.n; nSub = ct.sub_n;
      ts = window.CharTimes.events(ct, subMode);
      leadT = Math.max(0.05, Math.min(dur * 0.92, +(ss.lead_dur || 0) || dur * 0.28));
      subT = Math.max(0.2, dur - leadT);
    } else if (!twFrm) {
      if (!window.__subDraw) return { ts: [0], leadT: 0, nLead: 1, nSub: 1, subMode: false, ct: null };
      subMode = true;
      nSub = Math.max(1, Math.min(MAX_EVENTS, Math.round(+(ss.sub_len || 0) || 1)));
      for (i = 1; i < nSub; i++) ts.push(dur * i / nSub);
    } else {
      leadT = Math.max(0.05, Math.min(dur * 0.92, +(ss.lead_dur || 0) || dur * 0.28));
      subT = Math.max(0.2, dur - leadT);
      nLead = Math.min(120, Math.max(1, +(ss.lead || 0) || 1));
      nSub = Math.min(160, Math.max(1, +(ss.sub_len || 0) || 1));
      for (i = 1; i <= nLead; i++) ts.push(leadT * i / nLead);
      for (i = 1; i <= nSub; i++) ts.push(leadT + subT * i / nSub);
    }
    // 不把 dur 作为帧时刻：最后一帧由 flushPending(dur) 延伸到句末
    ts.sort(function (a, b) { return a - b; });
    var out = [], last = -1;
    for (i = 0; i < ts.length; i++) {
      var t = Math.max(0, Math.min(dur, ts[i]));
      if (t - last < MIN_GAP) continue;
      out.push(t); last = t;
    }
    if (out.length > MAX_EVENTS) {
      var step = out.length / MAX_EVENTS, red = [];
      for (i = 0; i < MAX_EVENTS; i++) red.push(out[Math.floor(i * step)]);
      out = red;
    }
    if (!out.length) out.push(0);
    return { ts: out, leadT: leadT, nLead: nLead, nSub: nSub, subMode: subMode, ct: ct };
  }

  /* ---------- 逐句离线编码：不等播放，帧时间戳由句内真实音轨推进 ---------- */
  async function encodeSentence(idx, seg, m, cfg) {
    var start = m.offsets[idx];
    var end = (idx + 1 < m.offsets.length) ? m.offsets[idx + 1] : m.dur;
    var dur = Math.max(0.2, end - start);
    var durUs = Math.round(dur * 1e6);
    var vchunks = [], achunks = [], vdesc = null, adesc = null;
    var durByTs = Object.create(null);                  // timestamp(us) -> 本帧持续时长(us)

    var venc = new VideoEncoder({
      output: function (c, meta) {
        if (!vdesc && meta && meta.decoderConfig && meta.decoderConfig.description) {
          var d = meta.decoderConfig.description;
          vdesc = new Uint8Array(d.byteLength !== undefined ? d.byteLength : d.length);
          if (d instanceof Uint8Array) vdesc.set(d); else new Uint8Array(d).forEach(function (x, k) { vdesc[k] = x; });
        }
        vchunks.push({ type: c.type, ts: c.timestamp,
          dur: durByTs[c.timestamp] || c.duration || Math.round(1e6 / FPS), data: copyChunk(c) });
      },
      error: function (e) { throw new Error('视频编码失败：' + (e && e.message || e)); }
    });
    venc.configure(Object.assign({}, cfg.vcfg));

    var aenc = new AudioEncoder({
      output: function (c, meta) {
        if (!adesc && meta && meta.decoderConfig && meta.decoderConfig.description) {
          var d = meta.decoderConfig.description;
          adesc = new Uint8Array(d instanceof Uint8Array ? d.length : (d.byteLength || 0));
          if (d instanceof Uint8Array) adesc.set(d); else adesc.set(new Uint8Array(d));
        }
        achunks.push({ type: c.type, ts: c.timestamp,
          dur: c.duration || Math.round(1024 / sr * 1e6), data: copyChunk(c) });
      },
      error: function (e) { throw new Error('音频编码失败：' + (e && e.message || e)); }
    });
    aenc.configure(Object.assign({}, cfg.acfg));

    /* 音频：从整轨 PCM 中切出这一句，按 1024 帧喂编码器 */
    var sr = MERGED_PCM.sampleRate || 48000;
    var ch = MERGED_PCM.getChannelData(0);
    var s0 = Math.floor(start * sr), s1 = Math.min(ch.length, Math.ceil(end * sr));
    var FR = 1024, k = 0;
    for (var off = s0; off < s1; off += FR) {
      var n = Math.min(FR, s1 - off), data = new Float32Array(n), q;
      for (q = 0; q < n; q++) data[q] = ch[off + q] || 0;
      var ad = new AudioData({ format: 'f32-planar', sampleRate: sr, numberOfFrames: n,
        numberOfChannels: 1, timestamp: Math.round((k * FR / sr) * 1e6), data: data });
      aenc.encode(ad); ad.close(); k++;
    }
    await aenc.flush(); aenc.close();

    /* 视频：事件驱动帧。画面未变就不出帧，变化的帧持续到下一个变化点。
       合成后点播放走的是 rAF + audio 时钟（零编码），这里只在导出时跑一遍。 */
    try { stopPlay(); } catch (_) {}
    S.playing = false;
    var ev = eventTimes(idx, dur);
    var octx = OFF.getContext('2d');
    var pending = null, lastKey = null, sent = 0, ei, t;

    function flushPending(nextT) {
      if (!pending) return;
      durByTs[pending.ts] = Math.max(1, Math.round((nextT - pending.t) * 1e6));
      venc.encode(pending.frame, { keyFrame: sent === 0 || sent % 60 === 0 });
      pending.frame.close(); sent++;
      pending = null;
    }

    for (ei = 0; ei < ev.ts.length; ei++) {
      t = ev.ts[ei];
      var key = frameKey(t, dur, ev);
      if (lastKey !== null && key === lastKey) continue;   // 没跨字，时长并入上一帧
      flushPending(t);
      S.playIdx = idx; S.playPos = Math.min(dur, t);
      try { drawPlayer(); } catch (_) {}
      octx.drawImage(canvas, 0, 0, cfg.w, cfg.h);
      pending = { t: t, ts: Math.round(t * 1e6),
        frame: new VideoFrame(OFF, { timestamp: Math.round(t * 1e6),
                                     duration: Math.round(1e6 / FPS) }) };
      lastKey = key;
      if (venc.encodeQueueSize > 6) {
        while (venc.encodeQueueSize > 2) await sleep(0);
      }
    }
    flushPending(dur);
    await venc.flush(); venc.close();

    return { v: vchunks, a: achunks, durUs: durUs, w: cfg.w, h: cfg.h,
      vcodec: cfg.vcodec, acodec: cfg.acodec, vdesc: vdesc, adesc: adesc,
      frames: sent, events: ev.ts.length, text: seg[idx] };
  }

  /* 配乐混入导出音轨：人声 + 循环铺满的背景音乐，按 06 区音量与总增益合成。
     预览另由独立音频元素播放；这里保证下载下来的成片里真的有配乐。 */
  var BGM_DECODE_CACHE = Object.create(null);
  async function decodeBgm(ac, src) {
    if (BGM_DECODE_CACHE[src]) return BGM_DECODE_CACHE[src];
    var arr = await (await fetch(src)).arrayBuffer();
    var bbuf = await ac.decodeAudioData(arr);
    var r = { data: bbuf.getChannelData(0), n: bbuf.length, sr: bbuf.sampleRate };
    BGM_DECODE_CACHE[src] = r;
    return r;
  }
  async function mixBgmIntoPCM(ac, pcm) {
    var st = (typeof S !== 'undefined' && S) ? S : (window.S || {});
    var bg = st.bgm || {}, mx = st.mix || {};
    var builtin = (typeof BUILTIN_BGM !== 'undefined') ? BUILTIN_BGM : 'assets/bgm/内置配乐.mp3';
    var src = (bg.url && bg.background === bg.file) ? bg.url
            : (bg.background ? builtin : null);
    var b = null, bn = 0, bsr = pcm.sampleRate, vol = 0;
    if (src && bg.background) {
      try {
        var d = await decodeBgm(ac, src);
        b = d.data; bn = d.n; bsr = d.sr;
        vol = Math.max(0, Math.min(1, +bg.volume || 0));
      } catch (e) { b = null; }
    }
    var ch = pcm.getChannelData(0), n = ch.length, sr = pcm.sampleRate;
    var vg = Math.max(0, +(st.voice && st.voice.volume) || 0);
    var mg = Math.pow(10, (+mx.master_db || 0) / 20);
    for (var i = 0; i < n; i++) {
      var bi = b ? Math.floor(i * bsr / sr) % bn : 0;
      var x = (ch[i] * vg + (b ? (b[bi] || 0) * vol : 0)) * mg;
      ch[i] = x > 1 ? 1 : (x < -1 ? -1 : x);
    }
  }

  /* ---------- 当前稿逐句人声（沿用页面已有缓存）+ 解码整轨 PCM ---------- */
  async function ensureVoice() {
    var seg = segments();
    if (!seg.length) throw new Error('当前文稿为空');
    var keys = seg.map(voiceSig);
    var track = [], reused = 0, fresh = 0, i;
    /* 页面「逐句合成」已经把整条人声轨填好且签名一致时，直接复用，
       不再逐句重复请求在线 TTS。 */
    var reuseTrack = false;
    try {
      reuseTrack = (typeof VOICE_TRACK !== 'undefined') &&
        VOICE_TRACK.length >= seg.length &&
        VOICE_TRACK.slice(0, seg.length).every(Boolean) &&
        (typeof LAST_SIG !== 'undefined') && LAST_SIG.length >= seg.length &&
        keys.every(function (k, j) { return LAST_SIG[j] === k; });
    } catch (_) {}
    if (reuseTrack) {
      for (i = 0; i < seg.length; i++) track[i] = { blob: VOICE_TRACK[i].blob, dur: VOICE_TRACK[i].dur };
      reused = seg.length;
    } else {
      if (typeof openVoiceCache === 'function') { try { await openVoiceCache(); } catch (_) {} }
      /* 未命中页面音轨时按 4 路并发补齐，并复用页面的超时 / 重试逻辑，不再逐句串行等待 */
      var cursor = 0, lane = Math.min(4, seg.length);
      await Promise.all(Array.from({ length: lane }, async function () {
        while (cursor < seg.length) {
          var j = cursor++;
          var b = null;
          if (typeof cacheGet === 'function') { try { b = await cacheGet(keys[j]); } catch (_) {} }
          if (!b) {
            if (typeof window.__voxSynthOne === 'function') b = await window.__voxSynthOne(seg[j], null, null);
            else b = await ttsFetch(seg[j]);
            if (typeof cachePut === 'function') { try { cachePut(keys[j], b); } catch (_) {} }
            fresh++;
          } else reused++;
          track[j] = { blob: b, dur: 0 };
        }
      }));
      var durs = await Promise.all(track.map(function (t) { return voiceDuration(t.blob); }));
      for (i = 0; i < seg.length; i++) track[i].dur = durs[i];
    }
    VOICE_TRACK.length = 0;
    seg.forEach(function (_, i) { VOICE_TRACK[i] = track[i]; });
    LAST_SIG = keys.slice();
    if (MERGED) { try { URL.revokeObjectURL(MERGED.url); } catch (_) {} MERGED = null; }
    if (mergedAudio) { try { mergedAudio.pause(); } catch (_) {} mergedAudio = null; }
    S.voiceDurs = track.map(function (t) { return t.dur; });
    if (!(S.leadDurs && S.leadDurs.length === seg.length)) { S.leadDurs = []; S.timeline = null; }
    var m = await buildMergedTrack();
    var AC = window.AudioContext || window.webkitAudioContext;
    var ac = new AC();
    MERGED_PCM = await ac.decodeAudioData(await m.blob.arrayBuffer());
    try { await mixBgmIntoPCM(ac, MERGED_PCM); } catch (_) {}
    try { await ac.close(); } catch (_) {}
    try { await window.CharTimes.ensure(seg, track, subtitleSegments(), window.TTS_BOUNDARIES || null); } catch (_) {}
    return { m: m, seg: seg, reused: reused, fresh: fresh };
  }

  /* ---------- 兜底：无 WebCodecs 时保留实时录制，明确告知 ---------- */
  async function fallbackRecord(c) {
    var m = c.m, seg = c.seg;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || !HTMLCanvasElement.prototype.captureStream || !window.MediaRecorder)
      throw new Error('当前浏览器不支持离线编码或本地录制，请用桌面版 Chrome / Edge');
    var rc = new AC(); if (rc.state === 'suspended') { try { await rc.resume(); } catch (_) {} }
    var ab = (typeof MERGED_PCM !== 'undefined' && MERGED_PCM) ? MERGED_PCM : await rc.decodeAudioData(await m.blob.arrayBuffer());
    var src = rc.createBufferSource(); src.buffer = ab;
    var gain = rc.createGain(); gain.gain.value = 1;
    var dest = rc.createMediaStreamDestination();
    src.connect(gain); gain.connect(rc.destination); gain.connect(dest);
    var vstream = canvas.captureStream(FPS);
    var stream = new MediaStream([].concat(vstream.getVideoTracks(), dest.stream.getAudioTracks()));
    var mime = ['video/mp4;codecs="avc1.42E01E,mp4a.40.2"', 'video/mp4',
      'video/webm;codecs=vp9,opus', 'video/webm'].filter(function (x) { return MediaRecorder.isTypeSupported(x); })[0] || '';
    var rec = new MediaRecorder(stream, { mimeType: mime }), chunks = [];
    rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
    var done = new Promise(function (r) { rec.onstop = function () { r(new Blob(chunks, { type: mime })); }; });
    try { stopPlay(); } catch (_) {}
    S.playIdx = 0; S.playPos = 0; S.playing = true;
    var t0 = rc.currentTime + 0.05; src.start(t0); rec.start(500);
    await new Promise(function (resolve) {
      (function step() {
        var t = rc.currentTime - t0;
        if (t < 0) { requestAnimationFrame(step); return; }
        if (t >= m.dur - 0.02) { S.playIdx = m.offsets.length - 1; drawPlayer(); resolve(); return; }
        var i = 0; while (i < m.offsets.length - 1 && t >= m.offsets[i + 1] - 0.001) i++;
        S.playIdx = i; S.playPos = Math.max(0, t - m.offsets[i]); drawPlayer();
        requestAnimationFrame(step);
      })();
    });
    S.playing = false; try { src.stop(); } catch (_) {}
    if (rec.state !== 'inactive') rec.stop();
    var blob = await done;
    try { await rc.close(); } catch (_) {}
    try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (_) {}
    return { blob: blob, ext: /webm/i.test(mime) ? 'webm' : 'mp4' };
  }

  /* ---------- 成片预生成：合成人声之后就备好，点导出只做一次下载 ---------- */
  var READY = null;                       // { sig, blob, name, stats }
  var EXPORTING = false;                  // 仅用户主动导出时更新可见进度
  var PREP_TIMER = null;

  function sigOf(seg) {
    return seg.map(function (t) { return voiceSig(t); }).join('\u0001');
  }
  /* 字级边界指纹：网关返回的真实时刻一变，片段与成片立即失效重编，
     避免命中按近似时间表编码的旧画面。 */
  function boundarySig() {
    var b = window.TTS_BOUNDARIES;
    if (!b || !b.length) return 'none';
    var parts = [], i, j, a, h, x;
    for (i = 0; i < b.length; i++) {
      a = b[i];
      if (!a || !a.length) { parts.push('0'); continue; }
      h = 0x811c9dc5;
      for (j = 0; j < a.length; j++) {
        x = a[j];
        h = Math.imul(h ^ (+x.offset || 0), 16777619) >>> 0;
        h = Math.imul(h ^ (+x.duration || 0), 16777619) >>> 0;
      }
      parts.push(a.length + ':' + h.toString(16));
    }
    return parts.join(',');
  }
  /* 画面配置指纹：作用位置、字幕设置、字号、封面/卡片配置任一变化，
     缓存片段与已就绪成片都必须失效，否则会下载到另一种模式的旧画面。 */
  function visualSig() {
    var d = window.__daily || {}, api = d.api || {}, st = d.get ? d.get() : (window.S || {}), s;
    try {
      s = JSON.stringify([
        api.typewriterCfg ? api.typewriterCfg() : st.typewriter,
        api.subtitleCfg ? api.subtitleCfg() : st.subtitle,
        api.bgmCfg ? api.bgmCfg() : st.bgm, api.mixCfg ? api.mixCfg() : st.mix,
        st.cover, st.card, api.exportSize ? api.exportSize() : '', boundarySig()
      ]);
    } catch (_) { s = String(st.typewriter && st.typewriter.target) + '|' + String(st.subtitle && st.subtitle.font_size); }
    var h = 0x811c9dc5, i;
    for (i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619) >>> 0;
    return h.toString(16) + '.' + s.length;
  }
  function readySig(seg) { return sigOf(seg) + '|' + visualSig(); }
  function modeLabel() {
    var t = (S.typewriter && S.typewriter.target) || 'frame';
    return t === 'frame' ? '静态画面' : (t === 'subtitle' ? '字幕逐字' : '静态画面');
  }

  function voiceComplete(seg) {
    try {
      return typeof VOICE_TRACK !== 'undefined' && VOICE_TRACK.length >= seg.length &&
             VOICE_TRACK.slice(0, seg.length).every(Boolean);
    } catch (_) { return false; }
  }
  async function waitPlay() {
    var guard = 0;
    while (S.playing && guard++ < 3600) await sleep(100);  // 播放优先，绝不抢画布
    return !S.playing;
  }
  var RESUME = null;
  function scheduleResume() {
    if (RESUME) return;
    var n = 0;
    RESUME = setInterval(function () {
      n++;
      var quiet = !S.playing && !(window.__bgmPreviewing && window.__bgmPreviewing());
      if (quiet || n > 1200) {
        clearInterval(RESUME); RESUME = null;
        if (!BUSY && !PREPARING) { try { prepare(); } catch (_) {} }
      }
    }, 500);
  }

  /* 只编码 + 封装，不下载。逐句片段按内容签名缓存，改一句只重编一句。 */
  async function build(c, cfg, sr, container, yieldToExport) {
    container = container || 'mp4';
    var seg = c.seg, m = c.m, vs = visualSig(), sigs = seg.map(function (t, i) {
      return voiceSig(t) + '|' + vs + '|' + cfg.w + 'x' + cfg.h + '|' + cfg.vcodec + '|' + cfg.acodec + '|vfr5|' + container;
    });
    var need = [], i;
    for (i = 0; i < seg.length; i++) if (!CACHE[sigs[i]]) need.push(i);
    state('成片准备：' + (seg.length - need.length) + ' 句复用 / ' + need.length + ' 句待编');
    for (i = 0; i < need.length; i++) {
      if (yieldToExport && BUSY) throw new Error('__exporting__');   // 仅后台备片让出编码器，用户自身导出不受影响
      if (!(await waitPlay())) throw new Error('__playing__');
      var idx = need[i];
      CACHE[sigs[idx]] = await encodeSentence(idx, seg, m, cfg);
      state('成片准备：' + (i + 1) + '/' + need.length);
      if (EXPORTING && ((i + 1) % 4 === 0 || i + 1 === need.length))
        say('<b>正在离线编码当前稿… ' + (i + 1) + '/' + need.length + ' 句</b>');
    }

    var t0 = performance.now();
    var V = [], A = [], base = 0;
    for (i = 0; i < seg.length; i++) {
      var segc = CACHE[sigs[i]];
      segc.v.forEach(function (x) { V.push({ type: x.type, ts: base + x.ts, dur: x.dur, data: x.data }); });
      segc.a.forEach(function (x) { A.push({ type: x.type, ts: base + x.ts, dur: x.dur, data: x.data }); });
      base += segc.durUs;
    }
    /* 句边界的末帧/末音块 ts 可能恰好等于本段时长，与下一句首帧重复。
       按轨强制严格递增，避免容器 dts 非单调引起播放器卡顿与音画错位。 */
    function monotonic(arr) {
      arr.sort(function (a, b) { return a.ts - b.ts; });
      var prev = -1;
      for (var k = 0; k < arr.length; k++) {
        if (arr[k].ts <= prev) arr[k].ts = prev + 1;
        prev = arr[k].ts;
      }
    }
    monotonic(V); monotonic(A);
    var first = CACHE[sigs[0]];
    var blob = container === 'webm'
      ? muxWebm({ v: V, a: A }, { w: cfg.w, h: cfg.h, sr: sr, vcodec: cfg.vcodec })
      : mux({ v: V, a: A }, { w: cfg.w, h: cfg.h, sr: sr, acodec: cfg.acodec,
          vdesc: first.vdesc, adesc: first.adesc });
    var joinMs = Math.round(performance.now() - t0);
    var stats = {
      scriptText: seg.join('\n\n'), segs: seg.slice(), dur: base / 1e6,
      frames: V.length, audioChunks: A.length, joinMs: joinMs,
      fixedFramesWouldBe: (function () {
        var n = 0;
        for (var j = 0; j < seg.length; j++) {
          var d = (j + 1 < m.offsets.length ? m.offsets[j + 1] : m.dur) - m.offsets[j];
          n += Math.max(1, Math.ceil(Math.max(0.2, d) * FPS));
        }
        return n;
      })(),
      reused: seg.length - need.length, fresh: need.length,
      budget: (window.__voxBrief||{}).budget, briefChars: (window.__voxBrief||{}).chars,
      vcodec: cfg.vcodec, acodec: cfg.acodec,
      encBytes: V.reduce(function (s, x) { return s + x.data.length; }, 0) +
                A.reduce(function (s, x) { return s + x.data.length; }, 0)
    };
    window.__voxExport = stats;
    stats.mode = modeLabel();
    return { blob: blob, name: '今日要闻_竖版9比16.' + (container === 'webm' ? 'webm' : 'mp4'),
             sig: readySig(seg), stats: stats, joinMs: joinMs };
  }

  async function prepare() {
    if (BUSY || PREPARING) return;
    if (window.__bgmPreviewing && window.__bgmPreviewing()) {
      state('配乐试听中 · 成片准备将在试听结束后自动开始');
      scheduleResume(); return;
    }
    if (currentFormat() !== 'mp4') return;   // 非视频格式不预生成成片、不碰人声
    var seg = segments();
    if (!seg.length || !voiceComplete(seg)) return;      // 没合成人声就不预生成
    if (READY && READY.sig === readyKey(seg, READY.container || 'mp4')) return;
    if (S.playing) {
      /* 播放期间连 ensureVoice 都不启动（它会重建整轨并停掉播放），
         停止后再自动继续，播放全程零干扰。 */
      state('播放中 · 成片准备将在停止播放后自动开始');
      scheduleResume();
      return;
    }
    PREPARING = true;
    try {
      var c = await ensureVoice();
      var sr = MERGED_PCM.sampleRate || 48000;
      OFF = document.createElement('canvas'); OFF.width = 540; OFF.height = 960;
      var pick = await pickAny(540, 960, sr);
      if (!pick) { state('当前浏览器不支持离线编码，点导出将按成片时长实时录制'); return; }
      var r = await build(c, pick.cfg, sr, pick.container, true);
      r.sig = readyKey(seg, pick.container);
      r.container = pick.container;
      READY = r;
      state('成片已就绪 · ' + (pick.container === 'webm' ? 'WebM (VP9/Opus)' : 'MP4 (H.264/AAC)') +
            ' · 点导出立即下载（0 编码）');
      say('<b>成片已按当前稿备好，点导出立即下载。</b>播放与画面预览不受影响；改一句只重算一句。');
    } catch (e) {
      var em = String((e && e.message) || e);
      if (em.indexOf('__playing__') >= 0 || em.indexOf('__exporting__') >= 0) {
        state(em.indexOf('__exporting__') >= 0
          ? '导出优先 · 后台备片已让路' : '播放中 · 成片准备已让路，停止播放后自动继续');
        scheduleResume();
      } else {
        state('成片稍后按需生成');
      }
    } finally {
      PREPARING = false;   // 备片不占导出锁，点导出永远不会被排队卡住
    }
  }

  /* 合成按钮完成后自动预生成；用户改稿后 1.2s 无输入也会增量更新 */
  var vb = document.getElementById('btnVoiceBuild');
  if (vb && typeof vb.onclick === 'function') {
    var origBuild = vb.onclick;
    vb.onclick = async function () {
      await origBuild.apply(this, arguments);
      /* 退到后台：合成完立即可播放，成片准备不挡着用户 */
      setTimeout(function () { try { prepare(); } catch (_) {} }, 30);
    };
  }
  function onCfgChange(ev) {
    if (ev && ev.target && ev.target.id === 'expFmt') return;  // 切换导出格式不触发后台备片
    if (PREP_TIMER) clearTimeout(PREP_TIMER);
    PREP_TIMER = setTimeout(function () {
      if (!BUSY && !PREPARING) { try { prepare(); } catch (_) {} }
    }, 1200);
  }
  document.addEventListener('input', onCfgChange, true);
  document.addEventListener('change', onCfgChange, true);

  /* ---------- 音频导出：与视频同源的混音 PCM，应用当前语速 / 音量 / 混声 ---------- */
  var AUDIO_READY = null, WEBM_READY = null;
  /* 后台备片与点击导出撞车时等待，不再静默丢弃这一次点击 */
  var PENDING_CLICK = null;
  async function waitIdle() {
    var g = 0, waited = false;
    while (BUSY && g++ < 600) {
      if (!waited) {
        waited = true;
        say('<b>后台正在按当前设置准备成片，完成后会自动继续导出…</b>');
        state('等待后台准备完成…');
      }
      await sleep(100);
    }
    if (BUSY) say('<b>后台准备超时，已按当前稿直接继续导出。</b>');
    return true;
  }
  function drainPending() {
    if (!PENDING_CLICK || BUSY) return;
    var f = PENDING_CLICK; PENDING_CLICK = null;
    setTimeout(function () { try { onExport(f); } catch (_) {} }, 30);
  }
  function curState() {
    return (window.__daily && window.__daily.get) ? window.__daily.get() : S;
  }
  function audioSig() {
    var st = curState();
    try {
      return readySig(segments()) + '|' + JSON.stringify([
        st.voice && st.voice.rate, st.voice && st.voice.volume,
        st.mix, st.bgm, boundarySig()
      ]);
    } catch (_) { return readySig(segments()) + '|x'; }
  }
  function mp3FromPCM(buf) {
    var L = window.lamejs;
    if (!L || !L.Mp3Encoder) return null;
    var sr = buf.sampleRate, ch = buf.getChannelData(0), n = ch.length;
    var enc = new L.Mp3Encoder(1, sr, 128), parts = [], off, i, b, f;
    for (off = 0; off < n; off += 1152) {
      var len = Math.min(1152, n - off), a = new Int16Array(1152);
      for (i = 0; i < len; i++) {
        var v = ch[off + i];
        a[i] = Math.max(-32768, Math.min(32767, v < 0 ? v * 32768 : v * 32767));
      }
      b = enc.encodeBuffer(a);
      if (b && b.length) parts.push(new Uint8Array(b));
    }
    f = enc.flush();
    if (f && f.length) parts.push(new Uint8Array(f));
    return parts.length ? new Blob(parts, { type: 'audio/mpeg' }) : null;
  }
  function mp3Fallback() {
    var parts = [], i;
    for (i = 0; i < VOICE_TRACK.length; i++) if (VOICE_TRACK[i]) parts.push(VOICE_TRACK[i].blob);
    return parts.length ? new Blob(parts, { type: 'audio/mpeg' }) : null;
  }
  async function audioPCM() {
    var sig = audioSig();
    if (AUDIO_READY && AUDIO_READY.sig === sig && MERGED_PCM) return AUDIO_READY.pcm;
    say('<b>正在按当前语速 / 音量准备人声音轨…</b>');
    await ensureVoice();
    AUDIO_READY = { sig: sig, pcm: MERGED_PCM };
    return AUDIO_READY.pcm;
  }
  async function exportAudio(fmt) {
    await waitIdle();
    BUSY = true; if (btn) btn.disabled = true;
    try {
      var pcm = await audioPCM(), blob, name, extra = '';
      if (fmt === 'mp3') {
        blob = mp3FromPCM(pcm);
        name = '今日要闻_人声.mp3';
        if (!blob) {
          blob = mp3Fallback();
          extra = '（MP3 编码模块未加载，已按原始人声流拼接；音量与配乐设置不生效）';
        }
        if (!blob) throw new Error('没有可导出的人声音频');
      } else {
        blob = (typeof window.encodeWav === 'function') ? window.encodeWav(pcm) : null;
        name = '今日要闻_人声音轨.wav';
        if (!blob) throw new Error('WAV 编码不可用');
      }
      dl(name, blob);
      var st = curState();
      say('<b>已下载 ' + (fmt === 'mp3' ? 'MP3' : 'WAV') + ' 人声音轨。</b>' +
        (pcm.duration || 0).toFixed(1) + ' 秒 · ' + (blob.size / 1048576).toFixed(2) +
        ' MiB · 语速 ' + ((st.voice && st.voice.rate) || 1).toFixed(2) + '× · 人声音量 ' +
        ((st.voice && st.voice.volume) || 1).toFixed(2) + ' · 与视频同源' + esc(extra) + '。');
      state('导出完成 · ' + fmt.toUpperCase() + ' · ' + (blob.size / 1048576).toFixed(2) + ' MiB');
    } catch (e) {
      say('<b>音频导出失败：</b>' + esc(String((e && e.message) || e)) + '。');
      state('导出失败');
    } finally {
      BUSY = false; if (btn) btn.disabled = false; drainPending();
    }
  }

  /* ---------- WebM 离线编码：VP9/VP8 + Opus，不按成片时长实时录制 ---------- */
  async function exportWebm() {
    await waitIdle();
    var seg = segments();
    /* 主按钮刚按当前稿备好 WebM 容器成片时直接复用，不再重复离线编码 */
    if (READY && READY.container === 'webm' && READY.sig === readyKey(seg, 'webm')) {
      dl(READY.name, READY.blob);
      say('<b>已下载当前内容的成片（WebM）。</b>' + READY.stats.segs.length + ' 句 · ' +
        READY.stats.dur.toFixed(1) + ' 秒 · 点导出 0 编码。');
      state('导出完成 · WebM · 直接下载已就绪成片');
      return;
    }
    if (WEBM_READY && WEBM_READY.sig === readySig(seg)) {
      dl(WEBM_READY.name, WEBM_READY.blob);
      say('<b>已下载当前内容的 WebM 成片。</b>' + WEBM_READY.stats.segs.length + ' 句 · ' +
        WEBM_READY.stats.dur.toFixed(1) + ' 秒 · 点导出 0 编码。');
      state('导出完成 · WebM · 直接下载已就绪成片');
      return;
    }
    BUSY = true; if (btn) btn.disabled = true;
    try {
      say('<b>正在按当前稿生成 WebM（离线编码 VP9 + Opus）…</b>');
      var c = await ensureVoice();
      var sr = MERGED_PCM.sampleRate || 48000;
      OFF = document.createElement('canvas'); OFF.width = 540; OFF.height = 960;
      var cfg = await pickWebmConfig(540, 960, sr);
      if (!cfg) throw new Error('当前浏览器不支持 VP9 / Opus 离线编码，请用桌面版 Chrome / Edge');
      var r = await build(c, cfg, sr, 'webm');
      WEBM_READY = r;
      dl(r.name, r.blob);
      say('<b>已下载当前内容的 WebM 成片。</b>' + r.stats.segs.length + ' 句 · ' +
        r.stats.dur.toFixed(1) + ' 秒 · ' + (r.blob.size / 1048576).toFixed(2) +
        ' MiB · ' + r.stats.frames + ' 帧 · 带人声与配乐。');
      state('导出完成 · WebM · ' + (r.blob.size / 1048576).toFixed(2) + ' MiB');
    } catch (e) {
      say('<b>WebM 导出失败：</b>' + esc(String((e && e.message) || e)) + '。');
      state('导出失败');
    } finally {
      BUSY = false; if (btn) btn.disabled = false; drainPending();
    }
  }

  /* ---------- 主流程：READY 命中时只下载，不合成、不编码 ---------- */
  async function exportMain() {
    await waitIdle();
    var seg0 = segments();
    if (READY && READY.sig === readyKey(seg0, READY.container || 'mp4')) {
      var r0 = READY;
      dl(r0.name, r0.blob);
      window.__voxExport = r0.stats;
      say('<b>已下载当前内容的成片 · ' + esc(modeLabel()) + '。</b>' + r0.stats.segs.length + ' 句 · ' +
        r0.stats.dur.toFixed(1) + ' 秒 · ' + (r0.blob.size / 1048576).toFixed(2) +
        ' MiB · <b>点导出到下载 0 编码、0 合成</b>，字幕与真人声共用同一播放头。');
      state('导出完成 · 直接下载已就绪成片');
      window.__voxExport.instant = true;
      return;
    }
    BUSY = true; if (btn) btn.disabled = true; EXPORTING = true;
    try {
      say('<b>正在按当前稿逐句合成人声…</b>');
      var c = await ensureVoice();
      var sr = MERGED_PCM.sampleRate || 48000;
      OFF = document.createElement('canvas'); OFF.width = 540; OFF.height = 960;
      var pick = await pickAny(540, 960, sr);

      if (!pick) {
        var mins = Math.ceil((MERGED_PCM ? MERGED_PCM.duration : 0) / 60);
        say('<b>当前浏览器不支持离线编码，转为本地录制（按成片时长走，约 ' + mins + ' 分钟，请不要关闭页面）…</b>');
        var f = await fallbackRecord(c);
        dl('今日要闻_竖版9比16.' + f.ext, f.blob);
        say('<b>已下载当前内容的成片。</b>本次按成片时长实时录制。');
        return;
      }

      say('<b>正在离线编码当前稿（' + seg0.length + ' 句）…</b>首次编码需要一点时间，进度会在本条提示里更新。');
      var r = await build(c, pick.cfg, sr, pick.container);
      r.sig = readyKey(seg0, pick.container);
      r.container = pick.container;
      READY = r;
      window.__voxExport.instant = false;
      dl(r.name, r.blob);
      var tail = pick.container === 'webm'
        ? ' MiB · 当前浏览器无 H.264/AAC，已自动输出 WebM（VP9/Opus）离线编码 · 复用 '
        : ' MiB · 每句一张静态画面（' + r.stats.frames + ' 帧，固定帧率需 ' +
          r.stats.fixedFramesWouldBe + ' 帧） · 复用 ';
      say('<b>已下载当前内容的成片 · ' + esc(modeLabel()) + '。</b>' + r.stats.segs.length + ' 句 · ' +
        r.stats.dur.toFixed(1) + ' 秒 · ' + (r.blob.size / 1048576).toFixed(2) + tail +
        r.stats.reused + ' 句 / 新编 ' + r.stats.fresh + ' 句 · 拼接 ' + r.joinMs + ' ms。');
      state('导出完成 · ' + r.stats.frames + ' 帧 · 当前稿 ' + r.stats.segs.length + ' 句');
    } catch (e) {
      var msg = String((e && e.message) || e);
      var hint = /fetch|Network|HTTP|Failed/i.test(msg)
        ? '在线人声接口不可达：检查网络后重试（页面直接请求 TTS，不经 Worker、不用登录）。' : '';
      say('<b>导出失败：</b>' + esc(msg) + '。' + esc(hint) + '不会下载旧成品。');
      state('导出失败');
    } finally {
      BUSY = false; EXPORTING = false; if (btn) btn.disabled = false; drainPending();
    }
  }

  /* ---------- 导出格式分流：非 MP4 一律不碰人声、不生成视频 ---------- */
  function currentFormat() {
    try {
      var d = window.__daily || {}, api = d.api || {};
      if (typeof api.exportFormat === 'function') return api.exportFormat();
      if (typeof d.exportFormat === 'function') return d.exportFormat();
      if (typeof exportFormat === 'function') return exportFormat();
    } catch (_) {}
    return 'mp4';
  }
  function otherFormat(f) {
    var d = window.__daily || {}, api = d.api || {};
    /* 页面把多格式导出挂在 __daily 根上，不在 api 子对象里；两处都试。 */
    var fn = (typeof api.exportByFormat === 'function') ? api.exportByFormat
           : (typeof d.exportByFormat === 'function' ? d.exportByFormat : null);
    if (fn) { try { fn(f); return true; } catch (_) {} }
    var ids = { webm: 'btnRecordWebM', mp3: 'btnExpMp3', wav: 'btnExportVoiceTrack',
      srt: 'btnExpSrt', png_cover: 'btnExportCoverPng', png_card: 'btnExportCardPng',
      md: 'btnExportScript', json_manifest: 'btnExportManifest', json_source: 'btnExportSrc' };
    if (f === 'ass' && typeof d.buildAss === 'function' && typeof api.download === 'function') {
      api.download('今日要闻_字幕.ass', d.buildAss(), 'text/plain;charset=utf-8'); return true;
    }
    var b = ids[f] && document.getElementById(ids[f]);
    if (b) { b.click(); return true; }
    return false;
  }
  function onExport(forced) {
    var f = (typeof forced === 'string' && forced) ? forced : currentFormat();
    if (BUSY) {
      PENDING_CLICK = f;
      say('<b>后台正在准备成片，已记下这次导出，准备完成后自动下载。</b>');
      state('已排队 · 准备完成后自动导出');
      return;
    }
    if (f === 'mp3' || f === 'wav') return exportAudio(f);
    if (f === 'webm') return exportWebm();
    if (f !== 'mp4') {
      if (otherFormat(f)) {
        say('<b>已按「' + esc(f) + '」格式导出。</b>该格式不合成人声、不生成视频。');
        state('导出完成 · ' + f + ' · 未合成人声');
        return;
      }
      say('<b>导出格式 ' + esc(f) + ' 暂不可用。</b>请改回「视频 · MP4」。');
      state('导出未完成 · ' + f + ' 不可用');
      return;
    }
    return exportMain();
  }

  /* 唯一出口：原来的「导出视频 MP4」按钮，不新增任何控件 */
  if (btn) btn.onclick = onExport;
  window.__exportVideo = exportMain;
  window.__voxPrepare = prepare;
  window.__voxExportAudio = exportAudio;
  window.__voxExportWebm = exportWebm;
  window.__voxClearCache = function () {
    CACHE = Object.create(null); MERGED_PCM = null; BGM_DECODE_CACHE = Object.create(null);
    WEBM_READY = null; AUDIO_READY = null;
  };

  function hide(el) { if (el && el.parentElement) el.parentElement.style.display = 'none'; }
  hide(document.getElementById('btnRecordWebM'));
  hide(document.getElementById('btnGhSetup'));
  hide(document.getElementById('subOffset'));
  var panel = document.getElementById('ghSetup'); if (panel) panel.style.display = 'none';
  if (note) note.className = 'okbar';
  say('<b>导入数据 → 逐句合成人声 → 播放即时顺畅 → 点导出立即下载。</b>成片在合成后台备好、播放期间自动让路；导出不再编码；整句字幕与真人声共用同一播放头，每句一张静态画面；不登录、不写回、不排队、不依赖旧成品。');
  try {
    S.tab = 'sec-export'; if (typeof applyTab === 'function') applyTab();
    if (typeof save === 'function') save();
    setTimeout(function () { try { S.tab = 'sec-export'; if (typeof applyTab === 'function') applyTab(); } catch (_) {} }, 700);
  } catch (_) {}
})();
