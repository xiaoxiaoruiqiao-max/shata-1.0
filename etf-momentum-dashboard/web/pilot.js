'use strict';
const kinds = {start:"启动试验",buy:"买入成交",sell:"卖出成交",skip:"未换仓 / 留余量",
  dividend:"到账分红",units:"份额调整"};
const reasons = {premium:"纳指溢价 / 净值不可用",halt:"停牌 / 涨跌停",liquidity:"价差或流动性不合格",
  unfilled:"未成交 / 部分成交",data:"数据待核对",risk:"触及试验止线"};
const ledger = el("ledger-form"), quotes=el("quote-form");
const chinaClock = new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Shanghai",dateStyle:"short",timeStyle:"medium"});
const number = new Intl.NumberFormat("zh-CN",{maximumFractionDigits:2});
const yuan = x => x==null?"—":number.format(Number(x))+" 元";
const px = x => x==null?"—":Number(x).toFixed(3);
const opts = entries => Object.entries(entries).map(([key,value])=>'<option value="'+key+'">'+value+'</option>').join("");
el("ledger-symbol").innerHTML=opts(Object.fromEntries(Object.entries(assets).map(([key,a])=>[key,a[0]+" "+a[1]])));
el("ledger-reason").innerHTML=opts(reasons);
let deskKey="", saving=false, started, asOfServer, markedAt;

