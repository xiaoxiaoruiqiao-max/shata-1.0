'use strict';
const el = id => document.getElementById(id);
const assets = {
  sh518880: ["黄金", "518880", "gold"], sh513100: ["纳指100", "513100", "nasdaq"],
  sz159915: ["创业板", "159915", "chinext"], sh510180: ["上证180", "510180", "sse"],
};
const ranges = [["全部",0],["近5年",5],["近3年",3],["近1年",1],["今年",-1]];
const percent = (x,digits=2) => x == null ? "—" : (x > 0 ? "+" : x < 0 ? "−" : "") +
  (Math.abs(x)*100).toLocaleString("zh-CN",{minimumFractionDigits:digits,maximumFractionDigits:digits})+"%";
const money = new Intl.NumberFormat("zh-CN",{maximumFractionDigits:0});
const tone = x => x > 0 ? "positive" : x < 0 ? "negative" : "";
const valueHTML = x => '<span class="number '+tone(x)+'">'+percent(x)+'</span>';
const label = symbol => assets[symbol]?.[0] ?? "空仓";
const assetHTML = symbol => assets[symbol] ?
  '<span class="'+assets[symbol][2]+'"><i class="dot"></i>'+label(symbol)+'</span>' : label(symbol);
const stamp = iso => iso ? new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",
  month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date(iso)) : "尚未自动核对";
