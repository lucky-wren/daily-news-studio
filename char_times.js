/* 字幕逐字：字表由真实句长推出，播放、预览、导出共用同一个音频播放头。 */
(function(){
 var C={};
 function clamp(v){return Math.max(0,Math.min(1,+v||0))}
 C.ensure=function(seg,track,subs){
  var out=[];
  for(var i=0;i<(seg||[]).length;i++){
   var t=track&&track[i],dur=Math.max(.2,+(t&&t.dur)||2);
   var sub=String((subs&&subs[i])||seg[i]||'');
   out.push({dur:dur,n:Array.from(String(seg[i]||'')).length,
    sub_n:Array.from(sub).length,source:'audio-clock'});
  }
  window.CHAR_TIMES=out;window.CHAR_TIMES_META={source:'audio-clock'};
  return Promise.resolve();
 };
 C.countAt=function(ct,t){return ct?Math.floor(clamp(t/ct.dur)*ct.n):0};
 C.subCountAt=function(ct,t){return ct?Math.floor(clamp(t/ct.dur)*ct.sub_n):0};
 C.subRatioAt=function(ct,t){return ct?clamp(t/ct.dur):1};
 C.events=function(ct,subMode){
  var n=Math.max(1,Math.min(160,ct?(subMode?ct.sub_n:ct.n):1)),dur=ct?ct.dur:2,out=[0],i;
  for(i=1;i<=n;i++)out.push(dur*i/n);
  return out;
 };
 window.CharTimes=C;
})();
