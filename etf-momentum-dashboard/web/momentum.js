'use strict';

const trailRanges = [["近15天","sessions"],["近1个月",1],["近3个月",3],["近半年",6],["近三年",36],["全部",0]];
const trailPools = [
  {id:"original",title:"上证180版", assets:["sh510180","sh518880","sh513100","sz159915"]},
  {id:"dividend",title:"红利版", assets:["sh510880","sh518880","sh513100","sz159915"]}
];
const trailColors={sh510180:"#89d9b8",sh510880:"#ff9f99",sh518880:"#edc97f",sh513100:"#8cacf7",sz159915:"#b59af9"};
const trailDate=el("trail-date");
let trailSource, trailRange="sessions", trailRows=[], trailPlots=[], trailSelected, trailVisible=false, trailLoading=false;

el("trail-ranges").innerHTML=trailRanges.map(([name,key])=>
  '<button type="button" data-trail="'+key+'" aria-pressed="'+(key==="sessions")+'"'+
  (key==="sessions"?' class="active"':"")+'>'+name+'</button>').join("");
el("trail-plots").innerHTML=trailPools.map(pool=>
  '<article class="trail-panel"><h3>'+pool.title+'动量轨迹</h3><div class="trail-legend">'+
    pool.assets.map(s=>'<span><i style="--asset:'+trailColors[s]+'"></i>'+shadowAsset(s)+'</span>').join("")+
  '</div><svg class="trail-chart" role="img" aria-label="'+pool.title+'四只 ETF 的动量分数曲线"></svg>'+
  '<div class="trail-reading"></div></article>').join("");

function slicedTrail(data,range){
  if(range==="sessions")return data.series.slice(-15);
  if(range==="0")return data.series;
  const cutoff=monthsBefore(data.as_of,Number(range));
  return data.series.filter(row=>row[0]>=cutoff);
}

function trailScale(values){
  const min=Math.min(0,...values),max=Math.max(0,...values);
  if(min===max)return {min:-.01,max:.01,ticks:[-.01,0,.01]};
  const raw=(max-min)/4, magnitude=10**Math.floor(Math.log10(raw));
  const step=[1,2,2.5,5,10].find(n=>n*magnitude>=raw)*magnitude;
  const pad=(max-min)*.04, low=min-pad,high=max+pad;
  const first=Math.ceil(low/step),last=Math.floor(high/step);
  return {min:low,max:high,ticks:Array.from({length:last-first+1},(_,i)=>(first+i)*step)};
}

function selectTrail(index){
  if(!trailRows.length)return;
  const row=trailRows[index];
  trailDate.value=index;
  trailDate.setAttribute("aria-valuetext",row[0]);
  trailSelected=row[0];
  for(const plot of trailPlots){
    const x=plot.x(index);
    for(const attr of ["x1","x2"])plot.guide.setAttribute(attr,x);
    plot.lines.forEach(({col,dot})=>{
      dot.setAttribute("cx",x);
      dot.setAttribute("cy",plot.y(row[col]));
    });
    plot.reading.innerHTML='<b>'+row[0]+'</b>'+plot.lines.slice().sort((a,b)=>row[b.col]-row[a.col]).map(({s,col})=>
      '<span style="--asset:'+trailColors[s]+'"><i></i>'+shadowAsset(s)+' '+percent(row[col])+'</span>').join("");
  }
}