let data, chartRows, selection = null, range = 0, calendarMonth, daySelected, reportYear, page = 0, liveView = false;
const series = () => liveView ? data.pilot : data;
const pageSize = 12;
const canvas = el("chart");
const ctx = canvas.getContext("2d");
let plot;
function chartData(reset=false) {
  const selectedDate=reset?null:chartRows?.[selection]?.[0];
  const report=series();
  const empty=!report.daily.length;
  for(const id of ["chart-wrap","ranges","chart-footer"])el(id).hidden=empty;
  el("chart-empty").hidden=!empty;
  if(empty){chartRows=[];plot=null;return;}
  const rows = [[report.start,1,1,0,0], ...report.daily];
  const year = Number(report.as_of.slice(0,4));
  const threshold = range === -1 ? year+"-01-01" : (year-range)+report.as_of.slice(4);
  const start = range === 0 ? 0 : Math.max(0, rows.findIndex(r=>r[0]>=threshold)-1);
  chartRows = rows.slice(start);
  el("ranges").innerHTML = ranges.map(([name,value])=>'<button data-range="'+value+'" class="'+
    (value===range?"active":"")+'" aria-pressed="'+(value===range)+'">'+name+'</button>').join("");
  const selectedIndex=selectedDate?chartRows.findLastIndex(row=>row[0]===selectedDate):-1;
  selection=selectedIndex<0?null:selectedIndex;
  draw();
}
function draw() { plot=drawNav(canvas,ctx,chartRows,selection,range,el); }
function drawNav(canvas, ctx, chartRows, selection, range, lookup) {
  let plot;
  if (!chartRows?.length) return;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (!width || !height) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = width*ratio; canvas.height = height*ratio;
  ctx.scale(ratio,ratio);
  const log = lookup("log-scale").checked;
  const map = log ? Math.log : x=>x;
  const left=width<430?38:47, right=width-7, top=11, bottom=height-25;
  const vals=chartRows.flatMap(r=>[map(r[1]),map(r[2])]);
  const low=log ? Math.min(...vals)-.07 : 0;
  const hi=Math.max(...vals)+(log?.08:Math.max(...vals)*.05);
  const n=chartRows.length-1 || 1;
  const xpos=i=>left+(right-left)*i/n, ypos=v=>bottom-(map(v)-low)/(hi-low)*(bottom-top);
  plot={left,right,n,xpos,ypos};
  ctx.font="10px Inter, sans-serif"; ctx.textBaseline="middle";
  for(let i=0;i<5;i++){
    const y=bottom-(bottom-top)*i/4;
    const val=low+(hi-low)*i/4;
    ctx.strokeStyle="#e6eee7"; ctx.lineWidth=1; ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();
    ctx.textAlign="right";ctx.fillStyle="#647a70";ctx.fillText((log?Math.exp(val):val).toFixed(1),left-7,y);
  }
  const ticks=width<500?4:6;
  for(let i=0;i<ticks;i++){
    const index=Math.round(i*n/(ticks-1)), x=xpos(index);
    const day=chartRows[index][0];
    ctx.textAlign=i===0?"left":i===ticks-1?"right":"center";
    ctx.fillText(range===0 ? day.slice(0,4):day.slice(0,7),x,bottom+15);
  }
  const trace=(column,color,line)=>{
    ctx.beginPath();ctx.strokeStyle=color;ctx.lineJoin="round";ctx.lineWidth=line;
    chartRows.forEach((r,i)=>{const x=xpos(i),y=ypos(r[column]);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});
    ctx.stroke();
  };
  ctx.beginPath();
  chartRows.forEach((r,i)=>{const x=xpos(i),y=ypos(r[1]);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});
  ctx.lineTo(right,bottom);ctx.lineTo(left,bottom);ctx.closePath();
  const fill = ctx.createLinearGradient(0,top,0,bottom);fill.addColorStop(0,"#35aa842e");fill.addColorStop(1,"#35aa8403");ctx.fillStyle=fill;ctx.fill();
  trace(2,"#879d95",1.1);trace(1,"#067758",2);
  const tip=lookup("chart-tip");
  if(selection==null){tip.hidden=true;return plot;}
  const row=chartRows[selection],x=xpos(selection),y=ypos(row[1]);
  ctx.strokeStyle="#537a6d";ctx.lineWidth=1;ctx.setLineDash([4,3]);ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle="#067758";ctx.beginPath();ctx.arc(x,y,3.5,0,2*Math.PI);ctx.fill();
  tip.innerHTML=row[0]+"<br>"+lookup("strategy-key").textContent+" "+row[1].toFixed(3)+" · "+lookup("benchmark-key").textContent+" "+row[2].toFixed(3)+"<br>当日 "+percent(row[3])+" · 回撤 "+percent(row[4]);
  canvas.setAttribute("aria-label","净值图，"+row[0]+"，轮动 "+row[1].toFixed(3)+"，当日 "+percent(row[3]));
  tip.hidden=false;tip.style.left=Math.max(0,Math.min(width-tip.offsetWidth,x+8))+"px";
  return plot;
}
function signal() {
  el("signal-date").textContent = "来自 "+data.as_of+" 收盘";
  const fresh = data.status === "ok";
  const stale = !data.signal_ready;
  if (data.next_session && !stale) el("signal-date").textContent += " · "+data.next_session+" 09:35";
  const status = el("status");
  status.textContent = stale ? "行情待核对" : fresh ? "行情已核对" : "历史行情";
  status.classList.toggle("warn",stale || !fresh);
  const warning=el("warning");
  warning.hidden=!stale;
  if(stale)warning.textContent="当前显示 "+data.as_of+" 的历史行情。"+(data.error||"下一次可执行信号尚未确认。")+" 暂停新买入。";
  el("signal").innerHTML = '<div class="signal"><span>'+(stale?"上次排名第一":"动量排名第一")+
    '</span><strong>'+assetHTML(data.target)+'</strong><small>'+assets[data.target][1]+' · 实盘 '+label(data.pilot.holding)+"</small></div>";
  const sorted=data.assets.slice().sort((a,b)=>b.score-a.score);
  el("ranking").innerHTML = '<div class="rank-head"><span>ETF</span><span>动量分数</span><span>近25日</span></div>'+
    sorted.map(a=>'<div class="rank"><span>'+assetHTML(a.symbol)+'</span><span>'+valueHTML(a.score)+'</span><span>'+valueHTML(a.ret25)+'</span></div>').join("");
  const notes=[];
  if(!data.next_session && !stale)notes.push("下一交易日日期待交易所日历确认。");
  if(sorted[0].score<0)notes.push("四只得分均为负，原规则仍选最高分。");
  if(data.premium) notes.push("纳指 "+data.premium.date+" 收盘估算溢价 "+percent(data.premium.value)+"，交易前须用当时报价复核 3.00% 门槛。");
  el("signal-note").textContent=notes.join(" ");
  el("updated").textContent="最近核对 "+stamp(data.checked_at)+"（北京时间） · 18:15 收盘采集，18:35、19:05、08:15、08:45 补采";
}
const monthCode = (year,month)=>year+"-"+String(month).padStart(2,"0");
const calendarAsset = symbol=>symbol==="sh510880"?"红利":label(symbol);
const calendarColor = symbol=>symbol==="sh510880"?"reddiv":assets[symbol]?.[2]||"cash";
let dividendCalendar;

