'use strict';
let redReport, redYear, redRange=0, redRows=[], redSelection=null, redPlot;
const redCanvas=el("chart-red"), redContext=redCanvas.getContext("2d");
const redElement=id=>el(id+"-red");

function drawDividend(){
  redPlot=drawNav(redCanvas,redContext,redRows,redSelection,redRange,redElement);
}
function dividendChart(reset=false){
  if(!redReport)return;
  const selectedDate=reset?null:redRows[redSelection]?.[0];
  const rows=[[redReport.base_date,1,1,0,0],...redReport.daily];
  const year=Number(redReport.as_of.slice(0,4));
  const threshold=redRange===-1?year+"-01-01":(year-redRange)+redReport.as_of.slice(4);
  const start=redRange===0?0:Math.max(0,rows.findIndex(r=>r[0]>=threshold)-1);
  redRows=rows.slice(start);
  const index=selectedDate?redRows.findIndex(r=>r[0]===selectedDate):-1;
  redSelection=index<0?null:index;
  redElement("ranges").innerHTML=ranges.map(([name,value])=>'<button data-range="'+value+'" class="'+
    (value===redRange?'active':'')+'" aria-pressed="'+(value===redRange)+'">'+name+'</button>').join('');
  drawDividend();
}
function renderDividend(report){
  redReport=report;
  if(!redReport.years.some(y=>y.year===redYear))redYear=report.as_of.slice(0,4);
  el("red-coverage").textContent=report.start+" 至 "+report.as_of+" · 历史开盘模拟 · 人民币";
  const s=report.summary;
  redElement("metrics").innerHTML=[["累计收益",valueHTML(s.ret)],["年化收益",valueHTML(s.cagr)],
    ["最大回撤",valueHTML(s.mdd)],["最新净值",s.nav.toFixed(2)]].map(([name,value])=>
    '<div class="metric"><span>'+name+'</span><strong>'+value+'</strong></div>').join('');
  redElement("month-year").innerHTML=report.years.slice().reverse().map(y=>
    '<option value="'+y.year+'">'+y.year+' 年</option>').join('');
  annual(report,redYear,"-red");
  dividendChart();
}
redElement("ranges").onclick=e=>{
  if(e.target.dataset.range==null)return;
  redRange=Number(e.target.dataset.range);dividendChart(true);
};
redElement("log-scale").onchange=drawDividend;
function selectDividendPoint(e){
  if(!redPlot)return;
  const x=e.clientX-redCanvas.getBoundingClientRect().left;
  redSelection=Math.max(0,Math.min(redPlot.n,Math.round((x-redPlot.left)/(redPlot.right-redPlot.left)*redPlot.n)));
  drawDividend();
}
redCanvas.addEventListener("pointerdown",selectDividendPoint);
redCanvas.addEventListener("pointermove",e=>{if(e.pointerType==="mouse"||e.buttons)selectDividendPoint(e);});
redCanvas.addEventListener("pointerleave",e=>{if(e.pointerType==="mouse"){redSelection=null;drawDividend();}});
redCanvas.addEventListener("keydown",e=>{
  if(!redPlot||!["ArrowLeft","ArrowRight"].includes(e.key))return;
  e.preventDefault();
  redSelection=Math.max(0,Math.min(redPlot.n,(redSelection??redPlot.n)+(e.key==="ArrowLeft"?-1:1)));
  drawDividend();
});
new ResizeObserver(drawDividend).observe(redElement("chart-wrap"));
redElement("month-year").onchange=e=>{redYear=e.target.value;annual(redReport,redYear,"-red");};
redElement("annual-body").onclick=e=>{
  const year=e.target.dataset.year;
  if(year){redYear=year;annual(redReport,redYear,"-red");}
};
redElement("month-grid").onclick=e=>{
  const month=e.target.closest("button[data-month]")?.dataset.month;
  if(!month||!data)return;
  if(liveView)el("book-mode").querySelector('[data-live="0"]').click();
  calendarMonth=month;calendar();el("calendar-dividend").scrollIntoView();
};

let redTrades=[], redPage=0;
redElement("trade-asset").innerHTML+=["sh518880","sh513100","sz159915","sh510880"].map(symbol=>
  '<option value="'+symbol+'">'+(symbol==="sh510880"?"红利 510880":assets[symbol][0]+" "+assets[symbol][1])+'</option>').join('');
function renderDividendTrades(rows){redTrades=rows;redPage=renderTrades(redTrades,redPage,"-red");}
redElement("trade-asset").onchange=()=>{redPage=0;renderDividendTrades(redTrades);};
redElement("trade-prev").onclick=()=>{redPage--;renderDividendTrades(redTrades);};
redElement("trade-next").onclick=()=>{redPage++;renderDividendTrades(redTrades);};