function drawTrails(){
  if(!trailRows.length)return;
  const bounds=trailScale(trailRows.flatMap(row=>row.slice(1)));
  const last=trailRows.length-1;
  const layout=document.querySelectorAll(".trail-panel");
  trailPlots=trailPools.map((pool,i)=>{
    const panel=layout[i],svg=panel.querySelector("svg");
    const width=svg.clientWidth||300,height=svg.clientHeight||200;
    const left=width<340?48:55,right=width-7,top=12,bottom=height-24;
    const x=j=>left+(right-left)*j/Math.max(1,last);
    const y=v=>bottom-(bottom-top)*(v-bounds.min)/(bounds.max-bounds.min);
    const tickLabel=v=>Math.abs(v)>=100?(v>0?"+":"−")+(Math.abs(v)/100).toFixed(1)+"万%":percent(v,0);
    svg.setAttribute("viewBox","0 0 "+width+" "+height);
    const grid=bounds.ticks.map(v=>'<line class="'+(v===0?"trail-zero":"")+'" x1="'+left+'" x2="'+right+
      '" y1="'+y(v)+'" y2="'+y(v)+'"/><text x="'+(left-5)+'" y="'+y(v)+
      '" text-anchor="end" dy=".33em">'+tickLabel(v)+'</text>').join("");
    const tickCount=width<390?4:5;
    const indexes=[...new Set(Array.from({length:tickCount},(_,k)=>Math.round(last*k/(tickCount-1))))];
    const xlabels=indexes.map(j=>{
      const date=trailRows[j][0],label=trailRange==="0"||trailRange==="36"?date.slice(0,7):date.slice(5);
      return '<text x="'+x(j)+'" y="'+(height-5)+'" text-anchor="'+(j===0?"start":j===last?"end":"middle")+'">'+label+'</text>';
    }).join("");
    const lines=pool.assets.map(s=>({s,col:trailSource.symbols.indexOf(s)+1}));
    const paths=lines.map(({s,col})=>'<polyline stroke="'+trailColors[s]+'" points="'+
      trailRows.map((r,j)=>x(j).toFixed(1)+","+y(r[col]).toFixed(1)).join(" ")+'"/>').join("");
    svg.innerHTML=grid+xlabels+paths+'<line class="trail-guide" y1="'+top+'" y2="'+bottom+'"/>'+
      lines.map(({s})=>'<circle fill="'+trailColors[s]+'" r="3.4"/>').join("");
    const markers=svg.querySelectorAll("circle");
    lines.forEach((line,j)=>{line.dot=markers[j];});
    return {svg,reading:panel.querySelector(".trail-reading"),x,y,left,right,lines,guide:svg.querySelector(".trail-guide")};
  });
  selectTrail(Number(trailDate.value));
}

function renderTrails(followLatest){
  trailRows=slicedTrail(trailSource,trailRange);
  const index=followLatest?-1:trailRows.findIndex(r=>r[0]===trailSelected);
  trailDate.max=trailRows.length-1;
  trailDate.value=index<0?trailRows.length-1:index;
  el("trail-date-wrap").hidden=false;
  el("trail-period").textContent=trailRows[0][0]+" 至 "+trailRows.at(-1)[0]+" · "+trailRows.length+" 个共同交易日";
  const status=el("trail-status");
  status.textContent=trailSource.stale?"等待行情核对":"更新至 "+trailSource.as_of;
  status.classList.toggle("warn",trailSource.stale);
  el("trail-warning").hidden=!trailSource.stale;
  el("trail-warning").textContent="保留最后一组共同核对的动量分数，行情齐全后会更新。";
  drawTrails();
}

el("trail-ranges").onclick=e=>{
  if(!trailSource||e.target.dataset.trail==null)return;
  trailRange=e.target.dataset.trail;
  activeChoice("trail-ranges","trail",trailRange);
  renderTrails(true);
};
trailDate.oninput=()=>selectTrail(Number(trailDate.value));
const trailResize=new ResizeObserver(drawTrails);
for(const svg of document.querySelectorAll(".trail-chart")){
  function moveTrail(event){
    const plot=trailPlots.find(p=>p.svg===svg);
    if(!plot)return;
    const position=event.clientX-svg.getBoundingClientRect().left;
    const index=Math.round((position-plot.left)/(plot.right-plot.left)*(trailRows.length-1));
    selectTrail(Math.max(0,Math.min(trailRows.length-1,index)));
  }
  svg.addEventListener("pointerdown",moveTrail);
  svg.addEventListener("pointermove",e=>{if(e.pointerType==="mouse"||e.buttons)moveTrail(e);});
  trailResize.observe(svg);
}

async function refreshTrails(){
  if(trailLoading)return;
  trailLoading=true;
  try{
    const response=await fetch("/api/momentum",{cache:"no-store"});
    if(!response.ok)throw Error();
    const next=await response.json();
    const followLatest=!trailSource||trailSelected===trailSource.as_of;
    trailSource=next;
    renderTrails(followLatest);
  }catch{
    el("trail-status").textContent="暂时无法核对";
    el("trail-status").classList.add("warn");
    el("trail-warning").hidden=false;
    el("trail-warning").textContent=trailSource?"继续显示上次读取的动量轨迹，稍后重试。":"动量轨迹暂未加载，稍后重试。";
  }finally{trailLoading=false;}
}
new IntersectionObserver(([entry])=>{
  trailVisible=entry.isIntersecting;
  if(trailVisible)refreshTrails();
}).observe(el("momentum-trails"));
document.addEventListener("visibilitychange",()=>{if(!document.hidden&&trailVisible)refreshTrails();});
setInterval(()=>{if(trailVisible&&!document.hidden)refreshTrails();},60*1000);
