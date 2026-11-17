'use strict';
const dualRanges = [["全部",0],["近10年",120],["近5年",60],["近3年",36],["近1年",12],["近6个月",6],["近3个月",3],["近1个月",1]];
const dualSlider=el("dual-slider");
let dualData, dualMonths=0, dualMode="return", dualSelection, dualRows=[];

el("dual-ranges").innerHTML=dualRanges.map(([title,months])=>
  '<button type="button" data-months="'+months+'" aria-pressed="'+(months===0)+'" class="'+(months===0?"active":"")+'">'+title+'</button>').join("");
el("dual-panels").innerHTML=shadowBooks.map(book=>'<article class="dual-panel" style="--book:'+book.color+'" id="history-'+book.id+'">'+
  '<h3>'+book.title+'动量看板 <small>'+book.code+'</small></h3><p class="dual-score-date caption"></p><dl class="dual-numbers"></dl>'+
  '<svg class="dual-chart" role="img" aria-label="'+book.title+'的区间净值表现"></svg><p class="day-info dual-point"></p>'+
  '<details class="dual-rank"><summary>最新动量排名</summary><ol></ol></details></article>').join("");

function monthsBefore(iso,months){
  const [y,m,d]=iso.split("-").map(Number);
  const first=new Date(Date.UTC(y,m-1-months,1));
  const last=new Date(Date.UTC(first.getUTCFullYear(),first.getUTCMonth()+1,0)).getUTCDate();
  first.setUTCDate(Math.min(d,last));
  return first.toISOString().slice(0,10);
}

function dualRange(){
  const full=dualData.series;
  const cutoff=dualMonths?monthsBefore(dualData.as_of,dualMonths):full[0][0];
  const first=full.findIndex(row=>row[0]>=cutoff);
  const anchor=first>0?full[first-1]:[null,1,1];
  const base=anchor.slice(1,3), peak=base.slice(), previous=base.slice();
  return full.slice(first).map(([date,a,b,h1,h2])=>{
    const books=[a,b].map((nav,i)=>{
      peak[i]=Math.max(peak[i],nav);
      const result={return:nav/base[i]-1, drawdown:nav/peak[i]-1, daily:nav/previous[i]-1, holding:i?h2:h1};
      previous[i]=nav;
      return result;
    });
    return {date,books};
  });
}

function drawDual(){
  if(!dualRows.length)return;
  const first=dualRows[0].date,last=dualRows.at(-1).date;
  const current=dualRows[Number(dualSlider.value)];
  const all=dualRows.flatMap(row=>row.books.map(book=>book[dualMode]));
  const low=Math.min(0,...all),high=Math.max(0,...all);
  const span=Math.max(high-low,.002), top=high+span*.1, bottom=low-span*.1;
  shadowBooks.forEach((book,i)=>{
    const panel=el("history-"+book.id), svg=panel.querySelector("svg");
    const width=svg.clientWidth||300, height=svg.clientHeight||172;
    svg.setAttribute("viewBox","0 0 "+width+" "+height);
    const left=width<350?51:63,right=width-9;
    const x=j=>left+(right-left)*j/Math.max(1,dualRows.length-1);
    const y=v=>10+(height-36)*(top-v)/(top-bottom);
    const ticks=high===low?[0]:[high,(high+low)/2,low];
    const grid=ticks.map(v=>'<line x1="'+left+'" x2="'+right+'" y1="'+y(v)+'" y2="'+y(v)+'"/>'+
      '<text x="'+(left-5)+'" y="'+y(v)+'" text-anchor="end" dy=".32em">'+percent(v,Math.abs(v)>=.5?0:1)+'</text>').join("");
    const line=dualRows.map((row,j)=>x(j).toFixed(1)+","+y(row.books[i][dualMode]).toFixed(1)).join(" ");
    const point=x(Number(dualSlider.value));
    svg.innerHTML=grid+'<polyline points="'+line+'"/><line class="dual-guide" x1="'+point+'" x2="'+point+
      '" y1="7" y2="'+(height-24)+'"/><circle cx="'+point+'" cy="'+y(current.books[i][dualMode])+'" r="4"/>'+
      '<text x="'+left+'" y="'+(height-6)+'">'+first+'</text><text x="'+right+'" y="'+(height-6)+'" text-anchor="end">'+last+'</text>';
    const b=current.books[i];
    panel.querySelector(".dual-point").innerHTML='<b>'+current.date+'</b><span>收盘持仓 '+shadowAsset(b.holding)+
      '</span><span>日收益 '+valueHTML(b.daily)+'</span><span>区间收益 '+valueHTML(b.return)+'</span><span>回撤 '+valueHTML(b.drawdown)+'</span>';
  });
}

