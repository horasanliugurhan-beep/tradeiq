'use strict';
const token = document.querySelector('meta[name="app-token"]').content;
const el = id => document.getElementById(id);
const fmt = value => Number(value).toLocaleString('tr-TR', {minimumFractionDigits:2, maximumFractionDigits:2});
const money = value => fmt(value) + ' USDT';
let currentState;
let working = false;
let selectionDirty = false;
let automaticDirty = false;
let refreshFailed = false;
const outcomes = {hold:'İzleniyor', buy_filled:'Sanal alım', buy_rejected:'Alım reddedildi', sell_rejected:'Satış reddedildi', signal:'Sanal satış', stop:'Stop ile satış', take_profit:'Kâr-al ile satış', buy_blocked_daily_limit:'Günlük sınır · alım engellendi', buy_blocked_position_limit:'Pozisyon sınırı', buy_rejected_min_notional:'Yetersiz işlem tutarı'};
function message(text, error=false){el('message').textContent=text;el('message').hidden=false;el('message').classList.toggle('error',error);}
async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{'X-App-Token':token,...options.headers},cache:'no-store'});
  const body = await response.json();
  if(!response.ok) throw new Error(body.error || 'İşlem tamamlanamadı.');
  return body;
}
async function command(command, extra={}){
  if(working&&command!=='pause')return;
  const interrupt=working&&command==='pause';
  if(!interrupt){working=true;document.body.classList.add('busy');}
  message(['buy','sell','refresh','testnet_connect','testnet_validate'].includes(command)?'Bağlantı kontrol ediliyor. Sonuç gelene kadar bekleyin…':'İşlem uygulanıyor…');
  try{
    const result=await api('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command,...extra})});
    if(command==='configure')selectionDirty=false;
    if(['start','pause','configure'].includes(command))automaticDirty=false;
    if(result.message)message(result.message);
    else if(command==='testnet_connect')message('Test ortamı bağlantısı doğrulandı. Gerçek hesaba bağlanılmadı.');
    else if(command==='pause')message('Yeni otomatik işlemler duraklatıldı. Açık sanal pozisyonların stop takibi devam eder.');
    else if(result.outcome&&result.outcome!=='hold'){message(outcomes[result.outcome]||result.outcome,result.outcome.includes('blocked')||result.outcome.includes('rejected'));}
    else {el('message').hidden=true;}
    const updated=await refresh();
    if(!updated)message('İşlem yanıtı geldi ancak panel yenilenemedi. Tekrar işlem göndermeden bağlantıyı kontrol edin.',true);
  }catch(error){message(error.message,true);}
  finally{if(!interrupt){working=false;document.body.classList.remove('busy');}}
}
function cell(row,text){const td=document.createElement('td');td.textContent=text;row.append(td);return td;}
function drawChart(points){
  const canvas=el('chart'), rect=canvas.getBoundingClientRect(), dpr=window.devicePixelRatio||1;
  canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);
  const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
  const w=rect.width,h=rect.height,l=56,r=15,t=12,b=30;
  const truncated=currentState?.chart_truncated;
  const initial=Number(currentState?.summary.initial_cash||1000);
  const values=points.length?(truncated?points.map(p=>Number(p.equity)):[initial,...points.map(p=>Number(p.equity))]):[initial,initial];
  const min=Math.min(...values),max=Math.max(...values),pad=Math.max((max-min)*.2,5);
  const low=min-pad,high=max+pad;
  const x=i=>l+(w-l-r)*i/(values.length-1), y=v=>t+(h-t-b)*(high-v)/(high-low);
  ctx.font='9px Segoe UI';ctx.fillStyle='#718194';ctx.lineWidth=1;
  for(let i=0;i<4;i++){const value=low+(high-low)*i/3,py=y(value);ctx.strokeStyle='#262f3b';ctx.beginPath();ctx.moveTo(l,py);ctx.lineTo(w-r,py);ctx.stroke();ctx.fillText(fmt(value),0,py+3);}
  if(points.length){
    const gradient=ctx.createLinearGradient(0,t,0,h-b);gradient.addColorStop(0,'rgba(197,238,139,.2)');gradient.addColorStop(1,'rgba(197,238,139,0)');
    ctx.beginPath();ctx.moveTo(x(0),h-b);values.forEach((v,i)=>ctx.lineTo(x(i),y(v)));ctx.lineTo(x(values.length-1),h-b);ctx.closePath();ctx.fillStyle=gradient;ctx.fill();
    ctx.beginPath();values.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.strokeStyle='#c5ee8b';ctx.lineWidth=2;ctx.stroke();
    ctx.beginPath();ctx.arc(x(values.length-1),y(values.at(-1)),3,0,Math.PI*2);ctx.fillStyle='#c5ee8b';ctx.fill();
    ctx.fillStyle='#718194';ctx.fillText(truncated?'Görünür dönem':'Başlangıç',l,h-5);ctx.fillText('Son olay',w-r-42,h-5);
  }
  el('chart-empty').hidden=points.length>0;
}
function render(state){
 currentState=state;const s=state.summary;
 el('equity').textContent=money(s.equity);el('cash').textContent=money(s.cash);el('pnl').textContent=(Number(s.net_pnl)>0?'+':'')+money(s.net_pnl);
 el('pnl').className=Number(s.net_pnl)>=0?'positive':'negative';
 const stale=state.source==='public'&&(state.price_age===null||state.price_age>30||state.error);
 el('fees').textContent=money(s.fees);el('risk').textContent=s.killed?'Yeni alım kapalı':stale?'Veri bekleniyor':'Sınırlar içinde';el('risk').className=s.killed?'risk-value negative':'risk-value';
 el('risk-note').textContent=stale?'Güncel fiyat olmadan koruma değerlendirilemez':s.killed?'Çıkış kontrolleri devam eder':'Günlük yeni alım sınırı: %2';
 el('chart-caption').textContent=state.chart_truncated?'Son 200 olay · toplam portföy değeri':'İşlenen olaylara göre toplam değer';
 el('mode').textContent=s.mode;if(!selectionDirty){el('source').value=state.source;el('symbol').value=state.symbol;}
 el('demo-controls').hidden=state.source!=='demo';el('public-controls').hidden=state.source!=='public';
 if(!automaticDirty)el('automatic').checked=state.automatic;el('automatic').disabled=state.active;el('demo-progress').textContent=s.events+' / '+state.demo_total+' olay';el('step').disabled=s.events>=state.demo_total;
 el('start').disabled=state.active;el('pause').disabled=!state.active&&!working;el('configure').disabled=state.active||Object.keys(s.open_positions).length>0;
 el('buy').disabled=!!s.open_positions[state.symbol];el('sell').disabled=!s.open_positions[state.symbol];
 el('symbol').disabled=el('source').value==='demo';
 el('price').textContent=state.price?money(state.price):'Henüz alınmadı';
 el('price-age').textContent=state.price_age===null?'Fiyatı yenileyerek başlayın.':'Son veri '+state.price_age+' saniye önce · '+(state.active?'Takip açık':'Yeni işlemler duraklatıldı');
 if(state.error)message(state.error,true);
 const positions=Object.entries(s.open_positions);el('positions-body').replaceChildren();
 for(const [symbol,p] of positions){const row=document.createElement('tr');[symbol,Number(p.qty).toLocaleString('tr-TR',{maximumFractionDigits:6}),fmt(p.entry),fmt(p.stop),fmt(p.target),'Sanal'].forEach(x=>cell(row,x));el('positions-body').append(row);}
 el('positions-empty').hidden=positions.length>0;el('position-count').textContent=positions.length+' pozisyon';
 el('history-body').replaceChildren();
 for(const entry of [...state.events].reverse()){
   const row=document.createElement('tr');cell(row,new Date(entry.timestamp).toLocaleString('tr-TR',{timeZone:'Europe/Istanbul',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}));cell(row,entry.symbol);
   const status=cell(row,'');const badge=document.createElement('span');badge.textContent=outcomes[entry.outcome]||entry.outcome;badge.className='outcome '+(entry.outcome==='buy_filled'?'buy':['stop','signal','take_profit'].includes(entry.outcome)?'sell':entry.outcome.includes('rejected')||entry.outcome.includes('blocked')?'blocked':'');status.append(badge);
   cell(row,money(entry.equity));cell(row,String(entry.open_positions));el('history-body').append(row);
 }
 el('history-empty').hidden=state.events.length>0;
 el('testnet-badge').textContent=state.testnet?(state.testnet.can_trade?'Test ortamı bağlı':'İşlem izni kapalı'):'Bağlı değil';el('testnet-badge').classList.toggle('connected',!!state.testnet);el('validate').disabled=!state.testnet?.can_trade;el('disconnect').disabled=!state.testnet;
 el('testnet-balances').replaceChildren();if(state.testnet){for(const b of state.testnet.balances){const tag=document.createElement('span');tag.textContent=b.asset+': '+fmt(b.free);el('testnet-balances').append(tag);}}
 drawChart(state.chart);
}
async function refresh(){try{render(await api('/api/state'));if(refreshFailed&&!currentState.error){el('message').hidden=true;}refreshFailed=false;return true;}catch(error){refreshFailed=true;message('Panel bağlantısı kesildi. İşlem yapmadan önce uygulamanın açık olduğunu kontrol edin.',true);return false;}}
el('configure').addEventListener('click',()=>command('configure',{source:el('source').value,symbol:el('symbol').value}));
el('source').addEventListener('change',()=>{selectionDirty=true;el('symbol').disabled=el('source').value==='demo';});
el('symbol').addEventListener('change',()=>{selectionDirty=true;});
el('automatic').addEventListener('change',()=>{automaticDirty=true;});
el('demo_restart').addEventListener('click',()=>command('demo_restart'));
for(const name of ['step','pause','buy','sell','refresh'])el(name).addEventListener('click',()=>command(name));
el('start').addEventListener('click',()=>command('start',{automatic:el('automatic').checked}));
el('connect-form').addEventListener('submit',async event=>{event.preventDefault();const key=el('key').value,secret=el('secret').value;el('key').value='';el('secret').value='';await command('testnet_connect',{key,secret});});
el('disconnect').addEventListener('click',()=>command('testnet_disconnect'));
el('validate').addEventListener('click',()=>command('testnet_validate',{amount:el('test-amount').value}));
el('export').addEventListener('click',async()=>{try{const response=await fetch('/api/export',{headers:{'X-App-Token':token}});if(!response.ok)throw Error('Rapor indirilemedi.');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download='TradeIQ-sanal-islemler.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){message(error.message,true);}});
window.addEventListener('resize',()=>{if(currentState)drawChart(currentState.chart);});
refresh();setInterval(()=>{if(!working)refresh();},4000);
