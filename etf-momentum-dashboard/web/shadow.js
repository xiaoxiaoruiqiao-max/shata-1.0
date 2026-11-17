'use strict';

const shadowBooks = [
  {id:"original", title:"上证180版", code:"510180", color:"#3b54a1"},
  {id:"dividend", title:"红利版", code:"510880", color:"#087058"}
];
const shadowAsset = s => s==="sh510880"?"红利":label(s);
const shadowMode = el("shadow-mode"), shadowSlider=el("shadow-day");
let shadowData, shadowMetric="ret";

function shadowChart(last=false){
  if(!shadowData)return;
  const series=shadowData.series;
  el("shadow-empty").hidden=series.length>0;
  el("shadow-chart-data").hidden=!series.length;
  if(!series.length)return;
  const lastIndex=series.length-1;
  shadowSlider.max=lastIndex;
  if(last)shadowSlider.value=lastIndex;
  shadowSlider.disabled=!lastIndex;
  el("shadow-range").textContent=series[0][0]+" 至 "+series.at(-1)[0];
  const row=series[Number(shadowSlider.value)];
  const col=shadowMetric==="ret"?1:2;
  const values=series.flatMap(r=>[r[col],r[col+2]]);
  const low=Math.min(0,...values),high=Math.max(0,...values);
  const pad=Math.max((high-low)*.09,.002);
  const xpos=i=>73+491*i/Math.max(lastIndex,1);
  const ypos=v=>190-170*(v-low+pad)/(high-low+2*pad);
  const x=xpos(Number(shadowSlider.value));
  const ticks=high===low?[0]:[high,(high+low)/2,low];
  const axes=ticks.map(v=>'<line x1="73" x2="565" y1="'+ypos(v)+'" y2="'+ypos(v)+
    '" stroke="#dbe4dd"/><text x="63" y="'+ypos(v)+'" dy=".35em" text-anchor="end">'+percent(v)+'</text>').join("");
  const curves=shadowBooks.map(({color},i)=>{
    const index=col+i*2;
    const points=series.map((r,j)=>xpos(j)+","+ypos(r[index])).join(" ");
    return '<polyline fill="none" stroke="'+color+'" stroke-width="2.5" points="'+points+'"/>'+
      '<circle cx="'+x+'" cy="'+ypos(row[index])+'" r="5" fill="'+color+'" stroke="#fff" stroke-width="2"/>';
  }).join("");
  el("shadow-lines").innerHTML=axes+'<line x1="'+x+'" x2="'+x+'" y1="17" y2="189" stroke="#8fa099" stroke-dasharray="3 4"/>'+
    curves+'<text x="73" y="210">'+series[0][0]+'</text>'+
    (lastIndex?'<text x="565" y="210" text-anchor="end">'+series.at(-1)[0]+'</text>':"");
  el("shadow-values").innerHTML='<b>'+row[0]+'</b>'+shadowBooks.map((b,i)=>
    '<span style="color:'+b.color+'">'+b.title+' '+percent(row[col+i*2])+'</span>').join("");
}

function renderShadow(info){
  const latest=!shadowData || shadowData.series.length===0 || Number(shadowSlider.value)===shadowData.series.length-1;
  shadowData=info;
  const status=el("shadow-status");
  status.textContent=info.session_note?"今日错过采集":info.error?"行情待核对":info.pending_frames?(info.capture_open?"采集盘口中":"采集结束，待结算"):info.latest_day?"已结算 "+info.latest_day:
    info.start?"等待首日结算":"等待启动";
  status.classList.toggle("warn",!!info.error||!!info.session_note);
  const timing=["信号基于 "+info.as_of+" 收盘"];
  if(info.start)timing.push("模拟起始 "+info.start);
  if(info.next_session)timing.push("信号适用 "+info.next_session+" 09:35");
  if(info.checked_at)timing.push("最近核对 "+stamp(info.checked_at));
  if(info.error)timing.push(info.error);
  if(info.session_note)timing.push(info.session_note);
  el("shadow-update").textContent=timing.join(" · ");
  el("shadow-cards").innerHTML=shadowBooks.map(b=>{
    const daily=info.latest[b.id], ranking=info.ranks[b.id];
    const target=ranking.length===4 ? shadowAsset(ranking[0].symbol):"待核对";
    const state=daily?.stopped?("止线 "+daily.stopped+(daily.holding?" · 待退出":" · 已退出")):"收盘持仓 "+(daily?shadowAsset(daily.holding):"空仓");
    return '<article class="shadow-book" style="--book:'+b.color+'"><h3>'+b.title+
      ' <small>'+b.code+'</small></h3><span class="shadow-caption">'+(info.next_session?"下一交易日排名第一":"最近已核对排名第一")+
      '</span><strong class="shadow-signal">'+target+'</strong><p class="shadow-caption">'+state+'</p>'+
      '<dl>'+[["累计收益",daily?.ret],["当前回撤",daily?.dd],["最大回撤",daily?.mdd]].map(([k,v])=>
        '<div><dt>'+k+'</dt><dd>'+valueHTML(v)+'</dd></div>').join("")+'</dl>'+
      '<ul aria-label="动量排名">'+ranking.map(a=>'<li><span>'+shadowAsset(a.symbol)+'</span><span>'+percent(a.score)+'</span></li>').join("")+
      '</ul></article>';
  }).join("");
  el("shadow-log").innerHTML=info.recent.length?info.recent.map(r=>
    '<article><b>'+r.date+'</b>'+shadowBooks.map(b=>{
      const box=r[b.id];
      return '<p><strong>'+b.title+'</strong> '+shadowAsset(box.holding)+' · '+percent(box.daily_ret)+' · '+box.note+'</p>'+
        (box.filled.length?'<ul>'+box.filled.map(f=>'<li>'+f.minute+' '+(f.side==="buy"?"买":"卖")+' '+shadowAsset(f.symbol)+' '+
          number.format(f.qty)+' 份 @ '+f.price.toFixed(3)+(f.iopv?' · IOPV '+f.iopv.toFixed(4):'')+'</li>').join("")+'</ul>':"");
    }).join("")+'</article>').join(""):'<p class="caption">尚无前瞻模拟交易日。</p>';
  shadowChart(latest);
}

async function refreshShadow(){
  try {
    const response=await fetch("/api/shadow",{cache:"no-store"});
    if(!response.ok)throw Error();
    renderShadow(await response.json());
  } catch {
    el("shadow-status").textContent="并行数据暂不可用";
    el("shadow-status").classList.add("warn");
  }
}
shadowMode.addEventListener("click",event=>{
  if(!event.target.dataset.which)return;
  shadowMetric=event.target.dataset.which;
  for(const button of shadowMode.querySelectorAll("button")){
    button.classList.toggle("active",button===event.target);
    button.setAttribute("aria-pressed",String(button===event.target));
  }
  shadowChart();
});
shadowSlider.addEventListener("input",()=>shadowChart());
document.addEventListener("DOMContentLoaded",refreshShadow);
document.addEventListener("visibilitychange",()=>{if(!document.hidden)refreshShadow();});
setInterval(refreshShadow,60*1000);