function renderDual(){
  dualRows=dualRange();
  const last=dualRows.at(-1);
  const selected=dualRows.findIndex(row=>row.date===dualSelection);
  dualSlider.max=dualRows.length-1;
  dualSlider.value=selected===-1?dualRows.length-1:selected;
  el("dual-slider-wrap").hidden=false;
  el("dual-window").textContent=dualRows[0].date+" 至 "+last.date+" · "+dualRows.length+" 个交易日";
  const status=el("dual-status");
  status.textContent=dualData.stale?"等待双源更新":"结算至 "+dualData.as_of;
  status.classList.toggle("warn",dualData.stale);
  const error=el("dual-error");
  error.hidden=!dualData.stale;
  error.textContent="显示最后一组共同核对的历史净值。新一日数据齐全后自动续算。";
  shadowBooks.forEach((book,i)=>{
    const panel=el("history-"+book.id), ranking=dualData.ranks[book.id];
    panel.querySelector(".dual-score-date").textContent="最新动量 "+dualData.rank_as_of+" · "+
      (ranking.length===4?"第一名 "+shadowAsset(ranking[0].symbol):"等待四只 ETF 行情");
    panel.querySelector(".dual-numbers").innerHTML=[
      ["区间累计收益",last.books[i].return],["区间最大回撤",Math.min(...dualRows.map(r=>r.books[i].drawdown))]
    ].map(([key,value])=>'<div class="metric"><dt>'+key+'</dt><dd>'+valueHTML(value)+'</dd></div>').join("");
    panel.querySelector("ol").innerHTML=ranking.map(a=>'<li><span>'+shadowAsset(a.symbol)+'</span><span>'+percent(a.score)+'</span></li>').join("");
  });
  drawDual();
}

function dualSelect(index){
  dualSlider.value=index;
  dualSelection=dualRows[index].date;
  drawDual();
}

function activeChoice(group,attr,value){
  for(const b of el(group).querySelectorAll("button")){
    const active=b.dataset[attr]===String(value);
    b.classList.toggle("active",active);
    b.setAttribute("aria-pressed",String(active));
  }
}

el("dual-ranges").onclick=e=>{
  if(!dualData||e.target.dataset.months==null)return;
  dualMonths=Number(e.target.dataset.months); dualSelection=null;
  activeChoice("dual-ranges","months",dualMonths);
  renderDual();
};
el("dual-mode").onclick=e=>{
  if(!dualData||!e.target.dataset.mode)return;
  dualMode=e.target.dataset.mode;
  activeChoice("dual-mode","mode",dualMode);
  drawDual();
};
dualSlider.oninput=()=>dualSelect(Number(dualSlider.value));
const dualResize=new ResizeObserver(drawDual);
for(const svg of document.querySelectorAll(".dual-chart")){
  svg.addEventListener("pointerdown",e=>{
    if(!dualRows.length)return;
    const box=svg.getBoundingClientRect(), left=svg.clientWidth<350?51:63;
    const i=Math.round((e.clientX-box.left-left)/(box.width-left-9)*(dualRows.length-1));
    dualSelect(Math.max(0,Math.min(dualRows.length-1,i)));
  });
  dualResize.observe(svg);
}

async function refreshDual(){
  try{
    const response=await fetch("/api/dual-history",{cache:"no-store"});
    if(!response.ok)throw Error();
    dualData=await response.json();
    renderDual();
    setDividendCalendar(dualData);
    renderDividend(dualData.dividend_report);
    renderDividendTrades(dualData.dividend_trades);
  }catch{
    el("dual-status").textContent="暂时无法核对";
    el("dual-status").classList.add("warn");
    el("dual-error").hidden=false;
    el("dual-error").textContent=dualData?"继续显示上次读取的历史数据，稍后重试。":"历史数据暂未加载，稍后重试。";
  }
}
document.addEventListener("DOMContentLoaded",refreshDual);
document.addEventListener("visibilitychange",()=>{if(!document.hidden)refreshDual();});
setInterval(refreshDual,60*1000);
