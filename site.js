;(function(){
"use strict";
var d=document, root=d.documentElement;
var reduced=false;
try{ reduced=window.matchMedia("(prefers-reduced-motion: reduce)").matches; }catch(e){}
root.classList.add("js");

/* theme toggle: pill switch (sun/moon knob) + View Transition cross-fade,
 * persisted, respects prefers-color-scheme */
var btn=d.getElementById("theme-toggle");
if(btn){
  var knob=btn.querySelector(".knob");
  var syncBtn=function(){
    var dark=root.getAttribute("data-theme")==="dark";
    if(knob){
      btn.setAttribute("aria-pressed",dark?"true":"false");
      btn.setAttribute("aria-label",dark?"Switch to light mode":"Switch to dark mode");
    }
  };
  syncBtn();
  btn.addEventListener("click",function(){
    var next=root.getAttribute("data-theme")==="light"?"dark":"light";
    var apply=function(){ root.setAttribute("data-theme",next); syncBtn(); };
    try{ if(d.startViewTransition&&!reduced){ d.startViewTransition(apply); } else { apply(); } }catch(e){ apply(); }
    try{ localStorage.setItem("mcphi-theme",next); }catch(e){}
  });
}

function onView(el,fn){
  if(reduced||!("IntersectionObserver" in window)){ fn(); return; }
  var io=new IntersectionObserver(function(es){
    if(es[0].isIntersecting){ io.disconnect(); fn(); }
  },{threshold:.35});
  io.observe(el);
}

/* hero donut: sweep stroke-dashoffset once, then rest (CSS drives the values) */
var donut=d.querySelector(".donut");
if(donut){ onView(donut,function(){ donut.classList.add("live"); });
  setTimeout(function(){ donut.classList.add("live"); },4000); /* safety net */ }

/* scroll reveal: fade+rise 12px, 500ms, staggered 60ms per batch */
var rev=[].slice.call(d.querySelectorAll("[data-reveal]"));
if("IntersectionObserver" in window && !reduced){
  var io2=new IntersectionObserver(function(es){
    var n=0;
    es.forEach(function(en){
      if(en.isIntersecting){
        en.target.style.transitionDelay=(n++*60)+"ms";
        en.target.classList.add("in");
        io2.unobserve(en.target);
      }
    });
  },{rootMargin:"0px 0px -6% 0px",threshold:0});
  rev.forEach(function(el){ io2.observe(el); });
}else{ rev.forEach(function(el){ el.classList.add("in"); }); }

/* stat counters: count up once in view (final value already in the HTML) */
[].slice.call(d.querySelectorAll("[data-count]")).forEach(function(el){
  onView(el,function(){
    var end=parseFloat(el.getAttribute("data-count")), suf=el.getAttribute("data-suffix")||"";
    if(reduced||isNaN(end)){ return; }
    var t0=null, dur=900;
    function step(ts){
      if(t0===null){ t0=ts; }
      var p=Math.min((ts-t0)/dur,1);
      el.textContent=Math.round(end*(1-Math.pow(1-p,3))).toLocaleString("en-US")+suf;
      if(p<1){ requestAnimationFrame(step); }
    }
    requestAnimationFrame(step);
  });
});

/* table sort (mono columns carry data-v for stable numeric compare) */
[].slice.call(d.querySelectorAll("#tbl th[data-sort]")).forEach(function(th){
  th.addEventListener("click",function(){
    var tb=document.querySelector("#tbl tbody"), rows=[].slice.call(tb.rows), i=th.cellIndex,
        num=th.getAttribute("data-sort")==="num";
    th.asc=!th.asc;
    [].slice.call(th.parentNode.children).forEach(function(h){ h.classList.remove("asc","desc"); });
    th.classList.add(th.asc?"asc":"desc");
    rows.sort(function(a,b){
      var A=a.cells[i].getAttribute("data-v"), B=b.cells[i].getAttribute("data-v");
      if(num){ A=parseFloat(A); B=parseFloat(B); return (A>B)-(A<B); }
      return String(A).localeCompare(String(B));
    });
    if(!th.asc){ rows.reverse(); }
    rows.forEach(function(r){ tb.appendChild(r); });
  });
});
})();