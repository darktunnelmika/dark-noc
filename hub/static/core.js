/* DARK NOC stable frontend core utilities. Keep dependency-free and side-effect free. */
(() => {
  'use strict';
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const bytesPerSecond = value => {
    const n=Number(value||0);
    if(n>=1e9)return `${(n/1e9).toFixed(2)} Gb/s`;
    if(n>=1e6)return `${(n/1e6).toFixed(0)} Mb/s`;
    if(n>=1e3)return `${(n/1e3).toFixed(0)} Kb/s`;
    return `${n.toFixed(0)} b/s`;
  };
  const fileSize = value => {
    const units=['B','KB','MB','GB','TB'];let size=Number(value||0),index=0;
    while(size>=1024&&index<units.length-1){size/=1024;index+=1;}
    return `${size.toFixed(index?2:0)} ${units[index]}`;
  };
  const displayHost = value => String(value||'').replace(/^::ffff:/,'')||'IP UNAVAILABLE';
  const relativeTime = timestamp => {
    if(!timestamp)return 'never';const seconds=Math.max(0,Math.floor(Date.now()/1000-timestamp));
    if(seconds<10)return 'now';if(seconds<60)return `${seconds}s ago`;if(seconds<3600)return `${Math.floor(seconds/60)}m ago`;return `${Math.floor(seconds/3600)}h ago`;
  };
  const duration = timestamp => {const total=Math.max(0,Math.floor(Date.now()/1000-Number(timestamp||Date.now()/1000))),hours=Math.floor(total/3600),minutes=Math.floor(total%3600/60),seconds=total%60;return [hours,minutes,seconds].map(value=>String(value).padStart(2,'0')).join(':');};
  const elapsedDuration = totalSeconds => {const total=Math.max(0,Math.floor(Number(totalSeconds||0))),hours=Math.floor(total/3600),minutes=Math.floor(total%3600/60),seconds=total%60;return [hours,minutes,seconds].map(value=>String(value).padStart(2,'0')).join(':');};
  window.DarkNocCore=Object.freeze({$, $$, esc, bytesPerSecond, fileSize, displayHost, relativeTime, duration, elapsedDuration});
})();