function ledgerFields(){
  const k=el("ledger-kind").value;
  const fill=k==="buy"||k==="sell";
  const show={fill,units:fill||k==="units",cash:k==="dividend",skip:k==="skip",nav:k==="buy"&&ledger.elements.symbol.value==="sh513100"};
  for(const label of ledger.querySelectorAll("[data-entry]")){
    label.hidden=!show[label.dataset.entry];
    const input=label.querySelector("input,select");
    input.disabled=label.hidden;
    input.required=!label.hidden&&!["reference","iopv"].includes(input.name);
  }
  el("qty-label").textContent=k==="units"?"券商变更后的总份额":"成交份额";
}
function nowChina(){
  return chinaClock.format(new Date(asOfServer+performance.now()-markedAt)).replace("T"," ");
}
function operatingPlan(){
  if(!data?.pilot.start)return {};
  const p=data.pilot;
  const clock=nowChina();
  const day=clock.slice(0,10), time=clock.slice(11,19);
  const match=data.next_session===day;
  const inWindow=time>="09:35:00" && time<"09:50:00";
  if(p.stop){
    return {sell:p.holding, stop:true, open:!!(data.session_today&&inWindow&&p.holding)};
  }
  if(!data.signal_ready||!match||p.lagging && p.events.some(e=>e.date<day&&e.date>data.as_of&&["buy","sell","units","dividend"].includes(e.kind))){
    return {unready:true, open:false};
  }
  const latestFill=p.events.find(e=>e.kind==="buy"||e.kind==="sell");
  const skippedBuy=p.events.some(e=>e.kind==="skip"&&e.reason==="unfilled"&&e.date===latestFill?.date);
  const finishBuy=latestFill?.kind==="buy"&&latestFill.symbol===data.target&&latestFill.date<day&&skippedBuy;
  const buy=!p.holding||p.holding!==data.target||finishBuy;
  return {buy:buy?data.target:"",sell:p.holding!==data.target?p.holding:"",
    done:latestFill?.date===day&&p.holding===data.target, open:inWindow};
}
function renderDesk(){
  if(!data)return;
  const p=data.pilot, plan=operatingPlan();
  const checked=plan.open&&!plan.done&&!!(plan.buy||plan.sell);
  const key=[plan.sell,plan.buy,checked].join("|");
  if(key!==deskKey){
    deskKey=key;quotes.reset();el("quote-result").hidden=true;
    const quoteInput=(kind, side)=>'<label>'+kind+' '+side+'<input name="'+(kind==="买入标的"?"buy":"sell")+
      (side==="买一"?"Bid":"Ask")+'" inputmode="decimal" type="number" min=".001" step=".001" required></label>';
    el("quote-fields").innerHTML=(plan.sell?quoteInput("卖出标的","买一")+quoteInput("卖出标的","卖一"):"")+
      (plan.buy?quoteInput("买入标的","买一")+quoteInput("买入标的","卖一"):"")+
      (plan.buy==="sh513100"?'<label>当日券商参考净值<input name="iopv" inputmode="decimal" type="number" min=".0001" step=".0001" required></label>':"");
  }
  quotes.hidden=!checked;
  const day=nowChina().slice(0,10);
  const time=nowChina().slice(11,19);
  el("session-state").textContent=!p.start?"登记试验起始日后，可在交易窗口核对委托价格。":
    plan.stop ? (p.holding?"已触及止线。"+(plan.open?"本窗口仅核对卖出 "+label(p.holding)+"。":"下一可交易窗口退出 "+label(p.holding)+"。"):"已触及止线并空仓，本轮结束。"):
    plan.unready ? (!p.lagging && data.signal_ready && data.next_session && day<data.next_session ?
      "下一窗口 "+data.next_session+" 09:35–09:50，目标 "+label(data.target)+"。":
      "当前没有可执行信号。等待交易日或行情核对，已成交部分照实登记。"):
    !plan.open ? (time>="09:50:00"?"执行窗口已结束。核对撤单及成交，次日重新判断。":"09:35–09:50 执行窗口尚未开始。"):
    plan.done ? "今日已登记目标仓位成交。若原委托还有余单，按原限价处理至 09:50。":
    !plan.buy && !plan.sell ? "目标与实盘持仓相同，继续持有 "+label(p.holding)+"。":
    (plan.sell?"确认 "+label(plan.sell)+" 可卖后先卖旧仓。":"按券商可用资金执行。")+
    (plan.buy?" 目标 "+label(plan.buy)+"，核对两腿后再下第一单。":"");
}
function renderPilot(){
  const p=data.pilot;
  asOfServer=Date.parse(data.now); markedAt=performance.now();
  const badge=el("pilot-status");
  badge.textContent=p.stop?"已触发试验止线":p.start?"实盘试验中":"尚未开始";
  badge.classList.toggle("warn",p.stop);
  const metrics=[["起始本金",yuan(p.capital)],["券商账本现金",yuan(p.cash)],["实盘持仓",label(p.holding)+(p.qty?" · "+number.format(p.qty)+" 份":"")],
    ["已核对收盘回撤",valueHTML(p.summary?.dd)]];
  el("pilot-kpis").innerHTML=metrics.map(([key,v])=>'<div class="metric"><span>'+key+'</span><strong>'+v+'</strong></div>').join("");
  const approved=p.checks.length&&p.checks.every(check=>check[1])&&data.signal_ready;
  el("review-info").textContent=!p.start?"需先登记试验起始日。":p.stop?"试验止线已触发，本轮不加仓。":
    approved?"满足量化门槛，可以复核实际资金与流动性；账户上限仍为 50 万元。":
    (p.lagging?"有待收盘估值。":"尚未通过全部加仓门槛。")+(p.slippage==null?"":" 已记录不利滑点 "+p.slippage.toFixed(1)+" bp。");
  el("review-checks").innerHTML=p.checks.map(([name,pass,value])=>'<li><span class="'+(pass?"met":"waiting")+'">'+
    (pass?"达标":"待达标")+'</span><span>'+name+(value?' <small>'+value+'</small>':"")+"</span></li>").join("");
  if(started!==p.start){
    started=p.start;
    el("ledger-kind").innerHTML=opts(p.start?Object.fromEntries(Object.entries(kinds).slice(1)):{start:kinds.start});
    ledger.elements.date.value=nowChina().slice(0,10);
    ledgerFields();
  }
  ledger.elements.date.max=nowChina().slice(0,10);
  if(started)ledger.elements.date.min=p.start;else ledger.elements.date.removeAttribute("min");
  el("undo-pilot").disabled=saving||!p.events.length;
  el("export-pilot").disabled=!p.events.length;
  el("ledger-body").innerHTML=p.events.length?p.events.map(e=>'<tr><td>'+e.date+(e.time?" "+e.time:"")+"</td><td>"+kinds[e.kind]+
    '</td><td>'+(e.symbol?assetHTML(e.symbol):reasons[e.reason]||(e.amount?yuan(e.amount):"—"))+'</td><td>'+(e.qty?number.format(e.qty):"—")+
    '</td><td>'+px(e.price)+'</td><td>'+yuan(e.fee)+'</td><td>'+(e.slippage_bp==null?"—":e.slippage_bp.toFixed(1)+" bp")+
    '</td><td>'+valueHTML(e.premium)+'</td><td>'+valueHTML(e.ret)+'</td><td>'+yuan(e.pnl)+'</td></tr>').join(""):
    '<tr><td colspan="10">暂无实盘记录</td></tr>';
  labelRecordCells("ledger-body");
  renderDesk();
}
function priceChecks(plan, get){
  let bid,ask,buyLimit,sellLimit,premium;
  for(const side of ["sell","buy"]){
    if(!plan[side])continue;
    bid=Number(get(side+"Bid"));ask=Number(get(side+"Ask"));
    if(!(bid>0&&ask>=bid))return {error:"买一、卖一报价无效。"};
    if(200*(ask-bid)/(ask+bid)>.3+1e-9)return {error:label(plan[side])+" 的价差大于 0.30%，跳过换仓。"};
    if(side==="sell"){
      sellLimit=Math.ceil(bid*.998*1000-1e-9)/1000;
    }else{
      buyLimit=Math.floor(ask*1.002*1000+1e-9)/1000;
      if(plan.buy==="sh513100"){
        const iopv=Number(get("iopv"));
        if(!(iopv>0))return {error:"缺少有效参考净值，跳过买入纳指。"};
        premium=ask/iopv-1;
        if(premium>=data.premium_cap-1e-12)return {error:"纳指按卖一报价计算溢价 "+percent(premium)+"，达到 3.00%，跳过换仓。"};
        buyLimit=Math.min(buyLimit,(Math.ceil(iopv*(1+data.premium_cap)*1000-1e-9)-1)/1000);
        if(buyLimit<ask-1e-12)return {error:"最小报价单位下没有可用的纳指买入限价，跳过换仓。"};
      }
    }
  }
  return {sellLimit,buyLimit,premium};
}
quotes.onsubmit=e=>{
  e.preventDefault();
  const plan=operatingPlan(), fields=new FormData(quotes);
  if(!plan.open||plan.done||!fields.get("verified"))return;
  const checked=priceChecks(plan,key=>fields.get(key));
  const output=el("quote-result");
  output.hidden=false;
  output.classList.toggle("blocked",!!checked.error);
  output.textContent=checked.error||[
    checked.sellLimit&&"卖出保护限价 ≥ "+px(checked.sellLimit)+" 元",
    checked.buyLimit&&"买入保护限价 ≤ "+px(checked.buyLimit)+" 元",
    checked.premium!=null&&"纳指估算溢价 "+percent(checked.premium),
    "实际委托前再核对报价、溢价和可用资金。09:50 撤余单。"
  ].filter(Boolean).join(" · ");
};
el("ledger-kind").onchange=ledgerFields;
el("ledger-symbol").onchange=ledgerFields;
async function saveLedger(fields){
  if(saving||!data)return;
  saving=true;
  const submit=ledger.querySelector('button[type="submit"]');
  const status=el("ledger-feedback");
  submit.disabled=true;el("undo-pilot").disabled=true;
  status.textContent="正在保存…";
  try{
    const response=await fetch("/api/pilot",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({...fields,revision:data.pilot.events.length})});
    const result=await response.json();
    if(!response.ok)throw Error(result.error||"保存失败");
    status.textContent=fields.kind==="undo"?"末条已撤回。":"已保存。";
    await refresh();
    for(const name of ["qty","price","reference","amount","iopv"])ledger.elements[name].value="";
  }catch(error){status.textContent=error.message;}
  finally{saving=false;submit.disabled=false;el("undo-pilot").disabled=!data.pilot.events.length;}
}
ledger.onsubmit=e=>{e.preventDefault();saveLedger(Object.fromEntries(new FormData(ledger)));};
el("undo-pilot").onclick=()=>{if(confirm("撤回账本最后一条记录？券商账户不会受影响。"))saveLedger({kind:"undo"});};
el("export-pilot").onclick=()=>{
  const url=URL.createObjectURL(new Blob([JSON.stringify(data.pilot.events.slice().reverse(),null,2)],{type:"application/json"}));
  const a=document.createElement("a");a.href=url;a.download="ETF实盘账本-"+data.as_of+".json";a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
};
setInterval(renderDesk,1000);