function setDividendCalendar(source) {
  let previous=1,peak=1,old="";
  const months=new Map();
  const daily=source.series.map(([date,,nav,,holding,assetRet])=>{
    const month=date.slice(0,7);
    if(!months.has(month))months.set(month,{anchor:previous});
    const m=months.get(month);
    m.ret=nav/m.anchor-1;
    peak=Math.max(peak,nav);
    const row=[date,nav,null,nav/previous-1,nav/peak-1,holding,old,holding!==old,false,null,assetRet];
    previous=nav;
    old=holding;
    return row;
  });
  dividendCalendar={start:daily[0][0],as_of:source.as_of,daily,months};
  if(data&&!liveView)calendar();
}

function offsetMonth(month,step) {
  const [y,m]=month.split("-").map(Number);
  const d=new Date(Date.UTC(y,m-1+step,1));
  return monthCode(d.getUTCFullYear(),d.getUTCMonth()+1);
}
function calendar() {
  if(!data)return;
  const report=series();
  el("calendar-original-title").textContent=liveView?"实盘持仓":"上证180版";
  el("calendar-dividend").hidden=liveView;
  el("calendar-basis").hidden=liveView;
  el("calendar-panels").classList.toggle("single",liveView);
  el("calendar-legend").innerHTML=Object.keys(assets).map(assetHTML).join("")+
    (liveView?"":'<span class="reddiv"><i class="dot"></i>红利</span>')+"<span>↔ 入场或换仓</span>";
  for(const id of ["calendar-year","calendar-month","prev-month","next-month"])el(id).disabled=!report.daily.length;
  if(!report.daily.length){
    el("calendar-summary").textContent="暂无实盘收盘记录";
    el("holding-runs").replaceChildren();el("calendar-grid").replaceChildren();
    el("day-info").textContent="成交登记后，等收盘行情核对完成。";return;
  }
  const from=report.start.slice(0,7), to=report.as_of.slice(0,7);
  calendarMonth=calendarMonth<from?from:calendarMonth>to?to:calendarMonth;
  const [year,month]=calendarMonth.split("-");
  el("calendar-year").value=year;
  el("calendar-month").value=String(+month);
  el("prev-month").disabled=calendarMonth===from;
  el("next-month").disabled=calendarMonth===to;
  const firstDate=new Date(Date.UTC(+year,+month-1,1));
  const count=new Date(Date.UTC(+year,+month,0)).getUTCDate();
  const offset=(firstDate.getUTCDay()+6)%7;
  const days=report.daily.filter(row=>row[0].startsWith(calendarMonth));
  const redDays=liveView?[]:dividendCalendar?.daily.filter(row=>row[0].startsWith(calendarMonth))??[];
  if(!days.concat(redDays).some(row=>row[0]===daySelected))
    daySelected=days.at(-1)?.[0]??redDays.at(-1)?.[0]??null;
  renderCalendar(report,days,report.years.find(y=>y.year===year)?.months[month]?.ret,count,offset);
  if(!liveView){
    if(dividendCalendar){
      renderCalendar(dividendCalendar,redDays,dividendCalendar.months.get(calendarMonth)?.ret,count,offset,"-red");
    }else{
      el("calendar-summary-red").textContent="等待读取红利版历史";
      for(const id of ["holding-runs-red","calendar-grid-red","day-info-red"])el(id).replaceChildren();
    }
  }
  calendarDetails();
}
function renderCalendar(report,days,monthRet,count,offset,suffix="") {
  const [year,month]=calendarMonth.split("-");
  const byDay=new Map(days.map(row=>[Number(row[0].slice(-2)),row]));
  el("calendar-summary"+suffix).innerHTML='<span>'+year+' 年 '+Number(month)+' 月</span><span>月度收益 '+valueHTML(monthRet)+
    '</span><span>'+days.length+' 个交易日</span>';
  const runs=[];
  for(const row of days){
    const last=runs.at(-1);
    if(last?.symbol===row[5])last.end=row[0];
    else runs.push({symbol:row[5],start:row[0],end:row[0]});
  }
  el("holding-runs"+suffix).innerHTML=runs.map(({symbol,start,end})=>'<span class="'+calendarColor(symbol)+'">'+
    start.slice(-2)+(start===end?" 日":"–"+end.slice(-2)+" 日")+" · "+calendarAsset(symbol)+"</span>").join("");
  const blank='<div aria-hidden="true"></div>';
  const cells=Array(offset).fill(blank);
  for(let day=1; day<=count;day++){
    const row=byDay.get(day);
    if(!row){
      const iso=calendarMonth+"-"+String(day).padStart(2,"0");
      const weekend=(offset+day-1)%7>=5;
      const quiet=iso<report.start?"区间前":weekend||iso<=report.as_of?"休市":"—";
      cells.push('<div><span class="cal-date">'+day+'</span><span class="quiet">'+quiet+'</span></div>');
      continue;
    }
    const tag=row[7]?"<span title=\"入场或换仓\">↔</span>":row[8]?"<span title=\"换仓延期\">·</span>":"";
    cells.push('<button data-day="'+row[0]+'" class="'+calendarColor(row[5])+'" aria-label="'+row[0]+" "+calendarAsset(row[5])+
      " 组合 "+percent(row[3])+'" aria-pressed="'+(row[0]===daySelected)+'"><span class="cal-date">'+day+tag+
      '</span><span class="cal-asset">'+calendarAsset(row[5])+'</span><span class="cal-return '+tone(row[3])+'">'+
      '<span class="calendar-full">'+percent(row[3])+'</span><span class="calendar-compact">'+percent(row[3],1)+'</span></span></button>');
  }
  while(cells.length%7)cells.push(blank);
  el("calendar-grid"+suffix).innerHTML=cells.join("");
}
function calendarDetails() {
  dayDetail(series(),"day-info",liveView);
  if(!liveView&&dividendCalendar)dayDetail(dividendCalendar,"day-info-red");
}
function dayDetail(report,id,actual=false) {
  const row=report.daily.find(r=>r[0]===daySelected);
  if(!row){el(id).textContent=daySelected?daySelected+" 暂无历史记录。":"本月暂无已核对的交易日。";return;}
  const [day,nav,,ret,dd,holding,old,trade,blocked,target,assetRet]=row;
  const status=actual ? (trade?"有实盘成交，收盘 "+calendarAsset(holding):"收盘 "+calendarAsset(holding))+(blocked?" · 已记录跳过":"") :
    trade?(old?calendarAsset(old)+" → "+calendarAsset(holding):"首次建仓 "+calendarAsset(holding)):
    blocked?"换仓延期，目标 "+calendarAsset(target):"继续持有 "+calendarAsset(holding);
  el(id).innerHTML="<b>"+day+'</b><span>'+status+'</span><span>组合日收益 '+valueHTML(ret)+
    '</span><span>收盘持仓 ETF 日收益 '+valueHTML(assetRet)+'</span><span>净值 '+nav.toFixed(4)+'</span><span>历史回撤 '+valueHTML(dd)+'</span>';
}
function labelRecordCells(id){
  const body=el(id);
  const labels=Array.from(body.closest("table").querySelectorAll("th"),th=>th.textContent);
  for(const row of body.rows){
    row.setAttribute("role","row");
    for(const [index,cell] of Array.from(row.cells).entries()){
      cell.setAttribute("role","cell");
      if(cell.colSpan===1)cell.dataset.label=labels[index];
    }
  }
}
function annual(report=series(), yearSelected=reportYear, suffix="") {
  const lookup=id=>el(id+suffix);
  const year=report.years.find(y=>y.year===yearSelected);
  lookup("month-year").disabled=!year;
  if(!year){
    lookup("month-grid").replaceChildren();lookup("annual-body").innerHTML='<tr><td colspan="5">暂无实盘收盘记录</td></tr>';
    labelRecordCells("annual-body"+suffix);
    return;
  }
  lookup("month-year").value=yearSelected;
  lookup("month-grid").innerHTML=Array.from({length:12},(_,i)=>{
    const stat=year.months[String(i+1).padStart(2,"0")];
    return '<button class="month-tile" data-month="'+monthCode(yearSelected,i+1)+'" '+(stat?"":"disabled")+
      ' aria-label="'+(i+1)+'月收益 '+percent(stat?.ret)+'，打开持仓日历"><span>'+(i+1)+' 月</span><b class="'+
      tone(stat?.ret)+'">'+percent(stat?.ret)+'</b></button>';
  }).join("");
  lookup("annual-body").innerHTML=report.years.slice().reverse().map(y=>'<tr class="'+(y.year===yearSelected?"selected":"")+
    '"><td><button class="year-button" data-year="'+y.year+'" aria-pressed="'+(y.year===yearSelected)+'">'+y.year+
    (y.year===report.as_of.slice(0,4)?" 年内":"")+'</button></td><td>'+valueHTML(y.ret)+'</td><td>'+valueHTML(y.mdd)+
    '</td><td>'+(y.draw_peak ? (y.draw_peak.startsWith(y.year)?y.draw_peak.slice(5):y.draw_peak)+" 至 "+y.trough.slice(5):"—")+'</td><td>'+valueHTML(y.benchmark)+'</td></tr>').join("");
  labelRecordCells("annual-body"+suffix);
}
function trades() { page=renderTrades(data.trades,page); }
function renderTrades(source, page, suffix="") {
  const lookup=id=>el(id+suffix);
  const filter=lookup("trade-asset").value;
  const rows=source.filter(t=>!filter||filter===t.symbol);
  const pages=Math.max(1,Math.ceil(rows.length/pageSize));
  page=Math.min(page,pages-1);
  const price=x=>x==null?"—":x.toFixed(3);
  lookup("trade-body").innerHTML=rows.slice(page*pageSize,(page+1)*pageSize).map(t=>"<tr><td>"+(t.symbol==="sh510880"?'<span class="reddiv"><i class="dot"></i>红利</span>':assetHTML(t.symbol))+
    (t.proxy?' <small class="quiet">代理</small>':"")+"</td><td>"+t.entry+"</td><td>"+(t.exit||"未平仓")+
    "</td><td>"+price(t.entry_price)+"</td><td>"+price(t.exit_price)+"</td><td>"+t.sessions+"</td><td>"+
    valueHTML(t.ret)+'</td><td class="number '+tone(t.pnl)+'">'+money.format(t.pnl)+"</td></tr>").join("");
  lookup("trade-count").textContent=rows.length+" 笔 · 初始本金 100 万元";
  labelRecordCells("trade-body"+suffix);
  lookup("trade-page").textContent=(page+1)+" / "+pages;
  lookup("trade-prev").disabled=page===0;lookup("trade-next").disabled=page>=pages-1;
  return page;
}
function reportView(reset=false) {
  const report=series();
  el("nav-title").textContent=liveView?"实盘组合净值":"上证180版组合净值";
  el("annual-title").textContent=liveView?"实盘年度收益与回撤":"上证180版年度收益与回撤";
  if(reset||!calendarMonth){calendarMonth=report.as_of?.slice(0,7);reportYear=report.as_of?.slice(0,4);daySelected=null;}
  el("mode-note").textContent=liveView?"券商成交记账 · 人民币":"历史开盘模拟 · 人民币";
  el("strategy-key").textContent=liveView?"实盘账本":"ETF 轮动";
  el("benchmark-key").textContent=liveView?"同池等权月调仓":"沪深300价格指数";
  el("annual-benchmark").textContent=el("benchmark-key").textContent;
  el("calendar-mode").textContent=liveView?"实盘账本 · 按交易日查看收盘持仓及收益":"历史模拟 · 两版同步切换月份与日期";
  el("annual-mode").textContent=(liveView?"实盘账本":"历史模拟")+" · 点年份查看 12 个月的收益";
  const s=report.summary;
  const kpi=[["累计收益",valueHTML(s?.ret)],["年化收益",valueHTML(s?.cagr)],
    ["最大回撤",valueHTML(s?.mdd)],["最新净值",s?.nav.toFixed(2)??"—"]];
  el("metrics").innerHTML=kpi.map(([key,v])=>"<div class=\"metric\"><span>"+key+"</span><strong>"+v+"</strong></div>").join("");
  const years=report.years.slice().reverse().map(y=>'<option value="'+y.year+'">'+y.year+' 年</option>').join("");
  el("calendar-year").innerHTML=years;el("month-year").innerHTML=years;
  chartData(reset);calendar();annual();
}
function render(fresh) {
  const first=!data;
  data=fresh;
  el("coverage").textContent=data.start+" 至 "+data.as_of+" · 境内交易日";
  if(first){
    el("calendar-month").innerHTML=Array.from({length:12},(_,i)=>'<option value="'+(i+1)+'">'+(i+1)+' 月</option>').join("");
    el("trade-asset").innerHTML += Object.entries(assets).map(([key,a])=>'<option value="'+key+'">'+a[0]+" "+a[1]+"</option>").join("");
  }
  signal();reportView();trades();renderPilot();
}
async function refresh() {
  try{
    const response=await fetch("/api/dashboard",{cache:"no-store"});
    if(!response.ok)throw Error("行情接口暂不可用");
    render(await response.json());
  }catch(error){
    el("status").textContent="加载失败";el("status").classList.add("warn");
    const w=el("warning");w.hidden=false;w.textContent="行情加载失败，稍后将重新检查。"+(data?" 当前继续显示 "+data.as_of+" 的记录。":"");
    if(data){data.signal_ready=false;renderPilot();}
  }
}
el("book-mode").onclick=e=>{
  if(!data||e.target.dataset.live==null)return;
  liveView=e.target.dataset.live==="1";
  for(const b of el("book-mode").querySelectorAll("button")){b.classList.toggle("active",b===e.target);b.setAttribute("aria-pressed",String(b===e.target));}
  range=0;reportView(true);
};
el("ranges").addEventListener("click",e=>{if(e.target.dataset.range!=null){range=Number(e.target.dataset.range);chartData(true);}});
el("log-scale").addEventListener("change",draw);
function selectChartPoint(e){
  if(!plot)return;
  const x=e.clientX-canvas.getBoundingClientRect().left;
  selection=Math.max(0,Math.min(plot.n,Math.round((x-plot.left)/(plot.right-plot.left)*plot.n)));draw();
}
canvas.addEventListener("pointerdown",selectChartPoint);
canvas.addEventListener("pointermove",e=>{if(e.pointerType==="mouse"||e.buttons)selectChartPoint(e);});
canvas.addEventListener("pointerleave",e=>{
  if(e.pointerType==="mouse"){selection=null;draw();}
});
canvas.addEventListener("keydown",e=>{
  if(!plot||!["ArrowLeft","ArrowRight"].includes(e.key))return;
  e.preventDefault();selection=Math.max(0,Math.min(plot.n,(selection??plot.n)+(e.key==="ArrowLeft"?-1:1)));draw();
});
new ResizeObserver(draw).observe(el("chart-wrap"));
el("prev-month").onclick=()=>{calendarMonth=offsetMonth(calendarMonth,-1);calendar();};
el("next-month").onclick=()=>{calendarMonth=offsetMonth(calendarMonth,1);calendar();};
for(const id of ["calendar-year","calendar-month"])el(id).onchange=()=>{
  calendarMonth=monthCode(el("calendar-year").value,el("calendar-month").value);calendar();
};
el("calendar-panels").onclick=e=>{
  const select=e.target.closest("button[data-day]");
  if(!select)return;
  daySelected=select.dataset.day;
  for(const button of el("calendar-panels").querySelectorAll("button[data-day]"))button.setAttribute("aria-pressed",String(button.dataset.day===daySelected));
  calendarDetails();
};
el("month-year").onchange=e=>{reportYear=e.target.value;annual();};
el("annual-body").onclick=e=>{const y=e.target.dataset.year;if(y){reportYear=y;annual();}};
el("month-grid").onclick=e=>{const m=e.target.closest("button[data-month]")?.dataset.month;if(m){calendarMonth=m;calendar();el("calendar").scrollIntoView();}};
el("trade-asset").onchange=()=>{page=0;trades();};
el("trade-prev").onclick=()=>{page--;trades();};
el("trade-next").onclick=()=>{page++;trades();};
document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});
document.addEventListener("DOMContentLoaded",refresh);
setInterval(refresh,60*1000);
